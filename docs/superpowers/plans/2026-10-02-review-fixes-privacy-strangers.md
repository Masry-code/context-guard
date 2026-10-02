# Review fixes, batch D (privacy + strangers) - Implementation Plan

> **For agentic workers:** one task per implementer, in order. Steps use checkbox (`- [ ]`) syntax.

**Goal:** stop the log and the ledger keeping things they should not (prompt text, keys and
tokens), stop old handoff archives piling up, stop one chat being handed other chats' words
unless the owner wants that, stop hard-coding the owner's own folder, tell strangers which
features need the desktop app, and run the suite on three operating systems.

**Source:** the 2 Oct honest review, fix-list rows 9 and 11. The owner gave permission to delete
`.used-*` handoff archives older than 30 days in `~/.claude/handoff/` - those files only.

**Tech stack:** Python 3 standard library only. Unit suite `python test_guard.py`. Full gate
`python "D:/AI Projects/Claude Needed Tools/safeguard-c/suite.py"` -> must end `rc=0 ... FAIL=0`
(PASS=1691 at `9e016d7`, about 175 s; every task adds checks, so PASS only goes up).

---

## What was measured before this plan (2 Oct 2026, HEAD 9e016d7, read-only)

Counts only. No value, prompt, title or name from the owner's files appears in this plan.

**Keys and tokens (Task 2).** A throwaway script ran the patterns of Task 2 over the real log
(`~/.claude/context-audit.log`, 4,999 lines) and the five real ledgers
(`~/.claude/handoff/*.requests.md`, 1,036 entries, 5,754 lines).

| file | hits | verdict |
|---|---|---|
| log | 0 for every pattern | 184 lines hold `sk-` inside ordinary words; the look-behind keeps all of them out |
| ledgers | 3, all the chat-bot-token shape (`<8-10 digits>:<35 chars>`) | all 3 are TRUE positives (one pasted in a request, two inside a webhook URL) |
| both | generic `key/token/password/secret/bearer <value>` rule: 0 hits | so that rule is proved only by the synthetic tests below |

False-positive check, same run: session UUIDs (189 in the log, 46 in the ledgers) and hex words
of 7-40 characters (919 / 710) were identical before and after masking, except the 3 digit runs
inside the 3 true hits. Nine synthetic look-alikes (UUID, 7- and 40-char sha, a 16-hex digest
after "token", "token budget 20000 tokens", `<task-notification>`, a long hyphenated word,
"password reset flow", a path after "token") all came out unchanged; nine fake keys of every
shape all came out masked. Verdict: no false positives found.

**Prompt text in the log (Task 1).** 1,000 `size: prompt=` lines (the review counted 892; it
has grown), 11 Sep - 2 Oct, each with the first 60 characters of a prompt (23 are empty).
Other log lines that carry free text: `checkpoint at` 189, `pause: paused` 13, `away:` 12
(fixed phrases only), `heredoc: blocked` 96 (fixed reasons only), `scratch: label` 4, archive
lines naming chat titles 28, `label-memory: appended` 69 (manifest project names).

**Handoff archives (Task 3).** `~/.claude/handoff/` holds 139 `.used-*` files (138 named
`<note>.used-YYYYmmdd-HHMMSS.md`, 1 renamed by hand to another `.used-...md` name). **0 are older
than 30 days** by mtime (the oldest is 21.3 days; 8 are past 21 days), so the first real run
deletes nothing. Also there, and never to be touched: 24 waiting notes, 5 ledgers, 5
`.seen.json` and 1 `.attrib.json` sidecars, 1 file named `<key>.used.md` (no dash - not a
`.used-*` file), and 2 folders (`overflow/` with 13 entries, and one unrelated folder). One
project holds 159 of the notes; all its notes are younger than 30 days today.

**config.json (Task 4).** The owner's `~/.claude/context-guard/config.json` does NOT exist.

**The manifest (Task 5).** His `memory-manifest.json` (LF) has top-level keys `_README`,
`_rules`, `t1_claude_md`, `projects`, `_craft_note`, `_craft`; 10 projects, each with `dir`,
`also`, `threads`, `files`. Nothing in it names the source folder (0 projects have it as `dir`
or `also`), so the manifest alone cannot reproduce today's `SOURCE_DIR` - it needs one new key.

**Prototypes (thrown away, in the scratchpad, never in the repo).**
- Source from the manifest with an empty default: 26 checks fail, all in the bootstrap,
  ordering-probe, label-fetch and catalogue tests. Adding `"source_dir": CWD` to the three test
  manifests (`BOOT_MANIFEST`, `KW_MANIFEST`, `MANIFEST_THREADS` via `write_threads_manifest`)
  turns all 26 green: PASS=1691, FAIL=0.
- Neighbour block off unless config says true: 12 checks fail, in exactly six tests (listed in
  Task 4). Nothing else depends on it.
- **Linux:** the unmodified suite at 9e016d7 under WSL Ubuntu 22.04, Python 3.10.12, with no
  global git identity (as on a CI runner): **PASS=1691, FAIL=0, rc=0.** macOS was not run.

---

## Machine rules (every task)

