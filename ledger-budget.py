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
    by_thread, unattributed, noise = {}, 0, 0
    raw = entries(path)
    for b in raw:
        m = guard.LEDGER_CHAT_RE.search(b.split(NL, 1)[0])
        sid = m.group(1) if m else None
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
    print("%9s %8s %9s %10s" % ("budget", "dropped", "kept", "chars kept"))
    for budget in (6000, 8000, 10000, 12000, 15000, 20000, 30000, 50000):
        dropped = kept = chars = 0
        for blocks in by_thread.values():
            o, n, om = guard.fit_budget(blocks, budget, guard.LEDGER_OLDEST_SHARE)
            dropped += om
            kept += len(o) + len(n)
            chars += sum(len(b) for b in o + n)
        if dropped == 0 and knee is None:
            knee = budget
        print("%9d %8d %9d %10d%s" % (budget, dropped, kept, chars,
                                      "   <- nothing dropped from here up"
                                      if budget == knee else ""))
    print()
    if worst == 0:
        print("verdict: %d is enough today - no thread loses a request."
              % guard.LEDGER_OWN_CHARS)
    else:
        print("verdict: %d is TOO SMALL - the worst thread loses %d request(s). "
              "Nothing is dropped at %s." % (guard.LEDGER_OWN_CHARS, worst, knee))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
