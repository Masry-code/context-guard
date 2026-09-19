"""Context-cost audit for Claude Code sessions.

Why this exists: Claude Code re-sends the WHOLE conversation on every turn.
Cost is driven by (context size) x (number of turns), not by how much work
got done. This finds the sessions where that product has gone wrong.

Usage:
    python audit.py               # summary of every session
    python audit.py --deep        # + what is filling the worst session
    python audit.py --deep <8-char-session-id>
"""
import json, os, glob, sys, collections, datetime, io

HOME_DIR = os.path.expanduser("~")
ROOT = os.path.join(os.path.expanduser("~"), ".claude", "projects")

# thresholds worth shouting about (measured 2026-09-11, see the memory)
AGE_DAYS_WARN   = 3          # a session still alive after this long is hoarding context
PEAK_CTX_WARN   = 150_000    # past this the per-turn cost curve goes vertical
CACHE_READ_WARN = 30_000_000 # cumulative re-reads


def scan(path):
    c = collections.Counter()
    turns = 0
    peak = 0
    first = last = None
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            ts = d.get("timestamp") or ""
            if ts:
                if first is None:
                    first = ts
                last = ts
            u = (d.get("message") or {}).get("usage") or {}
            if not u:
                continue
            turns += 1
            cr = u.get("cache_read_input_tokens", 0) or 0
            cw = u.get("cache_creation_input_tokens", 0) or 0
            c["in"] += u.get("input_tokens", 0) or 0
            c["out"] += u.get("output_tokens", 0) or 0
            c["cw"] += cw
            c["cr"] += cr
            peak = max(peak, cr + cw)
    return c, turns, peak, first, last


def days_between(a, b):
    if not (a and b):
        return 0.0
    fmt = "%Y-%m-%dT%H:%M:%S"
    try:
        d1 = datetime.datetime.strptime(a[:19], fmt)
        d2 = datetime.datetime.strptime(b[:19], fmt)
        return (d2 - d1).total_seconds() / 86400
    except Exception:
        return 0.0