- `guard.py` line endings: MEASURE before you touch it (count `\r\n` and bare `\n` in raw bytes
  with Python). `guard.py` is CRLF: **4303 CRLF and 0 bare LF at `9e016d7`.**
  - Patch it ONLY with a Python script written with the Write tool that opens the file with
    `io.open(path, newline="")`. Each anchor must occur exactly once, or the script aborts.
    New text gets `\r\n`. Print CRLF and bare-LF counts before and after. Bare LF stays 0.
  - **No heredocs, no `echo >`, no shell redirect into a repo file, and NO `sed -i` at all.**
- Every other repo file is LF: `audit.py`, `install.py`, `update.py`, `test_guard.py`,
  `README.md`, `tour.md`, and the new `.github/workflows/tests.yml`. Edit them with the Edit
  tool (create the workflow with the Write tool). Measure CRLF before and after: it stays 0.
- `test_guard.py` is UTF-8: any script that reads it uses `encoding="utf-8"`.
- Every new test function also goes into the explicit runner tuple at the bottom of
  `test_guard.py` (the last `for t in (` block). A test left out never runs.
- Tests drive the hooks as subprocesses against a throwaway home (`make_home`, `run`,
  `run_stop`, `write_chat_msgs`, `ledger_body`, `note_for`, `append_new_entry`, `context_of`,
  `expect_clean`, `make_boot_home`, `boot`, `kw_home`, `run_install`, `inst_module` already
  exist - reuse them). Internals go through `guard_call` / `guard_constant`.
- Test first: write the failing test, run it and see it fail, then write the code.
- Fixture names are invented ("alpha", "beta", "chat X"). No real project, app or chat names:
  a pre-push hook refuses them.
- **No realistic secret literal in any repo file.** Build every fake key at runtime from parts
  (`"gh" + "p_" + "a1B2" * 9`), so GitHub's secret scanning has nothing to flag.
- The hooks RUN from the installed copy `~/.claude/context-guard/bin/`; this clone is only where
  code is edited. **Never run `install.py` or `update.py` against the real home** - every
  installer test passes `--home <throwaway>`. The live install is the controller's step C2.
- Never leave guard.py, audit.py, install.py or update.py in a state that fails
  `python -c "import ast; ast.parse(open('<file>', encoding='utf-8').read())"`.
- Commit author is the repo default. One commit per task. End every message with the
  Co-Authored-By line the controller's brief gives you. Never push.

---

## Order, and why

1. **Task 1 - stop logging prompt text.** One call site, the biggest leak (1,000 lines), and
   the smallest change: done first so Task 2's tests can use a log line that carries no prompt.
2. **Task 2 - mask keys and tokens** in the log and the ledger. RISKY: it changes `log()`, which
   every hook calls, and the ledger writer and the pickup injection.
3. **Task 3 - delete `.used-*` archives older than 30 days.** RISKY: it deletes files. Comes
   before Task 4 so Task 4's pickup tests run against the final SessionStart.
4. **Task 4 - neighbour requests become opt-in.** RISKY: it changes what a pickup injects, and
   install.py writes the owner's config.
5. **Task 5 - the source folder comes from the manifest.** RISKY: bootstrap, label fetch and the
   memory catalogue all read it.
