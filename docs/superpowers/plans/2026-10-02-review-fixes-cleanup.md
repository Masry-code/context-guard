# Review fixes, batch C (cleanup) - Implementation Plan

> **For agentic workers:** one task per implementer, in order. Steps use checkbox (`- [ ]`) syntax.

**Goal:** delete the parts that almost never run, put the off switches in one file, and run
the hooks from an installed copy so a half-finished edit in the clone cannot break every open
chat at once.

**Source:** the 2 Oct honest review, fix-list rows 6 and 7. The owner picked "Cleanup" on
2 Oct 2026. What he was shown: "Removes parts that almost never run (about 420 lines), and
runs Context Guard from an installed copy, so a half-finished edit can't break all your chats
at once."

**Tech stack:** Python 3 standard library only. Unit suite `python test_guard.py`. Full gate
`python "D:/AI Projects/Claude Needed Tools/safeguard-c/suite.py"` (expect rc=0, about 170 s).

---

## What was measured before this plan (2 Oct 2026, HEAD d7d0f86)

`~/.claude/context-audit.log` starts 11 Sep 2026 (4,790 lines). Transcripts: every
`~/.claude/projects/*/*.jsonl`, Bash/PowerShell `tool_use` commands only.

| piece | claim | measured |
|---|---|---|
| `--reread` (PreToolUse/Read) | fired 3x ever, all tests | 3 `reread: flagged` lines, all 11 Sep 10:29-10:31, on `probe.png` / `hooktest.png`. Still installed in settings.json, so it runs on every Read. CONFIRMED. |
| memory sweep | never ran | 59 `sweep:` lines, all `switched off by ...no-memory-sweep - skipped` (29 Sep - 2 Oct). 0 `sweep: done in`, 0 moves. The off file dates from 25 Sep 18:59. 0 `rollout:` lines, no rollout scripts on disk, 0 of 11 project lists carry the one-home header. CONFIRMED: never ran. |
| `--skills` (the command) | never runs | 2 real runs, both 17 Sep (the day it was built). CONFIRMED for the command. **But** the same scan is appended to every handoff warning (`skill_proposals(path)` at the end of `_size_check`): 147 injections in 98 chats, 17 Sep - 30 Sep. The scan is NOT dead. |
| `--attribute` | never runs | 2 real runs, both 17 Sep (when built). Its one output, `<key>.requests.attrib.json`, exists and `ledger_tail()` still reads it on every pickup. |
| `ledger-budget.py` | never runs | about 9 real runs, all 19-20 Sep (when `LEDGER_OWN_CHARS` was tuned), none since. The `ledger: budget dropped` log line it was built to read has fired 26 times; that row in `--report` stays. |

Nothing outside the repo imports the deleted code, except the safeguard (c) rollout (below)
and one line in the open-chat skill (controller step C2).

**Does deleting the sweep cancel an owner request? Yes.** guard.py's sweep comment (and
test_guard.py's sweep section) quote his rule of 25 Sep 2026: *"make sure moving forward all
chats saves the memories in the correct folder or at least move them when possible at the
start of chat with a hook"*. He then approved the design with "a" to "Do parts 2 and 3 look
right?", where part 3 is the sweep. The safeguard (c) plan's goal line: "a SessionStart sweep
moves strays home without deleting anything". Its Tasks 12-16 (the rollout: pin spares, give
every list the header, dry run, real run, remove the off switch) never ran, and every one of
them calls code this plan deletes (`ensure_list_header`, `sweep_memory_strays`, `_move`,
`_backup_dir`, `_read_bytes`, `_entries`, `SWEEP_OFF`). Today 63 topic files sit in 11
project folders as spare copies. Part 2 (the label fetch names the shared folder, and new
memories go there) is NOT touched. Task 4 is gated on that confirmation.

---

## Machine rules (every task)

- `guard.py` line endings: MEASURE before you touch it (count `\r\n` and bare `\n` in raw bytes
  with Python). `guard.py` is CRLF: 4891 CRLF and 0 bare LF at `d7d0f86`.
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

Batch C additions:
- `update.py` is LF too (289 LF, 0 CRLF at `d7d0f86`). Edit it with the Edit tool.
- A deleted test also leaves the runner tuple. Grep the tuple for every name you delete.
- The working-tree rule above stays true until the controller runs the new installer (step C1).
  After that the hooks run from `~/.claude/context-guard/bin/`, and the tests never touch it:
  every installer test passes `--home <throwaway>`.

