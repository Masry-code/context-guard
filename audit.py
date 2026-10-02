"""Context-cost audit for Claude Code sessions.

Why this exists: Claude Code re-sends the WHOLE conversation on every turn.
Cost is driven by (context size) x (number of turns), not by how much work
got done. This finds the sessions where that product has gone wrong.

Usage:
    python audit.py               # summary of every session
    python audit.py --deep        # + what is filling the worst session
    python audit.py --deep <8-char-session-id>
    python audit.py --weekly      # average context per call, this week vs last week
"""
import json, os, glob, sys, collections, datetime, io, hashlib, time

HOME_DIR = os.path.expanduser("~")
ROOT = os.path.join(os.path.expanduser("~"), ".claude", "projects")
STATE_DIR = os.path.join(os.path.expanduser("~"), ".claude", "context-guard")

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
    if "--weekly" in args:
        print(weekly_line(weekly_refresh(STATE_DIR)) or "no calls measured yet")
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
# --weekly : average context per API call, this week against last week.
# Incremental (the transcripts total hundreds of MB and the SessionEnd hook has a 10 s
# timeout): each file is read from its saved byte offset, whole lines only. One API call is
# written as SEVERAL jsonl lines sharing message.id, and a forked chat copies earlier calls
# into a new file with the same ids - so calls are deduped on message.id (else requestId)
# across ALL files, never on uuid.
# ---------------------------------------------------------------------------

WARN_AT = 225_000       # mirrors guard.py LEVELS[0][0]; a test pins the two together
KEEP_DAYS = 15
BUCKET = 25_000
SCAN_FILE = "weekly-context-scan.json"
SUMMARY_FILE = "weekly-context.json"


def _load_json(path):
    try:
        with io.open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_json(path, obj):
    """Write-to-temp then os.replace, so a reader never sees half a file."""
    tmp = path + ".tmp%d" % os.getpid()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with io.open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except Exception:
            pass


def _local_day(ts):
    """'2026-10-02T12:00:00.000Z' (UTC) -> local date string, or None."""
    try:
        t = datetime.datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")
        return t.replace(tzinfo=datetime.timezone.utc).astimezone().date().isoformat()
    except Exception:
        return None


CHECK_EVERY = 2000      # lines between looks at the clock inside one file
CHECK_BYTES = 8_000_000 # ...or bytes, whichever comes first