6. **Task 6 - GitHub Actions** (+ the test file's SyntaxWarning). After every code change, so
   the first CI run tests the finished batch.
7. **Task 7 - README.** Last: Tasks 2-6 each add a line it must describe, and its badge test
   counts the checks the whole batch ends with.

---

## Task 1 - stop logging prompt text  (review row 9)

Every `log(` call that can carry text a person or a chat typed, at `9e016d7`:

| line | call | carries | decision |
|---|---|---|---|
| 2849 | `log("size: prompt=%d chars %r" % (len(prompt), prompt[:60]))` | the first 60 chars of EVERY prompt | **drop the words, keep the length** |
| 344 | `log("checkpoint at %d: %s" % (ctx, text))` | the chat's own milestone text (`--checkpoint`) | keep: written by the chat, shown by `--report`'s checkpoints row; masked by Task 2 |
| 377 | `log("pause: paused %s - %s" % (sid, why))` | the chat's reason (`--pause`) | keep, masked by Task 2 |
| 371 | `log("pause: resumed %s (was %r)" ...)` | the stored pause record (same reason) | keep, masked by Task 2 |
| 1155, 1159 | `log("away: armed/cleared by " + repr(s))` | the whole prompt, but ONLY when it equals a phrase in `AWAY_ON`/`AWAY_OFF` | keep: it can only ever be one of those fixed phrases |
| 2093, 2097, 2150 | archive lines naming chat titles | chat titles | keep: the archive queue is debugged by title; masked by Task 2 |
| 3373 | `log("scratch: label %r names a note in %s ...")` | a note's label (read from the note, not the prompt) | keep |
| 2716 | `log("heredoc: blocked - " + why)` | one of two fixed reasons, never the command | keep |
| 3470, 3465, 3468, 4117, 4119 | label-memory / bootstrap lines | project names from the manifest | keep |
| 242 (`swallowed`) | exception messages, cut to 200 chars | may quote data | keep, masked by Task 2 |
| update.py 177, 358; audit.py 514 | exception reprs; a fixed string | no typed text | keep (update.py masked by Task 2) |

`log()` calls not listed carry only numbers, paths, session ids or fixed text.

- [ ] Test `test_the_log_never_keeps_the_words_of_a_prompt`: a throwaway home with a transcript
  whose user messages contain a marker (`"zebra quartz marmalade 41"`). Run `--size` with that
  marker as the prompt, then `--ledger` (Stop), `--session-end` and `--bootstrap` for the same
  chat. Expect: the log has a `size: prompt=` line ending `prompt=<len> chars` with the right
  length and nothing after it; the marker appears NOWHERE in the log; the ledger DOES hold it
  (the ledger is meant to). A second prompt `afk` still logs `away: armed by 'afk'` (control).
- [ ] Code, guard.py ~2848-2850: the comment says why the length stays (it proves the build hands
  the hook the prompt) and that the words were dropped 2 Oct 2026 (review row 9); the call
  becomes `log("size: prompt=%d chars" % len(d.get("prompt") or ""))`.
- [ ] Nothing else in `--report` or the tests reads the old `%r` part (grep `prompt=` in
  test_guard.py: 0 hits at 9e016d7) - check that is still true.
- [ ] Run `python test_guard.py`; then the full gate.

## Task 2 - mask keys and tokens in the log and the ledger  (review row 9)  RISKY

One function, three writers. The ledger is "never edited", so the 3 old hits stay in the file;
they are masked on the way OUT (the pickup) instead, and the owner decides about the file (D2).

- [ ] Code, guard.py, just above `def log(msg):` (~213):
  - `SECRET_PATTERNS` - a tuple of `(kind, regex string)` pairs, compiled once into
    `_SECRET_RES`:

    | kind | regex |
    |---|---|
    | `anthropic key` | `sk-ant-[A-Za-z0-9_\-]{20,}` |
    | `openai key` | `(?<![A-Za-z0-9_\-])sk-(?:proj-\|svcacct-)?[A-Za-z0-9_\-]{20,}` |
    | `stripe key` | `\b[sr]k_(?:live\|test)_[A-Za-z0-9]{16,}` |
    | `github token` | `\bgh[pousr]_[A-Za-z0-9]{30,}` and `\bgithub_pat_[A-Za-z0-9_]{20,}` |
    | `aws key id` | `\b(?:AKIA\|ASIA)[0-9A-Z]{16}\b` |
    | `slack token` | `\bxox[abprs]-[A-Za-z0-9\-]{10,}` |
    | `google key` | `\bAIza[0-9A-Za-z_\-]{35}` |
    | `groq key` | `\bgsk_[A-Za-z0-9]{20,}` |
    | `huggingface token` | `\bhf_[A-Za-z0-9]{30,}` |
    | `bot token` | `\b\d{8,10}:[A-Za-z0-9_\-]{35}\b` |
    | `jwt` | `\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}` |

    (the `\|` above is table escaping: the regex has a plain `|`). Anthropic is listed before
    openai, so an `sk-ant-` key is named right.
  - `GENERIC_SECRET_RE`: `(?i)(\b(?:api[_-]?key|access[_-]?key|secret|token|password|passwd|pwd|bearer|authorization)\b["']?\s*(?:[:=]|\s)\s*["']?)([A-Za-z0-9_\-./+=]{16,})`.
    The value (group 2) is masked ONLY if it has a letter AND a digit, is NOT 7-64 hex
    characters, is NOT a UUID, holds no `/` and fewer than two dots. That keeps session ids, git
    shas, our own 16-hex digests, paths, file names and versions.
  - `mask_secrets(text)`: `str()` the input, apply every `_SECRET_RES` (replacement
    `[masked <kind>]`), then the generic rule (replacement `<group 1>[masked]`). Never raises:
    on any exception return the input unchanged (a regex over a str does not raise; this is
    belt and braces).
- [ ] Code, guard.py `log()`: write `mask_secrets(msg)`. `swallowed()` goes through `log()`.
- [ ] Code, guard.py `append_ledger` (~1091-1096): write `mask_secrets(t)` in place of `t`. The
  dedupe hash (~1059) and the legacy hash (~1063) stay on the RAW `t`, so the `.seen.json`
  sidecar and the 64 KB rewind behave exactly as today.
- [ ] Code, guard.py `ledger_tail`: mask each block as it is appended to `ours` / `theirs`
  (`"### " + mask_secrets(b)`). The file is never rewritten.
- [ ] Code, update.py: `SECRET_PATTERNS` and `GENERIC_SECRET_RE` as a local copy (update.py stays
  independent of guard.py), a local `_mask`, and `_log` writes `_mask(msg)`.
- [ ] Tests:
  1. `test_mask_secrets_masks_every_known_shape`: through `guard_call`, one fake per kind (built
     from parts at runtime) inside a sentence -> the fake is gone, `[masked <kind>]` is there,
     the words around it are intact. `api_key=<fake>`, `Authorization: Bearer <fake>` and
     `password: <fake>` keep their key word and lose the value.
  2. `test_mask_secrets_leaves_ids_and_ordinary_text_alone`: unchanged for a session UUID, a
     7-, 12- and 40-char git sha, `token 0123456789abcdef` (16 hex), `token budget 20000
     tokens`, `<task-notification>`, a long hyphenated word containing `sk-`, `password reset
     flow`, `token file at C:/x/.config/token.json`, a label `Alpha -3 (2 Oct)`, and a real-shape
     log line `size: ctx=231000 level=225000 last=0`.
  3. `test_a_key_never_reaches_the_log`: `--checkpoint <sid> "rotated <fake github token>"` ->
     the log line has `[masked github token]` and not the fake; a `swallowed()` call (through
     `guard_call`) whose exception message holds a fake key logs it masked.
  4. `test_the_ledger_keeps_his_words_but_not_a_key`: a transcript message `deploy with key
     <fake google key> please` -> the ledger has `deploy with key [masked google key] please`; a
     second Stop appends nothing (the raw-text dedupe still matches).
  5. `test_an_old_key_in_the_ledger_is_masked_on_pickup_and_the_file_is_untouched`: seed the
     ledger with an entry carrying a fake bot token for this thread's chat; pick the thread up
     -> the injection holds `[masked bot token]`, not the fake; the ledger bytes are identical.
  6. `test_update_masks_with_the_same_patterns`: update.py's `SECRET_PATTERNS` and
     `GENERIC_SECRET_RE.pattern` equal guard.py's (load both by path); `update._log(home, ...)`
     with a fake key writes it masked into the throwaway home's log.
- [ ] Run `python test_guard.py`; then the full gate.

## Task 3 - delete `.used-*` archives older than 30 days  (review row 11)  RISKY

**Scope, from the owner's permission:** regular files directly in `~/.claude/handoff/` whose name
matches `*.used-*.md`. Nothing else: not `overflow/` (see "Not in this batch"), not a waiting
note, not a ledger or a sidecar, not `<key>.used.md` (no dash), not a sub-folder, not a link.

**Hook: SessionStart `--bootstrap`, once a day.** It already does the handoff housekeeping
(`sweep_stubs` retires stubs to `.used-stale-*`), it runs once per chat start rather than per
prompt (`--size`, 10 s, carries the pickup) or per turn (`--ledger`), it has a 15 s timeout, and
it prints at most one JSON object that this step never adds to. `--session-end` was ruled out:
it does not fire when a chat dies. A stamp makes every start after the first of the day cost one
small file read.

**Attribution guard.** `all_notes()` reads the newest `LABEL_SCAN_MAX` (80) notes and archives of
a project to attribute ledger entries to threads (`chat_threads`) and to number labels
(`label_numbers`). Deleting one of those would move old requests of a thread out of THIS THREAD.
So the newest 80 notes of each project key are kept whatever their age; past them, age alone
decides. Today the 80 newest of the big project are all younger than 30 days, so the guard costs
nothing here.

**Thread-number guard (added by the controller, 2 Oct, after the owner asked whether pruning
hurts anything).** `label_numbers()` reads the highest number a thread reached from these
archives. A quiet thread - one nobody touched for 30+ days in a busy project - could have ALL its
notes pruned, and its next chat would restart at -1. So, also protected whatever its age: the
newest note of every thread label (`split_label_number(note_label(f))[0].lower()`, the same key
`label_numbers` uses). Test: a 40-day-old `.used-*` note that is the only note of thread "Quiet"
survives a pass that deletes an older-numbered 40-day-old note of a thread that has a newer one;
and `label_numbers()` reports the same {thread: number} before and after the pass.

- [ ] Code, guard.py, after `sweep_stubs` (~1015):
  - `USED_KEEP_DAYS = 30`, `USED_PRUNE_MAX = 200` (files per pass), `USED_PRUNE_STAMP =
    os.path.join(STATE, "used-prune-day")` - plain text holding `YYYY-MM-DD`, not `.json`, so
    `--report`'s session list never shows it. `USED_OFF = "no-used-prune"`, added to `SWITCHES`
    as `"used_prune"` (his standing rule: everything automatic has an off switch).
  - `prune_used_notes()`: return at once if `switched_off(USED_OFF)` or the stamp holds today.
    Write today into the stamp FIRST (a crash must not make every start rescan). Then
    `os.scandir(handoff_dir())`, no recursion; a candidate is
    `e.is_file(follow_symlinks=False)` and `fnmatch(e.name, "*.used-*.md")` and not
    `e.name.endswith(".requests.md")`. Group every `.md` note of the folder (live and archived,
    not `.requests.md`) by key `name.split(".")[0]`, newest first by mtime; the first
    `LABEL_SCAN_MAX` of each key are protected. Delete (`os.remove`) each unprotected candidate
    whose mtime is more than `USED_KEEP_DAYS` days old, oldest first, at most `USED_PRUNE_MAX`.
    Log one line per file - `used-prune: deleted <basename>` (file NAME only, never a path or
    content) - then `used-prune: <n> deleted, <m> kept as the newest of their project`. A failed
    delete logs `used-prune: could not delete <basename> (<ExceptionType>)` and goes on.
    Never raises, never prints.
  - `cmd_bootstrap`: call it right after `sweep_stubs(d)`, inside its own `try` (any exception
    goes to `swallowed("prune_used_notes", e)`).
- [ ] Tests (mtimes set with `os.utime`):
  1. `test_old_used_notes_are_deleted_and_nothing_else_is`: in a throwaway handoff folder,
     first 80 `.used-*` notes for `KEY` that are 1 day old (so the old files below fall outside
     the protected newest 80 - without them nothing is deleted, by design), then, 40 days old: `KEY.aaaaaaaa.used-20260801-101010.md`, `KEY.bbbbbbbb.used-stale-20260801-101010.md`,
     `KEY.cccccccc.used-20260801-hand-renamed.md`, a waiting note `KEY.dddddddd.md`, the ledger
     `KEY.requests.md`, `KEY.requests.seen.json`, `KEY.used.md`, `overflow/x.used-20260801-101010.md`,
     and a folder `KEY.used-dir.md/`; 29 days old: `KEY.eeeeeeee.used-20260904-101010.md`.
     One `boot()` -> exactly the three 40-day `.used-*` files are gone; every other file
     (the 80 recent ones included) has the same bytes and mtime; the log has three `used-prune: deleted <basename>` lines and no
     path; stdout is empty or one JSON object.
  2. `test_the_prune_runs_once_a_day`: test 1's home after its run (same 80 recent notes), add
     another 40-day `.used-*` file and
     `boot()` again -> it survives; set the stamp to yesterday, `boot()` -> it is gone.
  3. `test_the_newest_archives_of_a_project_are_kept_whatever_their_age`: 85 `.used-*` files for
     `KEY`, all 40+ days old with distinct mtimes, from chats whose labels name two threads.
     `boot()` -> the 5 oldest are gone, the 80 newest remain; `chat_threads` and
     `label_numbers` (through `guard_call`) return the same as before the run.
  4. `test_the_prune_has_an_off_switch`: `config.json` `{"used_prune": false}` -> nothing deleted,
     no stamp written; the `no-used-prune` file does the same.
  5. `test_a_failed_delete_is_logged_and_the_rest_go_on`: through `guard_call`, make `os.remove`
     raise for one name -> the log names that file, the other old file is still deleted.
- [ ] README "Turning bits off": add `used_prune` and `no-used-prune`. "How it works", the
  `--bootstrap` row: "once a day deletes consumed `.used-*` notes older than 30 days (the newest
  80 of each project are kept)".