---

## Task 1 - delete the image re-read hook  (review row 6)

Anchors in guard.py: the module docstring's `--reread  PreToolUse/Read ...` two lines (top of
file); `IMAGE_EXT` and `FREE_REREADS` (just below `LEVELS`); `def cmd_reread():` (~3187-3229);
the `REPORT_EVENTS` row `("image re-reads blocked", "reread: flagged", True)`; in `cmd_report`
the session line `"  %-40s last warned at %3dk, %d image(s) tracked"`; the dispatch branch
`elif "--reread" in a:`.

- [ ] Tests:
  1. `test_install_drops_a_stale_reread_hook`: seed settings with the OLD shape - our seven
     entries as an older install wrote them (copy `install.HOOKS` and add
     `("PreToolUse", "Read", <guard path>, "--reread", 10, None)`, built with `desired_entry`)
     plus a FOREIGN PreToolUse/Read hook (`"command": "echo other-tool"`). Run `install.py`.
     Expect: no command anywhere contains `--reread`; the foreign Read hook is still there,
     unchanged; exactly one `--bash` PreToolUse entry. A second run says "Already up to date".
  2. `test_uninstall_also_removes_a_stale_reread_hook`: the same seed, then `--uninstall`:
     no `--reread` left, the foreign hook kept.
  3. `test_a_leftover_reread_hook_is_a_silent_no_op`: run `guard.py --reread` with a Read
     payload for an existing `.png` three times: rc 0, empty stdout every time, and no
     `reads` key in that session's state file (a settings.json not yet re-installed must not
     break Read).
  4. `--report` has no `image re-reads` row (assert the substring is absent).
- [ ] Rewrite the report fixture so the report tests keep proving the same things without
  the deleted row: in `REPORT_LOG` replace the three `reread: flagged` lines with three
  `ledger: budget dropped 3 request(s) of thread alpha` lines on 11 Sep (same times). In
  `test_report_shows_what_was_blocked_not_just_how_many` assert `"thread alpha"` and
  `"11 Sep"`; in `test_report_flags_a_counter_that_only_ever_fired_on_one_day` look on the
  `ledger budget drops` row; in `test_report_counts_the_guards_that_were_added_later` swap
  `"image re-reads blocked"` for `"ledger budget drops"`. Update the comment block above
  `REPORT_LOG` in one line (the re-read guard was deleted 2 Oct 2026).
- [ ] `test_install_is_idempotent`: the PreToolUse matchers are now `["Bash"]`.
- [ ] Code, install.py: drop the Read row from `HOOKS`. Keep `"--reread"` recognisable as ours:
  add `RETIRED_FLAGS = ("--reread",)` with a one-line comment (an older install wrote it;
  a re-run must take it out), and have `is_ours` match `FLAGS + RETIRED_FLAGS`. `build()`
  must walk every event that holds an entry of ours, not only the events in `HOOKS`:
  `events = set(HOOKS events) | {ev for ev, lst in hooks.items() if isinstance(lst, list) and any(is_ours(e) for e in lst if isinstance(e, dict))}`.
  An event left with no entries is popped (the existing rule).
- [ ] Code, guard.py: delete the anchors above. `cmd_report`'s session line keeps the warned-at
  part only. A stale `reads` key in an old state file is ignored.
- [ ] README: delete the `guard.py --reread` hooks-table row. `audit.py`'s "read any image
  exactly once" standing rule stays (it is advice, not this hook).
- [ ] Run `python test_guard.py`; then the full gate.

## Task 2 - delete `--attribute` and `ledger-budget.py`  (review row 6)

Keep `attrib_path()`, `entry_key()` and the sidecar READ in `ledger_tail()`: the real sidecar
exists and holds recovered attributions for old ledger entries. Only the writer goes.

