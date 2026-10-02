# Review fixes, batch A (bugs) - Implementation Plan

> **For agentic workers:** one task per implementer, in order. Steps use checkbox (`- [ ]`) syntax.

**Goal:** fix the four bugs the 2 Oct honest review measured, plus one recall gap found while
proving the keyword fill. No new features.

**Source:** the review's section 3 and fix-list rows 4, 5, 8 and 12. The owner picked "Bugs" on
2 Oct 2026 (chat -49).

**Tech stack:** Python 3 standard library only. Unit suite `python test_guard.py`. Full gate
`python "D:/AI Projects/Claude Needed Tools/safeguard-c/suite.py"` (expect rc=0, about 140 s).

---

## Machine rules (every task)

- `guard.py` and `install.py` line endings: MEASURE before you touch them (count `\r\n` and bare
  `\n` in raw bytes with Python). `guard.py` is CRLF: 4615 CRLF and 0 bare LF at `ffbf806`.
  - Patch a CRLF file ONLY with a Python script written with the Write tool that opens the file
    with `io.open(path, newline="")`. Each anchor must occur exactly once, or the script aborts.
    New text gets `\r\n`. Print CRLF and bare-LF counts before and after. Bare LF stays 0.
  - Never `sed -i`, a heredoc, or a shell redirect on any repo file.
- `test_guard.py` and `README.md` are LF. Edit them with the Edit tool.
- Every new test function also goes into the explicit runner tuple at the bottom of
  `test_guard.py` (the last `for t in (` block, after
  `test_a_stop_over_a_string_message_record_does_not_crash`). A test left out never runs.
- Tests drive the hooks as subprocesses against a throwaway home (`make_home`, `run_stop`,
  `write_big_chat` and friends already exist - reuse them). Internals go through `guard_call`.
