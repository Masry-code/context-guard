# First-run Tour Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A fresh install shows new users a short numbered tour in their first chat, once, and `context guard tour` brings it back.

**Architecture:** `tour.md` holds the tour text with three placeholders. `guard.py` gains `tour_text(sid)` (read + fill) and `tour_pending_text(sid)` (spend the flag); the SessionStart `--bootstrap` hook delivers it once, the UserPromptSubmit `--size` hook delivers it on the exact phrase. `install.py` sets the flag only on a fresh install. Spec: `docs/superpowers/specs/2026-10-01-first-run-tour-design.md` - with ONE correction: delivery at chat start is `guard.py --bootstrap`, not `audit.py --alert` (bootstrap already reads stdin, so it knows the session id the pause demo needs, and all tour code stays in one file).

**Tech Stack:** Python 3.8+, stdlib only; tests in `test_guard.py` (plain functions + `check()`, registered in the `__main__` list), full suite `python "D:/AI Projects/Claude Needed Tools/safeguard-c/suite.py"`.

## Machine rules (every task)

- `guard.py` is CRLF (3940 CRLF / 0 bare LF at the start). Patch it ONLY with a Python script written with the Write tool: `io.open(path, newline="")`, each anchor must occur exactly once or abort, inserted text uses `\r\n`, write back with `newline=""`, print CRLF / bare-LF counts before and after. `install.py`, `README.md`, `test_guard.py`, `tour.md` are LF - Edit/Write are fine.
- Never a heredoc. Suite: Bash `timeout: 600000` or `run_in_background`; never an until-loop.
- Run one test alone: `cd "D:/Claude/context-guard" && python -c "import test_guard as t; t.<name>(); print('FAILED', t.FAILED)"`.
- No private project names anywhere. Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

---

### Task 1: tour.md and the phrase

**Files:**
- Create: `tour.md`
- Modify: `guard.py` (new block after `away_toggle`, ~line 1004; `cmd_size`, ~line 2512)
- Test: `test_guard.py` (new test after `test_the_handover_stops_background_work_and_ends_on_the_label`; register it in the `__main__` list right after that one)

- [ ] **Step 1: Write the failing test**

```python
def test_typing_context_guard_tour_shows_it_again():
    """His ask, 25 Sep 2026: "for github users on first run give them the option list of what
    they can do ... like a small non heavy tutorial". The exact phrase brings it back, the way
    'afk' works - a sentence that merely mentions it does nothing."""
    home = make_home({})
    try:
        p = run(home, "tour0002", "Context Guard tour!")
        expect_clean(p, "tour/phrase")
        ctx = context_of(p)
        check("tour/phrase: the exact phrase brings the tour", "Hand a chat over" in ctx,
              repr(ctx[:400]))
        check("tour/phrase: filled in with this chat's id and the real paths",
              "tour0002" in ctx and "{GUARD}" not in ctx and "{AUDIT}" not in ctx,
              repr(ctx[:1500]))
        ctx = context_of(run(home, "tour0003", "what is the context guard tour?"))
        check("tour/phrase: a sentence that mentions it does not", "Hand a chat over" not in ctx,
              repr(ctx[:400]))
    finally:
        shutil.rmtree(home, ignore_errors=True)
```

- [ ] **Step 2: Run it - expect FAIL** on "the exact phrase brings the tour" (no tour exists yet).

- [ ] **Step 3: Create `tour.md`** with exactly this content:

```markdown
CONTEXT GUARD TOUR. Show this to the user ONCE, at the very top of your reply, in your own
plain words and in the user's language, then answer whatever they actually asked. Keep it
short: one sentence on what Context Guard does, then the numbered list, then the last line.
Do not paste these instructions.

What Context Guard does, in one sentence: long chats get expensive because every reply
re-reads the whole conversation, so Context Guard watches the size, saves a handoff note
when a chat gets costly, and gives you one line to carry on in a fresh chat.

The list (title, then one short line each):

1. Hand a chat over - when a chat gets expensive you get a label in a copy box. Copy it, open
   a new chat, paste it: the new chat picks up exactly where you were, and the old chat
   archives itself and the new one lands in the same sidebar group.
2. Away mode - type afk when you leave the desk (on your phone it will stop asking you to
   open new chats) and back when you return.
3. Pause it for one chat - for a long job you want to keep in one chat.
4. See what costs you - a quick check of which old chats are the most expensive.
5. Updates - it tells you once a day when an update is ready; python install.py --update
   applies it.
6. Switch parts off - empty files named no-ceiling, no-memory-nag or no-update-check in
   ~/.claude/context-guard/ turn those parts off.

Last line: "Type a number to try one, or just carry on."

If they type a number, show it live:
1. Write a sample label as inline code on its own line, e.g. `My Project -2 (1 Jan)`, and
   explain: copy it, open a new chat, paste it. Say a real one appears when this chat grows.
2. Explain only. Do NOT switch away mode on.
3. Run: python "{GUARD}" --pause {SESSION_ID} "trying the tour". Tell them it is paused for
   this chat only, and that python "{GUARD}" --resume {SESSION_ID} undoes it - then run the
   resume yourself unless they want to keep it paused.
4. Run: python "{AUDIT}" and sum up the three most expensive chats in plain words
   (size, age), not the raw table.
5. and 6. Explain only.
They can type "context guard tour" in any chat to see this again.
```