- [ ] Tests:
  1. Rewrite `test_older_entries_get_their_chat_back` to seed the sidecar directly instead of
     running `--attribute`: write `KEY + ".requests.attrib.json"` in the handoff folder as
     `{entry_key(<block>): "guardaaa"}`, computing the key with `guard_call` on the ledger's
     first block. The rest of the test (pickup sorts the old entry into THIS THREAD, the other
     thread's stays on the other side) is unchanged.
  2. `test_attribute_is_gone`: `guard.py --attribute <KEY>` exits 0, prints nothing, and writes
     no `.attrib.json`; `ledger-budget.py` does not exist beside guard.py.
- [ ] Delete tests: `test_attribute_never_edits_the_ledger`,
  `test_attribute_refuses_to_guess_when_two_chats_said_it`,
  `test_the_budget_tool_reads_the_recovered_attribution_too`,
  `test_the_budget_tool_and_the_pickup_agree_on_the_thread`,
  `test_the_budget_tool_still_refuses_to_guess_without_the_sidecar`,
  `test_the_budget_verdict_always_names_a_number`, `test_the_token_column_is_the_cost_of_one_pickup`;
  helpers `run_attribute`, `setup_two_threads` (if test 1 no longer needs it), `BUDGET`,
  `run_budget`, `budget_rows`, `budget_unattributed`, `setup_recovered_thread`.
  KEEP `write_old_ledger` (used by `test_an_unattributed_project_still_gets_a_full_tail`) and
  `append_new_entry` (used by `test_the_neighbour_share_of_the_tail_is_bounded_too`).
- [ ] Code: `git rm ledger-budget.py`. In guard.py delete `def cmd_attribute():` (~2682-2768)
  and its dispatch branch. Comments that name the deleted tools get one honest clause each:
  `ledger_tail`'s "recovered once by --attribute" -> "recovered once by a since-deleted
  command (2 Oct 2026)"; the `LEDGER_OWN_CHARS` comment "once ledger-budget.py could see all
  of it" keeps its numbers and adds "(that tool was deleted 2 Oct 2026)".
- [ ] README: delete the `--attribute` and `ledger-budget.py` rows of "Commands you can run
  yourself" and the whole "Run `--attribute` before `ledger-budget.py`" paragraph.
- [ ] Run `python test_guard.py`; then the full gate.

## Task 3 - delete the `--skills` command  (review row 6)

**Default (what this task builds):** only the by-hand command goes. The skill-candidate block
in the handoff warning stays, because it fires (147 injections in 98 chats) - see "Controller
decisions" D1 for the alternative.

- [ ] Tests:
  1. Rewrite the engine tests that ran the command so they call the engine:
     `test_skills_proposes_a_shape_repeated_across_chats`,
     `test_skills_ignores_a_shape_repeated_inside_one_chat`,
     `test_skills_does_not_propose_the_shapes_it_actually_found`,
     `test_skills_still_proposes_a_script_run_by_path` now get their candidates from
     `guard_call(home, "print(json.dumps(guard.skill_candidates(<a path in KEY's folder>)[0]))")`
     and assert on the shapes. Same fixtures, same expected shapes.
  2. `test_skills_writes_nothing_at_all` becomes: a handoff warning on a home with repeated
     shapes leaves the `snapshot(home)` identical apart from the files the warning itself
     writes (state json and log) - or, simpler, assert `guard.py --skills <KEY>` now exits 0,
     prints nothing and the snapshot is unchanged.
  3. `test_handoff_offers_the_skill_candidates` and
     `test_handoff_stays_quiet_when_nothing_is_repeated` stay green untouched.
- [ ] Delete `run_skills` and `proposed` if nothing else uses them. KEEP `snapshot` (used by the
  report and weekly tests).
- [ ] Code: delete `def cmd_skills():` (~2659-2680) and its dispatch branch. Fix the
  `# --skills scan.` comment above `SKILL_MIN_CHATS` to "the skill-candidate scan".
- [ ] README: delete the `guard.py --skills` row.
- [ ] Run `python test_guard.py`; then the full gate.

## Task 4 - delete the memory sweep  (review row 6)

**STOP before starting:** the controller must have the owner's yes, in chat, to cancelling
the sweep half of his 25 Sep rule and safeguard (c) Tasks 12-16 (see the top of this plan).
Without it, skip this task and go on to Task 5.

Anchors in guard.py: delete from the line
`# ------------------------------------------- one home: the list header, and safe writes`
(~3650) up to, NOT including, the line
`# --------------------------------------------------- memory keywords (spec 2026-10-02)`
(~4123): about 473 lines (`LINK_RE_B`, `SWEEP_BACKUPS`, `_read_bytes` ... `_sweep_folder`,
`with_list_header`, `ensure_list_header`, `SWEEP_*`). KEEP `LIST_HEADER_LEAD`, `list_header()`
and `index_lines()` (above that line): `_bootstrap_list()` uses them.

- [ ] Before deleting, grep guard.py, audit.py, update.py, install.py for every deleted name
  (`_read_bytes`, `_eol`, `_free_path`, `_put_new`, `_move`, `_fresh`, `_entries`, `_slug_b`,
  `_edit_list`, `with_list_header`, `ensure_list_header`, `LINK_RE_B`, `SWEEP_`). Measured at
  d7d0f86: every use is inside the block (the word `_move` at ~2195 is inside prompt text).
  If that has changed, STOP.
- [ ] Tests:
  1. `test_bootstrap_leaves_stray_memories_where_they_are`: a home with a manifest, a project
     folder holding `MEMORY.md` plus a topic file `alpha-notes.md` (mtime an hour ago), an
     empty shared folder. Run `--bootstrap` twice (with and without a `no-memory-sweep`
     file). Both times: the topic file is still in the project folder with the same bytes;
     nothing is created in the shared folder or in `~/.claude/memory-backups/`; the log has
     no `sweep:` line; stdout is empty or one json object.
  2. Rewrite `test_bootstrap_and_the_sweep_print_one_json_object` as
     `test_bootstrap_prints_one_json_object`: a cwd in the manifest with no list yet plus a
     `tour-pending` flag -> stdout parses as exactly ONE json object holding both texts.
- [ ] Delete the test section from `# ---...--- one home: the list header and the sweep`
  (~6679) up to `# ---...--- update notice + --update` (~7685), EXCEPT the helpers used
  elsewhere: `guard_call` (18 uses outside) and `memory_folder` (4 uses outside). Move those
  two, unchanged, to just above the update-notice header under a one-line comment. Delete
  every sweep test from the runner tuple.
- [ ] Code: in `cmd_bootstrap()` remove the sweep block (the `SWEEP_OFF` check and the
  `sweep_memory_strays()` call) and drop `swept` from the joined text; fix its comment.
  Keep `"sweep-seen"` in `NOT_A_SESSION_STATE` (an old file may still exist in a home).
  `_memory_folders`' docstring: "a spare that the one-home sweep has not tidied yet" ->
  "a spare copy in a project folder".
- [ ] README: in the "One home for every memory file" paragraph remove the sweep sentences
  (from "At the start of every chat the SessionStart hook sweeps strays home" to the end of
  that paragraph) and say instead that spare copies in project folders are left alone. The
  `--bootstrap` hooks row loses "sweeps stray memory files home,"; the `--ledger` row's "from
  the `--bootstrap` sweep" becomes "from `--bootstrap`" (that is `sweep_stubs`, which stays);
  delete the `no-memory-sweep` off-switch bullet; the "moving memory files" words in the
  "no - hook entry points" row go.
- [ ] Docs: add one line under the title of `docs/superpowers/plans/2026-09-25-safeguard-c-memory-one-home.md`:
  `> 2 Oct 2026: Tasks 11-16 cancelled - the sweep was deleted in batch C (2026-10-02-review-fixes-cleanup.md, Task 4).`
- [ ] Run `python test_guard.py`; then the full gate.

## Task 5 - one config.json for the off switches  (review row 6)

Today seven empty files switch things off (after Task 4): `no-ceiling`, `no-memory-nag`,
`no-note-head-check`, `no-heredoc-guard`, `no-memory-hints`, `no-keywords-check` (guard.py) and
`no-update-check` (update.py). They become keys of `~/.claude/context-guard/config.json`, a
key set to JSON `false` switches that part off. The old files keep working.

| old file | config key |
|---|---|
| `no-ceiling` | `ceiling` |
| `no-memory-nag` | `memory_nag` |
| `no-note-head-check` | `note_head_check` |
| `no-heredoc-guard` | `heredoc_guard` |
| `no-memory-hints` | `memory_hints` |
| `no-keywords-check` | `keywords_check` |
| `no-update-check` | `update_check` |

- [ ] Tests:
  1. `test_config_json_switches_each_part_off`: for each of the four Stop/Bash/prompt
     switches with an existing off-file test (`test_the_memory_nag_has_an_off_switch`,
     `test_the_head_check_has_an_off_switch`, `test_the_ceiling_has_an_off_switch`,
     `test_the_heredoc_guard_has_an_off_switch`, the hints test ~8719, the keywords test
     ~8795) repeat the same scenario with `config.json` = `{"<key>": false}` and NO file:
     the part is silent. Keep the old-file tests as they are (they prove the old files work).
  2. `test_config_json_only_literal_false_switches_off`: `{"memory_nag": "no"}`,
     `{"memory_nag": 0}` and `{"memory_nag": true}` leave the nag ON.
  3. `test_a_broken_config_json_switches_nothing_off_and_is_logged`: `config.json` = `{not json`
     -> the nag still fires and the log gets one error line (through `swallowed`). A missing
     file logs nothing. A JSON list (`[]`) counts as empty.
  4. `test_update_check_obeys_config_json`: `update.check()` with `{"update_check": false}`
     makes no git call (reuse the "no git call" approach of
     `test_update_off_switch_and_no_git_folder_make_no_git_call`).
  5. `test_every_switch_is_in_the_readme`: every key of `guard.SWITCHES` plus `update_check`
     appears in README.md, and every old file name still does too.
- [ ] Code, guard.py: near `NAG_OFF`, add `CONFIG = os.path.join(STATE, "config.json")`,
  `SWITCHES = {NAG_OFF: "memory_nag", HEAD_OFF: "note_head_check", CEILING_OFF: "ceiling",
  HEREDOC_OFF: "heredoc_guard", HINTS_OFF: "memory_hints", KEYWORDS_OFF: "keywords_check"}`
  (define it after the last of those constants), and
  `def switched_off(off_file):` -> True when `STATE/off_file` exists, or when config.json is
  a JSON object whose `SWITCHES[off_file]` value `is False`. Read config.json at most once per
  process (module-level cache). Never raises. Replace the six
  `os.path.exists(os.path.join(STATE, X_OFF))` checks (in `ceiling_block`, `memory_nag_text`,
  `note_head_block`, `cmd_bash`, `_memory_hints`, `keywords_block`) with `switched_off(X_OFF)`.
  In `cmd_bash` move the switch check BELOW `why = heredoc_offence(cmd)` / `if not why: return`,
  so the plain Bash call (the 99% path) reads no extra file.
- [ ] Code, update.py: `_check()`'s `no-update-check` test also honours
  `{"update_check": false}` in `_base(home)/config.json` (a small local reader; update.py
  stays independent of guard.py).
- [ ] The texts Claude is shown (e.g. "create an empty file at ... no-ceiling") keep naming the
  old file: it still works, and an empty file is the simplest thing to tell a chat to make.
- [ ] README "Turning bits off": lead with config.json and its keys (one example:
  `{"memory_nag": false, "ceiling": false}`), then one line that the older empty files still
  work, listing them. Drop "Create an empty file" as the first instruction.
- [ ] Run `python test_guard.py`; then the full gate.

## Task 6 - run from an installed copy  (review row 7)

install.py copies the runtime files into `<home>/.claude/context-guard/bin/` and writes the
hooks to run from there. `RUNTIME = ("guard.py", "audit.py", "update.py",
"handoff-template.md", "tour.md")` - everything the three scripts open beside themselves
(`TEMPLATE`, `TOUR_FILE`, `tour_text`'s `{GUARD}`/`{AUDIT}`, `load_audit`, `update_line`).

- [ ] Tests (every one with `--home <throwaway>`; a "source dir" is a throwaway folder holding
  copies of `install.py` + `RUNTIME`, not a git clone):
  1. `test_install_copies_the_runtime_and_points_the_hooks_there`: after `install.py --home H`,
     each `RUNTIME` file in `H/.claude/context-guard/bin/` is byte-identical to the repo's
     (guard.py keeps its CRLF); `bin/source.json` holds `{"clone": <source dir>, "files":
     {name: sha256}}`; every one of our hook commands names `.../.claude/context-guard/bin/guard.py`
     or `.../bin/audit.py`, none names the source dir.
  2. `test_the_installed_copy_runs`: with `child_env(H)`, `bin/guard.py --report` exits 0 with
     no traceback; `bin/audit.py --alert` exits 0; the prompt `context guard tour` sent to
     `bin/guard.py --size` returns the tour text with `{GUARD}` filled as the bin path (proves
     tour.md and audit.py were found beside the copy).
  3. `test_runtime_lists_every_file_the_scripts_open_beside_themselves`: scan guard.py, audit.py
     and update.py for `os.path.join(os.path.dirname(os.path.abspath(__file__)), "<name>")`
     and `os.path.join(here, "<name>")`; every `<name>` is in `install.RUNTIME`.
  4. `test_reinstall_refreshes_only_a_changed_copy`: install from a source dir; a second run
     says "Already up to date"; append a comment line to the source dir's audit.py; the third
     run copies audit.py (bin bytes match the new source) and says which file it refreshed,
     and settings.json is byte-identical to before.
  5. `test_install_refuses_a_runtime_file_that_does_not_parse`: break the source dir's guard.py
     (`def (:`); install exits non-zero, says which file, and neither `bin/` nor settings.json
     changes (seed both from a good install first).
  6. `test_install_repoints_old_working_tree_hooks`: seed settings with our six entries naming
     `D:/old/place/context-guard/guard.py`; install -> six entries, all on the bin path, none
     on the old path, foreign hooks untouched.
  7. `test_dry_run_creates_no_copy`: `--dry-run` on a fresh home prints the settings diff and
     "would copy 5 file(s) to ...bin", and creates neither `bin/` nor settings.json.
  8. `test_uninstall_removes_the_copy_only`: after install, put `config.json`, a session state
     file and `no-ceiling` in `H/.claude/context-guard/`; `--uninstall` removes our hooks and
     the whole `bin/` (including a `__pycache__` inside it) and leaves the other three files.
     A `bin/` without `source.json` is NOT removed (it is not ours) - say so and leave it.
  9. Existing installer tests stay green; update `ours()` only if needed (it matches on the
     script name, which is unchanged).
- [ ] Code, install.py:
  - `bin_dir(home)` = `<home or ~>/.claude/context-guard/bin`. `HOOKS` rows name the bin
    paths; `command_for` is unchanged. Compute the bin paths from `a.home` inside `main()`
    (today `GUARD`/`AUDIT` are module constants from `HERE`) - pass `home` into `build()`.
  - `copy_plan(home)`: the `RUNTIME` names whose bin bytes differ from `HERE`'s (missing counts
    as different). Before any write, `ast.parse` every `.py` in `RUNTIME` from `HERE`; a
    failure or a missing source file -> print it, return 1, write nothing.
  - `install_copy(home, names)`: for each name write `bin/<name>.tmp` from the source bytes
    (binary, so CRLF survives), then `os.replace`; then write `source.json` the same way.
  - `main()`: the "Already up to date" early return now needs BOTH an unchanged settings dict
    AND an empty copy plan. Order: parse check, copy, then the settings merge (backup as
    today). Print "refreshed: <names>" when files were copied. `--dry-run` prints what it
    would copy and writes nothing. `--uninstall` removes our hooks, then `shutil.rmtree(bin)`
    only if `bin/source.json` exists.
  - Module docstring: say the hooks run from the installed copy, and that edits in the clone
    go live only when `install.py` (or `install.py --update`) is run.
- [ ] Run `python test_guard.py`; then the full gate.

## Task 7 - make `--update` and the update notice fit the copy  (review row 7)

Today `update.check()` uses `HERE` as the clone; from `bin/` there is no `.git`, so the notice
would go silent. And `_apply()` returns early on "already up to date" without re-running
install.py, so a clone that is AHEAD of origin (the owner's own commits) would never reach
`bin/`.

- [ ] Tests:
  1. `upd_setup()` also commits every `RUNTIME` file (copied from `HERE_DIR`) beside
     install.py, so `_after_update`'s install has something to copy. Drop the now-redundant
     update.py copy and exclude lines in `test_install_update_flag_is_wired_and_plain_install_is_unchanged`.
  2. `test_update_check_from_the_installed_copy_reads_the_clone`: install from the upd clone
     into home H; push one commit to origin; load `H/.claude/context-guard/bin/update.py` by
     path and call `check(home=H)` -> the "update available (1 new change(s))" line naming the
     CLONE's install.py, not the bin folder.
  3. `test_the_notice_says_when_the_clone_is_ahead_of_the_running_copy`: install from a source
     dir into H; append a comment to the source dir's guard.py; `bin/audit.py --alert`'s
     systemMessage contains `run: python "<source dir>/install.py"`. Restore the bytes -> no
     such line. A home with no `bin/` (running from a clone) never shows it.
  4. `test_update_when_current_still_refreshes_the_copy`: upd clone, install into H, make a
     local commit in the clone that changes guard.py (ahead of origin, nothing to fetch);
     `apply(clone, home=H)` says "already up to date" AND bin/guard.py now matches the clone.
     `test_update_apply_when_current_says_so_and_a_zip_says_how` stays green.
  5. All existing update tests stay green, including dirty-tree refusal (a dirty clone is
     still refused, and bin is untouched - assert that too).
- [ ] Code, update.py:
  - `_source_clone(here)`: `here/source.json`'s `clone` when `here` has no `.git` and that file
    names a folder holding install.py; else None. `check(clone=None, ...)` uses
    `clone or _source_clone(HERE) or HERE`.
  - `copy_line(here=HERE)`: "" unless `here/source.json` exists; sha256 each file it lists in
    the clone; any mismatch or missing file -> `'Context Guard: the running copy is older than
    your clone - run: python "<clone>/install.py"'`. Never raises (any error -> "").
  - In `_apply()`, both "Context Guard is already up to date" returns become
    `return _after_update(clone, home, say)` after the message, so the copy is refreshed.
- [ ] Code, audit.py `update_line()`: join `mod.check()` and `mod.copy_line()` (when present)
  with a space; an old update.py without `copy_line` still works.
- [ ] README "Install" and "Updating": the hooks run from `~/.claude/context-guard/bin/`;
  `python install.py` (re-run) or `python install.py --update` is how a change goes live; the
  chat-start line says when the clone is ahead of the running copy.
- [ ] Run `python test_guard.py`; then the full gate.

---

## Controller steps (not implementer tasks)

- **C1 - go live on the copy (after Task 7 and a green gate).** `python install.py --dry-run`,
  show the owner the diff (hook paths move to `bin/`, the Read hook goes). Then
  `python install.py`. Prove it: `~/.claude/settings.json` has no `D:/Claude/context-guard/`
  hook path and no `--reread`; open one new chat and see `--bootstrap` / `--size` lines in
  `~/.claude/context-audit.log` written after the install time.
- **C2 - outside-repo mentions.** `~/.claude/skills/open-chat/SKILL.md` lines 92-93 name
  `--skills`, `--attribute` and `ledger-budget.py` as safe commands: replace the sentence with
  "`--report`, `--checkpoint` and `--recall` are safe." (also drop `--bootstrap` there: the
  README calls it NOT safe by hand - it writes memory lists and stubs). `~/.claude/CLAUDE.md`
  names none of the deleted commands (grep found only `--recall`, which stays); its
  `D:/Claude/context-guard/guard.py --recall` path still works (both copies read the same
  state), so leave it. Measure each file's line endings before and after.
- **C3 - leftovers in his state folder.** `~/.claude/context-guard/no-memory-sweep` is dead
  after Task 4; leave it (harmless) unless he says otherwise. The 63 spare memory copies in
  project folders stay where they are.

## Controller decisions

- **D1 (Task 3):** delete only the `--skills` command (default), or also the skill-candidate
  block in the handoff warning (`skill_candidates`, `skill_proposals`, `_bash_shape`,
  `SKILL_*`, `LOOK_AROUND`, `SCRIPT_EXT`, `CMD_NAME`, about 150 more lines, plus up to 3 s of
  scanning per warning turn). It is not dead: 147 injections in 98 chats up to 30 Sep.
- **D2 (Task 4):** the owner's yes to cancelling the sweep (see the top of this plan).

## Done when

- All seven commits are in (six if D2 is no), the full gate is rc=0, and a reviewer has
  checked the batch.
- `guard.py` bare LF is still 0; audit.py, update.py, install.py, test_guard.py, README.md
  still 0 CRLF.
- C1 and C2 are done and proved.