- [ ] Run `python test_guard.py`; then the full gate.

## Task 4 - neighbour requests become opt-in  (review row 11)  RISKY

Today every pickup appends `-- OTHER CHATS IN THIS FOLDER --`: other threads' requests (up to
`LEDGER_OTHERS` / `LEDGER_OTHERS_CHARS`), and, in a folder with no attribution yet, up to
`LEDGER_TAIL` of everyone's (`ledger_tail`, guard.py ~1660-1689). For a stranger that hands one
chat the words typed in another. New key in `config.json`: `"neighbour_requests"`. **Only a
literal JSON `true` turns it on** - the mirror of batch C's rule that only literal `false`
turns a part off. Absent, `false`, `"yes"`, `1` -> off.

**How the owner keeps today's behaviour - install.py writes the key, and why that is the safest.**
His config.json does not exist. The hooks run the copy in `bin/`, and new code reaches `bin/`
ONLY through `install.py` (directly, or via `update.py`'s `_after_update`). So if install.py
writes the key in the same run, BEFORE it copies the new guard.py, there is never a moment when
his chats run the new code without it, and it covers every other existing install the same way.
A controller-only edit would leave a window between the copy and the edit, and would miss any
other existing install. The rule:

- an **existing install** = `not is_fresh(current)` (settings already hold a hook of ours) OR
  `bin/source.json` exists;
- if existing and config.json has no `neighbour_requests` key -> set it to `true`, keeping every
  other key; a missing config.json is created holding just that key;
- a fresh install writes nothing (the default is off);
- a config.json that is not a JSON object, on an existing install -> REFUSE: print the path and
  "fix or move it first", exit 1, copy nothing, change no settings (the batch C all-or-nothing
  rule). A fresh install with a broken config.json is not refused (nothing to keep);
- the key once present is never touched again, `false` included. To turn it off, set `false`.

- [ ] Code, guard.py: `def switched_on(key)` beside `switched_off` -> `_config().get(key) is
  True`, never raises. `NEIGHBOUR_KEY = "neighbour_requests"`.
- [ ] Code, guard.py `ledger_tail` (~1660): when `not switched_on(NEIGHBOUR_KEY)`, count the
  entries that would have gone to the neighbour side (after the summon-label and noise
  filtering, before the `LEDGER_OTHERS` cut), set `theirs = []`, and, if that count is above 0,
  add one line in place of the block:
  `-- <n> request(s) from other chats in this folder are not shown (they are in the ledger named above; "neighbour_requests": true in ~/.claude/context-guard/config.json shows them) --`.
  With the key on, the output is byte-identical to today.
- [ ] Code, install.py: `config_path(home)`, `neighbour_plan(home, current)` -> `None` (nothing
  to do), `"write"`, or a problem string; `write_config_key(home)` writes `config.json.tmp`
  then `os.replace` (`json.dumps(obj, indent=2) + "\n"`, UTF-8, LF). In `main()`: compute it
  next to `copy_plan`; a problem -> print `REFUSING: ...`, return 1; it counts as a change in
  the "Already up to date" test; `--dry-run` prints `would set "neighbour_requests": true in
  <path>`; on a real run it is written BEFORE `install_copy`; print `kept your current
  behaviour: "neighbour_requests": true in <path>`. `--uninstall` never touches config.json.
- [ ] Tests:
  1. Six existing tests need the neighbour block and get `neighbours_on(home)` (a new helper
     that writes `{"neighbour_requests": true}` to the throwaway config.json) before their
     pickup, assertions unchanged: `test_the_injected_tail_separates_this_thread_from_the_others`,
     `test_the_tail_still_reads_when_nothing_is_attributed`,
     `test_older_entries_get_their_chat_back`, `test_a_quiet_thread_still_gets_its_own_words`,
     `test_an_unattributed_project_still_gets_a_full_tail`,
     `test_the_neighbour_share_of_the_tail_is_bounded_too`. (Measured: these six hold all 12
     checks that fail with the block off, and no other check fails.)
  2. `test_neighbour_requests_are_off_by_default`: two threads (alpha, beta) with notes and
     ledger entries; pick up alpha with no config -> THIS THREAD holds alpha's words; beta's
     words are absent; no `-- OTHER CHATS IN THIS FOLDER` header; the one-line pointer gives the
     right count. Repeat with `{"neighbour_requests": "yes"}`, `1` and `false`: same.
  3. `test_neighbour_requests_on_is_todays_output`: same home with `true` -> the injection is
     byte-identical to what `9e016d7` produces (build the expected text by running the
     unchanged guard.py from `git show 9e016d7:guard.py` written to a throwaway folder beside
     copies of the other runtime files, or assert the header, both sides and the order - the
     implementer picks one and says which).
  4. `test_an_unattributed_folder_gets_only_the_pointer_when_off`: no notes at all, 5 ledger
     entries -> off: no entry text injected, the pointer says 5; on: today's full tail.
  5. `test_install_keeps_an_existing_installs_neighbours_on`: (a) seed settings with our six
     hooks (an existing install), no config.json -> after `run_install`, config.json is
     `{"neighbour_requests": true}`, written before the copy (assert config.json's mtime <=
     `bin/guard.py`'s); a second run says "Already up to date". (b) existing install with
     `{"memory_nag": false}` -> both keys after. (c) existing install with
     `{"neighbour_requests": false}` -> unchanged. (d) fresh home -> no config.json created.
     (e) existing install with config.json `[1]` or `{not json` -> rc 1, `REFUSING`, no `bin/`
     change, settings byte-identical. (f) `--dry-run` on (a) prints the `would set` line and
     creates nothing. (g) `--uninstall` leaves config.json as it was.
- [ ] README "Turning bits off" gets a short "Turning bits on" line: `neighbour_requests` - a
  pickup also shows other chats' requests from the same folder (off unless set to `true`; an
  install that existed before this change gets `true` written for it).
- [ ] Run `python test_guard.py`; then the full gate.

## Task 5 - the source folder comes from the manifest  (review row 11)  RISKY

Today (guard.py ~3138-3148) `SOURCE_KEY` and `SOURCE_DIR` are literals naming the owner's own
folder, and `SHARED_MEMORY` is built from them at import. Uses: `_resolve_key_dir` (~3309),
`label_memory_index`'s text (~3484), `list_header` (~3492), `_memory_folders` (~3621),
`_project_of` (~3659, ~3663), `_bootstrap_list` (~4097).

**Design.** A new optional top-level manifest key, `"source_dir"`: the folder whose memory
folder is the shared one. Read LAZILY, at most once per process, so the Bash hook and every
other hook that never needs it read no extra file, and nothing at import can raise (the
`_ceiling()` trap).

- [ ] Code, guard.py: replace the three constants with `_SOURCE = None` and `def _source():` ->
  `(source_dir, source_key, shared_memory)`, cached. `source_dir` = the manifest's
  `source_dir` if it is a non-empty `str` (stripped), else `""`; `source_key =
  dir_key(source_dir)`; `shared_memory = os.path.join(PROJECTS, source_key, "memory")` if
  `source_key` else `""`. No `isdir` check (the tests and a CI runner use a folder that does not
  exist). An unreadable manifest -> `swallowed('_source', e, quiet=MISSING)` and `("", "", "")`.
  Keep the comment's history, and say the literals moved into the manifest on 2 Oct 2026.
- [ ] Each use, with NO source folder (a stranger, or a manifest without the key):
  - `_resolve_key_dir`: the candidate list starts empty instead of `[SOURCE_DIR]`.
  - `_memory_folders`: no folder sorts first; plain sorted order.
  - `_project_of`: no key is excluded or special-cased - a slug claimed only by its own folder
    names that folder's project; nothing turns into "general" because of the source key.
  - `_bootstrap_list`: after the manifest is read, no source folder -> log `bootstrap: no
    source_dir in the manifest - declining to guess` and return "".
  - `label_memory_index`: the sentences naming the shared folder are left out; it still names
    the list it read.
  - `list_header`: only reached from the bootstrap, which has already declined.
  With the source folder set, every one of them behaves exactly as today.
- [ ] Tests:
  1. Add `"source_dir": CWD` to `BOOT_MANIFEST` and `KW_MANIFEST`, and to `MANIFEST_THREADS`
     (or have `write_threads_manifest` add it when `man is None`). Measured: these three
     fixtures hold all 26 checks that fail without it, and nothing else fails.
  2. `test_the_source_folder_comes_from_the_manifest`: with `{"source_dir": CWD, ...}`,
     `_source()` (through `guard_call`) equals `(CWD, KEY, <home>/.claude/projects/KEY/memory)`.
  3. `test_no_source_folder_is_a_quiet_stranger`: no manifest -> `("", "", "")`; a manifest
     without the key, a `null`, a `3`, an empty string -> the same; `{not json` -> the same and
     one `error:` log line; importing guard with each of these never raises (a `guard_call`
     that only imports exits 0).
  4. `test_a_stranger_without_a_source_folder`: (a) `boot()` in a manifest project's folder ->
     no list written, the `no source_dir` log line, stdout empty; (b) a label fetch -> the
     project's index arrives, the text has no "shared folder"; (c) the catalogue: a memory in
     `KEY`'s folder, claimed by no list, gets `_key_name(KEY, projects)` as its project (the
     folder key itself when no manifest project claims that folder), not "general".
  5. `test_the_bash_hook_reads_no_manifest`: a `--bash` call with a harmless command on a home
     whose manifest path is a FOLDER (any read would fail and log) -> rc 0, no output, no
     `error:` line.
