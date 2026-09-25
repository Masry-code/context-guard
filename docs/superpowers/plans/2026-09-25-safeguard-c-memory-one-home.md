# Safeguard (c): One Home for Every Memory File - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every memory topic file lives only in the shared folder, a project folder keeps just its `MEMORY.md` list, and a SessionStart sweep moves strays home without deleting anything.

**Architecture:** All code changes are in `guard.py`'s bootstrap section: the label fetch names the shared folder, and `cmd_bootstrap()` becomes one printer around the sweep and `_bootstrap_list()`, which writes only a list. The sweep (`sweep_memory_strays()`) runs first on every SessionStart under an `O_EXCL` lock, moves by copy-check-remove, backs a list up before rewriting it, and keeps each file's own line endings. A one-time five-step rollout runs from scripts outside the repo, and its dry run uses a copy of the real folders.

**Tech Stack:** Python 3.14 standard library only, git 2.54 in Git Bash on Windows, and the suite's own runner (`test_guard.py`, with an explicit tuple of tests).

**Spec:** `docs/superpowers/specs/2026-09-25-safeguard-c-memory-one-home-design.md`. It is approved. Do not edit it.

---

## Before you start (read once)

### Paths

| name | where |
|---|---|
| repo | `D:/Claude/context-guard` (public on GitHub) |
| WORK | `D:/AI Projects/Claude Needed Tools/safeguard-c`. This holds the helper and rollout scripts, baselines, clones and dry runs. It is outside the repo. |
| shared folder | `~/.claude/projects/D--Claude/memory` (`SOURCE_KEY`) |
| state folder | `~/.claude/context-guard` (manifest, `memcheck.py`, lock, off switch) |
| part-1 backup | `~/.claude/memory-backups/2026-09-25-before-merge/<key>/` |

### Rules on this machine (not optional)

- **Writing files.** Use the Write tool to write a file and the Edit tool to change one.
  - Never put file content through a heredoc or `echo >`.
  - Never use `sed -i`. It silently rewrites a CRLF file to LF.
  - A multi-line replacement goes through `splice.py` (Task 1), which uses `io.open(newline="")` and refuses unless each anchor occurs exactly once.
- **Check line endings after every edit.** After every edit to `guard.py`, `test_guard.py` or `README.md`, run `eol.py` and read the CRLF and bare-LF counts. `guard.py` must stay CRLF; `test_guard.py`, `README.md` and `install.py` must stay LF.
  - If a count is wrong, run `eol.py --fix` and then run `eol.py` again.
  - `eol.py` also parses every `.py` file. **The live hooks run the working-tree `guard.py`**, so a syntax error there breaks every open chat. Fix it before doing anything else.
- **Python output.** Prefix every Python command with `PYTHONIOENCODING=utf-8`. Add `PYTHONDONTWRITEBYTECODE=1` whenever a command imports from the repo.
- **Working directory.** Never `cd`. Use `git -C` and absolute paths. Shell variables do not survive between Bash calls, so each command block sets `W=...` itself.
- **Git.** Never use `git add -A`, `commit -a` or `stash`. Stage named files only. Pushing is the user's call: every push below is "ask the user".
- **Another session owns uncommitted lines** in `guard.py` (+53) and `test_guard.py` (+89): the per-chat pause feature. Do not edit, stage, stash or revert them.
  - If an Edit fails because a file changed under you, and the change is not yours, stop and tell the user.
- **Timeouts.** Run the full suite with the Bash timeout at 600000 ms. If it ever takes longer, run it with `run_in_background`.
- **Milestones.** Save milestones to memory as they land: commit A, commit B, and the finished rollout.
  - Save the file in the shared folder.
  - Put its index line in the Context Guard project list, `~/.claude/projects/D--Claude-context-guard/memory/MEMORY.md`. That is the rule Task 2 teaches every chat.

### The live-hook hazard and the off switch

Claude Code runs `guard.py` from this working tree on every SessionStart, so the sweep would act on the real memory folders as soon as its code lands in Task 6. Task 1 creates the empty off switch `~/.claude/context-guard/no-memory-sweep` before any code changes. `cmd_bootstrap()` checks that switch before it calls the sweep.

A direct call to `sweep_memory_strays()` bypasses the switch. That is how the controlled real run in Task 15 works. The switch is removed only when Task 16's proof passes.

### How every guard.py task runs (TDD)

1. Write the failing tests into `test_guard.py` and add them to the runner tuple. A test that is not in the tuple never runs. Then run `eol.py`.
2. **Red.** Run `one.py` on exactly the named tests. It must fail with exactly the FAIL lines listed, and for the reason given.
3. Make the guard.py change, running `eol.py` after each edit.
4. **Green.** Run `one.py` on the same tests. Expect `FAILED 0 check(s), 0 test(s) raised`.
5. **Full suite.** Run `suite.py`. Expect `rc=0`, the PASS count given and `FAIL=0`.

The PASS counts are relative to the numbers recorded in Task 1:
- **N0** is the working tree's PASS count (measured at 686 on 25 Sep).
- **C0** is the PASS count of a fresh clone of HEAD (667).

The tests assert structure: files, bytes, slugs, paths, line counts and JSON shape. They never assert prose. Each one builds a throwaway home under `tempfile`. They never import guard in the suite's own process; `guard_call()` imports it in a child process pointed at the temp home.

### Decisions this plan makes where the spec is silent

1. **An off switch** (`no-memory-sweep`), because the live hooks run the working tree. It follows the README's rule: "Every automatic behaviour has an override".
2. **Claimants are primary folders only.** A list claims a slug only when the list is in the primary `dir` folder of a manifest project, because that is the only list a label fetch shows. The manifest claims its `files` for that same folder.
   - An `also` folder, or a folder no project maps to, never claims. A line moved into such a list would disappear from every chat.
   - Measured: today's numbers are unchanged (3 case-3 lines, 0 case-4, 7 with no claimant, 0 with two or more).
3. **A list must name the shared folder before anything leaves its folder.** Lists that Claude Code writes itself have no header. Once their files moved home, their lines would point at nothing.
   - The sweep adds the header to a settled list first. It skips the folder while that list is fresh.
   - This changes nothing today, because rollout step 2 gives every existing list the header.
4. **Every list rewrite keeps a backup first.** Case 3 drops the shared wording, and measured, 1 of today's 3 case-3 lines is worded differently. The dry run prints each difference so the user can decide.
5. **Case 4 with a claimant that has no list.** The line stays in the shared list (0 such lines today).
6. **The `modified:` line.** "Identical" ignores `modified:` only inside a leading frontmatter block that is fenced by `---` lines.
7. **`sweep-seen.json` is a JSON object** keyed `sha:sha`, because `--report` reads every `*.json` in the state folder as an object.
8. **Merge-time sha256.** The spec does not name the 4 known-different spares or give their sha256. Rollout step 1 derives the pins from the part-1 backup, because those 4 spares are unchanged since then (measured). It also pins any spare it re-merges.
9. **A spare can change after part 1 and still match its shared copy.** Measured today, a live chat was writing both copies. Such a spare needs no merge.
10. **"Tests patch PROJECTS".** The suite's existing isolation already does the equivalent: a throwaway `~` in a child process. `guard_call()` adds fault injection the same way.
11. **"Project lists are LF".** One real list is CRLF. That is harmless, because line endings are measured per file.
12. **The bootstrap's missing-file message** says "NOT listed" instead of "NOT copied".

### Stop points (never pass one without the user's answer in chat)

