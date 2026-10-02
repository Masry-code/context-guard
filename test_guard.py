"""Tests for guard.py's handoff pickup.

Run:  python D:/Claude/context-guard/test_guard.py

Every test runs against a THROWAWAY home directory (USERPROFILE is overridden for the
subprocess, and guard.py derives HOME/PROJECTS/handoff dir from it at import). Nothing
here can touch the real notes in ~/.claude/handoff - which matters, because a test that
reaches pending_handoff() RENAMES the notes it delivers.

Why this file exists
--------------------
11 Sep 2026: the handoff mechanism was dead on the first message of a brand-new chat.
Claude Code writes the transcript AFTER the UserPromptSubmit hook fires, so the file the
hook was handed did not exist yet, find_transcript() returned None and cmd_size() gave up
silently. The feature only ever worked in chats that did not need it. Every earlier test
had driven the path where the transcript was already on disk - the same one-path blind
spot that shipped a broken headphone button in Harbor with 18 passing tests.
"""

import atexit
import datetime
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time

GUARD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "guard.py")
KEY = "D--Claude"
CWD = "D:/Claude"


def guard_constant(name):
    """A constant read out of guard.py's SOURCE, so a test can size its fixture against
    the budget that actually ships instead of hard-coding today's number.

    Deliberately not an import: importing guard binds HOME at import time and this suite
    must never be able to reach the real one. Measured 19 Sep 2026 - raising
    LEDGER_OWN_CHARS from 10,000 to 12,000 broke exactly one check, and it was a fixture
    tuned to the old value, not a behaviour anyone had decided on."""
    with open(GUARD, encoding="utf-8") as f:
        for line in f:
            m = re.match(r"\s*" + name + r"\s*=\s*([0-9_.]+)", line)
            if m:
                raw = m.group(1).replace("_", "")
                return float(raw) if "." in raw else int(raw)
    raise AssertionError("guard.py has no constant called " + name)

NOTE_CTX = "HANDOFF LABEL: context guard\n\n# Handoff - the guard\n\nbody-CONTEXTGUARD\n"
NOTE_SPOT = "HANDOFF LABEL: Harbor\n\n# Handoff - Harbor\n\nbody-HARBOR\n"
NOTE_NOVA = "HANDOFF LABEL: Nova Calc\n\n# Handoff - Nova\n\nbody-NOVA\n"
NOTE_QIX = "HANDOFF LABEL: QIX bot\n\n# Handoff - QIX\n\nbody-QIXBOT\n"

FAILED = []

# Every throwaway dir this run makes, so that none of them outlives it. Measured 30 Sep
# 2026: TEMP held ~260 of this suite's folders and the C: drive was full. Two holes let
# them out. shutil.rmtree(ignore_errors=True) silently gives up on git's read-only object
# files, so every updtest- dir survived its own `finally` - 21 of them per clean run. And
# a dir made before its test's `try`, or made inside it but removed only on the happy
# path, was left behind whenever a test raised.
MADE = []


def throwaway_dir(prefix):
    """tempfile.mkdtemp, remembered so remove_throwaways() can take it back at exit."""
    d = tempfile.mkdtemp(prefix=prefix)
    MADE.append(d)
    return d


def rm_tree(path):
    """shutil.rmtree that also removes read-only files, and never raises."""
    def unlock(func, p, _exc):
        try:
            os.chmod(p, stat.S_IREAD | stat.S_IWRITE)
            func(p)
        except OSError:
            pass
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=unlock)
    else:
        shutil.rmtree(path, onerror=unlock)


@atexit.register
def remove_throwaways():
    """Runs on a normal exit, a sys.exit(1) and an exception that escapes a test alike."""
    for d in MADE:
        rm_tree(d)


def make_home(notes, transcript_ctx=None):
    """A fake ~ with a projects dir, optional transcript, and the given waiting notes."""
    home = throwaway_dir("guardtest-")
    proj = os.path.join(home, ".claude", "projects", KEY)
    hand = os.path.join(home, ".claude", "handoff")
    os.makedirs(proj)
    os.makedirs(hand)
    for name, body in notes.items():
        with open(os.path.join(hand, name), "w", encoding="utf-8") as f:
            f.write(body)
    return home


def write_transcript(home, sid, ctx):
    """A transcript that reports `ctx` tokens of live context."""
    p = os.path.join(home, ".claude", "projects", KEY, sid + ".jsonl")
    rec = {"message": {"usage": {"cache_read_input_tokens": ctx,
                                 "cache_creation_input_tokens": 0}}}
    with open(p, "w", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    return p


def child_env(home):
    """The environment every hook subprocess runs under, pointed at the THROWAWAY home.

    This is the single most dangerous function in the file, which is why it is one function
    and not eight copies. guard.py derives everything from os.path.expanduser("~"), and the
    two platforms disagree about where that comes from:

      * Windows - expanduser prefers USERPROFILE. Git Bash also exports HOME, pointing at
        the MSYS home, so HOME has to be removed or it wins and the test writes somewhere
        real.
      * macOS / Linux - expanduser reads HOME and, when HOME is MISSING, falls back to the
        passwd database. Removing HOME there does not isolate the test, it does the exact
        opposite: every run would hoover up the notes in the user's real ~/.claude/handoff
        and rename them, because pending_handoff() renames what it delivers.

    So: unset HOME on Windows, SET it everywhere else. Until 18 Sep 2026 this file did the
    Windows thing on every platform, which made the suite unsafe to run on the machine of
    anyone this is shared with."""
    env = dict(os.environ)
    env["USERPROFILE"] = home
    if os.name == "nt":
        env.pop("HOME", None)
    else:
        env["HOME"] = home
    return env


def run(home, sid, prompt, transcript_exists=False):
    """Fire the UserPromptSubmit hook exactly as Claude Code does."""
    payload = {
        "session_id": sid,
        "transcript_path": os.path.join(home, ".claude", "projects", KEY, sid + ".jsonl"),
        "cwd": CWD,
        "prompt": prompt,
        "hook_event_name": "UserPromptSubmit",
    }
    env = child_env(home)
    p = subprocess.run([sys.executable, GUARD, "--size"],
                       input=json.dumps(payload), capture_output=True, text=True, env=env)
    return p


def context_of(proc):
    """The additionalContext guard.py handed back, or "" if it said nothing."""
    out = (proc.stdout or "").strip()
    if not out:
        return ""
    try:
        return json.loads(out).get("hookSpecificOutput", {}).get("additionalContext", "") or ""
    except Exception:
        return ""


def whole_context(home, proc):
    """context_of() with the overflow put back: the head WITHOUT its pointer line, then the
    overflow file the pointer names. For tests that read the end of a long message, which
    fit_hook_text() moves to a file once it passes HOOK_TEXT_MAX."""
    ctx = context_of(proc)
    if FIT_MARK not in ctx:
        return ctx
    folder = os.path.join(home, ".claude", "handoff", "overflow")
    names = sorted(os.listdir(folder)) if os.path.isdir(folder) else []
    if not names:
        return ctx
    with open(os.path.join(folder, names[-1]), "r", encoding="utf-8", newline="") as fh:
        return ctx[:ctx.rindex(FIT_MARK)] + fh.read()


def waiting(home):
    hand = os.path.join(home, ".claude", "handoff")
    return sorted(f for f in os.listdir(hand)
                  if f.endswith(".md") and ".used" not in f and not f.endswith(".requests.md"))


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "  <- " + detail))
    if not cond:
        FAILED.append(name)


def expect_clean(proc, name):
    check(name + ": hook exited 0", proc.returncode == 0, "rc=%d" % proc.returncode)
    check(name + ": no traceback", "Traceback" not in (proc.stderr or ""),
          (proc.stderr or "")[:200])


# ---------------------------------------------------------------- the reported bug
def test_new_chat_label_match():
    """First message of a BRAND-NEW chat - no transcript on disk yet - naming one thread."""
    home = make_home({KEY + ".9e19c7ab.md": NOTE_CTX,
                      KEY + ".143f0320.md": NOTE_SPOT,
                      KEY + ".a7734c71.md": NOTE_NOVA})
    try:
        p = run(home, "cf1f81f6-new", "context guard")
        ctx = context_of(p)
        expect_clean(p, "new-chat/label")
        check("new-chat/label: delivered a note", "body-CONTEXTGUARD" in ctx, repr(ctx[:160]))
        check("new-chat/label: delivered ONLY that note",
              "body-HARBOR" not in ctx and "body-NOVA" not in ctx)
        check("new-chat/label: consumed exactly one", len(waiting(home)) == 2, str(waiting(home)))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_new_chat_no_match_offers_menu():
    """No label named -> a menu, and NOTHING consumed."""
    home = make_home({KEY + ".9e19c7ab.md": NOTE_CTX,
                      KEY + ".143f0320.md": NOTE_SPOT,
                      KEY + ".a7734c71.md": NOTE_NOVA})
    try:
        p = run(home, "cf1f81f6-menu", "can you look at the weather")
        ctx = context_of(p)
        expect_clean(p, "new-chat/menu")
        check("new-chat/menu: offered the menu", "SAVED THREADS ARE WAITING" in ctx, repr(ctx[:160]))
        check("new-chat/menu: named the labels",
              "context guard" in ctx and "Harbor" in ctx and "Nova Calc" in ctx)
        check("new-chat/menu: consumed nothing", len(waiting(home)) == 3, str(waiting(home)))
        check("new-chat/menu: leaked no note body", "body-HARBOR" not in ctx)
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_menu_then_the_label_still_works():
    """The turn the old test never drove: he reads the menu, then types the label.

    Measured 11 Sep 2026 - this returned NOTHING. cmd_size set took_handoff=True on the
    menu, which consumes no note at all, so the chat's one-note-ever slot was already
    spent by the time he answered. His work sat on disk and no chat could ever claim it.
    The menu now sets menu_shown instead; only a real pickup burns the slot."""
    home = make_home({KEY + ".9e19c7ab.md": NOTE_CTX,
                      KEY + ".143f0320.md": NOTE_SPOT,
                      KEY + ".a7734c71.md": NOTE_NOVA})
    try:
        p1 = run(home, "cf1f81f6-answer", "hi")
        check("menu-then-label: turn 1 was the menu",
              "SAVED THREADS ARE WAITING" in context_of(p1), repr(context_of(p1)[:160]))

        p2 = run(home, "cf1f81f6-answer", "still nothing that matches")
        check("menu-then-label: menu is not repeated every turn",
              "SAVED THREADS ARE WAITING" not in context_of(p2), repr(context_of(p2)[:160]))

        p3 = run(home, "cf1f81f6-answer", "Nova Calc")
        ctx = context_of(p3)
        expect_clean(p3, "menu-then-label")
        check("menu-then-label: the label delivered its note", "body-NOVA" in ctx,
              repr(ctx[:200]))
        check("menu-then-label: delivered ONLY that note",
              "body-HARBOR" not in ctx and "body-CONTEXTGUARD" not in ctx)
        check("menu-then-label: left the other two waiting", len(waiting(home)) == 2,
              str(waiting(home)))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_new_chat_single_note_needs_no_label():
    """One note waiting - it goes, whatever he typed."""
    home = make_home({KEY + ".9e19c7ab.md": NOTE_CTX})
    try:
        p = run(home, "cf1f81f6-solo", "hi")
        ctx = context_of(p)
        expect_clean(p, "new-chat/solo")
        check("new-chat/solo: delivered the only note", "body-CONTEXTGUARD" in ctx, repr(ctx[:160]))
        check("new-chat/solo: consumed it", len(waiting(home)) == 0, str(waiting(home)))
    finally:
        shutil.rmtree(home, ignore_errors=True)


# ---------------------------------------------------------------- regression guards
def test_expensive_chat_does_not_eat_the_note():
    """A big live chat must LEAVE the note for a fresh one. This is the rule that stopped
    the guard's own chat eating the Harbor note one turn after being compacted."""
    home = make_home({KEY + ".9e19c7ab.md": NOTE_CTX})
    try:
        write_transcript(home, "bigchat", 200_000)
        p = run(home, "bigchat", "context guard", transcript_exists=True)
        expect_clean(p, "big-chat")
        check("big-chat: did NOT deliver the note", "body-CONTEXTGUARD" not in context_of(p))
        check("big-chat: left it waiting", len(waiting(home)) == 1, str(waiting(home)))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_new_chat_with_no_notes_is_quiet():
    """Nothing waiting -> say nothing, cleanly."""
    home = make_home({})
    try:
        p = run(home, "cf1f81f6-quiet", "hello")
        expect_clean(p, "no-notes")
        check("no-notes: stayed silent", context_of(p) == "", repr(context_of(p)[:160]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_second_note_only_when_he_names_it():
    """Was test_second_note_only_when_he_names_it - and it contradicted the code it was testing.

    guard.py was changed at 20:56 on 11 Sep, one minute AFTER this test was written, to let
    a chat that has already taken one note take a second one IF the user names it by label.
    The comment at the gate in cmd_size gives the reason: refusing "Nova Calc" in a chat
    that had loaded the guard note is a silent drop, the exact failure this whole mechanism
    exists to stop. That session ended before the test caught up, so the suite has been red
    ever since. The rule it pins now: never silently, only by name, and never once the chat
    has stopped being cheap."""
    home = make_home({KEY + ".9e19c7ab.md": NOTE_CTX,
                      KEY + ".143f0320.md": NOTE_SPOT,
                      KEY + ".a7734c71.md": NOTE_NOVA})
    try:
        p1 = run(home, "cf1f81f6-twice", "context guard")
        check("twice: turn 1 took the thread he named",
              "body-CONTEXTGUARD" in context_of(p1), repr(context_of(p1)[:160]))

        p2 = run(home, "cf1f81f6-twice", "right, carry on with that")
        ctx2 = context_of(p2)
        expect_clean(p2, "twice")
        check("twice: an unrelated turn takes nothing more", "body-" not in ctx2,
              repr(ctx2[:160]))
        check("twice: and does not re-offer the menu",
              "SAVED THREADS ARE WAITING" not in ctx2, repr(ctx2[:160]))
        check("twice: both other notes left waiting", len(waiting(home)) == 2,
              str(waiting(home)))

        p3 = run(home, "cf1f81f6-twice", "Harbor")
        ctx3 = context_of(p3)
        expect_clean(p3, "twice")
        check("twice: naming a second thread by label still works",
              "body-HARBOR" in ctx3, repr(ctx3[:160]))
        check("twice: and brings only that one", "body-NOVA" not in ctx3)
        check("twice: leaving the third waiting", len(waiting(home)) == 1, str(waiting(home)))

        write_transcript(home, "cf1f81f6-twice", 200_000)   # the chat is no longer cheap
        p4 = run(home, "cf1f81f6-twice", "Nova Calc")
        expect_clean(p4, "twice")
        check("twice: an expensive chat takes nothing, even by name",
              "body-NOVA" not in context_of(p4), repr(context_of(p4)[:160]))
        check("twice: that note is still there for a fresh chat", len(waiting(home)) == 1,
              str(waiting(home)))
    finally:
        shutil.rmtree(home, ignore_errors=True)

def test_short_label_is_still_summonable():
    """A label whose every word is under 4 characters - "QIX bot".

    Measured 17 Sep 2026 against his real handoff dir: _tokens() drops words shorter than
    4 chars so that a stray word cannot summon a note - but applied to the LABEL that left
    "QIX bot" as the EMPTY set, and `_tokens(label) & words` can then never match anything.
    He types back the exact name the menu just offered him and the hook prints NOTHING: a
    14k note unreachable from every chat that had already seen the menu."""
    home = make_home({KEY + ".7e5cfde2.md": NOTE_QIX,
                      KEY + ".143f0320.md": NOTE_SPOT})
    try:
        p1 = run(home, "cf1f81f6-qix", "hi")
        check("short-label: turn 1 was the menu",
              "SAVED THREADS ARE WAITING" in context_of(p1), repr(context_of(p1)[:160]))

        p2 = run(home, "cf1f81f6-qix", "QIX bot")
        ctx = context_of(p2)
        expect_clean(p2, "short-label")
        check("short-label: the name he was offered delivered its note",
              "body-QIXBOT" in ctx, repr(ctx[:200]))
        check("short-label: left the other one waiting", len(waiting(home)) == 1,
              str(waiting(home)))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_short_label_does_not_answer_to_a_stray_word():
    """The other half of that fix: "QIX bot" falls back to needing the WHOLE label named,
    so ordinary talk containing one of its words must not drag 14k of note into the chat.
    This one passed before the fix too - nothing matched that label at all then. It is here
    to stop the fallback ever being loosened from all-words to any-word."""
    home = make_home({KEY + ".7e5cfde2.md": NOTE_QIX,
                      KEY + ".143f0320.md": NOTE_SPOT})
    try:
        run(home, "cf1f81f6-stray", "hi")
        p2 = run(home, "cf1f81f6-stray", "reboot the bot on the server")
        expect_clean(p2, "stray-word")
        check("stray-word: one word of the label summons nothing",
              "body-QIXBOT" not in context_of(p2), repr(context_of(p2)[:160]))
        check("stray-word: both notes still waiting", len(waiting(home)) == 2,
              str(waiting(home)))
    finally:
        shutil.rmtree(home, ignore_errors=True)


# ------------------------------------------------- change 1: memories at handoff time
def test_handoff_instruction_demands_memory_consolidation():
    """His ask, 17 Sep 2026: "after finishing the handoff you need to create memories of the
    most important things that happend in this chat".

    The instruction already said "write any DURABLE lesson to a memory file", and that is not
    enough. Measured the same day: MEMORY.md is 14,996 bytes / ~3,749 tokens and is loaded on
    EVERY turn of EVERY chat, with 69 files and 377,355 bytes behind it. An auto-memory step
    that only ever ADDS grows the exact floor this tool exists to lower - so the step has to
    name the folder, name the index, and require merging into an existing file before making
    a new one."""
    home = make_home({})
    try:
        write_transcript(home, "bigchat", 240_000)
        p = run(home, "bigchat", "carry on")
        ctx = context_of(p)
        expect_clean(p, "memory-step")
        check("memory-step: the handoff warning fired",
              "THIS CHAT IS NOW" in ctx, repr(ctx[:200]))
        want = os.path.join("projects", KEY, "memory").replace(chr(92), "/")
        check("memory-step: points at this project's own memory folder",
              want in ctx.replace(chr(92), "/"), repr(ctx[:1200]))
        check("memory-step: names the MEMORY.md index", "MEMORY.md" in ctx, repr(ctx[:1200]))
        check("memory-step: requires consolidating, not just adding",
              "consolidat" in ctx.lower(), repr(ctx[:1200]))
        check("memory-step: says check for an existing file BEFORE creating one",
              "before creating" in ctx.lower(), repr(ctx[:1200]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def run_stop(home, sid, active=False, send_sid=True):
    """Fire the Stop hook exactly as Claude Code does."""
    payload = {
        "session_id": sid if send_sid else None,
        "transcript_path": os.path.join(home, ".claude", "projects", KEY, sid + ".jsonl"),
        "cwd": CWD,
        "hook_event_name": "Stop",
        "stop_hook_active": active,
    }
    env = child_env(home)
    return subprocess.run([sys.executable, GUARD, "--ledger"],
                          input=json.dumps(payload), capture_output=True, text=True, env=env)


def blocked(proc):
    """The reason the Stop hook refused to let the chat close, or "" if it let it go."""
    out = (proc.stdout or "").strip()
    if not out:
        return ""
    try:
        d = json.loads(out)
    except Exception:
        return ""
    return d.get("reason", "") if d.get("decision") == "block" else ""


def write_chat(home, sid, started, msg="do the thing"):
    """A transcript whose FIRST record carries the session's start time, the way a real one
    does. The nag dates the session from that, never from ctime - see session_start_ts."""
    p = os.path.join(home, ".claude", "projects", KEY, sid + ".jsonl")
    rec = {"type": "user",
           "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
           "message": {"role": "user", "content": msg}}
    with open(p, "w", encoding="utf-8") as f:
        f.write(json.dumps(rec) + chr(10))
    return p


def write_note(home, sid, body=None):
    """The handoff note THIS chat wrote - <KEY>.<sid8>.md, same as handoff_path()."""
    if body is None:
        # a well-formed head, so the head check (note_head_block) stays out of every test
        # that is about something else; NOTE_CTX itself is a freehand note with no number
        body = ("HANDOFF LABEL: context guard -3 (1 Oct)" + chr(10)
                + "WRITTEN BY: context guard -2 (30 Sep)" + chr(10)
                + "NEXT CHAT EFFORT: high - testing" + chr(10)
                + NOTE_CTX.split(chr(10), 1)[1])
    p = os.path.join(home, ".claude", "handoff", KEY + "." + sid[:8] + ".md")
    with open(p, "w", encoding="utf-8") as f:
        f.write(body)
    return p


def write_memory(home, name, mtime):
    d = os.path.join(home, ".claude", "projects", KEY, "memory")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write("# " + name + chr(10))
    os.utime(p, (mtime, mtime))
    os.utime(d, (mtime, mtime))     # else creating the file marks the FOLDER as touched
    return p


# --------------------------------------- change 2: memory is enforced, not asked for
def test_stop_nags_when_the_note_is_written_but_no_memory_was_saved():
    """His ask, 17 Sep 2026: "after finishing the handoff you need to create memories of the
    most important things that happend in this chat". Asking for it in the handoff text is
    not enough - the text can be read and skipped. This is the check that cannot be."""
    home = make_home({})
    try:
        started = time.time() - 3600
        write_chat(home, "handedoff", started)
        write_note(home, "handedoff")
        write_memory(home, "old-lesson.md", started - 86400)    # yesterday, not this chat
        p = run_stop(home, "handedoff")
        expect_clean(p, "mem-nag")
        why = blocked(p)
        check("mem-nag: refused to let the chat close", why != "", repr((p.stdout or "")[:200]))
        check("mem-nag: points at this project's memory folder",
              os.path.join("projects", KEY, "memory").replace(chr(92), "/")
              in why.replace(chr(92), "/"), repr(why[:300]))
        check("mem-nag: carries the consolidate-first rule", "consolidat" in why.lower(),
              repr(why[:300]))
        check("mem-nag: offers the override", "no-memory-nag" in why, repr(why[-200:]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_stop_is_silent_once_a_memory_was_really_written():
    """The check reads FILE TIMESTAMPS, so it cannot be satisfied by claiming it was done -
    and, just as important, it must go quiet the moment it really has been."""
    home = make_home({})
    try:
        started = time.time() - 3600
        write_chat(home, "handedoff", started)
        write_note(home, "handedoff")
        write_memory(home, "fresh-lesson.md", started + 60)     # written DURING this chat
        # ...and the transcript must show WHO wrote it. Since 19 Sep 2026 an mtime alone
        # is not enough: the folder is shared, so it proves only that somebody saved.
        append_tool_use(home, "handedoff", "Write", {"file_path": os.path.join(
            home, ".claude", "projects", KEY, "memory", "fresh-lesson.md")})
        p = run_stop(home, "handedoff")
        expect_clean(p, "mem-saved")
        check("mem-saved: let the chat close", blocked(p) == "", repr(blocked(p)[:200]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_stop_does_not_nag_a_chat_that_never_handed_off():
    """Most chats never hand off. The nag is tied to the note, not to every Stop - otherwise
    it fires on every reply of every chat he has, which is how a good check gets switched off."""
    home = make_home({})
    try:
        write_chat(home, "ordinary", time.time() - 3600)
        p = run_stop(home, "ordinary")
        expect_clean(p, "no-note")
        check("no-note: stayed silent", blocked(p) == "", repr(blocked(p)[:200]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_stop_never_blocks_twice_in_a_row():
    """stop_hook_active means Claude Code is ALREADY continuing because this hook blocked.
    Blocking again is an infinite loop that no user can interrupt."""
    home = make_home({})
    try:
        write_chat(home, "handedoff", time.time() - 3600)
        write_note(home, "handedoff")
        p = run_stop(home, "handedoff", active=True)
        expect_clean(p, "no-loop")
        check("no-loop: did not block a second time", blocked(p) == "", repr(blocked(p)[:200]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_memory_nag_gives_up_after_two_tries():
    """stop_hook_active is the first line of defence against a loop, but it is a flag THIS
    build happens to send - the design cannot rest on it. A chat that decides there is
    genuinely nothing durable to save never changes a memory mtime, so without a cap the
    hook would block its own Stop forever and burn his tokens doing it."""
    home = make_home({})
    try:
        write_chat(home, "handedoff", time.time() - 3600)
        write_note(home, "handedoff")
        first = blocked(run_stop(home, "handedoff"))
        second = blocked(run_stop(home, "handedoff"))
        third = run_stop(home, "handedoff")
        expect_clean(third, "nag-cap")
        check("nag-cap: nagged the first time", first != "", repr(first[:120]))
        check("nag-cap: nagged the second time", second != "", repr(second[:120]))
        check("nag-cap: gave up on the third", blocked(third) == "", repr(blocked(third)[:200]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_nag_refuses_to_run_without_a_session_id():
    """handoff_path() with no sid falls back to the LEGACY flat <KEY>.md name, which
    waiting_notes() still reads for notes written before per-chat names existed. If one of
    those ever sits in the folder again, a Stop payload with no session_id would make EVERY
    chat in the folder believe it had handed off, and nag on every reply. There is no flat
    note today - checked 17 Sep 2026 - which is exactly when to close the door."""
    home = make_home({})
    try:
        write_chat(home, "nosid", time.time() - 3600)
        with open(os.path.join(home, ".claude", "handoff", KEY + ".md"),
                  "w", encoding="utf-8") as f:
            f.write(NOTE_CTX)
        p = run_stop(home, "nosid", send_sid=False)
        expect_clean(p, "nag-nosid")
        check("nag-nosid: said nothing without a session id",
              blocked(p) == "", repr(blocked(p)[:200]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_memory_nag_has_an_off_switch():
    """His standing rule: anything automated leaves him an override. He asked for this switch
    by name when he approved the nag on 17 Sep 2026."""
    home = make_home({})
    try:
        write_chat(home, "handedoff", time.time() - 3600)
        write_note(home, "handedoff")
        off = os.path.join(home, ".claude", "context-guard")
        os.makedirs(off, exist_ok=True)
        open(os.path.join(off, "no-memory-nag"), "w").close()
        p = run_stop(home, "handedoff")
        expect_clean(p, "nag-off")
        check("nag-off: the switch silences it", blocked(p) == "", repr(blocked(p)[:200]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


GOOD_HEAD = ("HANDOFF LABEL: My App -3 (1 Oct)\nWRITTEN BY: My App -2 (30 Sep)\n"
             "NEXT CHAT EFFORT: high - building\n\n# Handoff\n\nbody\n")


def head_home(sid="headchat", body="HANDOFF LABEL: My App\n\n# Handoff\n\nbody\n",
              memory_nag_off=True):
    """A home with one chat that wrote a real note; the memory nag is switched off so only
    the head check can block."""
    home = make_home({})
    write_chat(home, sid, time.time() - 3600)
    write_note(home, sid, body)
    if memory_nag_off:
        off = os.path.join(home, ".claude", "context-guard")
        os.makedirs(off, exist_ok=True)
        open(os.path.join(off, "no-memory-nag"), "w").close()
    return home


def test_a_note_without_its_head_lines_is_sent_back_once():
    """1 Oct 2026: a chat that handed over before the size warning wrote `HANDOFF LABEL:
    My App` and nothing else, so the new chat could not number itself or find the old one."""
    home = head_home()
    try:
        p = run_stop(home, "headchat")
        expect_clean(p, "head-bad")
        why = blocked(p)
        check("head-bad: blocked", why != "", repr((p.stdout or "")[:200]))
        check("head-bad: names the note", "headchat"[:8] in why, repr(why[:300]))
        check("head-bad: names the missing number", "running number" in why, repr(why[:600]))
        check("head-bad: names the missing writer", "WRITTEN BY" in why, repr(why[:600]))
        check("head-bad: names the missing effort", "NEXT CHAT EFFORT" in why, repr(why[:600]))
        today = datetime.datetime.now().strftime("%d %b").lstrip("0")
        check("head-bad: gives today's date", today in why, repr(why[:800]))
        check("head-bad: ends with the corrected label as the last line",
              "last line" in why, repr(why[-300:]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_a_note_with_all_three_head_lines_is_not_blocked():
    home = head_home(body=GOOD_HEAD)
    try:
        p = run_stop(home, "headchat")
        expect_clean(p, "head-good")
        check("head-good: no block", blocked(p) == "", repr(blocked(p)[:300]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_head_check_blocks_once_per_version_of_the_note():
    home = head_home()
    try:
        first = blocked(run_stop(home, "headchat"))
        second = run_stop(home, "headchat")
        expect_clean(second, "head-once")
        check("head-once: first run blocked", first != "", repr(first[:120]))
        check("head-once: unchanged note not blocked again", blocked(second) == "",
              repr(blocked(second)[:200]))
        p = write_note(home, "headchat", "HANDOFF LABEL: My App -3 (1 Oct)\n\n# Handoff\n")
        t = time.time() + 30
        os.utime(p, (t, t))
        third = run_stop(home, "headchat")
        expect_clean(third, "head-again")
        why = blocked(third)
        check("head-again: a rewritten note still missing lines blocks again", why != "",
              repr((third.stdout or "")[:200]))
        check("head-again: the number is no longer reported missing",
              "running number" not in why.split(". Put")[0], repr(why[:400]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_head_check_never_blocks_twice_in_a_row():
    home = head_home()
    try:
        p = run_stop(home, "headchat", active=True)
        expect_clean(p, "head-active")
        check("head-active: no block", blocked(p) == "", repr(blocked(p)[:200]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_a_bad_head_and_a_missing_memory_save_come_in_one_block():
    """The Stop after a head block has stop_hook_active set, so the memory nag would never
    get its turn; both must ride in the one block."""
    home = head_home(memory_nag_off=False)
    try:
        p = run_stop(home, "headchat")
        expect_clean(p, "head-both")
        why = blocked(p)
        check("head-both: one json object", (p.stdout or "").strip().count(chr(10)) == 0,
              repr((p.stdout or "")[:200]))
        check("head-both: carries the head text", "HEAD IS INCOMPLETE" in why, repr(why[:200]))
        check("head-both: carries the memory nag", "NOTHING WAS SAVED TO MEMORY" in why,
              repr(why[-400:]))
        check("head-both: and still puts the label last, after the memory line",
              "ORDER OF THE REPLY" in why and "still the very last line" in why,
              repr(why[-400:]))
        check("head-both: the memory nag was counted",
              json.load(open(os.path.join(home, ".claude", "context-guard",
                                          "headchat.json")))["memory_nags"] == 1,
              "state")
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_decorated_and_bom_heads_are_not_false_positives():
    rest = ("WRITTEN BY: My App -2 (30 Sep)\nNEXT CHAT EFFORT: high - building\n\nbody\n")
    heads = {
        "bom": "﻿HANDOFF LABEL: My App -3 (1 Oct)\n" + rest,
        "bold-key": "**HANDOFF LABEL:** My App -3 (1 Oct)\n" + rest,
        "heading": "# HANDOFF LABEL: My App -3 (1 Oct)\n" + rest,
        "backtick": "HANDOFF LABEL: `My App -3 (1 Oct)`\n" + rest,
        "bold-value": "HANDOFF LABEL: **My App -3 (1 Oct)**\n" + rest,
        "bom-writer-first": "﻿WRITTEN BY: My App -2 (30 Sep)\n"
                            "HANDOFF LABEL: My App -3 (1 Oct)\n"
                            "NEXT CHAT EFFORT: high - building\n\nbody\n",
        "bom-effort-first": "﻿NEXT CHAT EFFORT: high - building\n"
                            "HANDOFF LABEL: My App -3 (1 Oct)\n"
                            "WRITTEN BY: My App -2 (30 Sep)\n\nbody\n",
    }
    for name, body in heads.items():
        home = head_home(body=body)
        try:
            with open(os.path.join(home, ".claude", "handoff", KEY + ".headchat.md"),
                      "w", encoding="utf-8", newline="") as f:
                f.write(body)
            p = run_stop(home, "headchat")
            expect_clean(p, "head-fp-" + name)
            check("head-fp-" + name + ": not blocked", blocked(p) == "", repr(blocked(p)[:300]))
        finally:
            shutil.rmtree(home, ignore_errors=True)


def test_the_head_check_has_an_off_switch():
    home = head_home()
    try:
        open(os.path.join(home, ".claude", "context-guard", "no-note-head-check"), "w").close()
        p = run_stop(home, "headchat")
        expect_clean(p, "head-off")
        check("head-off: the switch silences it", blocked(p) == "", repr(blocked(p)[:200]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_head_block_names_its_off_switch():
    home = head_home()
    try:
        why = blocked(run_stop(home, "headchat"))
        check("head-off-named: reason names the switch", "no-note-head-check" in why,
              repr(why[-200:]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_head_check_does_not_swallow_the_memory_nag():
    """Control: with a good head and the memory nag ON, the nag still fires."""
    home = head_home(body=GOOD_HEAD, memory_nag_off=False)
    try:
        p = run_stop(home, "headchat")
        expect_clean(p, "head-control")
        check("head-control: memory nag still fires", "NOTHING WAS SAVED" in blocked(p),
              repr(blocked(p)[:200]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_ledger_is_still_written_when_the_nag_fires():
    """The nag is bolted onto the Stop hook that already records his words. It must not cost
    him that - a new feature that breaks the old one is a net loss."""
    home = make_home({})
    try:
        started = time.time() - 3600
        write_chat(home, "handedoff", started, msg="the exact words he typed")
        write_note(home, "handedoff")
        p = run_stop(home, "handedoff")
        expect_clean(p, "ledger-still")
        lp = os.path.join(home, ".claude", "handoff", KEY + ".requests.md")
        body = ""
        if os.path.exists(lp):
            with open(lp, encoding="utf-8") as f:
                body = f.read()
        check("ledger-still: his words were still recorded",
              "the exact words he typed" in body, repr(body[:200]))
        check("ledger-still: and the nag still fired", blocked(p) != "")
    finally:
        shutil.rmtree(home, ignore_errors=True)


def write_tooluse(home, sid, commands):
    """A transcript of assistant Bash calls - what the skills scan actually reads."""
    p = os.path.join(home, ".claude", "projects", KEY, sid + ".jsonl")
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps({"type": "user", "timestamp": "2026-09-17T10:00:00Z",
                            "message": {"role": "user", "content": "go"}}) + chr(10))
        for c in commands:
            f.write(json.dumps({"type": "assistant", "message": {"role": "assistant",
                    "content": [{"type": "tool_use", "name": "Bash",
                                 "input": {"command": c}}]}}) + chr(10))
    return p


def skill_shapes(home, key=KEY):
    """The shapes the skill-candidate scan puts forward for a project folder, straight from
    the engine (the by-hand --skills command is gone)."""
    p = guard_call(home, "print(guard.json.dumps(guard.skill_candidates(%r)[0]))"
                   % os.path.join(home, ".claude", "projects", key, "x.jsonl"))
    try:
        rows = json.loads(p.stdout.strip().splitlines()[-1])
    except Exception:
        return None, p
    return [r[3] for r in rows], p


def snapshot(home):
    """Every file under `home` with its size and mtime - to prove nothing was written."""
    out = {}
    for root, _d, files in os.walk(home):
        for n in files:
            p = os.path.join(root, n)
            try:
                out[p] = (os.path.getsize(p), os.path.getmtime(p))
            except Exception:
                pass
    return out


# ------------------------------------- change 3: propose skills, never write them
def test_skills_proposes_a_shape_repeated_across_chats():
    """His ask, 17 Sep 2026: "create skills if sth was done repeatedly and would make it
    better for the next chat". Across chats is the whole point - the same command being
    re-derived in a fresh chat is work the last chat already did."""
    home = make_home({})
    try:
        write_tooluse(home, "chatA", ["python D:/Claude/context-guard/test_guard.py",
                                      "python D:/Claude/context-guard/test_guard.py"])
        write_tooluse(home, "chatB", ['cd "D:/Claude" && python test_guard.py',
                                      "python test_guard.py"])
        got, p = skill_shapes(home)
        check("skills: the engine ran", got is not None, (p.stderr or p.stdout or "")[:300])
        check("skills: proposed the repeated shape", "python test_guard.py" in (got or []), str(got))
        p = guard_call(home, "print(guard.json.dumps(guard.skill_candidates(%r)[0]))"
                       % os.path.join(home, ".claude", "projects", KEY, "x.jsonl"))
        row = json.loads(p.stdout.strip().splitlines()[-1])[0]
        check("skills: counted it across both chats", row[2] == 2, str(row))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_skills_ignores_a_shape_repeated_inside_one_chat():
    """A repeat inside ONE chat is somebody iterating on a problem, not a workflow worth a
    skill. Ranking by runs x distinct chats is his rule, recorded 17 Sep 2026."""
    home = make_home({})
    try:
        write_tooluse(home, "chatA", ["git status"] * 6)
        got, p = skill_shapes(home)
        check("skills-noise: the engine ran", got is not None, (p.stderr or p.stdout or "")[:300])
        check("skills-noise: did not propose a one-chat repeat",
              "git status" not in (got or []), str(got))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_skills_command_is_gone_and_writes_nothing():
    """The by-hand --skills command was deleted (the handoff warning still carries the
    candidates). Asking for it now does nothing at all: exit 0, no output, nothing written.
    PROPOSE ONLY stays true - "propose-only for skills", 17 Sep 2026."""
    home = make_home({})
    try:
        write_tooluse(home, "chatA", ["python test_guard.py"] * 2)
        write_tooluse(home, "chatB", ["python test_guard.py"] * 2)
        before = snapshot(home)
        p = subprocess.run([sys.executable, GUARD, "--skills", KEY], capture_output=True,
                           text=True, env=child_env(home))
        after = snapshot(home)
        check("skills-gone: exits 0", p.returncode == 0, (p.stderr or "")[:300])
        check("skills-gone: prints nothing", not (p.stdout or "").strip(), repr(p.stdout[:300]))
        check("skills-gone: created nothing", sorted(after) == sorted(before),
              str(sorted(set(after) - set(before))[:5]))
        check("skills-gone: changed nothing", after == before,
              str([k for k in before if after.get(k) != before[k]][:5]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_handoff_offers_the_skill_candidates():
    """The candidates have to reach him where he will actually see them - in the handoff
    warning, at the moment the chat is ending and the next one is being set up."""
    home = make_home({})
    try:
        write_tooluse(home, "chatA", ["python test_guard.py"] * 2)
        write_tooluse(home, "chatB", ["python test_guard.py"] * 2)
        write_transcript(home, "bigchat", 240_000)
        p = run(home, "bigchat", "carry on")
        ctx = whole_context(home, p)
        expect_clean(p, "skills-handoff")
        check("skills-handoff: the warning carries the candidates",
              "SKILL CANDIDATES" in ctx, repr(ctx[:1200]))
        check("skills-handoff: names the shape", "python test_guard.py" in ctx, repr(ctx[:1200]))
        check("skills-handoff: forbids building them unasked",
              "DO NOT BUILD" in ctx, repr(ctx[:1200]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_handoff_stays_quiet_when_nothing_is_repeated():
    """No candidates means no section. A warning that always ends in an empty list teaches
    him to stop reading the end of the warning."""
    home = make_home({})
    try:
        write_transcript(home, "bigchat", 240_000)
        ctx = context_of(run(home, "bigchat", "carry on"))
        check("skills-quiet: the warning still fired", "THIS CHAT IS NOW" in ctx, repr(ctx[:160]))
        check("skills-quiet: but proposed nothing", "SKILL CANDIDATES" not in ctx,
              repr(ctx[-400:]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


# These are real commands, copied from his own transcripts on 17 Sep 2026. The first scan
# over 12 of them proposed "python" (122 runs), "grep" (23), "cat" (17), "ls" (10), "echo" (7)
# and "sam" (23) - and "sam" is not a command at all, it is the front half of
# C:/Users/Sam Rivera/... after shlex split the unquoted space in his own username.
REAL_NOISE = [
    'N="C:/Users/Sam Rivera/.claude/handoff/D--Claude.fda5cb73.md"; echo "lines: $(wc -l < "$N")"',
    'cd "D:/Claude/context-guard" && grep -n "^def \|^# ---" guard.py',
    'ls -la ~/.claude/handoff/ 2>/dev/null | head -50',
    'cat "C:/Users/Sam Rivera/.claude/projects/D--Claude/x/tool-results/'
    'hook-d8588838-163a-4397-97f7-ab17f891b6b6-1-additionalContext.txt"',
    'cd "D:/AI Projects/X" && python - <<PY',
    'cd "D:/Claude" && echo "=== state files ===" && ls -1',
]


def test_skills_does_not_propose_the_shapes_it_actually_found():
    """A counter is only as good as the event under it. Looking around a folder - cat, ls,
    grep, a bare interpreter, a shell variable assignment - is how every chat starts and is
    never a workflow worth a skill. If those reach him, the real candidates are buried and
    he stops reading the list."""
    home = make_home({})
    try:
        # x3: on his real transcripts these ran 122, 23, 17, 10 and 7 times. Two runs
        # would sit under SKILL_MIN_RUNS and the thresholds would hide the bug, not
        # the shape function - which is exactly how this test passed on its first run.
        write_tooluse(home, "chatA", REAL_NOISE * 3)
        write_tooluse(home, "chatB", REAL_NOISE * 3)
        got, p = skill_shapes(home)
        check("skills-real: the engine ran", got is not None, (p.stderr or p.stdout or "")[:300])
        got = got or []
        for bad in ("python", "grep", "cat", "ls", "echo", "sam"):
            check("skills-real: did not propose bare " + repr(bad), bad not in got, str(got))
        # and not with an argument bolted on either - the first run also produced
        # "echo === state files ===", which is one line of one chat, not a workflow
        look = ("cat", "ls", "echo", "grep", "head", "tail", "wc", "find", "sed", "cd")
        check("skills-real: nothing built on a look-around command",
              not [g for g in got if g.split(" ")[0] in look], str(got))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_skills_still_proposes_a_script_run_by_path():
    """The other half: tightening must not throw away the real thing. A project script run
    with nothing but flags after it is exactly the repeated work a skill is for."""
    home = make_home({})
    try:
        write_tooluse(home, "chatA", ["./scripts/deploy.sh --prod"] * 2)
        write_tooluse(home, "chatB", ["./scripts/deploy.sh --prod"])
        got = skill_shapes(home)[0] or []
        check("skills-script: proposed the repeated script", "deploy.sh" in got, str(got))
    finally:
        shutil.rmtree(home, ignore_errors=True)




# ------------------------------------------------- change 4: numbered handoff labels
NOTE_QIX_NUM = "HANDOFF LABEL: QIX bot -2 (16 Sep)\n\n# Handoff - QIX\n\nbody-QIXBOT\n"


def today_label():
    """The date the label should carry - '17 Sep', not '17 Sep 2026' and not '09/17'."""
    return datetime.datetime.now().strftime("%d %b").lstrip("0")


def test_handoff_label_gets_the_next_number():
    """His ask, 17 Sep 2026: "can we ad a -number after the name of chat so it would be
    updated numerically so i can keep up with the order", and then, choosing the format:
    "when you tell me write "myapp - 2" when i start a chat you would notice the number so
    you would know that you need to increase it by one".

    A fresh chat cannot know how many chats of this thread came before it, so the NUMBER
    has to be computed from disk, not guessed. The .used-* archives are the history - the
    note this chat picked up is already one of them by the time it hands off - so they
    count too. Per THREAD, not per folder: Harbor being at -7 must not push Context
    Guard to -8."""
    home = make_home({
        KEY + ".aaaaaaaa.used-20260915-101010.md":
            "HANDOFF LABEL: Context Guard\n\n# Handoff\n\noldest\n",
        KEY + ".bbbbbbbb.used-20260916-101010.md":
            "HANDOFF LABEL: Context Guard -2 (16 Sep)\n\n# Handoff\n\nolder\n",
        KEY + ".cccccccc.used-20260917-111111.md":
            "HANDOFF LABEL: Harbor -7 (17 Sep)\n\n# Handoff\n\nspot\n",
    })
    try:
        write_transcript(home, "numchat", 240_000)
        p = run(home, "numchat", "carry on")
        ctx = context_of(p)
        expect_clean(p, "label-number")
        check("label-number: the handoff warning fired",
              "THIS CHAT IS NOW" in ctx, repr(ctx[:200]))
        check("label-number: the label format now carries a number and a date",
              "HANDOFF LABEL: <name> -N (" in ctx, repr(ctx[-900:]))
        check("label-number: reads the highest number this thread reached, from the archives",
              "'Context Guard' is at -2 (next: -3)" in ctx, repr(ctx[-900:]))
        check("label-number: counts per thread, so Harbor's -7 does not leak into it",
              "'Harbor' is at -7 (next: -8)" in ctx, repr(ctx[-900:]))
        # Asks the HINT, not the whole blob. Measured 18 Sep 2026: "-0" not in ctx failed
        # about one run in twenty-five, because the guard prints the project's memory path
        # and tempfile.mkdtemp had named the throwaway home "flake-03coaq6t" - the "-0" was
        # the random suffix, nothing to do with thread numbering. An absence test over a
        # whole context matches text that was never the subject. [[tests-must-ask-the-code]]
        check("label-number: an unnumbered thread is reported as such, not as -0",
              "(next: -0)" not in ctx and "is at -0" not in ctx, repr(ctx[-900:]))
        check("label-number: the words he is told to type carry the number, in a code box",
              "`Nova Calc -5 (" in ctx, repr(ctx[-900:]))
        check("label-number: offers today's date for the label",
              today_label() in ctx, today_label() + " not in " + repr(ctx[-900:]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_a_brand_new_thread_starts_at_one():
    """Nothing on disk to count from. The instruction must say so outright rather than
    leave Claude to invent a number - an invented one breaks his ordering on the very
    first note of a thread."""
    home = make_home({})
    try:
        write_transcript(home, "freshthread", 240_000)
        p = run(home, "freshthread", "carry on")
        ctx = context_of(p)
        expect_clean(p, "first-number")
        check("first-number: says plainly that a new thread starts at -1",
              "starts at -1" in ctx, repr(ctx[-600:]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_a_numbered_short_label_answers_to_its_name():
    """The regression the numbering itself creates, and the reason this is not a one-line
    change. _tokens() drops words under 4 chars, so "QIX bot" has NO strong tokens and
    falls back to requiring the WHOLE label to be named. Add a number and a date and that
    whole label becomes {qix, bot, 2, 16, sep} - typing "QIX bot" is no longer a superset
    and the note goes unreachable, exactly the 14k-note bug fixed on 17 Sep, reintroduced
    by the feature he asked for. The number and date must be stripped before matching."""
    home = make_home({KEY + ".7e5cfde2.md": NOTE_QIX_NUM,
                      KEY + ".143f0320.md": NOTE_SPOT})
    try:
        p1 = run(home, "cf1f81f6-qixnum", "hi")
        check("numbered-short-label: turn 1 was the menu",
              "SAVED THREADS ARE WAITING" in context_of(p1), repr(context_of(p1)[:160]))
        p2 = run(home, "cf1f81f6-qixnum", "QIX bot")
        ctx = context_of(p2)
        expect_clean(p2, "numbered-short-label")
        check("numbered-short-label: the bare name still delivers the note",
              "body-QIXBOT" in ctx, repr(ctx[:200]))
        check("numbered-short-label: left the other one waiting", len(waiting(home)) == 1,
              str(waiting(home)))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_a_numbered_label_answers_when_he_types_the_number_too():
    """The other half: he is being TOLD to type "QIX bot -2", so typing it must work as
    well as typing the name alone. Both, or the instruction contradicts the tool."""
    home = make_home({KEY + ".7e5cfde2.md": NOTE_QIX_NUM,
                      KEY + ".143f0320.md": NOTE_SPOT})
    try:
        run(home, "cf1f81f6-qixfull", "hi")
        p2 = run(home, "cf1f81f6-qixfull", "QIX bot -2")
        ctx = context_of(p2)
        expect_clean(p2, "numbered-label-typed-in-full")
        check("numbered-label-typed-in-full: the name he was quoted delivers its note",
              "body-QIXBOT" in ctx, repr(ctx[:200]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_a_numbered_label_still_ignores_a_stray_word():
    """Stripping the number must not loosen the all-short-words fallback from "every word"
    to "any word". Ordinary talk containing one word of the label still summons nothing."""
    home = make_home({KEY + ".7e5cfde2.md": NOTE_QIX_NUM,
                      KEY + ".143f0320.md": NOTE_SPOT})
    try:
        run(home, "cf1f81f6-numstray", "hi")
        p2 = run(home, "cf1f81f6-numstray", "reboot the bot on the server")
        expect_clean(p2, "numbered-stray-word")
        check("numbered-stray-word: one word of a numbered label summons nothing",
              "body-QIXBOT" not in context_of(p2), repr(context_of(p2)[:160]))
        check("numbered-stray-word: both notes still waiting", len(waiting(home)) == 2,
              str(waiting(home)))
    finally:
        shutil.rmtree(home, ignore_errors=True)




# ------------------------------------ change 5: checkpoints and a hard ceiling
# His point, 17 Sep 2026: "sometimes we need to make it end in a checkpoint as you saw with
# PhotoBox APK it was a very long task that ended up doing tons of tokens". Measured the
# same day: that chat ran 524 turns / 82M tokens re-read, warned repeatedly, and carried on
# - because clause (c) of the warning ("you are mid-build, finish it") is always true during
# a long build. And 6d115b8e was born 22 Jul, peaks at 859k and was still being written on
# 17 Sep: it has ignored every warning for six days. WARNING IS NOT RETIRING. He chose
# "Checkpoints + a hard ceiling".

def write_big_chat(home, sid, started, ctx, msg="build the thing"):
    """A transcript that both DATES the session (first record, for session_start_ts) and
    reports its size (last record, for live_context). write_chat does only the first and
    write_transcript only the second; the ceiling needs both."""
    p = os.path.join(home, ".claude", "projects", KEY, sid + ".jsonl")
    with open(p, "w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "user",
                            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                       time.gmtime(started)),
                            "message": {"role": "user", "content": msg}}) + chr(10))
        f.write(json.dumps({"message": {"usage": {"cache_read_input_tokens": ctx,
                                                  "cache_creation_input_tokens": 0}}}) + chr(10))
    return p


def run_checkpoint(home, sid, text):
    """--checkpoint is called by Claude from Bash, NOT by a hook: no stdin payload, so it
    has to find the transcript from the session id alone."""
    env = child_env(home)
    return subprocess.run([sys.executable, GUARD, "--checkpoint", sid, text],
                          capture_output=True, text=True, env=env)


def checkpoint_file(home, sid):
    return os.path.join(home, ".claude", "context-guard", sid + ".checkpoints")


def test_a_checkpoint_records_the_context_it_was_reached_at():
    """A checkpoint is worth nothing as a bare label - "how long ago" is the whole question.
    Recording the CONTEXT SIZE rather than a turn number is free (live_context already runs)
    and is the number that actually maps to cost."""
    home = make_home({})
    try:
        write_big_chat(home, "cpchat", time.time() - 3600, 210_000)
        p = run_checkpoint(home, "cpchat", "engine wired")
        check("checkpoint: exited 0", p.returncode == 0, (p.stderr or "")[:200])
        check("checkpoint: no traceback", "Traceback" not in (p.stderr or ""),
              (p.stderr or "")[:200])
        f = checkpoint_file(home, "cpchat")
        check("checkpoint: wrote a checkpoint file", os.path.exists(f), f)
        body = open(f, encoding="utf-8").read() if os.path.exists(f) else ""
        check("checkpoint: kept his words for it", "engine wired" in body, repr(body))
        check("checkpoint: recorded the context it was reached at", "210000" in body,
              repr(body))
        run_checkpoint(home, "cpchat", "tests green")
        body = open(f, encoding="utf-8").read() if os.path.exists(f) else ""
        check("checkpoint: a second one APPENDS, it does not overwrite",
              "engine wired" in body and "tests green" in body, repr(body))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_warning_reports_the_last_checkpoint():
    """The handoff warning has to say WHERE the clean break was, or "hand off at a
    checkpoint" is advice with no referent."""
    home = make_home({})
    try:
        started = time.time() - 3600
        write_big_chat(home, "cpwarn", started, 210_000)
        run_checkpoint(home, "cpwarn", "engine wired")
        write_big_chat(home, "cpwarn", started, 290_000)      # the chat grew since
        p = run(home, "cpwarn", "carry on")
        ctx = context_of(p)
        expect_clean(p, "checkpoint-warning")
        check("checkpoint-warning: names the last checkpoint reached",
              "engine wired" in ctx, repr(ctx[-900:]))
        check("checkpoint-warning: says what it cost to get here since",
              "80k of context ago" in ctx, repr(ctx[-900:]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_warning_says_when_no_checkpoint_was_ever_recorded():
    """The absence has to be LOUD. A silent "last checkpoint: none" is how a counter ends up
    measuring nothing at all - see the image-reread counter that reported 3 blocks, all of
    them its own test files. If nothing is recorded, say so and hand over the exact command."""
    home = make_home({})
    try:
        write_transcript(home, "nocp", 240_000)
        p = run(home, "nocp", "carry on")
        ctx = context_of(p)
        expect_clean(p, "no-checkpoint")
        check("no-checkpoint: says outright that none were recorded",
              "NO checkpoint" in ctx, repr(ctx[-900:]))
        check("no-checkpoint: hands over the exact command, session id included",
              "--checkpoint nocp" in ctx, repr(ctx[-900:]))
        check("no-checkpoint: tells it to hand off AT the checkpoint, not at a byte count",
              "hand off at" in ctx.lower(), repr(ctx[-900:]))
        check("no-checkpoint: asks for a checkpoint to be recorded, in those words",
              "record a checkpoint" in ctx.lower(), repr(ctx[-900:]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_ceiling_blocks_the_stop_until_the_note_is_written():
    """The change that answers PhotoBox. Past the ceiling the handoff stops being advice:
    the Stop hook refuses to let the chat close until the note exists on disk. Same
    mechanism as the memory nag, which he already approved and which already works."""
    home = make_home({})
    try:
        write_big_chat(home, "ceiling", time.time() - 3600, 400_000)
        p = run_stop(home, "ceiling")
        expect_clean(p, "ceiling")
        r = blocked(p)
        check("ceiling: refused to let a 400k chat close", bool(r), repr((p.stdout or "")[:200]))
        check("ceiling: names the note it wants written",
              KEY + ".ceiling" in r.replace(chr(92), "/"), repr(r[:400]))
        check("ceiling: the ledger was still written first",
              os.path.exists(os.path.join(home, ".claude", "handoff", KEY + ".requests.md")),
              str(os.listdir(os.path.join(home, ".claude", "handoff"))))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_ceiling_leaves_an_ordinary_chat_alone():
    """250k is expensive and already warned about on the way IN. The ceiling is the last
    resort, not a second warning - if it fires on ordinary chats he will switch it off."""
    home = make_home({})
    try:
        write_big_chat(home, "cheapish", time.time() - 3600, 200_000)
        p = run_stop(home, "cheapish")
        expect_clean(p, "ceiling-quiet")
        check("ceiling-quiet: let a 200k chat close", blocked(p) == "",
              repr(blocked(p)[:200]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_ceiling_is_satisfied_by_the_note_existing():
    """It is not a punishment for being big, it is a demand for the note. Once the note is
    on disk the chat may close - and by then the memory nag is the thing doing the checking."""
    home = make_home({})
    try:
        started = time.time() - 3600
        write_big_chat(home, "ceilnote", started, 400_000)
        write_note(home, "ceilnote")
        write_memory(home, "saved-this-chat.md", time.time())
        append_tool_use(home, "ceilnote", "Write", {"file_path": os.path.join(
            home, ".claude", "projects", KEY, "memory", "saved-this-chat.md")})
        p = run_stop(home, "ceilnote")
        expect_clean(p, "ceiling-satisfied")
        check("ceiling-satisfied: the written note lets the chat close",
              blocked(p) == "", repr(blocked(p)[:300]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_ceiling_has_an_off_switch():
    """[[automate-it-dont-tell-him]]: always leave him the override. Same shape as the
    memory nag's - an empty file he can create once."""
    home = make_home({})
    try:
        os.makedirs(os.path.join(home, ".claude", "context-guard"), exist_ok=True)
        open(os.path.join(home, ".claude", "context-guard", "no-ceiling"), "w").close()
        write_big_chat(home, "ceiloff", time.time() - 3600, 400_000)
        p = run_stop(home, "ceiloff")
        expect_clean(p, "ceiling-off")
        check("ceiling-off: the off switch really switches it off", blocked(p) == "",
              repr(blocked(p)[:200]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_ceiling_gives_up_rather_than_trapping_him():
    """A Stop hook that blocks forever is a chat he cannot end. If Claude will not write the
    note after three tries, something is wrong with the tool, not with him - let go."""
    home = make_home({})
    try:
        write_big_chat(home, "ceilcap", time.time() - 3600, 400_000)
        r = [blocked(run_stop(home, "ceilcap")) for _ in range(4)]
        check("ceiling-cap: blocked the first time", bool(r[0]), repr(r[0][:120]))
        check("ceiling-cap: blocked the third time", bool(r[2]), repr(r[2][:120]))
        check("ceiling-cap: let go on the fourth", r[3] == "", repr(r[3][:200]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_ceiling_does_not_block_a_stop_it_already_blocked():
    """stop_hook_active means Claude Code is ALREADY continuing because this hook blocked.
    Blocking again is a loop nobody can interrupt."""
    home = make_home({})
    try:
        write_big_chat(home, "ceilactive", time.time() - 3600, 400_000)
        p = run_stop(home, "ceilactive", active=True)
        expect_clean(p, "ceiling-active")
        check("ceiling-active: did not block a stop it had already blocked",
              blocked(p) == "", repr(blocked(p)[:200]))
    finally:
        shutil.rmtree(home, ignore_errors=True)




# --------------------------------- change 6: a counter must carry its own provenance
# Measured 17 Sep 2026, and it is the reason this change exists. An earlier chat read
# "image re-reads blocked: 3" off --report and concluded the re-read guard was broken and
# that text reads were the real cost. Both conclusions were wrong, and the report is what
# made them look right: a bare number with no dates and no examples. The three blocks were
# real blocks - of probe.png and hooktest.png, inside a two-minute window on 11 Sep, while
# the guard was being tested. Nothing in the report said so.
# (The re-read guard was deleted 2 Oct 2026; the fixture now uses the ledger budget row.)

REPORT_LOG = chr(10).join([
    "2026-09-11T10:29:39 ledger: budget dropped 3 request(s) of thread alpha",
    "2026-09-11T10:30:52 ledger: budget dropped 3 request(s) of thread alpha",
    "2026-09-11T10:31:39 ledger: budget dropped 3 request(s) of thread alpha",
    "2026-09-11T11:00:00 size: ctx=190000 level=150000 last=0 due=True",
    "2026-09-14T11:00:00 size: ctx=260000 level=250000 last=190000 due=True",
    "2026-09-17T12:00:00 handoff picked up by UserPromptSubmit (1 note(s), 20000 chars)",
    "2026-09-17T12:01:00 ledger: +2 request(s)",
    "2026-09-17T13:00:00 checkpoint at 182000: tests green",
    "2026-09-17T13:30:00 ceiling: blocked stop #1 at ctx=400000",
]) + chr(10)


# ------------------------------------------------ pause ONE chat, 24 Sep 2026
def run_pause(home, sid, *why, resume=False):
    """--pause / --resume are called by Claude from Bash, like --checkpoint: no payload."""
    return subprocess.run([sys.executable, GUARD, "--resume" if resume else "--pause", sid]
                          + list(why), capture_output=True, text=True, env=child_env(home))


def test_a_paused_chat_is_not_told_to_hand_off():
    """His words, 24 Sep 2026: "in this chat i need to pause context guard until we finish
    all the books". The unpaused sibling in the same folder is the control - without it a
    silent hook proves nothing, and it is also what "this chat only" means."""
    home = make_home({})
    try:
        started = time.time() - 3600
        write_big_chat(home, "pausedchat", started, 240_000)
        write_big_chat(home, "otherchat", started, 240_000)
        p = run_pause(home, "pausedchat", "reading", "the", "books")
        expect_clean(p, "pause")
        check("pause: says it paused", "paused" in (p.stdout or ""), repr(p.stdout[:200]))
        quiet = run(home, "pausedchat", "next book please")
        loud = run(home, "otherchat", "next book please")
        expect_clean(quiet, "pause-size")
        check("pause: the paused chat hears nothing at 240k", (quiet.stdout or "").strip() == "",
              repr((quiet.stdout or "")[:200]))
        check("pause: its sibling is still told to hand off",
              "STOP - THIS CHAT" in context_of(loud), repr(context_of(loud)[:200]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_a_paused_chat_can_close_past_the_ceiling():
    home = make_home({})
    try:
        started = time.time() - 3600
        write_big_chat(home, "pausedbig", started, 400_000)
        write_big_chat(home, "otherbig", started, 400_000)
        run_pause(home, "pausedbig", "reading the books")
        p = run_stop(home, "pausedbig")
        expect_clean(p, "pause-ceiling")
        check("pause-ceiling: the paused chat may close at 400k", blocked(p) == "",
              repr(blocked(p)[:200]))
        other = blocked(run_stop(home, "otherbig"))
        check("pause-ceiling: its sibling is still held", "CEILING" in other, repr(other[:200]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_a_paused_chat_still_gets_the_memory_nag():
    """"...but keep the creating memories" - the pause must never reach the memory nag."""
    home = make_home({})
    try:
        started = time.time() - 3600
        write_chat(home, "pausednote", started)
        run_pause(home, "pausednote", "reading the books")
        write_note(home, "pausednote")
        write_memory(home, "old-lesson.md", started - 86400)    # yesterday, not this chat
        why = blocked(run_stop(home, "pausednote"))
        check("pause-memory: the memory nag still fires", "SAVED TO MEMORY" in why,
              repr(why[:200]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_resume_brings_the_warning_back_and_a_typo_pauses_nothing():
    home = make_home({})
    try:
        write_big_chat(home, "resumed", time.time() - 3600, 240_000)
        run_pause(home, "resumed", "reading")
        r = run_pause(home, "resumed", resume=True)
        expect_clean(r, "resume")
        check("resume: says it resumed", "resumed" in (r.stdout or ""), repr(r.stdout[:200]))
        back = context_of(run(home, "resumed", "carry on"))
        check("resume: the warning is back", "STOP - THIS CHAT" in back, repr(back[:200]))
        again = run_pause(home, "resumed", resume=True)
        check("resume: twice is harmless and says so", "not paused" in (again.stdout or ""),
              repr(again.stdout[:200]))
        bad = run_pause(home, "no-such-chat", "typo")
        check("pause-typo: refuses a session with no transcript",
              "nothing paused" in (bad.stdout or ""), repr(bad.stdout[:200]))
        check("pause-typo: wrote no state for it", not os.path.exists(os.path.join(
            home, ".claude", "context-guard", "no-such-chat.json")), "state file exists")
    finally:
        shutil.rmtree(home, ignore_errors=True)


def run_report(home, log=REPORT_LOG):
    """log=None runs against whatever is already there - needed by the writes-nothing test,
    since rewriting the log would itself change an mtime and fake a failure."""
    if log is not None:
        d = os.path.join(home, ".claude")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "context-audit.log"), "w", encoding="utf-8") as f:
            f.write(log)
    env = child_env(home)
    return subprocess.run([sys.executable, GUARD, "--report"],
                          capture_output=True, text=True, env=env)


def line_with(out, needle):
    for l in out.splitlines():
        if needle in l:
            return l
    return ""


def test_report_shows_what_was_blocked_not_just_how_many():
    """"image re-reads blocked: 3" is unfalsifiable. The filenames make it checkable in one
    glance - probe.png and hooktest.png are obviously not his work."""
    home = make_home({})
    try:
        p = run_report(home)
        out = p.stdout or ""
        check("report: exited 0", p.returncode == 0, (p.stderr or "")[:200])
        check("report: no traceback", "Traceback" not in (p.stderr or ""),
              (p.stderr or "")[:200])
        check("report: names what was actually blocked", "thread alpha" in out,
              repr(out[:900]))
        check("report: dates the blocks", "11 Sep" in out, repr(out[:900]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_report_flags_a_counter_that_only_ever_fired_on_one_day():
    """The tell that this was a test run and not a working feature: every event landed on
    the same day. Two or more events, one date - say so on the row. A SINGLE event is not
    evidence either way and must not be flagged, or the warning becomes noise."""
    home = make_home({})
    try:
        out = (run_report(home).stdout or "")
        check("report: flags the all-in-one-day counter", "all on one day" in out,
              repr(out[:900]))
        check("report: flags it on the ledger budget row, not the warnings row",
              "all on one day" in line_with(out, "ledger budget drops"),
              repr(line_with(out, "ledger budget drops")))
        check("report: does not flag events spread over several days",
              "all on one day" not in line_with(out, "warnings fired"),
              repr(line_with(out, "warnings fired")))
        check("report: does not flag a single lone event",
              out.count("all on one day") == 1, str(out.count("all on one day")))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_report_says_outright_when_a_guard_has_never_fired():
    """A guard that has never once fired is the most important line in the report and the
    easiest to miss when it is printed as a bare 0 among other numbers."""
    home = make_home({})
    try:
        out = (run_report(home).stdout or "")
        check("report: lists the memory nag even though it never fired",
              "memory nag" in out.lower(), repr(out[:900]))
        check("report: says never, not just 0",
              "never" in line_with(out, "memory nag").lower(),
              repr(line_with(out, "memory nag")))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_report_counts_the_guards_that_were_added_later():
    """cmd_report() counted four events and was never updated. Three guards have been added
    since - the memory nag, the ceiling and checkpoints - and none of them appeared, so the
    report quietly described an older tool than the one running."""
    home = make_home({})
    try:
        out = (run_report(home).stdout or "")
        check("report: counts ceiling blocks", "1" in line_with(out, "ceiling"),
              repr(line_with(out, "ceiling")))
        check("report: counts checkpoints", "1" in line_with(out, "checkpoint"),
              repr(line_with(out, "checkpoint")))
        check("report: still counts the original four",
              all(line_with(out, n) for n in ("warnings fired", "handoff notes picked up",
                                              "ledger appends", "ledger budget drops")),
              repr(out[:900]))
        check("report: has no image re-reads row", "image re-reads" not in out, repr(out[:900]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_report_writes_nothing():
    """--report is the one command that is safe to run by hand against the real home, and
    it stays that way."""
    home = make_home({})
    try:
        run_report(home)                       # creates the log; snapshot AFTER that
        before = snapshot(home)
        run_report(home, log=None)
        check("report: changed nothing", snapshot(home) == before,
              "files differ after a second run")
    finally:
        shutil.rmtree(home, ignore_errors=True)




def test_label_number_never_goes_backwards():
    """The number he is TOLD to type must never drop.

    It did, on 17 Sep: he typed 'PhotoBox APK -5 (17 Sep)' at 18:51 and was handed
    'PhotoBox APK -2 (17 Sep)' at 19:27, because label_numbers() only counted labels
    that ALREADY carried a number and every older note was bare. His own ledger is the
    append-only record of how far the thread had really gone, so it is the floor."""
    home = make_home({KEY + ".aaaaaaaa.md": "HANDOFF LABEL: Foo -1 (17 Sep)" + chr(10) + "body-FOO" + chr(10)})
    try:
        led = os.path.join(home, ".claude", "handoff", KEY + ".requests.md")
        with open(led, "w", encoding="utf-8") as f:
            f.write("### 2026-09-17 10:00:00" + chr(10) * 2 + "Foo -4 (17 Sep)" + chr(10) * 2)
        sid = "bbbbbbbb-0000-0000-0000-000000000000"
        write_transcript(home, sid, 300000)
        ctx = context_of(run(home, sid, "carry on", transcript_exists=True))
        check("label-floor: takes the number from the ledger, not just the notes",
              "'Foo' is at -4 (next: -5)" in ctx, repr(ctx[-600:]))
        check("label-floor: does not offer the number he has already used",
              "(next: -2)" not in ctx, repr(ctx[-600:]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_ledger_prose_is_not_a_thread():
    """Only a real label seeds the floor. A sentence of his that happens to end in a
    number must not invent a thread, or the hint fills with junk he has to read."""
    home = make_home({KEY + ".aaaaaaaa.md": "HANDOFF LABEL: Foo -1 (17 Sep)" + chr(10) + "body-FOO" + chr(10)})
    try:
        led = os.path.join(home, ".claude", "handoff", KEY + ".requests.md")
        with open(led, "w", encoding="utf-8") as f:
            f.write("### 2026-09-17 10:00:00" + chr(10) * 2
                    + "can you check why the build dropped from 10 to -5" + chr(10) * 2)
        sid = "cccccccc-0000-0000-0000-000000000000"
        write_transcript(home, sid, 300000)
        ctx = context_of(run(home, sid, "carry on", transcript_exists=True))
        check("label-floor: prose does not become a thread",
              "build dropped" not in ctx, repr(ctx[-600:]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_pickup_renames_the_chat_to_its_own_number():
    """He asked for the number so he could 'keep up with the order' in his sidebar. The
    title is generated by the app from the conversation and drops the number (measured:
    he typed 'QIX handover -2 (17 Sep)', the chat was titled 'QIX handover'). Nothing
    ever renamed it, so picking up a note must do it."""
    home = make_home({KEY + ".9e19c7ab.md": NOTE_CTX})
    try:
        p = run(home, "dddddddd-new", "context guard")
        ctx = context_of(p)
        expect_clean(p, "pickup/rename")
        check("pickup/rename: delivered the note", "body-CONTEXTGUARD" in ctx, repr(ctx[:160]))
        check("pickup/rename: tells Claude to retitle this session",
              "set_session_title" in ctx, repr(ctx[:400]))
        check("pickup/rename: the title to set IS the label on the note, as it is",
              rename_title(ctx) == "context guard", repr(rename_title(ctx)))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def rename_title(ctx):
    """The title the pickup tells Claude to set: the first quoted string after 'self' in the
    set_session_title instruction. Read out of the instruction itself, so a test compares the
    title Claude will actually type against the label - agreement, never prose."""
    m = re.search(r"set_session_title[^']*'self'[^']*'([^']*)'", ctx or "")
    return m.group(1) if m else None


NOTE_MANGO = ("HANDOFF LABEL: Mango -4 (1 Jan)" + chr(10) + "WRITTEN BY: Mango -3 (1 Jan)"
              + chr(10) * 2 + "body-MANGO" + chr(10))


def test_the_title_is_the_label_as_it_is():
    """His words, 24 Sep 2026: "do 2" - yes to item 2 of chat -25's list, which read "The
    renaming rule says 'take the note's label and add one', but the label already is the
    next number". The chat that WRITES a note raises the number when it names the label, so
    the chat that picks it up must copy the label as it is. Read literally, the old wording
    named chat -25 as -26 and skipped a number in his sidebar; -25 only caught it by checking
    the chain (WRITTEN BY -24, label -25, he typed -25) instead of the prose. The pickup now
    prints the exact title, so there is no arithmetic left to get wrong."""
    for prompt in ("Mango -4 (1 Jan)", "mango", "carry on"):
        home = make_home({KEY + ".aaaaaaaa.md": NOTE_MANGO})
        try:
            p = run(home, "eeeeeeee-new", prompt)
            ctx = context_of(p)
            expect_clean(p, "title/as-is " + repr(prompt))
            check("title/as-is: delivered the note for " + repr(prompt),
                  "body-MANGO" in ctx, repr(ctx[:160]))
            check("title/as-is: the title to set is the label, for " + repr(prompt),
                  rename_title(ctx) == "Mango -4 (1 Jan)", repr(rename_title(ctx)))
            check("title/as-is: the next number up appears nowhere, for " + repr(prompt),
                  "Mango -5" not in ctx, repr(ctx[:600]))
        finally:
            shutil.rmtree(home, ignore_errors=True)


def test_his_typed_number_wins_the_title():
    """He typed the thread with a DIFFERENT number than the label on the note - chat -24
    opened that way. His number wins, and the note's date fills in when he leaves his off."""
    for prompt, want in (("Mango -3 (1 Jan)", "Mango -3 (1 Jan)"), ("mango -6", "Mango -6 (1 Jan)")):
        home = make_home({KEY + ".aaaaaaaa.md": NOTE_MANGO})
        try:
            ctx = context_of(run(home, "ffffffff-new", prompt))
            check("title/his-number: " + repr(prompt) + " makes the title " + repr(want),
                  rename_title(ctx) == want, repr(rename_title(ctx)))
        finally:
            shutil.rmtree(home, ignore_errors=True)


def test_two_threads_each_offer_their_own_label_as_the_title():
    """Two notes picked up at once: each thread's title is its own label, as it is, and no
    raised number is invented for either."""
    home = make_home({KEY + ".aaaaaaaa.md": NOTE_MANGO,
                      KEY + ".bbbbbbbb.md": "HANDOFF LABEL: Barley -7 (1 Jan)" + chr(10) * 2
                                            + "body-BARLEY" + chr(10)})
    try:
        ctx = context_of(run(home, "abababab-new", "mango and barley"))
        check("title/two: both notes delivered", "body-MANGO" in ctx and "body-BARLEY" in ctx,
              repr(ctx[:200]))
        for label in ("Mango -4 (1 Jan)", "Barley -7 (1 Jan)"):
            check("title/two: offers " + repr(label) + " as a title, as it is",
                  "'" + label + "'" in ctx, repr(ctx[:600]))
        check("title/two: no raised number is invented",
              "Mango -5" not in ctx and "Barley -8" not in ctx, repr(ctx[:600]))
    finally:
        shutil.rmtree(home, ignore_errors=True)

# ------------------------------------------------- the meter itself: an all-zero last turn
def write_zero_tail_chat(home, sid, started, ctx, msg="build the thing"):
    """A transcript whose real context is `ctx` but whose LAST usage record is all zeros.

    Measured 17 Sep 2026 on his own folder: 3 of 49 transcripts end on a record with
    cache_read=0, cache_creation=0, input=0, output=0 - the app writes one for an aborted
    or empty turn. live_context() returned on the first record carrying a `usage` key at
    all, so a chat holding 225,915 tokens reported 0 - "brand new chat"."""
    p = os.path.join(home, ".claude", "projects", KEY, sid + ".jsonl")
    with open(p, "w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "user",
                            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                       time.gmtime(started)),
                            "message": {"role": "user", "content": msg}}) + chr(10))
        f.write(json.dumps({"type": "assistant",
                            "message": {"usage": {"cache_read_input_tokens": ctx,
                                                  "cache_creation_input_tokens": 0}}}) + chr(10))
        f.write(json.dumps({"type": "assistant",
                            "message": {"usage": {"cache_read_input_tokens": 0,
                                                  "cache_creation_input_tokens": 0,
                                                  "input_tokens": 0,
                                                  "output_tokens": 0}}}) + chr(10))
    return p


def test_a_trailing_zero_usage_record_does_not_read_as_an_empty_chat():
    """The meter drives EVERYTHING - the warning, the ceiling, the checkpoint line and the
    freshness gate. One all-zero trailing record silenced the lot."""
    home = make_home({})
    try:
        write_zero_tail_chat(home, "zerotail", time.time() - 3600, 240_000)
        p = run(home, "zerotail", "carry on")
        expect_clean(p, "zero-tail")
        ctx = context_of(p)
        check("zero-tail: still warned about a 240k chat", "240k" in ctx, repr(ctx[:200]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_a_big_chat_with_a_zero_trailing_record_does_not_swallow_a_note():
    """The measured case: session 7e5cfde2, 226k of context and 1.3 MB on disk - UNDER the
    1.5 MB belt-and-braces gate - read as 0 and so passed the FRESH_CTX freshness test. It
    was eligible to eat a note written for a fresh chat, which is the exact failure
    FRESH_CTX exists to stop."""
    home = make_home({KEY + ".99999999.md": NOTE_CTX})
    try:
        write_zero_tail_chat(home, "zerobig", time.time() - 3600, 226_000)
        p = run(home, "zerobig", "hello")
        expect_clean(p, "zero-fresh")
        check("zero-fresh: the note was NOT handed to a 226k chat",
              "HANDOFF NOTE" not in context_of(p), repr(context_of(p)[:200]))
        check("zero-fresh: the note is still waiting for a real fresh chat",
              waiting(home) == [KEY + ".99999999.md"], str(waiting(home)))
    finally:
        shutil.rmtree(home, ignore_errors=True)


# ------------------------------------------- the ceiling must sit UNDER the compaction point
def write_settings(home, window):
    """His real settings.json carries autoCompactWindow. The app compacts at 91% of it,
    and a ceiling above that point can never be reached."""
    d = os.path.join(home, ".claude")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "settings.json"), "w", encoding="utf-8") as f:
        json.dump({"autoCompactWindow": window}, f)


def test_the_ceiling_fires_below_the_point_the_window_auto_compacts():
    """Measured 17 Sep 2026: autoCompactWindow is 350000, so the app compacts at 318,500 -
    and across 49 transcripts NOT ONE session since that setting existed has ever reached
    320k. The ceiling was 350k. It could not fire, and in six days it never did."""
    home = make_home({})
    try:
        write_settings(home, 350_000)
        write_big_chat(home, "nearcomp", time.time() - 3600, 305_000)
        p = run_stop(home, "nearcomp")
        expect_clean(p, "ceiling-below")
        check("ceiling-below: blocked a 305k chat before compaction could reset it",
              blocked(p) != "", repr((p.stdout or "")[:200]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_ceiling_tracks_a_smaller_configured_window():
    """Do not hardcode a number that a settings change can strand above the roof again.
    With a 200k window the app compacts at 182k, so the ceiling has to come down with it."""
    home = make_home({})
    try:
        write_settings(home, 200_000)
        write_big_chat(home, "smallwin", time.time() - 3600, 170_000)
        p = run_stop(home, "smallwin")
        expect_clean(p, "ceiling-window")
        check("ceiling-window: a 170k chat is past the ceiling of a 200k window",
              blocked(p) != "", repr((p.stdout or "")[:200]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_ceiling_still_leaves_a_mid_sized_chat_alone():
    """The guard against over-correcting: dropping the ceiling must not turn it into a
    third warning. 250k is expensive and already warned about on the way in."""
    home = make_home({})
    try:
        write_settings(home, 350_000)
        write_big_chat(home, "midsize", time.time() - 3600, 250_000)
        p = run_stop(home, "midsize")
        expect_clean(p, "ceiling-mid")
        check("ceiling-mid: let a 250k chat close", blocked(p) == "",
              repr(blocked(p)[:200]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


# ------------------------------------- change 6: the ledger is shared by every chat
# Measured 18 Sep 2026 across his own 45 D--Claude transcripts: the ledger dedups on the
# CONTENT of a message alone, project-wide, so the same words said in a different chat are
# silently dropped - 292 of 960 messages, including a real request asked in two chats and
# recorded once. And the tail injected alongside a handoff note held entries from ELEVEN
# different chats, only 3 of them from the thread being picked up.
def write_chat_msgs(home, sid, started, msgs):
    """A transcript carrying several of the user's messages, oldest first."""
    p = os.path.join(home, ".claude", "projects", KEY, sid + ".jsonl")
    with open(p, "w", encoding="utf-8") as f:
        for i, m in enumerate(msgs):
            f.write(json.dumps({
                "type": "user",
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started + i)),
                "message": {"role": "user", "content": m}}) + chr(10))
    return p


def ledger_body(home):
    lp = os.path.join(home, ".claude", "handoff", KEY + ".requests.md")
    if not os.path.exists(lp):
        return ""
    with open(lp, encoding="utf-8") as f:
        return f.read()


def test_the_same_words_in_two_chats_are_both_recorded():
    """The ledger exists so a request is never dropped. Keyed on content alone it drops the
    SECOND chat to say a thing - measured on his own data: "the wallet thingy needs a better
    approach what do you think?" was asked in two chats and recorded once."""
    home = make_home({})
    try:
        started = time.time() - 3600
        words = "the wallet thingy needs a better approach what do you think?"
        write_chat_msgs(home, "chatoneaa", started, [words])
        write_chat_msgs(home, "chattwobb", started + 10, [words])
        expect_clean(run_stop(home, "chatoneaa"), "two-chats-a")
        expect_clean(run_stop(home, "chattwobb"), "two-chats-b")
        body = ledger_body(home)
        check("two-chats: recorded once for each chat that said it",
              body.count(words) == 2, "counted %d" % body.count(words))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_ledger_records_which_chat_said_each_thing():
    """Without attribution a fresh chat cannot tell whose request it is reading, which is
    why the injected tail has to carry a warning telling it to guess."""
    home = make_home({})
    try:
        write_chat_msgs(home, "whosaidit", time.time() - 3600, ["make the thing faster"])
        expect_clean(run_stop(home, "whosaidit"), "attributed")
        body = ledger_body(home)
        check("attributed: the entry names the chat that said it",
              "whosaidi" in body, repr(body[-200:]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_a_chat_does_not_re_record_its_own_repeated_words():
    """Per-chat keying must not cost the dedup that stops the Stop hook re-appending the
    same message on every single turn. Same chat, same words, one entry."""
    home = make_home({})
    try:
        started = time.time() - 3600
        write_chat_msgs(home, "repeatsit", started, ["say it once"])
        expect_clean(run_stop(home, "repeatsit"), "no-repeat-1")
        expect_clean(run_stop(home, "repeatsit"), "no-repeat-2")
        body = ledger_body(home)
        check("no-repeat: one entry, not two",
              body.count("say it once") == 1, "counted %d" % body.count("say it once"))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_first_message_of_a_chip_chat_is_recorded_however_long():
    """A chat opened from a suggestion chip starts with the chip's prompt, which is a whole
    brief - measured 29 Sep 2026, 2203 chars against a 2000 cap, so the ledger dropped the
    one message that said what the chat was for. The cap is there to stop pasted skill
    bodies later in a chat; it must not eat the opening message."""
    home = make_home({})
    try:
        brief = "Fix the Harbor parser, test-first. " + ("More detail about the task. " * 80)
        later = "A pasted reference dump. " + ("filler " * 400)
        check("chip-chat: fixture brief is over the cap",
              len(brief) > guard_constant("LEDGER_MAX_CHARS"), str(len(brief)))
        write_chat_msgs(home, "chipchat1", time.time() - 3600,
                        [brief, "short follow up", later])
        expect_clean(run_stop(home, "chipchat1"), "chip-chat")
        body = ledger_body(home)
        check("chip-chat: the long opening message is in the ledger",
              "Fix the Harbor parser" in body, repr(body[:200]))
        check("chip-chat: but clipped at the cap, so a long brief cannot bloat every note",
              brief not in body and "more characters of this opening message" in body,
              repr(body[:300]))
        check("chip-chat: the short follow-up is still recorded",
              "short follow up" in body, repr(body[-200:]))
        check("chip-chat: control - a long message that is NOT first is still dropped",
              "A pasted reference dump" not in body, repr(body[-200:]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_harness_placeholders_are_not_recorded_as_his_words():
    """The ledger calls itself "his own words". 246 of the 292 dropped messages were the
    app's own text - image placeholders, the auto-continue line, the interrupt marker.
    Recording those per chat would bury the real requests under them."""
    home = make_home({})
    try:
        write_chat_msgs(home, "noisychat", time.time() - 3600, [
            "[Image: original 1080x2400, displayed at 900x2000. Multiply coordinates by 1.2]",
            "Continue from where you left off.",
            "[Request interrupted by user]",
            "this one is really his"])
        expect_clean(run_stop(home, "noisychat"), "noise")
        body = ledger_body(home)
        check("noise: the image placeholder is not his words", "[Image:" not in body)
        check("noise: the auto-continue line is not his words",
              "Continue from where you left off" not in body)
        check("noise: the interrupt marker is not his words",
              "[Request interrupted by user]" not in body)
        check("noise: but his real message survived", "this one is really his" in body)
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_injected_tail_separates_this_thread_from_the_others():
    """The whole point. A chat picking up "Context Guard" must not have to guess which of
    the last 30 requests were its own thread's - measured, 11 different chats in one tail."""
    home = make_home({})
    try:
        started = time.time() - 7200
        write_chat_msgs(home, "guardaaaa", started, ["the guard thing he asked for"])
        write_chat_msgs(home, "harborbbb", started + 10, ["the harbor thing he asked for"])
        expect_clean(run_stop(home, "guardaaaa"), "tail-a")
        expect_clean(run_stop(home, "harborbbb"), "tail-b")
        for sid, body in (("guardaaaa", "HANDOFF LABEL: Context Guard -2 (18 Sep)\n\n# n\n"),
                          ("harborbbb", "HANDOFF LABEL: Harbor -4 (18 Sep)\n\n# n\n")):
            with open(os.path.join(home, ".claude", "handoff",
                                   KEY + "." + sid[:8] + ".md"), "w", encoding="utf-8") as f:
                f.write(body)
        p = run(home, "freshchat", "Context Guard -2 (18 Sep)")
        expect_clean(p, "tail-split")
        ctx = context_of(p)
        mine = ctx.find("the guard thing he asked for")
        theirs = ctx.find("the harbor thing he asked for")
        split = ctx.find("OTHER CHATS")
        check("tail-split: this thread is named", "THIS THREAD" in ctx, repr(ctx[-400:]))
        check("tail-split: both requests still reach the new chat",
              mine > -1 and theirs > -1, "mine=%d theirs=%d" % (mine, theirs))
        check("tail-split: his own thread's words come first",
              -1 < mine < split, "mine=%d split=%d" % (mine, split))
        check("tail-split: the other chat's words are on the other side of the line",
              split > -1 and theirs > split, "theirs=%d split=%d" % (theirs, split))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_tail_still_reads_when_nothing_is_attributed():
    """His real ledger is 418 entries deep and none of them carry a chat id. The split must
    degrade to "I do not know whose this is", never to dropping them."""
    home = make_home({"%s.oldchatx.md" % KEY: NOTE_CTX})
    try:
        lp = os.path.join(home, ".claude", "handoff", KEY + ".requests.md")
        with open(lp, "w", encoding="utf-8") as f:
            f.write("# What the user actually asked for - his own words" + chr(10)
                    + chr(10) + "### 2026-09-11 10:00:00" + chr(10)
                    + chr(10) + "something he asked before attribution existed" + chr(10))
        write_transcript(home, "freshchat", 1000)
        p = run(home, "freshchat", "context guard")
        expect_clean(p, "tail-legacy")
        ctx = context_of(p)
        check("tail-legacy: the old entry is still handed over",
              "something he asked before attribution existed" in ctx, repr(ctx[-300:]))
        check("tail-legacy: and it is not claimed for this thread",
              ctx.find("something he asked before") > ctx.find("THIS THREAD")
              if "THIS THREAD" in ctx else True, repr(ctx[-300:]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


# --------------- change 7: recover the attribution the first 419 entries never had
# Entries written from 18 Sep 2026 carry their chat id inline. The ones before it do not,
# and his ledger is 421 entries deep - so a pickup today still sees a wall of unattributed
# requests. The words are still in the transcripts that said them, so it can be recovered.
# The ledger itself is append-only BY CONTRACT ("never edited, never summarised, never
# consumed"), so the recovered map goes in a sidecar and the ledger is not touched.
def write_old_ledger(home, entries):
    """A ledger in the pre-attribution format - "### <ts>" with no chat id."""
    lp = os.path.join(home, ".claude", "handoff", KEY + ".requests.md")
    with open(lp, "w", encoding="utf-8") as f:
        f.write("# What the user actually asked for - his own words" + chr(10))
        for ts, body in entries:
            f.write(chr(10) + "### " + ts + chr(10) + chr(10) + body + chr(10))
    return lp


def setup_two_threads(home):
    """Two chats, two threads, both their words in an OLD-format ledger."""
    started = time.time() - 7200
    write_chat_msgs(home, "guardaaaa", started, ["the guard thing he asked for"])
    write_chat_msgs(home, "harborbbb", started + 10, ["the harbor thing he asked for"])
    write_old_ledger(home, [("2026-09-17 10:00:00", "the guard thing he asked for"),
                            ("2026-09-17 10:01:00", "the harbor thing he asked for")])
    for sid, body in (("guardaaaa", "HANDOFF LABEL: Context Guard -2 (18 Sep)\n\n# n\n"),
                      ("harborbbb", "HANDOFF LABEL: Harbor -4 (18 Sep)\n\n# n\n")):
        with open(os.path.join(home, ".claude", "handoff",
                               KEY + "." + sid[:8] + ".md"), "w", encoding="utf-8") as f:
            f.write(body)


def test_older_entries_get_their_chat_back():
    """After the recovery runs, a pickup sorts the OLD entries by thread too."""
    home = make_home({})
    try:
        setup_two_threads(home)
        # the writer was deleted 2 Oct 2026; the sidecar it recovered is still READ, so
        # seed it directly: {entry_key of the ledger's first block: the chat id}
        lp = os.path.join(home, ".claude", "handoff", KEY + ".requests.md")
        g = guard_call(home, "print(guard.entry_key(open(%r, encoding='utf-8').read()"
                             ".split(chr(10) + '### ')[1]))" % lp)
        check("recover: the key was computed", g.returncode == 0 and g.stdout.strip(),
              (g.stderr or "")[:200])
        with open(os.path.join(home, ".claude", "handoff",
                               KEY + ".requests.attrib.json"), "w", encoding="utf-8") as f:
            json.dump({g.stdout.strip(): "guardaaa"}, f)
        p = run(home, "freshchat", "Context Guard -2 (18 Sep)")
        expect_clean(p, "recover")
        ctx = context_of(p)
        mine = ctx.find("the guard thing he asked for")
        theirs = ctx.find("the harbor thing he asked for")
        split = ctx.find("OTHER CHATS")
        check("recover: the old entry is now claimed for this thread",
              "THIS THREAD" in ctx and -1 < mine < split,
              "mine=%d split=%d" % (mine, split))
        check("recover: and the other thread's stayed on its own side",
              split > -1 and theirs > split, "theirs=%d split=%d" % (theirs, split))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_attribute_is_gone():
    """--attribute was deleted 2 Oct 2026 (it was a one-off recovery, already run). A
    leftover call must be a silent no-op, write no sidecar, and ledger-budget.py is gone."""
    home = make_home({})
    try:
        setup_two_threads(home)
        side = os.path.join(home, ".claude", "handoff", KEY + ".requests.attrib.json")
        a = subprocess.run([sys.executable, GUARD, "--attribute", KEY],
                           capture_output=True, text=True, env=child_env(home))
        check("attribute-gone: exits 0", a.returncode == 0, (a.stderr or "")[:200])
        check("attribute-gone: prints nothing", a.stdout == "", repr(a.stdout[:200]))
        check("attribute-gone: writes no sidecar", not os.path.exists(side))
        check("attribute-gone: ledger-budget.py does not exist beside guard.py",
              not os.path.exists(os.path.join(os.path.dirname(GUARD), "ledger-budget.py")))
    finally:
        shutil.rmtree(home, ignore_errors=True)


# ------------- change 8: the resumed thread's own words must not scroll off the tail
# Measured 18 Sep 2026 on his real ledger: a pickup of "Nova Calc" or "Harbor" got 30
# entries and NOT ONE of them belonged to that thread - both had been quiet while the QIX
# and PhotoBox chats filled the shared file. The thread being resumed is exactly the one
# whose requests matter, so it gets its own share of the tail instead of the last 30 overall.
def test_a_quiet_thread_still_gets_its_own_words():
    """A thread that has been quiet while its neighbours talked must still inherit its
    own open requests - that is the entire job of handing the tail over."""
    home = make_home({})
    try:
        started = time.time() - 7200
        write_chat_msgs(home, "guardaaaa", started, ["the quiet thread's open request"])
        write_chat_msgs(home, "harborbbb", started + 10,
                        ["loud chat message %d" % i for i in range(40)])
        expect_clean(run_stop(home, "guardaaaa"), "quiet-a")
        expect_clean(run_stop(home, "harborbbb"), "quiet-b")
        for sid, body in (("guardaaaa", "HANDOFF LABEL: Context Guard -2 (18 Sep)\n\n# n\n"),
                          ("harborbbb", "HANDOFF LABEL: Harbor -4 (18 Sep)\n\n# n\n")):
            with open(os.path.join(home, ".claude", "handoff",
                                   KEY + "." + sid[:8] + ".md"), "w", encoding="utf-8") as f:
                f.write(body)
        p = run(home, "freshchat", "Context Guard -2 (18 Sep)")
        expect_clean(p, "quiet")
        ctx = context_of(p)
        check("quiet: the quiet thread's own request survived 40 louder ones",
              "the quiet thread's open request" in ctx, repr(ctx[-300:]))
        check("quiet: and it is filed under this thread",
              "THIS THREAD" in ctx
              and ctx.find("the quiet thread's open request") < ctx.find("OTHER CHATS"),
              "at=%d split=%d" % (ctx.find("the quiet thread's open request"),
                                  ctx.find("OTHER CHATS")))
        check("quiet: the noisy chat is still handed over too",
              "loud chat message 39" in ctx)
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_an_unattributed_project_still_gets_a_full_tail():
    """Giving the resumed thread its own share must not shrink what a project with no
    attribution at all receives - most projects on the machine have none yet."""
    home = make_home({"%s.oldchatx.md" % KEY: NOTE_CTX})
    try:
        write_old_ledger(home, [("2026-09-17 10:%02d:00" % i, "old request %d" % i)
                                for i in range(40)])
        write_transcript(home, "freshchat", 1000)
        p = run(home, "freshchat", "context guard")
        expect_clean(p, "full-tail")
        ctx = context_of(p)
        n = ctx.count("old request ")
        check("full-tail: still hands over the usual 30 entries", n == 30, "got %d" % n)
        check("full-tail: the newest of them is there", "old request 39" in ctx)
    finally:
        shutil.rmtree(home, ignore_errors=True)


# --------------------- change 9: a counter with no event under it reports a fake
def test_bash_output_files_are_not_reported_as_background_jobs():
    """Measured 18 Sep 2026 in a live chat: the guard announced "THIS SESSION HAS LAUNCHED
    2 BACKGROUND JOB(S)" and BOTH ids were ordinary Bash tool output - one a large result
    the app had spilled to disk, the other the command running at that moment. Nothing had
    been backgrounded at all. Machine-wide there are 70 files under tasks/ and every single
    one is a .output, so nothing on disk tells a real background job from a foreground
    command. [[counters-must-be-measured]]"""
    home = make_home({})
    tmp = throwaway_dir("guardtemp-")
    try:
        sid = "jobschat"
        write_transcript(home, sid, 240_000)
        d = os.path.join(tmp, "claude", "D--Claude", sid, "tasks")
        os.makedirs(d)
        for n in ("bpfx450ba.output", "bzli9m01q.output"):
            with open(os.path.join(d, n), "w", encoding="utf-8") as f:
                f.write("ordinary bash output")
        payload = {"session_id": sid,
                   "transcript_path": os.path.join(home, ".claude", "projects", KEY,
                                                   sid + ".jsonl"),
                   "cwd": CWD, "prompt": "carry on",
                   "hook_event_name": "UserPromptSubmit"}
        env = child_env(home)
        env["TEMP"] = tmp
        env["TMP"] = tmp
        p = subprocess.run([sys.executable, GUARD, "--size"], input=json.dumps(payload),
                           capture_output=True, text=True, env=env)
        expect_clean(p, "fakejobs")
        ctx = context_of(p)
        check("fakejobs: the warning still fired", "THIS CHAT IS NOW" in ctx, repr(ctx[:120]))
        check("fakejobs: ordinary bash output is not called a background job",
              "BACKGROUND JOB" not in ctx, repr(ctx[-400:]))
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------- change 10: enforce the heredoc rule instead of asking for it
# His ask, 17 Sep 2026: "Heredoc choked on the content. Writing the note with the file tool
# instead. i see this a lot can we fix this "Heredoc "". The previous chat answered it with
# ~/.claude/CLAUDE.md. Measured 18 Sep 2026, that was NOT enough: 2056 of 14043 Bash calls in
# this folder carry a heredoc, and AFTER the CLAUDE.md existed three separate chats still
# used one - 81e1b972, feb0c0b7 and the chat that measured it. An instruction the model may
# reconsider is not the same as a rule the harness applies. [[automate-it-dont-tell-him]]
def run_bash_hook(home, cmd, sid="heredocchat"):
    """Fire the PreToolUse hook the way Claude Code does for a Bash call."""
    payload = {"session_id": sid,
               "transcript_path": os.path.join(home, ".claude", "projects", KEY,
                                               sid + ".jsonl"),
               "cwd": CWD, "hook_event_name": "PreToolUse", "tool_name": "Bash",
               "tool_input": {"command": cmd}}
    env = child_env(home)
    return subprocess.run([sys.executable, GUARD, "--bash"], input=json.dumps(payload),
                          capture_output=True, text=True, env=env)


def denied(proc):
    """The reason the PreToolUse hook refused the call, or "" if it allowed it."""
    out = (proc.stdout or "").strip()
    if not out:
        return ""
    try:
        h = json.loads(out).get("hookSpecificOutput", {})
    except Exception:
        return ""
    return h.get("permissionDecisionReason", "") if h.get(
        "permissionDecision") == "deny" else ""


def test_a_heredoc_writing_a_file_is_blocked():
    """The shape that mangles his files: quoted heredocs are not reliably literal here, so
    backslash-r collapses into a real CR and em-dashes come out wrong."""
    home = make_home({})
    try:
        p = run_bash_hook(home, "cd /d/x && cat > OPEN-REQUESTS.md <<'MD'\nsome - content\nMD")
        expect_clean(p, "heredoc-file")
        why = denied(p)
        check("heredoc-file: refused", why != "", repr((p.stdout or "")[:200]))
        check("heredoc-file: and says to use the Write tool instead",
              "Write" in why, repr(why[:200]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_a_heredoc_feeding_a_script_to_python_is_blocked():
    """His CLAUDE.md: "Need a script? Write it with the Write tool, then run it with Bash."
    This is the shape that survived the CLAUDE.md in three separate chats."""
    home = make_home({})
    try:
        p = run_bash_hook(home, "cd \"/d/AI Projects/X\" && python - <<'PY'\nprint(1)\nPY")
        expect_clean(p, "heredoc-script")
        check("heredoc-script: refused", denied(p) != "", repr((p.stdout or "")[:200]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_a_small_data_heredoc_is_left_alone():
    """The false positive the previous chat warned about. A heredoc handing a few lines of
    DATA to a program is not file content and must not be refused."""
    home = make_home({})
    try:
        p = run_bash_hook(home, "sqlite3 app.db <<'SQL'\nselect count(*) from pets;\nSQL")
        expect_clean(p, "heredoc-data")
        check("heredoc-data: allowed", denied(p) == "", repr(denied(p)[:200]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_stderr_redirection_is_not_mistaken_for_writing_a_file():
    """Measured while building this: a naive "does it contain >" rule called
    `... 2>/dev/null | head -1` a file write. 2> and >&2 are not file content."""
    home = make_home({})
    try:
        p = run_bash_hook(home, "grep -c x f 2>/dev/null; sqlite3 d <<'SQL'\nselect 1;\nSQL")
        expect_clean(p, "heredoc-stderr")
        check("heredoc-stderr: 2>/dev/null is not a file write", denied(p) == "",
              repr(denied(p)[:200]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_an_ordinary_command_is_never_touched():
    """This hook runs before EVERY Bash call in every chat on the machine. A command with no
    heredoc at all must cost nothing and say nothing."""
    home = make_home({})
    try:
        p = run_bash_hook(home, "git status && python test_guard.py | tail -3")
        expect_clean(p, "heredoc-none")
        check("heredoc-none: allowed", denied(p) == "")
        check("heredoc-none: and stays completely silent",
              (p.stdout or "").strip() == "", repr((p.stdout or "")[:200]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_heredoc_guard_has_an_off_switch():
    """Standing rule: anything automated leaves him an override.
    [[automate-it-dont-tell-him]]"""
    home = make_home({})
    try:
        state = os.path.join(home, ".claude", "context-guard")
        os.makedirs(state, exist_ok=True)
        with open(os.path.join(state, "no-heredoc-guard"), "w") as f:
            f.write("")
        p = run_bash_hook(home, "cat > f.md <<'MD'\nx\nMD")
        expect_clean(p, "heredoc-off")
        check("heredoc-off: the switch really switches it off", denied(p) == "",
              repr(denied(p)[:200]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_heredoc_guard_can_be_overridden_per_command():
    """A hard wall would be worse than the bug the day a heredoc is genuinely the right
    tool. One marker in the command allows that one call, and the refusal names it."""
    home = make_home({})
    try:
        p = run_bash_hook(home, "cat > f.md <<'MD'  # heredoc-ok\nx\nMD")
        expect_clean(p, "heredoc-escape")
        check("heredoc-escape: the marker allows that one call", denied(p) == "",
              repr(denied(p)[:200]))
        q = run_bash_hook(home, "cat > f.md <<'MD'\nx\nMD")
        check("heredoc-escape: and the refusal tells him the marker exists",
              "heredoc-ok" in denied(q), repr(denied(q)[:300]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


# --------------- change 9: scope a thread's share by SIZE, and disclose what was cut
# 18 Sep 2026, measured on his real 426-entry ledger: the tail gave each thread a flat
# 30-entry window, so "harbor" - which owns 278 entries - handed a pickup 30 and dropped
# 248 of his own words with no sign they existed. Meanwhile FIVE of the six threads would
# have fitted their ENTIRE history in under 7.3 KB and were being cut by a count that has
# nothing to do with size. A recency window also hides a thread's OLDEST requests, which
# are the ones most likely to be still open - Harbor's player glitch, asked twice and
# still unfixed, is exactly that shape.
def write_thread_ledger(home, per_chat):
    """A ledger in TODAY's format - every entry carries "- chat <sid8>", oldest first."""
    lp = os.path.join(home, ".claude", "handoff", KEY + ".requests.md")
    n = 0
    with open(lp, "w", encoding="utf-8") as f:
        f.write("# What the user actually asked for - his own words" + chr(10))
        for sid8, bodies in per_chat.items():
            for b in bodies:
                n += 1
                ts = "2026-09-%02d %02d:%02d:00" % (1 + (n // 1440) % 28,
                                                    (n // 60) % 24, n % 60)
                f.write(chr(10) + "### " + ts + " - chat " + sid8 + chr(10)
                        + chr(10) + b + chr(10))
    return lp


def note_for(home, sid8, label):
    """The note a chat wrote - which is the only thing that gives it a thread name."""
    p = os.path.join(home, ".claude", "handoff", KEY + "." + sid8 + ".md")
    with open(p, "w", encoding="utf-8") as f:
        f.write("HANDOFF LABEL: " + label + chr(10) + chr(10) + "# n" + chr(10))
    return p


def this_thread_section(ctx):
    """Only the part of the injection claimed for the thread being picked up. Asserting
    against the whole blob is how an absence test matches text that was never its
    subject - the label a pickup is summoned by is echoed in several other places."""
    i = ctx.find("-- THIS THREAD")
    if i < 0:
        return ""
    j = ctx.find("-- OTHER CHATS", i)
    return ctx[i:j if j > -1 else len(ctx)]


def pickup(home, bodies, sid8="guardaaa", label="Context Guard -2 (18 Sep)"):
    # sid8 is EIGHT characters, because that is what LEDGER_CHAT_RE matches and what a
    # session id truncates to. A nine-character id here made every one of these tests fail
    # with an empty section - the right verdict for the wrong reason, which is worse than
    # a pass, because it would have "proved" whatever the fix did next.
    """Build a one-thread folder and pick that thread up. Returns the injection."""
    write_thread_ledger(home, {sid8: bodies})
    note_for(home, sid8, label)
    write_transcript(home, "freshchat", 1000)
    p = run(home, "freshchat", label)
    expect_clean(p, "scope")
    return whole_context(home, p)


def test_a_thread_whose_history_fits_inherits_all_of_it():
    """Five of his six threads fit entirely in a few KB. A flat count of 30 truncated them
    for no reason: context guard owns 40 real requests and a pickup saw 22."""
    home = make_home({})
    try:
        ctx = pickup(home, ["guard request %02d - a real thing he asked for" % i
                            for i in range(45)])
        sec = this_thread_section(ctx)
        check("scope-fits: the oldest of 45 short entries is still handed over",
              "guard request 00" in sec, repr(sec[:300]))
        check("scope-fits: and so is the newest",
              "guard request 44" in sec, repr(sec[-300:]))
        missing = [i for i in range(45) if "guard request %02d" % i not in sec]
        check("scope-fits: none of the 45 are dropped", not missing, "missing %r" % missing)
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_a_long_thread_is_told_what_was_left_out():
    """Silent truncation is the actual contract violation: a Harbor pickup had no way to
    learn that 248 more of his requests existed."""
    home = make_home({})
    try:
        lp = os.path.join(home, ".claude", "handoff", KEY + ".requests.md")
        ctx = pickup(home, ["bulk request %03d %s" % (i, "y" * 600) for i in range(200)])
        with open(lp, "rb") as f:
            after = f.read()
        sec = this_thread_section(ctx)
        check("scope-cut: the pickup is told entries were left out",
              "are not shown" in sec, repr(sec[:400]))
        check("scope-cut: and how many",
              any(str(n) in sec for n in range(150, 200)), repr(sec[:400]))
        check("scope-cut: and how to find them - by chat id",
              "guardaaa" in sec, repr(sec[:400]))
        check("scope-cut: reading the tail never edits the ledger",
              len(after) > 100000 and after.count(b"bulk request") == 200,
              "%d bytes" % len(after))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_a_long_thread_keeps_its_oldest_requests():
    """A newest-first window is structurally blind to the requests most likely to be still
    open. His own standing rule: however old it is, if it was never delivered it is OPEN."""
    home = make_home({})
    try:
        bodies = ["THE-VERY-FIRST-THING-HE-ASKED and it was never done"]
        bodies += ["later request %03d %s" % (i, "z" * 600) for i in range(200)]
        sec = this_thread_section(pickup(home, bodies))
        check("scope-oldest: the thread's first request survives 200 newer ones",
              "THE-VERY-FIRST-THING-HE-ASKED" in sec, repr(sec[:400]))
        check("scope-oldest: and the newest is still there too",
              "later request 199" in sec, repr(sec[-400:]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_a_summon_label_is_not_carried_as_a_request():
    """Six of context guard's 48 entries are just the label he typed to SUMMON a note.
    They are his words so the ledger keeps them - but they are not requests, and spending
    a pickup's slots on them is spending them on nothing."""
    home = make_home({})
    try:
        sec = this_thread_section(pickup(
            home, ["Context Guard -4 (18 Sep)", "Context guard",
                   "a genuine request about the guard"]))
        check("scope-label: the real request is carried",
              "a genuine request about the guard" in sec, repr(sec[:400]))
        check("scope-label: the summon label is not",
              "Context Guard -4 (18 Sep)" not in sec, repr(sec[:400]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_old_noise_in_the_ledger_is_not_carried_into_a_pickup():
    """The APPEND path has dropped [Image: ...] and interruption markers since the filter
    was written - but 12 of them are already sitting in his real ledger from before it
    existed, and the VIEW never checked. Measured 18 Sep 2026: 1,321 chars of them, and
    today none falls inside a thread's window, so this costs nothing YET. It is the class
    that matters - an old artifact surfacing in the oldest slice would be handed to a
    fresh chat as though he had asked for it.

    The genuine request is asserted PRESENT on purpose: with only noise in the ledger the
    section comes back empty and every "not in" below would pass for the wrong reason."""
    home = make_home({})
    try:
        sec = this_thread_section(pickup(
            home, ["[Image: original 1080x2400, displayed at 900x2000. Multiply "
                   "coordinates by 1.2 to get the original]",
                   "[Request interrupted by user]",
                   "Continue from where you left off.",
                   "a genuine request about the guard"]))
        check("scope-noise: the real request is still carried",
              "a genuine request about the guard" in sec, repr(sec[:400]))
        check("scope-noise: an old image artifact is not",
              "[Image:" not in sec, repr(sec[:400]))
        check("scope-noise: an interruption marker is not",
              "[Request interrupted" not in sec, repr(sec[:400]))
        check("scope-noise: a bare resume is not",
              "Continue from where you left off." not in sec, repr(sec[:400]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_thread_budget_is_characters_not_entries():
    """The injection has a hard cap. A handful of very long entries must be cut by SIZE -
    a count-based window would have waved 16 KB through because it was only 8 entries."""
    budget = guard_constant("LEDGER_OWN_CHARS")
    each = 1900
    n = max(8, int(budget * 2 // each) + 2)     # comfortably more than twice the budget
    home = make_home({})
    try:
        sec = this_thread_section(pickup(
            home, ["BULK-%02d %s" % (i, "q" * each) for i in range(n)]))
        check("scope-budget: the thread's share is bounded by size",
              0 < len(sec) < budget + 4000, "%d chars, budget %d" % (len(sec), budget))
        check("scope-budget: the newest is kept", "BULK-%02d" % (n - 1) in sec,
              repr(sec[:200]))
        check("scope-budget: the oldest is kept", "BULK-00" in sec, repr(sec[:200]))
        check("scope-budget: and a middle one is cut", "BULK-%02d" % (n // 2) not in sec,
              "%d chars of %d offered" % (len(sec), n * each))
    finally:
        shutil.rmtree(home, ignore_errors=True)


# ------------------------------------ change 8: away mode, for when he is on the phone
# 18 Sep 2026, his words: "we need to have an exception if it was ever addresed that am
# currently remote/afk from the console and responding from the phone on claude mobile app
# so it would understand that i cant open a new session thus the naggin should stop until
# i return". Telling a man on a phone to open a new chat in a folder he cannot reach is
# the one piece of advice that is guaranteed to be useless.
#
# He chose EXPLICIT toggling over phrase-sniffing, so the whole prompt must BE the phrase -
# a sentence that merely mentions afk is left alone. And he chose "save silently, never
# nag": the note is still written, because a chat that dies while he is away with no note
# is the disaster the whole tool exists to prevent. Only the part he cannot act on goes.

def away_flag(home):
    return os.path.join(home, ".claude", "context-guard", "away")


def arm_away(home):
    d = os.path.join(home, ".claude", "context-guard")
    if not os.path.isdir(d):
        os.makedirs(d)
    with open(away_flag(home), "w", encoding="utf-8") as f:
        f.write("2026-09-18T12:00:00\n")


def sysmsg(proc):
    """The line the USER actually sees, or "" when the hook said nothing to him."""
    out = (proc.stdout or "").strip()
    if not out:
        return ""
    try:
        return json.loads(out).get("systemMessage", "") or ""
    except Exception:
        return ""


# ------------------------------------ the installer, which writes into someone ELSE'S home
INSTALL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "install.py")
FOREIGN = {"theme": "dark",
           "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo not-ours"}]}]}}


def run_install(home, *args):
    return subprocess.run([sys.executable, INSTALL, "--home", home] + list(args),
                          capture_output=True, text=True)


def settings_file(home):
    return os.path.join(home, ".claude", "settings.json")


def seed_settings(home, data):
    with open(settings_file(home), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def read_settings(home):
    with open(settings_file(home), encoding="utf-8") as f:
        return json.load(f)


def ours(entries):
    return [e for e in entries
            if any("guard.py" in (h.get("command") or "") or "audit.py" in (h.get("command") or "")
                   for h in e.get("hooks") or [])]


def test_install_is_idempotent():
    """Run it twice and you must not end up running every hook twice. This is the whole
    reason it matches on the script name rather than on position in the list."""
    home = make_home({})
    try:
        seed_settings(home, {})
        run_install(home)
        p = run_install(home)
        expect_clean(p, "install-twice")
        check("install-twice: second run changed nothing",
              "Already up to date" in (p.stdout or ""), repr((p.stdout or "")[:200]))
        s = read_settings(home)
        check("install-twice: one UserPromptSubmit hook, not two",
              len(ours(s["hooks"]["UserPromptSubmit"])) == 1,
              json.dumps(s["hooks"]["UserPromptSubmit"])[:300])
        check("install-twice: both PreToolUse matchers present exactly once",
              sorted(e.get("matcher") for e in ours(s["hooks"]["PreToolUse"])) == ["Bash"],
              json.dumps(s["hooks"]["PreToolUse"])[:300])
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_install_wires_the_bootstrap():
    """A feature that is built and tested but never wired into settings.json never runs.

    Asserted on the COMMAND, not the count: SessionStart already carries audit.py --alert,
    so "there is a SessionStart hook" was true before the bootstrap existed."""
    home = make_home({})
    try:
        seed_settings(home, {})
        p = run_install(home)
        expect_clean(p, "install-bootstrap")
        s = read_settings(home)
        cmds = [h.get("command") or ""
                for e in s.get("hooks", {}).get("SessionStart", [])
                for h in e.get("hooks") or []]
        check("install-bootstrap: --bootstrap is wired to SessionStart",
              any("--bootstrap" in c for c in cmds), json.dumps(cmds)[:300])
        check("install-bootstrap: the existing --alert hook is still there too",
              any("--alert" in c for c in cmds), json.dumps(cmds)[:300])
        run_install(home)
        s2 = read_settings(home)
        cmds2 = [h.get("command") or ""
                 for e in s2.get("hooks", {}).get("SessionStart", [])
                 for h in e.get("hooks") or []]
        check("install-bootstrap: installing twice does not wire it twice",
              len([c for c in cmds2 if "--bootstrap" in c]) == 1, json.dumps(cmds2)[:300])
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_uninstall_removes_the_bootstrap_too():
    """FLAGS is what uninstall matches on. A flag missing from it is a hook he cannot
    remove with the tool that installed it - and he would have to find it by hand."""
    home = make_home({})
    try:
        seed_settings(home, {})
        run_install(home)
        run_install(home, "--uninstall")
        s = read_settings(home)
        cmds = [h.get("command") or ""
                for e in s.get("hooks", {}).get("SessionStart", [])
                for h in e.get("hooks") or []]
        check("uninstall-bootstrap: the bootstrap hook is gone",
              not any("--bootstrap" in c for c in cmds), json.dumps(cmds)[:300])
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_install_keeps_other_peoples_settings_and_hooks():
    """A friend's settings.json is not ours to rewrite. If this ever clobbers their own
    hooks the tool has done more damage than the token bill it was meant to save."""
    home = make_home({})
    try:
        seed_settings(home, FOREIGN)
        p = run_install(home)
        expect_clean(p, "install-merge")
        s = read_settings(home)
        check("install-merge: unrelated settings survive", s.get("theme") == "dark", repr(s.get("theme")))
        stop = [e for e in s["hooks"]["Stop"] if not ours([e])]
        check("install-merge: their own Stop hook survives",
              any("echo not-ours" in (h.get("command") or "")
                  for e in stop for h in e.get("hooks") or []), json.dumps(s["hooks"]["Stop"])[:300])
        check("install-merge: and ours was added alongside it",
              len(ours(s["hooks"]["Stop"])) == 1, json.dumps(s["hooks"]["Stop"])[:300])
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_install_dry_run_writes_nothing():
    home = make_home({})
    try:
        seed_settings(home, FOREIGN)
        before = open(settings_file(home), encoding="utf-8").read()
        p = run_install(home, "--dry-run")
        expect_clean(p, "install-dry")
        check("install-dry: printed a diff", "+" in (p.stdout or "") and "guard.py" in (p.stdout or ""),
              repr((p.stdout or "")[:200]))
        check("install-dry: the file is byte-for-byte unchanged",
              open(settings_file(home), encoding="utf-8").read() == before, "file changed")
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_install_backs_the_file_up_before_writing():
    home = make_home({})
    try:
        seed_settings(home, FOREIGN)
        run_install(home)
        backups = [f for f in os.listdir(os.path.join(home, ".claude"))
                   if f.startswith("settings.json.backup-")]
        check("install-backup: a timestamped backup was left behind", len(backups) == 1, repr(backups))
        if backups:
            with open(os.path.join(home, ".claude", backups[0]), encoding="utf-8") as f:
                check("install-backup: and it holds the ORIGINAL", json.load(f) == FOREIGN, "differs")
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_uninstall_removes_only_our_hooks():
    home = make_home({})
    try:
        seed_settings(home, FOREIGN)
        run_install(home)
        p = run_install(home, "--uninstall")
        expect_clean(p, "uninstall")
        s = read_settings(home)
        check("uninstall: ours are gone",
              ours(s.get("hooks", {}).get("Stop", [])) == [], json.dumps(s.get("hooks"))[:300])
        check("uninstall: theirs is untouched",
              any("echo not-ours" in (h.get("command") or "")
                  for e in s["hooks"]["Stop"] for h in e.get("hooks") or []),
              json.dumps(s.get("hooks"))[:300])
        check("uninstall: unrelated settings survive", s.get("theme") == "dark", repr(s.get("theme")))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def old_install_with_reread(foreign_read=True):
    """Settings as an OLDER install wrote them: our entries plus the retired --reread one,
    beside a foreign PreToolUse/Read hook that must survive."""
    sys.path.insert(0, os.path.dirname(INSTALL))
    try:
        import install as inst
    finally:
        sys.path.pop(0)
    entries = [inst.desired_entry(m, s, f, t, st) for _ev, m, s, f, t, st in inst.HOOKS]
    hooks = {}
    for (ev, *_rest), entry in zip(inst.HOOKS, entries):
        hooks.setdefault(ev, []).append(entry)
    hooks.setdefault("PreToolUse", []).insert(
        0, inst.desired_entry("Read", inst.GUARD, "--reread", 10, None))
    if foreign_read:
        hooks["PreToolUse"].append(
            {"matcher": "Read", "hooks": [{"type": "command", "command": "echo other-tool"}]})
    return {"hooks": hooks}


def all_commands(settings):
    return [h.get("command") or "" for lst in settings.get("hooks", {}).values()
            for e in lst for h in e.get("hooks") or []]


def test_install_drops_a_stale_reread_hook():
    home = make_home({})
    try:
        seed_settings(home, old_install_with_reread())
        p = run_install(home)
        expect_clean(p, "stale-reread")
        s = read_settings(home)
        check("stale-reread: no command mentions --reread",
              not any("--reread" in c for c in all_commands(s)), json.dumps(s["hooks"])[:400])
        check("stale-reread: the foreign Read hook is kept, unchanged",
              {"matcher": "Read", "hooks": [{"type": "command", "command": "echo other-tool"}]}
              in s["hooks"]["PreToolUse"], json.dumps(s["hooks"]["PreToolUse"])[:400])
        check("stale-reread: exactly one --bash PreToolUse entry",
              sum("--bash" in c for c in all_commands({"hooks": {"PreToolUse": s["hooks"]["PreToolUse"]}})) == 1,
              json.dumps(s["hooks"]["PreToolUse"])[:400])
        p2 = run_install(home)
        check("stale-reread: a second run says Already up to date",
              "Already up to date" in (p2.stdout or ""), repr((p2.stdout or "")[:200]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_uninstall_also_removes_a_stale_reread_hook():
    home = make_home({})
    try:
        seed_settings(home, old_install_with_reread())
        p = run_install(home, "--uninstall")
        expect_clean(p, "stale-reread-uninstall")
        s = read_settings(home)
        check("stale-reread-uninstall: no --reread left",
              not any("--reread" in c for c in all_commands(s)), json.dumps(s.get("hooks"))[:400])
        check("stale-reread-uninstall: the foreign hook is kept",
              any("echo other-tool" in c for c in all_commands(s)), json.dumps(s.get("hooks"))[:400])
        check("stale-reread-uninstall: none of ours remain",
              not any("guard.py" in c or "audit.py" in c for c in all_commands(s)),
              json.dumps(s.get("hooks"))[:400])
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_a_leftover_reread_hook_is_a_silent_no_op():
    """A settings.json not yet re-installed still calls --reread on every Read. It must do
    nothing: rc 0, no output, no state written."""
    home = make_home({})
    try:
        img = os.path.join(home, "pic.png")
        with open(img, "wb") as f:
            f.write(b"\x89PNG not really")
        payload = {"session_id": "rereadchat", "cwd": CWD, "hook_event_name": "PreToolUse",
                   "tool_name": "Read", "tool_input": {"file_path": img}}
        for i in range(3):
            p = subprocess.run([sys.executable, GUARD, "--reread"], input=json.dumps(payload),
                               capture_output=True, text=True, env=child_env(home))
            check("leftover-reread: run %d rc 0, no stdout" % (i + 1),
                  p.returncode == 0 and (p.stdout or "") == "",
                  "rc=%s out=%r err=%r" % (p.returncode, p.stdout, (p.stderr or "")[:200]))
        sp = os.path.join(home, ".claude", "context-guard", "rereadchat.json")
        reads = None
        if os.path.exists(sp):
            with open(sp, encoding="utf-8") as f:
                reads = json.load(f).get("reads")
        check("leftover-reread: no reads key in the state file", reads is None, repr(reads))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_install_refuses_to_touch_a_settings_file_it_cannot_parse():
    """Overwriting a file you failed to read is how an installer eats someone's config."""
    home = make_home({})
    try:
        with open(settings_file(home), "w", encoding="utf-8") as f:
            f.write("{ this is not json ")
        p = run_install(home)
        check("install-badjson: refused", p.returncode == 2, "rc=%d" % p.returncode)
        check("install-badjson: said why", "not valid JSON" in (p.stdout or ""),
              repr((p.stdout or "")[:200]))
        check("install-badjson: left the file alone",
              open(settings_file(home), encoding="utf-8").read() == "{ this is not json ",
              "file was rewritten")
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_suite_cannot_reach_a_real_home_on_any_platform():
    """The defect that blocked sharing this with anyone on a Mac or Linux box.

    Checking only the branch this machine happens to take would prove nothing about the
    branch that was broken, so both are driven here by faking os.name. Windows must LOSE
    its HOME (Git Bash exports one) and POSIX must GAIN one (expanduser falls back to the
    passwd database when HOME is absent, i.e. straight into the real account)."""
    real = os.name
    try:
        os.name = "posix"
        e = child_env("/tmp/throwaway")
        check("isolation/posix: HOME is set to the throwaway home",
              e.get("HOME") == "/tmp/throwaway", repr(e.get("HOME")))
        os.name = "nt"
        e = child_env("C:/throwaway")
        check("isolation/nt: HOME is removed so USERPROFILE wins",
              "HOME" not in e, repr(e.get("HOME")))
        check("isolation/nt: USERPROFILE is the throwaway home",
              e.get("USERPROFILE") == "C:/throwaway", repr(e.get("USERPROFILE")))
    finally:
        os.name = real


def test_away_is_armed_only_by_an_explicit_phrase():
    """He picked explicit-only over auto-detection, so a sentence that happens to contain
    the word must NOT arm it - otherwise 'fix this before i go afk' silently disables the
    guard for the rest of the chat and he never finds out."""
    home = make_home({})
    try:
        write_chat(home, "awayarm", time.time() - 3600)
        p = run(home, "awayarm", "i will be afk later, fix the bug first")
        expect_clean(p, "away-arm")
        check("away-arm: a sentence merely mentioning afk does NOT arm it",
              not os.path.exists(away_flag(home)), away_flag(home))
        p = run(home, "awayarm", "afk")
        expect_clean(p, "away-arm")
        check("away-arm: the bare phrase arms it",
              os.path.exists(away_flag(home)), away_flag(home))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_saying_back_disarms_away():
    home = make_home({})
    try:
        write_chat(home, "awayback", time.time() - 3600)
        arm_away(home)
        p = run(home, "awayback", "i'm back")
        expect_clean(p, "away-back")
        check("away-back: he is at the console again",
              not os.path.exists(away_flag(home)), away_flag(home))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_back_is_ignored_when_he_was_never_away():
    """'back' is only read as a toggle while away mode is actually on. Otherwise a perfectly
    ordinary 'back' - go back, revert that - would be swallowed as a command."""
    home = make_home({})
    try:
        write_chat(home, "awaynever", time.time() - 3600)
        p = run(home, "awaynever", "back")
        expect_clean(p, "away-never")
        check("away-never: did not arm away mode",
              not os.path.exists(away_flag(home)), away_flag(home))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_away_silences_the_visible_nag_but_still_orders_the_save():
    """The trap this test is built to avoid: if away mode simply returned early, "no nag"
    would pass for the wrong reason and the note would silently stop being written. So the
    SAME chat is run twice - once at the console, where the nag MUST appear, and once away,
    where it must not - and the away run must still carry the order to write the note."""
    home = make_home({})
    try:
        write_big_chat(home, "awayloud", time.time() - 3600, 250_000)
        p = run(home, "awayloud", "carry on with the build")
        expect_clean(p, "away-quiet/control")
        check("away-quiet: CONTROL - at the console the nag really does fire",
              sysmsg(p) != "", repr(sysmsg(p)[:160]))
    finally:
        shutil.rmtree(home, ignore_errors=True)

    home = make_home({})
    try:
        write_big_chat(home, "awayquiet", time.time() - 3600, 250_000)
        arm_away(home)
        p = run(home, "awayquiet", "carry on with the build")
        expect_clean(p, "away-quiet")
        ctx = context_of(p)
        check("away-quiet: nothing was said to him", sysmsg(p) == "", repr(sysmsg(p)[:160]))
        check("away-quiet: the note is STILL ordered", "handoff note" in ctx.lower(),
              repr(ctx[:200]))
        # Grep for the INSTRUCTION, not the words. The away text necessarily contains the
        # phrase "cannot open a new chat", so a bare substring test would match the very
        # sentence that proves the fix works - the same trap that has bitten this suite
        # four times: a check that reads the prose instead of asking the code.
        check("away-quiet: he is not handed a label to type",
              "open a new chat here and just say" not in ctx.lower()
              and "quote him the exact label" not in ctx.lower(), repr(ctx[-400:]))
        check("away-quiet: and it says WHY it went quiet", "away mode is on" in ctx.lower(),
              repr(ctx[-400:]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_ceiling_still_saves_while_away():
    """Past the ceiling the note matters MORE while he is away, not less - he is not there
    to rescue the chat. It must still refuse to close; it just must not end with advice he
    cannot follow."""
    home = make_home({})
    try:
        write_big_chat(home, "awayceil", time.time() - 3600, 400_000)
        arm_away(home)
        p = run_stop(home, "awayceil")
        expect_clean(p, "away-ceiling")
        r = blocked(p)
        check("away-ceiling: still refused to close without the note", bool(r),
              repr((p.stdout or "")[:200]))
        check("away-ceiling: still names the note it wants",
              KEY + ".awayceil" in r.replace(chr(92), "/"), repr(r[:300]))
        check("away-ceiling: does not send him to a new chat",
              "tell the user in one line to open a new chat" not in r.lower()
              and "quote him the exact label" not in r.lower(), repr(r[:600]))
        check("away-ceiling: and it says why", "away mode is on" in r.lower(), repr(r[:600]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


# ---------------------------------------------------------------------------
# THE AFK-OFF BUG, measured 20-21 Sep 2026 on his own session 69ba63cb.
#
# He said "afk off" in a chat sitting at 227k and was told nothing at all. Two
# independent defects, and they compound into permanent silence:
#
#   1. the branch that clears away mode PRINTED AND RETURNED, so the size check
#      below it never ran on the one turn whose whole job is to re-arm the alarm.
#      His log proves it: every other turn that night wrote a "size: ctx=..."
#      line and the un-mute turn wrote none at all.
#   2. warnings fired WHILE away still moved warned_ctx - they were silent to
#      him, but they spent the watermark, reaching 290007. A compaction then
#      dropped that chat from 290k to 204k, which is nowhere near the 100k
#      re-arm floor, so the mark survived the compaction. due then needed
#      330007 while auto-compact fires at 318500: that chat could never warn
#      again, and never did.
#
# Each test below isolates ONE defect and pins it against a CONTROL run rather
# than against a sentence, so neither can be satisfied by rewording a message.


def seed_state(home, sid, **kw):
    """Plant per-session state the way a previous turn would have left it."""
    p = os.path.join(home, ".claude", "context-guard", sid[:40] + ".json")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(kw, f)
    return p


def read_state(home, sid):
    p = os.path.join(home, ".claude", "context-guard", sid[:40] + ".json")
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def one_json(proc):
    """The hook contract is ONE json object on stdout. sysmsg() and context_of() both
    json.loads() the WHOLE of stdout, so a branch that prints twice is indistinguishable
    from a branch that said nothing. A fix that falls through by simply printing again
    would pass a "did it warn" assertion in isolation and be invisible to him in real
    life; this is the check that catches it."""
    out = (proc.stdout or "").strip()
    if not out:
        return False
    try:
        json.loads(out)
        return True
    except Exception:
        return False


def test_coming_back_from_away_re_arms_on_that_same_turn():
    """DEFECT 1. The un-mute turn must be able to warn, because it is the turn he is
    standing there for. Pinned against a CONTROL: the same chat, same size, at the
    console, must produce a warning - and the un-mute turn must carry that SAME warning
    plus the away-off confirmation. Asserting the control's sentence is a substring of
    the un-mute turn's means neither can be reworded out of agreement."""
    home = make_home({})
    try:
        write_big_chat(home, "awayctl", time.time() - 3600, 250_000)
        c = run(home, "awayctl", "carry on with the build")
        expect_clean(c, "away-off-warns/control")
        control = sysmsg(c)
        check("away-off-warns: CONTROL - at the console this chat really does warn",
              bool(control), repr((c.stdout or "")[:200]))
    finally:
        shutil.rmtree(home, ignore_errors=True)

    home = make_home({})
    try:
        write_big_chat(home, "awayoff", time.time() - 3600, 250_000)
        arm_away(home)
        p = run(home, "awayoff", "afk off")
        expect_clean(p, "away-off-warns")
        check("away-off-warns: stdout is ONE json object",
              one_json(p), repr((p.stdout or "")[:200]))
        check("away-off-warns: he is still told away mode is off",
              "away mode off" in sysmsg(p).lower(), repr(sysmsg(p)[:200]))
        check("away-off-warns: the SAME turn carries the control's warning too",
              control and control in sysmsg(p), repr(sysmsg(p)[:300]))
        check("away-off-warns: and that turn orders the note",
              "handoff note" in context_of(p).lower(), repr(context_of(p)[:200]))
        check("away-off-warns: away mode really is off afterwards",
              not os.path.exists(away_flag(home)), away_flag(home))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_a_watermark_spent_while_away_does_not_mute_the_chat_forever():
    """DEFECT 1, the other half. Warnings that fired while he was away were never shown
    to him, but they still moved warned_ctx. Coming back must clear that debt, or he is
    silently charged for warnings he never received. Pinned on the STATE, not on prose."""
    home = make_home({})
    try:
        write_big_chat(home, "awaydebt", time.time() - 3600, 250_000)
        arm_away(home)
        seed_state(home, "awaydebt", warned_ctx=290_007)
        p = run(home, "awaydebt", "afk off")
        expect_clean(p, "away-debt")
        st = read_state(home, "awaydebt")
        check("away-debt: the watermark he never saw is not still sitting at 290007",
              st.get("warned_ctx") != 290_007, repr(st))
        check("away-debt: and the un-mute turn was not silenced by it",
              bool(sysmsg(p)), repr((p.stdout or "")[:200]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_a_compaction_re_arms_the_warning_even_far_above_the_floor():
    """DEFECT 2, and it bites chats that never touched away mode. Context only grows
    within an epoch, so a large DROP can only mean the history was summarised - the
    high-water mark no longer describes this chat. The old code re-armed only below the
    100k floor, and his real compaction landed at 204k.

    Pinned against a CONTROL at the same size with no history, because the question is
    not "does it say something" but "does a compacted chat behave like the fresh chat it
    now resembles". 290007 -> 203980 are his real numbers."""
    home = make_home({})
    try:
        write_big_chat(home, "compctl", time.time() - 3600, 243_980)
        c = run(home, "compctl", "carry on")
        expect_clean(c, "compaction-rearm/control")
        control = sysmsg(c)
        check("compaction-rearm: CONTROL - a chat this size with no history warns",
              bool(control), repr((c.stdout or "")[:200]))
    finally:
        shutil.rmtree(home, ignore_errors=True)

    home = make_home({})
    try:
        write_big_chat(home, "compacted", time.time() - 3600, 243_980)
        seed_state(home, "compacted", warned_ctx=290_007)
        p = run(home, "compacted", "carry on")
        expect_clean(p, "compaction-rearm")
        check("compaction-rearm: the compacted chat says what the fresh one says",
              control and control in sysmsg(p), repr(sysmsg(p)[:300]))
        check("compaction-rearm: and the stale high-water mark is gone",
              read_state(home, "compacted").get("warned_ctx") != 290_007,
              repr(read_state(home, "compacted")))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_guidance_forbids_handing_him_a_path_in_both_branches():
    """His decision, 21 Sep 2026: "yes always give me the label never a path line please".

    An AGREEMENT test, not a prose test: the point is that the prohibition rides BOTH
    branches of handoff_steps. Away or at the console, a path is equally useless to him -
    he cannot type one - so a rule present in only one branch is the bug, and that is what
    is asserted. The substring is the shortest stable stem of the rule; the surrounding
    wording is free to change.

    Why it matters: the complaint came from a chat that was NOT writing a note at the
    time, so step 4 alone could never have caught it. The rule has to be standing."""
    stem = "never hand him a filesystem path"

    home = make_home({})
    try:
        write_big_chat(home, "pathrule", time.time() - 3600, 250_000)
        p = run(home, "pathrule", "carry on")
        expect_clean(p, "path-rule/console")
        at_console = context_of(p).lower()
        check("path-rule: the at-console guidance forbids handing him a path",
              stem in at_console, repr(at_console[-400:]))
    finally:
        shutil.rmtree(home, ignore_errors=True)

    home = make_home({})
    try:
        write_big_chat(home, "pathaway", time.time() - 3600, 250_000)
        arm_away(home)
        p = run(home, "pathaway", "carry on")
        expect_clean(p, "path-rule/away")
        away = context_of(p).lower()
        check("path-rule: and so does the away guidance - both branches agree",
              stem in away, repr(away[-400:]))
        check("path-rule: CONTROL - the away branch still does not send him to a new chat",
              "quote him the exact words to type" not in away, repr(away[-400:]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_note_template_names_the_log_it_is_written_from():
    """His decision, 21 Sep 2026 - "yes if it is going to make it better" - asked on its
    own after being carried unasked through two handoffs. Every note re-derived where the
    diagnostics log lives and one of them wrote "guard.log", a file that has never
    existed.

    An AGREEMENT test, not a prose test. Both names are read out of guard.py's OWN source:
    the template guard.py points chats at must name the log guard.py actually writes. A
    hardcoded path in a static markdown file is the same false confidence the line was
    added to remove - it would go stale in silence the day LOG moves - so the test is the
    half that makes the line worth having, not an extra.

    The CONTROL carries as much weight as the assertion: naming a path FOR Claude must
    not weaken the standing rule that HE is never handed one."""
    with open(GUARD, encoding="utf-8") as f:
        src = f.read()
    mlog = re.search(r"^LOG\s*=.*?[\"']([^\"']+\.log)[\"']", src, re.M)
    mtpl = re.search(r"^TEMPLATE\s*=.*?[\"']([^\"']+\.md)[\"']", src, re.M)
    check("template-log: guard.py still names both the log and the template in source",
          bool(mlog) and bool(mtpl),
          "LOG=%r TEMPLATE=%r" % (mlog and mlog.group(1), mtpl and mtpl.group(1)))
    if not (mlog and mtpl):
        return

    path = os.path.join(os.path.dirname(GUARD), mtpl.group(1))
    with open(path, encoding="utf-8") as f:
        tmpl = f.read()
    check("template-log: the note template names the log guard.py actually writes",
          mlog.group(1) in tmpl,
          "guard.py writes %r; %s never names it" % (mlog.group(1), mtpl.group(1)))
    check("template-log: CONTROL - and it still forbids handing HIM a filesystem path",
          "never hand him a filesystem path" in tmpl.lower(), repr(tmpl[-600:]))


def test_growth_within_one_epoch_is_still_not_a_compaction():
    """The other half of defect 2, and the reason it is a threshold and not `ctx < last`.
    A chat that has warned and then grown a little must STAY quiet - re-arming on every
    small wobble would turn the guard back into the nagging it exists to replace. His
    words, 19 Sep: the indicator is once per chat, "every turn would be the very nagging
    away mode exists to stop"."""
    home = make_home({})
    try:
        write_big_chat(home, "epoch", time.time() - 3600, 252_000)
        seed_state(home, "epoch", warned_ctx=250_000)
        p = run(home, "epoch", "carry on")
        expect_clean(p, "epoch-quiet")
        check("epoch-quiet: a chat that warned at 250k does not warn again at 252k",
              sysmsg(p) == "", repr(sysmsg(p)[:200]))
        check("epoch-quiet: and its watermark is untouched",
              read_state(home, "epoch").get("warned_ctx") == 250_000,
              repr(read_state(home, "epoch")))
    finally:
        shutil.rmtree(home, ignore_errors=True)



# ---------------------------------------------------------------------------
# The auto-stub and the early warning. Both exist because of ONE measured
# failure, 19 Sep 2026: chat "Context Guard -10" ran for 19 hours, peaked at
# 132k of context - 38% of the window, nowhere near the 300k ceiling - and so
# never wrote a handoff note at all. He opened a fresh chat, typed its label,
# and was told there was no such saved thread. The ceiling has never once fired
# in ten chats, so a note written only AT the ceiling is a note essentially
# never written. His words: "fix this issue i never want for this to happen
# again".
#
# Declared here as a literal, not imported: this suite drives guard.py as a
# SUBPROCESS, so the marker is part of the on-disk contract between the two.
# If guard.py changes it, these tests must fail.
# ---------------------------------------------------------------------------

STUB_MARKER = "context-guard:auto-stub"


def run_session_end(home, sid):
    """Fire the SessionEnd hook exactly as Claude Code does."""
    payload = {
        "session_id": sid,
        "transcript_path": os.path.join(home, ".claude", "projects", KEY, sid + ".jsonl"),
        "cwd": CWD,
        "hook_event_name": "SessionEnd",
        "reason": "other",
    }
    return subprocess.run([sys.executable, GUARD, "--session-end"],
                          input=json.dumps(payload), capture_output=True, text=True,
                          env=child_env(home))


def stale_chat(home, sid, age_s, size=1_501_000):
    """A chat transcript at least FRESH_BYTES big whose file was last touched `age_s` ago."""
    p = write_big_chat(home, sid, time.time() - age_s - 60, 130_000)
    with open(p, "ab") as f:
        f.write(b" " * max(0, size - os.path.getsize(p)) + b"\n")
    t = time.time() - age_s
    os.utime(p, (t, t))
    return p


def handoff_files(home):
    d = os.path.join(home, ".claude", "handoff")
    return sorted(os.listdir(d)) if os.path.isdir(d) else []


def test_a_stop_writes_no_stub_any_more():
    """Stop fires at the end of EVERY reply: 223 stub writes into 66 notes measured 2 Oct,
    and a chat that tried to Write its real note failed 'not read yet'."""
    home = make_home({})
    try:
        write_big_chat(home, "stopnone", time.time() - 3600, 130_000)
        p = run_stop(home, "stopnone")
        expect_clean(p, "stop-no-stub")
        check("stop-no-stub: no note written by a Stop",
              not [f for f in handoff_files(home) if f.startswith(KEY + ".stopnone")],
              repr(handoff_files(home)))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_a_chat_that_never_reaches_the_ceiling_still_leaves_a_stub():
    """The exact failure that prompted this: well under the ceiling, no note, chat ends,
    thread unresumable. The stub is now written when the chat ENDS (SessionEnd)."""
    home = make_home({})
    try:
        stale_chat(home, "stubaaa1", 3600)
        note =os.path.join(home, ".claude", "handoff", KEY + ".stubaaa1.md")
        check("stub: CONTROL - no note exists before the SessionEnd hook runs",
              not os.path.exists(note), note)
        p = run_session_end(home, "stubaaa1")
        expect_clean(p, "stub")
        check("stub: a note now exists for a chat that never handed off",
              os.path.exists(note), note)
        body = ""
        if os.path.exists(note):
            with open(note, encoding="utf-8") as f:
                body = f.read()
        check("stub: it is marked as mechanical, not curated",
              STUB_MARKER in body, repr(body[:200]))
        check("stub: it carries a HANDOFF LABEL so the next chat can be summoned by name",
              "HANDOFF LABEL:" in body, repr(body[:200]))
        check("stub: it names the session it came from", "stubaaa1" in body,
              repr(body[:400]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_stub_never_overwrites_a_real_note():
    """A curated note is worth far more than a stub. If the stub could clobber one, this
    feature would destroy the very thing it exists to protect."""
    home = make_home({})
    try:
        write_big_chat(home, "stubaaa2", time.time() - 3600, 130_000)
        d = os.path.join(home, ".claude", "handoff")
        if not os.path.isdir(d):
            os.makedirs(d)
        note = os.path.join(d, KEY + ".stubaaa2.md")
        real = "HANDOFF LABEL: Real Note -3 (19 Sep)\n\nprose a human would miss.\n"
        with open(note, "w", encoding="utf-8") as f:
            f.write(real)
        p = run_stop(home, "stubaaa2")
        expect_clean(p, "stub-nooverwrite")
        expect_clean(run_session_end(home, "stubaaa2"), "stub-nooverwrite-end")
        old = stale_chat(home, "stubaaa2", 8 * 3600)    # a stale, big chat with this note
        expect_clean(boot(home, CWD, "newchat01"), "stub-nooverwrite-boot")
        with open(note, encoding="utf-8") as f:
            after = f.read()
        check("stub-nooverwrite: the real note is byte-for-byte untouched",
              after == real, repr(after[:200]))
        check("stub-nooverwrite: and it was not marked as a stub",
              STUB_MARKER not in after, repr(after[:200]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_a_chat_is_told_to_write_its_note_long_before_the_ceiling():
    """The stub is a floor, not a substitute - only the chat itself can write down what was
    DECIDED. So it must also be asked, early, while there is still room to do it well.

    130k is where chat "Context Guard -10" actually died, just under the then-150k first
    tier, which is why nobody was ever asked for a note. The first tier moved to 200k on
    26 Sep 2026, his decision, so this test now pins "asked well under the ceiling"
    instead - 240k, 80k clear of the 320k cap."""
    home = make_home({})
    try:
        write_big_chat(home, "earlyaaa", time.time() - 3600, 240_000)
        p = run(home, "earlyaaa", "carry on with the build")
        expect_clean(p, "early-note")
        ctx = context_of(p)
        check("early-note: the chat is asked for a handoff note well under the ceiling",
              "handoff note" in ctx.lower(), repr(ctx[:300]))
        check("early-note: and it is told where to put it",
              KEY + ".earlyaaa" in ctx.replace(chr(92), "/"), repr(ctx[:400]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_a_small_chat_is_not_asked_for_a_note():
    """THE CONTROL. Without it "the warning fired" passes for the wrong reason - a warning
    that fires on every chat from the first turn is noise, and noise gets ignored, which is
    how the guard stopped being heard in the first place."""
    home = make_home({})
    try:
        write_big_chat(home, "smallaaa", time.time() - 3600, 20_000)
        p = run(home, "smallaaa", "carry on with the build")
        expect_clean(p, "early-note/control")
        ctx = context_of(p)
        check("early-note CONTROL: a small chat is left alone",
              "handoff note" not in ctx.lower(), repr(ctx[:300]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_first_warning_waits_for_225k():
    """The first tier moved from 100k to 200k on 26 Sep 2026 and from 200k to 225k on
    2 Oct 2026, his decision (see the LEVELS comment) - so a chat sitting at 150k or 215k,
    which the OLD floor would already have warned, must now stay silent. A 230k chat crosses
    the NEW floor and must still be asked, by the tier's own name, for a handoff note. The
    other budgets are pinned here too, so a later edit to LEVELS[0] cannot silently drag
    them along with it."""
    home = make_home({})
    try:
        write_big_chat(home, "midtier1", time.time() - 3600, 150_000)
        p = run(home, "midtier1", "carry on with the build")
        expect_clean(p, "first-warning/150k")
        ctx = context_of(p)
        check("first-warning: a 150k chat is not warned",
              "handoff note" not in ctx.lower(), repr(ctx[:300]))
    finally:
        shutil.rmtree(home, ignore_errors=True)

    home = make_home({})
    try:
        write_big_chat(home, "midtier2", time.time() - 3600, 215_000)
        p = run(home, "midtier2", "carry on with the build")
        expect_clean(p, "first-warning/215k")
        ctx = context_of(p)
        check("first-warning: a 215k chat is not warned either",
              "handoff note" not in ctx.lower(), repr(ctx[:300]))
    finally:
        shutil.rmtree(home, ignore_errors=True)

    home = make_home({})
    try:
        write_big_chat(home, "midtier3", time.time() - 3600, 230_000)
        p = run(home, "midtier3", "carry on with the build")
        expect_clean(p, "first-warning/230k")
        ctx = context_of(p)
        check("first-warning: a 230k chat IS asked for a handoff note",
              "handoff note" in ctx.lower(), repr(ctx[:300]))
        check("first-warning: and named as GETTING EXPENSIVE",
              "GETTING EXPENSIVE" in ctx, repr(ctx[:300]))
    finally:
        shutil.rmtree(home, ignore_errors=True)

    home = make_home({})
    try:
        p = guard_call(home, "print(guard.LEVELS)")
        check("first-warning: the three tiers are 225k / 260k / 290k, same names",
              p.stdout.strip() == "[(225000, 'getting expensive'), (260000, 'expensive'), "
                                  "(290000, 'very expensive')]", repr(p.stdout[:200] + p.stderr[-200:]))
        check("first-warning: the top tier sits below the ceiling",
              guard_constant("CEILING_CAP") > 290_000, str(guard_constant("CEILING_CAP")))
        write_settings(home, 420_000)
        p = guard_call(home, "print(guard._ceiling())")
        check("first-warning: a 420k window gives a 320k ceiling",
              p.stdout.strip() == "320000", repr(p.stdout[:100] + p.stderr[-200:]))
    finally:
        shutil.rmtree(home, ignore_errors=True)
    check("first-warning: the ceiling cap is 320k",
          guard_constant("CEILING_CAP") == 320_000, str(guard_constant("CEILING_CAP")))
    check("first-warning: the rewarn step is unchanged",
          guard_constant("REWARN_STEP") == 40_000, str(guard_constant("REWARN_STEP")))
    check("first-warning: the freshness gate is unchanged",
          guard_constant("FRESH_CTX") == 120_000, str(guard_constant("FRESH_CTX")))


def test_audit_states_the_same_first_warning_as_guard():
    """audit.py tells the model its standing rules in prose. That number was typed by hand
    and drifted from LEVELS once; pin the two together."""
    home = make_home({})
    try:
        p = guard_call(home, "print(guard.LEVELS[0][0])")
        first = int(p.stdout.strip())
    finally:
        shutil.rmtree(home, ignore_errors=True)
    with open(os.path.join(os.path.dirname(GUARD), "audit.py"), encoding="utf-8") as f:
        src = f.read()
    check("audit-rule: audit.py says the same first-warning number as LEVELS",
          ("warn past ~%dk context" % (first // 1000)) in src, str(first))
    m = re.search(r"^WARN_AT\s*=\s*([0-9_]+)", src, re.M)
    check("audit-rule: audit.py's WARN_AT equals the first LEVELS number",
          bool(m) and int(m.group(1).replace("_", "")) == first, str(m and m.group(1)))


# ---------------------------------------------------------------- weekly context per call
def audit_call(home, code):
    """Run `code` after loading audit.py in a CHILD process pointed at the throwaway home."""
    env = child_env(home)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    pre = "import sys, json; sys.path.insert(0, %r); import audit\n" % HERE_DIR
    return subprocess.run([sys.executable, "-c", pre + code], capture_output=True,
                          text=True, encoding="utf-8", env=env)


def utc_stamp(days_ago, hour=12):
    """An ISO UTC timestamp `days_ago` days back, at midday so a timezone cannot move it a day."""
    t = datetime.datetime.utcnow().replace(hour=hour, minute=0, second=0, microsecond=0)
    return (t - datetime.timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def call_line(mid, days_ago, ctx, **extra):
    """One assistant jsonl line: input + cache read + cache write add up to `ctx`."""
    rec = {"type": "assistant", "timestamp": utc_stamp(days_ago), "requestId": "req-" + mid,
           "message": {"id": mid, "usage": {"input_tokens": 10, "output_tokens": 5,
                                            "cache_read_input_tokens": ctx - 110,
                                            "cache_creation_input_tokens": 100}}}
    rec.update(extra)
    return json.dumps(rec) + "\n"


def plant_calls(home, name, lines, mtime=None):
    p = os.path.join(home, ".claude", "projects", KEY, name + ".jsonl")
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write("".join(lines))
    if mtime:
        os.utime(p, (mtime, mtime))
    return p


def weekly_totals(home, budget="None"):
    """Refresh (saving) in a child and return the totals as a dict."""
    p = audit_call(home, "s=audit.weekly_refresh(audit.STATE_DIR, budget_s=%s)\n"
                         "t=audit.weekly_totals(s)\nt['complete']=s['complete']\nprint(json.dumps(t))"
                   % budget)
    try:
        return json.loads(p.stdout.strip().splitlines()[-1])
    except Exception:
        raise AssertionError("weekly refresh gave no totals: " + p.stdout + p.stderr)


def test_weekly_one_call_written_as_several_lines_counts_once():
    home = make_home({})
    try:
        plant_calls(home, "a", [call_line("m1", 2, 100_000)] * 3)
        t = weekly_totals(home)
        check("weekly/dedupe: three lines, one message id, one call",
              t["this_calls"] == 1 and t["this_sum"] == 100_000, str(t))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_weekly_puts_a_call_in_this_week_last_week_or_neither():
    home = make_home({})
    try:
        plant_calls(home, "a", [call_line("m1", 2, 100_000), call_line("m2", 10, 200_000),
                                call_line("m3", 20, 300_000)])
        t = weekly_totals(home)
        check("weekly/weeks: 2 days old is this week", t["this_calls"] == 1 and t["this_sum"] == 100_000, str(t))
        check("weekly/weeks: 10 days old is last week", t["last_calls"] == 1 and t["last_sum"] == 200_000, str(t))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_weekly_skips_sidechains_and_other_record_types():
    home = make_home({})
    try:
        plant_calls(home, "a", [call_line("m1", 1, 100_000, isSidechain=True),
                                call_line("m2", 1, 100_000, type="user"),
                                call_line("m3", 1, 120_000)])
        t = weekly_totals(home)
        check("weekly/skip: only the real assistant call counts",
              t["this_calls"] == 1 and t["this_sum"] == 120_000, str(t))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_weekly_a_forked_chat_copy_counts_once():
    home = make_home({})
    try:
        plant_calls(home, "a", [call_line("m1", 2, 100_000), call_line("m2", 2, 110_000)])
        plant_calls(home, "b", [call_line("m1", 2, 100_000), call_line("m2", 2, 110_000),
                                call_line("m3", 1, 120_000)])
        t = weekly_totals(home)
        check("weekly/fork: the same ids in two files count once",
              t["this_calls"] == 3 and t["this_sum"] == 330_000, str(t))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_weekly_refresh_is_incremental_and_survives_a_rewrite():
    home = make_home({})
    try:
        p = plant_calls(home, "a", [call_line("m1", 2, 100_000)])
        t1 = weekly_totals(home)
        with open(p, "a", encoding="utf-8", newline="") as f:
            f.write(call_line("m2", 1, 110_000) + call_line("m3", 1, 120_000))
        t2 = weekly_totals(home)
        check("weekly/incremental: two appended calls grow the total by exactly two",
              t1["this_calls"] == 1 and t2["this_calls"] == 3, str((t1, t2)))
        t3 = weekly_totals(home)
        check("weekly/incremental: no change, identical totals", t3 == t2, str((t2, t3)))
        # a half-written last line is not read until its newline arrives
        with open(p, "a", encoding="utf-8", newline="") as f:
            f.write(call_line("m4", 1, 130_000).rstrip("\n"))
        t4 = weekly_totals(home)
        check("weekly/incremental: a line with no newline yet is left for next time",
              t4["this_calls"] == 3, str(t4))
        with open(p, "a", encoding="utf-8", newline="") as f:
            f.write("\n")
        t5 = weekly_totals(home)
        check("weekly/incremental: ...and counted once its newline lands",
              t5["this_calls"] == 4, str(t5))
        # rewritten SMALLER: rescanned from 0, old ids not counted twice, a new one is
        plant_calls(home, "a", [call_line("m1", 2, 100_000), call_line("m9", 1, 90_000)])
        t6 = weekly_totals(home)
        check("weekly/rewrite: a shrunk file is rescanned without double counting",
              t6["this_calls"] == 5, str(t6))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_weekly_budget_stops_after_one_file_and_a_later_run_completes():
    home = make_home({})
    try:
        now = time.time()
        plant_calls(home, "new", [call_line("n1", 1, 100_000)], mtime=now)
        plant_calls(home, "old", [call_line("o1", 3, 110_000)], mtime=now - 3600)
        t1 = weekly_totals(home, budget="0")
        check("weekly/budget: budget 0 reads the first file only and says incomplete",
              t1["this_calls"] == 1 and t1["complete"] is False, str(t1))
        t2 = weekly_totals(home)
        check("weekly/budget: an unbudgeted run finishes it, counting the first file once",
              t2["this_calls"] == 2 and t2["complete"] is True, str(t2))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_weekly_cli_prints_the_sentence_or_says_nothing_measured():
    home = make_home({})
    env = child_env(home)
    try:
        e = subprocess.run([sys.executable, AUDIT, "--weekly"], capture_output=True, text=True, env=env)
        check("weekly/cli: an empty home says so, exit 0",
              e.returncode == 0 and "no calls measured yet" in e.stdout, e.stdout + e.stderr)
        lines = ([call_line("t%d" % i, 1, 300_000) for i in range(1)]
                 + [call_line("t9", 2, 100_000)]
                 + [call_line("l%d" % i, 9, 200_000) for i in range(2)]
                 + [call_line("l5", 9, 250_000)])
        plant_calls(home, "a", lines)
        r = subprocess.run([sys.executable, AUDIT, "--weekly"], capture_output=True, text=True, env=env)
        out = r.stdout.strip()
        # this week: 300k + 100k -> avg 200k, 1 of 2 past 225k; last week: 200k,200k,250k -> avg 217k, 1 of 3
        want = ("Average context per call: 200k this week (2 calls), 217k last week (3 calls) - "
                "8% smaller. 50% of this week's calls ran past the 225k warning (last week 33%).")
        check("weekly/cli: the sentence for a home with data, exit 0",
              r.returncode == 0 and out == want, out + " | " + r.stderr)
        check("weekly/cli: it saved the summary and the scan state",
              os.path.exists(os.path.join(home, ".claude", "context-guard", "weekly-context.json"))
              and os.path.exists(os.path.join(home, ".claude", "context-guard", "weekly-context-scan.json")))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_weekly_line_wording_follows_the_sign_and_drops_a_missing_comparison():
    home = make_home({})
    try:
        p = audit_call(home,
            "def S(a, b):\n"
            "    import datetime as d\n"
            "    t = d.date.today()\n"
            "    def day(n, ctxs):\n"
            "        k = (t - d.timedelta(days=n)).isoformat()\n"
            "        h = {}\n"
            "        for c in ctxs: h[str(c // 25000)] = h.get(str(c // 25000), 0) + 1\n"
            "        return k, {'calls': len(ctxs), 'sum_ctx': sum(ctxs), 'hist': h}\n"
            "    days = dict([day(1, a)] + ([day(9, b)] if b else []))\n"
            "    return {'days': days, 'complete': True}\n"
            "print(audit.weekly_line(S([300000], [200000])))\n"
            "print(audit.weekly_line(S([200000], [])))\n"
            "print(repr(audit.weekly_line({'days': {}})))\n")
        got = p.stdout.splitlines()
        check("weekly/line: bigger this week says bigger",
              len(got) == 3 and "50% bigger" in got[0], p.stdout + p.stderr)
        check("weekly/line: no last-week calls leaves the comparison out",
              len(got) == 3 and "last week" not in got[1] and "smaller" not in got[1]
              and got[1].startswith("Average context per call: 200k this week (1 call)."), p.stdout)
        check("weekly/line: no calls at all is an empty string", len(got) == 3 and got[2] == "''", p.stdout)
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_weekly_budget_also_applies_inside_one_huge_file():
    """The SessionEnd hook has 10 s. One huge transcript on a cold cache must not be able to
    overrun it: the budget is checked every ~2000 lines, the read stops at the last complete
    line with its offset saved, and repeated budgeted runs finish it with exact totals."""
    home = make_home({})
    try:
        n = 4500
        plant_calls(home, "huge", [call_line("h%d" % i, 1, 100_000 + i) for i in range(n)])
        want_sum = sum(100_000 + i for i in range(n))
        t1 = weekly_totals(home, budget="0")
        check("weekly/budget-inside: budget 0 reads the big file only partly, incomplete",
              0 < t1["this_calls"] < n and t1["complete"] is False, str(t1))
        got = [t1["this_calls"]]
        t = t1
        for _ in range(5):
            if t["complete"]:
                break
            t = weekly_totals(home, budget="0")
            got.append(t["this_calls"])
        check("weekly/budget-inside: every budgeted run makes progress", got == sorted(set(got)), str(got))
        check("weekly/budget-inside: repeated budgeted runs finish it with exact totals",
              t["complete"] is True and t["this_calls"] == n and t["this_sum"] == want_sum, str((got, t)))
        t2 = weekly_totals(home)
        check("weekly/budget-inside: one more unbudgeted run double counts nothing",
              t2["this_calls"] == n and t2["this_sum"] == want_sum and t2["complete"] is True, str(t2))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_weekly_budget_also_counts_bytes_not_only_lines():
    """Review of batch B: a cold transcript with a few hundred HUGE lines (pasted images)
    never reaches the 2000-line clock check, so the budget must also look every ~8 MB."""
    home = make_home({})
    try:
        n = 12
        pad = "x" * 1_000_000
        plant_calls(home, "fat", [call_line("f%d" % i, 1, 100_000, pad=pad) for i in range(n)])
        t1 = weekly_totals(home, budget="0")
        check("weekly/budget-bytes: budget 0 stops inside 12 one-MB lines",
              0 < t1["this_calls"] < n and t1["complete"] is False, str(t1))
        t2 = weekly_totals(home)
        check("weekly/budget-bytes: an unbudgeted run then finishes it exactly",
              t2["this_calls"] == n and t2["complete"] is True, str(t2))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def weekly_state_dir(home):
    return os.path.join(home, ".claude", "context-guard")


def test_session_end_also_refreshes_the_weekly_number_and_still_prints_nothing():
    home = make_home({})
    try:
        plant_calls(home, "endwk001", [call_line("e1", 1, 100_000), call_line("e2", 1, 120_000)])
        p = run_session_end(home, "endwk001")
        expect_clean(p, "weekly-end")
        check("weekly-end: the hook prints nothing", (p.stdout or "").strip() == "", p.stdout)
        sp = os.path.join(weekly_state_dir(home), "weekly-context.json")
        try:
            with open(sp, encoding="utf-8") as f:
                s = json.load(f)
        except Exception:
            s = {}
        calls = sum(r.get("calls", 0) for r in (s.get("days") or {}).values())
        check("weekly-end: weekly-context.json holds the transcript's calls", calls == 2, str(s)[:300])
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_report_shows_the_weekly_line_and_writes_nothing():
    home = make_home({})
    try:
        plant_calls(home, "rep", [call_line("r1", 1, 100_000), call_line("r2", 1, 140_000)])
        run_report(home)                       # creates the log; snapshot AFTER that
        st = weekly_state_dir(home)
        listing = lambda: sorted(os.listdir(st)) if os.path.isdir(st) else None
        logp = os.path.join(home, ".claude", "context-audit.log")
        before = (listing(), open(logp, "rb").read())
        p = run_report(home, log=None)
        out = p.stdout
        check("weekly-report: the section is there with the line",
              "-- chat size, week on week --" in out
              and "Average context per call: 120k this week (2 calls)" in out, out[-600:])
        check("weekly-report: it sits after the errors row and before the sessions list",
              out.index("errors (last 7 days)") < out.index("-- chat size, week on week --")
              < out.index("-- sessions being tracked --"), out)
        after = (listing(), open(logp, "rb").read())
        check("weekly-report: STATE listing and log bytes are identical", before == after, str((before[0], after[0])))
        check("weekly-report: no weekly file was created", not any(
            os.path.exists(os.path.join(st, n)) for n in ("weekly-context.json", "weekly-context-scan.json")))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_report_session_list_hides_the_weekly_files():
    home = make_home({})
    try:
        st = weekly_state_dir(home)
        os.makedirs(st, exist_ok=True)
        for name in ("weekly-context", "weekly-context-scan"):
            with open(os.path.join(st, name + ".json"), "w", encoding="utf-8") as f:
                json.dump({"days": {}, "files": {}, "seen": {}, "warned_ctx": 120000, "reads": {}}, f)
        part = run_report(home).stdout.split("-- sessions being tracked --")[-1].split("-- request ledgers")[0]
        check("weekly-report-list: neither weekly file is listed as a session",
              "weekly-context" not in part, repr(part))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_alert_adds_the_weekly_line_only_from_a_fresh_complete_summary():
    def alert_in(home):
        return subprocess.run([sys.executable, AUDIT, "--alert"], capture_output=True, text=True,
                              env=child_env(home), cwd=tempfile.gettempdir(), timeout=60)

    def put_summary(home, complete, age_days):
        st = weekly_state_dir(home)
        os.makedirs(st, exist_ok=True)
        # no update fetch from a test: the flag file update.py honours
        with open(os.path.join(st, "no-update-check"), "w") as f:
            f.write("")
        day = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        when = (datetime.datetime.now() - datetime.timedelta(days=age_days)).isoformat(timespec="seconds")
        with open(os.path.join(st, "weekly-context.json"), "w", encoding="utf-8") as f:
            json.dump({"days": {day: {"calls": 1, "sum_ctx": 100_000, "hist": {"4": 1}}},
                       "complete": complete, "refreshed": when}, f)

    home = make_home({})
    try:
        put_summary(home, True, 1)
        p = alert_in(home)
        expect_clean(p, "weekly-alert-fresh")
        d = one_json(p)
        check("weekly-alert: a fresh complete summary adds the line to systemMessage, nothing else to say",
              isinstance(d, dict) and "Average context per call: 100k this week (1 call)" in d.get("systemMessage", "")
              and "hookSpecificOutput" not in d, p.stdout)
    finally:
        shutil.rmtree(home, ignore_errors=True)
    for label, complete, age in (("stale", True, 5), ("incomplete", False, 1)):
        home = make_home({})
        try:
            put_summary(home, complete, age)
            plant_calls(home, "tr", [call_line("a1", 1, 100_000)])
            p = alert_in(home)
            expect_clean(p, "weekly-alert-" + label)
            check("weekly-alert: a %s summary prints no line" % label, (p.stdout or "").strip() == "", p.stdout)
            check("weekly-alert: %s - transcripts were not scanned" % label,
                  not os.path.exists(os.path.join(weekly_state_dir(home), "weekly-context-scan.json")))
        finally:
            shutil.rmtree(home, ignore_errors=True)


PICKUP_PLAIN = "Picked up the handoff note from your last chat."


def test_the_pickup_names_the_effort_the_note_asks_for():
    """A chat cannot change its own effort level, so the note names the level the NEXT chat
    needs and the pickup shows it to him on its first line. POSITIONAL, like the writer
    line: only the head of the note is structure, prose further down may mention the
    convention. Anything that is not a valid level in the first 5 lines changes nothing."""
    head = "HANDOFF LABEL: Mango -4 (1 Jan)" + chr(10) + "WRITTEN BY: Mango -3 (1 Jan)" + chr(10)
    good = "NEXT CHAT EFFORT: high - sending out agents and reviewing them"
    cases = (
        ("valid", head + good + chr(10) * 2 + "body-MANGO" + chr(10), True),
        ("valid on line 5", head + chr(10) * 2 + good + chr(10) + "body-MANGO" + chr(10), True),
        ("absent", NOTE_MANGO, False),
        ("unknown level", head + "NEXT CHAT EFFORT: turbo - x" + chr(10) * 2 + "body-MANGO"
         + chr(10), False),
        ("line 6", head + chr(10) * 3 + good + chr(10) + "body-MANGO" + chr(10), False),
        ("line 10", head + chr(10) * 7 + good + chr(10) + "body-MANGO" + chr(10), False),
    )
    for name, note, expect in cases:
        home = make_home({KEY + ".aaaaaaaa.md": note})
        try:
            p = run(home, "effort001-new", "mango")
            expect_clean(p, "effort/" + name)
            msg = sysmsg(p)
            if expect:
                want = PICKUP_PLAIN + " Set this chat's effort to High - sending out agents and reviewing them."
                check("effort/" + name + ": the pickup names the level and the reason",
                      "Set this chat's effort to High" in msg
                      and "sending out agents and reviewing them" in msg, repr(msg))
                check("effort/" + name + ": in exactly the agreed sentence", msg == want, repr(msg))
            else:
                check("effort/" + name + ": the pickup line is exactly as before",
                      msg == PICKUP_PLAIN, repr(msg))
        finally:
            shutil.rmtree(home, ignore_errors=True)

    home = make_home({})
    try:
        tmpl = os.path.join(os.path.dirname(GUARD), "handoff-template.md")
        with open(tmpl, encoding="utf-8") as f:
            check("effort: the note template carries the NEXT CHAT EFFORT line",
                  "NEXT CHAT EFFORT:" in f.read(), tmpl)
        write_transcript(home, "effort002", 240_000)
        p = run(home, "effort002", "carry on")
        expect_clean(p, "effort/instruction")
        ctx = context_of(p)
        check("effort/instruction: the handoff warning fired", "THIS CHAT IS NOW" in ctx,
              repr(ctx[:200]))
        check("effort/instruction: the note-writing instructions ask for the line",
              "NEXT CHAT EFFORT" in ctx, repr(ctx[-900:]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


# ---------------------------------------------------------------------------
# The away indicator. Measured failure, 19 Sep 2026: the user typed "Afk on" at
# 20:30 on 18 Sep and seventeen hours later asked, about a DIFFERENT chat,
# "QIX handover -13 (18 Sep) havent told me to change chat why is that". The
# flag is machine-wide and never expires, and nothing on an ordinary turn said
# it was on. He then guessed at the way out ("all this time i thought it was
# afk off not back") - he happened to be right, but nothing had told him.
#
# He declined auto-EXPIRY. That is a different thing and is NOT revisited here:
# the flag still only clears when he says so. This only makes it VISIBLE.
# ---------------------------------------------------------------------------


def test_away_mode_says_so_once_per_chat():
    """Once. A banner on every turn is a nag, and away mode exists to stop nagging -
    an indicator that repeated would defeat the feature it is reporting on."""
    home = make_home({})
    try:
        write_big_chat(home, "awaysay1", time.time() - 3600, 20_000)
        arm_away(home)
        first = run(home, "awaysay1", "carry on")
        expect_clean(first, "away-indicator")
        check("away-indicator: he is told away mode is on",
              "away mode" in sysmsg(first).lower(), repr(sysmsg(first)[:200]))
        second = run(home, "awaysay1", "carry on some more")
        expect_clean(second, "away-indicator/second")
        check("away-indicator: and NOT told again on the next turn",
              "away mode" not in sysmsg(second).lower(), repr(sysmsg(second)[:200]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_away_indicator_names_both_ways_out():
    """He guessed the phrase. The indicator that reports the state must also carry the
    exit, or it leaves him exactly where he was - knowing something is on, not how to
    leave. Both words are real entries in AWAY_OFF."""
    home = make_home({})
    try:
        write_big_chat(home, "awaysay2", time.time() - 3600, 20_000)
        arm_away(home)
        p = run(home, "awaysay2", "carry on")
        expect_clean(p, "away-indicator/exit")
        msg = sysmsg(p).lower()
        check("away-indicator: it names 'back'", "back" in msg, repr(msg[:200]))
        check("away-indicator: it names 'afk off' too", "afk off" in msg, repr(msg[:200]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_no_indicator_when_he_is_at_the_console():
    """THE CONTROL. Without it, a banner printed unconditionally would pass both tests
    above and would shout "away mode is on" at a man sitting at his desk."""
    home = make_home({})
    try:
        write_big_chat(home, "awaysay3", time.time() - 3600, 20_000)
        p = run(home, "awaysay3", "carry on")
        expect_clean(p, "away-indicator/control")
        check("away-indicator CONTROL: silent when he never went away",
              "away mode" not in sysmsg(p).lower(), repr(sysmsg(p)[:200]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_away_indicator_survives_a_handoff_pickup():
    """MEASURED IN THE WILD 19 Sep 2026, and the reason he never once saw the indicator.

    Every away test above uses make_home({}) - NO note waiting. In D:\\Claude a note is
    ALWAYS waiting, so turn one goes down the pickup branch, which prints its own
    systemMessage and returns before the indicator is ever reached. By turn two the chat
    is past LEVELS[0][0] and the indicator is correctly silent for good. The feature was
    therefore unreachable in the only folder that uses it - six handoff notes asked him
    whether he had ever seen it, and the answer was in the code.

    Same one-path blind spot as the headphone button: both branches exist, every test
    drove one. The pickup itself must keep working - that is asserted here too, so a fix
    that silences the pickup cannot pass."""
    for label, notes, prompt, arrives, marker in (
            ("pickup", {KEY + ".9e19c7ab.md": NOTE_CTX}, "context guard",
             "handoff", "body-CONTEXTGUARD"),
            ("menu", {KEY + ".9e19c7ab.md": NOTE_CTX, KEY + ".143f0320.md": NOTE_SPOT},
             "carry on with the build", "saved threads", "")):
        home = make_home(notes)
        try:
            arm_away(home)
            p = run(home, "awaypick1-" + label, prompt)
            expect_clean(p, "away-pickup/" + label)
            check("away-pickup/%s: the note still reaches the new chat" % label,
                  arrives in context_of(p).lower(), repr(context_of(p)[:160]))
            if marker:
                check("away-pickup/%s: it is the RIGHT note" % label,
                      marker in context_of(p), repr(context_of(p)[:160]))
            check("away-pickup/%s: and he is STILL told away mode is on" % label,
                  "away mode" in sysmsg(p).lower(), repr(sysmsg(p)[:200]))
            check("away-pickup/%s: the indicator carries the way out" % label,
                  "afk off" in sysmsg(p).lower(), repr(sysmsg(p)[:200]))
        finally:
            shutil.rmtree(home, ignore_errors=True)

        # CONTROL, in the same shape: at the console the pickup must say nothing about
        # away mode. Without it, hard-coding the phrase into the pickup message passes.
        home = make_home(notes)
        try:
            p = run(home, "awaypick2-" + label, prompt)
            expect_clean(p, "away-pickup/" + label + "/control")
            check("away-pickup/%s CONTROL: pickup alone never mentions away mode" % label,
                  "away mode" not in sysmsg(p).lower(), repr(sysmsg(p)[:200]))
            check("away-pickup/%s CONTROL: and the note still arrives" % label,
                  arrives in context_of(p).lower(), repr(context_of(p)[:160]))
        finally:
            shutil.rmtree(home, ignore_errors=True)

    # Once, not twice - the pickup turn spends the indicator like any other turn.
    home = make_home({KEY + ".9e19c7ab.md": NOTE_CTX})
    try:
        arm_away(home)
        first = run(home, "awaypick3", "context guard")
        expect_clean(first, "away-pickup/once")
        check("away-pickup/once: told on the pickup turn",
              "away mode" in sysmsg(first).lower(), repr(sysmsg(first)[:200]))
        write_big_chat(home, "awaypick3", time.time() - 60, 20_000)
        second = run(home, "awaypick3", "carry on")
        expect_clean(second, "away-pickup/once/second")
        check("away-pickup/once: and NOT told again on the next turn",
              "away mode" not in sysmsg(second).lower(), repr(sysmsg(second)[:200]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_a_note_that_mentions_the_marker_is_not_a_stub():
    """MEASURED IN THE WILD 19 Sep 2026 - this one cost a real 287-line handoff note.

    The note told the next chat to grep the handoff folder for the stub marker, so the
    marker string appeared in its body. real_note() tested the WHOLE file for that
    substring, decided a curated note was machine output, and write_stub() overwrote it
    with 21 lines of facts. The ceiling then fired saying no note existed - which was, by
    then, true. A guard that reads prose instead of structure is the recurring bug in this
    project, and this is the sixth instance."""
    home = make_home({})
    try:
        write_big_chat(home, "markerxx", time.time() - 3600, 130_000)
        d = os.path.join(home, ".claude", "handoff")
        if not os.path.isdir(d):
            os.makedirs(d)
        note = os.path.join(d, KEY + ".markerxx.md")
        real = ("HANDOFF LABEL: Real Note -9 (19 Sep)" + chr(10) * 2
                + "Tell the next chat to grep the folder for " + STUB_MARKER
                + " - that is how you spot a stub." + chr(10))
        with open(note, "w", encoding="utf-8") as f:
            f.write(real)
        p = run_stop(home, "markerxx")
        expect_clean(p, "marker-mention")
        expect_clean(run_session_end(home, "markerxx"), "marker-mention-end")
        with open(note, encoding="utf-8") as f:
            after = f.read()
        check("marker-mention: a note that merely NAMES the marker survives untouched",
              after == real, repr(after[:160]))
        # NOT "not blocked" - the memory nag legitimately blocks in a fake home with no
        # memory writes, and catching it here would make this test about the wrong thing.
        # Assert the CEILING's own sentence is absent.
        check("marker-mention: and the ceiling no longer claims there is no note",
              "has NOT written a handoff note" not in (p.stdout or ""),
              repr((p.stdout or "")[:200]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_session_end_never_touches_a_real_note_and_stubs_when_none():
    home = make_home({})
    try:
        write_big_chat(home, "endreal1", time.time() - 3600, 130_000)
        note = os.path.join(home, ".claude", "handoff", KEY + ".endreal1.md")
        real = "HANDOFF LABEL: Real -1 (2 Oct)\n\nprose.\n"
        with open(note, "w", encoding="utf-8") as f:
            f.write(real)
        expect_clean(run_session_end(home, "endreal1"), "end-real")
        with open(note, encoding="utf-8") as f:
            check("end-real: a real note is untouched", f.read() == real)
        stale_chat(home, "endnone1", 3600)
        expect_clean(run_session_end(home, "endnone1"), "end-none")
        check("end-none: a stub was written", os.path.exists(
            os.path.join(home, ".claude", "handoff", KEY + ".endnone1.md")))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_bootstrap_stubs_other_stale_chats_of_the_same_project_only():
    home = make_home({})
    try:
        stale_chat(home, "stale001", 8 * 3600)         # 8 h idle, big: gets a stub
        stale_chat(home, "recent01", 3600)             # 1 h idle: nothing
        stale_chat(home, "ancient1", 9 * 86400)        # over 7 days: nothing
        write_big_chat(home, "tiny0001", time.time() - 8 * 3600 - 60, 130_000)
        t = time.time() - 8 * 3600
        os.utime(os.path.join(home, ".claude", "projects", KEY, "tiny0001.jsonl"), (t, t))
        stale_chat(home, "newchat1", 8 * 3600)         # the starting chat itself
        other = os.path.join(home, ".claude", "projects", "D--Other")
        os.makedirs(other)
        op = os.path.join(other, "othproj1.jsonl")
        with open(op, "wb") as f:
            f.write(b" " * 1_501_000 + b"\n")
        t = time.time() - 8 * 3600
        os.utime(op, (t, t))
        p = boot(home, CWD, "newchat1")
        expect_clean(p, "sweep-stub")
        check("sweep-stub: at most one json object on stdout",
              len([l for l in (p.stdout or "").splitlines() if l.strip()]) <= 1,
              repr((p.stdout or "")[:200]))
        got = [f for f in handoff_files(home)]
        check("sweep-stub: the stale chat got a stub",
              KEY + ".stale001.md" in got, repr(got))
        check("sweep-stub: nothing else did", [f for f in got if "stale001" not in f] == [],
              repr(got))
        mt = os.path.getmtime(os.path.join(home, ".claude", "handoff", KEY + ".stale001.md"))
        os.utime(os.path.join(home, ".claude", "handoff", KEY + ".stale001.md"),
                 (mt - 100, mt - 100))
        boot(home, CWD, "newchat1")
        check("sweep-stub: a second start does not rewrite it",
              abs(os.path.getmtime(os.path.join(home, ".claude", "handoff",
                                                KEY + ".stale001.md")) - (mt - 100)) < 1)
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_bootstrap_retires_old_stubs_by_renaming_and_leaves_real_notes():
    home = make_home({})
    try:
        stale_chat(home, "retire01", 20 * 86400)
        stale_chat(home, "young001", 20 * 86400)
        expect_clean(run_session_end(home, "retire01"), "retire-mk")
        expect_clean(run_session_end(home, "young001"), "retire-mk2")
        d = os.path.join(home, ".claude", "handoff")
        old = os.path.join(d, KEY + ".retire01.md")
        young = os.path.join(d, KEY + ".young001.md")
        realp = os.path.join(d, KEY + ".realold1.md")
        with open(realp, "w", encoding="utf-8") as f:
            f.write("HANDOFF LABEL: Real -2 (1 Sep)\n\nprose.\n")
        for p, days in ((old, 8), (young, 3), (realp, 30)):
            t = time.time() - days * 86400
            os.utime(p, (t, t))
        expect_clean(boot(home, CWD, "newchat2"), "retire")
        names = handoff_files(home)
        check("retire: the old stub is gone under its own name", not os.path.exists(old),
              repr(names))
        check("retire: renamed to .used-stale-<stamp>.md, not deleted",
              any(re.match(re.escape(KEY) + r"\.retire01\.used-stale-\d{8}-\d{6}\.md$", n)
                  for n in names), repr(names))
        check("retire: a stub under 7 days stays", os.path.exists(young), repr(names))
        check("retire: a real note of any age stays", os.path.exists(realp), repr(names))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_install_wires_the_session_end_hook():
    home = make_home({})
    try:
        seed_settings(home, {})
        expect_clean(run_install(home), "install-end")
        run_install(home)
        s = read_settings(home)
        cmds = [h.get("command") or "" for e in s.get("hooks", {}).get("SessionEnd", [])
                for h in e.get("hooks") or []]
        check("install-end: --session-end is wired to SessionEnd exactly once",
              len([c for c in cmds if "--session-end" in c]) == 1, json.dumps(cmds)[:300])
        run_install(home, "--uninstall")
        s = read_settings(home)
        check("install-end: uninstall removes it",
              "SessionEnd" not in s.get("hooks", {}), json.dumps(s)[:300])
    finally:
        shutil.rmtree(home, ignore_errors=True)


# ------------------------------------- the hook channel holds ~10k characters
FIT_MARK = "CONTEXT GUARD OVERFLOW:"
FIT_BODY = "".join("line %05d of the long text, with an accent: café\n" % i for i in range(700))
FIT_CODE = ("text = ''.join('line %05d of the long text, with an accent: caf\\u00e9\\n' % i "
            "for i in range(700))\n")


def overflow_files(home):
    d = os.path.join(home, ".claude", "handoff", "overflow")
    return sorted(os.listdir(d)) if os.path.isdir(d) else []


def test_hook_text_over_the_cap_is_cut_and_the_rest_goes_to_a_file():
    home = make_home({})
    try:
        code = (FIT_CODE +
                "out = {'hookSpecificOutput': {'hookEventName': 'UserPromptSubmit',"
                " 'additionalContext': text}, 'systemMessage': 'keep me'}\n"
                "r = guard.fit_hook_text(out, 'abcd1234-5678')\n"
                "print(json.dumps(r))\n")
        p = guard_call(home, "import json\n" + code)
        res = json.loads(p.stdout)
        ctx = res["hookSpecificOutput"]["additionalContext"]
        check("fit: emitted at most 9800 characters", len(ctx) <= 9800, str(len(ctx)))
        check("fit: it starts with the original text", FIT_BODY.startswith(ctx[:200]))
        check("fit: other keys are untouched", res.get("systemMessage") == "keep me")
        check("fit: it ends with the pointer line", ctx.rstrip().splitlines()[-1].startswith(FIT_MARK),
              ctx[-300:])
        check("fit: the pointer line is short", len(ctx) - ctx.rindex(FIT_MARK) < 400)
        i = ctx.rindex(FIT_MARK)
        head = ctx[:i]
        check("fit: the head is cut at a line boundary", head.endswith("\n"), repr(head[-30:]))
        files = overflow_files(home)
        check("fit: exactly one overflow file", len(files) == 1, str(files))
        if files:
            check("fit: it is named with the sid8 and a timestamp",
                  re.match(r"^abcd1234-\d{8}-\d{6}-\d{6}\.md$", files[0]) is not None, files[0])
            fp = os.path.join(home, ".claude", "handoff", "overflow", files[0])
            with open(fp, "rb") as fh:
                raw = fh.read()
            rest = raw.decode("utf-8")
            check("fit: head + file is the original, byte for byte",
                  (head + rest) == FIT_BODY and (head.encode("utf-8") + raw) == FIT_BODY.encode("utf-8"))
            check("fit: the pointer names the path and the length",
                  fp in ctx and str(len(rest)) in ctx, ctx[-300:])
            check("fit: the pointer says to Read it now, before answering",
                  "Read" in ctx[i:] and "NOW" in ctx[i:] and "before answering" in ctx[i:], ctx[i:])
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_hook_text_at_or_under_the_cap_is_emitted_unchanged():
    home = make_home({})
    try:
        code = ("import json\n"
                "t = 'x' * 9799 + chr(10)\n"
                "out = {'hookSpecificOutput': {'additionalContext': t}}\n"
                "r = guard.fit_hook_text(json.loads(json.dumps(out)), 'abcd1234')\n"
                "print(json.dumps(r == out and r['hookSpecificOutput']['additionalContext'] == t))\n"
                "e = {'systemMessage': 'only a message'}\n"
                "print(json.dumps(guard.fit_hook_text(dict(e), 'abcd1234') == e))\n"
                "print(json.dumps(guard.fit_hook_text(None, 'abcd1234') is None))\n")
        p = guard_call(home, code)
        check("fit/under: unchanged, a message-only object and None too",
              p.stdout.split() == ["true", "true", "true"], p.stdout + p.stderr[-300:])
        check("fit/under: no file written", overflow_files(home) == [], str(overflow_files(home)))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_hook_text_with_no_newline_in_range_is_cut_hard():
    home = make_home({})
    try:
        code = ("import json\n"
                "t = 'y' * 20000\n"
                "r = guard.fit_hook_text({'hookSpecificOutput': {'additionalContext': t}}, 'abcd1234')\n"
                "print(json.dumps(r['hookSpecificOutput']['additionalContext']))\n")
        p = guard_call(home, code)
        ctx = json.loads(p.stdout)
        i = ctx.rindex(FIT_MARK)
        files = overflow_files(home)
        check("fit/hard: at most 9800 and a pointer", len(ctx) <= 9800 and i > 0, str(len(ctx)))
        rest = ""
        if files:
            with open(os.path.join(home, ".claude", "handoff", "overflow", files[0]),
                      "r", encoding="utf-8", newline="") as fh:
                rest = fh.read()
        check("fit/hard: head + file is the original", ctx[:i] + rest == "y" * 20000)
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_a_failed_overflow_write_emits_the_original_and_logs_it():
    home = make_home({})
    try:
        # a FILE where the overflow folder has to go, so makedirs cannot succeed
        with open(os.path.join(home, ".claude", "handoff", "overflow"), "w") as fh:
            fh.write("in the way")
        code = (FIT_CODE +
                "out = {'hookSpecificOutput': {'additionalContext': text}}\n"
                "r = guard.fit_hook_text(out, 'abcd1234')\n"
                "print(json.dumps(r['hookSpecificOutput']['additionalContext'] == text))\n")
        p = guard_call(home, "import json\n" + code)
        check("fit/fail: the original text is emitted whole", p.stdout.strip() == "true",
              p.stdout + p.stderr[-300:])
        check("fit/fail: it is logged by swallowed()", "error: fit_hook_text:" in guard_log(home),
              guard_log(home)[-300:])
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_a_real_30k_pickup_fits_the_hook_channel():
    """A real pickup through the real hook: the head keeps the procedure and the start of the
    note, the pointer names a file, and the file holds the end of the note."""
    big = "HANDOFF LABEL: context guard\n\n# Handoff - the guard\n\n" + "".join(
        "para %04d of the note, plain words and nothing else in it.\n" % i for i in range(520))
    big += "THE-VERY-LAST-LINE-OF-THE-NOTE\n"
    check("fit/real: the note is about 30k characters", 28_000 < len(big) < 36_000, str(len(big)))
    home = make_home({KEY + ".9e19c7ab.md": big})
    try:
        p = run(home, "fit00001-new", "context guard")
        ctx = context_of(p)
        expect_clean(p, "fit/real")
        check("fit/real: at most 9800 characters", 0 < len(ctx) <= 9800, str(len(ctx)))
        check("fit/real: starts with HANDOFF NOTE", ctx.startswith("HANDOFF NOTE"), ctx[:60])
        check("fit/real: the note's first heading is in the head", "# Handoff - the guard" in ctx)
        check("fit/real: the pointer line is last", FIT_MARK in ctx.rstrip().splitlines()[-1],
              ctx[-200:])
        files = overflow_files(home)
        check("fit/real: one overflow file named with the sid8", len(files) == 1
              and files[0].startswith("fit00001-"), str(files))
        if files:
            with open(os.path.join(home, ".claude", "handoff", "overflow", files[0]),
                      "r", encoding="utf-8", newline="") as fh:
                rest = fh.read()
            check("fit/real: the file holds the end of the note",
                  "THE-VERY-LAST-LINE-OF-THE-NOTE" in rest)
            check("fit/real: nothing of the note was lost",
                  big.rstrip("\n").endswith("THE-VERY-LAST-LINE-OF-THE-NOTE")
                  and ctx[:ctx.rindex(FIT_MARK)].endswith("\n"))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_plain_first_handoff_warning_is_emitted_whole():
    """The reviewer measured this exact warning (a fresh chat at 200k, no checkpoint ever
    recorded) at about 9557 characters. It is not a pickup, it has nothing to move to a
    file, and a cut here would send the model off to Read a file for a few hundred
    characters of its own warning."""
    home = make_home({})
    try:
        write_transcript(home, "whole001", 240_000)
        p = run(home, "whole001", "carry on")
        expect_clean(p, "whole-warning")
        ctx = context_of(p)
        check("whole-warning: it is a handoff warning of real length",
              9000 < len(ctx) <= 9800, str(len(ctx)))
        check("whole-warning: no pointer line", FIT_MARK not in ctx, ctx[-300:])
        check("whole-warning: no overflow file", overflow_files(home) == [],
              str(overflow_files(home)))
        check("whole-warning: it still ends with its own last section",
              "record a checkpoint" in ctx.lower(), repr(ctx[-300:]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_overflow_pointer_is_neutral_about_what_was_cut():
    home = make_home({})
    try:
        p = guard_call(home, "print(guard._overflow_pointer(1234, 'X:/some/path.md'))")
        out = p.stdout.strip()
        check("pointer: keeps the marker the tests and the model key on",
              out.startswith(FIT_MARK), out)
        check("pointer: says the rest of THIS MESSAGE, with the count and the path",
              "the rest of this message (1234 characters) is in X:/some/path.md" in out, out)
        check("pointer: says to Read it NOW, before answering",
              "Read it with the Read tool NOW, before answering" in out, out)
        check("pointer: names what a pickup's file holds",
              "On a handoff pickup it holds the rest of the note, the user's own words "
              "and the memory index." in out, out)
        check("pointer: does not claim every cut message is a handoff note",
              "too long for one hook message" not in out and len(out) < 400, str(len(out)))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_two_cuts_in_a_row_for_one_session_write_two_overflow_files():
    home = make_home({})
    try:
        code = (FIT_CODE +
                "for _ in range(2):\n"
                "    guard.fit_hook_text({'hookSpecificOutput': {'additionalContext': text}},"
                " 'abcd1234-5678')\n")
        p = guard_call(home, code)
        files = overflow_files(home)
        check("overflow-name: two cuts, two files", len(files) == 2, str(files) + p.stderr[-300:])
        check("overflow-name: the name carries microseconds",
              all(re.match(r"^abcd1234-\d{8}-\d{6}-\d{6}\.md$", f) for f in files), str(files))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_a_stale_stub_is_retired_even_behind_fifty_old_real_notes():
    """The retire loop looked at the oldest 50 waiting notes BEFORE asking which were stubs,
    so 50 old real notes starved a stale stub behind them forever."""
    home = make_home({})
    try:
        d = os.path.join(home, ".claude", "handoff")
        for i in range(51):
            p = os.path.join(d, KEY + ".real%04d.md" % i)
            with open(p, "w", encoding="utf-8") as f:
                f.write("HANDOFF LABEL: Real %d (1 Aug)\n\nprose.\n" % i)
            t = time.time() - 60 * 86400 - i
            os.utime(p, (t, t))
        stub = os.path.join(d, KEY + ".lateStub.md")
        with open(stub, "w", encoding="utf-8") as f:
            f.write("HANDOFF LABEL: Unsaved chat lateStub\n\n<!-- " + STUB_MARKER + " -->\n\nx\n")
        t = time.time() - 10 * 86400
        os.utime(stub, (t, t))
        expect_clean(boot(home, CWD, "newchatR"), "retire-behind")
        names = handoff_files(home)
        check("retire-behind: the stale stub was renamed out of the way",
              not os.path.exists(stub) and any(".lateStub.used-stale-" in n for n in names),
              repr([n for n in names if "late" in n]))
        check("retire-behind: all 51 real notes are untouched",
              len([n for n in names if ".real" in n and n.endswith(".md")
                   and ".used" not in n]) == 51, str(len(names)))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_sweep_writes_a_stub_only_for_a_chat_idle_under_48_hours():
    home = make_home({})
    try:
        stale_chat(home, "idle40h1", 40 * 3600)        # 40 h: still a thread to resume
        stale_chat(home, "idle3day", 3 * 86400)        # 72 h: history now
        expect_clean(boot(home, CWD, "newchatS"), "sweep-48h")
        got = handoff_files(home)
        check("sweep-48h: a chat idle 40 h gets a stub", KEY + ".idle40h1.md" in got, repr(got))
        check("sweep-48h: a chat idle 3 days does not", KEY + ".idle3day.md" not in got, repr(got))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_session_end_skips_a_consumed_note_and_a_tiny_chat():
    """The sweep already refuses both; SessionEnd used to re-create a stub for a chat whose
    note had just been picked up (the .used-* file), and for a chat too small to resume."""
    home = make_home({})
    try:
        stale_chat(home, "consum01", 3600)
        d = os.path.join(home, ".claude", "handoff")
        used = KEY + ".consum01.used-20261002-000000.md"
        with open(os.path.join(d, used), "w", encoding="utf-8") as f:
            f.write("HANDOFF LABEL: Real -1 (2 Oct)\n\nprose.\n")
        expect_clean(run_session_end(home, "consum01"), "end-consumed")
        check("end-consumed: no stub next to a consumed note",
              handoff_files(home) == [used], repr(handoff_files(home)))
        write_big_chat(home, "tinychat", time.time() - 3600, 130_000)
        expect_clean(run_session_end(home, "tinychat"), "end-tiny")
        check("end-tiny: a chat under FRESH_BYTES gets nothing",
              not [n for n in handoff_files(home) if "tinychat" in n], repr(handoff_files(home)))
        stale_chat(home, "bigchat1", 3600)
        expect_clean(run_session_end(home, "bigchat1"), "end-big")
        check("end-big: CONTROL - a big chat with no note still gets its stub",
              KEY + ".bigchat1.md" in handoff_files(home), repr(handoff_files(home)))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_manifest_cache_and_sort_failures_are_logged_but_missing_files_are_not():
    home = make_home({})
    try:
        lp = os.path.join(home, ".claude", "context-audit.log")

        def errs():
            if not os.path.exists(lp):
                return []
            with open(lp, encoding="utf-8") as f:
                return [l for l in f if " error: " in l]
        code = ("import os\n"
                "guard.label_list_path('some label')\n"
                "guard._resolve_key_dir('D--Claude')\n"
                "tp = os.path.join(guard.PROJECTS, 'D--Claude', 'x.jsonl')\n"
                "open(os.path.join(guard.handoff_dir(), 'D--Claude.bbbbbbbb.md'), 'w').write('x')\n"
                "open(os.path.join(guard.handoff_dir(), 'D--Claude.cccccccc.md'), 'w').write('x')\n"
                "def gone(p): raise FileNotFoundError(p)\n"
                "os.path.getmtime = gone\n"
                "guard.all_notes(tp)\n")
        guard_call(home, code)
        check("quiet-sites: a missing manifest and a vanished note leave no error line",
              errs() == [], repr(errs()))
        code = ("import os\n"
                "os.makedirs(os.path.dirname(guard.MANIFEST), exist_ok=True)\n"
                "open(guard.MANIFEST, 'wb').write(b'{ not json')\n"
                "guard.label_list_path('some label')\n"
                "guard._resolve_key_dir('D--Claude')\n"
                "tp = os.path.join(guard.PROJECTS, 'D--Claude', 'x.jsonl')\n"
                "open(os.path.join(guard.handoff_dir(), 'D--Claude.aaaaaaaa.md'), 'w').write('x')\n"
                "open(os.path.join(guard.handoff_dir(), 'D--Claude.dddddddd.md'), 'w').write('x')\n"
                "def denied(p): raise PermissionError(p)\n"
                "os.path.getmtime = denied\n"
                "guard.all_notes(tp)\n")
        guard_call(home, code)
        e = " ".join(errs())
        check("quiet-sites: a corrupt manifest is logged by label_list_path",
              " error: label_list_path:" in e, e[-400:])
        check("quiet-sites: a corrupt manifest is logged by _resolve_key_dir",
              " error: _resolve_key_dir:" in e, e[-400:])
        check("quiet-sites: a note whose mtime cannot be read is logged by all_notes",
              " error: all_notes:" in e, e[-400:])
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_a_reversed_two_word_keyword_is_not_scored_in_strict_mode():
    """Word order decides the hit: 'phone android emulator' carries the words of the phrase
    keyword 'android phone' backwards and also matches the keyword 'emulator'. Only the
    keyword that really appears may be counted."""
    home = kw_home(SCORE_MEMS)
    try:
        res = kw_json(home, "import json\nprint(json.dumps([[e['slug'], h] for _s, _m, e, h in "
                            "guard.score_memories('phone android emulator', "
                            "guard.memory_catalogue(), set(), True)]))")
        check("kw-order: only 'emulator' scores, never the reversed phrase",
              res == [["emulator-notes", ["emulator"]]], repr(res))
        res = kw_json(home, "import json\nprint(json.dumps([[e['slug'], h] for _s, _m, e, h in "
                            "guard.score_memories('android phone emulator', "
                            "guard.memory_catalogue(), set(), True)]))")
        check("kw-order: CONTROL - in order, both of its keywords score",
              res == [["emulator-notes", ["android phone", "emulator"]]], repr(res))
    finally:
        rm_tree(home)


# ------------------------------------- change 7: the finished chat archives itself
WRITER_NOTE = ("HANDOFF LABEL: context guard -4 (19 Sep)\n"
               "WRITTEN BY: Context Guard -3 (18 Sep)\n\n"
               "# Handoff - the guard\n\nbody-CONTEXTGUARD\n")


def test_pickup_names_the_finished_chat_for_archiving():
    """19 Sep 2026: his sidebar held 47 dead chats and he asked whether they could be
    archived when a new chat takes a note. Pickup is exactly the right trigger - a note
    being consumed is proof the chat that wrote it is done."""
    home = make_home({KEY + ".9e19c7ab.md": WRITER_NOTE})
    try:
        p = run(home, "arch0001-new", "context guard")
        ctx = context_of(p)
        expect_clean(p, "pickup/archive")
        # NOT '"Context Guard -3 (18 Sep)" in ctx' - the note body is delivered
        # verbatim and the title lives in it, so that check passed before the feature
        # existed. Assert the title inside the GUARD'S OWN sentence; only extraction
        # can produce that.
        check("pickup/archive: names the finished chat by its EXACT title",
              "is titled 'Context Guard -3 (18 Sep)'" in ctx, repr(ctx[:1200]))
        check("pickup/archive: names the tool that does it",
              "archive_session" in ctx, repr(ctx[:1200]))
        check("pickup/archive: still delivers the note itself",
              "body-CONTEXTGUARD" in ctx, repr(ctx[:200]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_pickup_files_the_new_chat_in_the_old_chats_group():
    """His ask, 1 Oct 2026: "is there a way to make the chat appear where its pervious one was
    like this chat was in the group "Apps"". The tidy's list_sessions row already carries the
    old chat's group, so the new chat moves itself there before it archives the old one."""
    home = make_home({KEY + ".9e19c7ab.md": WRITER_NOTE})
    try:
        p = run(home, "grp00001-new", "context guard")
        ctx = context_of(p)
        expect_clean(p, "pickup/group")
        check("pickup/group: moves THIS chat with move_sessions into the old row's group",
              "mcp__ccd_sidebar__move_sessions" in ctx and "group.id" in ctx
              and "['self']" in ctx, repr(ctx[:1500]))
        check("pickup/group: the move comes before the archive",
              0 <= ctx.find("move_sessions") < ctx.find("archive_session on it"),
              repr(ctx[:1500]))
        check("pickup/group: says where this chat's pinned flag lives",
              "get_session with 'self'" in ctx, repr(ctx[:1500]))
    finally:
        shutil.rmtree(home, ignore_errors=True)
    home = make_home({KEY + ".9e19c7ab.md": NOTE_CTX})
    try:
        ctx = context_of(run(home, "grp00002-new", "context guard"))
        check("pickup/group: no writer line, no move", "move_sessions" not in ctx,
              repr(ctx[-500:]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_pickup_without_a_writer_line_says_nothing_about_archiving():
    """Every note written before today lacks the line, and so will any note a chat
    forgets. Missing must be SILENT - not an error, and above all not a guess. The
    cost of archiving the wrong chat is his trust, which is not worth a heuristic."""
    home = make_home({KEY + ".9e19c7ab.md": NOTE_CTX})
    try:
        p = run(home, "arch0002-new", "context guard")
        ctx = context_of(p)
        expect_clean(p, "pickup/no-writer")
        check("pickup/no-writer: says nothing at all about archiving",
              "archive_session" not in ctx, repr(ctx[-500:]))
        check("pickup/no-writer: still delivers the note",
              "body-CONTEXTGUARD" in ctx, repr(ctx[:200]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_a_writer_line_deep_in_the_body_is_not_the_writer():
    """The marker bug of 19 Sep, pre-empted rather than repeated. That one cost a
    287-line note: a sentinel was matched anywhere in the file, and the note that
    EXPLAINED the sentinel was destroyed by it. A note is allowed to discuss the
    convention. Only the head of the file is structure; everything below is prose."""
    body = (NOTE_CTX.rstrip("\n")
            + "\n\nEvery note must carry WRITTEN BY: <that chat's title> near the top "
              "so the next chat knows which session is finished.\n")
    home = make_home({KEY + ".9e19c7ab.md": body})
    try:
        p = run(home, "arch0003-new", "context guard")
        ctx = context_of(p)
        expect_clean(p, "pickup/deep-writer")
        check("pickup/deep-writer: prose ABOUT the convention is not a writer line",
              "archive_session" not in ctx, repr(ctx[-500:]))
        check("pickup/deep-writer: and the note survives to be delivered",
              "body-CONTEXTGUARD" in ctx, repr(ctx[:200]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_note_instruction_asks_for_the_writer_line():
    """A convention nothing asks for never appears. If the instruction that demands
    the note does not also demand the line, the archive step is dead code for every
    note ever written - green tests about a feature that can never fire."""
    home = make_home({})
    try:
        write_transcript(home, "arch0004", 240_000)
        p = run(home, "arch0004", "carry on")
        ctx = context_of(p)
        expect_clean(p, "note-instruction")
        check("note-instruction: the handoff warning fired",
              "THIS CHAT IS NOW" in ctx, repr(ctx[:200]))
        check("note-instruction: asks the chat to record its own title",
              "WRITTEN BY:" in ctx, repr(ctx[-900:]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_archive_instruction_survives_a_truncated_tail():
    """MEASURED IN THE WILD 19 Sep 2026, within the hour of shipping the feature.

    QIX -21 picked up a 24,089-char note and never archived anything. guard.py's own
    truncation had NOT fired - the app's persisted-output cut took the end of the
    injected text, and the archive instruction was sitting at the very end because I
    put it there so a test could find it in ctx[-700:]. A test convenience placed a
    live instruction in the one position that gets discarded.

    The note body is the part that is allowed to be cut. Instructions go in FRONT of
    it. Asserted as an ORDERING, because "it is present" was true in the broken
    version too - present in a region nothing downstream kept."""
    home = make_home({KEY + ".9e19c7ab.md": WRITER_NOTE})
    try:
        p = run(home, "arch0005-new", "context guard")
        ctx = context_of(p)
        expect_clean(p, "pickup/order")
        i_arch = ctx.find("archive_session")
        i_body = ctx.find("body-CONTEXTGUARD")
        check("pickup/order: the instruction and the note are both delivered",
              i_arch >= 0 and i_body >= 0, "arch=%d body=%d" % (i_arch, i_body))
        check("pickup/order: the archive instruction comes BEFORE the note body",
              0 <= i_arch < i_body, "arch=%d body=%d" % (i_arch, i_body))
    finally:
        shutil.rmtree(home, ignore_errors=True)


# ------------------------- change 8: a finished chat is archived for SURE, 29 Sep 2026
# The pickup tells the new chat once, in words, and that fails three ways: the app refuses
# while the old chat is open on screen and nothing retries; the model never makes the call;
# and once one is missed no later chat hears of it. So the titles go on a list on disk, the
# success is detected from the transcripts (never from the model's word), and every chat
# that is talking is asked again until it is done.
PA_TITLE = "Harbor -6 (29 Sep)"
PA_ODD = "Nova's \"Calc\" (2) -3 (1 Oct)"


def pa_note(writer, label="harbor", body="body-HARBOR"):
    return ("HANDOFF LABEL: " + label + "\nWRITTEN BY: " + writer + "\n\n# Handoff\n\n"
            + body + "\n")


def pa_file(home):
    return os.path.join(home, ".claude", "context-guard", "pending-archive.json")


def pa_read(home):
    try:
        with open(pa_file(home), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def pa_write(home, entries):
    os.makedirs(os.path.dirname(pa_file(home)), exist_ok=True)
    with open(pa_file(home), "w", encoding="utf-8") as f:
        json.dump(entries, f)


def pa_entry(title, age=0, offers=0, since_offer=None, watch=None):
    now = time.time()
    return {"title": title, "added": now - age, "offers": offers,
            "last_offered": (now - since_offer) if since_offer is not None else 0,
            "watch": watch or []}


def pa_id(title):
    return hashlib.sha1(title.encode("utf-8")).hexdigest()[:8]


def pa_text(title):
    return 'Archived session local_9eea7bbb-1d12-453e-979a-113e2cf3d0cb ("%s").' % title


def pa_success_line(title, escaped=True, as_list=False):
    """The app's own success text, as it lands in a transcript: a tool_result line whose
    JSON has already escaped the quotes. escaped=False gives the BARE text with no JSON
    around it; as_list=True gives the content as a list of text blocks."""
    text = pa_text(title)
    body = [{"type": "text", "text": text}] if as_list else text
    rec = {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "toolu_1", "content": body}]}}
    return json.dumps(rec) + "\n" if escaped else text + "\n"


def pa_rec_line(kind, title, text=None):
    """A NON-genuine line that still carries the success words: an assistant quoting it, a
    user pasting it, a Read of a file that holds it, a tool_use writing it."""
    text = text if text is not None else pa_text(title)
    if kind == "assistant":
        rec = {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "It said: " + text}]}}
    elif kind == "user":
        rec = {"type": "user", "message": {"role": "user", "content": text}}
    elif kind == "user-block":
        rec = {"type": "user", "message": {"role": "user", "content": [
            {"type": "text", "text": text}]}}
    elif kind == "read":
        rec = {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_2",
             "content": "     1\tnotes\n     2\t" + text + "\n     3\tmore"}]}}
    elif kind == "read-list":
        rec = {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_2", "content": [
                {"type": "text", "text": "log: " + text}]}]}}
    else:
        rec = {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "toolu_3", "name": "Write",
             "input": {"file_path": "a.txt", "content": text}}]}}
    return json.dumps(rec) + "\n"


def pa_append(path, text):
    with open(path, "a", encoding="utf-8") as f:
        f.write(text)


def pa_whole_object(p, name):
    """stdout must be exactly ONE json object: everything reading it json.loads the whole."""
    try:
        obj = json.loads((p.stdout or "").strip())
    except Exception:
        check(name + ": stdout is exactly one json object", False, repr((p.stdout or "")[:300]))
        return {}
    check(name + ": stdout is exactly one json object", isinstance(obj, dict), repr(obj)[:200])
    return obj


def pa_ctx(p):
    return context_of(p)


def test_a_consumed_pickup_adds_the_writer_to_the_pending_list():
    home = make_home({KEY + ".aaaa1111.md": pa_note(PA_TITLE)})
    try:
        p = run(home, "pa1-new", "harbor")
        expect_clean(p, "pa-add")
        got = pa_read(home)
        check("pa-add: one entry, keyed by the exact title",
              isinstance(got, list) and [e.get("title") for e in got] == [PA_TITLE], repr(got))
        e = (got or [{}])[0]
        check("pa-add: offers starts at 0 and last_offered at 0",
              e.get("offers") == 0 and e.get("last_offered") == 0, repr(e))
        check("pa-add: added is now", abs(time.time() - float(e.get("added", 0))) < 120, repr(e))
        tp = os.path.join(home, ".claude", "projects", KEY, "pa1-new.jsonl")
        check("pa-add: the pickup chat's own transcript and size are watched",
              e.get("watch") == [[tp, 0]], repr(e.get("watch")))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_menu_adds_nothing_to_the_pending_list():
    home = make_home({KEY + ".aaaa1111.md": pa_note(PA_TITLE, "harbor", "body-HARBOR"),
                      KEY + ".bbbb2222.md": pa_note("Nova Calc -2 (29 Sep)", "nova calc",
                                                    "body-NOVA")})
    try:
        p = run(home, "pa2-new", "hello there")
        expect_clean(p, "pa-menu")
        check("pa-menu: it really was the menu",
              "SAVED THREADS ARE WAITING" in pa_ctx(p), repr(pa_ctx(p)[:200]))
        check("pa-menu: nothing was consumed, so nothing is listed",
              not pa_read(home), repr(pa_read(home)))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_a_second_pickup_of_the_same_title_is_not_a_duplicate():
    home = make_home({KEY + ".aaaa1111.md": pa_note(PA_TITLE)})
    try:
        run(home, "pa3-one", "harbor")
        with open(os.path.join(home, ".claude", "handoff", KEY + ".cccc3333.md"), "w",
                  encoding="utf-8") as f:
            f.write(pa_note(PA_TITLE))
        p = run(home, "pa3-two", "harbor")
        expect_clean(p, "pa-dup")
        got = pa_read(home) or []
        check("pa-dup: still exactly one entry", len(got) == 1, repr(got))
        check("pa-dup: but both pickup chats are watched",
              len((got or [{}])[0].get("watch", [])) == 2, repr(got))
    finally:
        shutil.rmtree(home, ignore_errors=True)


RC_OFF = "set_remote_control"


def test_a_live_work_refusal_switches_remote_control_off_unless_away():
    """His rule, 30 Sep 2026: "make it when it is doing the handover for the next chat and
    everything that it turns off remote control unless afk is on". Measured the same day: a
    finished chat refused "live work" twice, archived at once after its phone link was
    switched off. Both archive instructions - the retry and the pickup's - carry it at the
    console, and neither does while he is away, when the phone link is how he follows along."""
    first = guard_constant("ARCHIVE_FIRST_WAIT")
    home = make_home({})
    try:
        pa_write(home, [pa_entry(PA_TITLE, age=first + 60)])
        ctx = pa_ctx(run(home, "rc-retry", "carry on"))
        check("rc-off/retry: at the console the retry switches the phone link off",
              "'" + PA_TITLE + "'" in ctx and RC_OFF in ctx and "live work" in ctx, repr(ctx[-500:]))
    finally:
        shutil.rmtree(home, ignore_errors=True)
    home = make_home({})
    try:
        arm_away(home)
        pa_write(home, [pa_entry(PA_TITLE, age=first + 60)])
        ctx = pa_ctx(run(home, "rc-retry-away", "carry on"))
        check("rc-off/retry-away: CONTROL - still offered while away",
              "'" + PA_TITLE + "'" in ctx, repr(ctx[-500:]))
        check("rc-off/retry-away: but it never touches the phone link",
              RC_OFF not in ctx, repr(ctx[-500:]))
    finally:
        shutil.rmtree(home, ignore_errors=True)
    home = make_home({KEY + ".aaaa1111.md": pa_note(PA_TITLE)})
    try:
        ctx = pa_ctx(run(home, "rc-pickup", "harbor"))
        check("rc-off/pickup: the pickup's archive step carries it too",
              "body-HARBOR" in ctx and RC_OFF in ctx, repr(ctx[-500:]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_handover_switches_its_own_remote_control_off_unless_away():
    """The same rule at the other end: the chat that hands over switches its OWN phone link
    off, so its successor's archive is not refused - but not while he is away."""
    home = make_home({})
    try:
        write_big_chat(home, "rc-hand", time.time() - 3600, 250_000)
        ctx = context_of(run(home, "rc-hand", "carry on"))
        check("rc-off/handover: at the console the handover switches its own link off",
              RC_OFF in ctx and "'self'" in ctx, repr(ctx[-600:]))
    finally:
        shutil.rmtree(home, ignore_errors=True)
    home = make_home({})
    try:
        write_big_chat(home, "rc-hand-away", time.time() - 3600, 250_000)
        arm_away(home)
        ctx = context_of(run(home, "rc-hand-away", "carry on"))
        check("rc-off/handover-away: never while he is away", RC_OFF not in ctx, repr(ctx[-600:]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_label_is_handed_over_in_a_code_box():
    """His ask, 30 Sep 2026: "can we note that the label should always be written like that in
    the red box so it would always be easy to copy??" - inline code, on its own line."""
    home = make_home({})
    try:
        write_big_chat(home, "label-box", time.time() - 3600, 250_000)
        ctx = context_of(run(home, "label-box", "carry on"))
        check("label-box: the handover asks for the label as inline code on its own line",
              "inline code" in ctx and "own line" in ctx, repr(ctx[-600:]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_handover_stops_background_work_and_ends_on_the_label():
    """1 Oct 2026: a live background task makes archive_session refuse "live work"; and his
    ask that the label be the very last thing in the reply (console only)."""
    home = make_home({})
    try:
        write_big_chat(home, "bg-con", time.time() - 3600, 250_000)
        ctx = context_of(run(home, "bg-con", "carry on"))
        check("bg/console: TaskStop, run_in_background and 'very last line' are named",
              "TaskStop" in ctx and "run_in_background" in ctx and "very last line" in ctx,
              repr(ctx[-1200:]))
        check("bg/console: control - the label inline-code rule is still there",
              "inline code" in ctx and "own line" in ctx, repr(ctx[-1200:]))
    finally:
        shutil.rmtree(home, ignore_errors=True)
    home = make_home({})
    try:
        write_big_chat(home, "bg-away", time.time() - 3600, 250_000)
        arm_away(home)
        ctx = context_of(run(home, "bg-away", "carry on"))
        check("bg/away: TaskStop and run_in_background are named",
              "TaskStop" in ctx and "run_in_background" in ctx, repr(ctx[-1200:]))
        check("bg/away: no label line, so no 'very last line'",
              "very last line" not in ctx, repr(ctx[-1200:]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


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
    # Review finding, 1 Oct 2026: is_fresh walks EVERY event, build() only ours - so another
    # tool's malformed entry must not turn an install that used to work into a traceback.
    for odd in ({"hooks": "bad"}, {"hooks": [1]}, {"hooks": [{"command": 5}]}):
        home = make_home({})
        try:
            seed_settings(home, {"hooks": {"Notification": [odd]}})
            p = run_install(home)
            expect_clean(p, "tour/install-odd %s" % json.dumps(odd))
            check("tour/install-odd %s: still installs" % json.dumps(odd),
                  "Installed" in p.stdout, p.stdout[-300:] + p.stderr[-300:])
        finally:
            shutil.rmtree(home, ignore_errors=True)


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


def test_the_handover_shows_the_label_and_finishes_when_the_new_chat_reports():
    """His decision, 30 Sep 2026: no chip (a chip chat stays its parent's running task, so the
    parent's archive was refused four times); the label in the copy box, the old chat archives
    itself and the effort is still set automatically. Console only - away is unchanged."""
    home = make_home({})
    try:
        write_big_chat(home, "hand-con", time.time() - 3600, 250_000)
        p = run(home, "hand-con", "carry on")
        expect_clean(p, "handover/console")
        ctx = context_of(p)
        check("handover/console: no spawn_task chip any more",
              "spawn_task" not in ctx and "Start locally" not in ctx, repr(ctx[-900:]))
        check("handover/console: the phone link still goes off",
              "set_remote_control" in ctx, repr(ctx[-900:]))
        check("handover/console: the label goes in inline code on its own line",
              "inline code" in ctx and "own line" in ctx, repr(ctx[-900:]))
        check("handover/console: and the HANDOVER STARTED instruction is there",
              "HANDOVER STARTED" in ctx and "set_session_effort" in ctx
              and "archive_session" in ctx and "'self'" in ctx, repr(ctx[-1200:]))
    finally:
        shutil.rmtree(home, ignore_errors=True)
    home = make_home({})
    try:
        write_big_chat(home, "hand-away", time.time() - 3600, 250_000)
        arm_away(home)
        p = run(home, "hand-away", "carry on")
        expect_clean(p, "handover/away")
        ctx = context_of(p)
        check("handover/away: no handover message and no phone-link change while he is away",
              "spawn_task" not in ctx and "HANDOVER STARTED" not in ctx
              and "set_remote_control" not in ctx, repr(ctx[-900:]))
        check("handover/away: control - the path rule is still there", "NEVER hand him a filesystem path" in ctx,
              repr(ctx[-400:]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_pickup_reports_to_the_old_chat_only_when_the_note_has_an_effort():
    note = ("HANDOFF LABEL: Mango -4 (1 Jan)" + chr(10) + "WRITTEN BY: Mango -3 (1 Jan)" + chr(10)
            + "NEXT CHAT EFFORT: medium - x" + chr(10) * 2 + "body-MANGO" + chr(10))
    home = make_home({KEY + ".aaaaaaaa.md": note})
    try:
        p = run(home, "hand-pick1", "mango")
        expect_clean(p, "handover/pickup")
        ctx = context_of(p)
        check("handover/pickup: finds the writer by its exact title and sends the message",
              "list_sessions" in ctx and "'Mango -3 (1 Jan)'" in ctx and "send_message" in ctx
              and "effort=medium" in ctx and "HANDOVER STARTED" in ctx, repr(ctx[:1800]))
        check("handover/pickup: a label-opened chat has no parent to ask",
              "parentSessionId" not in ctx, repr(ctx[:1800]))
        # the pickup also tells the new chat to archive the note's writer "right after the
        # rename" - if that wins the race, the writer never sets the effort. So the chat
        # that was sent HANDOVER STARTED must be left out of the sidebar tidy.
        check("handover/pickup: the chat it messaged is left out of the tidy",
              "do NOT archive" in ctx and "archives itself" in ctx, repr(ctx[:1800]))
    finally:
        shutil.rmtree(home, ignore_errors=True)
    home = make_home({KEY + ".aaaaaaaa.md": NOTE_MANGO})
    try:
        p = run(home, "hand-pick2", "mango")
        expect_clean(p, "handover/pickup-none")
        check("handover/pickup-none: no effort line, no HANDOVER STARTED",
              "HANDOVER STARTED" not in context_of(p), repr(context_of(p)[:600]))
    finally:
        shutil.rmtree(home, ignore_errors=True)
    note = ("HANDOFF LABEL: Mango -4 (1 Jan)" + chr(10)
            + "NEXT CHAT EFFORT: medium - x" + chr(10) * 2 + "body-MANGO" + chr(10))
    home = make_home({KEY + ".aaaaaaaa.md": note})
    try:
        p = run(home, "hand-pick3", "mango")
        expect_clean(p, "handover/pickup-nowriter")
        check("handover/pickup-nowriter: an effort but no real writer, no HANDOVER STARTED",
              "HANDOVER STARTED" not in context_of(p), repr(context_of(p)[:600]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_an_entry_is_offered_only_when_due():
    home = make_home({})
    try:
        first = guard_constant("ARCHIVE_FIRST_WAIT")
        every = guard_constant("ARCHIVE_EVERY")
        pa_write(home, [pa_entry(PA_TITLE, age=first - 60)])
        p = run(home, "pa4-a", "carry on")
        expect_clean(p, "pa-due/early")
        check("pa-due: nothing is offered before the first wait",
              "archive_session" not in pa_ctx(p), repr(pa_ctx(p)[:200]))
        check("pa-due: and the entry is untouched", (pa_read(home) or [{}])[0].get("offers") == 0,
              repr(pa_read(home)))
        pa_write(home, [pa_entry(PA_TITLE, age=first + 60)])
        p = run(home, "pa4-b", "carry on")
        expect_clean(p, "pa-due/first")
        ctx = pa_ctx(p)
        check("pa-due: offered once the first wait has passed",
              "'" + PA_TITLE + "'" in ctx and "archive_session" in ctx, repr(ctx[:300]))
        e = (pa_read(home) or [{}])[0]
        check("pa-due: the offer is counted and stamped",
              e.get("offers") == 1 and time.time() - e.get("last_offered", 0) < 120, repr(e))
        tp = os.path.join(home, ".claude", "projects", KEY, "pa4-b.jsonl")
        check("pa-due: the chat that was asked is watched from then on",
              [tp, 0] in e.get("watch", []), repr(e.get("watch")))
        p = run(home, "pa4-c", "carry on")
        check("pa-due: not again inside the retry gap", "archive_session" not in pa_ctx(p),
              repr(pa_ctx(p)[:200]))
        pa_write(home, [pa_entry(PA_TITLE, age=first + 3000, offers=1, since_offer=every + 60)])
        p = run(home, "pa4-d", "carry on")
        check("pa-due: and again after the retry gap", "'" + PA_TITLE + "'" in pa_ctx(p),
              repr(pa_ctx(p)[:300]))
        check("pa-due: the second offer is counted", (pa_read(home) or [{}])[0].get("offers") == 2,
              repr(pa_read(home)))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_one_instruction_covers_every_due_title_and_stays_short():
    home = make_home({})
    try:
        pa_write(home, [pa_entry(PA_TITLE, age=500), pa_entry("Nova Calc -3 (1 Oct)", age=500)])
        p = run(home, "pa5-a", "carry on")
        expect_clean(p, "pa-one")
        ctx = pa_ctx(p)
        check("pa-one: both titles are in the one instruction",
              "'" + PA_TITLE + "'" in ctx and "'Nova Calc -3 (1 Oct)'" in ctx, repr(ctx[:400]))
        check("pa-one: it is silent, exact-match and safe",
              "do not mention" in ctx and "list_sessions" in ctx and "not running" in ctx
              and "this chat" in ctx, repr(ctx[:600]))
        check("pa-one: it asks for 200 sessions (the default 20 would make an older chat "
              "read as 'gone') but not the archived ones (a 12k-token reply per offer)",
              "limit 200" in ctx and "include_archived" not in ctx, repr(ctx[:700]))
        check("pa-one: an already-archived or unmatched title is done, by short id",
              "already archived" in ctx and "nothing matches" in ctx
              and "--archived <id>" in ctx, repr(ctx[:900]))
        check("pa-one: each title carries its id, and the command needs no quoting",
              "[id " + pa_id(PA_TITLE) + "]" in ctx
              and "[id " + pa_id("Nova Calc -3 (1 Oct)") + "]" in ctx
              and "--archived \"" not in ctx and GUARD in ctx, repr(ctx[:900]))
        check("pa-one: it says the live list leaves archived chats out",
              "leaves archived chats out" in ctx, repr(ctx[:900]))
        check("pa-one: it is short (under 115 words)", len(ctx.split()) < 115,
              str(len(ctx.split())))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_offer_merges_into_a_message_without_context_and_reaches_a_paused_chat():
    home = make_home({})
    try:
        arm_away(home)                       # the away notice is a systemMessage, no context
        pa_write(home, [pa_entry(PA_TITLE, age=500)])
        p = run(home, "pa6-away", "carry on")
        expect_clean(p, "pa-merge")
        obj = pa_whole_object(p, "pa-merge/away")
        check("pa-merge: the existing systemMessage survived",
              bool(obj.get("systemMessage")), repr(obj)[:300])
        check("pa-merge: the retry is added as a fresh additionalContext with one event name",
              "'" + PA_TITLE + "'" in pa_ctx_of(obj) and (obj.get("hookSpecificOutput") or {}
                                                        ).get("hookEventName") ==
              "UserPromptSubmit", repr(obj)[:300])
    finally:
        shutil.rmtree(home, ignore_errors=True)
    home = make_home({})
    try:
        write_big_chat(home, "pa6-paused", time.time() - 3600, 240_000)   # past the 225k floor
        run_pause(home, "pa6-paused", "reading")
        pa_write(home, [pa_entry(PA_TITLE, age=500)])
        p = run(home, "pa6-paused", "next please")
        expect_clean(p, "pa-paused")
        obj = pa_whole_object(p, "pa-paused")
        check("pa-paused: a paused chat is still asked to tidy the sidebar",
              "'" + PA_TITLE + "'" in (obj.get("hookSpecificOutput") or {}).get(
                  "additionalContext", ""), repr(obj)[:300])
        check("pa-paused: and hears nothing about its context",
              "STOP - THIS CHAT" not in json.dumps(obj), repr(obj)[:300])
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_away_mode_does_not_suppress_the_retry():
    home = make_home({})
    try:
        arm_away(home)
        pa_write(home, [pa_entry(PA_TITLE, age=500)])
        p = run(home, "pa7-a", "carry on")
        expect_clean(p, "pa-away")
        check("pa-away: archiving is silent, so away mode still asks for it",
              "'" + PA_TITLE + "'" in pa_ctx(p), repr((p.stdout or "")[:300]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_success_is_read_from_the_transcript_and_only_from_the_stored_offset():
    home = make_home({})
    try:
        tp = os.path.join(home, ".claude", "projects", KEY, "pa8-watched.jsonl")
        pa_append(tp, pa_success_line(PA_TITLE))            # BEFORE the stored offset
        off = os.path.getsize(tp)
        pa_write(home, [pa_entry(PA_TITLE, age=500, watch=[[tp, off]])])
        p = run(home, "pa8-a", "carry on")
        expect_clean(p, "pa-ok/before")
        check("pa-ok: text before the stored offset does not count",
              [e["title"] for e in (pa_read(home) or [])] == [PA_TITLE], repr(pa_read(home)))
        check("pa-ok: so the retry still goes out", "'" + PA_TITLE + "'" in pa_ctx(p),
              repr(pa_ctx(p)[:200]))
    finally:
        shutil.rmtree(home, ignore_errors=True)
    home = make_home({})
    try:
        tp = os.path.join(home, ".claude", "projects", KEY, "pa8-watched.jsonl")
        pa_append(tp, "an ordinary line\n")
        off = os.path.getsize(tp)
        pa_write(home, [pa_entry(PA_TITLE, age=500, watch=[[tp, off]]),
                        pa_entry("Nova Calc -3 (1 Oct)", age=500, watch=[[tp, off]])])
        pa_append(tp, pa_success_line(PA_TITLE))            # AFTER it, JSON-escaped
        p = run(home, "pa8-b", "carry on")
        expect_clean(p, "pa-ok/after")
        left = [e["title"] for e in (pa_read(home) or [])]
        check("pa-ok: the escaped success line removes exactly that entry",
              left == ["Nova Calc -3 (1 Oct)"], repr(left))
        check("pa-ok: and that title is not offered again",
              "'" + PA_TITLE + "'" not in pa_ctx(p) and "'Nova Calc -3 (1 Oct)'" in pa_ctx(p),
              repr(pa_ctx(p)[:300]))
    finally:
        shutil.rmtree(home, ignore_errors=True)
    home = make_home({})
    try:
        tp = os.path.join(home, ".claude", "projects", KEY, "pa8-plain.jsonl")
        pa_append(tp, "x\n")
        pa_write(home, [pa_entry(PA_TITLE, age=500, watch=[[tp, 2]])])
        pa_append(tp, pa_success_line(PA_TITLE, as_list=True))     # content as text blocks
        p = run(home, "pa8-c", "carry on")
        check("pa-ok: a tool_result whose content is a list of text blocks counts",
              not pa_read(home), repr(pa_read(home)))
        check("pa-ok: and nothing is offered", "archive_session" not in pa_ctx(p),
              repr(pa_ctx(p)[:200]))
    finally:
        shutil.rmtree(home, ignore_errors=True)
    home = make_home({})
    try:
        tp = os.path.join(home, ".claude", "projects", KEY, "pa8-other.jsonl")
        pa_append(tp, "x\n")
        pa_write(home, [pa_entry(PA_TITLE, age=500, watch=[[tp, 2]])])
        pa_append(tp, pa_success_line(PA_TITLE + " and more"))
        pa_append(tp, pa_success_line("Old " + PA_TITLE))
        run(home, "pa8-d", "carry on")
        check("pa-ok: another chat's title is not this title",
              [e["title"] for e in (pa_read(home) or [])] == [PA_TITLE], repr(pa_read(home)))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_only_a_genuine_tool_result_clears_an_entry():
    """12 of 56 non-genuine 'Archived session local_' lines in real transcripts matched the
    first, loose version: an assistant quoting it, a user pasting it, a Read of a file that
    holds it, a tool_use writing it. Only a tool_result whose WHOLE text is the app's line
    counts."""
    for kind in ("assistant", "user", "user-block", "read", "read-list", "tool_use"):
        home = make_home({})
        try:
            tp = os.path.join(home, ".claude", "projects", KEY, "pa8k-watched.jsonl")
            pa_append(tp, "x\n")
            pa_write(home, [pa_entry(PA_TITLE, age=500, watch=[[tp, 2]])])
            pa_append(tp, pa_rec_line(kind, PA_TITLE))
            p = run(home, "pa8k-a", "carry on")
            expect_clean(p, "pa-strict/" + kind)
            check("pa-strict: a " + kind + " line does NOT clear the entry",
                  [e["title"] for e in (pa_read(home) or [])] == [PA_TITLE], repr(pa_read(home)))
        finally:
            shutil.rmtree(home, ignore_errors=True)
    home = make_home({})
    try:
        tp = os.path.join(home, ".claude", "projects", KEY, "pa8k-watched.jsonl")
        pa_append(tp, "x\n")
        pa_write(home, [pa_entry(PA_TITLE, age=500, watch=[[tp, 2]])])
        pa_append(tp, "this is not json but says " + pa_text(PA_TITLE) + "\n")
        run(home, "pa8k-b", "carry on")
        check("pa-strict: a line that is not JSON does not clear it either",
              len(pa_read(home) or []) == 1, repr(pa_read(home)))
        pa_append(tp, pa_success_line(PA_TITLE))
        run(home, "pa8k-c", "carry on")
        check("pa-strict: the genuine line still does", not pa_read(home), repr(pa_read(home)))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_old_text_before_the_offset_is_not_reread_when_new_text_follows():
    """The mutation f.seek(0) passed the first suite: with nothing after the stored offset
    the read never ran. Here an OLD success line sits before the offset and NEW text that
    does not match follows it, so a read that starts at 0 would clear the entry."""
    home = make_home({})
    try:
        tp = os.path.join(home, ".claude", "projects", KEY, "pa8o-watched.jsonl")
        pa_append(tp, pa_success_line(PA_TITLE))
        off = os.path.getsize(tp)
        pa_append(tp, "an ordinary new line\n")
        pa_write(home, [pa_entry(PA_TITLE, age=500, watch=[[tp, off]])])
        p = run(home, "pa8o-a", "carry on")
        expect_clean(p, "pa-old")
        check("pa-old: the entry stays", [e["title"] for e in (pa_read(home) or [])] == [PA_TITLE],
              repr(pa_read(home)))
        w = (pa_read(home) or [{}])[0].get("watch", [[0, -1]])
        check("pa-old: and the offset moved on past the new line only",
              [x for x in w if x[0] == tp] == [[tp, os.path.getsize(tp)]], repr(w))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_a_success_line_split_across_two_runs_is_still_found():
    home = make_home({})
    try:
        tp = os.path.join(home, ".claude", "projects", KEY, "pa8s-watched.jsonl")
        pa_append(tp, "first\n")
        off = os.path.getsize(tp)
        pa_write(home, [pa_entry(PA_TITLE, age=500, watch=[[tp, off]])])
        line = pa_success_line(PA_TITLE)
        half = len(line) // 2
        with open(tp, "ab") as f:
            f.write(line.encode("utf-8")[:half])              # a write caught mid-line
        p = run(home, "pa8s-a", "carry on")
        expect_clean(p, "pa-split/1")
        w = [x for x in (pa_read(home) or [{}])[0].get("watch", []) if x[0] == tp]
        check("pa-split: an unfinished line is not consumed - the offset stays put",
              w == [[tp, off]], repr(w))
        with open(tp, "ab") as f:
            f.write(line.encode("utf-8")[half:])
        run(home, "pa8s-b", "carry on")
        check("pa-split: once the line is whole the next run finds it", not pa_read(home),
              repr(pa_read(home)))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_a_watched_offset_is_never_moved_forward_by_an_offer():
    """After a capped read the offset sits BEHIND the end of the file on purpose; an offer
    that re-stamped it with the file size would skip everything in between."""
    home = make_home({})
    try:
        cap = guard_constant("ARCHIVE_READ_CAP")
        tp = os.path.join(home, ".claude", "projects", KEY, "pa8w-self.jsonl")
        with open(tp, "wb") as f:
            f.write(b"a" * (cap + 1000))
        pa_write(home, [pa_entry(PA_TITLE, age=500, watch=[[tp, 0]])])
        p = run(home, "pa8w-self", "carry on")           # this chat IS the watched file
        expect_clean(p, "pa-keep")
        check("pa-keep: the offer went out", "'" + PA_TITLE + "'" in pa_ctx(p),
              repr(pa_ctx(p)[:200]))
        w = (pa_read(home) or [{}])[0].get("watch", [])
        check("pa-keep: the offset is where the capped read left it, not the file size",
              w == [[tp, cap]], repr(w))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_offset_moves_forward_and_a_run_reads_at_most_the_cap():
    home = make_home({})
    try:
        cap = guard_constant("ARCHIVE_READ_CAP")
        tp = os.path.join(home, ".claude", "projects", KEY, "pa9-big.jsonl")
        with open(tp, "wb") as f:
            f.write(b"a" * (cap + 1000) + b"\n")
        pa_append(tp, pa_success_line(PA_TITLE))               # beyond the first 8 MB
        pa_write(home, [pa_entry(PA_TITLE, age=30, watch=[[tp, 0]])])
        run(home, "pa9-a", "carry on")
        e = (pa_read(home) or [{}])[0]
        check("pa-cap-read: one run moved the offset by no more than the cap",
              e.get("watch", [[0, -1]])[0][1] == cap, repr(e.get("watch")))
        check("pa-cap-read: and the success past it is not seen yet",
              e.get("title") == PA_TITLE, repr(e))
        run(home, "pa9-b", "carry on")
        check("pa-cap-read: the next run reads on and finds it", not pa_read(home),
              repr(pa_read(home)))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_an_awkward_title_round_trips_through_pickup_offer_success_and_the_flag():
    home = make_home({KEY + ".aaaa1111.md": pa_note(PA_ODD, "nova calc", "body-NOVA")})
    try:
        run(home, "pa10-new", "nova calc")
        got = pa_read(home) or []
        check("pa-odd: the pickup stored the title byte for byte",
              [e.get("title") for e in got] == [PA_ODD], repr(got))
        got[0]["added"] = time.time() - 500
        pa_write(home, got)
        p = run(home, "pa10-b", "carry on")
        expect_clean(p, "pa-odd/offer")
        pa_whole_object(p, "pa-odd/offer")
        check("pa-odd: the offer carries it unchanged", "'" + PA_ODD + "'" in pa_ctx(p),
              repr(pa_ctx(p)[:400]))
        tp = os.path.join(home, ".claude", "projects", KEY, "pa10-b.jsonl")
        pa_append(tp, pa_success_line(PA_ODD))
        run(home, "pa10-c", "carry on")
        check("pa-odd: the escaped success line, quotes and all, matches", not pa_read(home),
              repr(pa_read(home)))
        pa_write(home, [pa_entry(PA_ODD, age=500)])
        p = subprocess.run([sys.executable, GUARD, "--archived", PA_ODD],
                           capture_output=True, text=True, env=child_env(home))
        expect_clean(p, "pa-odd/flag")
        check("pa-odd: --archived takes it off the list", not pa_read(home), repr(pa_read(home)))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_archived_flag_removes_an_entry_and_an_unknown_title_is_harmless():
    home = make_home({})
    try:
        pa_write(home, [pa_entry(PA_TITLE, age=500), pa_entry("Nova Calc -3 (1 Oct)", age=500)])
        p = subprocess.run([sys.executable, GUARD, "--archived", PA_TITLE],
                           capture_output=True, text=True, env=child_env(home))
        expect_clean(p, "pa-flag")
        check("pa-flag: it prints one line saying so",
              len((p.stdout or "").strip().splitlines()) == 1 and PA_TITLE in p.stdout,
              repr(p.stdout))
        check("pa-flag: the other entry stays",
              [e["title"] for e in (pa_read(home) or [])] == ["Nova Calc -3 (1 Oct)"],
              repr(pa_read(home)))
        p = subprocess.run([sys.executable, GUARD, "--archived", "No Such Chat -1"],
                           capture_output=True, text=True, env=child_env(home))
        expect_clean(p, "pa-flag/unknown")
        check("pa-flag: an unknown title says nothing matched and exits 0",
              p.returncode == 0 and "nothing matched" in (p.stdout or "").lower(), repr(p.stdout))
        check("pa-flag: and changes nothing", len(pa_read(home) or []) == 1, repr(pa_read(home)))
    finally:
        shutil.rmtree(home, ignore_errors=True)
    home = make_home({})
    try:
        p = subprocess.run([sys.executable, GUARD, "--archived", "Anything"],
                           capture_output=True, text=True, env=child_env(home))
        expect_clean(p, "pa-flag/no-file")
        check("pa-flag: no list at all is harmless", p.returncode == 0, repr(p.stderr))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_cap_drops_an_entry_and_tells_him_exactly_once():
    n = guard_constant("ARCHIVE_MAX_OFFERS")
    day = guard_constant("ARCHIVE_MAX_AGE")
    for label, entry in (("offers", pa_entry(PA_TITLE, age=5000, offers=int(n), since_offer=9000)),
                         ("age", pa_entry(PA_TITLE, age=day + 60, offers=2, since_offer=9000))):
        home = make_home({})
        try:
            pa_write(home, [entry])
            p = run(home, "pa11-a", "carry on")
            expect_clean(p, "pa-drop/" + label)
            obj = pa_whole_object(p, "pa-drop/" + label)
            msg = obj.get("systemMessage", "")
            want = ("Context Guard could not archive '" + PA_TITLE + "' after repeated tries - "
                    "it may be pinned or still open; archive it by hand from the sidebar.")
            check("pa-drop/%s: HE is told, in the sentence he was promised" % label,
                  want in msg, repr(msg))
            check("pa-drop/%s: the entry is gone and no retry goes out" % label,
                  not pa_read(home) and "archive_session" not in pa_ctx_of(obj), repr(obj)[:300])
            p = run(home, "pa11-b", "carry on")
            check("pa-drop/%s: and it never repeats" % label,
                  "could not archive" not in (p.stdout or ""), repr((p.stdout or "")[:200]))
        finally:
            shutil.rmtree(home, ignore_errors=True)
    home = make_home({KEY + ".aaaa1111.md": pa_note("Nova Calc -3 (1 Oct)", "nova calc",
                                                    "body-NOVA")})
    try:
        pa_write(home, [pa_entry(PA_TITLE, age=day + 60)])
        p = run(home, "pa11-c-new", "nova calc")
        obj = pa_whole_object(p, "pa-drop/merge")
        check("pa-drop: it joins the pickup's message rather than replacing it",
              "Picked up" in obj.get("systemMessage", "")
              and "could not archive" in obj.get("systemMessage", ""), repr(obj)[:400])
    finally:
        shutil.rmtree(home, ignore_errors=True)


def pa_ctx_of(obj):
    return (obj.get("hookSpecificOutput") or {}).get("additionalContext", "") or ""


def test_a_corrupt_pending_list_neither_crashes_nor_breaks_the_output():
    for junk in ("{not json at all", "", "[1, 2, \"x\", {\"title\": 5}]", "{\"a\": 1}"):
        home = make_home({KEY + ".aaaa1111.md": pa_note(PA_TITLE)})
        try:
            os.makedirs(os.path.dirname(pa_file(home)), exist_ok=True)
            with open(pa_file(home), "w", encoding="utf-8") as f:
                f.write(junk)
            p = run(home, "pa12-new", "harbor")
            expect_clean(p, "pa-corrupt/" + repr(junk[:8]))
            obj = pa_whole_object(p, "pa-corrupt/" + repr(junk[:8]))
            check("pa-corrupt: the pickup still works",
                  "body-HARBOR" in pa_ctx_of(obj), repr(obj)[:200])
            check("pa-corrupt: the list starts over with the one new entry",
                  [e.get("title") for e in (pa_read(home) or [])] == [PA_TITLE],
                  repr(pa_read(home)))
        finally:
            shutil.rmtree(home, ignore_errors=True)
    home = make_home({})
    try:
        os.makedirs(os.path.dirname(pa_file(home)), exist_ok=True)
        with open(pa_file(home), "w", encoding="utf-8") as f:
            f.write("{not json")
        p = run(home, "pa12-quiet", "carry on")
        expect_clean(p, "pa-corrupt/quiet")
        check("pa-corrupt: a chat with nothing to say still says nothing",
              (p.stdout or "").strip() == "", repr(p.stdout))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_report_lists_the_chats_waiting_to_be_archived():
    home = make_home({})
    try:
        pa_write(home, [pa_entry(PA_TITLE, age=7200, offers=3, since_offer=100)])
        p = run_report(home)
        expect_clean(p, "pa-report")
        out = p.stdout or ""
        check("pa-report: the section is there", "-- chats waiting to be archived --" in out,
              repr(out[-600:]))
        row = line_with(out, PA_TITLE)
        check("pa-report: the row carries the title, its age and its offers",
              PA_TITLE in row and "2h" in row and "3" in row, repr(row))
        check("pa-report: the list file is not mistaken for a tracked session",
              "pending-archive" not in out.split("-- chats waiting")[0].split(
                  "-- sessions being tracked --")[-1], repr(out[-800:]))
    finally:
        shutil.rmtree(home, ignore_errors=True)
    home = make_home({})
    try:
        out = run_report(home).stdout or ""
        head = "-- chats waiting to be archived --"
        after = out.split(head)[-1] if head in out else ""
        check("pa-report: an empty list prints (none)", "(none)" in after, repr(out[-400:]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_pickup_asks_for_the_archive_right_after_the_rename():
    home = make_home({KEY + ".aaaa1111.md": pa_note(PA_TITLE)})
    try:
        p = run(home, "pa13-new", "harbor")
        ctx = pa_ctx(p)
        expect_clean(p, "pa-tail")
        check("pa-tail: the old 'AFTER you have answered him' is gone",
              "AFTER you have answered him" not in ctx, repr(ctx[:900]))
        check("pa-tail: it now says to archive RIGHT AFTER the rename, before the work",
              "RIGHT AFTER" in ctx and "rename" in ctx[ctx.find("RIGHT AFTER"):][:120]
              and "before the rest of the work" in ctx, repr(ctx[:1200]))
        check("pa-tail: a refusal is fine because Context Guard retries",
              "retries" in ctx and "refuses" in ctx, repr(ctx[:1400]))
        for clause in ("EXACTLY", "not running", "not this chat", "more than one",
                       "archive NOTHING for that title", "Do not ask permission",
                       "do not announce it", "unarchive_session", "skip it silently"):
            check("pa-tail: safety clause kept - " + clause, clause in ctx, repr(ctx[:1400]))
        check("pa-tail: it asks for 200 sessions, not the archived ones",
              "limit 200" in ctx and "include_archived" not in ctx, repr(ctx[:1400]))
        check("pa-tail: an already-archived or unmatched title is done, by short id",
              "already archived" in ctx and "nothing matches" in ctx
              and "--archived " in ctx and "(id " + pa_id(PA_TITLE) + ")" in ctx,
              repr(ctx[:1600]))
        check("pa-tail: it says the live list leaves archived chats out",
              "leaves archived chats out" in ctx, repr(ctx[:1400]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_a_pickup_or_menu_turn_gets_no_offer_and_nothing_is_stamped():
    """The offer would land after a note of up to 21 kB - the edge where the app truncates
    injected text. The pickup's own archive_tail already covers its writer, so the offer
    waits for the next ordinary prompt and is not counted."""
    home = make_home({KEY + ".aaaa1111.md": pa_note("Nova Calc -3 (1 Oct)", "nova calc",
                                                    "body-NOVA")})
    try:
        pa_write(home, [pa_entry(PA_TITLE, age=500)])
        p = run(home, "pa14-new", "nova calc")
        expect_clean(p, "pa-skip")
        obj = pa_whole_object(p, "pa-skip")
        ctx = pa_ctx_of(obj)
        check("pa-skip: the pickup's own context is untouched",
              "body-NOVA" in ctx and "is titled 'Nova Calc -3 (1 Oct)'" in ctx, repr(ctx[:200]))
        check("pa-skip: and carries no retry", "Sidebar tidy" not in ctx, repr(ctx[-300:]))
        check("pa-skip: the pickup's systemMessage survived",
              "Picked up" in obj.get("systemMessage", ""), repr(obj)[:300])
        got = {e["title"]: e for e in (pa_read(home) or [])}
        e = got.get(PA_TITLE, {})
        check("pa-skip: the skipped entry is NOT stamped or counted",
              e.get("offers") == 0 and e.get("last_offered") == 0, repr(e))
        check("pa-skip: the pickup's own writer was still added", "Nova Calc -3 (1 Oct)" in got,
              repr(list(got)))
        q = run(home, "pa14-next", "carry on")
        check("pa-skip: the next ordinary prompt offers it at once",
              "'" + PA_TITLE + "'" in pa_ctx(q), repr(pa_ctx(q)[:300]))
    finally:
        shutil.rmtree(home, ignore_errors=True)
    home = make_home({KEY + ".aaaa1111.md": pa_note("W1 -1 (1 Oct)", "one", "body-ONE"),
                      KEY + ".bbbb2222.md": pa_note("W2 -1 (1 Oct)", "two", "body-TWO")})
    try:
        pa_write(home, [pa_entry(PA_TITLE, age=500)])
        p = run(home, "pa14-menu", "hello there")
        check("pa-skip/menu: it was the menu", "SAVED THREADS ARE WAITING" in pa_ctx(p),
              repr(pa_ctx(p)[:100]))
        check("pa-skip/menu: no retry on the menu either", "Sidebar tidy" not in pa_ctx(p),
              repr(pa_ctx(p)[-300:]))
        check("pa-skip/menu: not stamped", (pa_read(home) or [{}])[0].get("offers") == 0,
              repr(pa_read(home)))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_a_held_lock_skips_the_step_cleanly_and_a_stale_one_is_taken_over():
    home = make_home({})
    lock = os.path.join(home, ".claude", "context-guard", "pending-archive.lock")
    try:
        arm_away(home)                       # gives the run a systemMessage to print
        pa_write(home, [pa_entry(PA_TITLE, age=500)])
        with open(lock, "w") as f:
            f.write("999999")
        t0 = time.time()
        p = run(home, "pa15-held", "carry on")
        took = time.time() - t0
        expect_clean(p, "pa-lock/held")
        obj = pa_whole_object(p, "pa-lock/held")
        check("pa-lock: a held lock means this run skips the step - no offer",
              "Sidebar tidy" not in pa_ctx_of(obj) and bool(obj.get("systemMessage")),
              repr(obj)[:300])
        check("pa-lock: and the list is untouched",
              (pa_read(home) or [{}])[0].get("offers") == 0, repr(pa_read(home)))
        check("pa-lock: it never blocks the hook for long (about 0.8 s of waiting)",
              took < 6.0, "%.1fs" % took)
        check("pa-lock: a lock it does not own is left in place", os.path.exists(lock),
              "lock vanished")
        stale = guard_constant("ARCHIVE_LOCK_STALE")
        old = time.time() - stale - 30
        os.utime(lock, (old, old))
        p = run(home, "pa15-stale", "carry on")
        expect_clean(p, "pa-lock/stale")
        check("pa-lock: a stale lock is taken over and the step runs",
              "'" + PA_TITLE + "'" in pa_ctx(p), repr((p.stdout or "")[:300]))
        check("pa-lock: and the lock is released afterwards", not os.path.exists(lock),
              "lock left behind")
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_hooks_running_at_once_lose_no_pickup():
    """Measured by the review: 4 hooks at once lost a pickup's add in 18 of 40 rounds."""
    names = ("alpha", "bravo", "charlie", "delta")
    consumed_total = 0
    for rnd in range(6):
        notes = {KEY + ".%04d%04d.md" % (rnd, i):
                 pa_note("Nova %s -%d (1 Oct)" % (n, rnd + 1), n, "body-" + n.upper())
                 for i, n in enumerate(names)}
        home = make_home(notes)
        try:
            procs = []
            for n in names:
                sid = "pa16-%s-%d-new" % (n, rnd)
                payload = {"session_id": sid, "cwd": CWD, "prompt": n,
                           "transcript_path": os.path.join(home, ".claude", "projects", KEY,
                                                           sid + ".jsonl"),
                           "hook_event_name": "UserPromptSubmit"}
                pr = subprocess.Popen([sys.executable, GUARD, "--size"], stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                      env=child_env(home))
                pr.stdin.write(json.dumps(payload))
                pr.stdin.close()
                procs.append(pr)
            for pr in procs:
                pr.wait(timeout=60)
            got = sorted(e["title"] for e in (pa_read(home) or []))
            # Four hooks also race for the note FILES (a rename can be refused while
            # another hook has the file open), so the fair count is the notes that were
            # really consumed - each of those must have its entry.
            left = waiting(home)
            want = sorted("Nova %s -%d (1 Oct)" % (n, rnd + 1) for i, n in enumerate(names)
                          if not any(f.endswith("%04d%04d.md" % (rnd, i)) for f in left))
            consumed_total += len(want)
            check("pa-race/round %d: every consumed pickup's add survived" % rnd,
                  got == want, repr((got, want)))
        finally:
            shutil.rmtree(home, ignore_errors=True)
    check("pa-race: the rounds really did consume notes", consumed_total >= 12,
          str(consumed_total))


def test_non_finite_numbers_and_a_huge_file_are_survived():
    for junk in ("NaN", "Infinity", "-Infinity", "1e999"):
        home = make_home({})
        try:
            os.makedirs(os.path.dirname(pa_file(home)), exist_ok=True)
            with open(pa_file(home), "w", encoding="utf-8") as f:
                f.write('[{"title": "%s", "added": %s, "offers": %s, "last_offered": %s, '
                        '"watch": []}]' % (PA_TITLE, junk, junk, junk))
            for name, cmd in (("report", [sys.executable, GUARD, "--report"]),
                              ("archived", [sys.executable, GUARD, "--archived", "Nope"])):
                p = subprocess.run(cmd, capture_output=True, text=True, env=child_env(home))
                expect_clean(p, "pa-nan/%s/%s" % (junk, name))
            p = run(home, "pa17-a", "carry on")
            expect_clean(p, "pa-nan/%s/size" % junk)
            check("pa-nan: %s in the list does not crash the hook or print junk" % junk,
                  (p.stdout or "").strip() == "" or isinstance(json.loads(p.stdout), dict),
                  repr((p.stdout or "")[:200]))
        finally:
            shutil.rmtree(home, ignore_errors=True)
    home = make_home({})
    try:
        os.makedirs(os.path.dirname(pa_file(home)), exist_ok=True)
        with open(pa_file(home), "w", encoding="utf-8") as f:
            f.write("[" + ",".join('{"title": "T%d", "added": 1, "watch": []}' % i
                                   for i in range(30000)) + "]")
        check("pa-huge: the fixture really is over 1 MB", os.path.getsize(pa_file(home)) > 1_000_000,
              str(os.path.getsize(pa_file(home))))
        t0 = time.time()
        p = run(home, "pa17-b", "carry on")
        expect_clean(p, "pa-huge")
        check("pa-huge: a list over 1 MB reads as corrupt - quiet and quick",
              (p.stdout or "").strip() == "" and time.time() - t0 < 6.0, repr((p.stdout or "")[:200]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_an_untitled_writer_is_ignored():
    for w in ("(untitled)", "(Untitled)", "(UNTITLED)"):
        home = make_home({KEY + ".aaaa1111.md": pa_note(w)})
        try:
            p = run(home, "pa18-new", "harbor")
            expect_clean(p, "pa-untitled/" + w)
            check("pa-untitled: %s is not put on the list" % w, not pa_read(home),
                  repr(pa_read(home)))
            check("pa-untitled: %s is not named for archiving" % w,
                  "archive_session" not in pa_ctx(p) and "body-HARBOR" in pa_ctx(p),
                  repr(pa_ctx(p)[:300]))
        finally:
            shutil.rmtree(home, ignore_errors=True)


def test_an_entry_has_a_short_stable_id_and_the_flag_takes_it():
    home = make_home({KEY + ".aaaa1111.md": pa_note(PA_ODD, "nova calc", "body-NOVA")})
    try:
        run(home, "pa19-new", "nova calc")
        e = (pa_read(home) or [{}])[0]
        check("pa-id: the id is the first 8 hex of sha1(title)", e.get("id") == pa_id(PA_ODD),
              repr(e))
        p = subprocess.run([sys.executable, GUARD, "--archived", pa_id(PA_ODD)],
                           capture_output=True, text=True, env=child_env(home))
        expect_clean(p, "pa-id/flag")
        check("pa-id: --archived <id> removes it", not pa_read(home) and "Removed" in p.stdout,
              repr((p.stdout, pa_read(home))))
        pa_write(home, [pa_entry(PA_ODD, age=500), pa_entry(PA_TITLE, age=500)])
        p = subprocess.run([sys.executable, GUARD, "--archived", pa_id("No Such -1")],
                           capture_output=True, text=True, env=child_env(home))
        check("pa-id: an unknown id is harmless", p.returncode == 0 and len(pa_read(home)) == 2,
              repr((p.stdout, pa_read(home))))
    finally:
        shutil.rmtree(home, ignore_errors=True)


# ---------------------------------------------------------------- the bootstrap
# His words, 19 Sep 2026: "we need to make sure once we do the 'write Context Guard -14
# (19 Sep) in a new chat' thingy it needs to create a folder then and there and organize
# it automatically can we do that??"
#
# He is not asking for 109 files to be sorted by hand. He is asking that the FIRST chat
# opened in a project's own directory furnish its own memory folder from the shared one.
# A hand-sorted copy would satisfy "do that yes" and miss the request entirely.

BOOT_MANIFEST = {
    "t1_claude_md": ["dev-laptop-hardware"],
    "projects": {
        "garden": {"dir": r"D:\AI Projects\Garden", "also": [],
                 "files": ["plant-watering-log-app"]},
        "photobox": {"dir": r"D:\AI Projects\PhotoBox APK",
                       "also": [r"D:\AI Projects\PhotoBox PC"],
                       "files": ["photobox-pc-port", "youtubedl-android-traps"]},
    },
    "_craft": ["regex-char-class-range-trap"],
}

BOOT_FILES = {
    "plant-watering-log-app": "body-GARDEN",
    "photobox-pc-port": "body-SBPC",
    "youtubedl-android-traps": "body-YTDL",
    "dev-laptop-hardware": "body-LAPTOP",
    "regex-char-class-range-trap": "body-REGEX",
}

BOOT_INDEX = "\n".join([
    "# Memory Index",
    "- [Garden / plant watering log](plant-watering-log-app.md) - v0.1.0 beta APK at D:\\AI Projects\\Garden",
    "- [PhotoBox PC port](photobox-pc-port.md) - 5 files differ from the APK tree",
    "- [youtubedl-android traps](youtubedl-android-traps.md) - the progress callback only sees '[' lines",
    "- [Laptop hardware](dev-laptop-hardware.md) - HP EliteBook 845 G7, no discrete GPU",
    "- [Regex char-class range trap](regex-char-class-range-trap.md) - it ate table rows for weeks",
]) + "\n"


def make_boot_home(manifest=None, files=None):
    """A fake ~ holding the SHARED memory folder and the manifest, and nothing else."""
    home = throwaway_dir("guardboot-")
    mem = os.path.join(home, ".claude", "projects", KEY, "memory")
    state = os.path.join(home, ".claude", "context-guard")
    os.makedirs(mem)
    os.makedirs(state)
    os.makedirs(os.path.join(home, ".claude", "handoff"))
    for name, body in (BOOT_FILES if files is None else files).items():
        with open(os.path.join(mem, name + ".md"), "w", encoding="utf-8") as f:
            f.write("---\nname: %s\n---\n\n%s\n" % (name, body))
    with open(os.path.join(mem, "MEMORY.md"), "w", encoding="utf-8") as f:
        f.write(BOOT_INDEX)
    with open(os.path.join(state, "memory-manifest.json"), "w", encoding="utf-8") as f:
        json.dump(BOOT_MANIFEST if manifest is None else manifest, f)
    return home


def boot(home, cwd, sid="boot0001"):
    """Fire the SessionStart hook exactly as Claude Code does."""
    payload = {"session_id": sid, "cwd": cwd, "hook_event_name": "SessionStart",
               "source": "startup"}
    p = subprocess.run([sys.executable, GUARD, "--bootstrap"],
                       input=json.dumps(payload), capture_output=True, text=True,
                       env=child_env(home))
    return p


def boot_memory(home, key):
    """Everything the bootstrap left in that project's memory folder."""
    d = os.path.join(home, ".claude", "projects", key, "memory")
    if not os.path.isdir(d):
        return None
    return sorted(os.listdir(d))


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


def test_the_project_key_matches_the_ones_claude_code_actually_made():
    """The folder name is the ONE thing that cannot be a near miss.

    Measured against the three project keys that exist on his disk on 19 Sep 2026 -
    D--Claude, D--TEST and D--demo-project---Beta. Note the triple hyphen: the
    runs are NOT collapsed, and a tidier-looking rule that collapses them produces a
    folder Claude Code will never read. Five of ten project directories were wrong on
    the manifest's first draft; this is the same failure one level down."""
    pairs = [(r"D:\Claude", "D--Claude"),
             (r"D:\TEST", "D--TEST"),
             (r"D:\demo-project - Beta", "D--demo-project---Beta")]
    man = {"t1_claude_md": [], "_craft": [], "projects": {}}
    for i, (cwd, _key) in enumerate(pairs):
        man["projects"]["p%d" % i] = {"dir": cwd, "also": [],
                                      "files": ["plant-watering-log-app"]}
    for cwd, key in pairs:
        home = make_boot_home(manifest=man)
        try:
            p = boot(home, cwd)
            expect_clean(p, "bootstrap/key " + key)
            check("bootstrap/key: %s -> %s" % (cwd, key),
                  os.path.isfile(os.path.join(home, ".claude", "projects", key,
                                              "memory", "MEMORY.md")),
                  "made: " + str(sorted(os.listdir(os.path.join(home, ".claude", "projects")))))
        finally:
            shutil.rmtree(home, ignore_errors=True)


def test_bootstrap_furnishes_a_fresh_directory():
    """The whole request, in one test: open a chat somewhere new, find your memories."""
    home = make_boot_home()
    try:
        p = boot(home, r"D:\AI Projects\Garden")
        expect_clean(p, "bootstrap/fresh")
        got = boot_memory(home, "D--AI-Projects-Garden")
        check("bootstrap/fresh: the folder was created", got is not None, str(got))
        check("bootstrap/fresh: its memory is listed, and its file stays shared",
              "plant-watering-log-app" in (boot_listed(home, "D--AI-Projects-Garden") or [])
              and got is not None and "plant-watering-log-app.md" not in got, str(got))
        check("bootstrap/fresh: an index was written",
              got is not None and "MEMORY.md" in got, str(got))
        if got:
            with open(os.path.join(home, ".claude", "projects", "D--AI-Projects-Garden",
                                   "memory", "MEMORY.md"), encoding="utf-8") as f:
                idx = f.read()
            check("bootstrap/fresh: the index line was carried over, not invented",
                  "(plant-watering-log-app.md)" in idx, repr(idx[:200]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_bootstrap_gives_the_new_folder_only_its_own_memories():
    """The point of the split. A Garden chat must not inherit PhotoBox's 9 files."""
    home = make_boot_home()
    try:
        boot(home, r"D:\AI Projects\Garden")
        got = [s + ".md" for s in (boot_listed(home, "D--AI-Projects-Garden") or [])]
        # POSITIVE CONTROL. Every other check here asserts an ABSENCE, and an absence is
        # trivially true while the feature does not exist at all - that is how a no-op
        # step reports green. This one line makes the test able to fail.
        check("bootstrap/only-own: its own memory did arrive",
              "plant-watering-log-app.md" in got, str(got))
        check("bootstrap/only-own: no other project's memories",
              "photobox-pc-port.md" not in got and "youtubedl-android-traps.md" not in got,
              str(got))
        check("bootstrap/only-own: no craft memories in a project folder",
              "regex-char-class-range-trap.md" not in got, str(got))
        check("bootstrap/only-own: no tier-1 memories in a project folder",
              "dev-laptop-hardware.md" not in got, str(got))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_bootstrap_puts_the_new_index_in_context_on_the_same_turn():
    """THE UNMEASURED ORDERING, designed around rather than guessed at.

    Nobody knows whether Claude Code's memory loader runs before or after SessionStart.
    If it runs first, a folder created by this hook is only read by the NEXT chat - and
    that failure looks exactly like success, because chat #2 in the folder works fine.
    So the hook hands the index back as additionalContext and the ordering stops
    mattering. This test is the belt; the folder on disk is the braces."""
    home = make_boot_home()
    try:
        p = boot(home, r"D:\AI Projects\Garden")
        ctx = context_of(p)
        check("bootstrap/turn-one: said something", bool(ctx.strip()), repr(ctx[:120]))
        check("bootstrap/turn-one: the index is in context immediately",
              "plant-watering-log-app" in ctx, repr(ctx[:400]))
        check("bootstrap/turn-one: names the folder it made",
              "D--AI-Projects-Garden" in ctx, repr(ctx[:400]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_bootstrap_lists_rather_than_copies():
    """ONE HOME: the new folder gets a list and never a copy, and the shared folder -
    where every chat he has open reads and saves - is not touched at all."""
    home = make_boot_home()
    try:
        boot(home, r"D:\AI Projects\Garden")
        src = os.path.join(home, ".claude", "projects", KEY, "memory")
        left = sorted(os.listdir(src))
        # POSITIVE CONTROL - "the source still has it" is also true when nothing ran.
        check("bootstrap/list: the destination lists it",
              "plant-watering-log-app" in (boot_listed(home, "D--AI-Projects-Garden") or []),
              str(boot_listed(home, "D--AI-Projects-Garden")))
        check("bootstrap/list: and holds no copy of it",
              boot_memory(home, "D--AI-Projects-Garden") == ["MEMORY.md"],
              str(boot_memory(home, "D--AI-Projects-Garden")))
        check("bootstrap/list: the shared folder still has every file",
              all(n + ".md" in left for n in BOOT_FILES), str(left))
        check("bootstrap/list: the shared index is untouched",
              open(os.path.join(src, "MEMORY.md"), encoding="utf-8").read() == BOOT_INDEX)
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_bootstrap_never_overwrites_an_existing_memory_folder():
    """A folder that already has memories is HIS. Never write over it."""
    home = make_boot_home()
    try:
        mem = os.path.join(home, ".claude", "projects", "D--AI-Projects-Garden", "memory")
        os.makedirs(mem)
        with open(os.path.join(mem, "MEMORY.md"), "w", encoding="utf-8") as f:
            f.write("# Memory Index\n- [his own](his-own.md) - written by hand\n")
        p = boot(home, r"D:\AI Projects\Garden")
        expect_clean(p, "bootstrap/no-clobber")
        body = open(os.path.join(mem, "MEMORY.md"), encoding="utf-8").read()
        check("bootstrap/no-clobber: his index survived", "his-own.md" in body, repr(body))
        check("bootstrap/no-clobber: nothing was added",
              "plant-watering-log-app" not in body, repr(body))
        check("bootstrap/no-clobber: and it said nothing", not context_of(p).strip(),
              repr(context_of(p)[:120]))
        # POSITIVE CONTROL, in the SAME home: a folder that does not exist yet is still
        # furnished. Without this the whole test passes against a feature that is dead.
        boot(home, r"D:\AI Projects\PhotoBox APK", sid="boot0009")
        check("bootstrap/no-clobber: control - a fresh folder IS still furnished",
              "photobox-pc-port" in (boot_listed(home, "D--AI-Projects-PhotoBox-APK") or []),
              str(boot_listed(home, "D--AI-Projects-PhotoBox-APK")))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_bootstrap_is_idempotent():
    """SessionStart fires on every startup AND every resume. It must run exactly once."""
    home = make_boot_home()
    try:
        boot(home, r"D:\AI Projects\Garden", sid="boot0001")
        first = boot_memory(home, "D--AI-Projects-Garden")
        first_list = list_bytes(home, "D--AI-Projects-Garden")
        # POSITIVE CONTROL - "unchanged" is trivially true when the first run did nothing.
        check("bootstrap/idempotent: the first run actually furnished it",
              "plant-watering-log-app" in (boot_listed(home, "D--AI-Projects-Garden") or []),
              str(first))
        p2 = boot(home, r"D:\AI Projects\Garden", sid="boot0002")
        expect_clean(p2, "bootstrap/idempotent")
        check("bootstrap/idempotent: the folder is unchanged",
              boot_memory(home, "D--AI-Projects-Garden") == first
              and list_bytes(home, "D--AI-Projects-Garden") == first_list, str(first))
        check("bootstrap/idempotent: the second run is silent",
              not context_of(p2).strip(), repr(context_of(p2)[:120]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_bootstrap_ignores_a_directory_the_manifest_does_not_know():
    """Do not guess. An unknown folder gets nothing, silently."""
    home = make_boot_home()
    try:
        # POSITIVE CONTROL first, in the same home: prove the feature is live, THEN prove
        # it declines to guess. A control that cannot fire makes the finding meaningless.
        boot(home, r"D:\AI Projects\Garden", sid="boot0008")
        check("bootstrap/unknown: control - a known folder IS furnished",
              "plant-watering-log-app" in (boot_listed(home, "D--AI-Projects-Garden") or []),
              str(boot_listed(home, "D--AI-Projects-Garden")))
        p = boot(home, r"D:\AI Projects\Something Brand New")
        expect_clean(p, "bootstrap/unknown")
        check("bootstrap/unknown: no folder invented",
              boot_memory(home, "D--AI-Projects-Something-Brand-New") is None,
              str(boot_memory(home, "D--AI-Projects-Something-Brand-New")))
        check("bootstrap/unknown: and it said nothing", not context_of(p).strip(),
              repr(context_of(p)[:120]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_bootstrap_matches_an_also_directory():
    """PhotoBox lives in two folders and Harbor in four. `also` is not decoration."""
    home = make_boot_home()
    try:
        p = boot(home, r"D:\AI Projects\PhotoBox PC")
        expect_clean(p, "bootstrap/also")
        got = [s + ".md" for s in (boot_listed(home, "D--AI-Projects-PhotoBox-PC") or [])]
        check("bootstrap/also: the alias folder was furnished too",
              "photobox-pc-port.md" in got and "youtubedl-android-traps.md" in got, str(got))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_bootstrap_survives_a_manifest_entry_with_no_file_on_disk():
    """The manifest is judgement written by hand; a name in it can rot.

    A missing file must not abort the other eight, and must not be silently skipped
    either - a bootstrap that quietly drops memories is worse than one that fails."""
    files = dict(BOOT_FILES)
    del files["plant-watering-log-app"]
    home = make_boot_home(files=files)
    try:
        p = boot(home, r"D:\AI Projects\Garden")
        expect_clean(p, "bootstrap/missing-file")
        ctx = context_of(p)
        check("bootstrap/missing-file: it says which name it could not find",
              "plant-watering-log-app" in ctx, repr(ctx[:300]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_bootstrap_writes_only_the_list_with_the_header():
    """ONE HOME, 25 Sep 2026: a new project folder gets its MEMORY.md and nothing else.
    Copying the topic files in gave every project memory two homes, and they drifted.
    The list carries every manifest slug the shared folder has, and one header line above
    the entries names the shared folder as the place the files live."""
    home = make_boot_home()
    try:
        p = boot(home, r"D:\AI Projects\PhotoBox APK")
        expect_clean(p, "bootstrap/list-only")
        key = "D--AI-Projects-PhotoBox-APK"
        shared = os.path.join(home, ".claude", "projects", KEY, "memory")
        check("bootstrap/list-only: the folder holds only its list",
              boot_memory(home, key) == ["MEMORY.md"], str(boot_memory(home, key)))
        check("bootstrap/list-only: it lists every manifest slug, in manifest order",
              boot_listed(home, key) == ["photobox-pc-port", "youtubedl-android-traps"],
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


# ------------------------------------------------- the ordering, answered by the hook
# Carried unanswered across SEVEN handoff notes: when a chat creates a project's memory
# folder during SessionStart, does THAT chat get to read it, or only the next one?
# It cannot be measured by hand here - there is no claude CLI on this machine - and
# every note so far has ended with "write the answer down when it happens", which is a
# reminder, i.e. a bug in the tooling. The hook can answer it itself: Claude Code's own
# memory loader leaves a fingerprint in the session transcript, so the bootstrap arms a
# probe when it creates a folder and a later SessionStart reads that transcript back and
# writes the verdict down once, for good.

def probe_paths(home):
    state = os.path.join(home, ".claude", "context-guard")
    return (os.path.join(state, "ordering-probe.json"),
            os.path.join(state, "ordering-answer.txt"))


def fake_transcript(home, key, sid, saw_memory, turns=1):
    """A session transcript shaped like a real one.

    The needle is taken verbatim from his own 19 Sep transcript, where Claude Code's
    memory loader writes: Contents of C:\\Users\\...\\projects\\<KEY>\\memory\\MEMORY.md
    (user's auto-memory, persists across conversations)."""
    d = os.path.join(home, ".claude", "projects", key)
    os.makedirs(d, exist_ok=True)
    lines = []
    if saw_memory:
        lines.append(json.dumps({"type": "system", "content":
                                 "Contents of C:\\Users\\Someone\\.claude\\projects\\"
                                 + key + "\\memory\\MEMORY.md (user's auto-memory, "
                                 "persists across conversations)"}))
    for _i in range(turns):
        lines.append(json.dumps({"type": "user",
                                 "message": {"role": "user", "content": "hello"}}))
    with open(os.path.join(d, sid + ".jsonl"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def test_the_bootstrap_arms_a_probe_only_when_it_actually_furnished_a_folder():
    """A verdict about a chat that was handed nothing would be a verdict about nothing.

    Two positive controls in the same shape as the real case: a folder that already
    existed (this chat did not create it, so its memories were always there to read)
    and a manifest whose files have all rotted (nothing copied, so an empty index
    proves nothing either way). Neither may leave a probe behind."""
    home = make_boot_home()
    try:
        boot(home, r"D:\AI Projects\Garden", sid="probe001")
        probe, answer = probe_paths(home)
        check("ordering/arm: furnishing a fresh folder arms the probe",
              os.path.isfile(probe), str(sorted(os.listdir(os.path.dirname(probe)))))
        if os.path.isfile(probe):
            with open(probe, encoding="utf-8") as f:
                rec = json.load(f)
            check("ordering/arm: the probe records the session it must read back",
                  rec.get("sid") == "probe001", json.dumps(rec)[:200])
            check("ordering/arm: the probe records the project key it furnished",
                  rec.get("key") == "D--AI-Projects-Garden", json.dumps(rec)[:200])
        check("ordering/arm: nothing is answered yet", not os.path.exists(answer),
              answer)
        # control 1: the same directory a second time - the folder is already there
        if os.path.exists(probe):
            os.remove(probe)
        boot(home, r"D:\AI Projects\Garden", sid="probe002")
        check("ordering/arm: an existing memory folder arms no probe",
              not os.path.exists(probe), "re-armed on a folder it left alone")
    finally:
        shutil.rmtree(home, ignore_errors=True)
    # control 2: a manifest entry whose file has rotted - folder made, nothing copied
    files = dict(BOOT_FILES)
    del files["plant-watering-log-app"]
    home = make_boot_home(files=files)
    try:
        boot(home, r"D:\AI Projects\Garden", sid="probe003")
        probe, _a = probe_paths(home)
        check("ordering/arm: a folder furnished with NO memories arms no probe",
              not os.path.exists(probe), "armed on an empty index")
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_hook_answers_the_ordering_question_by_itself():
    """The whole point: nobody has to remember to look.

    Driven both ways round, because a detector that only ever says yes is not a
    detector. Resolution is deliberately hung on SessionStart - it fires on every
    startup AND every resume, so the answer lands within hours and costs one stat per
    session rather than one per prompt."""
    for saw, want, tag in ((True, "same-session", "same"),
                           (False, "next-session", "next")):
        home = make_boot_home()
        try:
            boot(home, r"D:\AI Projects\Garden", sid="ord" + tag)
            probe, answer = probe_paths(home)
            fake_transcript(home, "D--AI-Projects-Garden", "ord" + tag, saw_memory=saw)
            # any later SessionStart resolves it - here one the manifest does not know,
            # so nothing is furnished and only the resolution can have written the file
            p = boot(home, r"D:\Nowhere At All", sid="later" + tag)
            expect_clean(p, "ordering/answer " + tag)
            check("ordering/answer: %s - the verdict was written down" % tag,
                  os.path.isfile(answer), str(sorted(os.listdir(os.path.dirname(answer)))))
            got = ""
            if os.path.isfile(answer):
                with open(answer, encoding="utf-8") as f:
                    got = f.read()
            check("ordering/answer: %s - and it is the right verdict" % tag,
                  want in got, repr(got[:300]))
            check("ordering/answer: %s - the probe is spent, not asked again" % tag,
                  not os.path.exists(probe), "probe survived its own answer")
            check("ordering/answer: %s - the verdict says which project it came from"
                  % tag, "D--AI-Projects-Garden" in got, repr(got[:300]))
        finally:
            shutil.rmtree(home, ignore_errors=True)


def test_an_unanswerable_probe_waits_instead_of_guessing():
    """No transcript yet, or one with no user turn in it, is not evidence of absence.

    Hooks fire before the transcript exists - measured 11 Sep 2026 - so the first
    SessionStart after arming will usually find nothing on disk. Guessing there would
    answer the question wrongly and then stop asking, which is the worst of the three
    outcomes."""
    home = make_boot_home()
    try:
        boot(home, r"D:\AI Projects\Garden", sid="ordwait")
        probe, answer = probe_paths(home)
        boot(home, r"D:\Nowhere At All", sid="later1")      # no transcript at all
        check("ordering/wait: no transcript yet - nothing is claimed",
              not os.path.exists(answer), "answered with no evidence")
        check("ordering/wait: and the probe is kept for later",
              os.path.isfile(probe), "probe thrown away unanswered")
        # a transcript that exists but has not reached a user turn yet
        fake_transcript(home, "D--AI-Projects-Garden", "ordwait", saw_memory=False, turns=0)
        boot(home, r"D:\Nowhere At All", sid="later2")
        check("ordering/wait: an empty transcript is not a 'no'",
              not os.path.exists(answer), "answered off a transcript with no user turn")
        check("ordering/wait: the probe is still waiting",
              os.path.isfile(probe), "probe thrown away unanswered")
        # ...and once the turn arrives it answers normally
        fake_transcript(home, "D--AI-Projects-Garden", "ordwait", saw_memory=True)
        boot(home, r"D:\Nowhere At All", sid="later3")
        check("ordering/wait: the same probe answers once the evidence lands",
              os.path.isfile(answer), "still unanswered with a full transcript on disk")
    finally:
        shutil.rmtree(home, ignore_errors=True)


# ------------------------------------------- three silences, each made to explain itself
# 19 Sep 2026: `--report` said "memory nags 0 never fired" after eight days and 82 handoff
# pickups, and nothing in the tool could say whether that meant "every chat saved its
# memories" or "the check is dead". The away indicator was exactly that, and it was dead.
# A counter that cannot explain a zero is not evidence. Same shape for the other two: the
# ledger budget drops requests silently, and the ordering verdict lands in a file nobody
# has any reason to open.

def guard_log(home):
    """Everything guard.py wrote to its own log inside this throwaway home."""
    p = os.path.join(home, ".claude", "context-audit.log")
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception:
        return ""


def test_a_nag_that_stays_quiet_says_why():
    """A zero in the report must be explainable, not just true.

    The memory folder is SHARED by every chat in the folder, so a neighbour saving a
    memory makes this chat's nag stand down - by mtime there is no way to tell the two
    apart. That is a real hole and it is why the count can be zero while the check is
    healthy; the least the hook can do is say which of the two happened."""
    home = make_home({})
    try:
        started = time.time() - 3600
        write_chat(home, "quiet", started)
        write_note(home, "quiet")
        write_memory(home, "saved-this-chat.md", time.time())
        append_tool_use(home, "quiet", "Edit", {"file_path": os.path.join(
            home, ".claude", "projects", KEY, "memory", "saved-this-chat.md")})
        p = run_stop(home, "quiet")
        expect_clean(p, "nag-quiet")
        check("nag-quiet: the chat is let go", blocked(p) == "", repr(blocked(p)[:200]))
        check("nag-quiet: and the log says why it stayed quiet",
              "memory-nag: skipped" in guard_log(home), repr(guard_log(home)[-400:]))
    finally:
        shutil.rmtree(home, ignore_errors=True)
    # control, same shape: when it DOES fire the log still says so, and differently
    home = make_home({})
    try:
        started = time.time() - 3600
        write_chat(home, "loud", started)
        write_note(home, "loud")
        write_memory(home, "old-lesson.md", started - 86400)
        p = run_stop(home, "loud")
        expect_clean(p, "nag-loud")
        check("nag-quiet: control - it still blocks when nothing was saved",
              "MEMORY" in blocked(p), repr(blocked(p)[:120]))
        check("nag-quiet: control - and that is logged as fired, not skipped",
              "memory-nag: note written" in guard_log(home), repr(guard_log(home)[-400:]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_ledger_says_when_the_budget_drops_his_requests():
    """The budget was re-measured by hand on 19 Sep because nothing announced itself.

    The ledger is append-only and grows, so the next overflow is a certainty. Dropping
    his own words into a "... N more ..." line is exactly the kind of loss that must not
    wait for somebody to think of measuring it again."""
    budget = guard_constant("LEDGER_OWN_CHARS")
    each = 1900
    n = max(8, int(budget * 2 // each) + 2)
    home = make_home({})
    try:
        pickup(home, ["BULK-%02d %s" % (i, "q" * each) for i in range(n)])
        check("ledger-drop: the omission is logged, not just printed in the injection",
              "ledger: budget dropped" in guard_log(home), repr(guard_log(home)[-400:]))
    finally:
        shutil.rmtree(home, ignore_errors=True)
    # control: a thread that fits must NOT report a drop, or the line means nothing
    home = make_home({})
    try:
        pickup(home, ["a short request about the guard", "and another one"])
        check("ledger-drop: control - a thread that fits says nothing",
              "ledger: budget dropped" not in guard_log(home),
              repr(guard_log(home)[-400:]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def append_tool_use(home, sid, name, inp):
    """An assistant tool_use record, shaped the way Claude Code writes one."""
    p = os.path.join(home, ".claude", "projects", KEY, sid + ".jsonl")
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps({"type": "assistant", "message": {"role": "assistant",
                "content": [{"type": "tool_use", "name": name, "input": inp}]}}) + chr(10))


def append_prose(home, sid, text):
    """An assistant TEXT block that merely talks about a path. Not a save."""
    p = os.path.join(home, ".claude", "projects", KEY, sid + ".jsonl")
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps({"type": "assistant", "message": {"role": "assistant",
                "content": [{"type": "text", "text": text}]}}) + chr(10))


def test_a_neighbours_memory_save_no_longer_silences_this_chats_nag():
    """The hole the nag has had since it was written, closed by asking the transcript.

    Every chat in a folder shares one memory directory, so the mtime test answers "did
    ANYONE save something" when the question is "did THIS chat save something". With five
    chats live in D:\\Claude that is not a corner case. The transcript knows: a save is a
    tool_use with a file_path inside the memory folder, and that is a POSITIONAL fact -
    the folder's path also appears in the hook's own instruction text, which is in the
    transcript too, so a grep over the file would call every chat a saver."""
    home = make_home({})
    try:
        started = time.time() - 3600
        write_chat(home, "neighbour", started)
        write_note(home, "neighbour")
        write_memory(home, "someone-elses-save.md", time.time())   # another chat, just now
        p = run_stop(home, "neighbour")
        expect_clean(p, "nag-neighbour")
        check("nag-neighbour: the nag is no longer silenced by somebody else's save",
              "MEMORY" in blocked(p), repr(blocked(p)[:120]))
        check("nag-neighbour: and the log attributes the save elsewhere",
              "memory-nag: the folder changed but this chat wrote nothing"
              in guard_log(home), repr(guard_log(home)[-300:]))
    finally:
        shutil.rmtree(home, ignore_errors=True)
    # control 1: THIS chat wrote a memory file - it must stay silent
    home = make_home({})
    try:
        started = time.time() - 3600
        write_chat(home, "saver", started)
        append_tool_use(home, "saver", "Write", {"file_path": os.path.join(
            home, ".claude", "projects", KEY, "memory", "a-real-lesson.md")})
        write_note(home, "saver")
        write_memory(home, "a-real-lesson.md", time.time())
        p = run_stop(home, "saver")
        expect_clean(p, "nag-saver")
        check("nag-saver: a chat that really saved is let go",
              blocked(p) == "", repr(blocked(p)[:200]))
    finally:
        shutil.rmtree(home, ignore_errors=True)
    # control 2: the prose trap - the path is MENTIONED, not written to
    home = make_home({})
    try:
        started = time.time() - 3600
        write_chat(home, "talker", started)
        append_prose(home, "talker", "I will save this to " + os.path.join(
            home, ".claude", "projects", KEY, "memory", "MEMORY.md") + " shortly.")
        write_note(home, "talker")
        write_memory(home, "someone-elses-save.md", time.time())
        p = run_stop(home, "talker")
        expect_clean(p, "nag-talker")
        check("nag-talker: talking about the memory folder is not saving to it",
              "MEMORY" in blocked(p), repr(blocked(p)[:120]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_report_surfaces_the_ordering_verdict():
    """The probe answers a question nobody is watching for. Put the answer where the one
    command anybody runs will show it - otherwise it is a reminder again."""
    home = make_home({})
    state = os.path.join(home, ".claude", "context-guard")
    os.makedirs(state, exist_ok=True)
    try:
        out = run_report(home).stdout
        check("report-ordering: silent while there is nothing to say",
              "ordering" not in out.lower(), repr(line_with(out, "rdering")))
        with open(os.path.join(state, "ordering-probe.json"), "w", encoding="utf-8") as f:
            json.dump({"sid": "probe001", "key": "D--AI-Projects-Garden"}, f)
        out = run_report(home).stdout
        check("report-ordering: a pending probe is reported as waiting",
              "waiting" in out.lower() and "D--AI-Projects-Garden" in out,
              repr(line_with(out, "aiting")))
        os.remove(os.path.join(state, "ordering-probe.json"))
        with open(os.path.join(state, "ordering-answer.txt"), "w", encoding="utf-8") as f:
            f.write("verdict : same-session\nproject : D--AI-Projects-Garden\n")
        out = run_report(home).stdout
        check("report-ordering: the verdict itself is printed once it lands",
              "same-session" in out, repr(out[-400:]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


# --------------- change 12: helper kept from the budget-tool tests (tool deleted 2 Oct 2026)
def append_new_entry(home, ts, sid8, body):
    """An entry in the POST-18-Sep format, which carries its chat id inline."""
    lp = os.path.join(home, ".claude", "handoff", KEY + ".requests.md")
    with open(lp, "a", encoding="utf-8") as f:
        f.write(chr(10) + "### " + ts + " - chat " + sid8 + chr(10) + chr(10)
                + body + chr(10))


def test_the_neighbour_share_of_the_tail_is_bounded_too():
    """The thread's own budget has had three agreement bugs this week. The NEIGHBOUR side
    - the other threads sharing the folder - had no check against a real pickup at all,
    and it is bounded by two constants rather than one. Measured against the constants
    read from source, so raising either one cannot silently turn this green."""
    home = make_home({})
    try:
        with open(os.path.join(home, ".claude", "handoff",
                               KEY + ".guardaaa.md"), "w", encoding="utf-8") as f:
            f.write("HANDOFF LABEL: Context Guard -2 (18 Sep)" + chr(10) * 2 + "# n" + chr(10))
        with open(os.path.join(home, ".claude", "handoff",
                               KEY + ".harborba.md"), "w", encoding="utf-8") as f:
            f.write("HANDOFF LABEL: Harbor -4 (18 Sep)" + chr(10) * 2 + "# n" + chr(10))
        lp = os.path.join(home, ".claude", "handoff", KEY + ".requests.md")
        with open(lp, "w", encoding="utf-8") as f:
            f.write("# What the user actually asked for - his own words" + chr(10))
        for i in range(2):
            append_new_entry(home, "2026-09-18 10:0%d:00" % i, "guardaaa",
                             "my own request %d" % i)
        # a loud neighbour: far more entries and far more chars than either bound allows
        for i in range(40):
            append_new_entry(home, "2026-09-18 11:%02d:00" % i, "harborba",
                             "neighbour request %d: " % i + ("word " * 120))
        p = run(home, "freshchat", "Context Guard -2 (18 Sep)")
        expect_clean(p, "tail-neighbour")
        ctx = context_of(p)
        split = ctx.find("OTHER CHATS")
        check("tail-neighbour: the pickup did split the two sides", split > -1,
              repr(ctx[-300:]))
        theirs = ctx[split:] if split > -1 else ""
        carried = theirs.count(chr(10) + "### ")
        n_max = guard_constant("LEDGER_OTHERS")
        c_max = guard_constant("LEDGER_OTHERS_CHARS")
        check("tail-neighbour: no more neighbour entries than LEDGER_OTHERS allows",
              0 < carried <= n_max, "carried=%d, LEDGER_OTHERS=%d" % (carried, n_max))
        check("tail-neighbour: and no more neighbour CHARS than LEDGER_OTHERS_CHARS",
              len(theirs) <= c_max + 2000,
              "neighbour section=%d chars, LEDGER_OTHERS_CHARS=%d" % (len(theirs), c_max))
        check("tail-neighbour: the loud neighbour did not crowd out his own words",
              "my own request 0" in ctx and "my own request 1" in ctx,
              repr(ctx[:400]))
    finally:
        shutil.rmtree(home, ignore_errors=True)



def test_bootstrap_finds_an_index_line_that_moved_to_the_pointer():
    """19 Sep 2026: once every project had its own folder, 45 of the shared index's 48
    lines were moved out to ~/.claude/context-guard/projects-index.md, which cut 6,670
    bytes off every turn of every chat started in D:\\Claude. The bootstrap builds a new
    project's index by CARRYING OVER his line - it must never invent one - so a line that
    has moved has to be found where it now lives. Without this the next project added to
    the manifest gets a folder full of memories and an index that does not mention them."""
    home = make_boot_home()
    try:
        idxp = os.path.join(home, ".claude", "projects", KEY, "memory", "MEMORY.md")
        with open(idxp, encoding="utf-8") as f:
            lines = f.read().splitlines()
        moved = [ln for ln in lines if "(plant-watering-log-app.md)" in ln]
        check("bootstrap/pointer: the fixture really has that line to move",
              len(moved) == 1, "lines=%r" % (lines[:6],))
        rest = [ln for ln in lines if "(plant-watering-log-app.md)" not in ln]
        with open(idxp, "w", encoding="utf-8") as f:
            f.write(chr(10).join(rest) + chr(10))
        with open(os.path.join(home, ".claude", "context-guard", "projects-index.md"),
                  "w", encoding="utf-8") as f:
            f.write("# Other projects - full index" + chr(10) * 2
                    + chr(10).join(moved) + chr(10))
        p = boot(home, r"D:\AI Projects\Garden")
        expect_clean(p, "bootstrap/pointer")
        with open(os.path.join(home, ".claude", "projects", "D--AI-Projects-Garden",
                               "memory", "MEMORY.md"), encoding="utf-8") as f:
            got = f.read()
        check("bootstrap/pointer: his line was carried over from the pointer file",
              moved[0].strip() in got, repr(got[:300]))
        check("bootstrap/pointer: and it was not replaced by an invented summary",
              got.count("(plant-watering-log-app.md)") == 1, repr(got[:300]))
    finally:
        shutil.rmtree(home, ignore_errors=True)



# --------------- change 13: the keyword he types must hand him THAT project's memory
# His words, 20 Sep 2026: "this needs to be automated when the keyword is first written in
# a new chat cause idk how to do that to begin with". He was being told to open a chat in
# D:\AI Projects\Qix-Data-Pipeline so it would load QIX's memories instead of the shared
# index - and being told is the bug. A chat started in D:\Claude gets the D--Claude folder
# whatever thread it is about, because the memory folder keys off the DIRECTORY.
#
# So the label he already types to summon a note now also fetches that project's own
# MEMORY.md. The mapping is explicit in memory-manifest.json ("threads"), never fuzzy:
# "qix bot" and "qix handover" are one project by his review, not by luck.
MANIFEST_THREADS = {
    "_README": "test fixture",
    "projects": {
        "qix": {"dir": "D:\\AI Projects\\QixTrackerBot", "also": [],
                "threads": ["qix handover", "qix bot"], "files": []},
        "harbor": {"dir": "D:\\AI Projects\\Harbor", "also": [],
                     "threads": ["harbor"], "files": []},
        "context-guard": {"dir": "D:\\Claude\\context-guard", "also": [],
                          "threads": [], "files": []},
    },
}


def write_threads_manifest(home, man=None):
    state = os.path.join(home, ".claude", "context-guard")
    os.makedirs(state, exist_ok=True)
    with open(os.path.join(state, "memory-manifest.json"), "w", encoding="utf-8") as f:
        json.dump(MANIFEST_THREADS if man is None else man, f)


def write_project_index(home, key, body):
    """A project's OWN memory index - the curated one, not the flat pointer file."""
    d = os.path.join(home, ".claude", "projects", key, "memory")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "MEMORY.md"), "w", encoding="utf-8") as f:
        f.write(body)
    return d


def test_a_thread_label_hands_back_that_projects_memory_index():
    """The label carries the number and the date he actually types, not a tidy stem."""
    home = make_home({KEY + ".9e19c7ab.md": NOTE_CTX, KEY + ".b0b0b0b0.md": NOTE_QIX})
    try:
        write_threads_manifest(home)
        write_project_index(home, "D--AI-Projects-QixTrackerBot",
                            "# Memory Index\n- [a](b.md) - INDEX-QIX\n")
        p = run(home, "aa11-label-mem", "QIX bot -3 (20 Sep)")
        ctx = context_of(p)
        expect_clean(p, "label-memory")
        check("label-memory: the note still arrives", "body-QIXBOT" in ctx, repr(ctx[:200]))
        check("label-memory: that project's own index came with it",
              "INDEX-QIX" in ctx, repr(ctx[-400:]))
        check("label-memory: and it says which index it is and where it came from",
              "MEMORY INDEX for qix" in ctx and "D--AI-Projects-QixTrackerBot" in ctx,
              repr(ctx[-400:]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_a_label_that_maps_to_no_project_appends_nothing():
    """THE CONTROL. Without it, "append every index on disk" passes the test above.

    context-guard is in the manifest with an EMPTY threads list - reviewed and mapped to
    nothing on purpose - and its index sits on disk next to QIX's."""
    home = make_home({KEY + ".9e19c7ab.md": NOTE_CTX})
    try:
        write_threads_manifest(home)
        write_project_index(home, "D--AI-Projects-QixTrackerBot",
                            "# Memory Index\n- [a](b.md) - INDEX-QIX\n")
        write_project_index(home, "D--Claude-context-guard",
                            "# Memory Index\n- [a](b.md) - INDEX-GUARD\n")
        p = run(home, "bb22-label-none", "context guard")
        ctx = context_of(p)
        expect_clean(p, "label-none")
        check("label-none: the note still arrives", "body-CONTEXTGUARD" in ctx, repr(ctx[:200]))
        check("label-none: no index was appended",
              "INDEX-QIX" not in ctx and "INDEX-GUARD" not in ctx, repr(ctx[-300:]))
        check("label-none: and it said nothing about memory at all",
              "MEMORY INDEX for" not in ctx, repr(ctx[-300:]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_a_project_with_no_memory_folder_is_skipped_not_crashed():
    """Nine `also` directories have no memory folder. Silence, not a traceback."""
    home = make_home({KEY + ".143f0320.md": NOTE_SPOT})
    try:
        write_threads_manifest(home)          # harbor maps; nothing ever furnished it
        p = run(home, "cc33-label-nofolder", "Harbor")
        ctx = context_of(p)
        expect_clean(p, "label-nofolder")     # rc 0 and no traceback IS the assertion
        check("label-nofolder: the note still arrives", "body-HARBOR" in ctx, repr(ctx[:200]))
        check("label-nofolder: nothing was invented",
              "MEMORY INDEX for" not in ctx, repr(ctx[-300:]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_project_index_is_not_re_injected_every_turn():
    """It rides the pickup turn only. Anywhere else re-injects the index forever, which
    is the exact bloat this tool exists to remove."""
    home = make_home({KEY + ".b0b0b0b0.md": NOTE_QIX})
    try:
        write_threads_manifest(home)
        write_project_index(home, "D--AI-Projects-QixTrackerBot",
                            "# Memory Index\n- [a](b.md) - INDEX-QIX\n")
        first = context_of(run(home, "dd44-once", "QIX bot"))
        check("label-once: the first turn carried it", "INDEX-QIX" in first, repr(first[-300:]))
        second = run(home, "dd44-once", "QIX bot")
        check("label-once: the next turn does not",
              "INDEX-QIX" not in context_of(second), repr(context_of(second)[:300]))
        expect_clean(second, "label-once")
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_label_fetch_names_the_shared_folder_and_the_project_list():
    """ONE HOME, 25 Sep 2026. The fetch used to say the files were "in the same folder" as
    the list, so chats edited the project copies while new memories went to the shared
    folder, and the two drifted. It must now name BOTH places: the list it read, and the
    shared folder where every file that list points at lives."""
    home = make_home({KEY + ".9e19c7ab.md": NOTE_CTX, KEY + ".b0b0b0b0.md": NOTE_QIX})
    try:
        write_threads_manifest(home)
        lst = os.path.join(write_project_index(home, "D--AI-Projects-QixTrackerBot",
                                               "# Memory Index\n- [a](b.md) - INDEX-QIX\n"),
                           "MEMORY.md")
        shared = os.path.join(home, ".claude", "projects", KEY, "memory")
        # CONTROL: a label no project claims fetches nothing, so it names neither place.
        ctl = context_of(run(home, "ee55-label-ctl", "context guard"))
        check("label-home: control - an unmapped label fetches no index",
              "INDEX-QIX" not in ctl, repr(ctl[-300:]))
        check("label-home: control - and names no shared folder", shared not in ctl,
              repr(ctl[-300:]))
        p = run(home, "ee55-label-home", "QIX bot")
        ctx = context_of(p)
        expect_clean(p, "label-home")
        check("label-home: the index arrived", "INDEX-QIX" in ctx, repr(ctx[-400:]))
        check("label-home: it names the project list it read", lst in ctx, repr(ctx[-600:]))
        check("label-home: it names the shared folder the files live in", shared in ctx,
              repr(ctx[-600:]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


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
    home = throwaway_dir("guardsweep-")
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
        check("list-header: the first call's stdout is exactly [True, True]",
              p1.stdout.strip() == "[True, True]", repr(p1.stdout))
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
        check("list-header: BETA's old list was backed up too",
              [raw_bytes(x) for x in sweep_backups(home, BETA, "MEMORY.md")] == [old_b],
              str(sweep_backups(home, BETA, "MEMORY.md")))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_a_list_that_changes_during_the_write_is_left_alone():
    """_write_list checks the list against `before` once, before the backup is written - not
    again right before os.replace swaps the new bytes in. A write that lands in that gap,
    such as another chat saving a memory, is silently overwritten and lost from both the list
    and the backup. A second check right before os.replace must catch it instead:
    ensure_list_header returns False, the late entry stays in the list, and the temp file is
    removed rather than left behind."""
    home = make_sweep_home()
    try:
        a = plant(home, ALPHA, "MEMORY.md",
                  "# Memory Index\n%s\n" % index_entry("alpha-notes"), eol="\r\n")
        old_a = raw_bytes(a)
        late = b"- [late](late.md) - saved during the write\r\n"
        code = ("p = %r\n"
                "real = guard._put_new\n"
                "def put_new_then_another_chat_saves(dest, blob, mtime=None):\n"
                "    real(dest, blob, mtime)\n"
                "    with open(p, 'ab') as f:\n"
                "        f.write(%r)\n"
                "guard._put_new = put_new_then_another_chat_saves\n"
                "print(guard.ensure_list_header(p))\n") % (a, late)
        p1 = guard_call(home, code)
        new_a = raw_bytes(a)
        check("write-race: the call ran cleanly", p1.returncode == 0, p1.stderr[-300:])
        check("write-race: ensure_list_header returned False",
              p1.stdout.strip() == "False", p1.stdout + p1.stderr[-300:])
        check("write-race: the late entry another chat saved is still in the list",
              new_a == old_a + late, repr(new_a))
        check("write-race: no MEMORY.md.sweep-tmp is left in the memory folder",
              not os.path.exists(a + ".sweep-tmp"), str(sorted(os.listdir(os.path.dirname(a)))))
    finally:
        shutil.rmtree(home, ignore_errors=True)


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


# ---------------------------------------------- one home: commit B's quality-review fixes
def test_one_unwritable_list_does_not_stop_the_sweep():
    """I1. A list that cannot be replaced - here a read-only MEMORY.md in the first folder -
    used to raise out of the whole sweep: no other folder was reached, an orphan
    MEMORY.md.sweep-tmp was left, and every retry piled up one more list backup. Now that
    folder is left alone, everything else is swept, nothing is left behind, and the folder
    is swept on the first sweep after the list is writable again."""
    import stat
    home = make_sweep_home()
    a = None
    try:
        a = plant_list(home, ALPHA, [index_entry("alpha-notes")], "")
        asrc = plant(home, ALPHA, "alpha-notes.md", memory_text("alpha-notes", "body-ALPHA"))
        plant_list(home, BETA, [index_entry("beta-notes")], header_of(home))
        bsrc = plant(home, BETA, "beta-notes.md", memory_text("beta-notes", "body-BETA"))
        plant(home, KEY, "dup.md", memory_text("dup", "body-SHARED"))
        plant(home, BETA, "dup.md", memory_text("dup", "body-SPARE"))
        old_a = raw_bytes(a)
        os.chmod(a, stat.S_IREAD)
        p1 = run_sweep(home)
        p2 = run_sweep(home, sid="sweep002")
        expect_clean(p1, "sweep/unwritable")
        shared_beta = os.path.join(memory_folder(home, KEY), "beta-notes.md")
        check("sweep/unwritable: the folder after it was still swept",
              not os.path.exists(bsrc) and os.path.isfile(shared_beta), bsrc)
        check("sweep/unwritable: the sweep did not stop - its log has no 'stopped by'",
              "stopped by" not in guard_log(home), guard_log(home)[-400:])
        check("sweep/unwritable: no MEMORY.md.sweep-tmp is left",
              not os.path.exists(a + ".sweep-tmp"), str(sorted(os.listdir(os.path.dirname(a)))))
        check("sweep/unwritable: the list itself is untouched", raw_bytes(a) == old_a,
              repr(raw_bytes(a)))
        check("sweep/unwritable: and so is its stray - the list does not name the shared "
              "folder yet", os.path.exists(asrc), asrc)
        check("sweep/unwritable: two sweeps left no list backup piling up for it",
              len(sweep_backups(home, ALPHA, "MEMORY.md")) <= 1,
              str(sweep_backups(home, ALPHA, "MEMORY.md")))
        check("sweep/unwritable: the one-json report of the later folder still holds",
              one_json(p1) and "dup" in context_of(p1), repr(p1.stdout[:300]))
        check("sweep/unwritable: the same pair is not reported again", p2.stdout == "",
              repr(p2.stdout[:200]))
        os.chmod(a, stat.S_IREAD | stat.S_IWRITE)
        run_sweep(home, sid="sweep003")
        check("sweep/unwritable: control - once writable its stray goes home",
              not os.path.exists(asrc)
              and os.path.isfile(os.path.join(memory_folder(home, KEY), "alpha-notes.md")),
              asrc)
        check("sweep/unwritable: and its list names the shared folder now",
              memory_folder(home, KEY) in (raw_bytes(a) or b"").decode("utf-8"),
              repr(raw_bytes(a)))
    finally:
        if a and os.path.exists(a):
            os.chmod(a, stat.S_IREAD | stat.S_IWRITE)
        shutil.rmtree(home, ignore_errors=True)


def test_a_list_held_open_piles_up_no_backups_and_no_debris():
    """I1, the other way a list cannot be replaced: another program holds it open, so
    os.replace fails even though the file is writable. On Windows a plain open() blocks the
    replace. The sweep logs it, removes its temp file, and a retry does not add a second,
    identical backup."""
    home = make_sweep_home()
    fh = None
    try:
        a = plant_list(home, ALPHA, [index_entry("alpha-notes")], "")
        asrc = plant(home, ALPHA, "alpha-notes.md", memory_text("alpha-notes", "body-ALPHA"))
        plant_list(home, BETA, [index_entry("beta-notes")], header_of(home))
        bsrc = plant(home, BETA, "beta-notes.md", memory_text("beta-notes", "body-BETA"))
        old_a = raw_bytes(a)
        fh = open(a, "rb")
        run_sweep(home)
        run_sweep(home, sid="sweep002")
        check("sweep/held: the folder after it was still swept", not os.path.exists(bsrc), bsrc)
        check("sweep/held: no MEMORY.md.sweep-tmp is left", not os.path.exists(a + ".sweep-tmp"),
              str(sorted(os.listdir(os.path.dirname(a)))))
        check("sweep/held: two sweeps made one list backup, not two",
              len(sweep_backups(home, ALPHA, "MEMORY.md")) <= 1,
              str(sweep_backups(home, ALPHA, "MEMORY.md")))
        if os.name == "nt":
            check("sweep/held: the held list is untouched, and so is its stray",
                  raw_bytes(a) == old_a and os.path.exists(asrc), repr(raw_bytes(a)))
        fh.close()
        fh = None
        run_sweep(home, sid="sweep003")
        check("sweep/held: control - once released its stray goes home", not os.path.exists(asrc),
              asrc)
        check("sweep/held: and the list has exactly one backup, of the original",
              [raw_bytes(x) for x in sweep_backups(home, ALPHA, "MEMORY.md")] == [old_a],
              str(sweep_backups(home, ALPHA, "MEMORY.md")))
    finally:
        if fh:
            fh.close()
        shutil.rmtree(home, ignore_errors=True)


def test_a_write_that_fails_midway_leaves_no_half_file_at_the_shared_path():
    """I-1. _put_new opens the destination with "xb" and then writes it. A write that dies
    midway (disk full) used to leave a TRUNCATED file at the canonical path, so the next
    sweep read it as a different copy - "two different copies" - and left both. Now the
    half file is removed and the error still raised; a destination that already existed
    is never touched (the "xb" open itself fails, so there is nothing of ours to remove)."""
    home = make_sweep_home()
    try:
        dest = os.path.join(memory_folder(home, KEY), "alpha-notes.md")
        code = ("real = open\n"
                "class W:\n"
                "    def __init__(s, f): s.f = f\n"
                "    def __enter__(s): return s\n"
                "    def __exit__(s, *a):\n"
                "        s.f.close()\n"
                "        return False\n"
                "    def write(s, b):\n"
                "        s.f.write(b[:5])\n"
                "        s.f.flush()\n"
                "        raise OSError('injected disk full')\n"
                "def fake(p, mode='r', *a, **k):\n"
                "    f = real(p, mode, *a, **k)\n"
                "    return W(f) if mode == 'xb' else f\n"
                "guard.open = fake\n"
                "try:\n"
                "    guard._put_new(%r, b'x' * 100)\n"
                "except OSError as e:\n"
                "    print('raised', e)\n" % dest)
        p = guard_call(home, code)
        check("put_new: the write error was raised to the caller",
              "raised injected disk full" in p.stdout, (p.stdout + p.stderr)[-300:])
        check("put_new: no half file is left at the destination",
              not os.path.exists(dest), repr(raw_bytes(dest)))
        keep = plant(home, KEY, "beta-notes.md", memory_text("beta-notes", "body-KEPT"))
        before = raw_bytes(keep)
        p2 = guard_call(home, "try:\n"
                              "    guard._put_new(%r, b'other')\n"
                              "except FileExistsError:\n"
                              "    print('exists')\n" % keep)
        check("put_new: an existing destination still refuses the write",
              "exists" in p2.stdout, (p2.stdout + p2.stderr)[-300:])
        check("put_new: and is never removed or changed", raw_bytes(keep) == before,
              repr(raw_bytes(keep)))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_one_failing_folder_file_or_list_step_does_not_stop_the_rest_of_the_sweep():
    """I-2. The sweep isolates trouble three ways: a try per folder, a try per file, and a
    try around the shared list's lines. Each is broken here with an injected fault, and the
    work after it must still happen and the later report still be printed."""
    # the folder layer: the first folder raises out of _sweep_folder
    home = make_sweep_home()
    try:
        plant_list(home, ALPHA, [], header_of(home))
        aa = plant(home, ALPHA, "a.md", memory_text("a", "only-A"))
        plant_list(home, BETA, [], header_of(home))
        bb = plant(home, BETA, "b.md", memory_text("b", "only-B"))
        p = guard_call(home, "real = guard._sweep_folder\n"
                             "def f(d, now, pairs):\n"
                             "    if 'Alpha' in d:\n"
                             "        raise PermissionError('folder boom')\n"
                             "    return real(d, now, pairs)\n"
                             "guard._sweep_folder = f\n"
                             "print(repr(guard.sweep_memory_strays()))\n")
        check("sweep/isolation folder: the sweep ended cleanly", p.returncode == 0, p.stderr[-300:])
        check("sweep/isolation folder: the failing folder is logged and left as it was",
              "PermissionError: folder boom" in guard_log(home) and os.path.exists(aa),
              guard_log(home)[-300:])
        check("sweep/isolation folder: the next folder was still swept",
              not os.path.exists(bb)
              and os.path.isfile(os.path.join(memory_folder(home, KEY), "b.md")), bb)
    finally:
        shutil.rmtree(home, ignore_errors=True)
    # the file layer: one file raises out of _move, the file after it is still moved
    home = make_sweep_home()
    try:
        plant_list(home, ALPHA, [], header_of(home))
        a = plant(home, ALPHA, "a.md", memory_text("a", "only-A"))
        b = plant(home, ALPHA, "b.md", memory_text("b", "only-B"))
        plant(home, KEY, "dup.md", memory_text("dup", "body-SHARED"))
        plant(home, ALPHA, "dup.md", memory_text("dup", "body-SPARE"))
        p = guard_call(home, "real = guard._move\n"
                             "def m(src, dest):\n"
                             "    if src.endswith('a.md'):\n"
                             "        raise OSError('file boom')\n"
                             "    return real(src, dest)\n"
                             "guard._move = m\n"
                             "print(repr(guard.sweep_memory_strays()))\n")
        check("sweep/isolation file: the sweep ended cleanly", p.returncode == 0, p.stderr[-300:])
        check("sweep/isolation file: the failing file is logged and left as it was",
              "a.md stopped by OSError: file boom" in guard_log(home) and os.path.exists(a),
              guard_log(home)[-300:])
        check("sweep/isolation file: the next file in the same folder was still moved home",
              not os.path.exists(b)
              and os.path.isfile(os.path.join(memory_folder(home, KEY), "b.md")), b)
        check("sweep/isolation file: and the two-copies report after it is still printed",
              "dup" in p.stdout, repr(p.stdout[:300]))
    finally:
        shutil.rmtree(home, ignore_errors=True)
    # the lines layer: _sweep_lines raises, the folders and the report are unaffected
    home = make_sweep_home()
    try:
        plant_list(home, ALPHA, [], header_of(home))
        plant(home, KEY, "dup.md", memory_text("dup", "body-SHARED"))
        plant(home, ALPHA, "dup.md", memory_text("dup", "body-SPARE"))
        plant_list(home, BETA, [], header_of(home))
        b = plant(home, BETA, "b.md", memory_text("b", "only-B"))
        p = guard_call(home, "def l(*a):\n"
                             "    raise OSError('lines boom')\n"
                             "guard._sweep_lines = l\n"
                             "print(repr(guard.sweep_memory_strays()))\n")
        check("sweep/isolation lines: the sweep ended cleanly", p.returncode == 0, p.stderr[-300:])
        check("sweep/isolation lines: the failure is logged",
              "lines stopped by OSError: lines boom" in guard_log(home), guard_log(home)[-300:])
        check("sweep/isolation lines: the folders were still swept", not os.path.exists(b), b)
        check("sweep/isolation lines: and the report is still printed", "dup" in p.stdout,
              repr(p.stdout[:300]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_a_spare_that_cannot_be_removed_piles_up_no_backups():
    """M-1. Case 2 backs a spare up and then removes it. If the remove always fails (held
    open, read-only folder) every chat's sweep used to make one more identical backup. Now
    two sweeps leave exactly one, and once the remove works the spare goes and the backup
    stays."""
    home = make_sweep_home()
    try:
        plant(home, KEY, "alpha-notes.md", memory_text("alpha-notes", "body-ALPHA", "2026-09-20"))
        spare = plant(home, ALPHA, "alpha-notes.md",
                      memory_text("alpha-notes", "body-ALPHA", "2026-09-01"))
        blob = raw_bytes(spare)
        code = ("import os\n"
                "real = os.remove\n"
                "def boom(path, *a, **k):\n"
                "    if str(path).endswith('.md'):\n"
                "        raise OSError('injected cannot remove')\n"
                "    return real(path, *a, **k)\n"
                "os.remove = boom\n"
                "print(repr(guard.sweep_memory_strays()))\n")
        guard_call(home, code)
        guard_call(home, code)
        got = sweep_backups(home, ALPHA, "alpha-notes.md")
        check("sweep/spare-stuck: two failing sweeps left exactly one backup", len(got) == 1,
              str(got))
        check("sweep/spare-stuck: and the spare is still there, whole", raw_bytes(spare) == blob,
              spare)
        run_sweep(home, sid="sweep003")
        got = sweep_backups(home, ALPHA, "alpha-notes.md")
        check("sweep/spare-stuck: once removable the spare goes, still with one backup",
              not os.path.exists(spare) and [raw_bytes(g) for g in got] == [blob], str(got))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_a_project_folder_that_is_a_junction_onto_the_shared_folder_is_left_alone():
    """M-b. A project `memory` dir that is a junction onto the shared folder holds the
    shared copies themselves. It used to read as a folder of spares: every shared file was
    'identical to its shared copy' and moved to the backup folder, and the shared list lost
    its lines. It must be skipped whole."""
    home = make_sweep_home()
    link = memory_folder(home, ALPHA)
    made = False
    try:
        shared_dir = memory_folder(home, KEY)
        top = plant_list(home, KEY, [index_entry("alpha-notes")], header_of(home))
        note = plant(home, KEY, "alpha-notes.md", memory_text("alpha-notes", "body-SHARED"))
        old_top, old_note = raw_bytes(top), raw_bytes(note)
        os.makedirs(os.path.dirname(link))
        r = subprocess.run(["cmd", "/c", "mklink", "/J", link, shared_dir],
                           capture_output=True, text=True)
        made = r.returncode == 0 and os.path.isdir(link)
        if not made:
            print("  SKIP  junction: mklink failed here (%s) - the hard-link test covers "
                  "the samefile branch" % (r.stdout + r.stderr).strip())
            return
        p = run_sweep(home)
        expect_clean(p, "sweep/junction")
        check("sweep/junction: every shared file is still in the shared folder",
              raw_bytes(note) == old_note, repr(raw_bytes(note)))
        check("sweep/junction: the shared list is untouched", raw_bytes(top) == old_top,
              repr(raw_bytes(top)))
        check("sweep/junction: nothing was moved to a backup folder",
              not sweep_backups(home, ALPHA, "alpha-notes.md"), str(
                  sweep_backups(home, ALPHA, "alpha-notes.md")))
        check("sweep/junction: and it said nothing", p.stdout == "", repr(p.stdout[:200]))
    finally:
        if made:
            os.rmdir(link)       # removes the junction only, never the shared folder
        shutil.rmtree(home, ignore_errors=True)


def test_a_file_hard_linked_to_its_shared_copy_is_not_a_spare():
    """M-b, the file-level half: a project file that IS the shared file (a hard link) is
    not a spare copy of it, and must not be moved to the backup folder."""
    home = make_sweep_home()
    try:
        plant_list(home, ALPHA, [index_entry("alpha-notes")], header_of(home))
        shared = plant(home, KEY, "alpha-notes.md", memory_text("alpha-notes", "body-SHARED"))
        src = os.path.join(memory_folder(home, ALPHA), "alpha-notes.md")
        os.link(shared, src)
        blob = raw_bytes(shared)
        p = run_sweep(home)
        expect_clean(p, "sweep/hardlink")
        check("sweep/hardlink: the linked file stays in the project folder",
              raw_bytes(src) == blob, repr(raw_bytes(src)))
        check("sweep/hardlink: the shared file is untouched", raw_bytes(shared) == blob,
              repr(raw_bytes(shared)))
        check("sweep/hardlink: nothing went to a backup folder",
              not sweep_backups(home, ALPHA, "alpha-notes.md"),
              str(sweep_backups(home, ALPHA, "alpha-notes.md")))
        check("sweep/hardlink: and it said nothing", p.stdout == "", repr(p.stdout[:200]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_a_move_re_reads_its_source_before_removing_it():
    """M-d. _move verified the copy against what it read, but removed the source without
    looking again: a chat that saved into the source between the copy and the remove lost
    that write. Now the source is compared once more, and a source that changed is left
    where it is, next to the copy, and logged."""
    home = make_sweep_home()
    try:
        plant_list(home, ALPHA, [index_entry("alpha-notes")], header_of(home))
        src = plant(home, ALPHA, "alpha-notes.md", memory_text("alpha-notes", "body-ALPHA"))
        old = raw_bytes(src)
        late = b"\nsaved by another chat during the move\n"
        dest = os.path.join(memory_folder(home, KEY), "alpha-notes.md")
        code = ("real = guard._put_new\n"
                "def put_new_then_another_chat_saves(dest, blob, mtime=None):\n"
                "    real(dest, blob, mtime)\n"
                "    with open(%r, 'ab') as f:\n"
                "        f.write(%r)\n"
                "guard._put_new = put_new_then_another_chat_saves\n"
                "print(repr(guard.sweep_memory_strays()))\n") % (src, late)
        p = guard_call(home, code)
        check("move-race: the sweep ran cleanly", p.returncode == 0 and p.stdout.strip() == "''",
              (p.stdout + p.stderr)[-300:])
        check("move-race: the source is still there with the late write",
              raw_bytes(src) == old + late, repr(raw_bytes(src)))
        check("move-race: the copy is there too, whole", raw_bytes(dest) == old,
              repr(raw_bytes(dest)))
        log = guard_log(home)
        check("move-race: it is logged", "alpha-notes.md" in log and "both copies" in log,
              log[-400:])
        check("move-race: it did not claim to have moved it",
              "moved %s/alpha-notes.md home" % ALPHA not in log, log[-400:])
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_an_already_backed_up_spare_is_re_read_before_it_is_removed():
    """Case 2's skip branch (an identical backup already exists) removes the spare. A chat
    that saved into it after it was read must not lose that write: the spare is compared
    once more, and one that changed stays where it is, logged."""
    home = make_sweep_home()
    try:
        plant(home, KEY, "alpha-notes.md", memory_text("alpha-notes", "body-ALPHA", "2026-09-20"))
        src = plant(home, ALPHA, "alpha-notes.md",
                    memory_text("alpha-notes", "body-ALPHA", "2026-09-01"))
        old = raw_bytes(src)
        bdir = os.path.join(home, ".claude", "memory-backups",
                            datetime.date.today().isoformat() + "-sweep", ALPHA)
        os.makedirs(bdir)
        with open(os.path.join(bdir, "alpha-notes.md"), "wb") as f:
            f.write(old)
        late = b"\nsaved by another chat after the read\n"
        code = ("real = guard._has_backup\n"
                "def has_then_another_chat_saves(d, name, blob):\n"
                "    r = real(d, name, blob)\n"
                "    with open(%r, 'ab') as f:\n"
                "        f.write(%r)\n"
                "    return r\n"
                "guard._has_backup = has_then_another_chat_saves\n"
                "print(repr(guard.sweep_memory_strays()))\n") % (src, late)
        p = guard_call(home, code)
        check("spare-race: the sweep ran cleanly", p.returncode == 0 and p.stdout.strip() == "''",
              (p.stdout + p.stderr)[-300:])
        check("spare-race: the late write survives at the spare", raw_bytes(src) == old + late,
              repr(raw_bytes(src)))
        check("spare-race: it is logged", "alpha-notes.md" in guard_log(home)
              and "changed" in guard_log(home), guard_log(home)[-400:])
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_a_put_new_cleanup_that_fails_is_logged_and_the_original_error_raised():
    """When the write fails AND removing the half file fails too, the half file is still
    there: say so in the log, naming it, and raise the write's error, not the remove's."""
    home = make_sweep_home()
    try:
        dest = os.path.join(memory_folder(home, KEY), "alpha-notes.md")
        code = ("import os\n"
                "real = open\n"
                "class W:\n"
                "    def __init__(s, f): s.f = f\n"
                "    def __enter__(s): return s\n"
                "    def __exit__(s, *a):\n"
                "        s.f.close()\n"
                "        return False\n"
                "    def write(s, b):\n"
                "        s.f.write(b[:5])\n"
                "        raise OSError('injected disk full')\n"
                "def fake(p, mode='r', *a, **k):\n"
                "    f = real(p, mode, *a, **k)\n"
                "    return W(f) if mode == 'xb' else f\n"
                "guard.open = fake\n"
                "def nope(path, *a, **k):\n"
                "    raise PermissionError('injected cannot remove')\n"
                "os.remove = nope\n"
                "try:\n"
                "    guard._put_new(%r, b'x' * 100)\n"
                "except Exception as e:\n"
                "    print('raised', type(e).__name__, e)\n" % dest)
        p = guard_call(home, code)
        check("put_new-cleanup: the ORIGINAL error propagates",
              "raised OSError injected disk full" in p.stdout, (p.stdout + p.stderr)[-300:])
        log = guard_log(home)
        check("put_new-cleanup: the failed cleanup is logged, naming the file",
              dest in log and "injected cannot remove" in log, log[-400:])
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_case_4_gives_a_target_list_with_no_header_the_header():
    """M-f. Case 4 appended a line into a settled target list as it was. A list that has no
    header does not say where the shared files are, so the line would point at nothing:
    the appended list gets the header too."""
    home = make_sweep_home()
    try:
        plant(home, KEY, "beta-notes.md", memory_text("beta-notes", "body-BETA"))
        line = "- [Beta, in his words](beta-notes.md) - the wording he chose"
        top = plant(home, KEY, "MEMORY.md", "# Memory Index\n%s\n" % line)
        blst = plant_list(home, BETA, [index_entry("beta-own")], "")
        p = run_sweep(home)
        expect_clean(p, "sweep/case4-header")
        got = raw_bytes(blst).decode("utf-8")
        check("sweep/case4-header: his line was appended", got.splitlines()[-1] == line,
              repr(got[-200:]))
        check("sweep/case4-header: and the list now names the shared folder",
              memory_folder(home, KEY) in got, repr(got))
        check("sweep/case4-header: the header sits above the first entry",
              got.find(memory_folder(home, KEY)) < got.find("(beta-own.md)"), repr(got))
        check("sweep/case4-header: the line left the shared list",
              "(beta-notes.md)" not in raw_bytes(top).decode("utf-8"), repr(raw_bytes(top)))
    finally:
        shutil.rmtree(home, ignore_errors=True)


# ---------------------------------------------------------------- update notice + --update
# Every "origin" here is a LOCAL bare repo in a temp dir - never the network.
HERE_DIR = os.path.dirname(os.path.abspath(__file__))
AUDIT = os.path.join(HERE_DIR, "audit.py")


def git_in(cwd, *args):
    """Run git in `cwd` with a throwaway identity and return stdout."""
    cmd = ["git", "-c", "user.name=Tester", "-c", "user.email=tester@example.com",
           "-c", "commit.gpgsign=false", "-C", cwd] + list(args)
    return subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=60).stdout


def commit_file(work, name, body, subject):
    with open(os.path.join(work, name), "w", encoding="utf-8", newline="") as f:
        f.write(body)
    git_in(work, "add", name)
    git_in(work, "commit", "-q", "-m", subject)


def upd_setup():
    """A bare origin with one commit, its working copy `work`, and a clone of it."""
    tmp = throwaway_dir("updtest-")
    origin = os.path.join(tmp, "origin.git")
    work = os.path.join(tmp, "work")
    clone = os.path.join(tmp, "clone")
    os.makedirs(origin)
    git_in(origin, "init", "-q", "--bare")
    git_in(origin, "symbolic-ref", "HEAD", "refs/heads/main")
    os.makedirs(work)
    git_in(work, "init", "-q")
    git_in(work, "checkout", "-q", "-B", "main")
    shutil.copy(INSTALL, os.path.join(work, "install.py"))   # apply() re-runs the clone's own
    git_in(work, "add", "install.py")                         # install.py after an update
    commit_file(work, "a.txt", "one\n", "first change")
    git_in(work, "remote", "add", "origin", origin)
    git_in(work, "push", "-q", "origin", "main")
    subprocess.run(["git", "clone", "-q", origin, clone], check=True, capture_output=True,
                   timeout=60)
    return {"tmp": tmp, "origin": origin, "work": work, "clone": clone}


def upd_push(s, name, subject):
    commit_file(s["work"], name, subject + "\n", subject)
    git_in(s["work"], "push", "-q", "origin", "main")


def upd_module():
    if HERE_DIR not in sys.path:
        sys.path.insert(0, HERE_DIR)
    import update
    return update


def upd_state(home):
    p = os.path.join(home, ".claude", "context-guard", "update-check.json")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def upd_apply(u, s, home, dry_run=False):
    import contextlib
    import io as _io
    buf = _io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = u.apply(dry_run=dry_run, clone=s["clone"], home=home)
    return rc, buf.getvalue()


def tree_fingerprint(clone):
    files = {}
    for n in sorted(os.listdir(clone)):
        p = os.path.join(clone, n)
        if os.path.isfile(p):
            with open(p, "rb") as f:
                files[n] = f.read()
    return git_in(clone, "rev-parse", "HEAD").strip(), files


def test_update_up_to_date_says_nothing_and_behind_says_the_line():
    u = upd_module()
    s = upd_setup()
    home = make_home({})
    try:
        check("update: an up-to-date clone gets no line", u.check(clone=s["clone"], home=home) == "")
        home2 = make_home({})
        upd_push(s, "b.txt", "second change")
        upd_push(s, "c.txt", "third change")
        line = u.check(clone=s["clone"], home=home2)
        check("update: behind by 2 says '2 new change(s)'", "2 new change(s)" in line, line)
        check("update: the line names install.py by path",
              os.path.join(s["clone"], "install.py") in line, line)
        check("update: the line carries --update", "--update" in line, line)
        check("update: the line starts as specified",
              line.startswith("Context Guard update available ("), line)
        shutil.rmtree(home2, ignore_errors=True)
    finally:
        shutil.rmtree(home, ignore_errors=True)
        rm_tree(s["tmp"])


def test_update_checks_at_most_once_a_day():
    u = upd_module()
    s = upd_setup()
    home = make_home({})
    try:
        t0 = time.time()
        check("update-24h: first check is quiet", u.check(clone=s["clone"], home=home, now=t0) == "")
        upd_push(s, "b.txt", "second change")     # origin moves; a fetch would now see it
        again = u.check(clone=s["clone"], home=home, now=t0 + 3600)
        check("update-24h: a second check inside 24h does not fetch", again == "", again)
        st = upd_state(home)
        check("update-24h: the state file records last_check, behind, remote",
              st.get("last_check") == t0 and st.get("behind") == 0 and st.get("remote"), str(st))
        later = u.check(clone=s["clone"], home=home, now=t0 + 25 * 3600)
        check("update-24h: a check after 24h does fetch", "1 new change(s)" in later, later)
    finally:
        shutil.rmtree(home, ignore_errors=True)
        rm_tree(s["tmp"])


def test_update_offline_is_silent_fast_and_recorded():
    u = upd_module()
    s = upd_setup()
    home = make_home({})
    try:
        git_in(s["clone"], "remote", "set-url", "origin", os.path.join(s["tmp"], "nowhere.git"))
        t0 = time.time()
        line = u.check(clone=s["clone"], home=home, now=t0)
        took = time.time() - t0
        check("update-offline: gives no line", line == "", line)
        check("update-offline: well inside the 5s timeout", took < 4.5, "%.1fs" % took)
        check("update-offline: the check time is still recorded",
              upd_state(home).get("last_check") == t0)
    finally:
        shutil.rmtree(home, ignore_errors=True)
        rm_tree(s["tmp"])


def test_update_off_switch_and_no_git_folder_make_no_git_call():
    u = upd_module()
    s = upd_setup()
    home = make_home({})
    calls = []
    real = u.subprocess.Popen

    def spy(*a, **k):
        calls.append(a)
        return real(*a, **k)
    try:
        upd_push(s, "b.txt", "second change")
        u.subprocess.Popen = spy
        sw = os.path.join(home, ".claude", "context-guard")
        os.makedirs(sw)
        with open(os.path.join(sw, "no-update-check"), "w") as f:
            f.write("")
        check("update-off: the off switch gives no line", u.check(clone=s["clone"], home=home) == "")
        check("update-off: ...and made no git call", not calls, str(calls))
        os.remove(os.path.join(sw, "no-update-check"))
        zipdir = os.path.join(s["tmp"], "unzipped")
        os.makedirs(zipdir)
        check("update-zip: a folder with no .git gives no line",
              u.check(clone=zipdir, home=home) == "")
        check("update-zip: ...and made no git call", not calls, str(calls))
    finally:
        u.subprocess.Popen = real
        shutil.rmtree(home, ignore_errors=True)
        rm_tree(s["tmp"])


def test_update_a_silent_server_cannot_hang_the_check():
    """A server that accepts and never answers: git's http helper would hold the pipes open
    past the SessionStart timeout unless the whole process tree is killed."""
    import socket
    import threading
    u = upd_module()
    s = upd_setup()
    home = make_home({})
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    held = []

    def accept_and_say_nothing():
        while True:
            try:
                c, _ = srv.accept()
            except OSError:
                return
            held.append(c)
    threading.Thread(target=accept_and_say_nothing, daemon=True).start()
    try:
        git_in(s["clone"], "remote", "set-url", "origin",
               "http://127.0.0.1:%d/x.git" % srv.getsockname()[1])
        t0 = time.time()
        got = []
        th = threading.Thread(target=lambda: got.append(u.check(clone=s["clone"], home=home,
                                                                  now=t0)), daemon=True)
        th.start()
        th.join(u.FETCH_TIMEOUT + 10)    # a hung check must FAIL the suite, not freeze it
        took = time.time() - t0
        line = got[0] if got else "<still running after %.0fs>" % took
        check("update-hang: gives no line", line == "", line)
        check("update-hang: bounded by the fetch timeout", took < u.FETCH_TIMEOUT + 3.5,
              "%.1fs" % took)
        try:
            recorded = upd_state(home).get("last_check")
        except OSError:
            recorded = None
        check("update-hang: the check time is recorded, so the next chat does not retry",
              recorded == t0, str(recorded))
    finally:
        srv.close()
        for c in held:
            c.close()
        shutil.rmtree(home, ignore_errors=True)
        rm_tree(s["tmp"])


def test_update_offline_keeps_an_earlier_notice():
    u = upd_module()
    s = upd_setup()
    home = make_home({})
    try:
        t0 = time.time()
        upd_push(s, "b.txt", "second change")
        first = u.check(clone=s["clone"], home=home, now=t0)
        check("update-keep: the first check sees the change", "1 new change(s)" in first, first)
        git_in(s["clone"], "remote", "set-url", "origin", os.path.join(s["tmp"], "nowhere.git"))
        u.check(clone=s["clone"], home=home, now=t0 + 25 * 3600)      # offline, a day later
        again = u.check(clone=s["clone"], home=home, now=t0 + 26 * 3600)
        check("update-keep: an offline day does not erase the notice", "1 new change(s)" in again,
              again)
    finally:
        shutil.rmtree(home, ignore_errors=True)
        rm_tree(s["tmp"])


def test_update_leaves_a_branch_of_their_own_alone():
    u = upd_module()
    s = upd_setup()
    home = make_home({})
    try:
        git_in(s["clone"], "checkout", "-q", "-b", "mine")
        commit_file(s["clone"], "mine.txt", "x\n", "my own work")
        upd_push(s, "b.txt", "second change")
        line = u.check(clone=s["clone"], home=home, now=time.time())
        check("update-branch: no notice on a branch of their own", line == "", line)
        head = git_in(s["clone"], "rev-parse", "HEAD")
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = u.apply(clone=s["clone"], home=home)
        check("update-branch: --update refuses", rc == 1 and "REFUSING" in buf.getvalue(),
              buf.getvalue())
        check("update-branch: ...and touches nothing", git_in(s["clone"], "rev-parse", "HEAD") == head)
    finally:
        shutil.rmtree(home, ignore_errors=True)
        rm_tree(s["tmp"])


def test_update_apply_without_git_says_so_plainly():
    import contextlib
    import io
    u = upd_module()
    s = upd_setup()
    home = make_home({})
    real = u.subprocess.Popen

    def no_git(*a, **k):
        raise FileNotFoundError("git")
    buf = io.StringIO()
    try:
        u.subprocess.Popen = no_git
        with contextlib.redirect_stdout(buf):
            rc = u.apply(clone=s["clone"], home=home)
        check("update-nogit: exit code 1, no traceback", rc == 1, str(rc))
        check("update-nogit: says git was not found", "git was not found" in buf.getvalue(),
              buf.getvalue())
    finally:
        u.subprocess.Popen = real
        shutil.rmtree(home, ignore_errors=True)
        rm_tree(s["tmp"])


def test_update_a_clone_ahead_of_origin_says_nothing():
    u = upd_module()
    s = upd_setup()
    home = make_home({})
    try:
        commit_file(s["clone"], "mine.txt", "local\n", "my own work")
        line = u.check(clone=s["clone"], home=home)
        check("update-ahead: ahead and not behind shows nothing", line == "", line)
    finally:
        shutil.rmtree(home, ignore_errors=True)
        rm_tree(s["tmp"])


def rewrite_origin(s):
    """Replace origin's whole history with one unrelated commit."""
    git_in(s["work"], "checkout", "-q", "--orphan", "fresh")
    commit_file(s["work"], "only.txt", "clean slate\n", "one clean commit")
    git_in(s["work"], "push", "-q", "--force", "origin", "fresh:main")


def test_update_a_rewritten_history_says_it_was_rebuilt():
    u = upd_module()
    s = upd_setup()
    home = make_home({})
    try:
        rewrite_origin(s)
        line = u.check(clone=s["clone"], home=home)
        check("update-rewrite: says the history was rebuilt",
              "(the history was rebuilt)" in line and "--update" in line, line)
        check("update-rewrite: no change count in that line", "new change" not in line, line)
    finally:
        shutil.rmtree(home, ignore_errors=True)
        rm_tree(s["tmp"])


def test_update_apply_fast_forwards_and_lists_the_new_subjects():
    u = upd_module()
    s = upd_setup()
    home = make_home({})
    try:
        upd_push(s, "b.txt", "add the Harbor widget")
        upd_push(s, "c.txt", "fix the Nova rounding")
        rc, out = upd_apply(u, s, home)
        check("update-apply: exit 0", rc == 0, "rc=%s %s" % (rc, out))
        check("update-apply: the clone is now at origin/main",
              git_in(s["clone"], "rev-parse", "HEAD") == git_in(s["origin"], "rev-parse", "main"))
        check("update-apply: lists both new subjects",
              "  - add the Harbor widget" in out and "  - fix the Nova rounding" in out, out)
        check("update-apply: says to restart", "Restart Claude Code, or open a new chat, for the update to load." in out, out)
        check("update-apply: re-ran the hook merge (settings.json now exists)",
              os.path.exists(os.path.join(home, ".claude", "settings.json")), out)
        st = upd_state(home)
        check("update-apply: the notice state is reset", st.get("behind") == 0, str(st))
        check("update-apply: ...so check() is now quiet", u.check(clone=s["clone"], home=home) == "")
    finally:
        shutil.rmtree(home, ignore_errors=True)
        rm_tree(s["tmp"])


def test_update_apply_refuses_a_dirty_tree_and_changes_nothing():
    u = upd_module()
    s = upd_setup()
    home = make_home({})
    try:
        upd_push(s, "b.txt", "second change")
        with open(os.path.join(s["clone"], "a.txt"), "w", encoding="utf-8") as f:
            f.write("my local edit\n")
        before = tree_fingerprint(s["clone"])
        refs_before = git_in(s["clone"], "for-each-ref")
        rc, out = upd_apply(u, s, home)
        check("update-dirty: exit 1", rc == 1, "rc=%s" % rc)
        check("update-dirty: names the changed file", "a.txt" in out, out)
        check("update-dirty: says commit or copy them", "commit or copy them somewhere, then run --update again" in out, out)
        check("update-dirty: HEAD and every file are identical afterwards",
              tree_fingerprint(s["clone"]) == before)
        check("update-dirty: not even the remote refs moved",
              git_in(s["clone"], "for-each-ref") == refs_before)
    finally:
        shutil.rmtree(home, ignore_errors=True)
        rm_tree(s["tmp"])


def test_update_apply_on_a_rewrite_backs_up_then_resets():
    u = upd_module()
    s = upd_setup()
    home = make_home({})
    try:
        commit_file(s["clone"], "mine.txt", "local\n", "my own work")
        old_head = git_in(s["clone"], "rev-parse", "HEAD").strip()
        rewrite_origin(s)
        rc, out = upd_apply(u, s, home)
        check("update-rewrite-apply: exit 0", rc == 0, "rc=%s %s" % (rc, out))
        branches = [b.strip().lstrip("* ") for b in git_in(s["clone"], "branch", "--list").splitlines()]
        backups = [b for b in branches if re.match(r"context-guard-backup-\d{8}-\d{6}$", b)]
        check("update-rewrite-apply: one timestamped backup branch", len(backups) == 1, str(branches))
        if backups:
            check("update-rewrite-apply: the backup is at the OLD head",
                  git_in(s["clone"], "rev-parse", backups[0]).strip() == old_head)
            check("update-rewrite-apply: names the backup and how to return",
                  backups[0] in out and "git checkout " + backups[0] in out, out)
        check("update-rewrite-apply: HEAD is now origin/main",
              git_in(s["clone"], "rev-parse", "HEAD") == git_in(s["origin"], "rev-parse", "main"))
        check("update-rewrite-apply: the new file is there and the old commit's file is gone",
              os.path.exists(os.path.join(s["clone"], "only.txt"))
              and not os.path.exists(os.path.join(s["clone"], "mine.txt")))
    finally:
        shutil.rmtree(home, ignore_errors=True)
        rm_tree(s["tmp"])


def test_update_dry_run_changes_nothing_not_even_the_remote_ref():
    u = upd_module()
    s = upd_setup()
    home = make_home({})
    try:
        upd_push(s, "b.txt", "second change")
        before = tree_fingerprint(s["clone"])
        refs_before = git_in(s["clone"], "for-each-ref")
        rc, out = upd_apply(u, s, home, dry_run=True)
        check("update-dry: exit 0", rc == 0, "rc=%s %s" % (rc, out))
        check("update-dry: says it would do something", "would" in out.lower(), out)
        check("update-dry: HEAD and files unchanged", tree_fingerprint(s["clone"]) == before)
        check("update-dry: refs/remotes/origin/main did not move",
              git_in(s["clone"], "for-each-ref") == refs_before)
        check("update-dry: no state written", not os.path.exists(
            os.path.join(home, ".claude", "context-guard", "update-check.json")))
        check("update-dry: no settings.json written", not os.path.exists(
            os.path.join(home, ".claude", "settings.json")))
    finally:
        shutil.rmtree(home, ignore_errors=True)
        rm_tree(s["tmp"])


def test_update_apply_when_current_says_so_and_a_zip_says_how():
    u = upd_module()
    s = upd_setup()
    home = make_home({})
    try:
        rc, out = upd_apply(u, s, home)
        check("update-current: exit 0", rc == 0, out)
        check("update-current: says already up to date",
              "Context Guard is already up to date" in out, out)
        zipdir = os.path.join(s["tmp"], "unzipped")
        os.makedirs(zipdir)
        import contextlib
        import io as _io
        buf = _io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = u.apply(clone=zipdir, home=home)
        check("update-zip-apply: exit 1 and explains downloading again",
              rc == 1 and "download" in buf.getvalue().lower(), buf.getvalue())
    finally:
        shutil.rmtree(home, ignore_errors=True)
        rm_tree(s["tmp"])


def test_update_apply_fails_cleanly_when_fetch_fails():
    u = upd_module()
    s = upd_setup()
    home = make_home({})
    try:
        git_in(s["clone"], "remote", "set-url", "origin", os.path.join(s["tmp"], "nowhere.git"))
        rc, out = upd_apply(u, s, home)
        check("update-fetch-fail: exit 1 with a plain sentence", rc == 1 and "fetch" in out.lower(), out)
    finally:
        shutil.rmtree(home, ignore_errors=True)
        rm_tree(s["tmp"])


def run_alert_in_clone(s, home, with_update=True):
    """Run a COPY of audit.py (and update.py) sitting in the clone, from another cwd, the way
    the hook does: by absolute path."""
    shutil.copy(AUDIT, os.path.join(s["clone"], "audit.py"))
    if with_update:
        shutil.copy(os.path.join(HERE_DIR, "update.py"), os.path.join(s["clone"], "update.py"))
    return subprocess.run([sys.executable, os.path.join(s["clone"], "audit.py"), "--alert"],
                          capture_output=True, text=True, env=child_env(home),
                          cwd=tempfile.gettempdir(), timeout=60)


def one_json(proc):
    out = (proc.stdout or "").strip()
    try:
        return json.loads(out)
    except Exception:
        return None


def write_big_session(home):
    p = os.path.join(home, ".claude", "projects", KEY, "bigbigbig.jsonl")
    with open(p, "w", encoding="utf-8") as f:
        for _ in range(22):
            f.write(json.dumps({"timestamp": "2026-09-01T00:00:00Z", "pad": "x" * 1_000_000}) + "\n")


def test_alert_carries_the_update_line_when_nothing_is_flagged():
    s = upd_setup()
    home = make_home({})
    try:
        upd_push(s, "b.txt", "second change")
        p = run_alert_in_clone(s, home)
        expect_clean(p, "alert-update-quiet")
        d = one_json(p)
        check("alert-update-quiet: stdout is exactly one JSON object", isinstance(d, dict), p.stdout)
        msg = (d or {}).get("systemMessage", "")
        check("alert-update-quiet: systemMessage carries the update line",
              "Context Guard update available (1 new change(s))" in msg and "--update" in msg, msg)
        ctx = ((d or {}).get("hookSpecificOutput") or {}).get("additionalContext", "")
        check("alert-update-quiet: the model is told nothing", ctx == "", ctx)
    finally:
        shutil.rmtree(home, ignore_errors=True)
        rm_tree(s["tmp"])


def test_alert_appends_the_update_line_when_sessions_are_flagged():
    s = upd_setup()
    home = make_home({})
    try:
        write_big_session(home)
        upd_push(s, "b.txt", "second change")
        p = run_alert_in_clone(s, home)
        expect_clean(p, "alert-update-flagged")
        d = one_json(p)
        check("alert-update-flagged: exactly one JSON object", isinstance(d, dict), p.stdout)
        msg = (d or {}).get("systemMessage", "")
        check("alert-update-flagged: the audit message is still there", "bloated session" in msg, msg)
        check("alert-update-flagged: the update line is appended", "update available" in msg, msg)
        ctx = ((d or {}).get("hookSpecificOutput") or {}).get("additionalContext", "")
        check("alert-update-flagged: the model still gets the audit", "CONTEXT-COST AUDIT" in ctx, ctx[:100])
        check("alert-update-flagged: the model does not get the update line", "update available" not in ctx)
    finally:
        shutil.rmtree(home, ignore_errors=True)
        rm_tree(s["tmp"])


def test_alert_with_nothing_to_say_still_prints_nothing():
    s = upd_setup()
    home = make_home({})
    try:
        p = run_alert_in_clone(s, home)
        expect_clean(p, "alert-update-none")
        check("alert-update-none: no stdout when up to date and nothing flagged",
              (p.stdout or "").strip() == "", p.stdout)
    finally:
        shutil.rmtree(home, ignore_errors=True)
        rm_tree(s["tmp"])


def test_alert_survives_an_old_checkout_without_update_py():
    s = upd_setup()
    home = make_home({})
    try:
        write_big_session(home)
        p = run_alert_in_clone(s, home, with_update=False)
        expect_clean(p, "alert-no-update-py")
        d = one_json(p)
        check("alert-no-update-py: still one JSON object with the audit",
              isinstance(d, dict) and "bloated session" in d.get("systemMessage", ""), p.stdout)
        check("alert-no-update-py: no update line", "update available" not in (d or {}).get("systemMessage", ""))
    finally:
        shutil.rmtree(home, ignore_errors=True)
        rm_tree(s["tmp"])


def test_install_update_flag_is_wired_and_plain_install_is_unchanged():
    s = upd_setup()
    home = make_home({})
    try:
        # install.py is already in the clone (upd_setup committed it); update.py is not
        shutil.copy(os.path.join(HERE_DIR, "update.py"), os.path.join(s["clone"], "update.py"))
        with open(os.path.join(s["clone"], ".git", "info", "exclude"), "a") as f:
            f.write("update.py\n__pycache__/\n")     # keep the tree clean
        p = subprocess.run([sys.executable, os.path.join(s["clone"], "install.py"), "--home", home,
                            "--update", "--dry-run"], capture_output=True, text=True, timeout=60)
        expect_clean(p, "install-update-flag")
        check("install-update-flag: the flag is accepted, exit 0", p.returncode == 0, p.stdout + p.stderr)
        check("install-update-flag: dry-run writes no settings.json",
              not os.path.exists(settings_file(home)))
        p2 = run_install(home, "--dry-run")
        check("install-update-flag: plain install --dry-run still works", p2.returncode == 0, p2.stderr)
    finally:
        shutil.rmtree(home, ignore_errors=True)
        rm_tree(s["tmp"])


# ---------------------------------------------------------------- folderless chat redirect
SCRATCH_DIRS = (r"C:\Users\someone\AppData\Roaming\Claude\scratch-workspaces"
                r"\11111111-aaaa\22222222-bbbb\scratch-2026-09-30-8222df")
SCRATCH_KEY = re.sub(r"[^A-Za-z0-9]", "-", SCRATCH_DIRS)
BOILERPLATE = ("<system-reminder>\nNo folder is selected. This session runs in a scratch "
               "workspace, not a project. Claude cannot see your directory; make a request "
               "and choose a folder.\n</system-reminder>")


def scratch_home():
    """A throwaway home with a real project folder (dir), its key, and a note waiting
    under that key labelled `Harbor -3 (30 Sep)`."""
    home = make_home({})
    proj = os.path.join(home, "work", "harbor-app")
    os.makedirs(proj)
    key = re.sub(r"[^A-Za-z0-9]", "-", proj)
    note = os.path.join(home, ".claude", "handoff", key + ".ab12cd34.md")
    with open(note, "w", encoding="utf-8") as f:
        f.write("HANDOFF LABEL: Harbor -3 (30 Sep)\n\n# Handoff\n\nbody-HARBOR3\n")
    return home, proj, key, note


def scratch_call(home, cwd, prompt, tp=None):
    tp = tp or os.path.join(home, ".claude", "projects", SCRATCH_KEY, "s1.jsonl")
    code = ("print(repr(guard.scratch_redirect(%r, %r, %r)))" % (tp, cwd, prompt))
    return guard_call(home, code)


def write_manifest(home, projects):
    p = os.path.join(home, ".claude", "context-guard", "memory-manifest.json")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"projects": projects}, f)


def test_a_folderless_chat_naming_a_saved_thread_is_told_to_move():
    home, proj, key, note = scratch_home()
    try:
        write_manifest(home, {"Harbor": {"dir": proj, "threads": ["harbor"]}})
        p = scratch_call(home, SCRATCH_DIRS, "Harbor -3 (30 Sep)")
        out = p.stdout
        check("scratch/a: ran cleanly", p.returncode == 0, p.stderr[-300:])
        check("scratch/a: names change_directory", "change_directory" in out, out[:200])
        check("scratch/a: names the resolved folder", proj.replace("\\", "\\\\") in out
              or proj in out, out[:300])
        check("scratch/a: leaves the note where it is", os.path.exists(note))
        check("scratch/a: no rename order", "set_session_title" not in out)
        check("scratch/a: never carries the note body", "body-HARBOR3" not in out)
        # the hook itself: fires the redirect and does NOT take the handoff
        payload = {"session_id": "scr-a", "cwd": SCRATCH_DIRS, "prompt": "Harbor -3 (30 Sep)",
                   "transcript_path": os.path.join(home, ".claude", "projects", SCRATCH_KEY,
                                                   "scr-a.jsonl"),
                   "hook_event_name": "UserPromptSubmit"}
        h = subprocess.run([sys.executable, GUARD, "--size"], input=json.dumps(payload),
                           capture_output=True, text=True, env=child_env(home))
        ctx = context_of(h)
        check("scratch/a: the hook hands the chat the move", "change_directory" in ctx,
              (h.stdout + h.stderr)[:300])
        st = os.path.join(home, ".claude", "context-guard", "state", "scr-a.json")
        stt = ""
        for root, _d, files in os.walk(os.path.join(home, ".claude", "context-guard")):
            for fn in files:
                if fn.startswith("scr-a") or fn == "scr-a.json":
                    with open(os.path.join(root, fn), encoding="utf-8", errors="replace") as f:
                        stt += f.read()
        check("scratch/a: took_handoff is not set", '"took_handoff": true' not in stt, stt[:200])
        check("scratch/a: the note is still there after the hook", os.path.exists(note))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_a_big_folderless_chat_is_not_told_to_move():
    """Only a FRESH chat is redirected, the same gate as the pickup. Measured 30 Sep: the
    real folderless chat worked for 20 minutes with the thread's word in most messages,
    so an ungated redirect would have ordered a move on every one of them."""
    home, proj, key, note = scratch_home()
    try:
        write_manifest(home, {"Harbor": {"dir": proj, "threads": ["harbor"]}})
        tp = os.path.join(home, ".claude", "projects", SCRATCH_KEY, "scr-g.jsonl")
        os.makedirs(os.path.dirname(tp), exist_ok=True)
        with open(tp, "w", encoding="utf-8") as f:
            f.write(json.dumps({"cwd": SCRATCH_DIRS, "message": {"usage": {
                "cache_read_input_tokens": 200_000, "cache_creation_input_tokens": 0}}}) + "\n")
        payload = {"session_id": "scr-g", "cwd": SCRATCH_DIRS,
                   "prompt": "the harbor photos look off", "transcript_path": tp,
                   "hook_event_name": "UserPromptSubmit"}
        h = subprocess.run([sys.executable, GUARD, "--size"], input=json.dumps(payload),
                           capture_output=True, text=True, env=child_env(home))
        check("scratch/g: a 200k chat is not told to move", "change_directory" not in h.stdout,
              h.stdout[:300])
        check("scratch/g: and the note stays put", os.path.exists(note))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_a_folderless_chat_naming_nothing_is_untouched():
    home, proj, key, note = scratch_home()
    try:
        write_manifest(home, {"Harbor": {"dir": proj}})
        p = scratch_call(home, SCRATCH_DIRS, "what is the weather like")
        check("scratch/b: no label -> None", p.stdout.strip() == "None", p.stdout + p.stderr[-200:])
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_an_ordinary_folder_gets_no_redirect():
    home, proj, key, note = scratch_home()
    try:
        write_manifest(home, {"Harbor": {"dir": proj}})
        tp = os.path.join(home, ".claude", "projects", KEY, "s2.jsonl")
        p = scratch_call(home, "D:/Claude", "Harbor -3 (30 Sep)", tp)
        check("scratch/c: non-scratch cwd -> None", p.stdout.strip() == "None",
              p.stdout + p.stderr[-200:])
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_apps_boilerplate_never_names_a_label():
    home, proj, key, note = scratch_home()
    try:
        write_manifest(home, {"Ws": {"dir": proj}})
        with open(note, "w", encoding="utf-8") as f:
            f.write("HANDOFF LABEL: Workspace Tools -2 (30 Sep)\n\n# H\n\nbody\n")
        bare = BOILERPLATE + "\n\n | "
        p1 = scratch_call(home, SCRATCH_DIRS, bare)
        p2 = scratch_call(home, SCRATCH_DIRS, bare + "Workspace Tools -2 (30 Sep)")
        check("scratch/d: boilerplate alone -> None", p1.stdout.strip() == "None",
              p1.stdout + p1.stderr[-200:])
        check("scratch/d: boilerplate + the label -> redirect", "change_directory" in p2.stdout,
              p2.stdout + p2.stderr[-200:])
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_folder_is_read_from_a_transcripts_cwd_when_the_manifest_lacks_it():
    home, proj, key, note = scratch_home()
    try:
        write_manifest(home, {})
        pd = os.path.join(home, ".claude", "projects", key)
        os.makedirs(pd)
        with open(os.path.join(pd, "old.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"type": "summary"}) + "\n")
            f.write(json.dumps({"cwd": proj, "type": "user"}) + "\n")
        p = scratch_call(home, SCRATCH_DIRS, "Harbor -3 (30 Sep)")
        check("scratch/e: resolved from the transcript cwd",
              "change_directory" in p.stdout and proj in p.stdout.replace("\\\\", "\\"),
              p.stdout + p.stderr[-200:])
        os.remove(os.path.join(pd, "old.jsonl"))
        p = scratch_call(home, SCRATCH_DIRS, "Harbor -3 (30 Sep)")
        check("scratch/e: unresolved -> the fallback line, no move, no path",
              "change_directory" not in p.stdout and proj not in p.stdout
              and "new chat" in p.stdout, p.stdout + p.stderr[-200:])
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_a_note_under_a_scratch_key_is_never_a_redirect_target():
    home, proj, key, note = scratch_home()
    try:
        write_manifest(home, {"Harbor": {"dir": proj}})
        os.remove(note)
        other = os.path.join(home, ".claude", "handoff", SCRATCH_KEY + ".ff00ee11.md")
        with open(other, "w", encoding="utf-8") as f:
            f.write("HANDOFF LABEL: Harbor -3 (30 Sep)\n\n# H\n\nbody\n")
        p = scratch_call(home, SCRATCH_DIRS, "Harbor -3 (30 Sep)")
        check("scratch/f: scratch-key note ignored", p.stdout.strip() == "None",
              p.stdout + p.stderr[-200:])
        check("scratch/f: and left in place", os.path.exists(other))
    finally:
        shutil.rmtree(home, ignore_errors=True)


# ------------------------------------------------------------------ memory keywords
def mem_text(slug, desc, kws=None, eol="\n"):
    """A memory file the way Claude Code writes one, with an optional keywords line."""
    lines = ["---", "name: " + slug, 'description: "' + desc + '"']
    if kws is not None:
        lines.append("keywords: " + ", ".join(kws))
    lines += ["metadata:", "  type: project", "---", "", "body of " + slug]
    return eol.join(lines) + eol


def kw_home(memories, lists=None, manifest=None, claude_md=None):
    """A throwaway home holding memory files. memories: {(key, slug): text};
    lists: {key: MEMORY.md text}; manifest: the memory-manifest.json dict."""
    home = make_home({})
    for (key, slug), body in memories.items():
        d = os.path.join(home, ".claude", "projects", key, "memory")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, slug + ".md"), "w", encoding="utf-8", newline="") as f:
            f.write(body)
    for key, text in (lists or {}).items():
        d = os.path.join(home, ".claude", "projects", key, "memory")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "MEMORY.md"), "w", encoding="utf-8") as f:
            f.write(text)
    state = os.path.join(home, ".claude", "context-guard")
    os.makedirs(state, exist_ok=True)
    if manifest is not None:
        with open(os.path.join(state, "memory-manifest.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f)
    if claude_md is not None:
        with open(os.path.join(home, ".claude", "CLAUDE.md"), "w", encoding="utf-8") as f:
            f.write(claude_md)
    return home


def kw_json(home, code):
    """Run `code` (which must print one JSON value) inside guard, and parse it."""
    p = guard_call(home, code)
    try:
        return json.loads(p.stdout.strip().splitlines()[-1])
    except Exception:
        return {"_error": (p.stderr or p.stdout)[-400:]}


def test_the_keywords_reader_reads_the_line_and_falls_back_to_the_summary():
    """Spec 2026-10-02 section 1: a keywords line is read as given; a file without one
    still works, from its slug and description; CRLF and a BOM change nothing."""
    home = kw_home({
        (KEY, "android-adb"): mem_text("android-adb", "adb on the phone",
                                       ["adb", "Android Phone", "usb debugging"]),
        (KEY, "crlf-one"): mem_text("crlf-one", "x", ["alpha", "beta"], eol="\r\n"),
        (KEY, "telegram-bot-traps"): mem_text("telegram-bot-traps",
                                              "Telegram polling and webhook conflict"),
        (KEY, "no-front"): "# just a heading\n",
    })
    try:
        p = os.path.join(home, ".claude", "projects", KEY, "memory")
        with open(os.path.join(p, "bom-one.md"), "w", encoding="utf-8-sig") as f:
            f.write(mem_text("bom-one", "y", ["gamma"]))
        r = kw_json(home, "import json, os\n"
                    "p = %r\n"
                    "print(json.dumps({n: guard.memory_keywords(os.path.join(p, n + '.md'))\n"
                    "  for n in ['android-adb','crlf-one','telegram-bot-traps','no-front','bom-one']}))"
                    % p)
        a = r.get("android-adb") or {}
        check("kw-read: keywords are read and lowercased",
              a.get("keywords") == ["adb", "android phone", "usb debugging"], repr(a))
        check("kw-read: a written line is not derived", a.get("derived") is False, repr(a))
        check("kw-read: the description is read without its quotes",
              a.get("description") == "adb on the phone", repr(a))
        check("kw-read: CRLF changes nothing",
              (r.get("crlf-one") or {}).get("keywords") == ["alpha", "beta"], repr(r.get("crlf-one")))
        check("kw-read: a BOM changes nothing",
              (r.get("bom-one") or {}).get("keywords") == ["gamma"], repr(r.get("bom-one")))
        t = r.get("telegram-bot-traps") or {}
        check("kw-read: no line -> derived from slug and description",
              t.get("derived") is True and "telegram" in t.get("keywords", [])
              and "webhook" in t.get("keywords", []), repr(t))
        check("kw-read: derived words are 4+ chars, at most 8",
              all(len(w) >= 4 for w in t.get("keywords", [])) and len(t.get("keywords", [])) <= 8,
              repr(t))
        check("kw-read: no frontmatter -> None", r.get("no-front") is None, repr(r.get("no-front")))
    finally:
        rm_tree(home)


KW_MANIFEST = {"projects": {
    "droid": {"dir": "D:\\Demo\\Droid", "threads": ["droid"], "also": [], "files": []},
    "tides": {"dir": "D:\\Demo\\Tides", "threads": ["tides"], "also": [],
              "files": ["tide-tables"]}}}
DROID_KEY = "D--Demo-Droid"


def test_the_catalogue_prefers_the_shared_copy_and_names_each_project():
    """Spec section 2: one entry per slug, the shared copy wins over a spare, and the
    project comes from the manifest, then the one list that claims it, then 'general'."""
    home = kw_home(
        {(KEY, "android-adb"): mem_text("android-adb", "shared copy", ["adb"]),
         (DROID_KEY, "android-adb"): mem_text("android-adb", "SPARE copy", ["adb"]),
         (KEY, "tide-tables"): mem_text("tide-tables", "t", ["tide"]),
         (KEY, "loose-lesson"): mem_text("loose-lesson", "l", ["regex"])},
        lists={DROID_KEY: "# Memory Index\n- [Android adb](android-adb.md) - x\n"},
        manifest=KW_MANIFEST)
    try:
        r = kw_json(home, "import json\nprint(json.dumps({e['slug']: e for e in "
                          "guard.memory_catalogue()}))")
        a = r.get("android-adb") or {}
        check("kw-cat: one entry per slug", sorted(r) == ["android-adb", "loose-lesson",
                                                          "tide-tables"], repr(sorted(r)))
        check("kw-cat: the shared copy wins", a.get("description") == "shared copy", repr(a))
        check("kw-cat: title from the list line", a.get("title") == "Android adb", repr(a))
        check("kw-cat: project from the one list that claims it",
              a.get("project") == "droid", repr(a))
        check("kw-cat: project from the manifest's files",
              (r.get("tide-tables") or {}).get("project") == "tides", repr(r.get("tide-tables")))
        l = r.get("loose-lesson") or {}
        check("kw-cat: unclaimed -> general, title from the slug",
              l.get("project") == "general" and l.get("title") == "loose lesson", repr(l))
    finally:
        rm_tree(home)


def test_the_catalogue_cache_rereads_only_a_changed_file_and_survives_corruption():
    """Spec section 2: the prompt hook must not pay for every file on every prompt."""
    home = kw_home({(KEY, "one"): mem_text("one", "a", ["alpha"]),
                    (KEY, "two"): mem_text("two", "b", ["beta"])})
    count = ("import json\nn = []\nreal = guard.memory_keywords\n"
             "guard.memory_keywords = lambda p: (n.append(p), real(p))[1]\n"
             "guard.memory_catalogue()\nprint(json.dumps(len(n)))")
    try:
        check("kw-cache: a cold build reads every file", kw_json(home, count) == 2)
        check("kw-cache: a warm build reads none", kw_json(home, count) == 0)
        p = os.path.join(home, ".claude", "projects", KEY, "memory", "two.md")
        with open(p, "a", encoding="utf-8") as f:
            f.write("more\n")
        check("kw-cache: a changed file is re-read alone", kw_json(home, count) == 1)
        with open(os.path.join(home, ".claude", "context-guard", "memory-catalogue.json"),
                  "w", encoding="utf-8") as f:
            f.write("{not json")
        check("kw-cache: a corrupt cache is rebuilt", kw_json(home, count) == 2)
        # Measured 2 Oct 2026: a reader fix shipped and a memory whose file had not changed
        # kept the OLD reader's answer, because the sig still matched. A cache entry written
        # by another reader version is a stale answer, not a saving.
        cpath = os.path.join(home, ".claude", "context-guard", "memory-catalogue.json")
        with open(cpath, encoding="utf-8") as f:
            cache = json.load(f)
        for c in cache.values():
            c.pop("v", None)
            c["kw"]["keywords"] = ["stale"]
        with open(cpath, "w", encoding="utf-8") as f:
            json.dump(cache, f)
        check("kw-cache: an older reader's cache is re-read", kw_json(home, count) == 2)
        r = kw_json(home, "import json\nprint(json.dumps(sorted(k for e in "
                          "guard.memory_catalogue() for k in e['keywords'])))")
        check("kw-cache: ...and gives the real keywords", r == ["alpha", "beta"], repr(r))
    finally:
        rm_tree(home)


SCORE_MEMS = {
    (KEY, "adb-setup"): mem_text("adb-setup", "a", ["adb", "android phone"]),
    (KEY, "gradle-wsl"): mem_text("gradle-wsl", "g", ["gradle build", "android phone"]),
    (KEY, "emulator-notes"): mem_text("emulator-notes", "e", ["android phone", "emulator"]),
    (KEY, "telegram-bot-traps"): mem_text("telegram-bot-traps",
                                          "Telegram polling and webhook conflict"),
    (KEY, "bank-bill"): mem_text("bank-bill", "b", ["فاتورة"]),
}


def scored(home, text, strict=True):
    return kw_json(home, "import json\nprint(json.dumps([e['slug'] for _s, _m, e, _h in "
                         "guard.score_memories(%r, guard.memory_catalogue(), set(), %r)]))"
                   % (text, strict))


def test_the_scorer_asks_for_two_keywords_or_one_rare_one():
    """Spec section 3: a common keyword alone never qualifies, a rare one does, a phrase
    needs its words in order, derived keywords need two, and Arabic matches Arabic."""
    home = kw_home(SCORE_MEMS)
    try:
        common = scored(home, "my android phone is slow")
        check("kw-score: one common keyword is not enough", common == [], repr(common))
        check("kw-score: one rare keyword is enough",
              scored(home, "can adb see the device") == ["adb-setup"])
        check("kw-score: two keywords qualify",
              scored(home, "android phone and the emulator crashed") == ["emulator-notes"])
        check("kw-score: word order matters for the prompt hook (strict)",
              scored(home, "phone android is slow", strict=True) == [])
        check("kw-score: a derived keyword alone is not enough",
              scored(home, "telegram is down again") == [])
        check("kw-score: two derived keywords qualify",
              scored(home, "telegram webhook broke again") == ["telegram-bot-traps"])
        check("kw-score: Arabic matches Arabic",
              scored(home, "اين فاتورة الشهر الماضي") == ["bank-bill"])
        check("kw-score: loose mode takes one common keyword",
              len(scored(home, "my android phone is slow", strict=False)) == 3)
    finally:
        rm_tree(home)


def run_recall(home, *words):
    env = child_env(home)
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run([sys.executable, GUARD, "--recall"] + list(words),
                          capture_output=True, text=True, encoding="utf-8", env=env)


def test_recall_lists_the_best_five_and_says_so_when_nothing_matches():
    """Spec section 4: the chat's own search - loose, top 5, title, project, path, keywords."""
    mems = {(KEY, "m%d" % i): mem_text("m%d" % i, "d", ["widget", "w%d" % i]) for i in range(7)}
    home = kw_home(mems)
    try:
        p = run_recall(home, "widget")
        expect_clean(p, "kw-recall")
        heads = [l for l in p.stdout.splitlines() if l and not l.startswith(" ")]
        check("kw-recall: at most five results", len(heads) == 5, p.stdout[:300])
        check("kw-recall: each result names its path and keywords",
              ".md" in heads[0] and "  keywords: widget" in p.stdout, p.stdout[:300])
        q = run_recall(home, "nothing", "here")
        check("kw-recall: the empty case says so in one line",
              q.stdout.strip() == "No memory matches: nothing here", q.stdout)
    finally:
        rm_tree(home)


HINT_MEMS = {
    (KEY, "android-adb"): mem_text("android-adb", "adb on the phone", ["adb", "usb debugging"]),
    (KEY, "own-thing"): mem_text("own-thing", "o", ["kettle", "teapot"]),
    (KEY, "rule-thing"): mem_text("rule-thing", "r", ["spoon", "fork"]),
    (KEY, "label-thing"): mem_text("label-thing", "l", ["anchor", "rope"]),
}
HINT_LISTS = {KEY: "# Memory Index\n- [Own](own-thing.md) - o\n",
              DROID_KEY: "# Memory Index\n- [Android adb](android-adb.md) - x\n"}


def test_the_hint_names_another_projects_memory_once():
    """Spec section 3, the whole path: his prompt names it, the chat gets one line with
    the project and the path, the search tip rides the first hint only, never twice."""
    home = kw_home({**HINT_MEMS, (KEY, "sprocket-two"): mem_text(
        "sprocket-two", "s", ["sprocket", "flange"])}, lists=HINT_LISTS, manifest=KW_MANIFEST)
    try:
        p = run(home, "hint1", "why does adb not see my phone today")
        expect_clean(p, "kw-hint")
        c = context_of(p)
        check("kw-hint: the matching memory is named with its project",
              "Android adb (droid)" in c and "android-adb.md" in c, c[:400])
        check("kw-hint: the first hint carries the search tip", "--recall" in c, c[:400])
        check("kw-hint: within the cap", len(c) <= guard_constant("HINT_CHARS"), str(len(c)))
        q = run(home, "hint1", "adb still does not see my phone")
        check("kw-hint: the same memory is never hinted twice",
              "android-adb.md" not in context_of(q), context_of(q)[:300])
        s = context_of(run(home, "hint1", "the sprocket and the flange broke again today"))
        check("kw-hint: a second hint names its memory but carries no search tip",
              "sprocket-two.md" in s and "--recall" not in s, s[:300])
    finally:
        rm_tree(home)


def test_the_hint_skips_what_the_chat_already_has():
    """The chat's own list, CLAUDE.md's [[rules]] and the label-fetched list are already in
    front of it; hinting them is the bloat this tool exists to remove."""
    home = kw_home(HINT_MEMS, lists=HINT_LISTS, manifest=KW_MANIFEST,
                   claude_md="- a rule `[[rule-thing]]`\n")
    try:
        lst = os.path.join(home, "label-list.md")   # outside every memory folder
        with open(lst, "w", encoding="utf-8") as f:
            f.write("- [L](label-thing.md) - l\n")
        guard_call(home, "guard.save_state('hint2', {'label_list': %r})" % lst)
        for words, what in (("the kettle and the teapot again", "own list"),
                            ("a spoon and a fork please now", "CLAUDE.md rule"),
                            ("the anchor and the rope today", "label list")):
            c = context_of(run(home, "hint2", words))
            check("kw-hint-skip: " + what, "MEMORIES FROM OTHER PROJECTS" not in c, c[:200])
    finally:
        rm_tree(home)


def test_the_hint_stays_quiet_when_it_should():
    """A short prompt, the off switch and a busy turn get nothing, and nothing is recorded."""
    home = kw_home(HINT_MEMS, lists=HINT_LISTS, manifest=KW_MANIFEST)
    try:
        check("kw-hint-quiet: three words or fewer",
              "android-adb" not in context_of(run(home, "hint3", "adb help now")))
        r = kw_json(home, "import json\n"
                    "o = {'hookSpecificOutput': {'additionalContext': 'busy'}}\n"
                    "d = {'session_id': 'hint3', 'prompt': 'why does adb not see my phone'}\n"
                    "r = guard.memory_hints(o, d, 'hint3')\n"
                    "print(json.dumps([r['hookSpecificOutput']['additionalContext'],\n"
                    "  guard.load_state('hint3').get('memory_hints')]))")
        check("kw-hint-quiet: a busy turn is returned unchanged and nothing recorded",
              r == ["busy", None], repr(r))
        open(os.path.join(home, ".claude", "context-guard", "no-memory-hints"), "w").close()
        check("kw-hint-quiet: the off switch",
              "android-adb" not in context_of(run(home, "hint4", "why does adb not see my phone")))
    finally:
        rm_tree(home)


def test_the_hint_shows_at_most_three():
    mems = {(KEY, "w%d" % i): mem_text("w%d" % i, "d" * 120, ["sprocket", "flange"])
            for i in range(6)}
    home = kw_home(mems)
    try:
        c = context_of(run(home, "hint5", "the sprocket and the flange broke"))
        n = sum(1 for l in c.splitlines() if l.startswith("- "))
        check("kw-hint-cap: at most three memories", 1 <= n <= 3, c[:400])
        check("kw-hint-cap: under the character cap",
              len(c) <= guard_constant("HINT_CHARS"), str(len(c)))
    finally:
        rm_tree(home)


def write_mem_writes(home, sid, paths, tool="Write", mention=()):
    """A transcript in which this chat used `tool` on each of `paths`, and only MENTIONED
    each of `mention` in a message."""
    p = os.path.join(home, ".claude", "projects", KEY, sid + ".jsonl")
    started = time.time() - 600
    with open(p, "w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "user", "timestamp": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
            "message": {"role": "user", "content": "save it " + " ".join(mention)}}) + chr(10))
        for fp in paths:
            f.write(json.dumps({"type": "assistant", "message": {"role": "assistant",
                    "content": [{"type": "tool_use", "name": tool,
                                 "input": {"file_path": fp, "content": "x"}}]}}) + chr(10))
    return p


def test_a_memory_saved_without_keywords_is_sent_back_once():
    """Spec section 5: Claude Code's own memory format has no keywords field, so asking is
    not enough - the Stop hook checks what this chat actually wrote."""
    home = kw_home({(KEY, "bare-one"): mem_text("bare-one", "no keywords here"),
                    (KEY, "good-one"): mem_text("good-one", "fine", ["alpha", "beta"]),
                    (KEY, "said-only"): mem_text("said-only", "only mentioned")})
    try:
        md = os.path.join(home, ".claude", "projects", KEY, "memory")
        write_mem_writes(home, "kwstop", [os.path.join(md, "bare-one.md"),
                                          os.path.join(md, "good-one.md"),
                                          os.path.join(md, "MEMORY.md")],
                         mention=[os.path.join(md, "said-only.md")])
        p = run_stop(home, "kwstop")
        expect_clean(p, "kw-stop")
        why = blocked(p)
        check("kw-stop: blocked", why != "", (p.stdout or "")[:200])
        check("kw-stop: names the bare memory", "bare-one.md" in why, why[:300])
        check("kw-stop: not the good one, MEMORY.md or a mere mention",
              "good-one.md" not in why and "MEMORY.md" not in why and "said-only" not in why,
              why[:300])
        check("kw-stop: names its off switch", "no-keywords-check" in why, why[-200:])
        check("kw-stop: never twice in a row", blocked(run_stop(home, "kwstop", active=True)) == "")
        check("kw-stop: never twice for the same files", blocked(run_stop(home, "kwstop")) == "")
    finally:
        rm_tree(home)


def test_the_keywords_check_has_an_off_switch_and_a_cap():
    home = kw_home({(KEY, "bare-two"): mem_text("bare-two", "x")})
    try:
        md = os.path.join(home, ".claude", "projects", KEY, "memory")
        bare = os.path.join(md, "bare-two.md")
        write_mem_writes(home, "kwcap", [bare], tool="Edit")
        n = 0
        for i in range(4):
            os.utime(bare, (time.time() + i + 1, time.time() + i + 1))  # a new version each time
            n += 1 if blocked(run_stop(home, "kwcap")) else 0
        check("kw-stop-cap: at most NAG_MAX blocks", n == guard_constant("NAG_MAX"), str(n))
        write_mem_writes(home, "kwoff", [bare])
        open(os.path.join(home, ".claude", "context-guard", "no-keywords-check"), "w").close()
        check("kw-stop-off: the switch", blocked(run_stop(home, "kwoff")) == "")
    finally:
        rm_tree(home)


def test_the_saving_instructions_ask_for_keywords():
    home = kw_home({}, manifest={"projects": {"droid": {"dir": "D:\\Demo\\Droid",
                                                         "threads": ["droid"], "files": []}}},
                   lists={DROID_KEY: "# Memory Index\n- [A](a.md) - a\n"})
    try:
        t = kw_json(home, "import json\nprint(json.dumps(guard.label_memory_index('droid -2 (1 Oct)')))")
        check("kw-ask: the label fetch asks for keywords", "keywords:" in (t or ""), repr(t)[:300])
        started = time.time() - 3600
        write_chat(home, "kwnag", started)
        write_note(home, "kwnag")
        check("kw-ask: the memory nag asks for keywords",
              "keywords:" in blocked(run_stop(home, "kwnag")))
    finally:
        rm_tree(home)


def test_the_label_belongs_only_to_the_handover_reply():
    """2 Oct 2026: a note said 'label in a code box as the LAST line' and the next chat ended
    every reply with its own label. Both ends are told: the writer how to phrase the rule,
    the reader never to end a reply with its own label."""
    home = make_home({KEY + ".lab12345.md": "HANDOFF LABEL: Harbor -4 (2 Oct)\n"
                      "WRITTEN BY: Harbor -3 (1 Oct)\nNEXT CHAT EFFORT: high - x\n\nbody\n"})
    try:
        steps = guard_call(home, "print(guard.handoff_steps('2 Oct'))").stdout
        check("label-only: the writer is told it is the handover reply only",
              "HANDOVER reply only" in steps, steps[:300])
        c = context_of(run(home, "labchat1", "Harbor -4 (2 Oct)"))
        check("label-only: the pickup says never end a reply with its own label",
              "Never end a reply with this chat's own label" in c, c[:600])
    finally:
        rm_tree(home)


def test_arabic_forms_meet_in_the_word_splitter():
    """Review fix 1: one splitter serves prompts and keywords, so 'the bill' and 'bill' meet
    and a vowel mark does not split a word."""
    home = kw_home({(KEY, "bank-bill"): mem_text("bank-bill", "b", ["فاتورة"]),
                    (KEY, "marked-bill"): mem_text("marked-bill", "m", ["مَدْرَسَة"])})
    try:
        r = kw_json(home, "import json\nprint(json.dumps([guard._words(%r), guard._words(%r),"
                          " guard._words(%r), guard._words(%r)]))"
                    % ("الفاتورة", "فَاتُورَة", "Hello World", "ال"))
        check("kw-ar: the article is dropped from a long word", r[0] == ["فاتورة"], ascii(r))
        check("kw-ar: vowel marks are dropped, not split on", r[1] == ["فاتورة"], ascii(r))
        check("kw-ar: English is unchanged", r[2] == ["hello", "world"], ascii(r))
        check("kw-ar: a short word keeps its letters", r[3] == ["ال"], ascii(r))
        check("kw-ar: keyword and prompt-with-article meet",
              scored(home, "اين الفاتورة الشهر الماضي") == ["bank-bill"])
        check("kw-ar: a marked keyword meets the bare word",
              scored(home, "اين المدرسة اليوم الان") == ["marked-bill"])
    finally:
        rm_tree(home)


def test_odd_frontmatter_is_read_sensibly():
    """Review fix 2: a bracket list, a capitalised key, a folded marker and a long description."""
    def raw(slug, *front):
        return "\n".join(["---", "name: " + slug] + list(front) + ["---", "", "body", ""])
    home = kw_home({
        (KEY, "listed"): raw("listed", "description: Cased words here",
                             "keywords: [alpha, \"beta\", 'gamma']"),
        (KEY, "cap"): raw("cap", "Description: Capital Key", "Keywords: delta"),
        (KEY, "folded"): raw("folded", "description: >-", "  the real text is on later lines"),
        (KEY, "bar"): raw("bar", "description: |"),
        (KEY, "long"): raw("long", "description: " + "x" * 600, "keywords: k"),
        (KEY, "many"): raw("many", "description: " + " ".join(
            "word%s" % c for c in "abcdefghijklmnopqrst")),
    })
    try:
        p = os.path.join(home, ".claude", "projects", KEY, "memory")
        r = kw_json(home, "import json, os\np = %r\nprint(json.dumps({n: guard.memory_keywords("
                          "os.path.join(p, n + '.md')) for n in "
                          "['listed','cap','folded','bar','long','many']}))" % p)
        check("kw-front: a bracket list is split and unquoted",
              r["listed"]["keywords"] == ["alpha", "beta", "gamma"], repr(r["listed"]))
        check("kw-front: Description is matched case-insensitively",
              r["cap"]["description"] == "Capital Key" and r["cap"]["keywords"] == ["delta"],
              repr(r["cap"]))
        check("kw-front: a folded marker -> empty description",
              r["folded"]["description"] == "" and r["bar"]["description"] == "",
              repr([r["folded"], r["bar"]]))
        check("kw-front: the description is capped at 500",
              len(r["long"]["description"]) == 500, str(len(r["long"]["description"])))
        check("kw-front: derived keywords stop at 8 even with a long description",
              r["many"]["derived"] is True and len(r["many"]["keywords"]) == 8, repr(r["many"]))
    finally:
        rm_tree(home)


def test_the_catalogue_distrusts_a_malformed_cache_entry_and_names_its_temp_by_pid():
    """Review fix 3."""
    home = kw_home({(KEY, "one"): mem_text("one", "a", ["alpha"])})
    try:
        r = kw_json(home, "import json, os\n"
                    "guard.memory_catalogue()\n"
                    "c = json.load(open(guard.CATALOGUE))\n"
                    "for p in c: c[p]['kw'] = {'slug': 'x'}\n"
                    "json.dump(c, open(guard.CATALOGUE, 'w'))\n"
                    "src = []\nreal = os.replace\n"
                    "os.replace = lambda a, b: (src.append(a), real(a, b))[1]\n"
                    "e = guard.memory_catalogue()\n"
                    "print(json.dumps([[x.get('keywords') for x in e], src, os.getpid()]))")
        check("kw-cache: a malformed cached kw is re-read",
              r[0] == [["alpha"]], repr(r))
        check("kw-cache: the temp file name carries the pid",
              len(r[1]) == 1 and r[1][0].endswith(".tmp" + str(r[2])), repr(r))
    finally:
        rm_tree(home)


def test_rarity_boundary_and_recall_with_a_flag_word():
    """Review fix 4: 2 carriers is rare, 3 is not, a 2-letter keyword never counts alone,
    and --recall --size searches for the words instead of running --size."""
    mems = {}
    for i in range(2):
        mems[(KEY, "z%d" % i)] = mem_text("z%d" % i, "d", ["zebra", "zz%d" % i])
    for i in range(3):
        mems[(KEY, "y%d" % i)] = mem_text("y%d" % i, "d", ["yakkity", "yy%d" % i])
    mems[(KEY, "q0")] = mem_text("q0", "d", ["qa", "qq0"])
    mems[(KEY, "w0")] = mem_text("w0", "d", ["widget", "ww0"])
    home = kw_home(mems)
    try:
        check("kw-rare: a keyword carried by exactly two memories qualifies alone",
              sorted(scored(home, "the zebra is here today")) == ["z0", "z1"])
        check("kw-rare: a keyword carried by three does not",
              scored(home, "the yakkity is here today") == [])
        check("kw-rare: a rare two-character keyword does not",
              scored(home, "the qa is here today") == [])
        p = run_recall(home, "--size", "widget")
        check("kw-recall: --size is searched for, not run",
              p.returncode == 0 and "w0.md" in p.stdout, repr((p.returncode, p.stdout[:200],
                                                               p.stderr[-200:])))
    finally:
        rm_tree(home)


def test_keywords_under_metadata_are_read_and_the_instructions_allow_both_places():
    """Review fix 8: Claude Code's memory system moves a top-level keywords line under
    metadata: and quotes it. Both shapes must read."""
    six = "usage credits, red bar, extra usage, credit limit, scary red, billing"
    moved = ['---', 'name: moved', 'description: "d"', 'metadata:', '  node_type: memory',
             '  keywords: "%s"' % six, '  type: reference', '---', '', 'body', '']
    single = ['---', 'name: single', 'description: "d"', 'metadata:',
              "  keywords: 'one, two'", '---', '', 'body', '']
    top = ['---', 'name: top', 'description: "d"', 'keywords: three, four', '---', '', 'b', '']
    home = kw_home({(KEY, "moved"): "\n".join(moved), (KEY, "crlf"): "\r\n".join(moved),
                    (KEY, "single"): "\n".join(single), (KEY, "top"): "\n".join(top)})
    try:
        p = os.path.join(home, ".claude", "projects", KEY, "memory")
        r = kw_json(home, "import json, os\np = %r\nprint(json.dumps({n: guard.memory_keywords("
                          "os.path.join(p, n + '.md')) for n in ['moved','crlf','single','top']}))"
                    % p)
        want = [k.strip() for k in six.split(",")]
        check("kw-meta: indented and quoted under metadata reads the six",
              r["moved"]["keywords"] == want and r["moved"]["derived"] is False, repr(r["moved"]))
        check("kw-meta: a CRLF file of that shape too", r["crlf"]["keywords"] == want,
              repr(r["crlf"]))
        check("kw-meta: single quotes", r["single"]["keywords"] == ["one", "two"], repr(r["single"]))
        check("kw-meta: the top-level form still reads", r["top"]["keywords"] == ["three", "four"],
              repr(r["top"]))
        with open(GUARD, encoding="utf-8") as f:
            src = f.read()
        check("kw-meta: no instruction says 'straight after description'",
              "straight after `description:`" not in src)
        check("kw-meta: the instructions allow either place",
              src.count("top level or under `metadata:`, either works") >= 3)
    finally:
        rm_tree(home)


def test_a_spec_slug_linked_by_two_project_lists_is_general():
    """Review fix 6: two project lists claiming a slug name no single project."""
    home = kw_home(
        {(KEY, "shared-lesson"): mem_text("shared-lesson", "s", ["lesson"])},
        lists={DROID_KEY: "# Memory Index\n- [Shared lesson](shared-lesson.md) - x\n",
               "D--Demo-Tides": "# Memory Index\n- [Shared lesson](shared-lesson.md) - x\n"},
        manifest=KW_MANIFEST)
    try:
        r = kw_json(home, "import json\nprint(json.dumps({e['slug']: e['project'] for e in "
                          "guard.memory_catalogue()}))")
        check("kw-cat: a slug linked by two project lists is general",
              r.get("shared-lesson") == "general", repr(r))
    finally:
        rm_tree(home)


def test_odd_state_and_short_arabic_words_are_safe():
    """Review Minors, 2 Oct 2026: a state file of the wrong shape must not crash the Stop
    hook, and the article strip must leave 3+ letters (a 4-letter word starting with alef-lam
    is not 'the' + a 2-letter word)."""
    home = kw_home({})
    try:
        r = kw_json(home, "import json\n"
                    "guard.save_state('odd1', {'kw_written': 5, 'kw_scan_offset': 'x'})\n"
                    "a = guard.memory_files_written('nowhere.jsonl', 'odd1')\n"
                    "w = guard._words('الله الفاتورة')\n"
                    "print(json.dumps([a, w]))")
        check("kw-odd: a wrong-shaped state gives an empty list, no crash",
              isinstance(r, list) and r[0] == [], ascii(r))
        check("kw-odd: a 4-letter word keeps its alef-lam, an 8-letter one loses it",
              isinstance(r, list) and r[1] == ["الله",
                                               "فاتورة"], ascii(r))
    finally:
        rm_tree(home)


def test_the_written_memories_scan_is_incremental():
    """Second review fix 9: a Stop must not re-read the whole transcript. Proved by counting
    json.loads on tool_use lines: the second call parses only what was appended."""
    home = kw_home({(KEY, "a"): mem_text("a", "x"), (KEY, "b"): mem_text("b", "x"),
                    (KEY, "c"): mem_text("c", "x")})
    try:
        md = os.path.join(home, ".claude", "projects", KEY, "memory")
        pa, pb, pc = (os.path.join(md, n + ".md") for n in "abc")
        tp = write_mem_writes(home, "kwinc", [pa, pb])
        code = ("import json, os\n"
                "P = %r\n"
                "n = [0]\nreal = json.loads\n"
                "def counted(s, *a, **k):\n"
                "    if 'tool_use' in s: n[0] += 1\n"
                "    return real(s, *a, **k)\n"
                "json.loads = counted\n"
                "r1 = guard.memory_files_written(P, 'kwinc')\n"
                "first = n[0]\n"
                "rec = {'type': 'assistant', 'message': {'role': 'assistant', 'content': ["
                "{'type': 'tool_use', 'name': 'Write', 'input': {'file_path': %r, 'content': 'x'}}]}}\n"
                "line = real and json.dumps(rec)\n"
                "with open(P, 'a', encoding='utf-8') as f: f.write(line)\n"   # no newline yet
                "r2 = guard.memory_files_written(P, 'kwinc')\n"
                "with open(P, 'a', encoding='utf-8') as f: f.write(chr(10))\n"
                "n[0] = 0\n"
                "r3 = guard.memory_files_written(P, 'kwinc')\n"
                "again = n[0]\n"
                "with open(P, 'w', encoding='utf-8') as f: f.write(line + chr(10))\n"
                "r4 = guard.memory_files_written(P, 'kwinc')\n"
                "print(json.dumps([r1, first, r2, r3, again, r4]))") % (tp, pc)
        r = kw_json(home, code)
        base = lambda xs: [os.path.basename(x) for x in xs]
        check("kw-scan: the first call finds both writes",
              base(r[0]) == ["a.md", "b.md"] and r[1] == 2, repr(r))
        check("kw-scan: a half-written last line is not consumed", base(r[2]) == ["a.md", "b.md"],
              repr(r))
        check("kw-scan: once complete it is found, and only it was parsed",
              base(r[3]) == ["a.md", "b.md", "c.md"] and r[4] == 1, repr(r))
        check("kw-scan: a smaller file is rescanned from the start", base(r[5]) == ["c.md"],
              repr(r))
    finally:
        rm_tree(home)


def test_a_real_label_pickup_keeps_its_list_out_of_the_hints():
    """Second review fix 11a: the list a label pickup put in front of the chat is never hinted."""
    home = kw_home(HINT_MEMS, lists=HINT_LISTS, manifest=KW_MANIFEST)
    try:
        with open(os.path.join(home, ".claude", "handoff", KEY + ".lab12345.md"), "w",
                  encoding="utf-8") as f:
            f.write("HANDOFF LABEL: droid -2 (1 Oct)\nWRITTEN BY: droid -1 (30 Sep)\n"
                    "NEXT CHAT EFFORT: high - x\n\nbody\n")
        c0 = context_of(run(home, "labh", "droid -2 (1 Oct)"))
        lp = kw_json(home, "import json\nprint(json.dumps(guard.load_state('labh').get('label_list')))")
        check("kw-hint-label: the pickup happened and recorded the list",
              "HANDOFF NOTE" in c0 and bool(lp) and "MEMORY.md" in lp, repr((c0[:100], lp)))
        c = context_of(run(home, "labh", "why does adb not see my phone"))
        check("kw-hint-label: a slug in that list is not hinted", "android-adb" not in c, c[:200])
    finally:
        rm_tree(home)


def test_report_lists_only_real_sessions_not_the_other_state_files():
    """Review row 12: memory-catalogue, memory-manifest and update-check are dicts in STATE
    but are not chat sessions, and --report listed them under 'sessions being tracked'."""
    home = make_home({})
    try:
        st = os.path.join(home, ".claude", "context-guard")
        os.makedirs(st, exist_ok=True)
        sid = "0a1b2c3d-1111-2222-3333-444455556666"
        for name in (sid, "memory-catalogue", "memory-manifest", "update-check"):
            with open(os.path.join(st, name + ".json"), "w", encoding="utf-8") as f:
                json.dump({"warned_ctx": 120000, "reads": {}}, f)
        out = run_report(home).stdout
        part = out.split("-- sessions being tracked --")[-1].split("-- request ledgers")[0]
        check("report-sessions: the real session is listed", sid in part, repr(part))
        for other in ("memory-catalogue", "memory-manifest", "update-check"):
            check("report-sessions: %s is not listed" % other, other not in part, repr(part))
    finally:
        rm_tree(home)


def test_a_swallowed_error_leaves_one_line_in_the_log():
    """Review row 8: 108 `except Exception` blocks, and the log had no error line at all."""
    home = make_home({})
    try:
        env = child_env(home)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        code = ("import sys; sys.path.insert(0, %r); import guard; print(guard.read_stdin())"
                % os.path.dirname(GUARD))
        pr = subprocess.run([sys.executable, "-c", code], input="not json at all",
                            capture_output=True, text=True, env=env)
        check("swallowed: read_stdin still answers {}", pr.stdout.strip() == "{}"
              and "Traceback" not in pr.stderr, repr((pr.stdout, pr.stderr[-200:])))
        with open(GUARD, encoding="utf-8", newline="") as f:
            src = f.read().replace("\r\n", "\n").split("\n")
        want = [i + 1 for i, l in enumerate(src) if "return json.loads(sys.stdin.read()" in l]
        lp = os.path.join(home, ".claude", "context-audit.log")
        with open(lp, encoding="utf-8") as f:
            errs = [l.strip() for l in f if " error: " in l]
        check("swallowed: exactly one error line", len(errs) == 1, repr(errs))
        line = errs[0] if errs else ""
        check("swallowed: names the function and the exception type",
              " error: read_stdin: JSONDecodeError: " in line, line)
        check("swallowed: the line number is where it was raised in guard.py",
              len(want) == 1 and line.endswith("(line %d)" % want[0]), repr((line, want)))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_swallowed_never_raises_and_falls_back_to_the_callers_line():
    home = make_home({})
    try:
        r = guard_call(home, "guard.swallowed('plain', ValueError('a\\nb'))\n"
                             "guard.swallowed(None, None)\n"
                             "guard.LOG = '/no/such/dir/x.log'\n"
                             "guard.swallowed('nolog', KeyError('k'))\n"
                             "print('survived')")
        check("swallowed-safe: nothing raised", r.stdout.strip() == "survived"
              and "Traceback" not in r.stderr, repr((r.stdout, r.stderr[-300:])))
        lp = os.path.join(home, ".claude", "context-audit.log")
        with open(lp, encoding="utf-8") as f:
            errs = [l.strip() for l in f if " error: " in l]
        first = errs[0] if errs else ""
        check("swallowed-safe: a never-raised exception takes the caller's line",
              " error: plain: ValueError: a b (line 2)" in first, repr(errs))
        check("swallowed-safe: the message stays on one line", len(errs) >= 1
              and "\n" not in first, repr(errs))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_a_missing_state_file_is_silent_but_a_corrupt_one_is_logged():
    """A probe whose normal answer is 'not there yet' stays quiet; any other failure
    (corrupt JSON, undecodable bytes, no permission) leaves exactly one error line, and
    the return value is the same {} either way."""
    home = make_home({})
    try:
        code = "print(guard.load_state('probe-sid'))\n"
        r = guard_call(home, code)
        lp = os.path.join(home, ".claude", "context-audit.log")

        def errs():
            if not os.path.exists(lp):
                return []
            with open(lp, encoding="utf-8") as f:
                return [l for l in f if " error: " in l]
        check("quiet-probe: missing state -> {} and no error line",
              r.stdout.strip() == "{}" and errs() == [], repr((r.stdout, errs())))
        st = os.path.join(home, ".claude", "context-guard")
        os.makedirs(st, exist_ok=True)
        with open(os.path.join(st, "probe-sid.json"), "wb") as f:
            f.write(b"{ this is not json \xff\xfe")
        r = guard_call(home, code)
        e = errs()
        check("quiet-probe: corrupt state -> still {}", r.stdout.strip() == "{}"
              and "Traceback" not in r.stderr, repr((r.stdout, r.stderr[-200:])))
        check("quiet-probe: corrupt state -> exactly one error line from load_state",
              len(e) == 1 and " error: load_state: " in e[0], repr(e))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_report_has_an_errors_row_with_the_last_five_lines():
    home = make_home({})
    try:
        now = datetime.datetime.now()
        def stamp(days):
            return (now - datetime.timedelta(days=days)).isoformat(timespec="seconds")
        old = stamp(9) + " error: oldfn: OSError: too old to count (line 1)\n"
        new = "".join(stamp(1 + i / 100.0) + " error: fn%d: OSError: boom%d (line %d)\n"
                      % (i, i, 10 + i) for i in range(7))
        out = run_report(home, log=old + new).stdout
        row = line_with(out, "errors (last 7 days)")
        check("report-errors: the row exists and counts 7, not the old one",
              row.split()[-1] != "fired" and " 7 " in row, repr(row))
        check("report-errors: the last five lines are shown",
              "fn6" in out and "fn2" in out and "fn1:" not in out, out[-600:])
        check("report-errors: the old line is not shown", "oldfn" not in out, out[-600:])
        out2 = run_report(home, log=stamp(1) + " ceiling: blocked something\n").stdout
        row2 = line_with(out2, "errors (last 7 days)")
        check("report-errors: none -> never fired", "never fired" in row2, repr(row2))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_a_stop_over_a_string_message_record_does_not_crash():
    """Second review fix 13: a transcript record whose message is a plain string."""
    home = kw_home({})
    try:
        p = os.path.join(home, ".claude", "projects", KEY, "strmsg.jsonl")
        with open(p, "w", encoding="utf-8") as f:
            f.write(json.dumps({"type": "user", "message": "just a string"}) + chr(10))
            f.write(json.dumps({"type": "assistant", "message": "another"}) + chr(10))
            f.write(json.dumps({"type": "user", "timestamp": "2026-10-01T10:00:00Z",
                                "message": {"role": "user", "content": "a real ask"}}) + chr(10))
        pr = run_stop(home, "strmsg")
        expect_clean(pr, "kw-strmsg")
        check("kw-strmsg: exit 0, no traceback", pr.returncode == 0 and "Traceback" not in pr.stderr,
              pr.stderr[-300:])
    finally:
        rm_tree(home)


def test_recall_finds_a_memory_from_one_word_of_a_phrase_keyword():
    """Plan batch A task 2: loose mode counts a 3+ character query word that sits inside a
    keyword phrase as a weak hit; a whole-phrase hit still ranks first; strict is unchanged."""
    mems = {
        (KEY, "adb-fails"): mem_text("adb-fails", "d", ["adb install fails", "usb cable"]),
        (KEY, "adb-bare"): mem_text("adb-bare", "d", ["adb", "logcat"]),
        (KEY, "red-bar"): mem_text("red-bar", "d", ["red usage credits bar", "billing"]),
        (KEY, "state-art"): mem_text("state-art", "d", ["state of the art", "qa checks"]),
    }
    home = kw_home(mems)
    try:
        d = os.path.join(home, ".claude", "projects", KEY, "memory")
        os.utime(os.path.join(d, "adb-bare.md"), (1000000000, 1000000000))   # OLDER than adb-fails
        p = run_recall(home, "adb")
        expect_clean(p, "kw-weak")
        heads = [l.split(" (")[0] for l in p.stdout.splitlines() if l and not l.startswith(" ")]
        check("kw-weak: one word of a phrase keyword finds it", "adb-fails" in " ".join(heads)
              or any("adb-fails.md" in l for l in p.stdout.splitlines()), p.stdout[:300])
        order = [l for l in p.stdout.splitlines() if l and not l.startswith(" ")]
        check("kw-weak: a whole-phrase hit ranks above a one-word hit",
              len(order) == 2 and "adb-bare.md" in order[0] and "adb-fails.md" in order[1],
              p.stdout[:300])
        q = run_recall(home, "red", "usage", "bar")
        check("kw-weak: red usage bar finds red usage credits bar", "red-bar.md" in q.stdout,
              q.stdout[:300])
        for words in (("the", "a", "of"), ("qa",), ("of",)):
            r = run_recall(home, *words)
            check("kw-weak: %r finds nothing" % (words,),
                  r.stdout.strip() == "No memory matches: " + " ".join(words), r.stdout[:300])
        check("kw-weak: strict mode still gives no hint for one word of a phrase",
              scored(home, "why does adb break on my phone today") == ["adb-bare"]
              and scored(home, "the red thing is here today") == [])
    finally:
        rm_tree(home)


def test_a_cross_session_message_gets_no_hint():
    """Review fix 7: hints are for HIS typing - not a hand-back or a relayed message."""
    home = kw_home(HINT_MEMS, lists=HINT_LISTS, manifest=KW_MANIFEST)
    try:
        said = "why does adb not see my phone"
        for i, wrap in enumerate(("<cross-session-message from=x>%s</cross-session-message>",
                                  "<agent-message>%s</agent-message>",
                                  "[Subagent hand-back] %s",
                                  "Stop hook feedback: %s",
                                  "<task-notification>%s</task-notification>")):
            c = context_of(run(home, "hintx%d" % i, wrap % said))
            check("kw-hint-from-others: " + wrap[:22], "android-adb" not in c, c[:200])
        c = context_of(run(home, "hintx9", said))
        check("kw-hint-from-others: the bare sentence still hints", "android-adb.md" in c, c[:200])
        p = kw_json(home, "import json\n"
                    "d = {'session_id': 'hinty', 'prompt': '[Image: x] why does adb not see my phone'}\n"
                    "r = guard.memory_hints({}, d, 'hinty')\n"
                    "print(json.dumps([r, guard.load_state('hinty').get('memory_hints')]))")
        check("kw-hint-from-others: ledger noise gets nothing and records nothing",
              p == [{}, None], repr(p))
    finally:
        rm_tree(home)


if __name__ == "__main__":
    for t in (test_a_folderless_chat_naming_a_saved_thread_is_told_to_move,
              test_a_big_folderless_chat_is_not_told_to_move,
              test_a_folderless_chat_naming_nothing_is_untouched,
              test_an_ordinary_folder_gets_no_redirect,
              test_the_apps_boilerplate_never_names_a_label,
              test_the_folder_is_read_from_a_transcripts_cwd_when_the_manifest_lacks_it,
              test_a_note_under_a_scratch_key_is_never_a_redirect_target,
              test_update_up_to_date_says_nothing_and_behind_says_the_line,
              test_update_checks_at_most_once_a_day,
              test_update_offline_is_silent_fast_and_recorded,
              test_update_off_switch_and_no_git_folder_make_no_git_call,
              test_update_a_silent_server_cannot_hang_the_check,
              test_update_offline_keeps_an_earlier_notice,
              test_update_leaves_a_branch_of_their_own_alone,
              test_update_apply_without_git_says_so_plainly,
              test_update_a_clone_ahead_of_origin_says_nothing,
              test_update_a_rewritten_history_says_it_was_rebuilt,
              test_update_apply_fast_forwards_and_lists_the_new_subjects,
              test_update_apply_refuses_a_dirty_tree_and_changes_nothing,
              test_update_apply_on_a_rewrite_backs_up_then_resets,
              test_update_dry_run_changes_nothing_not_even_the_remote_ref,
              test_update_apply_when_current_says_so_and_a_zip_says_how,
              test_update_apply_fails_cleanly_when_fetch_fails,
              test_alert_carries_the_update_line_when_nothing_is_flagged,
              test_alert_appends_the_update_line_when_sessions_are_flagged,
              test_alert_with_nothing_to_say_still_prints_nothing,
              test_alert_survives_an_old_checkout_without_update_py,
              test_install_update_flag_is_wired_and_plain_install_is_unchanged,
              test_handoff_instruction_demands_memory_consolidation,
              test_handoff_label_gets_the_next_number,
              test_a_brand_new_thread_starts_at_one,
              test_a_numbered_short_label_answers_to_its_name,
              test_a_numbered_label_answers_when_he_types_the_number_too,
              test_a_numbered_label_still_ignores_a_stray_word,
              test_new_chat_label_match,
              test_new_chat_no_match_offers_menu,
              test_menu_then_the_label_still_works,
              test_short_label_is_still_summonable,
              test_short_label_does_not_answer_to_a_stray_word,
              test_new_chat_single_note_needs_no_label,
              test_expensive_chat_does_not_eat_the_note,
              test_new_chat_with_no_notes_is_quiet,
              test_second_note_only_when_he_names_it,
              test_stop_nags_when_the_note_is_written_but_no_memory_was_saved,
              test_stop_is_silent_once_a_memory_was_really_written,
              test_stop_does_not_nag_a_chat_that_never_handed_off,
              test_stop_never_blocks_twice_in_a_row,
              test_the_memory_nag_has_an_off_switch,
              test_the_nag_refuses_to_run_without_a_session_id,
              test_the_memory_nag_gives_up_after_two_tries,
              test_a_note_without_its_head_lines_is_sent_back_once,
              test_a_note_with_all_three_head_lines_is_not_blocked,
              test_the_head_check_blocks_once_per_version_of_the_note,
              test_the_head_check_never_blocks_twice_in_a_row,
              test_a_bad_head_and_a_missing_memory_save_come_in_one_block,
              test_decorated_and_bom_heads_are_not_false_positives,
              test_the_head_check_has_an_off_switch,
              test_the_head_block_names_its_off_switch,
              test_the_head_check_does_not_swallow_the_memory_nag,
              test_the_ledger_is_still_written_when_the_nag_fires,
              test_a_checkpoint_records_the_context_it_was_reached_at,
              test_the_warning_reports_the_last_checkpoint,
              test_the_warning_says_when_no_checkpoint_was_ever_recorded,
              test_the_ceiling_blocks_the_stop_until_the_note_is_written,
              test_the_ceiling_leaves_an_ordinary_chat_alone,
              test_the_ceiling_is_satisfied_by_the_note_existing,
              test_the_ceiling_has_an_off_switch,
              test_the_ceiling_gives_up_rather_than_trapping_him,
              test_a_paused_chat_is_not_told_to_hand_off,
              test_a_paused_chat_can_close_past_the_ceiling,
              test_a_paused_chat_still_gets_the_memory_nag,
              test_resume_brings_the_warning_back_and_a_typo_pauses_nothing,
              test_the_ceiling_does_not_block_a_stop_it_already_blocked,
              test_skills_proposes_a_shape_repeated_across_chats,
              test_skills_ignores_a_shape_repeated_inside_one_chat,
              test_skills_command_is_gone_and_writes_nothing,
              test_handoff_offers_the_skill_candidates,
              test_handoff_stays_quiet_when_nothing_is_repeated,
              test_skills_does_not_propose_the_shapes_it_actually_found,
              test_skills_still_proposes_a_script_run_by_path,
              test_report_shows_what_was_blocked_not_just_how_many,
              test_report_flags_a_counter_that_only_ever_fired_on_one_day,
              test_report_says_outright_when_a_guard_has_never_fired,
              test_report_counts_the_guards_that_were_added_later,
              test_label_number_never_goes_backwards,
              test_ledger_prose_is_not_a_thread,
              test_pickup_renames_the_chat_to_its_own_number,
              test_the_title_is_the_label_as_it_is,
              test_his_typed_number_wins_the_title,
              test_two_threads_each_offer_their_own_label_as_the_title,
              test_a_trailing_zero_usage_record_does_not_read_as_an_empty_chat,
              test_a_big_chat_with_a_zero_trailing_record_does_not_swallow_a_note,
              test_the_ceiling_fires_below_the_point_the_window_auto_compacts,
              test_the_ceiling_tracks_a_smaller_configured_window,
              test_the_ceiling_still_leaves_a_mid_sized_chat_alone,
              test_the_same_words_in_two_chats_are_both_recorded,
              test_the_ledger_records_which_chat_said_each_thing,
              test_a_chat_does_not_re_record_its_own_repeated_words,
              test_the_first_message_of_a_chip_chat_is_recorded_however_long,
              test_harness_placeholders_are_not_recorded_as_his_words,
              test_the_injected_tail_separates_this_thread_from_the_others,
              test_the_tail_still_reads_when_nothing_is_attributed,
              test_older_entries_get_their_chat_back,
              test_attribute_is_gone,
              test_a_quiet_thread_still_gets_its_own_words,
              test_an_unattributed_project_still_gets_a_full_tail,
              test_bash_output_files_are_not_reported_as_background_jobs,
              test_a_heredoc_writing_a_file_is_blocked,
              test_a_heredoc_feeding_a_script_to_python_is_blocked,
              test_a_small_data_heredoc_is_left_alone,
              test_stderr_redirection_is_not_mistaken_for_writing_a_file,
              test_an_ordinary_command_is_never_touched,
              test_the_heredoc_guard_has_an_off_switch,
              test_the_heredoc_guard_can_be_overridden_per_command,
              test_a_thread_whose_history_fits_inherits_all_of_it,
              test_a_long_thread_is_told_what_was_left_out,
              test_a_long_thread_keeps_its_oldest_requests,
              test_a_summon_label_is_not_carried_as_a_request,
              test_old_noise_in_the_ledger_is_not_carried_into_a_pickup,
              test_the_thread_budget_is_characters_not_entries,
              test_report_writes_nothing,
              test_install_is_idempotent,
              test_install_wires_the_bootstrap,
              test_uninstall_removes_the_bootstrap_too,
              test_install_keeps_other_peoples_settings_and_hooks,
              test_install_dry_run_writes_nothing,
              test_install_backs_the_file_up_before_writing,
              test_uninstall_removes_only_our_hooks,
              test_install_drops_a_stale_reread_hook,
              test_uninstall_also_removes_a_stale_reread_hook,
              test_a_leftover_reread_hook_is_a_silent_no_op,
              test_install_refuses_to_touch_a_settings_file_it_cannot_parse,
              test_the_suite_cannot_reach_a_real_home_on_any_platform,
              test_away_is_armed_only_by_an_explicit_phrase,
              test_saying_back_disarms_away,
              test_back_is_ignored_when_he_was_never_away,
              test_away_silences_the_visible_nag_but_still_orders_the_save,
              test_the_ceiling_still_saves_while_away,
              test_a_chat_that_never_reaches_the_ceiling_still_leaves_a_stub,
              test_the_stub_never_overwrites_a_real_note,
              test_a_chat_is_told_to_write_its_note_long_before_the_ceiling,
              test_a_small_chat_is_not_asked_for_a_note,
              test_away_mode_says_so_once_per_chat,
              test_the_away_indicator_names_both_ways_out,
              test_no_indicator_when_he_is_at_the_console,
              test_the_away_indicator_survives_a_handoff_pickup,
              test_a_note_that_mentions_the_marker_is_not_a_stub,
              test_pickup_names_the_finished_chat_for_archiving,
              test_pickup_files_the_new_chat_in_the_old_chats_group,
              test_pickup_without_a_writer_line_says_nothing_about_archiving,
              test_a_writer_line_deep_in_the_body_is_not_the_writer,
              test_the_note_instruction_asks_for_the_writer_line,
              test_the_archive_instruction_survives_a_truncated_tail,
              test_the_project_key_matches_the_ones_claude_code_actually_made,
              test_bootstrap_furnishes_a_fresh_directory,
              test_bootstrap_gives_the_new_folder_only_its_own_memories,
              test_bootstrap_puts_the_new_index_in_context_on_the_same_turn,
              test_bootstrap_lists_rather_than_copies,
              test_bootstrap_never_overwrites_an_existing_memory_folder,
              test_bootstrap_is_idempotent,
              test_bootstrap_ignores_a_directory_the_manifest_does_not_know,
              test_bootstrap_matches_an_also_directory,
              test_bootstrap_survives_a_manifest_entry_with_no_file_on_disk,
              test_the_bootstrap_arms_a_probe_only_when_it_actually_furnished_a_folder,
              test_the_hook_answers_the_ordering_question_by_itself,
              test_an_unanswerable_probe_waits_instead_of_guessing,
              test_a_nag_that_stays_quiet_says_why,
              test_the_ledger_says_when_the_budget_drops_his_requests,
              test_a_neighbours_memory_save_no_longer_silences_this_chats_nag,
              test_the_neighbour_share_of_the_tail_is_bounded_too,
              test_bootstrap_finds_an_index_line_that_moved_to_the_pointer,
              test_the_report_surfaces_the_ordering_verdict,
              test_a_thread_label_hands_back_that_projects_memory_index,
              test_a_label_that_maps_to_no_project_appends_nothing,
              test_a_project_with_no_memory_folder_is_skipped_not_crashed,
              test_the_project_index_is_not_re_injected_every_turn,
              test_coming_back_from_away_re_arms_on_that_same_turn,
              test_a_watermark_spent_while_away_does_not_mute_the_chat_forever,
              test_a_compaction_re_arms_the_warning_even_far_above_the_floor,
              test_growth_within_one_epoch_is_still_not_a_compaction,
              test_the_guidance_forbids_handing_him_a_path_in_both_branches,
              test_the_note_template_names_the_log_it_is_written_from,
              test_the_label_fetch_names_the_shared_folder_and_the_project_list,
              test_bootstrap_writes_only_the_list_with_the_header,
              test_the_header_goes_into_an_existing_list_once_and_keeps_its_endings,
              test_a_list_that_changes_during_the_write_is_left_alone,
              test_the_first_warning_waits_for_225k,
              test_audit_states_the_same_first_warning_as_guard,
              test_weekly_one_call_written_as_several_lines_counts_once,
              test_weekly_puts_a_call_in_this_week_last_week_or_neither,
              test_weekly_skips_sidechains_and_other_record_types,
              test_weekly_a_forked_chat_copy_counts_once,
              test_weekly_refresh_is_incremental_and_survives_a_rewrite,
              test_weekly_budget_stops_after_one_file_and_a_later_run_completes,
              test_weekly_cli_prints_the_sentence_or_says_nothing_measured,
              test_weekly_line_wording_follows_the_sign_and_drops_a_missing_comparison,
              test_weekly_budget_also_applies_inside_one_huge_file,
              test_weekly_budget_also_counts_bytes_not_only_lines,
              test_session_end_also_refreshes_the_weekly_number_and_still_prints_nothing,
              test_report_shows_the_weekly_line_and_writes_nothing,
              test_report_session_list_hides_the_weekly_files,
              test_alert_adds_the_weekly_line_only_from_a_fresh_complete_summary,
              test_the_pickup_names_the_effort_the_note_asks_for,
              test_the_sweep_moves_a_memory_with_no_shared_copy_home,
              test_the_sweep_leaves_a_file_changed_in_the_last_ten_minutes,
              test_the_sweep_skips_while_another_sweep_holds_the_lock,
              test_the_sweep_has_an_off_switch,
              test_a_sweep_with_nothing_to_do_prints_nothing,
              test_the_sweep_gives_a_list_the_header_before_moving_out_of_it,
              test_the_sweep_backs_up_a_spare_identical_to_its_shared_copy,
              test_a_failure_between_copy_and_remove_loses_nothing,
              test_two_different_copies_are_left_alone_and_reported_once,
              test_bootstrap_and_the_sweep_print_one_json_object,
              test_the_sweep_drops_a_shared_line_its_one_claimant_already_lists,
              test_the_sweep_moves_a_shared_line_to_the_one_claimant_that_lacks_it,
              test_the_sweep_leaves_a_slug_with_zero_or_two_claimants,
              test_a_crlf_list_stays_crlf,
              test_the_sweep_leaves_a_list_changed_in_the_last_ten_minutes,
              test_one_unwritable_list_does_not_stop_the_sweep,
              test_a_list_held_open_piles_up_no_backups_and_no_debris,
              test_a_write_that_fails_midway_leaves_no_half_file_at_the_shared_path,
              test_one_failing_folder_file_or_list_step_does_not_stop_the_rest_of_the_sweep,
              test_a_spare_that_cannot_be_removed_piles_up_no_backups,
              test_a_project_folder_that_is_a_junction_onto_the_shared_folder_is_left_alone,
              test_a_file_hard_linked_to_its_shared_copy_is_not_a_spare,
              test_a_move_re_reads_its_source_before_removing_it,
              test_an_already_backed_up_spare_is_re_read_before_it_is_removed,
              test_a_put_new_cleanup_that_fails_is_logged_and_the_original_error_raised,
              test_case_4_gives_a_target_list_with_no_header_the_header,
              test_a_consumed_pickup_adds_the_writer_to_the_pending_list,
              test_the_menu_adds_nothing_to_the_pending_list,
              test_a_second_pickup_of_the_same_title_is_not_a_duplicate,
              test_a_live_work_refusal_switches_remote_control_off_unless_away,
              test_the_handover_switches_its_own_remote_control_off_unless_away,
              test_the_label_is_handed_over_in_a_code_box,
              test_the_handover_stops_background_work_and_ends_on_the_label,
              test_typing_context_guard_tour_shows_it_again,
              test_the_first_chat_after_install_shows_the_tour_once,
              test_a_fresh_install_leaves_the_tour_for_the_first_chat,
              test_the_tour_names_no_private_project,
              test_the_handover_shows_the_label_and_finishes_when_the_new_chat_reports,
              test_the_pickup_reports_to_the_old_chat_only_when_the_note_has_an_effort,
              test_an_entry_is_offered_only_when_due,
              test_one_instruction_covers_every_due_title_and_stays_short,
              test_the_offer_merges_into_a_message_without_context_and_reaches_a_paused_chat,
              test_away_mode_does_not_suppress_the_retry,
              test_success_is_read_from_the_transcript_and_only_from_the_stored_offset,
              test_only_a_genuine_tool_result_clears_an_entry,
              test_old_text_before_the_offset_is_not_reread_when_new_text_follows,
              test_a_success_line_split_across_two_runs_is_still_found,
              test_a_watched_offset_is_never_moved_forward_by_an_offer,
              test_the_offset_moves_forward_and_a_run_reads_at_most_the_cap,
              test_an_awkward_title_round_trips_through_pickup_offer_success_and_the_flag,
              test_the_archived_flag_removes_an_entry_and_an_unknown_title_is_harmless,
              test_the_cap_drops_an_entry_and_tells_him_exactly_once,
              test_a_corrupt_pending_list_neither_crashes_nor_breaks_the_output,
              test_the_report_lists_the_chats_waiting_to_be_archived,
              test_the_pickup_asks_for_the_archive_right_after_the_rename,
              test_a_pickup_or_menu_turn_gets_no_offer_and_nothing_is_stamped,
              test_a_held_lock_skips_the_step_cleanly_and_a_stale_one_is_taken_over,
              test_hooks_running_at_once_lose_no_pickup,
              test_non_finite_numbers_and_a_huge_file_are_survived,
              test_an_untitled_writer_is_ignored,
              test_an_entry_has_a_short_stable_id_and_the_flag_takes_it,
              test_the_keywords_reader_reads_the_line_and_falls_back_to_the_summary,
              test_the_catalogue_prefers_the_shared_copy_and_names_each_project,
              test_the_catalogue_cache_rereads_only_a_changed_file_and_survives_corruption,
              test_the_scorer_asks_for_two_keywords_or_one_rare_one,
              test_recall_lists_the_best_five_and_says_so_when_nothing_matches,
              test_the_hint_names_another_projects_memory_once,
              test_the_hint_skips_what_the_chat_already_has,
              test_the_hint_stays_quiet_when_it_should,
              test_the_hint_shows_at_most_three,
              test_a_memory_saved_without_keywords_is_sent_back_once,
              test_the_keywords_check_has_an_off_switch_and_a_cap,
              test_the_saving_instructions_ask_for_keywords,
              test_the_label_belongs_only_to_the_handover_reply,
              test_arabic_forms_meet_in_the_word_splitter,
              test_odd_frontmatter_is_read_sensibly,
              test_the_catalogue_distrusts_a_malformed_cache_entry_and_names_its_temp_by_pid,
              test_rarity_boundary_and_recall_with_a_flag_word,
              test_keywords_under_metadata_are_read_and_the_instructions_allow_both_places,
              test_a_cross_session_message_gets_no_hint,
              test_a_spec_slug_linked_by_two_project_lists_is_general,
              test_the_written_memories_scan_is_incremental,
              test_odd_state_and_short_arabic_words_are_safe,
              test_a_real_label_pickup_keeps_its_list_out_of_the_hints,
              test_a_stop_over_a_string_message_record_does_not_crash,
              test_a_swallowed_error_leaves_one_line_in_the_log,
              test_swallowed_never_raises_and_falls_back_to_the_callers_line,
              test_a_missing_state_file_is_silent_but_a_corrupt_one_is_logged,
              test_report_has_an_errors_row_with_the_last_five_lines,
              test_report_lists_only_real_sessions_not_the_other_state_files,
              test_recall_finds_a_memory_from_one_word_of_a_phrase_keyword,
              test_a_stop_writes_no_stub_any_more,
              test_session_end_never_touches_a_real_note_and_stubs_when_none,
              test_bootstrap_stubs_other_stale_chats_of_the_same_project_only,
              test_bootstrap_retires_old_stubs_by_renaming_and_leaves_real_notes,
              test_install_wires_the_session_end_hook,
              test_hook_text_over_the_cap_is_cut_and_the_rest_goes_to_a_file,
              test_hook_text_at_or_under_the_cap_is_emitted_unchanged,
              test_hook_text_with_no_newline_in_range_is_cut_hard,
              test_a_failed_overflow_write_emits_the_original_and_logs_it,
              test_a_real_30k_pickup_fits_the_hook_channel,
              test_the_plain_first_handoff_warning_is_emitted_whole,
              test_the_overflow_pointer_is_neutral_about_what_was_cut,
              test_two_cuts_in_a_row_for_one_session_write_two_overflow_files,
              test_a_stale_stub_is_retired_even_behind_fifty_old_real_notes,
              test_the_sweep_writes_a_stub_only_for_a_chat_idle_under_48_hours,
              test_session_end_skips_a_consumed_note_and_a_tiny_chat,
              test_manifest_cache_and_sort_failures_are_logged_but_missing_files_are_not,
              test_a_reversed_two_word_keyword_is_not_scored_in_strict_mode):
        print(t.__name__)
        t()
    print()
    if FAILED:
        print("FAILED %d check(s): %s" % (len(FAILED), "; ".join(FAILED)))
        sys.exit(1)
    print("all checks passed")