- [ ] README, in the "The label also fetches that project's memories" paragraph: one sentence on
  `"source_dir"` - the folder whose `memory/` holds the shared files; without it, the bootstrap
  and the shared-folder wording stay off.
- [ ] Run `python test_guard.py`; then the full gate.

## Task 6 - GitHub Actions on Windows, macOS and Linux  (review row 11)

`suite.py` lives OUTSIDE the repo and runs only `python -W ignore::SyntaxWarning test_guard.py`
with `PYTHONIOENCODING=utf-8` and `PYTHONDONTWRITEBYTECODE=1`. `test_guard.py` is the only test
file in the repo, so CI runs that, with the same two variables. Platform code that already
exists: `child_env` sets `HOME` off Windows (and unsets it on Windows), `update._git` kills a
hung git with `taskkill` only on Windows, and `.gitattributes` (`* -text`) keeps guard.py's
CRLF on every checkout. Measured: the whole suite passes on Linux with no change, so **no check
is skipped** - nothing in it turned out to be Windows-only at the check level (the heredoc guard
is Windows-motivated, but its tests are text tests and pass on Linux). Mac support is deferred
by the owner: a mac failure is information for the report, not something to fix here.

- [ ] Fold-in (cheap, self-contained): test_guard.py ~954 has `"^def \|^# ---"` inside a
  normal string - an invalid escape and a SyntaxWarning (an error in a future Python). Make it
  `\\|`: the string's VALUE is unchanged (an invalid escape keeps its backslash). Test
  `test_the_test_file_compiles_without_warnings`: `compile()` test_guard.py, guard.py, audit.py,
  install.py and update.py under `warnings.simplefilter("error")` -> no exception.