| # | where | condition |
|---|---|---|
| S1 | Task 1 | Always: the gate question, and approval to create the off switch. |
| S2 | Task 1 | The baseline suite is not green. It is not ours to fix. |
| S3 | Tasks 5 and 10 | Something is already staged, or `stage_ours.py` refuses (a merge conflict, or a line outside this build's regions). |
| S4 | Tasks 5 and 10 | Always: every push is the user's call. |
| S5 | Task 12 | A spare needs a 3-way merge (show it, and apply it only on an OK), or a spare differs from its shared copy and has no part-1 copy. |
| S6 | Task 14 | Always: show the dry run. On any difference from the expectation, stop and show the difference. |
| S7 | Task 14 | The sweep takes 1000 ms or more. |
| S8 | Task 15 | A pinned sha256 has changed. Nothing is moved. |
| S9 | Task 16 | The proof fails. The off switch stays on. |

### File map

| file | in repo | change |
|---|---|---|
| `guard.py` (CRLF) | yes | Bootstrap section only, between `# ---...--- the bootstrap` and `def cmd_report():`. |
| `test_guard.py` (LF) | yes | Boot helpers, 6 boot tests adjusted, 1 test renamed, 18 new tests, and the runner tuple. |
| `README.md` (LF) | yes | A one-home paragraph, a hooks row, a commands row and an off-switch bullet. |
| this plan | yes | Committed with A and B. |
| `~/.claude/context-guard/memcheck.py` (LF) | **no** | Task 11. |
| WORK scripts | **no** | Tasks 1 and 12-16. |
| `~/.claude/context-guard/no-memory-sweep` | **no** | Created in Task 1, removed in Task 16. |

---

## Task 1: Gate, off switch, helpers and baselines (no repo edits)

**Files:**
- Create: WORK/`eol.py`, `one.py`, `suite.py`, `splice.py`, `stage_ours.py`
- Create: `~/.claude/context-guard/no-memory-sweep` (only on the user's OK)

- [ ] **Step 1: Ask the gate question (STOP S1).** Send this, then wait for the answers:

> Before I start, two questions.
>
> 1. Another session has uncommitted work in `guard.py` (+53 lines) and `test_guard.py` (+89 lines): the per-chat pause feature. Will its owner commit it before this build commits?
>    - **Yes:** I wait until it is committed, then commit normally.
>    - **No:** I commit only my own lines (HEAD plus my hunks) and leave theirs uncommitted in the working tree.
>
>    Either way, until my commits land, a `git add guard.py` from that session would carry my unfinished lines into its commit.
> 2. The live hooks run the working-tree `guard.py`, so the sweep would act on your real memory folders as soon as its code lands. OK to create the empty off switch `~/.claude/context-guard/no-memory-sweep` now? It stays until the rollout's proof passes.

The answers decide the commit path and what happens next:

- **Yes to question 1:** the commit path is **N** (normal). Wait until the user says the commit has landed, then confirm with this command, which must print nothing:

  ```bash
  git -C D:/Claude/context-guard status --porcelain -- guard.py test_guard.py
  ```

  Do not edit either file before then.
- **No to question 1:** the commit path is **F** (fallback).
- **No to question 2:** STOP and ask how to proceed. The sweep must not be built in a live tree without the switch.

- [ ] **Step 2: Create the off switch and the work folder**

```bash
touch "$HOME/.claude/context-guard/no-memory-sweep" && mkdir -p "D:/AI Projects/Claude Needed Tools/safeguard-c/snippets" && ls -la "$HOME/.claude/context-guard/no-memory-sweep"
```

Expected: an empty file (size 0). In Git Bash on this machine `$HOME` is the Windows profile folder (checked on 25 Sep), so `$HOME/.claude` is the real `~/.claude`.

- [ ] **Step 3: Write `WORK/eol.py`** with the Write tool

```python
"""Line endings and syntax of every file this build edits. Exit 1 on any surprise.

usage: python eol.py [checkout] [--memcheck] [--fix]
  --memcheck  also check ~/.claude/context-guard/memcheck.py
  --fix       rewrite a file whose endings are wrong to the ending it must have"""
import ast
import os
import sys
import warnings

warnings.simplefilter("ignore", SyntaxWarning)
args = [a for a in sys.argv[1:] if not a.startswith("--")]
repo = os.path.abspath(args[0] if args else "D:/Claude/context-guard")
want = {os.path.join(repo, "guard.py"): b"\r\n",
        os.path.join(repo, "test_guard.py"): b"\n",
        os.path.join(repo, "install.py"): b"\n",
        os.path.join(repo, "README.md"): b"\n"}
if "--memcheck" in sys.argv:
    want[os.path.join(os.path.expanduser("~"), ".claude", "context-guard", "memcheck.py")] = b"\n"
bad = 0
for p, eol in want.items():
    with open(p, "rb") as f:
        b = f.read()
    crlf = b.count(b"\r\n")
    lf = b.count(b"\n") - crlf
    ok = lf == 0 if eol == b"\r\n" else crlf == 0
    if not ok and "--fix" in sys.argv:
        b = b.replace(b"\r\n", b"\n").replace(b"\n", eol)
        with open(p, "wb") as f:
            f.write(b)
        crlf = b.count(b"\r\n")
        lf = b.count(b"\n") - crlf
        ok = True
        print("FIXED", end=" ")
    if p.endswith(".py"):
        try:
            ast.parse(b.decode("utf-8"), filename=p)
        except SyntaxError as e:
            ok = False
            print("SYNTAX ERROR in %s: %s" % (os.path.basename(p), e))
    print("%-4s %-14s CRLF=%-5d bare-LF=%-5d must be %s" % (
        "ok" if ok else "BAD", os.path.basename(p), crlf, lf, "CRLF" if eol == b"\r\n" else "LF"))
    bad += not ok
sys.exit(1 if bad else 0)
```

- [ ] **Step 4: Write `WORK/one.py`**

```python
"""Run chosen tests from a checkout's test_guard.py, in this process, and count.

usage: python one.py <checkout> <test_name> [<test_name> ...]
A red test may raise; it is reported as RAISED and the others still run."""
import os
import sys
import warnings

sys.dont_write_bytecode = True
warnings.simplefilter("ignore", SyntaxWarning)
sys.path.insert(0, os.path.abspath(sys.argv[1]))
import test_guard as tg

raised = []
for name in sys.argv[2:]:
    print(name)
    try:
        getattr(tg, name)()
    except Exception as e:
        raised.append(name)
        print("  RAISED  %s: %s" % (type(e).__name__, e))
print()
print("FAILED %d check(s), %d test(s) raised" % (len(tg.FAILED), len(raised)))
for n in tg.FAILED:
    print("  - " + n)
sys.exit(1 if tg.FAILED or raised else 0)
```

- [ ] **Step 5: Write `WORK/suite.py`**

```python
"""Run a checkout's whole test_guard.py and summarise it.

usage: python suite.py [checkout]  -> rc, PASS and FAIL counts, seconds, every FAIL line"""
import os
import subprocess
import sys
import time

repo = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "D:/Claude/context-guard")
env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
t0 = time.time()
p = subprocess.run([sys.executable, "-W", "ignore::SyntaxWarning", "test_guard.py"], cwd=repo,
                   capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
out = p.stdout.splitlines()
fails = [l for l in out if l.startswith("  FAIL  ")]
print("%s  rc=%d  PASS=%d  FAIL=%d  %.0f s" % (
    repo, p.returncode, sum(l.startswith("  PASS  ") for l in out), len(fails), time.time() - t0))
for l in fails:
    print(l)
if p.returncode and not fails:
    print("no FAIL line, so a test RAISED - stderr tail:")
    print("\n".join(p.stderr.splitlines()[-15:]))
print(out[-1] if out else "(no output)")
sys.exit(p.returncode)
```

- [ ] **Step 6: Write `WORK/splice.py`**

```python
"""Replace FILE's text from START up to (not including) END with the text of NEW, in
FILE's own line ending. Refuses, writing nothing, unless each anchor occurs exactly once.
NEW's trailing blank lines are normalised to two, so END keeps two blank lines above it.

usage: python splice.py FILE START END NEW"""
import io
import sys

path, start, end, new = sys.argv[1:5]
with io.open(path, "r", encoding="utf-8", newline="") as f:
    text = f.read()
crlf = text.count("\r\n")
eol = "\r\n" if crlf and crlf >= text.count("\n") - crlf else "\n"
for a in (start, end):
    if text.count(a) != 1:
        sys.exit("anchor %r occurs %d times - nothing written" % (a, text.count(a)))
i, j = text.index(start), text.index(end)
if j <= i:
    sys.exit("END comes before START - nothing written")
with io.open(new, "r", encoding="utf-8", newline="") as f:
    body = f.read().replace("\r\n", "\n").rstrip() + "\n\n\n"
text = text[:i] + body.replace("\n", eol) + text[j:]
with io.open(path, "w", encoding="utf-8", newline="") as f:
    f.write(text)
crlf = text.count("\r\n")
print("%s: CRLF=%d bare-LF=%d" % (path, crlf, text.count("\n") - crlf))
```

- [ ] **Step 7: Write `WORK/stage_ours.py`** (commit path F stages with it; path N uses `--check`)

```python
"""Stage HEAD plus ONLY this build's hunks. The other session's lines stay unstaged.

usage: python stage_ours.py <baseline-dir> [--check]
For guard.py, test_guard.py and README.md:
    result = git merge-file -p <HEAD copy> <baseline copy> <working tree>
that is, HEAD plus (working tree minus baseline). Refuses, staging nothing, when anything
is already staged, on a merge conflict, or when a line the result changes against HEAD
lies outside this build's regions. --check stops there. Otherwise each result is written
with git hash-object -w --no-filters and staged with git update-index --cacheinfo."""
import difflib
import os
import subprocess
import sys
import tempfile

REPO = "D:/Claude/context-guard"
FILES = ("guard.py", "test_guard.py", "README.md")
BASE = os.path.abspath(sys.argv[1])
CHECK = "--check" in sys.argv[2:]
BOOT = b"# ---------------------------------------------------------------- the bootstrap"
OURS = {
    "test_the_label_fetch_names_the_shared_folder_and_the_project_list",
    "test_bootstrap_writes_only_the_list_with_the_header",
    "test_bootstrap_lists_rather_than_copies",
    "test_the_header_goes_into_an_existing_list_once_and_keeps_its_endings",
    "test_the_sweep_moves_a_memory_with_no_shared_copy_home",
    "test_the_sweep_leaves_a_file_changed_in_the_last_ten_minutes",
    "test_the_sweep_skips_while_another_sweep_holds_the_lock",
    "test_the_sweep_has_an_off_switch",
    "test_a_sweep_with_nothing_to_do_prints_nothing",
    "test_the_sweep_gives_a_list_the_header_before_moving_out_of_it",
    "test_the_sweep_backs_up_a_spare_identical_to_its_shared_copy",
    "test_a_failure_between_copy_and_remove_loses_nothing",
    "test_two_different_copies_are_left_alone_and_reported_once",
    "test_bootstrap_and_the_sweep_print_one_json_object",
    "test_the_sweep_drops_a_shared_line_its_one_claimant_already_lists",
    "test_the_sweep_moves_a_shared_line_to_the_one_claimant_that_lacks_it",
    "test_the_sweep_leaves_a_slug_with_zero_or_two_claimants",
    "test_a_crlf_list_stays_crlf",
    "test_the_sweep_leaves_a_list_changed_in_the_last_ten_minutes",
}
ALLOWED = OURS | {"test_bootstrap_copies_rather_than_moves",
                  "test_the_note_template_names_the_log_it_is_written_from"}


def git(*args, data=None):
    return subprocess.run(["git", "-C", REPO, *args], input=data, capture_output=True,
                          check=True).stdout


def at(lines, prefix):
    return next(i for i, l in enumerate(lines) if l.startswith(prefix))


def outside(name, head, res):
    """(side, line number, text) for every changed line outside this build's regions."""
    if name == "README.md":
        return []
    sides = {"HEAD": head.split(b"\n"), "result": res.split(b"\n")}
    h, r = sides["HEAD"], sides["result"]
    changed = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, h, r, autojunk=False).get_opcodes():
        if op != "equal":
            changed += [("HEAD", i, h[i]) for i in range(i1, i2)]
            changed += [("result", j, r[j]) for j in range(j1, j2)]
    bounds = {}
    for side, lines in sides.items():
        if name == "guard.py":
            bounds[side] = (at(lines, BOOT), at(lines, b"def cmd_report():"))
        else:
            bounds[side] = (at(lines, b"BOOT_MANIFEST = {"),
                            at(lines, b'if __name__ == "__main__":'))
    bad = []
    for side, i, text in changed:
        lo, hi = bounds[side]
        if name == "guard.py":
            ok = lo < i < hi
        elif i <= hi:
            ok = i > lo
        else:              # inside the runner tuple: only our tests may appear or change
            ok = text.strip().rstrip(b",):").decode("utf-8", "replace") in ALLOWED
        if not ok:
            bad.append((side, i + 1, text))
    return bad


if git("diff", "--cached", "--name-only").strip():
    sys.exit("something is already staged - nothing done. Ask the user whose it is.")
results = {}
for name in FILES:
    head = git("show", "HEAD:" + name)
    hp = os.path.join(tempfile.mkdtemp(prefix="stage-ours-"), name)
    with open(hp, "wb") as f:
        f.write(head)
    m = subprocess.run(["git", "merge-file", "-p", hp, os.path.join(BASE, name),
                        os.path.join(REPO, name)], capture_output=True)
    if m.returncode != 0:
        sys.exit("%s: git merge-file exit %d (conflicts or an error) - nothing staged"
                 % (name, m.returncode))
    bad = outside(name, head, m.stdout)
    for side, n, text in bad[:20]:
        print("  OUTSIDE THIS BUILD'S REGIONS  %s %s line %d: %r" % (name, side, n, text[:90]))
    if bad:
        sys.exit("%s: %d line(s) outside this build's regions - nothing staged" % (name, len(bad)))
    results[name] = m.stdout
    print("%-14s %s" % (name, "same as HEAD" if m.stdout == head else "HEAD + our hunks only"))
if CHECK:
    sys.exit(0)
for name, blob in results.items():
    sha = git("hash-object", "-w", "--no-filters", "--stdin", data=blob).decode().strip()
    git("update-index", "--cacheinfo", "100644,%s,%s" % (sha, name))
    print("staged %s as %s" % (name, sha[:12]))
```

- [ ] **Step 8: Snapshot baseline A.** This is the working tree before any of our edits. On path N, take it after their commit has landed.

```bash
W="D:/AI Projects/Claude Needed Tools/safeguard-c"; mkdir -p "$W/baseline-A" && cp D:/Claude/context-guard/guard.py D:/Claude/context-guard/test_guard.py D:/Claude/context-guard/README.md "$W/baseline-A/" && git -C D:/Claude/context-guard diff --numstat && git -C D:/Claude/context-guard log --oneline -1
```

Expected output:
- Path F: `53 0 guard.py` and `89 0 test_guard.py` (their lines).
- Path N: nothing, and HEAD is their commit.

Note what you see. Tasks 5 and 10 compare against it.

- [ ] **Step 9: Baseline measurements (2 full-suite runs)**

```bash
W="D:/AI Projects/Claude Needed Tools/safeguard-c"; PYTHONIOENCODING=utf-8 python "$W/eol.py" && PYTHONIOENCODING=utf-8 PYTHONDONTWRITEBYTECODE=1 python "$W/suite.py"
```

Expected:
- `eol.py`: four `ok` lines, with `guard.py bare-LF=0` and the other files `CRLF=0`.
- `suite.py`: `rc=0 PASS=N0 FAIL=0`. N0 was 686 on 25 Sep. Record the actual number and the seconds it took.
- If FAIL > 0: STOP (S2). It is not ours to fix.

```bash
W="D:/AI Projects/Claude Needed Tools/safeguard-c"; C="$W/clone-0-$(date +%H%M%S)"; git clone -q D:/Claude/context-guard "$C" && PYTHONIOENCODING=utf-8 PYTHONDONTWRITEBYTECODE=1 python "$W/suite.py" "$C"
```

Expected: `rc=0 PASS=C0 FAIL=0`. C0 was 667 at HEAD `72332da`. Record it.

---

## Task 2: The label fetch names the shared folder and the project list (spec 2.1)

**Files:**
- Modify: `guard.py`, at `SOURCE_KEY = "D--Claude"` (about line 2346) and in `label_memory_index()` (about lines 2545-2554)
- Test: `test_guard.py`. Add the test above `if __name__ == "__main__":` and in the runner tuple.

- [ ] **Step 1: Write the failing test.** Use Edit: old_string `if __name__ == "__main__":`. The new_string is the block below, then two blank lines, then `if __name__ == "__main__":`.

```python
def test_the_label_fetch_names_the_shared_folder_and_the_project_list():
    """ONE HOME, 25 Sep 2026. The fetch used to say the files were "in the same folder" as
    the list, so chats edited the project copies while new memories went to the shared
    folder, and the two drifted. It must now name BOTH places: the list it read, and the
    shared folder where every file that list points at lives."""
    home = make_home({KEY + ".9e19c7ab.md": NOTE_CTX, KEY + ".b0b0b0b0.md": NOTE_EGX})
    try:
        write_threads_manifest(home)
        lst = os.path.join(write_project_index(home, "D--AI-Projects-EgxScannerzBot",
                                               "# Memory Index\n- [a](b.md) - INDEX-EGX\n"),
                           "MEMORY.md")
        shared = os.path.join(home, ".claude", "projects", KEY, "memory")
        # CONTROL: a label no project claims fetches nothing, so it names neither place.
        ctl = context_of(run(home, "ee55-label-ctl", "context guard"))
        check("label-home: control - an unmapped label fetches no index",
              "INDEX-EGX" not in ctl, repr(ctl[-300:]))
        check("label-home: control - and names no shared folder", shared not in ctl,
              repr(ctl[-300:]))
        p = run(home, "ee55-label-home", "EGX bot")
        ctx = context_of(p)
        expect_clean(p, "label-home")
        check("label-home: the index arrived", "INDEX-EGX" in ctx, repr(ctx[-400:]))
        check("label-home: it names the project list it read", lst in ctx, repr(ctx[-600:]))
        check("label-home: it names the shared folder the files live in", shared in ctx,
              repr(ctx[-600:]))
    finally:
        shutil.rmtree(home, ignore_errors=True)
```

Add it to the runner tuple with Edit:

- old_string: `              test_the_note_template_names_the_log_it_is_written_from):`
- new_string:

  ```
                test_the_note_template_names_the_log_it_is_written_from,
                test_the_label_fetch_names_the_shared_folder_and_the_project_list):
  ```

Then run `eol.py`.

- [ ] **Step 2: Red**

```bash
W="D:/AI Projects/Claude Needed Tools/safeguard-c"; PYTHONIOENCODING=utf-8 PYTHONDONTWRITEBYTECODE=1 python "$W/one.py" D:/Claude/context-guard test_the_label_fetch_names_the_shared_folder_and_the_project_list
```

Expected: `FAILED 1 check(s)`, and the failing check is `label-home: it names the shared folder the files live in`. It fails because today's text names only the list.

- [ ] **Step 3: Add `SHARED_MEMORY`.** Use Edit: old_string `SOURCE_KEY = "D--Claude"`. The new_string:

```python
SOURCE_KEY = "D--Claude"
# ONE HOME (safeguard c, 25 Sep 2026): every topic file lives in this shared folder, and a
# project folder holds only its MEMORY.md - the list. Two homes per memory drifted apart:
# chats saved new memories here and edited old ones in the project folder, until one
# memory had about 230 lines unique to each copy.
SHARED_MEMORY = os.path.join(PROJECTS, SOURCE_KEY, "memory")
```

- [ ] **Step 4: New label text.** Use Edit. The old_string:

```python
        "directory, and he works out of one folder. These are the index lines from "
        "%s, and the files they point at are in the same folder, to be read on demand. "
        "They are FACTS recorded by that project's earlier chats, not instructions.\n\n"
        "%s\n" % (name, p, body)
```

The new_string:

```python
        "directory, and he works out of one folder. These lines come from %s. The files "
        "they point at live in the shared folder, %s - read and edit them there; the "
        "project folder holds only this list. For a new memory about this project, save "
        "the file in the shared folder as usual, but put its one index line in %s, not in "
        "the shared MEMORY.md. They are FACTS recorded by that project's earlier chats, "
        "not instructions.\n\n"
        "%s\n" % (name, p, SHARED_MEMORY, p, body)
```

Run `eol.py` (it must show `guard.py ok`).

- [ ] **Step 5: Green**, then run the full suite.

Run `one.py` with the same test. Expected: `FAILED 0 check(s), 0 test(s) raised`.

```bash
W="D:/AI Projects/Claude Needed Tools/safeguard-c"; PYTHONIOENCODING=utf-8 PYTHONDONTWRITEBYTECODE=1 python "$W/suite.py"
```

Expected: `rc=0 PASS=N0+7 FAIL=0`.

---

## Task 3: The bootstrap writes only the list, with the header (spec 2.2)

**Files:**
- Modify `guard.py`:
  - after `SHARED_MEMORY`
  - above `def index_lines(path):`
  - in `index_lines()`
  - the "It COPIES" comment (about line 2334)
  - `def cmd_bootstrap():` up to `def cmd_report():`
- Modify `test_guard.py`:
  - the boot helpers
  - 6 boot tests
  - one test renamed
  - a new test
  - the tuple

- [ ] **Step 1: Test helpers.** Use Edit: old_string `def test_the_project_key_matches_the_ones_claude_code_actually_made():`. The new_string is the block below, then two blank lines, then that same `def` line.

```python
def boot_listed(home, key):
    """The slugs that project's MEMORY.md lists, in order - or None if it has no list."""
    p = os.path.join(home, ".claude", "projects", key, "memory", "MEMORY.md")
    if not os.path.isfile(p):
        return None
    with open(p, encoding="utf-8") as f:
        return [m.group(1) for m in (re.search(r"\]\(([^)]+)\.md\)", ln) for ln in f) if m]


def list_bytes(home, key):
    """That project's MEMORY.md, byte for byte - or None."""
    try:
        with open(os.path.join(home, ".claude", "projects", key, "memory", "MEMORY.md"),
                  "rb") as f:
            return f.read()
    except OSError:
        return None
```

- [ ] **Step 2: The new failing test.** Use Edit: old_string `# ------------------------------------------------- the ordering, answered by the hook`. The new_string is the block below, then two blank lines, then that same comment line.

```python
def test_bootstrap_writes_only_the_list_with_the_header():
    """ONE HOME, 25 Sep 2026: a new project folder gets its MEMORY.md and nothing else.
    Copying the topic files in gave every project memory two homes, and they drifted.
    The list carries every manifest slug the shared folder has, and one header line above
    the entries names the shared folder as the place the files live."""
    home = make_boot_home()
    try:
        p = boot(home, r"D:\AI Projects\StreamBERT APK")
        expect_clean(p, "bootstrap/list-only")
        key = "D--AI-Projects-StreamBERT-APK"
        shared = os.path.join(home, ".claude", "projects", KEY, "memory")
        check("bootstrap/list-only: the folder holds only its list",
              boot_memory(home, key) == ["MEMORY.md"], str(boot_memory(home, key)))
        check("bootstrap/list-only: it lists every manifest slug, in manifest order",
              boot_listed(home, key) == ["streambert-pc-port", "youtubedl-android-traps"],
              str(boot_listed(home, key)))
        lines = (list_bytes(home, key) or b"").decode("utf-8").splitlines()
        heads = [i for i, ln in enumerate(lines) if shared in ln]
        first = next((i for i, ln in enumerate(lines) if re.search(r"\]\([^)]+\.md\)", ln)), -1)
        check("bootstrap/list-only: exactly one line names the shared folder",
              len(heads) == 1, repr(lines[:4]))
        check("bootstrap/list-only: and it sits above the first entry",
              len(heads) == 1 and 0 <= heads[0] < first, repr(lines[:4]))
        check("bootstrap/list-only: the shared folder still has every file",
              all(os.path.isfile(os.path.join(shared, n + ".md")) for n in BOOT_FILES),
              str(sorted(os.listdir(shared))))
    finally:
        shutil.rmtree(home, ignore_errors=True)
```

- [ ] **Step 3: Adjust the six boot tests that assumed copies.** Make each of these Edits.

**M1** (`test_bootstrap_furnishes_a_fresh_directory`). The old_string:

```python
        check("bootstrap/fresh: the memory file came with it",
              got is not None and "food-expiry-scanner-app.md" in got, str(got))
```

The new_string:

```python
        check("bootstrap/fresh: its memory is listed, and its file stays shared",
              "food-expiry-scanner-app" in (boot_listed(home, "D--AI-Projects-Taza") or [])
              and got is not None and "food-expiry-scanner-app.md" not in got, str(got))
```

**M2** (only-own).
- old_string: `        got = boot_memory(home, "D--AI-Projects-Taza") or []`
- new_string: `        got = [s + ".md" for s in (boot_listed(home, "D--AI-Projects-Taza") or [])]`

**M4** (no-clobber control). The old_string:

```python
        check("bootstrap/no-clobber: control - a fresh folder IS still furnished",
              "streambert-pc-port.md" in (boot_memory(home, "D--AI-Projects-StreamBERT-APK") or []),
              str(boot_memory(home, "D--AI-Projects-StreamBERT-APK")))
```

The new_string:

```python
        check("bootstrap/no-clobber: control - a fresh folder IS still furnished",
              "streambert-pc-port" in (boot_listed(home, "D--AI-Projects-StreamBERT-APK") or []),
              str(boot_listed(home, "D--AI-Projects-StreamBERT-APK")))
```

**M5** (idempotent) takes three Edits.

M5, first Edit: old_string `        first = boot_memory(home, "D--AI-Projects-Taza")`. The new_string:

```python
        first = boot_memory(home, "D--AI-Projects-Taza")
        first_list = list_bytes(home, "D--AI-Projects-Taza")
```

M5, second Edit: old_string `              first is not None and "food-expiry-scanner-app.md" in first, str(first))`. The new_string:

```python
              "food-expiry-scanner-app" in (boot_listed(home, "D--AI-Projects-Taza") or []),
              str(first))
```

M5, third Edit: old_string `              boot_memory(home, "D--AI-Projects-Taza") == first, str(first))`. The new_string:

```python
              boot_memory(home, "D--AI-Projects-Taza") == first
              and list_bytes(home, "D--AI-Projects-Taza") == first_list, str(first))
```

**M6** (unknown-directory control). The old_string:

```python
        check("bootstrap/unknown: control - a known folder IS furnished",
              "food-expiry-scanner-app.md" in (boot_memory(home, "D--AI-Projects-Taza") or []),
              str(boot_memory(home, "D--AI-Projects-Taza")))
```

The new_string:

```python
        check("bootstrap/unknown: control - a known folder IS furnished",
              "food-expiry-scanner-app" in (boot_listed(home, "D--AI-Projects-Taza") or []),
              str(boot_listed(home, "D--AI-Projects-Taza")))
```

**M7** (also-directory).
- old_string: `        got = boot_memory(home, "D--AI-Projects-StreamBERT-PC") or []`
- new_string: `        got = [s + ".md" for s in (boot_listed(home, "D--AI-Projects-StreamBERT-PC") or [])]`

- [ ] **Step 4: Rename and rewrite the copy test (M3).** Write `WORK/snippets/lists_rather_than_copies.py` with the Write tool:

```python
def test_bootstrap_lists_rather_than_copies():
    """ONE HOME: the new folder gets a list and never a copy, and the shared folder -
    where every chat he has open reads and saves - is not touched at all."""
    home = make_boot_home()
    try:
        boot(home, r"D:\AI Projects\Taza")
        src = os.path.join(home, ".claude", "projects", KEY, "memory")
        left = sorted(os.listdir(src))
        # POSITIVE CONTROL - "the source still has it" is also true when nothing ran.
        check("bootstrap/list: the destination lists it",
              "food-expiry-scanner-app" in (boot_listed(home, "D--AI-Projects-Taza") or []),
              str(boot_listed(home, "D--AI-Projects-Taza")))
        check("bootstrap/list: and holds no copy of it",
              boot_memory(home, "D--AI-Projects-Taza") == ["MEMORY.md"],
              str(boot_memory(home, "D--AI-Projects-Taza")))
        check("bootstrap/list: the shared folder still has every file",
              all(n + ".md" in left for n in BOOT_FILES), str(left))
        check("bootstrap/list: the shared index is untouched",
              open(os.path.join(src, "MEMORY.md"), encoding="utf-8").read() == BOOT_INDEX)
    finally:
        shutil.rmtree(home, ignore_errors=True)
```

```bash
W="D:/AI Projects/Claude Needed Tools/safeguard-c"; PYTHONIOENCODING=utf-8 python "$W/splice.py" D:/Claude/context-guard/test_guard.py "def test_bootstrap_copies_rather_than_moves():" "def test_bootstrap_never_overwrites_an_existing_memory_folder():" "$W/snippets/lists_rather_than_copies.py"
```

Expected: `test_guard.py: CRLF=0 bare-LF=<n>`.

Now update the runner tuple with two Edits:

1. The rename:
   - old_string: `              test_bootstrap_copies_rather_than_moves,`
   - new_string: `              test_bootstrap_lists_rather_than_copies,`
2. The new test:
   - old_string: `              test_the_label_fetch_names_the_shared_folder_and_the_project_list):`
   - new_string:

     ```
                   test_the_label_fetch_names_the_shared_folder_and_the_project_list,
                   test_bootstrap_writes_only_the_list_with_the_header):
     ```

Run `eol.py`.

- [ ] **Step 5: Red**

```bash
W="D:/AI Projects/Claude Needed Tools/safeguard-c"; PYTHONIOENCODING=utf-8 PYTHONDONTWRITEBYTECODE=1 python "$W/one.py" D:/Claude/context-guard test_bootstrap_writes_only_the_list_with_the_header test_bootstrap_furnishes_a_fresh_directory test_bootstrap_lists_rather_than_copies test_bootstrap_gives_the_new_folder_only_its_own_memories test_bootstrap_never_overwrites_an_existing_memory_folder test_bootstrap_is_idempotent test_bootstrap_ignores_a_directory_the_manifest_does_not_know test_bootstrap_matches_an_also_directory
```

Expected: `FAILED 5 check(s), 0 test(s) raised`. They all fail because the topic files are still copied and the list has no header:
- `bootstrap/list-only: the folder holds only its list`
- `bootstrap/list-only: exactly one line names the shared folder`
- `bootstrap/list-only: and it sits above the first entry`
- `bootstrap/fresh: its memory is listed, and its file stays shared`
- `bootstrap/list: and holds no copy of it`

- [ ] **Step 6: The pattern and header constants.** Use Edit: old_string `SHARED_MEMORY = os.path.join(PROJECTS, SOURCE_KEY, "memory")`. The new_string:

```python
SHARED_MEMORY = os.path.join(PROJECTS, SOURCE_KEY, "memory")
# An entry line in any MEMORY.md: "- [title](slug.md) - ...". One pattern for every reader,
# so the header below can be shown never to be read as an entry.
LINK_RE = re.compile(r"\]\(([^)]+)\.md\)")
# The one line above a project list's entries that says where its files live. Written by
# the bootstrap into every new list and by rollout step 2 into every existing one, and
# recognised by this prefix alone, so it is never added twice.
LIST_HEADER_LEAD = "> The files for these entries live in the shared memory folder"
```

- [ ] **Step 7: `list_header()` and `index_lines()`.** Use Edit: old_string `def index_lines(path):`. The new_string:

```python
def list_header():
    """The one-home header line. It names the shared folder, and it must never match
    LINK_RE, or index_lines() would read it as an entry."""
    return ("%s, %s - read and edit them there. This folder holds only this list."
            % (LIST_HEADER_LEAD, SHARED_MEMORY))


def index_lines(path):
```

Then one more Edit:
- old_string: `                m = re.search(r"\]\(([^)]+)\.md\)", line)`
- new_string: `                m = LINK_RE.search(line)`

- [ ] **Step 8: The section comment.** Use Edit. The old_string:

```python
# It COPIES, never moves. Every chat he currently has open runs in the shared folder, and
# moving would blind all of them at once to shrink a number. The shrink is a separate step
# he takes when he is satisfied - and copying is the version that can be undone.
```

The new_string:

```python
# Since 25 Sep 2026 it writes only the LIST. It used to copy each topic file into the new
# folder, which gave every project memory two homes, and the copies drifted apart (see
# SHARED_MEMORY). The files stay in the shared folder, and the list's header says so.
```

- [ ] **Step 9: Split `cmd_bootstrap()` into one printer and `_bootstrap_list()`.** Write `WORK/snippets/cmd_bootstrap.py` with the Write tool:

```python
def cmd_bootstrap():
    """SessionStart. Prints at most ONE json object - a hook that prints two reads as
    silence - so every part of it returns its text here instead of printing it."""
    d = read_stdin()
    # First, before any of the give-up paths below: a probe left by an earlier session may
    # finally be answerable. Resolution must not be hostage to this session's own cwd.
    resolve_ordering_probe()
    text = _bootstrap_list(d)
    if text:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                                 "additionalContext": text}}))


def _bootstrap_list(d):
    """Give this directory its own memory LIST if it has none yet. Returns the message for
    SessionStart, or "" on every path that has nothing to say."""
    cwd = d.get("cwd") or ""
    key = dir_key(cwd)
    if not key:
        # Measured 19 Sep 2026: the first end-to-end run of this hook did NOTHING and said
        # nothing, and it took a debugging session to find out why - the JSON on stdin had
        # been mangled by the shell that sent it, so cwd was empty. Every give-up path below
        # is silent BY DESIGN (SessionStart must not chatter), which means the log is the
        # only place a misconfiguration can ever show up. Say which branch declined, always.
        log("bootstrap: no cwd on stdin - nothing to do")
        return ""
    dest = os.path.join(PROJECTS, key, "memory")
    # A folder that already holds memories is HIS. Never overwrite one, never merge into
    # one, and say nothing - SessionStart fires on every resume as well as every startup.
    if os.path.exists(os.path.join(dest, "MEMORY.md")):
        log("bootstrap: %s already has a memory folder - left alone" % key)
        return ""
    try:
        with open(MANIFEST, encoding="utf-8") as f:
            man = json.load(f)
    except Exception as e:
        log("bootstrap: no usable manifest (%s) - declining to guess" % e)
        return ""              # no manifest, or unreadable: nothing here is worth guessing
    name, entry = manifest_project(cwd, man)
    if not entry:
        log("bootstrap: %s is not in the manifest - declining to guess" % cwd)
        return ""              # a directory nobody has classified. Silence beats a guess.
    src = SHARED_MEMORY
    # The pointer file FIRST, so the live index wins on any slug listed in both. A line
    # that has moved is still the line he wrote, and this hook must never invent a
    # replacement - see index_lines().
    idx = index_lines(PROJECTS_INDEX)
    idx.update(index_lines(os.path.join(src, "MEMORY.md")))
    listed, missing = [], []
    for slug in (entry.get("files") or []):
        if os.path.isfile(os.path.join(src, slug + ".md")):
            listed.append(slug)
        else:
            missing.append(slug)          # a name in the manifest that has since rotted
    body = ["# Memory Index", list_header()]
    for slug in listed:
        body.append(idx.get(slug) or ("- [%s](%s.md)" % (slug, slug)))
    try:
        os.makedirs(dest, exist_ok=True)
        with open(os.path.join(dest, "MEMORY.md"), "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(body) + "\n")
    except Exception as e:
        log("bootstrap: could not write %s's list (%s)" % (name, e))
        return ""
    log("bootstrap: %s -> %s, %d listed, %d missing" % (name, key, len(listed), len(missing)))
    # Only when something was actually listed: an index with no entries gives the loader
    # nothing to inject, so its absence downstream would prove nothing about the ordering.
    if listed:
        arm_ordering_probe(d.get("session_id"), key, d.get("transcript_path"))
    out = ["Context Guard just created this project's own memory list.",
           "",
           "  project : %s" % name,
           "  list    : %s" % os.path.join(dest, "MEMORY.md"),
           "  listed  : %d memories (their files stay in the shared folder, %s)"
           % (len(listed), src),
           ""]
    if missing:
        # Never let a bootstrap quietly drop a memory. A short set that looks complete is
        # worse than a short set that says what is missing.
        out += ["NOT listed - the manifest names these and they are not on disk:",
                "  " + ", ".join(missing),
                ""]
    # BELT AND BRACES. It is not known whether Claude Code's memory loader runs before or
    # after SessionStart. If it runs first, a folder created here is only read by the NEXT
    # chat - and that failure looks exactly like success, because chat #2 works fine.
    # Handing the index back as additionalContext makes the ordering stop mattering.
    out += ["This project's memories, in context now regardless of when the folder is read:",
            ""] + body
    return "\n".join(out)
```

```bash
W="D:/AI Projects/Claude Needed Tools/safeguard-c"; PYTHONIOENCODING=utf-8 python "$W/splice.py" D:/Claude/context-guard/guard.py "def cmd_bootstrap():" "def cmd_report():" "$W/snippets/cmd_bootstrap.py" && PYTHONIOENCODING=utf-8 python "$W/eol.py"
```

Expected: `guard.py: CRLF=<n> bare-LF=0`, then all `ok`.

- [ ] **Step 10: Green**, then run the full suite.

Re-run the Step 5 `one.py` command. Expected: `FAILED 0 check(s), 0 test(s) raised`.

Run `suite.py`. Expected: `rc=0 PASS=N0+15 FAIL=0`. That is the 7 new checks, plus one more check in the renamed test.

---

## Task 4: The header for an existing list (spec 2.3, the function rollout step 2 uses)

**Files:**
- Modify: `guard.py`. Add a block above `def cmd_bootstrap():`.
- Test: `test_guard.py`. Add the sweep section helpers and one test above `if __name__`, and one tuple entry.

- [ ] **Step 1: Shared test helpers for the header and sweep tests, and the failing test.** Use Edit: old_string `if __name__ == "__main__":`. The new_string is the block below, then two blank lines, then `if __name__ == "__main__":`.

```python
# ---------------------------------------------- one home: the list header and the sweep
# His rule, 25 Sep 2026: "make sure moving forward all chats saves the memories in the
# correct folder or at least move them when possible at the start of chat with a hook".
# Every topic file lives in the shared folder and a project folder holds only its list;
# the sweep tidies strays home at the start of every chat and never deletes anything.

SWEEP_OLD = 3600          # an hour old: well past the sweep's ten-minute settle time
ALPHA, BETA = "D--Work-Alpha", "D--Work-Beta"
SWEEP_MANIFEST = {
    "projects": {
        "alpha": {"dir": "D:\\Work\\Alpha", "also": [], "threads": ["alpha"],
                  "files": ["alpha-notes"]},
        "beta": {"dir": "D:\\Work\\Beta", "also": [], "threads": ["beta"],
                 "files": ["beta-notes"]},
    },
}


def make_sweep_home(manifest=None):
    """A fake ~ with an empty shared memory folder, the state folder and a manifest."""
    home = tempfile.mkdtemp(prefix="guardsweep-")
    for parts in (("projects", KEY, "memory"), ("context-guard",), ("handoff",)):
        os.makedirs(os.path.join(home, ".claude", *parts))
    with open(os.path.join(home, ".claude", "context-guard", "memory-manifest.json"),
              "w", encoding="utf-8") as f:
        json.dump(SWEEP_MANIFEST if manifest is None else manifest, f)
    return home


def memory_folder(home, key):
    return os.path.join(home, ".claude", "projects", key, "memory")


def memory_text(slug, body, modified="2026-09-01"):
    """A topic file the way auto-memory writes one, frontmatter and all."""
    return ("---\nname: %s\ndescription: about %s\nmodified: %s\n---\n\n%s\n"
            % (slug, slug, modified, body))


def index_entry(slug):
    return "- [%s](%s.md) - about %s" % (slug, slug, slug)


def plant(home, key, name, text, age=SWEEP_OLD, eol="\n"):
    """Write one file into a memory folder, `age` seconds old. Returns its path."""
    p = os.path.join(memory_folder(home, key), name)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(text.replace("\n", eol).encode("utf-8"))
    t = time.time() - age
    os.utime(p, (t, t))
    return p


def raw_bytes(p):
    try:
        with open(p, "rb") as f:
            return f.read()
    except OSError:
        return None


def sweep_backups(home, key, name):
    """Every backup of `name` (or a -N variant of it) under that key, on any day."""
    root = os.path.join(home, ".claude", "memory-backups")
    stem, ext = os.path.splitext(name)
    pat = re.compile(re.escape(stem) + r"(-\d+)?" + re.escape(ext) + "$")
    found = []
    for day in (sorted(os.listdir(root)) if os.path.isdir(root) else []):
        d = os.path.join(root, day, key)
        found += [os.path.join(d, f) for f in (sorted(os.listdir(d)) if os.path.isdir(d) else [])
                  if pat.match(f)]
    return found


def guard_call(home, code):
    """Run `code` after `import guard` in a CHILD process pointed at the throwaway home -
    never in this one, which must not be able to reach the real ~ (see guard_constant).
    For the functions guard.py has no command for, and for injecting a fault."""
    env = child_env(home)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    pre = "import sys; sys.path.insert(0, %r); import guard\n" % os.path.dirname(GUARD)
    return subprocess.run([sys.executable, "-c", pre + code], capture_output=True,
                          text=True, encoding="utf-8", env=env)


def header_of(home):
    """The header line exactly as guard.py writes it for this home."""
    return guard_call(home, "print(guard.list_header())").stdout.strip()


def plant_list(home, key, lines, head, eol="\n", age=SWEEP_OLD):
    """A project list: its title, then `head` (the header line, "" for none), then lines."""
    body = ["# Memory Index"] + ([head] if head else []) + list(lines)
    return plant(home, key, "MEMORY.md", "\n".join(body) + "\n", age=age, eol=eol)


def run_sweep(home, sid="sweep001"):
    """SessionStart from the shared folder's own directory. The bootstrap does nothing
    there - that folder already has its list, or SWEEP_MANIFEST does not name D:/Claude -
    so whatever the hook does or says here is the sweep's."""
    return boot(home, CWD, sid)


def test_the_header_goes_into_an_existing_list_once_and_keeps_its_endings():
    """Rollout step 2 gives every existing project list the header: once, above the first
    entry, in the file's own line endings and final-newline state, backing the old list up
    first - and never in a form index_lines() would read as an entry."""
    home = make_sweep_home()
    try:
        shared = memory_folder(home, KEY).encode("utf-8")
        a = plant(home, ALPHA, "MEMORY.md",
                  "# Memory Index\n%s\n" % index_entry("alpha-notes"), eol="\r\n")
        b = plant(home, BETA, "MEMORY.md", "# Memory Index\n%s" % index_entry("beta-notes"))
        old_a, old_b = raw_bytes(a), raw_bytes(b)
        call = "print([guard.ensure_list_header(p) for p in %r])" % ([a, b],)
        p1 = guard_call(home, call)
        new_a, new_b = raw_bytes(a), raw_bytes(b)
        p2 = guard_call(home, call)
        idx = guard_call(home, "print(sorted(guard.index_lines(%r)) + sorted(guard.index_lines(%r)))"
                         % (a, b))
        check("list-header: both calls ran cleanly", p1.returncode == 0 and p2.returncode == 0,
              (p1.stderr + p2.stderr)[-300:])
        check("list-header: the first call gave both lists the header",
              new_a != old_a and new_b != old_b, repr(new_a))
        check("list-header: a second call changes nothing",
              (raw_bytes(a), raw_bytes(b)) == (new_a, new_b), repr(raw_bytes(a)))
        check("list-header: the header is never read as an entry",
              idx.stdout.strip() == "['alpha-notes', 'beta-notes']",
              (idx.stdout + idx.stderr)[-300:])
        for tag, blob, crlf, final in (("crlf", new_a, True, True), ("lf", new_b, False, False)):
            lines = blob.splitlines()
            heads = [i for i, ln in enumerate(lines) if shared in ln]
            first = next((i for i, ln in enumerate(lines) if b".md)" in ln), -1)
            check("list-header: %s list - one header line, above its entry" % tag,
                  len(heads) == 1 and heads[0] < first, repr(blob))
            check("list-header: %s list - its own line endings" % tag,
                  (blob.count(b"\n") == blob.count(b"\r\n")) if crlf else (b"\r" not in blob),
                  repr(blob))
            check("list-header: %s list - its final newline as it was" % tag,
                  blob.endswith(b"\n") == final, repr(blob[-40:]))
        check("list-header: the old list was backed up first",
              [raw_bytes(x) for x in sweep_backups(home, ALPHA, "MEMORY.md")] == [old_a],
              str(sweep_backups(home, ALPHA, "MEMORY.md")))
    finally:
        shutil.rmtree(home, ignore_errors=True)
```

Add it to the tuple with Edit:
- old_string: `              test_bootstrap_writes_only_the_list_with_the_header):`
- new_string:

  ```
                test_bootstrap_writes_only_the_list_with_the_header,
                test_the_header_goes_into_an_existing_list_once_and_keeps_its_endings):
  ```

Run `eol.py`.

- [ ] **Step 2: Red**

```bash
W="D:/AI Projects/Claude Needed Tools/safeguard-c"; PYTHONIOENCODING=utf-8 PYTHONDONTWRITEBYTECODE=1 python "$W/one.py" D:/Claude/context-guard test_the_header_goes_into_an_existing_list_once_and_keeps_its_endings
```

Expected: `FAILED 5 check(s)`. The reason is that the child reports `AttributeError: module 'guard' has no attribute 'ensure_list_header'`, which shows in the first check's detail. The five failing checks:
- `both calls ran cleanly`
- `the first call gave both lists the header`
- `crlf list - one header line, above its entry`
- `lf list - one header line, above its entry`
- `the old list was backed up first`

- [ ] **Step 3: Safe writes and the header.** Use Edit: old_string `def cmd_bootstrap():`. The new_string is the block below, then two blank lines, then `def cmd_bootstrap():`.

```python
# ------------------------------------------- one home: the list header, and safe writes
# Every write the sweep and rollout step 2 make goes through these: nothing overwrites a
# file, a list is backed up before it is replaced, and every file keeps its own line
# endings - the shared MEMORY.md is CRLF, and the lists differ file by file.
LINK_RE_B = re.compile(LINK_RE.pattern.encode("ascii"))
SWEEP_BACKUPS = os.path.join(HOME, ".claude", "memory-backups")


def _read_bytes(p):
    with open(p, "rb") as f:
        return f.read()


def _eol(blob):
    """A file's own line ending, measured: CRLF when most of its lines end that way."""
    crlf = blob.count(b"\r\n")
    return b"\r\n" if crlf and crlf >= blob.count(b"\n") - crlf else b"\n"


def _free_path(p):
    """p if nothing is there yet, else p with -2, -3 ... before its extension."""
    if not os.path.exists(p):
        return p
    stem, ext = os.path.splitext(p)
    n = 2
    while os.path.exists("%s-%d%s" % (stem, n, ext)):
        n += 1
    return "%s-%d%s" % (stem, n, ext)


def _put_new(dest, blob, mtime=None):
    """Write a file that must not exist yet, then read it back. Raises on a mismatch."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "xb") as f:
        f.write(blob)
    if mtime is not None:
        os.utime(dest, (mtime, mtime))
    if _read_bytes(dest) != blob:
        raise OSError("the copy at %s does not match its source" % dest)


def _backup_dir(key):
    """~/.claude/memory-backups/<YYYY-MM-DD>-sweep/<project key>"""
    return os.path.join(SWEEP_BACKUPS, datetime.date.today().isoformat() + "-sweep", key)


def _write_list(path, before, after, key):
    """Replace a MEMORY.md that still holds `before` with `after`: backup first, then a
    temp file and os.replace. False, with nothing written, if it changed since it was
    read - another chat is writing it, and the next chat retries."""
    if _read_bytes(path) != before:
        log("sweep: %s/MEMORY.md changed while it was being read - left alone" % key)
        return False
    _put_new(_free_path(os.path.join(_backup_dir(key), "MEMORY.md")), before)
    tmp = path + ".sweep-tmp"
    with open(tmp, "wb") as f:
        f.write(after)
    os.replace(tmp, path)
    return True


def with_list_header(blob):
    """The list with the one-home header above its first entry - or unchanged, when a line
    already starts with LIST_HEADER_LEAD. A list with no entries gets it under its "#"
    title, else at the top. Bytes in, bytes out: the file keeps its own line endings and
    its final newline."""
    lead = LIST_HEADER_LEAD.encode("utf-8")
    lines = blob.splitlines(keepends=True)
    if any(ln.startswith(lead) for ln in lines):
        return blob
    eol = _eol(blob)
    at = next((i for i, ln in enumerate(lines) if LINK_RE_B.search(ln)), None)
    if at is None:
        at = 1 if lines and lines[0].startswith(b"#") else 0
    if at and not lines[at - 1].endswith((b"\n", b"\r")):
        lines[at - 1] += eol
    lines.insert(at, list_header().encode("utf-8") + eol)
    out = b"".join(lines)
    if blob and not blob.endswith((b"\n", b"\r")) and out.endswith(eol):
        out = out[:-len(eol)]            # it had no final newline, and still has none
    return out


def ensure_list_header(path):
    """Give one list the header. True when the list carries it afterwards - already, or
    now - and False when the list changed under us and was left alone."""
    before = _read_bytes(path)
    after = with_list_header(before)
    if after == before:
        return True
    return _write_list(path, before, after,
                       os.path.basename(os.path.dirname(os.path.dirname(path))))
```

Run `eol.py`.

- [ ] **Step 4: Green**, then run the full suite.

Re-run the Step 2 `one.py` command. Expected: `FAILED 0`.

Run `suite.py`. Expected: `rc=0 PASS=N0+26 FAIL=0`.

---

## Task 5: Commit A (part 2) and prove it on a fresh clone

**Files:**
- Commit: `guard.py`, `test_guard.py` and this plan.

- [ ] **Step 1: Stage**

**Path N:**

```bash
W="D:/AI Projects/Claude Needed Tools/safeguard-c"; PYTHONIOENCODING=utf-8 python "$W/stage_ours.py" "$W/baseline-A" --check && git -C D:/Claude/context-guard add guard.py test_guard.py docs/superpowers/plans/2026-09-25-safeguard-c-memory-one-home.md
```

**Path F:**

```bash
W="D:/AI Projects/Claude Needed Tools/safeguard-c"; PYTHONIOENCODING=utf-8 python "$W/stage_ours.py" "$W/baseline-A" && git -C D:/Claude/context-guard add docs/superpowers/plans/2026-09-25-safeguard-c-memory-one-home.md
```

Expected:
- `guard.py` and `test_guard.py` show `HEAD + our hunks only`.
- `README.md` shows `same as HEAD`.
- Path F also prints two `staged ... as <sha>` lines.

If it refuses, STOP (S3). Show the user the lines it names.

- [ ] **Step 2: Verify the index**

```bash
git -C D:/Claude/context-guard diff --cached --name-only && git -C D:/Claude/context-guard diff --cached --stat
```

Expected: exactly `docs/superpowers/plans/2026-09-25-safeguard-c-memory-one-home.md`, `guard.py` and `test_guard.py`.

- [ ] **Step 3: Commit**

```bash
git -C D:/Claude/context-guard commit -m "Send chats to the shared folder, and list memories instead of copying them" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" && git -C D:/Claude/context-guard diff --numstat
```

Expected `--numstat`:
- Path F: the same `53 0 guard.py` / `89 0 test_guard.py` as Task 1 step 8. Their lines are still uncommitted, and none of ours are.
- Path N: nothing.

- [ ] **Step 4: Snapshot baseline B** (the working tree right after commit A, for commit B's staging)

```bash
W="D:/AI Projects/Claude Needed Tools/safeguard-c"; mkdir -p "$W/baseline-B" && cp D:/Claude/context-guard/guard.py D:/Claude/context-guard/test_guard.py D:/Claude/context-guard/README.md "$W/baseline-B/"
```

- [ ] **Step 5: Prove it on a fresh clone** (full-suite run)

```bash
W="D:/AI Projects/Claude Needed Tools/safeguard-c"; C="$W/clone-A-$(date +%H%M%S)"; git clone -q D:/Claude/context-guard "$C" && PYTHONIOENCODING=utf-8 PYTHONDONTWRITEBYTECODE=1 python "$W/suite.py" "$C" && PYTHONIOENCODING=utf-8 python "$W/eol.py" "$C"
```

Expected: `rc=0 PASS=C0+26 FAIL=0`, and four `ok` lines.

- [ ] **Step 6: Ask about the push (S4).** Ask the user:

> Commit A is local as `<sha>` and passes on a fresh clone (PASS `<n>`, FAIL 0). Push it to origin/main?

Carry on with Task 6. Run `git -C D:/Claude/context-guard push origin main` only on an explicit yes.

---

## Task 6: The sweep, with case 1, the settle time, the lock, the off switch, silence, and the header rule

**Files:**
- Modify: `guard.py`. Add a block above `def cmd_bootstrap():`, and edit `cmd_bootstrap()`.
- Test: `test_guard.py`. Add 6 tests above `if __name__`, and the tuple.

- [ ] **Step 1: Check that the off switch is still there.** This must print a file: `ls "$HOME/.claude/context-guard/no-memory-sweep"`. If it is missing, STOP. The code in this task goes live as soon as it is saved.

- [ ] **Step 2: Write the failing tests.** Use Edit: old_string `if __name__ == "__main__":`. The new_string is the block below, then two blank lines, then `if __name__ == "__main__":`.

```python
def test_the_sweep_moves_a_memory_with_no_shared_copy_home():
    """Case 1. A memory saved into a project folder - its only copy - goes home to the
    shared folder byte for byte, mtime and all. Its list line stays where it is."""
    home = make_sweep_home()
    try:
        lst = plant_list(home, ALPHA, [index_entry("alpha-notes")], header_of(home))
        src = plant(home, ALPHA, "alpha-notes.md", memory_text("alpha-notes", "body-ALPHA"))
        blob, mtime, lst_before = raw_bytes(src), os.path.getmtime(src), raw_bytes(lst)
        p = run_sweep(home)
        expect_clean(p, "sweep/home")
        dest = os.path.join(memory_folder(home, KEY), "alpha-notes.md")
        check("sweep/home: the project folder no longer has it", not os.path.exists(src), src)
        check("sweep/home: the shared folder has it, byte for byte", raw_bytes(dest) == blob,
              repr(raw_bytes(dest))[:200])
        check("sweep/home: it kept its mtime",
              os.path.exists(dest) and abs(os.path.getmtime(dest) - mtime) < 2, dest)
        check("sweep/home: its list is untouched", raw_bytes(lst) == lst_before,
              repr(raw_bytes(lst))[:200])
        check("sweep/home: and it said nothing", p.stdout == "", repr(p.stdout[:200]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_sweep_leaves_a_file_changed_in_the_last_ten_minutes():
    """Another chat may be writing it: skipped without a word, and the next chat retries."""
    home = make_sweep_home()
    try:
        src = plant(home, ALPHA, "alpha-notes.md", memory_text("alpha-notes", "body-ALPHA"),
                    age=60)
        p = run_sweep(home)
        expect_clean(p, "sweep/fresh")
        check("sweep/fresh: a file changed a minute ago stays put", os.path.exists(src), src)
        check("sweep/fresh: and it said nothing", p.stdout == "", repr(p.stdout[:200]))
        # CONTROL: the same file, settled, does move - or the check above proves nothing.
        t = time.time() - SWEEP_OLD
        os.utime(src, (t, t))
        run_sweep(home, sid="sweep002")
        check("sweep/fresh: control - once settled it moves home",
              not os.path.exists(src)
              and os.path.isfile(os.path.join(memory_folder(home, KEY), "alpha-notes.md")), src)
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_sweep_skips_while_another_sweep_holds_the_lock():
    """Two chats starting at once must not race. A held lock means skip, silently; a lock
    older than a minute belongs to a sweep that died, and is taken over."""
    home = make_sweep_home()
    try:
        src = plant(home, ALPHA, "alpha-notes.md", memory_text("alpha-notes", "body-ALPHA"))
        lock = os.path.join(home, ".claude", "context-guard", "sweep.lock")
        with open(lock, "w") as f:
            f.write("12345")
        p = run_sweep(home)
        expect_clean(p, "sweep/lock")
        check("sweep/lock: another sweep holds it - nothing moved", os.path.exists(src), src)
        check("sweep/lock: and it said nothing", p.stdout == "", repr(p.stdout[:200]))
        check("sweep/lock: the other sweep's lock was left alone", os.path.exists(lock), lock)
        t = time.time() - 120
        os.utime(lock, (t, t))
        run_sweep(home, sid="sweep002")
        check("sweep/lock: a stale lock is taken over and the sweep runs",
              not os.path.exists(src), src)
        check("sweep/lock: and the lock is gone afterwards", not os.path.exists(lock), lock)
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_sweep_has_an_off_switch():
    """Every automatic behaviour has an override: an empty file called no-memory-sweep."""
    home = make_sweep_home()
    try:
        src = plant(home, ALPHA, "alpha-notes.md", memory_text("alpha-notes", "body-ALPHA"))
        off = os.path.join(home, ".claude", "context-guard", "no-memory-sweep")
        open(off, "w").close()
        p = run_sweep(home)
        expect_clean(p, "sweep/off")
        check("sweep/off: switched off - nothing moved", os.path.exists(src), src)
        os.remove(off)
        run_sweep(home, sid="sweep002")
        check("sweep/off: control - switched back on, it moves", not os.path.exists(src), src)
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_a_sweep_with_nothing_to_do_prints_nothing():
    """Nothing to say means nothing printed at all - not an empty object, not a newline."""
    home = make_sweep_home()
    try:
        plant_list(home, ALPHA, [index_entry("alpha-notes")], header_of(home))
        p = run_sweep(home)
        expect_clean(p, "sweep/quiet")
        check("sweep/quiet: nothing to do - stdout is empty", p.stdout == "",
              repr(p.stdout[:200]))
        src = plant(home, ALPHA, "alpha-notes.md", memory_text("alpha-notes", "body-ALPHA"))
        p2 = run_sweep(home, sid="sweep002")
        check("sweep/quiet: control - this time it did work", not os.path.exists(src), src)
        check("sweep/quiet: and still printed nothing", p2.stdout == "", repr(p2.stdout[:200]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_sweep_gives_a_list_the_header_before_moving_out_of_it():
    """A list Claude Code wrote itself, in a folder the rollout never saw, has no header,
    and its lines would point at nothing once the file moves home. So the sweep gives it
    the header first - and leaves the whole folder alone while that list is fresh."""
    home = make_sweep_home()
    try:
        lst = plant_list(home, ALPHA, [index_entry("alpha-notes")], "", age=60)
        src = plant(home, ALPHA, "alpha-notes.md", memory_text("alpha-notes", "body-ALPHA"))
        run_sweep(home)
        check("sweep/header: a fresh list without the header - nothing moved",
              os.path.exists(src), src)
        t = time.time() - SWEEP_OLD
        os.utime(lst, (t, t))
        p = run_sweep(home, sid="sweep002")
        expect_clean(p, "sweep/header")
        body = (raw_bytes(lst) or b"").decode("utf-8")
        check("sweep/header: once settled, the file moved home", not os.path.exists(src), src)
        check("sweep/header: and its list now names the shared folder",
              memory_folder(home, KEY) in body, repr(body[:300]))
        check("sweep/header: and still lists the memory", "(alpha-notes.md)" in body,
              repr(body[:300]))
    finally:
        shutil.rmtree(home, ignore_errors=True)
```

Add the tests to the tuple with Edit:
- old_string: `              test_the_header_goes_into_an_existing_list_once_and_keeps_its_endings):`
- new_string:

  ```
                test_the_header_goes_into_an_existing_list_once_and_keeps_its_endings,
                test_the_sweep_moves_a_memory_with_no_shared_copy_home,
                test_the_sweep_leaves_a_file_changed_in_the_last_ten_minutes,
                test_the_sweep_skips_while_another_sweep_holds_the_lock,
                test_the_sweep_has_an_off_switch,
                test_a_sweep_with_nothing_to_do_prints_nothing,
                test_the_sweep_gives_a_list_the_header_before_moving_out_of_it):
  ```

Run `eol.py`.

- [ ] **Step 3: Red**

```bash
W="D:/AI Projects/Claude Needed Tools/safeguard-c"; PYTHONIOENCODING=utf-8 PYTHONDONTWRITEBYTECODE=1 python "$W/one.py" D:/Claude/context-guard test_the_sweep_moves_a_memory_with_no_shared_copy_home test_the_sweep_leaves_a_file_changed_in_the_last_ten_minutes test_the_sweep_skips_while_another_sweep_holds_the_lock test_the_sweep_has_an_off_switch test_a_sweep_with_nothing_to_do_prints_nothing test_the_sweep_gives_a_list_the_header_before_moving_out_of_it
```

Expected: `FAILED 10 check(s), 0 test(s) raised`. They fail because no sweep exists yet, so nothing ever moves:
- `sweep/home`: `no longer has it`, `the shared folder has it, byte for byte`, `it kept its mtime`
- `sweep/fresh: control - once settled it moves home`
- `sweep/lock`: `a stale lock is taken over and the sweep runs`, `and the lock is gone afterwards`
- `sweep/off: control - switched back on, it moves`
- `sweep/quiet: control - this time it did work`
- `sweep/header`: `once settled, the file moved home`, `and its list now names the shared folder`

- [ ] **Step 4: The sweep.** Use Edit: old_string `def cmd_bootstrap():`. The new_string is the block below, then two blank lines, then `def cmd_bootstrap():`.

```python
# ------------------------------------------------------------------------ the sweep
# His rule, 25 Sep 2026: "make sure moving forward all chats saves the memories in the
# correct folder or at least move them when possible at the start of chat with a hook".
# Part 2 is the first half. This is the second: at the start of EVERY chat, a topic file
# found in a project folder goes home - or, when the shared folder already has an
# identical copy, to the backup folder. Nothing is ever deleted, anything touched in the
# last ten minutes is left for the next chat, and it says nothing unless there is
# something he has to decide.
SWEEP_OFF = "no-memory-sweep"
SWEEP_LOCK = os.path.join(STATE, "sweep.lock")
SWEEP_SETTLE = 600       # seconds: younger than this, another chat may still be writing it
SWEEP_LOCK_STALE = 60    # seconds: a lock older than this belongs to a sweep that died


def _fresh(p, now):
    """Changed in the last SWEEP_SETTLE seconds - or unreadable, which counts the same."""
    try:
        return now - os.path.getmtime(p) < SWEEP_SETTLE
    except OSError:
        return True


def _move(src, dest):
    """Copy, check, and only then remove the source. A crash in between leaves two
    identical copies, which the next sweep tidies as case 2. The mtime travels with the
    file, so a moved memory never reads as one a chat has just written."""
    _put_new(dest, _read_bytes(src), os.path.getmtime(src))
    os.remove(src)


def _take_sweep_lock(now):
    """O_EXCL, so two chats starting at once cannot both sweep. False while another sweep
    holds it; a lock older than SWEEP_LOCK_STALE is taken over."""
    os.makedirs(STATE, exist_ok=True)
    for _attempt in (1, 2):
        try:
            fd = os.open(SWEEP_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                if now - os.path.getmtime(SWEEP_LOCK) <= SWEEP_LOCK_STALE:
                    return False
                os.remove(SWEEP_LOCK)
                log("sweep: took over a stale lock")
            except FileNotFoundError:
                pass                     # its owner finished in between: try once more
            continue
        with os.fdopen(fd, "w") as f:
            f.write(str(os.getpid()))
        return True
    return False


def _list_says_where(path, now):
    """True once this list names the shared folder - giving it the header now if the list
    has settled. Nothing may leave a folder whose list does not say where it went."""
    blob = _read_bytes(path)
    if with_list_header(blob) == blob:
        return True
    return not _fresh(path, now) and ensure_list_header(path)


def sweep_memory_strays():
    """Part 3 of safeguard (c). Returns the text for SessionStart - "" unless there is
    something he has to decide. Never raises: an error is logged, stops the sweep where it
    is, and the next chat retries."""
    now = time.time()
    t0 = time.perf_counter()
    try:
        if not _take_sweep_lock(now):
            log("sweep: another chat's sweep holds the lock - skipped")
            return ""
    except Exception as e:
        log("sweep: could not take the lock (%s) - skipped" % e)
        return ""
    try:
        return _sweep(now)
    except Exception as e:
        log("sweep: stopped by %s: %s - the rest left as it was, the next chat retries"
            % (type(e).__name__, e))
        return ""
    finally:
        try:
            os.remove(SWEEP_LOCK)
        except Exception:
            pass
        log("sweep: done in %d ms" % ((time.perf_counter() - t0) * 1000))


def _sweep(now):
    """sweep_memory_strays() with the lock held. Returns the text for SessionStart."""
    if not os.path.isdir(SHARED_MEMORY):
        log("sweep: no shared memory folder at %s - nothing to do" % SHARED_MEMORY)
        return ""
    folders = [os.path.join(PROJECTS, k, "memory") for k in sorted(os.listdir(PROJECTS))
               if k != SOURCE_KEY and os.path.isdir(os.path.join(PROJECTS, k, "memory"))]
    for d in folders:
        key = os.path.basename(os.path.dirname(d))
        # Topic files only - never the list, never anything that is not a .md file - and
        # none changed in the last SWEEP_SETTLE seconds: another chat may be writing it.
        names = [n for n in sorted(os.listdir(d))
                 if n != "MEMORY.md" and n.endswith(".md")
                 and os.path.isfile(os.path.join(d, n)) and not _fresh(os.path.join(d, n), now)]
        lst = os.path.join(d, "MEMORY.md")
        if names and os.path.isfile(lst) and not _list_says_where(lst, now):
            log("sweep: %s/MEMORY.md does not name the shared folder yet and has just "
                "changed - that folder is left for the next chat" % key)
            continue
        for name in names:
            src, shared = os.path.join(d, name), os.path.join(SHARED_MEMORY, name)
            if not os.path.exists(shared):
                # case 1: its only copy. Its line stays in the list, which now points here.
                _move(src, shared)
                log("sweep: moved %s/%s home to the shared folder" % (key, name))
                continue
    return ""
```

Run `eol.py`.

- [ ] **Step 5: Wire the sweep in, before any give-up path.** Use Edit. The old_string:

```python
    resolve_ordering_probe()
    text = _bootstrap_list(d)
```

The new_string:

```python
    resolve_ordering_probe()
    # The sweep, before any give-up path, so it runs at the start of EVERY chat - labelled
    # or not, startup, resume, clear or compact - and it needs nothing from stdin.
    if os.path.exists(os.path.join(STATE, SWEEP_OFF)):
        log("sweep: switched off by %s - skipped" % os.path.join(STATE, SWEEP_OFF))
        swept = ""
    else:
        swept = sweep_memory_strays()
    text = "\n\n".join(t for t in (swept, _bootstrap_list(d)) if t)
```

Run `eol.py`.

- [ ] **Step 6: Green**, then run the full suite.

Re-run the Step 3 `one.py` command. Expected: `FAILED 0`.

Run `suite.py`. Expected: `rc=0 PASS=N0+60 FAIL=0`.

---

## Task 7: Case 2 (identical spares go to the backup) and a crash between copy and remove

**Files:**
- Modify: `guard.py`. Add a block above `def sweep_memory_strays():`, and edit `_sweep()`.
- Test: `test_guard.py`. Add 2 tests and the tuple.

- [ ] **Step 1: Write the failing tests.** Insert this block above `if __name__ == "__main__":`, the same way as before.

```python
def test_the_sweep_backs_up_a_spare_identical_to_its_shared_copy():
    """Case 2. Identical but for the frontmatter `modified:` line: the spare goes to the
    backup folder, never the bin, and a second spare of that name gets its own -2 file."""
    home = make_sweep_home()
    try:
        shared = plant(home, KEY, "alpha-notes.md",
                       memory_text("alpha-notes", "body-ALPHA", "2026-09-20"))
        keep = raw_bytes(shared)
        spare = plant(home, ALPHA, "alpha-notes.md",
                      memory_text("alpha-notes", "body-ALPHA", "2026-09-01"))
        blob = raw_bytes(spare)
        p = run_sweep(home)
        expect_clean(p, "sweep/spare")
        got = sweep_backups(home, ALPHA, "alpha-notes.md")
        check("sweep/spare: it left the project folder", not os.path.exists(spare), spare)
        check("sweep/spare: it is in the backup folder, byte for byte",
              [raw_bytes(g) for g in got] == [blob], str(got))
        check("sweep/spare: the shared copy is untouched", raw_bytes(shared) == keep)
        check("sweep/spare: and it said nothing", p.stdout == "", repr(p.stdout[:200]))
        plant(home, ALPHA, "alpha-notes.md",
              memory_text("alpha-notes", "body-ALPHA", "2026-09-02"))
        run_sweep(home, sid="sweep002")
        got = sweep_backups(home, ALPHA, "alpha-notes.md")
        check("sweep/spare: a second spare of that name gets a backup of its own",
              len(got) == 2 and any(os.path.basename(g) == "alpha-notes-2.md" for g in got),
              str(got))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_a_failure_between_copy_and_remove_loses_nothing():
    """The move copies, checks, then removes. Break it between the copy and the remove:
    both copies must be whole, the error logged, the lock released - and the next sweep
    must heal it, as case 2."""
    home = make_sweep_home()
    try:
        src = plant(home, ALPHA, "alpha-notes.md", memory_text("alpha-notes", "body-ALPHA"))
        blob = raw_bytes(src)
        dest = os.path.join(memory_folder(home, KEY), "alpha-notes.md")
        p = guard_call(home, "import os\n"
                             "real = os.remove\n"
                             "def boom(path, *a, **k):\n"
                             "    if str(path).endswith('.md'):\n"
                             "        raise OSError('injected between copy and remove')\n"
                             "    return real(path, *a, **k)\n"
                             "os.remove = boom\n"
                             "print(repr(guard.sweep_memory_strays()))\n")
        check("sweep/crash: the sweep swallowed the error",
              p.returncode == 0 and p.stdout.strip() == "''", (p.stdout + p.stderr)[-300:])
        check("sweep/crash: the source is still there, whole", raw_bytes(src) == blob, src)
        check("sweep/crash: the copy is there, whole", raw_bytes(dest) == blob, dest)
        check("sweep/crash: the error is in the log",
              "injected between copy and remove" in guard_log(home), guard_log(home)[-300:])
        check("sweep/crash: the lock was released", not os.path.exists(
            os.path.join(home, ".claude", "context-guard", "sweep.lock")))
        p2 = run_sweep(home, sid="sweep002")
        expect_clean(p2, "sweep/crash")
        check("sweep/crash: the next sweep healed it - the spare is in the backup",
              not os.path.exists(src)
              and [raw_bytes(g) for g in sweep_backups(home, ALPHA, "alpha-notes.md")] == [blob],
              src)
        check("sweep/crash: and the shared copy is still whole", raw_bytes(dest) == blob, dest)
    finally:
        shutil.rmtree(home, ignore_errors=True)
```

Add the tests to the tuple with Edit:
- old_string: `              test_the_sweep_gives_a_list_the_header_before_moving_out_of_it):`
- new_string:

  ```
                test_the_sweep_gives_a_list_the_header_before_moving_out_of_it,
                test_the_sweep_backs_up_a_spare_identical_to_its_shared_copy,
                test_a_failure_between_copy_and_remove_loses_nothing):
  ```

Run `eol.py`.

- [ ] **Step 2: Red**

```bash
W="D:/AI Projects/Claude Needed Tools/safeguard-c"; PYTHONIOENCODING=utf-8 PYTHONDONTWRITEBYTECODE=1 python "$W/one.py" D:/Claude/context-guard test_the_sweep_backs_up_a_spare_identical_to_its_shared_copy test_a_failure_between_copy_and_remove_loses_nothing
```

Expected: `FAILED 4 check(s)`. All four fail because there is no case 2 yet:
- `sweep/spare`: `it left the project folder`, `it is in the backup folder, byte for byte`, `a second spare of that name gets a backup of its own`
- `sweep/crash: the next sweep healed it - the spare is in the backup`

The crash test's first five checks already pass, because Task 6's move is copy-check-remove.

- [ ] **Step 3: `_sans_modified()`.** Use Edit: old_string `def sweep_memory_strays():`. The new_string is the block below, then two blank lines, then `def sweep_memory_strays():`.

```python
def _sans_modified(blob):
    """The file without its frontmatter `modified:` line - the one line two otherwise
    identical copies may disagree on. Only inside a leading block fenced by "---" lines;
    anything else is compared whole."""
    lines = blob.splitlines(keepends=True)
    if not lines or lines[0].rstrip(b"\r\n") != b"---":
        return blob
    for i in range(1, len(lines)):
        if lines[i].rstrip(b"\r\n") == b"---":
            return b"".join(lines[:1] + [ln for ln in lines[1:i]
                                         if not re.match(rb"\s*modified\s*:", ln)]
                            + lines[i:])
    return blob                          # no closing fence: not frontmatter after all
```

- [ ] **Step 4: Case 2 in `_sweep()`.** Use Edit. The old_string:

```python
                log("sweep: moved %s/%s home to the shared folder" % (key, name))
                continue
    return ""
```

The new_string:

```python
                log("sweep: moved %s/%s home to the shared folder" % (key, name))
                continue
            mine, theirs = _read_bytes(src), _read_bytes(shared)
            if _sans_modified(mine) == _sans_modified(theirs):
                # case 2: a spare copy. Nothing is deleted - it goes to the backup folder.
                dest = _free_path(os.path.join(_backup_dir(key), name))
                _move(src, dest)
                log("sweep: %s/%s is identical to its shared copy - moved to %s"
                    % (key, name, dest))
                continue
    return ""
```

Run `eol.py`.

- [ ] **Step 5: Green**, then run the full suite.

Re-run the Step 2 `one.py` command. Expected: `FAILED 0`.

Run `suite.py`. Expected: `rc=0 PASS=N0+76 FAIL=0`.

---

## Task 8: Two different copies are reported once, and the hook prints one JSON object

**Files:**
- Modify: `guard.py`. Add a block above `def sweep_memory_strays():`, and make two edits in `_sweep()`.
- Test: `test_guard.py`. Add 2 tests and the tuple.

- [ ] **Step 1: Write the failing tests.** Insert this block above `if __name__ == "__main__":`.

```python
def test_two_different_copies_are_left_alone_and_reported_once():
    """The one case that speaks. Both copies untouched, one line naming the memory, the
    same pair never reported twice - and a copy that changes again is a new pair."""
    home = make_sweep_home()
    try:
        shared = plant(home, KEY, "alpha-notes.md", memory_text("alpha-notes", "body-SHARED"))
        spare = plant(home, ALPHA, "alpha-notes.md", memory_text("alpha-notes", "body-SPARE"))
        a, b = raw_bytes(shared), raw_bytes(spare)
        p = run_sweep(home)
        expect_clean(p, "sweep/two")
        ctx = context_of(p)
        check("sweep/two: one json object", one_json(p), repr(p.stdout[:200]))
        check("sweep/two: it names the memory", "alpha-notes" in ctx, repr(ctx[:300]))
        check("sweep/two: in one line", len(ctx.strip().splitlines()) == 1, repr(ctx[:300]))
        check("sweep/two: both copies untouched",
              raw_bytes(shared) == a and raw_bytes(spare) == b)
        p2 = run_sweep(home, sid="sweep002")
        check("sweep/two: the same pair is not reported twice", p2.stdout == "",
              repr(p2.stdout[:200]))
        plant(home, ALPHA, "alpha-notes.md", memory_text("alpha-notes", "body-SPARE-EDITED"))
        p3 = run_sweep(home, sid="sweep003")
        check("sweep/two: a copy that changed again is reported again",
              "alpha-notes" in context_of(p3), repr(p3.stdout[:200]))
        check("sweep/two: every occurrence is logged",
              guard_log(home).count("alpha-notes.md") >= 3, guard_log(home)[-400:])
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_bootstrap_and_the_sweep_print_one_json_object():
    """A hook that prints two json objects reads as silence. When the sweep has something
    to say AND the bootstrap furnishes a new folder, both go in ONE object."""
    home = make_sweep_home()
    try:
        plant(home, KEY, "alpha-notes.md", memory_text("alpha-notes", "body-SHARED"))
        plant(home, KEY, "beta-notes.md", memory_text("beta-notes", "body-BETA"))
        plant(home, ALPHA, "alpha-notes.md", memory_text("alpha-notes", "body-SPARE"))
        p = boot(home, r"D:\Work\Beta")
        expect_clean(p, "sweep/one-json")
        ctx = context_of(p)
        check("sweep/one-json: exactly one json object", one_json(p), repr(p.stdout[:300]))
        check("sweep/one-json: it carries the sweep's report", "alpha-notes" in ctx,
              repr(ctx[:400]))
        check("sweep/one-json: and the bootstrap's message",
              BETA in ctx and "beta-notes" in ctx, repr(ctx[:400]))
    finally:
        shutil.rmtree(home, ignore_errors=True)
```

Add the tests to the tuple with Edit:
- old_string: `              test_a_failure_between_copy_and_remove_loses_nothing):`
- new_string:

  ```
                test_a_failure_between_copy_and_remove_loses_nothing,
                test_two_different_copies_are_left_alone_and_reported_once,
                test_bootstrap_and_the_sweep_print_one_json_object):
  ```

Run `eol.py`.

- [ ] **Step 2: Red**

```bash
W="D:/AI Projects/Claude Needed Tools/safeguard-c"; PYTHONIOENCODING=utf-8 PYTHONDONTWRITEBYTECODE=1 python "$W/one.py" D:/Claude/context-guard test_two_different_copies_are_left_alone_and_reported_once test_bootstrap_and_the_sweep_print_one_json_object
```

Expected: `FAILED 6 check(s)`. They fail because nothing reports a pair yet:
- `sweep/two`: `one json object`, `it names the memory`, `in one line`, `a copy that changed again is reported again`, `every occurrence is logged`
- `sweep/one-json: it carries the sweep's report`

- [ ] **Step 3: The report.** Use Edit: old_string `def sweep_memory_strays():`. The new_string is the block below, then two blank lines, then `def sweep_memory_strays():`.

```python
# Two different copies of one memory are the one thing the sweep speaks about, and each
# distinct pair only once. Keyed by both sha256 values, so a copy that changes again is a
# new pair and is reported again. A JSON OBJECT, because --report reads every *.json in
# the state folder as one.
SWEEP_SEEN = os.path.join(STATE, "sweep-seen.json")


def _report_pairs(pairs):
    """Log every pair, and return ONE line for SessionStart about the pairs not reported
    before - or "" when there are none."""
    if not pairs:
        return ""
    try:
        with open(SWEEP_SEEN, encoding="utf-8") as f:
            seen = json.load(f)
    except Exception:
        seen = {}
    if not isinstance(seen, dict):
        seen = {}
    new = []
    for key, name, mine, theirs in pairs:
        tag = mine + ":" + theirs
        log("sweep: two different copies of %s/%s and the shared one - both left untouched%s"
            % (key, name, " (reported before)" if tag in seen else ""))
        if tag not in seen:
            seen[tag] = "%s/%s %s" % (key, name, datetime.date.today().isoformat())
            new.append("%s (the shared folder's copy and %s's)" % (name[:-3], key))
    if not new:
        return ""
    tmp = SWEEP_SEEN + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(seen, f, indent=1, sort_keys=True)
    os.replace(tmp, SWEEP_SEEN)
    return ("MEMORY SWEEP: two different copies of the same memory - %s. Both were left "
            "untouched. Tell the author in ONE line which memory has two different copies, "
            "that both were left untouched and nothing is lost, and that a chat will merge "
            "them when he asks." % "; ".join(new))
```

- [ ] **Step 4: Collect the pairs in `_sweep()`.** This takes two Edits.

The first Edit's old_string:

```python
    for d in folders:
        key = os.path.basename(os.path.dirname(d))
```

Its new_string:

```python
    pairs = []
    for d in folders:
        key = os.path.basename(os.path.dirname(d))
```

The second Edit's old_string:

```python
                log("sweep: %s/%s is identical to its shared copy - moved to %s"
                    % (key, name, dest))
                continue
    return ""
```

Its new_string:

```python
                log("sweep: %s/%s is identical to its shared copy - moved to %s"
                    % (key, name, dest))
                continue
            # Two different copies: both stay exactly as they are, and he is told once.
            pairs.append((key, name, hashlib.sha256(mine).hexdigest(),
                          hashlib.sha256(theirs).hexdigest()))
    return _report_pairs(pairs)
```

Run `eol.py`.

- [ ] **Step 5: Green**, then run the full suite.

Re-run the Step 2 `one.py` command. Expected: `FAILED 0`.

Run `suite.py`. Expected: `rc=0 PASS=N0+90 FAIL=0`.

---

## Task 9: Cases 3 and 4 (a shared-list line with exactly one claimant)

**Files:**
- Modify: `guard.py`. Add a block above `def sweep_memory_strays():`, and make one edit in `_sweep()`.
- Test: `test_guard.py`. Add 5 tests and the tuple.

- [ ] **Step 1: Write the failing tests.** Insert this block above `if __name__ == "__main__":`.

```python
def test_the_sweep_drops_a_shared_line_its_one_claimant_already_lists():
    """Case 3. alpha-notes has one claimant - Alpha's list and the manifest agree - and
    Alpha's list already has the line, so the shared list stops carrying it."""
    home = make_sweep_home()
    try:
        plant(home, KEY, "alpha-notes.md", memory_text("alpha-notes", "body-ALPHA"))
        plant(home, KEY, "keep-me.md", memory_text("keep-me", "body-KEEP"))
        top = plant(home, KEY, "MEMORY.md", "# Memory Index\n%s\n%s\n"
                    % (index_entry("alpha-notes"), index_entry("keep-me")))
        old_top = raw_bytes(top)
        alst = plant_list(home, ALPHA, [index_entry("alpha-notes")], header_of(home))
        old_alpha = raw_bytes(alst)
        p = run_sweep(home)
        expect_clean(p, "sweep/case3")
        body = raw_bytes(top).decode("utf-8")
        check("sweep/case3: the line left the shared list", "(alpha-notes.md)" not in body,
              repr(body))
        check("sweep/case3: a line nobody claims stayed", "(keep-me.md)" in body, repr(body))
        check("sweep/case3: Alpha's list is untouched", raw_bytes(alst) == old_alpha)
        check("sweep/case3: the old shared list was backed up first",
              [raw_bytes(x) for x in sweep_backups(home, KEY, "MEMORY.md")] == [old_top],
              str(sweep_backups(home, KEY, "MEMORY.md")))
        check("sweep/case3: and it said nothing", p.stdout == "", repr(p.stdout[:200]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_sweep_moves_a_shared_line_to_the_one_claimant_that_lacks_it():
    """Case 4. beta-notes is claimed by the manifest alone, and Beta's list lacks it: his
    line is appended there word for word, and then it leaves the shared list."""
    home = make_sweep_home()
    try:
        plant(home, KEY, "beta-notes.md", memory_text("beta-notes", "body-BETA"))
        line = "- [Beta, in his words](beta-notes.md) - the wording he chose"
        top = plant(home, KEY, "MEMORY.md", "# Memory Index\n%s\n" % line)
        blst = plant_list(home, BETA, [index_entry("beta-own")], header_of(home))
        p = run_sweep(home)
        expect_clean(p, "sweep/case4")
        got = raw_bytes(blst).decode("utf-8")
        check("sweep/case4: his line was appended to Beta's list, word for word",
              got.splitlines()[-1] == line, repr(got[-200:]))
        check("sweep/case4: Beta's own line is still there", "(beta-own.md)" in got, repr(got))
        check("sweep/case4: the line left the shared list",
              "(beta-notes.md)" not in raw_bytes(top).decode("utf-8"), repr(raw_bytes(top)))
        check("sweep/case4: Beta's list is still LF", b"\r" not in raw_bytes(blst),
              repr(raw_bytes(blst)[-80:]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_sweep_leaves_a_slug_with_zero_or_two_claimants():
    """Unsure means it stays in the shared list, where every chat still sees it: no
    claimant, two claimants, or only a list in a folder no project maps to - no label
    fetch ever shows that list, so it is no claim."""
    home = make_sweep_home()
    try:
        h = header_of(home)
        slugs = ("nobody", "both", "stray", "alpha-notes")
        for s in slugs:
            plant(home, KEY, s + ".md", memory_text(s, "body-" + s))
        top = plant(home, KEY, "MEMORY.md",
                    "# Memory Index\n" + "\n".join(index_entry(s) for s in slugs) + "\n")
        plant_list(home, ALPHA, [index_entry("both"), index_entry("alpha-notes")], h)
        plant_list(home, BETA, [index_entry("both")], h)
        plant_list(home, "D--Somewhere-Else", [index_entry("stray")], h)
        p = run_sweep(home)
        expect_clean(p, "sweep/unsure")
        body = raw_bytes(top).decode("utf-8")
        check("sweep/unsure: zero claimants - it stays", "(nobody.md)" in body, repr(body))
        check("sweep/unsure: two claimants - it stays", "(both.md)" in body, repr(body))
        check("sweep/unsure: only an unmapped folder lists it - it stays",
              "(stray.md)" in body, repr(body))
        check("sweep/unsure: control - one claimant, and it left",
              "(alpha-notes.md)" not in body, repr(body))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_a_crlf_list_stays_crlf():
    """The shared MEMORY.md is CRLF today. Every rewrite keeps each file's own line
    endings, measured per file, and its final newline."""
    home = make_sweep_home()
    try:
        plant(home, KEY, "beta-notes.md", memory_text("beta-notes", "body-BETA"))
        plant(home, KEY, "keep-me.md", memory_text("keep-me", "body-KEEP"))
        top = plant(home, KEY, "MEMORY.md", "# Memory Index\n%s\n%s\n"
                    % (index_entry("beta-notes"), index_entry("keep-me")), eol="\r\n")
        blst = plant_list(home, BETA, [index_entry("beta-own")], header_of(home), eol="\r\n")
        p = run_sweep(home)
        expect_clean(p, "sweep/crlf")
        t, b = raw_bytes(top), raw_bytes(blst)
        check("sweep/crlf: control - the shared list was rewritten", b"beta-notes" not in t,
              repr(t))
        check("sweep/crlf: control - Beta's list was rewritten", b"beta-notes" in b, repr(b))
        for tag, blob in (("shared", t), ("beta", b)):
            check("sweep/crlf: the %s list has no bare LF" % tag,
                  blob.count(b"\n") == blob.count(b"\r\n"), repr(blob))
            check("sweep/crlf: the %s list still ends in CRLF" % tag, blob.endswith(b"\r\n"),
                  repr(blob[-20:]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_sweep_leaves_a_list_changed_in_the_last_ten_minutes():
    """A list another chat may be writing is not rewritten - neither the shared list nor
    the one list a line would be appended to. The next chat retries."""
    home = make_sweep_home()
    try:
        h = header_of(home)
        plant(home, KEY, "beta-notes.md", memory_text("beta-notes", "body-BETA"))
        top = plant(home, KEY, "MEMORY.md", "# Memory Index\n%s\n" % index_entry("beta-notes"),
                    age=60)
        blst = plant_list(home, BETA, [index_entry("beta-own")], h)
        run_sweep(home)
        check("sweep/fresh-list: a fresh shared list is not rewritten",
              b"beta-notes" in raw_bytes(top), repr(raw_bytes(top)))
        t = time.time() - SWEEP_OLD
        os.utime(top, (t, t))
        plant_list(home, BETA, [index_entry("beta-own")], h, age=60)
        run_sweep(home, sid="sweep002")
        check("sweep/fresh-list: a fresh target list - the line stays shared",
              b"beta-notes" in raw_bytes(top) and b"beta-notes" not in raw_bytes(blst),
              repr(raw_bytes(top)))
        os.utime(blst, (t, t))
        run_sweep(home, sid="sweep003")
        check("sweep/fresh-list: control - both settled, the line moves",
              b"beta-notes" not in raw_bytes(top) and b"beta-notes" in raw_bytes(blst),
              repr(raw_bytes(blst)))
    finally:
        shutil.rmtree(home, ignore_errors=True)
```

Add the tests to the tuple with Edit:
- old_string: `              test_bootstrap_and_the_sweep_print_one_json_object):`
- new_string:

  ```
                test_bootstrap_and_the_sweep_print_one_json_object,
                test_the_sweep_drops_a_shared_line_its_one_claimant_already_lists,
                test_the_sweep_moves_a_shared_line_to_the_one_claimant_that_lacks_it,
                test_the_sweep_leaves_a_slug_with_zero_or_two_claimants,
                test_a_crlf_list_stays_crlf,
                test_the_sweep_leaves_a_list_changed_in_the_last_ten_minutes):
  ```

Run `eol.py`.

- [ ] **Step 2: Red**

```bash
W="D:/AI Projects/Claude Needed Tools/safeguard-c"; PYTHONIOENCODING=utf-8 PYTHONDONTWRITEBYTECODE=1 python "$W/one.py" D:/Claude/context-guard test_the_sweep_drops_a_shared_line_its_one_claimant_already_lists test_the_sweep_moves_a_shared_line_to_the_one_claimant_that_lacks_it test_the_sweep_leaves_a_slug_with_zero_or_two_claimants test_a_crlf_list_stays_crlf test_the_sweep_leaves_a_list_changed_in_the_last_ten_minutes
```

Expected: `FAILED 8 check(s)`. They fail because no list line ever moves yet:
- `sweep/case3`: `the line left the shared list`, `the old shared list was backed up first`
- `sweep/case4`: `his line was appended to Beta's list, word for word`, `the line left the shared list`
- `sweep/unsure: control - one claimant, and it left`
- `sweep/crlf`: both controls
- `sweep/fresh-list: control - both settled, the line moves`

- [ ] **Step 3: Cases 3 and 4.** Use Edit: old_string `def sweep_memory_strays():`. The new_string is the block below, then two blank lines, then `def sweep_memory_strays():`.

```python
def _slug_b(line):
    """The slug an entry line (bytes) points at, or None."""
    m = LINK_RE_B.search(line)
    return m.group(1).decode("utf-8", "surrogateescape") if m else None


def _entries(blob):
    """slug -> its whole line, ending included, for each entry line of a list's bytes."""
    out = {}
    for ln in blob.splitlines(keepends=True):
        s = _slug_b(ln)
        if s is not None and s not in out:
            out[s] = ln
    return out


def _edit_list(before, drop=(), add=()):
    """A list's bytes without the entry lines for `drop` and with the lines in `add`
    appended. Every kept line stays byte for byte, added ones take the file's own line
    ending, and the final newline stays as it was."""
    eol = _eol(before)
    keep = [ln for ln in before.splitlines(keepends=True) if _slug_b(ln) not in drop]
    if add and keep and not keep[-1].endswith((b"\n", b"\r")):
        keep[-1] += eol
    keep += [ln.rstrip(b"\r\n") + eol for ln in add]
    out = b"".join(keep)
    if before and not before.endswith((b"\n", b"\r")) and out.endswith(eol):
        out = out[:-len(eol)]
    return out


def _sweep_lines(folders, now):
    """Cases 3 and 4: a shared-list line whose memory has exactly ONE claimant belongs in
    that claimant's list, not in the index every chat pays for.

    A claimant is a manifest project's PRIMARY folder: its list claims every slug it has a
    line for, and the manifest claims its `files` for it. An `also` folder, or a folder no
    project maps to, never claims - no label fetch shows its list, so a line moved there
    would vanish from every chat."""
    top = os.path.join(SHARED_MEMORY, "MEMORY.md")
    if not os.path.isfile(top) or _fresh(top, now):
        return
    try:
        with open(MANIFEST, encoding="utf-8") as f:
            man = json.load(f)
    except Exception as e:
        log("sweep: no usable manifest (%s) - the shared list is left as it is" % e)
        return
    claims, lists = {}, {}
    for e in (man.get("projects") or {}).values():
        k = dir_key(e.get("dir") or "")
        if k and k != SOURCE_KEY:
            lists.setdefault(k, None)
            for s in (e.get("files") or []):
                claims.setdefault(s, set()).add(k)
    for folder in folders:
        k = os.path.basename(os.path.dirname(folder))
        p = os.path.join(folder, "MEMORY.md")
        if k in lists and os.path.isfile(p):
            blob = _read_bytes(p)
            lists[k] = (p, blob, _entries(blob))
            for s in lists[k][2]:
                claims.setdefault(s, set()).add(k)
    before = _read_bytes(top)
    drop, adds = set(), {}
    for s, line in _entries(before).items():
        owners = claims.get(s) or set()
        if len(owners) != 1:
            continue             # nobody, or two or more: it stays where every chat sees it
        k = next(iter(owners))
        if not lists.get(k):
            continue             # its one claimant has no list yet: it stays shared
        if s not in lists[k][2]:
            if _fresh(lists[k][0], now):
                continue         # case 4, but that list may be being written: next chat
            adds.setdefault(k, []).append(line)       # case 4: his line, word for word
        drop.add(s)              # cases 3 and 4
    for k, new_lines in sorted(adds.items()):
        p, blob, _ents = lists[k]
        if _write_list(p, blob, _edit_list(blob, add=new_lines), k):
            log("sweep: %d shared-list line(s) appended to %s's list" % (len(new_lines), k))
        else:
            drop -= {_slug_b(ln) for ln in new_lines}
    if drop and _write_list(top, before, _edit_list(before, drop=drop), SOURCE_KEY):
        log("sweep: %d line(s) left the shared list for their one project's list: %s"
            % (len(drop), ", ".join(sorted(drop))))
```

- [ ] **Step 4: Call it.** Use Edit.

The old_string:

```python
    return _report_pairs(pairs)
```

The new_string:

```python
    _sweep_lines(folders, now)
    return _report_pairs(pairs)
```

Run `eol.py`.

- [ ] **Step 5: Green**, then run the full suite.

Re-run the Step 2 `one.py` command. Expected: `FAILED 0`.

Run `suite.py`. Expected: `rc=0 PASS=N0+120 FAIL=0`.

---

## Task 10: The README, commit B (part 3) and the fresh-clone proof

**Files:**
- Modify: `README.md`. Four edits. Use the Edit tool, because this file uses em-dashes.
- Commit: `guard.py`, `test_guard.py`, `README.md` and this plan.

- [ ] **Step 1: README edits**

1. A new paragraph after the label-fetch paragraph.
   - old_string: `memory folder yet is skipped in silence.`
   - new_string:

     ```
     memory folder yet is skipped in silence.

     **One home for every memory file.** Every memory file lives in the one shared folder
     your chats run in, and a project's own folder holds only its `MEMORY.md`, whose header
     line says where the files are — the label fetch says the same. At the start of every
     chat the SessionStart hook sweeps strays home: a memory saved into a project folder
     moves to the shared one, a spare copy identical to its shared copy moves to
     `~/.claude/memory-backups/`, and a shared index line that exactly one project claims
     moves to that project's list. Nothing is ever deleted, anything changed in the last ten
     minutes waits for the next chat, and it says nothing unless one memory has two different
     copies — then it tells you once and leaves both alone.
     ```

2. A hooks table row.
   - old_string: `| `guard.py --ledger` | Stop | appends your own words to a permanent ledger, writes a stub note if none exists, and past the ceiling refuses to let the chat close |`
   - new_string: the same line, then a new line: `| `guard.py --bootstrap` | SessionStart | sweeps stray memory files home, and gives a project directory its own memory list the first time a chat opens there |`

3. The commands table.
   - old_string: `| `guard.py --size` / `--ledger`, `audit.py --alert` | **no** — hook entry points with side effects, including consuming a handoff note |`
   - new_string: `| `guard.py --size` / `--ledger` / `--bootstrap`, `audit.py --alert` | **no** — hook entry points with side effects, including consuming a handoff note and moving memory files |`

4. The off switch.
   - old_string: `* `no-heredoc-guard` — allow heredocs again (or put `# heredoc-ok` in a single command)`
   - new_string: the same line, then a new line: `* `no-memory-sweep` — stop the SessionStart sweep moving memory files and index lines`

Run `eol.py`. It must show `README.md ok`, with the em-dashes intact.

- [ ] **Step 2: Stage**

**Path N:**

```bash
W="D:/AI Projects/Claude Needed Tools/safeguard-c"; PYTHONIOENCODING=utf-8 python "$W/stage_ours.py" "$W/baseline-B" --check && git -C D:/Claude/context-guard add guard.py test_guard.py README.md docs/superpowers/plans/2026-09-25-safeguard-c-memory-one-home.md
```

**Path F:**

```bash
W="D:/AI Projects/Claude Needed Tools/safeguard-c"; PYTHONIOENCODING=utf-8 python "$W/stage_ours.py" "$W/baseline-B" && git -C D:/Claude/context-guard add docs/superpowers/plans/2026-09-25-safeguard-c-memory-one-home.md
```

Expected: all three files show `HEAD + our hunks only`. If it refuses: STOP (S3).

- [ ] **Step 3: Verify the index**

```bash
git -C D:/Claude/context-guard diff --cached --name-only && git -C D:/Claude/context-guard diff --cached --stat
```

Expected: exactly `README.md`, `guard.py` and `test_guard.py`, plus the plan if it changed since commit A. Anything else: STOP (S3).

- [ ] **Step 3b: Commit**

```bash
git -C D:/Claude/context-guard commit -m "Sweep stray memories home at the start of every chat" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" && git -C D:/Claude/context-guard diff --numstat
```

Expected: after the commit, `--numstat` shows the same as Task 1 step 8, meaning their lines on path F and nothing on path N.

- [ ] **Step 4: Prove it on a fresh clone** (full-suite run)

```bash
W="D:/AI Projects/Claude Needed Tools/safeguard-c"; C="$W/clone-B-$(date +%H%M%S)"; git clone -q D:/Claude/context-guard "$C" && PYTHONIOENCODING=utf-8 PYTHONDONTWRITEBYTECODE=1 python "$W/suite.py" "$C" && PYTHONIOENCODING=utf-8 python "$W/eol.py" "$C"
```

Expected: `rc=0 PASS=C0+120 FAIL=0`, and four `ok` lines.

- [ ] **Step 5: Ask about the push (S4)**, in the same words as Task 5 step 6, with commit B's sha. Push only on a yes. Save the milestone memory.

---

## Task 11: memcheck.py learns one home (NOT IN THE REPO)

`~/.claude/context-guard/memcheck.py` is a read-only diagnostic that lives outside git. Nothing in this task is committed. The rollout's proof (Task 16) reads its drift line.

**Files:**
- Modify: `~/.claude/context-guard/memcheck.py` (LF). Seven Edits.

- [ ] **Step 1: Back it up, and record its current headline counts**

```bash
W="D:/AI Projects/Claude Needed Tools/safeguard-c"; cp "$HOME/.claude/context-guard/memcheck.py" "$W/memcheck.py.before" && PYTHONIOENCODING=utf-8 python "$HOME/.claude/context-guard/memcheck.py" | grep '^\['
```

Expected, as measured on 25 Sep:
- `iii: index line pointing at a file that is not there` 0
- `iii: manifest slug with no file` 0
- `iv: memory in A that no index and no CLAUDE.md line reaches` 3
- `[c: project copies vs A] identical 56, differ 4, only in project 0`

- [ ] **Step 2: The edits** (Edit tool; each old_string occurs exactly once)

**E1.** Resolve every list's links against the shared folder A.

The old_string:

```python
            if not tgt.startswith("http") and not (f / tgt).exists():
                dangling.append(f"{f.parent.name}/MEMORY.md -> {tgt}")
```

The new_string:

```python
            # one home (safeguard c): every list, shared or project, points into A
            if not tgt.startswith("http") and not (A / tgt).exists():
                dangling.append(f"{f.parent.name}/MEMORY.md -> {tgt} (missing in A)")
```

**E2.** Stop requiring a copy in each project folder. This takes three Edits.

1. old_string `section, sec_folder = None, None` → new_string `section = None`
2. old_string `        section, sec_folder = h.group(1), PROJ / folder_key(h.group(2)) / "memory"` → new_string `        section = h.group(1)`
3. The third old_string is the two lines below, including the newline that ends the second one. Its new_string is empty, so both lines are deleted.

```python
        if sec_folder is not None and not (sec_folder / tgt).exists():
            dangling.append(f"projects-index.md [{section}] -> {tgt} (missing in its project folder)")
```

**E3.** A manifest slug must be LISTED in its project list, and its file must be in A. This takes two Edits.

The first Edit's old_string is `man_missing = [f"t1 {s}" for s in sorted(t1) if not (A / f"{s}.md").exists()]`. Its new_string:

```python
# one home (safeguard c): a project folder holds only its list, so a manifest slug must be
# LISTED there - its file lives in A
proj_listed = {k: set(t[:-3] for _, t in IDX_LINK.findall(read(pf / "MEMORY.md")))
               if (pf / "MEMORY.md").exists() else set() for k, pf in proj_folder.items()}
man_missing = [f"t1 {s}" for s in sorted(t1) if not (A / f"{s}.md").exists()]
```

The second Edit's old_string:

```python
        if not (proj_folder[k] / f"{s}.md").exists():
            man_missing.append(f"{k} {s} (missing in {proj_folder[k].parent.name})")
```

Its new_string:

```python
        if s not in proj_listed[k]:
            man_missing.append(f"{k} {s} (not listed in {proj_folder[k].parent.name}/MEMORY.md)")
```

**E4.** The project lists reach memories too. This takes two Edits.

The first Edit's old_string is `c_idx = set(t[:-3] for _, t in IDX_LINK.findall(read(CIDX)))`. Its new_string:

```python
c_idx = set(t[:-3] for _, t in IDX_LINK.findall(read(CIDX)))
# one home (safeguard c): every project list reaches its memories in A too
l_idx = set()
for f in folders:
    if f != A and (f / "MEMORY.md").exists():
        l_idx |= set(t[:-3] for _, t in IDX_LINK.findall(read(f / "MEMORY.md")))
```

The second Edit changes this old_string:

```python
    where = [n for n, ix in (("A-index", a_idx), ("projects-index", p_idx), ("craft-index", c_idx)) if s in ix]
```

to this new_string:

```python
    where = [n for n, ix in (("A-index", a_idx), ("projects-index", p_idx), ("craft-index", c_idx), ("project-lists", l_idx)) if s in ix]
```

**E5.** The orphans count uses the project lists too.

The old_string:

```python
orphans = sorted(s for s in a_stems if s not in a_idx | p_idx | c_idx | claude_set)
```

The new_string:

```python
orphans = sorted(s for s in a_stems if s not in a_idx | p_idx | c_idx | l_idx | claude_set)
```

- [ ] **Step 3: Check it**

```bash
W="D:/AI Projects/Claude Needed Tools/safeguard-c"; PYTHONIOENCODING=utf-8 python "$W/eol.py" --memcheck && PYTHONIOENCODING=utf-8 python "$HOME/.claude/context-guard/memcheck.py" | grep '^\['
```

Expected:
- `memcheck.py ok (CRLF=0)`.
- The orphans line goes from 3 to 0.
- The two `iii` lines stay at 0.
- Every other headline count is unchanged.

If a count moves the other way, restore the file from `memcheck.py.before` and STOP.

---

## Task 12: Rollout step 1 - re-merge any drift, and pin the spares the sweep will leave alone

The rollout runs once. It starts only after Tasks 1-11 are done, with commit B proven, and with the off switch still present.

**Files:**
- Create: `WORK/rollout_1_drift.py`. It writes `WORK/merge-time.json` and `WORK/merge/`.

- [ ] **Step 1: Write `WORK/rollout_1_drift.py`**

```python
"""Rollout step 1 - re-merge any drift, and pin the spares the sweep will leave alone.

usage: python rollout_1_drift.py               classify every spare, write the pins and
                                               any merge candidates. Exit 2 = STOP.
       python rollout_1_drift.py --apply K/N   apply the approved merge for key K, file N

A spare is a topic file in a project folder. It is one of:
  home      no shared copy - the sweep moves it home
  identical same as its shared copy but for `modified:` - the sweep backs it up
  pinned    differs, and is unchanged since part 1 (or was re-merged here) - rollout_4
            moves it after a sha256 check
  merge     differs AND changed since part 1 - needs a 3-way merge onto the shared copy
  unknown   differs, and has no part-1 copy - needs him"""
import datetime
import hashlib
import json
import os
import subprocess
import sys

sys.path.insert(0, "D:/Claude/context-guard")
import guard                                    # the real home; pure helpers only

BK = os.path.join(guard.HOME, ".claude", "memory-backups", "2026-09-25-before-merge")
WORK = os.path.dirname(os.path.abspath(__file__))
PINS = os.path.join(WORK, "merge-time.json")
MERGED = os.path.join(WORK, "merge")


def sha(b):
    return hashlib.sha256(b).hexdigest()


old_pins = {}
if os.path.exists(PINS):
    with open(PINS, encoding="utf-8") as f:
        old_pins = json.load(f)

if sys.argv[1:2] == ["--apply"]:
    key, name = sys.argv[2].split("/", 1)
    blob = guard._read_bytes(os.path.join(MERGED, "%s__%s" % (key, name)))
    if b"\n<<<<<<< " in b"\n" + blob:
        sys.exit("the merge still has conflict markers - resolve them with him first")
    shared = os.path.join(guard.SHARED_MEMORY, name)
    bak = guard._free_path(os.path.join(guard.SWEEP_BACKUPS, datetime.date.today().isoformat()
                                        + "-remerge", guard.SOURCE_KEY, name))
    guard._put_new(bak, guard._read_bytes(shared))
    tmp = shared + ".remerge-tmp"
    with open(tmp, "wb") as f:
        f.write(blob)
    os.replace(tmp, shared)
    old_pins["%s/%s" % (key, name)] = sha(guard._read_bytes(
        os.path.join(guard.PROJECTS, key, "memory", name)))
    with open(PINS, "w", encoding="utf-8") as f:
        json.dump(old_pins, f, indent=1, sort_keys=True)
    print("applied: the shared copy of %s was replaced (the old one is at %s); spare pinned"
          % (name, bak))
    sys.exit(0)

pins, counts, stop, changed_same = {}, dict.fromkeys(
    ("home", "identical", "pinned", "merge", "unknown"), 0), [], 0
os.makedirs(MERGED, exist_ok=True)
for k in sorted(os.listdir(guard.PROJECTS)):
    d = os.path.join(guard.PROJECTS, k, "memory")
    if k == guard.SOURCE_KEY or not os.path.isdir(d):
        continue
    for name in sorted(os.listdir(d)):
        p = os.path.join(d, name)
        if name == "MEMORY.md" or not name.endswith(".md") or not os.path.isfile(p):
            continue
        kn, mine = "%s/%s" % (k, name), guard._read_bytes(p)
        shared, bak = os.path.join(guard.SHARED_MEMORY, name), os.path.join(BK, k, name)
        old = guard._read_bytes(bak) if os.path.isfile(bak) else None
        if not os.path.isfile(shared):
            counts["home"] += 1
        elif guard._sans_modified(mine) == guard._sans_modified(guard._read_bytes(shared)):
            counts["identical"] += 1
            changed_same += old is not None and old != mine
        elif old_pins.get(kn) == sha(mine) or old == mine:
            counts["pinned"] += 1
            pins[kn] = sha(mine)
        elif old is not None:
            counts["merge"] += 1
            out = os.path.join(MERGED, "%s__%s" % (k, name))
            m = subprocess.run(["git", "merge-file", "-p", "--diff3", "-L", "shared",
                                "-L", "merge-time", "-L", "spare", shared, bak, p],
                               capture_output=True)
            with open(out, "wb") as f:
                f.write(m.stdout)
            stop.append("MERGE %s -> %s (merge-file exit %d: 0 = clean, N = conflicts). "
                        "Show him: git diff --no-index \"%s\" \"%s\"" % (kn, out, m.returncode,
                                                                          shared, out))
        else:
            counts["unknown"] += 1
            stop.append("UNKNOWN %s differs from its shared copy and has no part-1 copy" % kn)
with open(PINS, "w", encoding="utf-8") as f:
    json.dump(pins, f, indent=1, sort_keys=True)
print("spares %d: %s" % (sum(counts.values()), ", ".join("%s %d" % kv for kv in counts.items())))
print("changed since part 1 but identical to the shared copy (nothing to merge): %d"
      % changed_same)
print("pins written: %d -> %s" % (len(pins), PINS))
for s in stop:
    print(s)
sys.exit(2 if stop else 0)
```

- [ ] **Step 2: Run it**

```bash
W="D:/AI Projects/Claude Needed Tools/safeguard-c"; PYTHONIOENCODING=utf-8 PYTHONDONTWRITEBYTECODE=1 python "$W/rollout_1_drift.py"
```

Expected, measured today: `spares 60: home 0, identical 56, pinned 4, merge 0, unknown 0`, then `changed since part 1 but identical to the shared copy (nothing to merge): 1`, and `pins written: 4`, with exit 0.

- **Exit 2 (STOP S5):** show the user every MERGE and UNKNOWN line. For each MERGE, show the `git diff --no-index` output.
- **On the user's OK for one file:** run `rollout_1_drift.py --apply <key>/<name>`, then run the classification again until it exits 0.
- **UNKNOWN:** the user decides.

---

## Task 13: Rollout step 2 - the header line in every project list

**Files:**
- Create: `WORK/rollout_2_headers.py`. It rewrites up to 11 real lists, each backed up first.

- [ ] **Step 1: Write `WORK/rollout_2_headers.py`**

```python
"""Rollout step 2 - give every project list the one-home header. Idempotent: a second
run changes nothing. A list changed in the last ten minutes is skipped; run it again
later. Each rewrite is backed up first (guard._write_list).
usage: python rollout_2_headers.py"""
import os
import sys
import time

sys.path.insert(0, "D:/Claude/context-guard")
import guard


def ends(b):
    crlf = b.count(b"\r\n")
    return crlf, b.count(b"\n") - crlf


now, lead, rows, bad = time.time(), guard.LIST_HEADER_LEAD.encode("utf-8"), [], 0
for k in sorted(os.listdir(guard.PROJECTS)):
    p = os.path.join(guard.PROJECTS, k, "memory", "MEMORY.md")
    if k == guard.SOURCE_KEY or not os.path.isfile(p):
        continue
    before = guard._read_bytes(p)
    if guard.with_list_header(before) == before:
        rows.append(("had it", k))
        continue
    if guard._fresh(p, now):
        rows.append(("SKIPPED - changed in the last 10 minutes", k))
        continue
    if not guard.ensure_list_header(p):
        rows.append(("FAILED - changed while it was being read", k))
        bad += 1
        continue
    after, problems = guard._read_bytes(p), []
    if guard._entries(after) != guard._entries(before):
        problems.append("its entries changed")
    (c0, l0), (c1, l1) = ends(before), ends(after)
    if (c1 - c0, l1 - l0) != ((1, 0) if guard._eol(before) == b"\r\n" else (0, 1)):
        problems.append("its line endings changed")
    if after.endswith(b"\n") != before.endswith(b"\n"):
        problems.append("its final newline changed")
    if sum(ln.startswith(lead) for ln in after.splitlines()) != 1:
        problems.append("it does not have exactly one header")
    bad += bool(problems)
    rows.append(("added" if not problems else "BAD: " + ", ".join(problems), k))
for what, k in rows:
    print("  %-44s %s" % (what, k))
print("lists %d: added %d, had it %d, skipped %d, bad %d" % (
    len(rows), sum(r[0] == "added" for r in rows), sum(r[0] == "had it" for r in rows),
    sum(r[0].startswith("SKIPPED") for r in rows), bad))
sys.exit(1 if bad else 0)
```

- [ ] **Step 2: Run it twice**

```bash
W="D:/AI Projects/Claude Needed Tools/safeguard-c"; PYTHONIOENCODING=utf-8 PYTHONDONTWRITEBYTECODE=1 python "$W/rollout_2_headers.py" && PYTHONIOENCODING=utf-8 PYTHONDONTWRITEBYTECODE=1 python "$W/rollout_2_headers.py"
```

Expected:
- First run: `lists 11: added 11, had it 0, skipped 0, bad 0`.
- Second run: `added 0, had it 11`.
- If any list was skipped, wait 10 minutes and run it again. Do not force it.
- If the result is bad: STOP, and restore that list from `~/.claude/memory-backups/<today>-sweep/<key>/MEMORY.md`.

---

## Task 14: Rollout step 3 - the dry run on a copy (STOP S6)

**Files:**
- Create: `WORK/rollout_3_dryrun.py`. It creates `WORK/dryrun-<stamp>/`. Nothing real is touched.

- [ ] **Step 1: Write `WORK/rollout_3_dryrun.py`**

```python
"""Rollout step 3 - the dry run. Copies every memory folder and the manifest into a
throwaway home, runs the REAL guard.py --bootstrap there once, and prints what the sweep
did. Touches nothing real. Exit 1 = it differs from the expectation: STOP and show him.
usage: python rollout_3_dryrun.py"""
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time

REPO = "D:/Claude/context-guard"
sys.path.insert(0, REPO)
import guard                                        # the real home; pure helpers only

WORK = os.path.dirname(os.path.abspath(__file__))
EXPECT = {"spares moved to the backup": 56, "memories moved home": 0,
          "lines that left the shared list": 3, "lines that landed in no list": 0,
          "lines appended to a project list": 0, "project lists rewritten": 0,
          "shared-list lines left": 7, "pinned spares untouched": 4,
          "pinned spares named in the report": 4, "list backups": 1,
          "json objects printed": 1, "anything else changed": 0}
with open(os.path.join(WORK, "merge-time.json"), encoding="utf-8") as f:
    pins = json.load(f)

T = os.path.join(WORK, "dryrun-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
TC = os.path.join(T, ".claude")
for k in sorted(os.listdir(guard.PROJECTS)):
    src = os.path.join(guard.PROJECTS, k, "memory")
    if os.path.isdir(src):
        shutil.copytree(src, os.path.join(TC, "projects", k, "memory"))   # mtimes kept
os.makedirs(os.path.join(TC, "context-guard"))
shutil.copy2(guard.MANIFEST, os.path.join(TC, "context-guard", "memory-manifest.json"))


def read(rel):
    return guard._read_bytes(os.path.join(TC, rel))


def snap():
    out = {}
    for root, _dirs, files in os.walk(TC):
        for n in files:
            rel = os.path.relpath(os.path.join(root, n), TC).replace("\\", "/")
            if rel.startswith(("projects/", "memory-backups/")):
                out[rel] = hashlib.sha256(read(rel)).hexdigest()
    return out


before = snap()
lists0 = {r: read(r) for r in before if r.endswith("/memory/MEMORY.md")}
now = time.time()
young = sorted(r for r in before
               if now - os.path.getmtime(os.path.join(TC, r)) < guard.SWEEP_SETTLE)
if young:
    print("WARNING: %d file(s) changed in the last 10 minutes - the sweep skips them:" % len(young))
    for r in young:
        print("   " + r)

env = dict(os.environ, USERPROFILE=T, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
env.pop("HOME", None)
payload = {"session_id": "dryrun01", "cwd": "D:\\Claude", "hook_event_name": "SessionStart",
           "source": "startup"}
t0 = time.perf_counter()
p = subprocess.run([sys.executable, os.path.join(REPO, "guard.py"), "--bootstrap"],
                   input=json.dumps(payload), capture_output=True, text=True,
                   encoding="utf-8", env=env, timeout=15)
wall = (time.perf_counter() - t0) * 1000
after = snap()

today = datetime.date.today().isoformat()
top = "projects/%s/memory/MEMORY.md" % guard.SOURCE_KEY
gone = sorted(r for r in before if r not in after)
new = sorted(r for r in after if r not in before)
changed = sorted(r for r in before if r in after and before[r] != after[r])
to_backup, home, unexplained = [], [], []
for r in gone:
    _p, key, _m, name = r.split("/", 3)
    bak = "memory-backups/%s-sweep/%s/%s" % (today, key, name)
    dst = "projects/%s/memory/%s" % (guard.SOURCE_KEY, name)
    if bak in new and after[bak] == before[r]:
        to_backup.append(r)
        new.remove(bak)
    elif dst in new and after[dst] == before[r]:
        home.append(r)
        new.remove(dst)
    else:
        unexplained.append("GONE " + r)
list_baks = [r for r in new if r.startswith("memory-backups/") and r.endswith("/MEMORY.md")]
unexplained += ["NEW " + r for r in new if r not in list_baks]
unexplained += ["CHANGED " + r for r in changed if not r.endswith("/memory/MEMORY.md")]
lists_changed = [r for r in changed if r.endswith("/memory/MEMORY.md") and r != top]

e0, e1 = guard._entries(lists0[top]), guard._entries(read(top))
left = [s for s in e0 if s not in e1]
appended, wording, landed = [], [], set()
for r, b0 in sorted(lists0.items()):
    if r == top:
        continue
    old, cur = guard._entries(b0), guard._entries(read(r))
    appended += ["%s <- %s" % (r.split("/")[1], s) for s in cur if s not in old]
    for s in left:
        if s in cur:
            landed.add(s)
            a, b = e0[s].rstrip(b"\r\n"), cur[s].rstrip(b"\r\n")
            wording.append("%s: kept in %s's list, %s" % (
                s, r.split("/")[1], "same wording" if a == b else
                "DIFFERENT wording\n      shared : %s\n      project: %s"
                % (a.decode("utf-8", "replace"), b.decode("utf-8", "replace"))))

ctx, objects = "", 0
if p.stdout.strip():
    try:
        ctx = json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"]
        objects = 1
    except Exception:
        objects = -1                                  # printed, but not ONE json object
untouched = sum(1 for kn, h in pins.items()
                if after.get("projects/%s/memory/%s" % tuple(kn.split("/", 1))) == h)
named = sum(1 for kn in pins if kn.split("/", 1)[1][:-3] in ctx)
logp = os.path.join(TC, "context-audit.log")
logtxt = guard._read_bytes(logp).decode("utf-8", "replace") if os.path.exists(logp) else ""
ms = [int(x) for x in re.findall(r"sweep: done in (\d+) ms", logtxt)]

actual = {"spares moved to the backup": len(to_backup), "memories moved home": len(home),
          "lines that left the shared list": len(left),
          "lines that landed in no list": len([s for s in left if s not in landed]),
          "lines appended to a project list": len(appended),
          "project lists rewritten": len(lists_changed), "shared-list lines left": len(e1),
          "pinned spares untouched": untouched, "pinned spares named in the report": named,
          "list backups": len(list_baks), "json objects printed": objects,
          "anything else changed": len(unexplained)}

print("\nDRY RUN on a copy at %s - nothing real was touched" % T)
print("hook: rc=%d, %.0f ms wall; the sweep itself: %s ms (budget 1000; hook timeout 15 s)"
      % (p.returncode, wall, ms))
per = {}
for r in to_backup:
    per[r.split("/")[1]] = per.get(r.split("/")[1], 0) + 1
print("spares moved to the backup, per folder:")
for k, n in sorted(per.items()):
    print("  %3d  %s" % (n, k))
print("memories moved home: %s" % (home or "none"))
print("lines that left the shared list:")
for w in wording or ["none"]:
    print("  " + w)
print("lines appended to project lists: %s" % (appended or "none"))
print("what the chat would be told: %r" % ctx)
for u in unexplained:
    print("UNEXPLAINED " + u)
print("\n%-36s %8s %8s" % ("", "expected", "actual"))
diff = 0
for k in EXPECT:
    mark = "" if EXPECT[k] == actual[k] else "   <- DIFFERENT"
    diff += bool(mark)
    print("%-36s %8d %8d%s" % (k, EXPECT[k], actual[k], mark))
slow = p.returncode != 0 or not ms or max(ms) >= 1000
print("\nRESULT: %s" % ("as expected - show him all of this and wait for his OK"
                        if not diff and not slow else "DIFFERENT - STOP and show him"))
sys.exit(1 if diff or slow else 0)
```

- [ ] **Step 2: Run it** (the measurement of the sweep's runtime on a copy of the real folders)

```bash
W="D:/AI Projects/Claude Needed Tools/safeguard-c"; PYTHONIOENCODING=utf-8 PYTHONDONTWRITEBYTECODE=1 python "$W/rollout_3_dryrun.py"
```

Expected (measured today):

| what | count |
|---|---|
| spares moved to the backup | 56 |
| memories moved home | 0 |
| lines that left the shared list | 3 (the Context Guard lines, kept in Context Guard's list) |
| shared-list lines left | 7 |
| pinned spares untouched, and named in one report line | 4 |
| list backups | 1 |
| anything else changed | 0 |
| the sweep's time | well under 1000 ms (reading everything took 56 ms) |

At least one of the 3 lines prints `DIFFERENT wording`.

- [ ] **Step 3: STOP (S6).** Show the user the whole output, including every `DIFFERENT wording` pair. The shared wording survives in the backup either way. Say that nothing real has moved, and ask:

> OK to run it for real?

- On `DIFFERENT - STOP`: show the user the rows marked `<- DIFFERENT` (S6), or the time (S7). Do not continue.
- A difference caused only by the WARNING files: offer to run the dry run again 10 minutes later.

---

## Task 15: Rollout step 4 - the real run, then the pinned spares (only after the user's OK in Task 14)

**Files:**
- Create: `WORK/rollout_4_real.py`

- [ ] **Step 1: Write `WORK/rollout_4_real.py`**

```python
"""Rollout step 4 - the real run.
  (a) One real sweep, called directly, so the off switch stays on for every chat
      meanwhile. Its report comes to whoever runs this, and sweep-seen.json keeps it from
      reaching a later chat.
  (b) The pinned spares to the backup - only if EVERY one still has its pinned sha256.
      Otherwise nothing moves.
usage: python rollout_4_real.py"""
import hashlib
import json
import os
import sys

sys.path.insert(0, "D:/Claude/context-guard")
import guard

WORK = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(WORK, "merge-time.json"), encoding="utf-8") as f:
    pins = json.load(f)
print("4a - the sweep said: %r" % guard.sweep_memory_strays())
bad, todo = [], []
for kn, want in sorted(pins.items()):
    key, name = kn.split("/", 1)
    p = os.path.join(guard.PROJECTS, key, "memory", name)
    try:
        got = hashlib.sha256(guard._read_bytes(p)).hexdigest()
    except OSError as e:
        bad.append("%s: cannot be read (%s)" % (kn, e))
        continue
    if got == want:
        todo.append((key, name, p))
    else:
        bad.append("%s: its sha256 changed since it was pinned" % kn)
if bad:
    print("4b STOPPED - nothing was moved:")
    for b in bad:
        print("  " + b)
    sys.exit(1)
for key, name, p in todo:
    dest = guard._free_path(os.path.join(guard._backup_dir(key), name))
    guard._move(p, dest)
    guard.log("rollout: pinned spare %s/%s moved to %s" % (key, name, dest))
    print("4b - moved %s/%s -> %s" % (key, name, dest))
```

- [ ] **Step 2: Run it**

```bash
W="D:/AI Projects/Claude Needed Tools/safeguard-c"; PYTHONIOENCODING=utf-8 PYTHONDONTWRITEBYTECODE=1 python "$W/rollout_4_real.py"
```

Expected:
- `4a` prints one `MEMORY SWEEP: two different copies ...` line naming the 4 pinned memories.
- `4b` prints 4 `moved` lines.
- Tell the user the 4a line in one sentence: these are the 4 known-different spares, and 4b has just moved them to the backup.
- If it prints `4b STOPPED` (S8): show the user the lines, and do not move anything by hand.

---

## Task 16: Rollout step 5 - prove it, then switch the sweep on

**Files:**
- Create: `WORK/rollout_5_prove.py`. It removes `~/.claude/context-guard/no-memory-sweep` only if every check passes.

- [ ] **Step 1: Write `WORK/rollout_5_prove.py`**

```python
"""Rollout step 5 - prove it. Exit 1 on any failure; the off switch is removed ONLY when
every check passes, and then one live SessionStart must print nothing.
usage: python rollout_5_prove.py"""
import json
import os
import re
import subprocess
import sys

REPO = "D:/Claude/context-guard"
sys.path.insert(0, REPO)
import guard

fails, lead = [], guard.LIST_HEADER_LEAD.encode("utf-8")
for k in sorted(os.listdir(guard.PROJECTS)):
    d = os.path.join(guard.PROJECTS, k, "memory")
    if k == guard.SOURCE_KEY or not os.path.isdir(d):
        continue
    extra = sorted(n for n in os.listdir(d) if n != "MEMORY.md")
    if extra:
        fails.append("%s holds more than MEMORY.md: %s" % (k, ", ".join(extra)))
    lst = os.path.join(d, "MEMORY.md")
    if not os.path.isfile(lst):
        continue
    blob = guard._read_bytes(lst)
    heads = sum(1 for ln in blob.splitlines() if ln.startswith(lead))
    if heads != 1:
        fails.append("%s/MEMORY.md has %d header lines, not 1" % (k, heads))
    for s in guard._entries(blob):
        if not os.path.isfile(os.path.join(guard.SHARED_MEMORY, s + ".md")):
            fails.append("%s/MEMORY.md lists %s, which is not in the shared folder" % (k, s))
mc = subprocess.run([sys.executable, os.path.join(guard.STATE, "memcheck.py")],
                    capture_output=True, text=True, encoding="utf-8", errors="replace")
drift = [l for l in mc.stdout.splitlines() if l.startswith("[c: project copies vs A]")]
if drift != ["[c: project copies vs A] identical 0, differ 0, only in project 0"]:
    fails.append("memcheck's drift line: %r" % (drift,))
if fails:
    print("PROOF FAILED - the off switch stays on:")
    for f in fails:
        print("  " + f)
    sys.exit(1)
off = os.path.join(guard.STATE, guard.SWEEP_OFF)
if os.path.exists(off):
    os.remove(off)
print("every proof passed; the off switch is removed - the sweep is live")
size = os.path.getsize(guard.LOG) if os.path.exists(guard.LOG) else 0
payload = {"session_id": "rollout5", "cwd": "D:\\Claude", "hook_event_name": "SessionStart",
           "source": "startup"}
p = subprocess.run([sys.executable, os.path.join(REPO, "guard.py"), "--bootstrap"],
                   input=json.dumps(payload), capture_output=True, text=True,
                   encoding="utf-8", timeout=15)
tail = ""
if os.path.exists(guard.LOG):
    with open(guard.LOG, "rb") as f:
        f.seek(size)
        tail = f.read().decode("utf-8", "replace")
ms = re.findall(r"sweep: done in (\d+) ms", tail)
print("one live SessionStart: rc=%d, stdout=%r, sweep ms=%s" % (p.returncode, p.stdout[:200], ms))
if p.returncode == 0 and p.stdout == "" and ms:
    sys.exit(0)
open(off, "w").close()                  # the live check failed: the switch goes back on
print("LIVE CHECK FAILED - the off switch is back on. The log tail:")
print(tail[-1500:])
sys.exit(1)
```

- [ ] **Step 2: Run it**

```bash
W="D:/AI Projects/Claude Needed Tools/safeguard-c"; PYTHONIOENCODING=utf-8 PYTHONDONTWRITEBYTECODE=1 python "$W/rollout_5_prove.py"
```

Expected:
- `every proof passed; the off switch is removed - the sweep is live`
- Then `one live SessionStart: rc=0, stdout='', sweep ms=['<n>']`

If it prints `PROOF FAILED` or `LIVE CHECK FAILED` (S9), the switch is on. Show the user every line.
- A non-.md file left in a project folder is left there by design. The user decides what happens to it.
- A file skipped because it was fresh: wait 10 minutes, run the real sweep once more, then run this step again. The sweep command:

  ```bash
  PYTHONIOENCODING=utf-8 PYTHONDONTWRITEBYTECODE=1 python -c "import sys; sys.path.insert(0, 'D:/Claude/context-guard'); import guard; print(repr(guard.sweep_memory_strays()))"
  ```

- The live SessionStart printed something: the sweep found two new different copies. Show the user the line. The user decides whether the switch comes off.

- [ ] **Step 3: Record and report.**
  - Save the milestone memory, with the rollout's measured numbers.
  - Then give the user the end-of-build report in the user's format: what changed, what succeeded, what failed. Then the still-open items as two tables: **"Still open — needs you"** and **"Still open — automatic, just for you to read"**.
  - Include any push not yet answered, and the uncommitted plan ticks, if any.