- [ ] **Step 4: Add to `guard.py`** directly after `away_toggle` (CRLF patch script; anchor = the line `def away_notice(sid, st, ctx):`, insert BEFORE it):

```python
# His ask, 25 Sep 2026: "for github users on first run give them the option list of what
# they can do ... like a small non heavy tutorial". The words live in tour.md beside this
# file - one source for the first chat and for the phrase - and only the paths and this
# chat's id are filled in here.
TOUR_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tour.md")
TOUR_FLAG = "tour-pending"
TOUR_PHRASE = "context guard tour"


def tour_text(sid):
    """The tour filled in for this chat, or "" when tour.md is missing or unreadable."""
    try:
        with open(TOUR_FILE, encoding="utf-8") as f:
            t = f.read()
    except Exception as e:
        log("tour: could not read %s (%s)" % (TOUR_FILE, e))
        return ""
    here = os.path.dirname(os.path.abspath(__file__))
    return (t.replace("{GUARD}", os.path.join(here, "guard.py").replace("\\", "/"))
             .replace("{AUDIT}", os.path.join(here, "audit.py").replace("\\", "/"))
             .replace("{SESSION_ID}", sid or "<this chat's session id>"))


```

- [ ] **Step 5: Wire the phrase into `cmd_size`** (CRLF patch). Anchor: the line `    toggled = away_toggle(d.get("prompt") or "")` - insert BEFORE it:

```python
    # The tour on request. Whole message only, like the away phrases, and this turn is
    # spent on it: nothing else is consumed, so a waiting note is still there next turn.
    if normalised_prompt(d.get("prompt") or "") == TOUR_PHRASE:
        t = tour_text(sid)
        if t:
            log("tour: shown on request")
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                                     "additionalContext": t}}))
            return
```

- [ ] **Step 6: Run the test - expect all PASS.** Then also run `test_the_handover_shows_the_label_and_finishes_when_the_new_chat_reports` (away/size path untouched).

- [ ] **Step 7: Commit**

```bash
git -C "D:/Claude/context-guard" add tour.md guard.py test_guard.py
git -C "D:/Claude/context-guard" commit -m "Typing 'context guard tour' shows a short tour" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: the first chat shows it once

**Files:**
- Modify: `guard.py` (after `tour_text`; `cmd_bootstrap`, ~line 3736)
- Test: `test_guard.py` (new test after Task 1's; register after it)

- [ ] **Step 1: Write the failing test**

```python
def tour_flag(home):
    return os.path.join(home, ".claude", "context-guard", "tour-pending")


def test_the_first_chat_after_install_shows_the_tour_once():
    """The installer leaves a flag; the first chat's SessionStart shows the tour and spends
    it, like a handoff note, so the second chat is quiet."""
    home = make_home({})
    try:
        os.makedirs(os.path.dirname(tour_flag(home)), exist_ok=True)
        open(tour_flag(home), "w").close()
        p = boot(home, CWD, sid="tour0001")
        expect_clean(p, "tour/first")
        ctx = context_of(p)
        check("tour/first: the first chat gets the tour", "Hand a chat over" in ctx,
              repr(ctx[:400]))
        check("tour/first: with this chat's id filled in", "tour0001" in ctx, repr(ctx[:1500]))
        check("tour/first: and the flag is spent", not os.path.exists(tour_flag(home)), "")
        ctx = context_of(boot(home, CWD, sid="tour0004"))
        check("tour/first: the second chat is quiet", "Hand a chat over" not in ctx,
              repr(ctx[:400]))
    finally:
        shutil.rmtree(home, ignore_errors=True)