- [ ] Write `.github/workflows/tests.yml` (LF):
  - `name: tests`; `on: push` (branches `main`) and `pull_request`; `permissions: contents: read`.
  - one job, `strategy.fail-fast: false`, `matrix.os: [windows-latest, macos-latest,
    ubuntu-latest]`, `runs-on: ${{ matrix.os }}`, `timeout-minutes: 30`.
  - `continue-on-error: ${{ matrix.os == 'macos-latest' }}`, commented: mac support is
    deferred by the owner, so its result is information and not a gate.
  - steps: `actions/checkout@v4`; `actions/setup-python@v5` with `python-version: "3.12"`;
    then two steps with `env: {PYTHONIOENCODING: utf-8, PYTHONDONTWRITEBYTECODE: "1"}`:
    `if: runner.os == 'Windows'` -> `python -W error::SyntaxWarning test_guard.py`, and
    `if: runner.os != 'Windows'` -> `python3 -W error::SyntaxWarning test_guard.py`.
  - no secrets, no tokens, no `secrets.` anywhere, no write permissions. The tests set their own
    git identity (`-c user.name=... -c user.email=...`), so no git config step.
- [ ] Test `test_the_ci_workflow_runs_the_in_repo_suite` (reads the yaml as text; no yaml
  library): it names all three OSes, `actions/checkout@v4`, `actions/setup-python@v5`, a
  `python-version`, `test_guard.py` with `python3` and with `python`, `contents: read`; it does
  NOT contain `secrets.`, `suite.py` or `Claude Needed Tools`; it is LF.