def deep(path):
    """What is actually sitting in this session's context."""
    id2name, id2file = {}, {}
    calls, tbytes = collections.Counter(), collections.Counter()
    reads_by_file, read_count = collections.Counter(), collections.Counter()
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            content = (d.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_use":
                    n = b.get("name", "?")
                    calls[n] += 1
                    id2name[b.get("id")] = n
                    if n == "Read":
                        fp = (b.get("input") or {}).get("file_path", "?")
                        id2file[b.get("id")] = fp
                        read_count[fp] += 1
                elif b.get("type") == "tool_result":
                    tid = b.get("tool_use_id")
                    sz = len(json.dumps(b.get("content"), ensure_ascii=False)) if b.get("content") is not None else 0
                    tbytes[id2name.get(tid, "unknown")] += sz
                    if tid in id2file:
                        reads_by_file[id2file[tid]] += sz
    return calls, tbytes, reads_by_file, read_count


def main():
    args = [a for a in sys.argv[1:]]
    if "--alert" in args:
        alert()
        return
    want_deep = "--deep" in args
    if want_deep:
        args.remove("--deep")
    target = args[0] if args else None

    rows = []
    for p in glob.glob(os.path.join(ROOT, "*", "*.jsonl")):
        try:
            c, turns, peak, first, last = scan(p)
        except Exception as e:
            print("  ! could not read", p, e)
            continue
        if not turns:
            continue
        rows.append({
            "path": p,
            "sid": os.path.basename(p)[:8],
            "proj": os.path.basename(os.path.dirname(p)),
            "mb": os.path.getsize(p) / 1e6,
            "turns": turns, "peak": peak, "first": first, "last": last,
            "age": days_between(first, last), **c,
        })
    seen = set()
    uniq = []
    for r in rows:
        key = (r["sid"], r["turns"], r["cr"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(r)
    rows = uniq
    rows.sort(key=lambda r: r["cr"], reverse=True)

    print(f"{'session':9} {'project':<28} {'turns':>6} {'peak ctx':>9} {'cache-read':>11} {'MB':>7} {'alive':>7}  flags")
    print("-" * 100)
    for r in rows[:20]:
        flags = []
        if r["age"] > AGE_DAYS_WARN:
            flags.append(f"OLD({r['age']:.0f}d)")
        if r["peak"] > PEAK_CTX_WARN:
            flags.append("BIG-CTX")
        if r["cr"] > CACHE_READ_WARN:
            flags.append("HEAVY")
        print(f"{r['sid']:9} {r['proj'][:28]:<28} {r['turns']:>6} {r['peak']/1000:>8.0f}k "
              f"{r['cr']/1e6:>10.1f}M {r['mb']:>7.1f} {r['age']:>6.1f}d  {' '.join(flags)}")

    if not rows:
        return
    print()
    worst = rows[0]
    if target:
        match = [r for r in rows if r["sid"].startswith(target)]
        if match:
            worst = match[0]
    print(f"worst: {worst['sid']} ({worst['proj']}) - {worst['cr']/1e6:.0f}M tokens re-read "
          f"over {worst['turns']} turns = {worst['cr']/max(worst['turns'],1)/1000:.0f}k of context on every single turn")

    if not want_deep:
        print("\nrun with --deep to see what is filling it")
        return

    calls, tbytes, reads, rcount = deep(worst["path"])
    print("\n-- tool calls --")
    for n, k in calls.most_common(10):
        print(f"  {k:6d}  {n}")
    print("\n-- tool-result payload sitting in context --")
    for n, b in tbytes.most_common(8):
        print(f"  {b/1e6:8.2f} MB  {n}")
    if reads:
        print("\n-- biggest Read payloads (images are the usual culprit) --")
        for fp, b in reads.most_common(8):
            print(f"  {b/1e6:8.2f} MB  x{rcount[fp]:<3} {fp[-70:]}")



# ---------------------------------------------------------------------------
# --alert : SessionStart hook mode.
# Must be FAST and QUIET. Never parses a whole transcript - stats the file and
# reads only the first and last line. Prints nothing when nothing is flagged.
# ---------------------------------------------------------------------------

BIG_FILE_MB   = 20     # a transcript this large is a hoarding session
OLD_FILE_MB   = 2      # ...or a smaller one that has simply been alive too long
ALERT_AGE_DAYS = 3


def _ts_of(line):
    try:
        return (json.loads(line) or {}).get("timestamp") or ""
    except Exception:
        return ""


def fast_stat(path):
    """(size_mb, age_days) without parsing the file."""
    size = os.path.getsize(path)
    first = last = ""
    with open(path, "rb") as f:
        # the first record is not always a timestamped one - scan a few
        for _ in range(50):
            raw = f.readline()
            if not raw:
                break
            first = _ts_of(raw.decode("utf-8", "replace"))
            if first:
                break
        if size > 65536:
            f.seek(-65536, os.SEEK_END)
            f.readline()  # discard the partial line
        else:
            f.seek(0)
        for line in reversed(f.read().decode("utf-8", "replace").splitlines()):
            t = _ts_of(line)
            if t:
                last = t
                break
    return size / 1e6, days_between(first, last)


def _project_key(path):
    """D:/Claude -> D--Claude, matching how Claude Code names project folders."""
    return "".join(c if c.isalnum() else "-" for c in path)


def handoff_block():
    """A note the previous chat left for this project. Returned once, then archived."""
    f = os.path.join(HOME_DIR, ".claude", "handoff", _project_key(os.getcwd()) + ".md")
    if not os.path.exists(f):
        return None
    # timestamped, never a flat ".used.md" - the flat name overwrote the previous archive
    # every time. Measured 11 Sep: this hook consumed a note at 15:56 and destroyed the
    # earlier one in the same stroke. guard.py was fixed for this; audit.py had been missed.
    used = f[:-3] + ".used-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + ".md"
    try:
        body = io.open(f, encoding="utf-8", errors="replace").read()
    except Exception:
        return None
    try:
        os.replace(f, used)   # consume it so it appears exactly once
    except Exception:
        pass
    # Do not truncate detail in silence - the old 6000-char cap cut a real 17,672-char
    # note off mid-word. Past the cap, point at the file so the rest can be read.
    if len(body) > 40_000:
        body = (body[:40_000] + chr(10) + chr(10)
                + "... NOTE TRUNCATED HERE - it is " + str(len(body)) + " chars long. "
                "READ THE REST NOW with the Read tool from: " + used)
    return ("HANDOFF NOTE from the previous chat in this project. It was ended because it "
            "had grown too expensive, NOT because the work finished. Read it and carry on "
            "from there; tell the user in one line that you have picked up where you left off."
            + chr(10) + chr(10) + body)


def alert():
    # unconditional breadcrumb: proves whether the hook ran at all
    try:
        with open(os.path.join(os.path.expanduser("~"), ".claude", "context-audit.log"), "a") as lg:
            print(datetime.datetime.now().isoformat(timespec="seconds"), " alert() ran", file=lg)
    except Exception:
        pass
    hits = []
    for p in glob.glob(os.path.join(ROOT, "*", "*.jsonl")):
        try:
            mb, age = fast_stat(p)
        except Exception:
            continue
        if mb > BIG_FILE_MB or (age > ALERT_AGE_DAYS and mb > OLD_FILE_MB):
            hits.append((mb, age, os.path.basename(p)[:8], os.path.basename(os.path.dirname(p))))
    # NOTE PICKUP DELIBERATELY DISABLED HERE (11 Sep 2026). SessionStart fires for a RESUMED
    # session too, and it has no way to tell a fresh chat from a 244k one being reopened. It
    # consumed the Spotliar handoff note at 15:56 and handed it to an unrelated chat about
    # Wi-Fi shortcuts, twice removing it from the chat it was written for. guard.py's
    # UserPromptSubmit path is now the SINGLE owner of pickup: it measures live context first
    # and refuses above FRESH_CTX. One path, one gate, one thing to get right.
    hand = None
    if not hits and not hand:
        return
    hits.sort(reverse=True)
    lines = [f"  {sid}  {proj[:20]:<20} {mb:6.0f} MB  {age:.0f}d alive" for mb, age, sid, proj in hits[:5]]
    extra = f"\n  (+{len(hits)-5} more flagged)" if len(hits) > 5 else ""
    ctx = "" if not hits else (
        "CONTEXT-COST AUDIT (automatic, measured from transcripts on disk).\n"
        "Cost = context size x turn count. These sessions are re-reading a huge "
        "history on every turn; do NOT resume them, start fresh instead:\n"
        + "\n".join(lines) + extra +
        "\nIf the user asks about cost/limits/slowness, run:\n"
        # derived, never hardcoded: this line told the user where to run audit.py from,
        # so a hardcoded path silently lied the moment the file moved (it did, 18 Sep
        # 2026, into the context-guard folder beside guard.py). It is also the last
        # "D:/..." in the alert, which is what made the alert Windows-only.
        '  python "' + os.path.abspath(__file__).replace("\\", "/") + '" --deep\n'
        "Standing rules: warn past ~150k context, read any image exactly once, "
        "one session per task."
    )
    full = ((hand + chr(10) + chr(10) + "") if hand else "") + ctx
    sysmsg = []
    if hand:
        sysmsg.append("Picking up the handoff note from your last chat.")
    if hits:
        sysmsg.append("Context-cost audit: %d bloated session(s) flagged - largest %.0f MB, %.0f days alive."
                      % (len(hits), hits[0][0], hits[0][1]))
    print(json.dumps({
        "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": full},
        "systemMessage": " ".join(sysmsg),
    }))

if __name__ == "__main__":
    main()
