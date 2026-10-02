# Review fixes, batch B (proof + thresholds) - Implementation Plan

> **For agentic workers:** one task per implementer, in order. Steps use checkbox (`- [ ]`) syntax.

**Goal:** move the warning and the ceiling to the owner's new numbers, and give him one number
that shows whether chats are actually getting smaller: average context per call, this week
against last week.

**Source:** the 2 Oct honest review, fix-list rows 2 and 3. The owner picked "Proof it saves
money" on 2 Oct 2026, then chose his own numbers (option "c", 2 Oct, chat -50): auto-compact
window 420k, first warning 225k, ceiling 320k. They come from a measurement over 129 real chats
(27,902 calls): a new chat starts at ~72k, grows ~1.9k per call, and the cheapest first-warning
point once handover overhead is counted sits around 200-225k. The review's 150k was too low.
The settings.json change (450k -> 420k) is made by the controller, not by a task.

**Tech stack:** Python 3 standard library only. Unit suite `python test_guard.py`. Full gate
`python "D:/AI Projects/Claude Needed Tools/safeguard-c/suite.py"` (expect rc=0, about 170 s).

---

## Machine rules (every task)

- `guard.py` line endings: MEASURE before you touch it (count `\r\n` and bare `\n` in raw bytes
  with Python). `guard.py` is CRLF: 4849 CRLF and 0 bare LF at `0a0883e`.
  - Patch a CRLF file ONLY with a Python script written with the Write tool that opens the file
    with `io.open(path, newline="")`. Each anchor must occur exactly once, or the script aborts.
    New text gets `\r\n`. Print CRLF and bare-LF counts before and after. Bare LF stays 0.
  - Never `sed -i`, a heredoc, or a shell redirect on any repo file.
- `audit.py`, `test_guard.py`, `install.py` and `README.md` are LF. Edit them with the Edit tool.
- Every new test function also goes into the explicit runner tuple at the bottom of
  `test_guard.py` (the last `for t in (` block). A test left out never runs.
- Tests drive the hooks as subprocesses against a throwaway home (`make_home`, `run`, `run_stop`,
  `write_big_chat`, `write_transcript` and friends already exist - reuse them). Internals go
  through `guard_call` / `guard_constant`.
- Test first: write the failing test, run it and see it fail, then write the code.
- Fixture names are invented. No real project or app names: a pre-push hook refuses them.
- Commit author is the repo default. End every message with
  `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. One commit per task. Never push.
- The hooks run straight from this working tree, so a commit is live in every open chat at
  once. Never leave guard.py or audit.py in a state that fails `python -c "import ast; ast.parse(open('<file>', encoding='utf-8').read())"`.

---

## Task 1 - the new thresholds: first warning 225k, ceiling 320k  (review row 3)

Today `LEVELS` is `(200_000, "getting expensive")`, `(250_000, "expensive")`,
`(320_000, "very expensive")` and `CEILING_CAP = 300_000` (guard.py ~17-32 and ~130).
The ceiling must stay ABOVE the top tier (the comment above `CEILING_CAP` says why: it is a last
resort, not another warning), so the tiers move down under it.

- [ ] Test: rewrite `test_the_first_warning_waits_for_200k` as `test_the_first_warning_waits_for_225k`
  (rename it in the runner tuple too): a 150k chat and a 215k chat are NOT warned; a 230k chat IS
  asked for a handoff note, named GETTING EXPENSIVE. Pin: `LEVELS` == 225k / 260k / 290k with the
  three existing names, `CEILING_CAP == 320_000`, the top tier is below `CEILING_CAP`,
  `REWARN_STEP == 40_000`, `FRESH_CTX` unchanged (whatever it is today - read it).
- [ ] Test: with `write_settings(home, 420_000)` the effective ceiling (`_ceiling()` through
  `guard_call`, or the same route the existing `ceiling-window` test uses) is 320_000; with a
  200k window it is still `int(200_000*0.91) - CEILING_HEADROOM` (the existing test - keep it green).
- [ ] Code: `LEVELS` -> `(225_000, ...)`, `(260_000, ...)`, `(290_000, ...)`; `CEILING_CAP` ->
  `320_000`. Update the LEVELS comment: add a dated line (2 Oct 2026, his "c", chat -50: 225k
  first warning, 320k ceiling, 420k window - measured ~72k start, ~1.9k growth per call), and fix
  "His auto-compact sits at 93% of a 450k window" to 420k. Keep the older history lines.
- [ ] Every other test that relies on a 200k chat being WARNED (grep `200_000` and `"200k"` in
  test_guard.py - e.g. ~294, 350, 418, 912, 930, 1034, 1067, 1223, 1709-1713, 2142, 3321,
  3748-3755, 4039, 7955) must still prove what it proved: move the chat to 240_000 and the
  expected text from "200k" to "240k" ONLY where the test needs a warning. Where a test wants a
  QUIET chat (e.g. ~1264 "let a 200k chat close", ~1373-1381 the paused chat) leave it. Rename
  `test_the_plain_200k_handoff_warning_is_emitted_whole` only if its body changes; it must still
  check the warning fits under `HOOK_TEXT_MAX`.
- [ ] `audit.py` ~315: "warn past ~200k context" -> "warn past ~225k context". Add a test that
  `audit.py`'s text says the same number as `LEVELS[0][0]` (read the file, look for
  `~%dk` % (LEVELS[0][0]//1000)), so the two cannot drift again.
- [ ] Run `python test_guard.py`; then the full gate.

## Task 2 - measure context per call, week on week  (review row 2) - the engine, in audit.py

audit.py is the cost-measurement module, so the engine lives there. It must be incremental:
the transcripts total hundreds of MB, the SessionEnd hook has a 10 s timeout, and the
SessionStart audit must stay fast.

Data rules (memory `transcript-usage-parsing-traps`, measured): one API call is written as
SEVERAL jsonl lines sharing `message.id` - dedupe on `message.id` (fall back to `requestId`),
never `uuid`. Context of a call = `input_tokens + cache_read_input_tokens +
cache_creation_input_tokens`. Skip lines with `isSidechain: true` and any `type` other than
`assistant`. A forked chat copies earlier calls into a new file with the same ids - so the
dedupe must work ACROSS files, not only within one.

- [ ] Tests (new, in test_guard.py, driving `audit.py` as a subprocess against a throwaway home,
  with transcripts written as real jsonl lines carrying `timestamp`, `message.id` and `usage`):
  1. Three lines with one `message.id` count as ONE call.
  2. A call 2 days old lands in "this week", one 10 days old in "last week", one 20 days old in
     neither.
  3. The same `message.id` in two files (a fork) counts once.
  4. Incremental: run the refresh, append two calls to the file, run it again - the totals
     grow by exactly two calls; a third run with no change leaves them identical. A file that
     SHRANK (rewritten) is rescanned from 0 without double counting.
  5. Budget: with a time budget of 0 the refresh stops after the first file, saves its offsets,
     and marks the result incomplete; an unbudgeted run then completes it.
  6. `python audit.py --weekly` prints the line in item 7 for a home with data, and
     "no calls measured yet" for an empty home. Exit code 0 both times.
  7. The line reads, for example:
     `Average context per call: 186k this week (1,204 calls), 212k last week (980 calls) - 12% smaller. 31% of this week's calls ran past the 225k warning (last week 44%).`
     "smaller"/"bigger" follows the sign; with no last-week calls the comparison part is left out.