- [ ] Run `python test_guard.py`; then the full gate. CI itself first runs when the controller
  pushes (C3); this task cannot see it.

## Task 7 - README: the desktop-app-only features, and badges that cannot drift  (review row 11)

Badges at `9e016d7`: `checks-1279 passing` (the suite has 1,691 and grows every task) and
`hooks-5` / alt "Five hooks" (`install.HOOKS` has **6** rows). The Tests section repeats "1279
checks". Desktop-app-only: everything that needs a `mcp__ccd_*` tool. guard.py names, at
`9e016d7`: `list_sessions`, `get_session`, `archive_session`, `set_remote_control`,
`send_message`, `set_session_effort`, `clear_session` (session management),
`set_session_title` (named after a split prefix at ~2483-2484, and bare at ~3038),
`move_sessions` (sidebar) and `change_directory` (directory). In the terminal those tools do not
exist, so these steps cannot happen: the old chat archiving itself, the new chat renaming itself
with the numbered label, setting the next chat's effort, telling the old chat it was picked up,
landing in the same sidebar group, and a folderless chat moving into the thread's folder. The
size warnings, notes, ledger, ceiling, stubs, away mode, pause, audit and updates do not need
them. (`get_usage` is NOT used by Context Guard - leave it out.)

- [ ] README:
  - a `### Desktop app only` subsection under "Using it" listing those steps in plain words,
    each with its tool name in backticks, and one line that everything else works in the
    terminal too. Do not claim what a terminal chat does when told to call a missing tool - that
    was never measured.
  - "Using it": the archive/sidebar/effort sentences get "(desktop app)".
  - badges: `hooks-6` with alt `6 hooks`; checks as a floor, `checks-<N>%2B` with alt `<N>+
    checks`, `N` = the PASS count after this task rounded DOWN to the hundred; the Tests
    section says the same `<N>+`. Add the CI badge
    `https://github.com/Masry-code/context-guard/actions/workflows/tests.yml/badge.svg`.
  - "What it does not do": replace "It has only ever been run on Windows" with: CI runs the
    suite on Windows, macOS and Linux on every push; the hooks have been used daily on Windows
    only, and macOS support is not finished.