```

- [ ] **Step 2: Run it - expect FAIL** on "the first chat gets the tour".

- [ ] **Step 3: Add after `tour_text` in `guard.py`** (CRLF patch; anchor = the line `def away_notice(sid, st, ctx):`, insert BEFORE it):

```python
def tour_pending_text(sid):
    """SessionStart: the tour once after a fresh install, or "". The flag is spent BEFORE the
    text is read, so a broken tour.md cannot nag every chat - and a flag that cannot be
    removed shows nothing, for the same reason."""
    p = os.path.join(STATE, TOUR_FLAG)
    if not os.path.exists(p):
        return ""
    try:
        os.remove(p)
    except Exception as e:
        log("tour: could not spend the flag (%s) - not shown" % e)
        return ""
    log("tour: shown once after install")
    return tour_text(sid)


```

- [ ] **Step 4: Deliver it from `cmd_bootstrap`** (CRLF patch). Replace the exact line

```python
    text = "\n\n".join(t for t in (swept, _bootstrap_list(d)) if t)
```

with

```python
    text = "\n\n".join(t for t in (swept, _bootstrap_list(d),
                                   tour_pending_text(d.get("session_id"))) if t)
```

- [ ] **Step 5: Run the test - expect all PASS.** Also run any one existing bootstrap test (grep `def test_.*bootstrap`) to confirm it still passes.

- [ ] **Step 6: Commit**

```bash
git -C "D:/Claude/context-guard" add guard.py test_guard.py
git -C "D:/Claude/context-guard" commit -m "The first chat after a fresh install shows the tour once" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: the installer sets the flag, on a fresh install only