def _count_line(raw, st, seen, days, cutoff):
    try:
        d = json.loads(raw)
        if d.get("type") != "assistant" or d.get("isSidechain"):
            return
        m = d.get("message") or {}
        u = m.get("usage") or {}
        mid = m.get("id") or d.get("requestId")
        if not u or not mid:
            return
        day = _local_day(d.get("timestamp") or "")
        if not day or day < cutoff:
            return
        h = hashlib.sha1(str(mid).encode("utf-8")).hexdigest()[:12]
        if h in seen:
            return
        seen[h] = day
        ctx = ((u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
               + (u.get("cache_creation_input_tokens") or 0))
        row = days.setdefault(day, {"calls": 0, "sum_ctx": 0, "hist": {}})
        row["calls"] += 1
        row["sum_ctx"] += ctx
        b = str(int(ctx // BUCKET))
        row["hist"][b] = row["hist"].get(b, 0) + 1
        st["last"] = h
    except Exception:
        pass


def _read_new(path, st, seen, days, cutoff, deadline=None):
    """Count the complete new lines of one file. Mutates st, seen, days. Never raises.
    Returns True when it stopped early because `deadline` (a time.monotonic() value) passed:
    the clock is looked at every CHECK_EVERY lines, the offset saved is that of the last
    complete line read, so a later run carries on exactly there."""
    try:
        size = os.path.getsize(path)
        off = st.get("off", 0)
        if size < off:
            off = 0                      # rewritten smaller: rescan; seen ids stop double counts
        if size == off:
            st.update(off=off, size=size)
            return False
        pos, n, early, since = off, 0, False, 0
        with open(path, "rb") as f:
            f.seek(off)
            while True:
                raw = f.readline()
                if not raw or not raw.endswith(b"\n"):
                    break                # end of file, or a line still being written
                _count_line(raw, st, seen, days, cutoff)
                pos += len(raw)
                n += 1
                since += len(raw)
                # every CHECK_EVERY lines OR CHECK_BYTES read: a few hundred huge lines
                # (pasted images) must not run past the SessionEnd hook's 10 s kill
                if deadline is not None and (n % CHECK_EVERY == 0 or since >= CHECK_BYTES):
                    since = 0
                    if time.monotonic() < deadline:
                        continue
                    early = pos < size
                    break
        st.update(off=pos, size=size)
        return early
    except Exception:
        return False


def weekly_refresh(home_state, budget_s=None, save=True):
    """Fold the new transcript lines into the per-day summary and return it. With a budget,
    stops starting new files once it is spent (the first file is always read) and marks the
    summary incomplete. save=False writes nothing."""
    started = time.monotonic()
    scan_path = os.path.join(home_state, SCAN_FILE)
    sum_path = os.path.join(home_state, SUMMARY_FILE)
    scan = _load_json(scan_path)
    files = scan.get("files") if isinstance(scan.get("files"), dict) else {}
    seen = scan.get("seen") if isinstance(scan.get("seen"), dict) else {}
    prev = _load_json(sum_path)
    days = prev.get("days") if isinstance(prev.get("days"), dict) else {}
    today = datetime.date.today()
    cutoff = (today - datetime.timedelta(days=KEEP_DAYS)).isoformat()
    days = {k: v for k, v in days.items() if k >= cutoff}
    seen = {k: v for k, v in seen.items() if v >= cutoff}
    horizon = time.time() - KEEP_DAYS * 86400
    paths = []
    for p in glob.glob(os.path.join(ROOT, "*", "*.jsonl")):
        try:
            mt = os.path.getmtime(p)
            if mt >= horizon:
                paths.append((mt, p))
        except Exception:
            continue
    paths.sort(reverse=True)
    complete = True
    for i, (_mt, p) in enumerate(paths):
        if i and budget_s is not None and time.monotonic() - started >= budget_s:
            complete = False
            break
        st = files.get(p) if isinstance(files.get(p), dict) else {}
        early = _read_new(p, st, seen, days, cutoff,
                          None if budget_s is None else started + budget_s)
        files[p] = st
        if early:
            complete = False
            break
    live = {p for _mt, p in paths}
    files = {p: s for p, s in files.items() if p in live}
    summary = {"days": days, "complete": complete,
               "refreshed": datetime.datetime.now().isoformat(timespec="seconds")}
    if save:
        _save_json(scan_path, {"files": files, "seen": seen})
        _save_json(sum_path, summary)
    return summary


def weekly_totals(summary, today=None, warn_at=WARN_AT):
    """Totals for this week (the last 7 local days, today included) and the 7 before it."""
    today = today or datetime.date.today()
    t = dict(this_calls=0, this_sum=0, this_over=0, last_calls=0, last_sum=0, last_over=0)
    for key, row in ((summary or {}).get("days") or {}).items():
        try:
            n = (today - datetime.date.fromisoformat(key)).days
            part = "this" if 0 <= n <= 6 else "last" if 7 <= n <= 13 else None
            if part is None:
                continue
            t[part + "_calls"] += row["calls"]
            t[part + "_sum"] += row["sum_ctx"]
            t[part + "_over"] += sum(c for b, c in row["hist"].items() if int(b) * BUCKET >= warn_at)
        except Exception:
            continue
    return t


def weekly_line(summary, warn_at=WARN_AT):
    """The one-sentence verdict, or "" when there is nothing measured."""
    t = weekly_totals(summary, warn_at=warn_at)
    tc, lc = t["this_calls"], t["last_calls"]
    if not tc and not lc:
        return ""
    calls = lambda n: "%s call%s" % (format(n, ","), "" if n == 1 else "s")
    k = lambda total, n: "%dk" % round(total / n / 1000)
    if not tc:
        return "Average context per call: no calls this week, %s last week (%s)." % (
            k(t["last_sum"], lc), calls(lc))
    s = "Average context per call: %s this week (%s)" % (k(t["this_sum"], tc), calls(tc))
    over = "%d%%" % round(100 * t["this_over"] / tc)
    tail = "%s of this week's calls ran past the %dk warning" % (over, warn_at // 1000)
    if lc:
        s += ", %s last week (%s)" % (k(t["last_sum"], lc), calls(lc))
        a, b = t["this_sum"] / tc, t["last_sum"] / lc
        pct = round(100 * abs(b - a) / b) if b else 0
        s += " - " + ("no change" if pct == 0 else "%d%% %s" % (pct, "smaller" if a < b else "bigger"))
        tail += " (last week %d%%)" % round(100 * t["last_over"] / lc)
    return s + ". " + tail + "."


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


def update_line():
    """The 'a newer Context Guard exists' line, or "". update.py sits beside this file and
    is loaded by path, because the hook runs audit.py by absolute path from any cwd. An old
    checkout without update.py, or any failure inside it, just means no line."""
    try:
        import importlib.util
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "update.py")
        if not os.path.exists(path):
            return ""
        spec = importlib.util.spec_from_file_location("cg_update", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        parts = [mod.check() or ""]
        copy_line = getattr(mod, "copy_line", None)   # an old update.py has none
        if copy_line:
            parts.append(copy_line() or "")
        return " ".join(p for p in parts if p)
    except Exception:
        return ""


WEEKLY_FRESH_DAYS = 3


def weekly_note():
    """The weekly chat-size line from the SAVED summary, or "". SessionStart must stay fast,
    so this never scans a transcript: a summary that is incomplete, or was refreshed more than
    WEEKLY_FRESH_DAYS ago, says nothing."""
    try:
        s = _load_json(os.path.join(STATE_DIR, SUMMARY_FILE))
        if s.get("complete") is not True:
            return ""
        when = datetime.datetime.fromisoformat(s.get("refreshed") or "")
        if datetime.datetime.now() - when > datetime.timedelta(days=WEEKLY_FRESH_DAYS):
            return ""
        return weekly_line(s)
    except Exception:
        return ""


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
    # consumed the Harbor handoff note at 15:56 and handed it to an unrelated chat about
    # Wi-Fi shortcuts, twice removing it from the chat it was written for. guard.py's
    # UserPromptSubmit path is now the SINGLE owner of pickup: it measures live context first
    # and refuses above FRESH_CTX. One path, one gate, one thing to get right.
    hand = None
    upd = update_line()
    wk = weekly_note()
    if not hits and not hand:
        quiet = " ".join(x for x in (wk, upd) if x)
        if quiet:   # these notices are for the USER only: no additionalContext at all
            print(json.dumps({"systemMessage": quiet}))
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
        "Standing rules: warn past ~225k context, read any image exactly once, "
        "one session per task."
    )
    full = ((hand + chr(10) + chr(10) + "") if hand else "") + ctx
    sysmsg = []
    if hand:
        sysmsg.append("Picking up the handoff note from your last chat.")
    if hits:
        sysmsg.append("Context-cost audit: %d bloated session(s) flagged - largest %.0f MB, %.0f days alive."
                      % (len(hits), hits[0][0], hits[0][1]))
    if wk:
        sysmsg.append(wk)
    if upd:
        sysmsg.append(upd)
    print(json.dumps({
        "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": full},
        "systemMessage": " ".join(sysmsg),
    }))

if __name__ == "__main__":
    main()