- [ ] tour.md item 1: add "(desktop app)" after the archive/sidebar words, so tour and README
  agree.
- [ ] Tests:
  1. `test_the_readme_names_every_desktop_only_tool`: collect the tool names from guard.py
     source - `re.findall(r"mcp__ccd_[a-z]+(?:_[a-z]+)*?__([a-z_]+)", src)` plus every bare
     `\bset_session_[a-z_]+` - and assert each one appears inside README's `### Desktop app
     only` section (from that heading to the next `#` heading). A new desktop tool in guard.py
     then fails this test until the README says so.
  2. `test_the_tour_and_the_readme_agree_on_desktop_only`: tour.md's item 1 says "(desktop
     app)"; the README section exists and mentions the archive and the sidebar group.
  3. `test_the_hooks_badge_counts_install_hooks`: README holds `badge/hooks-%d-` and alt
     `%d hooks` for `len(inst_module().HOOKS)`.
  4. The checks badge: `check()` counts every call in a module-level counter; after the runner
     loop (before the FAILED summary), `readme_checks_badge_agrees(total)` reads the `checks-
     <N>%2B` badge and the Tests-section `<N>+` and checks: both equal, `N <= total` (the
     badge never claims more than ran) and `total < N * 1.25` (it is bumped before it goes
     stale). Why this shape: a test cannot know the final count while the suite is still
     running, an exact number would break on every new check, and a floor with a band stays
     true across a batch yet still fails once the README is far behind.
- [ ] Run `python test_guard.py`; then the full gate.

---

## Not in this batch

- `~/.claude/handoff/overflow/` is NOT pruned: the owner has not decided whether overflow files
  may be deleted. Task 3 never enters that folder.
- Mac support itself (deferred by the owner). CI reports it; nothing is fixed for it here.
- The README's "Python 3.8+" claim: CI runs 3.12 only (Linux was also measured on 3.10).

## Controller steps (not implementer tasks)

- **C1 - give the owner's manifest its source folder, BEFORE the live install.** Read the two
  literals from `git show 9e016d7:guard.py` (`SOURCE_DIR`, `SOURCE_KEY`). Insert one top-level
  line, `"source_dir": <SOURCE_DIR as a JSON string, backslash escaped>,`, right after the
  opening `{` of `~/.claude/context-guard/memory-manifest.json`, with a Python script written
  with the Write tool (anchor on the file's first line, abort if not unique; the file is LF -
  count before and after; `json.load` it afterwards). Today's installed code ignores an unknown
  top-level key, so this edit is safe before the copy changes. Prove: with the NEW guard.py
  (clone) loaded by path under the real home (read-only - import and call `_source()` only),
  `_source()` returns exactly the two old literals and the old `SHARED_MEMORY` path.
- **C2 - live install.** `python install.py --dry-run` (expect: the `would set
  "neighbour_requests": true` line, the copy list, no hook path change), show the owner, then
  `python install.py`. Prove: `~/.claude/context-guard/config.json` now holds
  `"neighbour_requests": true`; the next pickup in a shared folder still shows the OTHER CHATS
  block; a new `size: prompt=` log line ends at `chars`; the day's first chat start leaves a
  `used-prune:` summary line (0 deleted today).
- **C3 - push and read CI.** Push (the names-guard hook runs). `gh run watch` the first
  `tests` run; record the result per OS. A mac failure goes in the end-of-build report as
  information (failing check names, counts), not a fix.

## Controller decisions (owner)

- **D1:** scrub the 1,000 old `size: prompt=` previews out of the existing log (rewrite each
  such line to end at `chars`; the log is a CRLF text file on Windows - preserve it), or leave
  them. Task 1 only stops new ones.
- **D2:** the ledger holds 3 secrets from before Task 2 (counts above). The ledger is "never
  edited" by design, so the batch masks them only on the way into a chat. Mask them in the file
  too (a one-off, owner's yes), and rotate those credentials either way.
- **D3:** mac in CI is `continue-on-error`. Make it a hard gate once mac support is built.

## Done when

- All seven commits are in, the full gate is rc=0, and a reviewer has checked the batch.
- `guard.py` bare LF is still 0; every other repo file still 0 CRLF.
- C1-C3 are done and proved.