**Files:**
- Modify: `install.py` (helpers after `build`, ~line 101; `main`, ~lines 122-165)
- Test: `test_guard.py` (new test after Task 2's; register after it)

- [ ] **Step 1: Write the failing test**

```python
def test_a_fresh_install_leaves_the_tour_for_the_first_chat():
    """Only a FRESH install earns the tour - a re-install, an update over an older copy, a
    dry run and an uninstall never set it, so existing users are never surprised by it."""
    home = make_home({})
    try:
        seed_settings(home, FOREIGN)
        p = run_install(home)
        check("tour/install: a fresh install sets the flag", os.path.exists(tour_flag(home)),
              p.stdout[-400:])
        check("tour/install: and says the first chat will show you around",
              "show you around" in p.stdout and "context guard tour" in p.stdout, p.stdout[-400:])
        os.remove(tour_flag(home))
        run_install(home)
        check("tour/install: installing again does not set it",
              not os.path.exists(tour_flag(home)), "")
    finally:
        shutil.rmtree(home, ignore_errors=True)
    home = make_home({})
    try:
        seed_settings(home, {"hooks": {"Stop": [{"hooks": [
            {"type": "command", "command": '"python" "/old/place/guard.py" --ledger'}]}]}})
        p = run_install(home)
        check("tour/install: control - an install over an older copy did write",
              "Installed" in p.stdout, p.stdout[-400:])
        check("tour/install: but does not set the flag", not os.path.exists(tour_flag(home)), "")
    finally:
        shutil.rmtree(home, ignore_errors=True)
    home = make_home({})
    try:
        seed_settings(home, {})
        run_install(home, "--dry-run")
        check("tour/install: a dry run does not set it", not os.path.exists(tour_flag(home)), "")
        run_install(home)
        os.remove(tour_flag(home))
        run_install(home, "--uninstall")
        check("tour/install: an uninstall does not set it", not os.path.exists(tour_flag(home)), "")
    finally:
        shutil.rmtree(home, ignore_errors=True)
```

- [ ] **Step 2: Run it - expect FAIL** on "a fresh install sets the flag".

- [ ] **Step 3: Add helpers to `install.py`** after `build()` (before `def dumps`):

```python
TOUR_LINES = ("Open a new chat in Claude Code - it will show you around once.",
              'Type "context guard tour" in any chat to see it again.')


def is_fresh(current):
    """No Context Guard hook in these settings yet - the only install that earns the tour."""
    hooks = current.get("hooks")
    if not isinstance(hooks, dict):
        return True
    return not any(is_ours(e) for entries in hooks.values() if isinstance(entries, list)
                   for e in entries if isinstance(e, dict))


def flag_tour(home=None):
    """Leave the flag guard.py's SessionStart spends on the first chat. False on failure."""
    d = os.path.join(home or os.path.expanduser("~"), ".claude", "context-guard")
    try:
        if not os.path.isdir(d):
            os.makedirs(d)
        open(os.path.join(d, "tour-pending"), "w").close()
        return True
    except OSError as e:
        print("note: could not set up the first-chat tour (%s)" % e)
        return False
```

- [ ] **Step 4: Use them in `main()`.** After `wanted = build(current, remove=a.uninstall)` add:

```python
    fresh = not a.uninstall and is_fresh(current)
```

and replace the final block

```python
    if not a.uninstall:
        print("")
        print("One optional extra: set \"autoCompactWindow\" in that file to your context")
        print("window (e.g. 350000). Context Guard derives its ceiling from it, and without")
        print("it the ceiling falls back to a flat 300k.")
        print("Restart Claude Code, or open a new chat, for the hooks to load.")
    return 0
```

with

```python
    if not a.uninstall:
        print("")
        print("One optional extra: set \"autoCompactWindow\" in that file to your context")
        print("window (e.g. 350000). Context Guard derives its ceiling from it, and without")
        print("it the ceiling falls back to a flat 300k.")
        print("Restart Claude Code, or open a new chat, for the hooks to load.")
        if fresh and flag_tour(a.home):
            print("")
            for line in TOUR_LINES:
                print(line)
    return 0
```

(`--dry-run` returns earlier and `--update` returns at the top, so neither reaches this.)

- [ ] **Step 5: Run the test - expect all PASS**, then `test_install_is_idempotent` and `test_install_keeps_other_peoples_settings_and_hooks`.

- [ ] **Step 6: Commit**

```bash
git -C "D:/Claude/context-guard" add install.py test_guard.py
git -C "D:/Claude/context-guard" commit -m "A fresh install leaves the tour for the first chat" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: README, the private-names check, the spec correction

**Files:**
- Modify: `README.md` (end of "## Install", right after the line `Then restart Claude Code. Python 3.8+, nothing to \`pip install\`.`)
- Modify: `docs/superpowers/specs/2026-10-01-first-run-tour-design.md` (the `audit.py --alert` component)
- Test: `test_guard.py` (new test after Task 3's; register after it)

- [ ] **Step 1: Write the test**

```python
def test_the_tour_names_no_private_project():
    """His project names are private. Compared against his own list when this machine has
    one; the failure prints a count, never the names."""
    names_file = os.path.join(os.path.expanduser("~"), ".claude", "context-guard",
                              "private-names.txt")
    with open(os.path.join(HERE_DIR, "tour.md"), encoding="utf-8") as f:
        tour = f.read().lower()
    if not os.path.exists(names_file):
        check("tour/names: no private-names list on this machine - nothing to compare", True)
        return
    with open(names_file, encoding="utf-8") as f:
        names = [l.strip().lower() for l in f
                 if l.strip() and not l.strip().startswith("#")]
    hits = [n for n in names if n in tour]
    check("tour/names: tour.md carries none of the private names", not hits,
          "%d hit(s)" % len(hits))
```

Confirm first that `os.path.expanduser("~")` inside the test PROCESS is the real home (the throwaway home is only set in `child_env` for subprocesses); if it is not, report it instead of working around it.

- [ ] **Step 2: Run it - expect PASS** (tour.md is generic).

- [ ] **Step 3: README** - insert after the "Then restart Claude Code..." line:

```markdown

Your first chat after installing opens with a short tour - six things Context Guard does,
each one tryable on the spot. Type `context guard tour` in any chat to see it again.
```

- [ ] **Step 4: Spec correction** - in the spec's Components list, replace the `audit.py --alert` bullet's first words "**`audit.py --alert`** (SessionStart, already delivers the daily update notice)" with "**`guard.py --bootstrap`** (SessionStart; it reads stdin, so it knows the session id the pause demo needs)", and in Testing item 2 replace `audit.py --alert` with `guard.py --bootstrap`.

- [ ] **Step 5: Full suite** - expect `rc=0`, PASS = baseline + the new checks, FAIL=0.

- [ ] **Step 6: Commit**

```bash
git -C "D:/Claude/context-guard" add README.md docs/superpowers/specs/2026-10-01-first-run-tour-design.md test_guard.py
git -C "D:/Claude/context-guard" commit -m "README and spec for the first-run tour" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
