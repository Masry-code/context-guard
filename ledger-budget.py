"""Is LEDGER_OWN_CHARS still big enough? Measure, do not guess.

The request ledger is append-only, so a budget that dropped nothing last month will one
day start summarising the user's own words away. The hook now logs each time that happens
(`ledger: budget dropped ...`, and the `ledger budget drops` row in `--report`), and this
is the tool that says what to change the number to.

Read-only. It imports guard.py so the thread split and the budget arithmetic are the ones
that actually ship, not a second implementation that can drift.

    python ledger-budget.py             # this directory's project
    python ledger-budget.py D--Claude   # a project key, as Claude Code spells it
"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import guard  # noqa: E402

NL = chr(10)


def ledger_for(key):
    return os.path.join(guard.HOME, ".claude", "handoff", key + ".requests.md")


def any_transcript(key):
    """chat_threads() reads the whole project folder, so any transcript in it will do."""
    hits = sorted(glob.glob(os.path.join(guard.PROJECTS, key, "*.jsonl")))
    return hits[0] if hits else os.path.join(guard.PROJECTS, key, "none.jsonl")


def entries(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read().split(NL + "### ")[1:]


def main(argv):
    key = argv[0] if argv else guard.dir_key(os.getcwd())
    path = ledger_for(key)
    if not os.path.isfile(path):
        print("no ledger for %s at %s" % (key, path))
        return 1
    stems = guard.chat_threads(any_transcript(key))
    known = set(stems.values())
    # The entries written before the chat id went inline know whose they are only through
    # the .attrib.json sidecar, and guard.ledger_tail() reads it. Measuring without it
    # measures a SMALLER ledger than the one the budget is actually spent on - 19 Sep 2026
    # on his real file, 399 recovered attributions were written off as "unattributed",
    # this tool printed "context guard 43 reqs ... 0 dropped, 12000 is enough today", and
    # --report logged "budget dropped 34 of 83" from the same ledger on the same day.
    try:
        with open(guard.attrib_path(any_transcript(key)), encoding="utf-8") as f:
            attrib = json.load(f) or {}
    except Exception:
        attrib = {}
    by_thread, unattributed, noise = {}, 0, 0
    raw = entries(path)
    for b in raw:
        m = guard.LEDGER_CHAT_RE.search(b.split(NL, 1)[0])
        sid = m.group(1) if m else (attrib.get(guard.entry_key(b))
                                    if attrib else None)
        stem = stems.get(sid) if sid else None
        body = b.split(NL, 1)[1].strip() if NL in b else ""
        if guard.is_summon_label(body, known) or guard.ledger_noise(body):
            noise += 1
            continue
        if stem:
            by_thread.setdefault(stem, []).append("### " + b)
        else:
            unattributed += 1

    print("LEDGER: %s" % path)
    print("  %d entries, %d bytes, %d skipped as summon-labels/noise, %d unattributed"
          % (len(raw), os.path.getsize(path), noise, unattributed))
    print("  %d recovered from the attribution sidecar%s"
          % (len(attrib), "" if attrib else "  <- none: run guard.py --attribute, or "
             "this tool is measuring a smaller ledger than the pickup path"))
    print("  in force: LEDGER_OWN_CHARS=%d  LEDGER_OLDEST_SHARE=%s"
          % (guard.LEDGER_OWN_CHARS, guard.LEDGER_OLDEST_SHARE))
    print()
    hdr = "%-26s %6s %9s %7s %8s %8s" % ("thread", "reqs", "chars", "kept",
                                         "dropped", "oldest")
    print(hdr)
    print("-" * len(hdr))
    worst = 0
    for stem in sorted(by_thread, key=lambda s: -len(by_thread[s])):
        blocks = by_thread[stem]
        oldest, newest, omitted = guard.fit_budget(
            blocks, guard.LEDGER_OWN_CHARS, guard.LEDGER_OLDEST_SHARE)
        worst = max(worst, omitted)
        print("%-26s %6d %9d %7d %8d %8d"
              % (stem[:26], len(blocks), sum(len(b) for b in blocks),
                 len(oldest) + len(newest), omitted, len(oldest)))
    print()

    # The knee: the smallest budget at which no thread loses anything. Printed as a
    # recommendation rather than applied, because the number is a judgement about how
    # much of the injection one thread may take, and that judgement is the user's.
    knee = None
    # The smallest budget at which nothing is dropped is ARITHMETIC - it is the largest
    # thread's own size - so it is computed and added as a rung rather than searched for
    # among round numbers. Measured 19 Sep 2026: the largest thread was 69,706 chars, the
    # fixed rungs stopped at 50,000, and the tool recommended "None".
    rungs = [6000, 8000, 10000, 12000, 15000, 20000, 30000, 50000]
    fits_all = max((sum(len(b) for b in blocks)
                    for blocks in by_thread.values()), default=0)
    if fits_all:
        rungs = sorted(set(rungs + [fits_all]))
    print("%9s %8s %9s %10s %9s" % ("budget", "dropped", "kept", "chars kept",
                                    "~tok/pick"))
    for budget in rungs:
        dropped = kept = chars = worst_one = 0
        for blocks in by_thread.values():
            o, n, om = guard.fit_budget(blocks, budget, guard.LEDGER_OLDEST_SHARE)
            dropped += om
            kept += len(o) + len(n)
            c = sum(len(b) for b in o + n)
            chars += c
            # A pickup injects ONE thread's share, never the sum of every thread's - the
            # neighbours are bounded separately by LEDGER_OTHERS_CHARS. Summing them here
            # printed 28,968 tokens beside a verdict that said 17,426, from one run.
            worst_one = max(worst_one, c)
        if dropped == 0 and knee is None:
            knee = budget
        # ~tokens is what the injection costs on turn one of EVERY pickup, which is the
        # other half of the judgement: 4 chars/token, the ratio this project has used
        # throughout. A budget is a trade between his old words and the floor this tool
        # exists to lower, and the table should show both sides of it.
        print("%9d %8d %9d %10d %9d%s" % (budget, dropped, kept, chars, worst_one // 4,
                                          "   <- nothing dropped from here up"
                                          if budget == knee else ""))
    print()
    if worst == 0:
        print("verdict: %d is enough today - no thread loses a request."
              % guard.LEDGER_OWN_CHARS)
    else:
        print("verdict: %d is TOO SMALL - the worst thread loses %d request(s). "
              "Nothing is dropped at %s (~%s tokens on turn one of every pickup)."
              % (guard.LEDGER_OWN_CHARS, worst, knee,
                 "?" if knee is None else knee // 4))
        print("         Raising it is NOT automatic: the dropped requests are still in "
              "this file, and the")
        print("         note names the chat ids to grep for. The trade is his.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