- Test first: write the failing test, run it and see it fail, then write the code.
- Fixture names are invented. No real project or app names: a pre-push hook refuses them.
- Commit author is the repo default. End every message with
  `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. One commit per task. Never push.
- The hooks run straight from this working tree, so a commit is live in every open chat at
  once. Never leave guard.py in a state that fails `python -c "import ast; ast.parse(open('guard.py').read())"`.

---

## Task 1 - `--report` lists only real chat sessions  (review row 12)

`cmd_report` prints every `STATE/*.json` that holds a dict, so `memory-catalogue`,
`memory-manifest` and `update-check` show up as "sessions being tracked".

- [ ] Test: a home whose STATE holds one session file (a uuid-shaped name) plus
  `memory-catalogue.json`, `memory-manifest.json`, `update-check.json` (all dicts). `--report`
  output lists the session and none of the other three.
- [ ] Fix: in the sessions loop, skip any file whose stem is not a session id. A session id is
  what `save_state(sid, ...)` writes - read `save_state`/`state_path` to get the exact naming and
  match THAT (do not guess a uuid regex if the code says otherwise).

## Task 2 - `--recall` finds a memory from one word of a phrase keyword

Found live 2 Oct: real keywords are phrases ("adb install fails"), and a keyword only hits when
ALL its words are in the query, so `--recall adb` misses that memory and `--recall red usage bar`
misses "red usage credits bar". The prompt hook (strict) must NOT get noisier.

- [ ] Tests (loose mode only, via `--recall`): a memory with keyword `adb install fails` is found
  by `--recall adb`; a memory with `red usage, credits bar` is found by `--recall red usage bar`;
  a whole-phrase hit still ranks above a one-word hit; a stopword-only query (`the a of`) and a
  2-letter word find nothing. Strict mode: a test that a prompt naming one word of a phrase
  keyword gives NO hint (unchanged behaviour).
- [ ] Fix in `score_memories`: when `strict` is False, after the phrase hits, also count weak
  hits - a query word of 3+ characters that equals a word inside a keyword phrase. Rank by
  (phrase hits, weak hits, mtime). Strict mode is untouched. Reuse `_words` so the Arabic and
  case folding stay symmetric.

## Task 3 - the stub stops being rewritten on every reply  (review row 4)

"Stop" fires at the end of EVERY reply. `cmd_ledger` calls `write_stub` on every Stop
(guard.py:859): 223 stub writes into 66 notes, one chat re-stubbed 23 times, and a chat that
tries to Write its real note fails "not read yet" because the stub changed under it.

The stub's job stays: a chat that ends without writing a note leaves a pointer behind.

- [ ] Tests:
  1. A Stop on a big chat writes NO stub any more (update the existing stub tests that assert the
     opposite - `test_a_chat_that_never_reaches_the_ceiling_still_leaves_a_stub` and friends - to
     drive the new paths below instead; keep `test_the_stub_never_overwrites_a_real_note`'s
     guarantee on every new path).
  2. A `--session-end` run (SessionEnd hook, stdin carries `session_id` and `transcript_path`)
     writes the stub for that chat when no real note exists, and never touches a real note.
  3. `--bootstrap` (SessionStart) of a NEW chat writes a stub for each OTHER transcript in the
     same project whose file was last modified more than 6 h ago and less than 7 days ago, is not
     fresh (reuse the FRESH_BYTES idea: under it means too small to matter), and has no note.
     A transcript idle 1 h gets nothing. Its own transcript gets nothing.
  4. `--bootstrap` retires a stub older than 7 days by renaming it to
     `<name>.used-stale-<YYYYmmdd-HHMMSS>.md` (NO delete - permanent deletes are the owner's).
     A real note of any age is never touched. A stub younger than 7 days stays.
- [ ] Fix: remove the call at guard.py:859. Add `cmd_session_end` (+ `--session-end` dispatch in
  `__main__`) calling `write_stub`. Add the sweep + retire step to the bootstrap path, bounded
  (at most 20 transcripts looked at, newest first) and wrapped so a failure only logs.
- [ ] `install.py`: add `("SessionEnd", None, GUARD, "--session-end", 10, None)` to `HOOKS`.
  Check install.py's own tests/dry-run still pass and that an existing install gains the entry on
  re-run (read how HOOKS entries are merged; add a test if install.py has tests in the suite).
- [ ] Update the docstring of `write_stub` and the comment at guard.py:24 so they say where the
  stub is written now.

## Task 4 - swallowed errors are logged  (review row 8)

108 `except Exception` blocks, about 26 log. The log has no error lines at all.

- [ ] Tests: a helper `swallowed(where, e)` writes one log line
  `error: <where>: <ExceptionType>: <message> (line N)`; `--report` prints a row
  `errors (last 7 days)` with the count and the last 5 lines, and `never fired` style output
  when there are none (follow REPORT_EVENTS' shape - read it first).
- [ ] Fix: add the helper next to `log`. Then, in every `except Exception:` / `except Exception as e:`
  block that today neither logs nor re-raises, add a `swallowed("<function name>", e)` call -
  EXCEPT the ones whose comment says the failure is the expected answer (e.g. `is_stub` returning
  False for an unreadable file, `reconfigure` on stdout). List the ones you skipped and why in
  your report. Behaviour must not change otherwise: same return values, same output.
- [ ] `swallowed` itself must never raise.

## Task 5 - the pickup fits the hook channel  (review row 5)

Claude Code moves hook output over about 10,000 characters into a file and shows the chat only a
2 KB preview. Pickups run 20-66 KB, so 122 of 124 chats saw mostly procedure and had to Read the
whole file. Nothing in guard.py knows the limit.

- [ ] Tests:
  1. A pickup whose final `additionalContext` would be over `HOOK_TEXT_MAX` (9000) emits at most
     9000 characters, and those characters START with the pickup text as today (procedure, then
     the note) - cut at a line boundary - and END with one line naming a file and saying to Read
     it now, before answering.
  2. That file holds exactly the remainder (head + remainder == the original text, byte for byte),
     lives under `~/.claude/handoff/overflow/`, and is named with the chat's sid8 and a timestamp.
  3. Output under the limit is unchanged, byte for byte (no file written).
  4. The same applies to anything else `cmd_size` emits (menu, warnings, archive offers, hints),
     because the cap runs at the single print point.
- [ ] Fix: one function `fit_hook_text(out, sid)` called in `cmd_size` right before
  `print(json.dumps(out))` (after `archive_step` and `memory_hints`). It only touches
  `out["hookSpecificOutput"]["additionalContext"]`. If writing the file fails, emit the text
  unchanged and log it (never lose the note).
- [ ] Reorder the pickup so the ledger and the memory index come AFTER the note (check they
  already do; if so, leave it). Do not reword the procedure text in this task.

---

## Done when

- All five commits are in, the full gate is rc=0, and a reviewer has checked the batch.
- `guard.py` bare LF is still 0.