- [ ] Code in audit.py:
  - `WARN_AT = 225_000` with a comment that it mirrors guard.py `LEVELS[0][0]`; Task 1's
    drift test is extended to pin `WARN_AT == LEVELS[0][0]`.
  - State in `~/.claude/context-guard/`: `weekly-context-scan.json` (per transcript path: byte
    offset, file size, last message id; a set of seen message-id hashes - first 12 hex of sha1 -
    with their day, pruned past 15 days) and `weekly-context.json` (the small summary: per local
    day `calls`, `sum_ctx`, and a 25k-bucket histogram; `complete` true/false; `refreshed` ISO time).
    Write each with write-to-temp + `os.replace`.
  - `weekly_refresh(home_state, budget_s=None, save=True)`: scan `ROOT/*/*.jsonl` modified in the
    last 15 days, from each file's saved offset; reset to 0 when the size is below the offset;
    read only complete lines (stop at the last `\n`). Stop starting new files once `budget_s` is
    spent (checked between files) and mark `complete` False. Days older than 15 are dropped.
    Returns the summary dict. With `save=False` nothing is written (Task 3's `--report` needs that).
  - `weekly_line(summary, warn_at=WARN_AT)`: the sentence in test 7, or "" when there are no
    calls. "This week" = the last 7 local days including today; "last week" = the 7 before.
  - `--weekly` CLI: full refresh (no budget, saves), prints `weekly_line` or "no calls measured yet".
- [ ] Never raise out of these functions: an unreadable line or file is skipped.
- [ ] Run `python test_guard.py`; then the full gate.

## Task 3 - show the number where he looks  (wiring)

- [ ] Tests:
  1. `guard.py --session-end` (existing SessionEnd hook) also runs `weekly_refresh` with a
     budget of 6 s and saves: after it runs on a home with one transcript, `weekly-context.json`
     exists with that transcript's calls. It still prints nothing and still writes the stub as
     before (keep the existing session-end tests green).
  2. `guard.py --report` prints a `-- chat size, week on week --` section with the line, computed
     with `save=False`: the report creates NO file in STATE and writes NOTHING to the log (assert
     the STATE listing and the log bytes are identical before and after).
  3. `audit.py --alert` adds the line to its `systemMessage` when `weekly-context.json` is
     `complete` and `refreshed` within the last 3 days; it does NOT scan transcripts (a home with
     a stale or incomplete summary prints no line, even if transcripts exist). With nothing else
     to say it still prints a systemMessage-only json (the shape `update_line` already uses).
  4. `--report`'s session list does not show `weekly-context` or `weekly-context-scan`.
- [ ] Code:
  - guard.py loads audit.py BY PATH (`importlib.util.spec_from_file_location`, the same pattern
    as `audit.update_line` uses for update.py) - audit.py sits beside guard.py. A failure to
    load just means no number; never break the hook.
  - Add `"weekly-context"` and `"weekly-context-scan"` to `NOT_A_SESSION_STATE`.
  - `--report`: the new section goes after the errors block, before the sessions list.
  - `cmd_session_end`: the refresh runs AFTER the stub logic, inside its own try, so a slow or
    failing scan can never cost the stub.
- [ ] README: one short paragraph under the report/audit docs saying what the line means
  (average context sent per call; smaller is cheaper) - no project names.
- [ ] Run `python test_guard.py`; then the full gate.

---

## Done when

- All three commits are in, the full gate is rc=0, and a reviewer has checked the batch.
- `guard.py` bare LF is still 0; audit.py, test_guard.py, README.md still 0 CRLF.
- The controller has set `autoCompactWindow` to 420000 in `~/.claude/settings.json` (his yes,
  2 Oct) and re-run `python install.py --dry-run` to show the hooks are unchanged.
