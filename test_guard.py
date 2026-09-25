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
spot that shipped a broken headphone button in Spotliar with 18 passing tests.
"""

import datetime
import json
import os
import re
import shutil
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
NOTE_SPOT = "HANDOFF LABEL: Spotliar\n\n# Handoff - Spotliar\n\nbody-SPOTLIAR\n"
NOTE_CREDO = "HANDOFF LABEL: Credo Calc\n\n# Handoff - Credo\n\nbody-CREDO\n"
NOTE_EGX = "HANDOFF LABEL: EGX bot\n\n# Handoff - EGX\n\nbody-EGXBOT\n"

FAILED = []


def make_home(notes, transcript_ctx=None):
    """A fake ~ with a projects dir, optional transcript, and the given waiting notes."""
    home = tempfile.mkdtemp(prefix="guardtest-")
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
                      KEY + ".a7734c71.md": NOTE_CREDO})
    try:
        p = run(home, "cf1f81f6-new", "context guard")
        ctx = context_of(p)
        expect_clean(p, "new-chat/label")
        check("new-chat/label: delivered a note", "body-CONTEXTGUARD" in ctx, repr(ctx[:160]))
        check("new-chat/label: delivered ONLY that note",
              "body-SPOTLIAR" not in ctx and "body-CREDO" not in ctx)
        check("new-chat/label: consumed exactly one", len(waiting(home)) == 2, str(waiting(home)))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_new_chat_no_match_offers_menu():
    """No label named -> a menu, and NOTHING consumed."""
    home = make_home({KEY + ".9e19c7ab.md": NOTE_CTX,
                      KEY + ".143f0320.md": NOTE_SPOT,
                      KEY + ".a7734c71.md": NOTE_CREDO})
    try:
        p = run(home, "cf1f81f6-menu", "can you look at the weather")
        ctx = context_of(p)
        expect_clean(p, "new-chat/menu")
        check("new-chat/menu: offered the menu", "SAVED THREADS ARE WAITING" in ctx, repr(ctx[:160]))
        check("new-chat/menu: named the labels",
              "context guard" in ctx and "Spotliar" in ctx and "Credo Calc" in ctx)
        check("new-chat/menu: consumed nothing", len(waiting(home)) == 3, str(waiting(home)))
        check("new-chat/menu: leaked no note body", "body-SPOTLIAR" not in ctx)
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
                      KEY + ".a7734c71.md": NOTE_CREDO})
    try:
        p1 = run(home, "cf1f81f6-answer", "hi")
        check("menu-then-label: turn 1 was the menu",
              "SAVED THREADS ARE WAITING" in context_of(p1), repr(context_of(p1)[:160]))

        p2 = run(home, "cf1f81f6-answer", "still nothing that matches")
        check("menu-then-label: menu is not repeated every turn",
              "SAVED THREADS ARE WAITING" not in context_of(p2), repr(context_of(p2)[:160]))

        p3 = run(home, "cf1f81f6-answer", "Credo Calc")
        ctx = context_of(p3)
        expect_clean(p3, "menu-then-label")
        check("menu-then-label: the label delivered its note", "body-CREDO" in ctx,
              repr(ctx[:200]))
        check("menu-then-label: delivered ONLY that note",
              "body-SPOTLIAR" not in ctx and "body-CONTEXTGUARD" not in ctx)
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
    the guard's own chat eating the Spotliar note one turn after being compacted."""
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
    The comment at the gate in cmd_size gives the reason: refusing "Credo Calc" in a chat
    that had loaded the guard note is a silent drop, the exact failure this whole mechanism
    exists to stop. That session ended before the test caught up, so the suite has been red
    ever since. The rule it pins now: never silently, only by name, and never once the chat
    has stopped being cheap."""
    home = make_home({KEY + ".9e19c7ab.md": NOTE_CTX,
                      KEY + ".143f0320.md": NOTE_SPOT,
                      KEY + ".a7734c71.md": NOTE_CREDO})
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

        p3 = run(home, "cf1f81f6-twice", "Spotliar")
        ctx3 = context_of(p3)
        expect_clean(p3, "twice")
        check("twice: naming a second thread by label still works",
              "body-SPOTLIAR" in ctx3, repr(ctx3[:160]))
        check("twice: and brings only that one", "body-CREDO" not in ctx3)
        check("twice: leaving the third waiting", len(waiting(home)) == 1, str(waiting(home)))

        write_transcript(home, "cf1f81f6-twice", 200_000)   # the chat is no longer cheap
        p4 = run(home, "cf1f81f6-twice", "Credo Calc")
        expect_clean(p4, "twice")
        check("twice: an expensive chat takes nothing, even by name",
              "body-CREDO" not in context_of(p4), repr(context_of(p4)[:160]))
        check("twice: that note is still there for a fresh chat", len(waiting(home)) == 1,
              str(waiting(home)))
    finally:
        shutil.rmtree(home, ignore_errors=True)

def test_short_label_is_still_summonable():
    """A label whose every word is under 4 characters - "EGX bot".

    Measured 17 Sep 2026 against his real handoff dir: _tokens() drops words shorter than
    4 chars so that a stray word cannot summon a note - but applied to the LABEL that left
    "EGX bot" as the EMPTY set, and `_tokens(label) & words` can then never match anything.
    He types back the exact name the menu just offered him and the hook prints NOTHING: a
    14k note unreachable from every chat that had already seen the menu."""
    home = make_home({KEY + ".7e5cfde2.md": NOTE_EGX,
                      KEY + ".143f0320.md": NOTE_SPOT})
    try:
        p1 = run(home, "cf1f81f6-egx", "hi")
        check("short-label: turn 1 was the menu",
              "SAVED THREADS ARE WAITING" in context_of(p1), repr(context_of(p1)[:160]))

        p2 = run(home, "cf1f81f6-egx", "EGX bot")
        ctx = context_of(p2)
        expect_clean(p2, "short-label")
        check("short-label: the name he was offered delivered its note",
              "body-EGXBOT" in ctx, repr(ctx[:200]))
        check("short-label: left the other one waiting", len(waiting(home)) == 1,
              str(waiting(home)))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_short_label_does_not_answer_to_a_stray_word():
    """The other half of that fix: "EGX bot" falls back to needing the WHOLE label named,
    so ordinary talk containing one of its words must not drag 14k of note into the chat.
    This one passed before the fix too - nothing matched that label at all then. It is here
    to stop the fallback ever being loosened from all-words to any-word."""
    home = make_home({KEY + ".7e5cfde2.md": NOTE_EGX,
                      KEY + ".143f0320.md": NOTE_SPOT})
    try:
        run(home, "cf1f81f6-stray", "hi")
        p2 = run(home, "cf1f81f6-stray", "reboot the bot on the server")
        expect_clean(p2, "stray-word")
        check("stray-word: one word of the label summons nothing",
              "body-EGXBOT" not in context_of(p2), repr(context_of(p2)[:160]))
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
        write_transcript(home, "bigchat", 200_000)
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


def write_note(home, sid, body=NOTE_CTX):
    """The handoff note THIS chat wrote - <KEY>.<sid8>.md, same as handoff_path()."""
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


def run_skills(home, key=KEY):
    env = child_env(home)
    return subprocess.run([sys.executable, GUARD, "--skills", key],
                          capture_output=True, text=True, env=env)


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
        p = run_skills(home)
        out = p.stdout or ""
        check("skills: exited 0", p.returncode == 0, (p.stderr or "")[:300])
        check("skills: proposed the repeated shape", "python test_guard.py" in out, repr(out[:500]))
        check("skills: counted it across both chats", "2 chats" in out, repr(out[:500]))
        check("skills: says out loud that it only proposes",
              "never creates a skill" in out, repr(out[:500]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_skills_ignores_a_shape_repeated_inside_one_chat():
    """A repeat inside ONE chat is somebody iterating on a problem, not a workflow worth a
    skill. Ranking by runs x distinct chats is his rule, recorded 17 Sep 2026."""
    home = make_home({})
    try:
        write_tooluse(home, "chatA", ["git status"] * 6)
        p = run_skills(home)
        out = p.stdout or ""
        check("skills-noise: exited 0", p.returncode == 0, (p.stderr or "")[:300])
        check("skills-noise: did not propose a one-chat repeat",
              "git status" not in out, repr(out[:500]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_skills_writes_nothing_at_all():
    """PROPOSE ONLY. He confirmed it in his own words - "propose-only for skills" - and the
    handoff note says not to reopen it. A scan that can write is a different feature."""
    home = make_home({})
    try:
        write_tooluse(home, "chatA", ["python test_guard.py"] * 2)
        write_tooluse(home, "chatB", ["python test_guard.py"] * 2)
        before = snapshot(home)
        run_skills(home)
        after = snapshot(home)
        check("skills: created nothing", sorted(after) == sorted(before),
              str(sorted(set(after) - set(before))[:5]))
        check("skills: changed nothing", after == before,
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
        write_transcript(home, "bigchat", 200_000)
        p = run(home, "bigchat", "carry on")
        ctx = context_of(p)
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
        write_transcript(home, "bigchat", 200_000)
        ctx = context_of(run(home, "bigchat", "carry on"))
        check("skills-quiet: the warning still fired", "THIS CHAT IS NOW" in ctx, repr(ctx[:160]))
        check("skills-quiet: but proposed nothing", "SKILL CANDIDATES" not in ctx,
              repr(ctx[-400:]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def proposed(proc):
    """Just the shapes a --skills run actually put forward."""
    out = []
    for l in (proc.stdout or "").splitlines():
        if " runs across " not in l:
            continue
        out.append(l.split(" runs across ")[0].rsplit(None, 1)[0].strip())
    return out


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
        p = run_skills(home)
        got = proposed(p)
        check("skills-real: exited 0", p.returncode == 0, (p.stderr or "")[:300])
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
        got = proposed(run_skills(home))
        check("skills-script: proposed the repeated script", "deploy.sh" in got, str(got))
    finally:
        shutil.rmtree(home, ignore_errors=True)




# ------------------------------------------------- change 4: numbered handoff labels
NOTE_EGX_NUM = "HANDOFF LABEL: EGX bot -2 (16 Sep)\n\n# Handoff - EGX\n\nbody-EGXBOT\n"


def today_label():
    """The date the label should carry - '17 Sep', not '17 Sep 2026' and not '09/17'."""
    return datetime.datetime.now().strftime("%d %b").lstrip("0")


def test_handoff_label_gets_the_next_number():
    """His ask, 17 Sep 2026: "can we ad a -number after the name of chat so it would be
    updated numerically so i can keep up with the order", and then, choosing the format:
    "when you tell me write "zack - 2" when i start a chat you would notice the number so
    you would know that you need to increase it by one".

    A fresh chat cannot know how many chats of this thread came before it, so the NUMBER
    has to be computed from disk, not guessed. The .used-* archives are the history - the
    note this chat picked up is already one of them by the time it hands off - so they
    count too. Per THREAD, not per folder: Spotliar being at -7 must not push Context
    Guard to -8."""
    home = make_home({
        KEY + ".aaaaaaaa.used-20260915-101010.md":
            "HANDOFF LABEL: Context Guard\n\n# Handoff\n\noldest\n",
        KEY + ".bbbbbbbb.used-20260916-101010.md":
            "HANDOFF LABEL: Context Guard -2 (16 Sep)\n\n# Handoff\n\nolder\n",
        KEY + ".cccccccc.used-20260917-111111.md":
            "HANDOFF LABEL: Spotliar -7 (17 Sep)\n\n# Handoff\n\nspot\n",
    })
    try:
        write_transcript(home, "numchat", 200_000)
        p = run(home, "numchat", "carry on")
        ctx = context_of(p)
        expect_clean(p, "label-number")
        check("label-number: the handoff warning fired",
              "THIS CHAT IS NOW" in ctx, repr(ctx[:200]))
        check("label-number: the label format now carries a number and a date",
              "HANDOFF LABEL: <name> -N (" in ctx, repr(ctx[-900:]))
        check("label-number: reads the highest number this thread reached, from the archives",
              "'Context Guard' is at -2 (next: -3)" in ctx, repr(ctx[-900:]))
        check("label-number: counts per thread, so Spotliar's -7 does not leak into it",
              "'Spotliar' is at -7 (next: -8)" in ctx, repr(ctx[-900:]))
        # Asks the HINT, not the whole blob. Measured 18 Sep 2026: "-0" not in ctx failed
        # about one run in twenty-five, because the guard prints the project's memory path
        # and tempfile.mkdtemp had named the throwaway home "flake-03coaq6t" - the "-0" was
        # the random suffix, nothing to do with thread numbering. An absence test over a
        # whole context matches text that was never the subject. [[tests-must-ask-the-code]]
        check("label-number: an unnumbered thread is reported as such, not as -0",
              "(next: -0)" not in ctx and "is at -0" not in ctx, repr(ctx[-900:]))
        check("label-number: the words he is told to type carry the number",
              "just say: Credo Calc -5 (" in ctx, repr(ctx[-900:]))
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
        write_transcript(home, "freshthread", 200_000)
        p = run(home, "freshthread", "carry on")
        ctx = context_of(p)
        expect_clean(p, "first-number")
        check("first-number: says plainly that a new thread starts at -1",
              "starts at -1" in ctx, repr(ctx[-600:]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_a_numbered_short_label_answers_to_its_name():
    """The regression the numbering itself creates, and the reason this is not a one-line
    change. _tokens() drops words under 4 chars, so "EGX bot" has NO strong tokens and
    falls back to requiring the WHOLE label to be named. Add a number and a date and that
    whole label becomes {egx, bot, 2, 16, sep} - typing "EGX bot" is no longer a superset
    and the note goes unreachable, exactly the 14k-note bug fixed on 17 Sep, reintroduced
    by the feature he asked for. The number and date must be stripped before matching."""
    home = make_home({KEY + ".7e5cfde2.md": NOTE_EGX_NUM,
                      KEY + ".143f0320.md": NOTE_SPOT})
    try:
        p1 = run(home, "cf1f81f6-egxnum", "hi")
        check("numbered-short-label: turn 1 was the menu",
              "SAVED THREADS ARE WAITING" in context_of(p1), repr(context_of(p1)[:160]))
        p2 = run(home, "cf1f81f6-egxnum", "EGX bot")
        ctx = context_of(p2)
        expect_clean(p2, "numbered-short-label")
        check("numbered-short-label: the bare name still delivers the note",
              "body-EGXBOT" in ctx, repr(ctx[:200]))
        check("numbered-short-label: left the other one waiting", len(waiting(home)) == 1,
              str(waiting(home)))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_a_numbered_label_answers_when_he_types_the_number_too():
    """The other half: he is being TOLD to type "EGX bot -2", so typing it must work as
    well as typing the name alone. Both, or the instruction contradicts the tool."""
    home = make_home({KEY + ".7e5cfde2.md": NOTE_EGX_NUM,
                      KEY + ".143f0320.md": NOTE_SPOT})
    try:
        run(home, "cf1f81f6-egxfull", "hi")
        p2 = run(home, "cf1f81f6-egxfull", "EGX bot -2")
        ctx = context_of(p2)
        expect_clean(p2, "numbered-label-typed-in-full")
        check("numbered-label-typed-in-full: the name he was quoted delivers its note",
              "body-EGXBOT" in ctx, repr(ctx[:200]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_a_numbered_label_still_ignores_a_stray_word():
    """Stripping the number must not loosen the all-short-words fallback from "every word"
    to "any word". Ordinary talk containing one word of the label still summons nothing."""
    home = make_home({KEY + ".7e5cfde2.md": NOTE_EGX_NUM,
                      KEY + ".143f0320.md": NOTE_SPOT})
    try:
        run(home, "cf1f81f6-numstray", "hi")
        p2 = run(home, "cf1f81f6-numstray", "reboot the bot on the server")
        expect_clean(p2, "numbered-stray-word")
        check("numbered-stray-word: one word of a numbered label summons nothing",
              "body-EGXBOT" not in context_of(p2), repr(context_of(p2)[:160]))
        check("numbered-stray-word: both notes still waiting", len(waiting(home)) == 2,
              str(waiting(home)))
    finally:
        shutil.rmtree(home, ignore_errors=True)




# ------------------------------------ change 5: checkpoints and a hard ceiling
# His point, 17 Sep 2026: "sometimes we need to make it end in a checkpoint as you saw with
# StreamBERT APK it was a very long task that ended up doing tons of tokens". Measured the
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
        write_transcript(home, "nocp", 200_000)
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
    """The change that answers StreamBERT. Past the ceiling the handoff stops being advice:
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

REPORT_LOG = chr(10).join([
    "2026-09-11T10:29:39 reread: flagged probe.png (x3)",
    "2026-09-11T10:30:52 reread: flagged hooktest.png (x3)",
    "2026-09-11T10:31:39 reread: flagged hooktest.png (x4)",
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
        write_big_chat(home, "pausedchat", started, 200_000)
        write_big_chat(home, "otherchat", started, 200_000)
        p = run_pause(home, "pausedchat", "reading", "the", "books")
        expect_clean(p, "pause")
        check("pause: says it paused", "paused" in (p.stdout or ""), repr(p.stdout[:200]))
        quiet = run(home, "pausedchat", "next book please")
        loud = run(home, "otherchat", "next book please")
        expect_clean(quiet, "pause-size")
        check("pause: the paused chat hears nothing at 200k", (quiet.stdout or "").strip() == "",
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
        write_big_chat(home, "resumed", time.time() - 3600, 200_000)
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
        check("report: names what was actually blocked", "probe.png" in out
              and "hooktest.png" in out, repr(out[:900]))
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
        check("report: flags it on the re-read row, not the warnings row",
              "all on one day" in line_with(out, "image re-reads blocked"),
              repr(line_with(out, "image re-reads blocked")))
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
                                              "ledger appends", "image re-reads blocked")),
              repr(out[:900]))
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

    It did, on 17 Sep: he typed 'StreamBERT APK -5 (17 Sep)' at 18:51 and was handed
    'StreamBERT APK -2 (17 Sep)' at 19:27, because label_numbers() only counted labels
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
    he typed 'EGX handover -2 (17 Sep)', the chat was titled 'EGX handover'). Nothing
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
        write_zero_tail_chat(home, "zerotail", time.time() - 3600, 200_000)
        p = run(home, "zerotail", "carry on")
        expect_clean(p, "zero-tail")
        ctx = context_of(p)
        check("zero-tail: still warned about a 200k chat", "200k" in ctx, repr(ctx[:200]))
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
        write_chat_msgs(home, "spotlibbb", started + 10, ["the spotliar thing he asked for"])
        expect_clean(run_stop(home, "guardaaaa"), "tail-a")
        expect_clean(run_stop(home, "spotlibbb"), "tail-b")
        for sid, body in (("guardaaaa", "HANDOFF LABEL: Context Guard -2 (18 Sep)\n\n# n\n"),
                          ("spotlibbb", "HANDOFF LABEL: Spotliar -4 (18 Sep)\n\n# n\n")):
            with open(os.path.join(home, ".claude", "handoff",
                                   KEY + "." + sid[:8] + ".md"), "w", encoding="utf-8") as f:
                f.write(body)
        p = run(home, "freshchat", "Context Guard -2 (18 Sep)")
        expect_clean(p, "tail-split")
        ctx = context_of(p)
        mine = ctx.find("the guard thing he asked for")
        theirs = ctx.find("the spotliar thing he asked for")
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


def run_attribute(home, key=KEY):
    env = child_env(home)
    return subprocess.run([sys.executable, GUARD, "--attribute", key],
                          capture_output=True, text=True, env=env)


def setup_two_threads(home):
    """Two chats, two threads, both their words in an OLD-format ledger."""
    started = time.time() - 7200
    write_chat_msgs(home, "guardaaaa", started, ["the guard thing he asked for"])
    write_chat_msgs(home, "spotlibbb", started + 10, ["the spotliar thing he asked for"])
    write_old_ledger(home, [("2026-09-17 10:00:00", "the guard thing he asked for"),
                            ("2026-09-17 10:01:00", "the spotliar thing he asked for")])
    for sid, body in (("guardaaaa", "HANDOFF LABEL: Context Guard -2 (18 Sep)\n\n# n\n"),
                      ("spotlibbb", "HANDOFF LABEL: Spotliar -4 (18 Sep)\n\n# n\n")):
        with open(os.path.join(home, ".claude", "handoff",
                               KEY + "." + sid[:8] + ".md"), "w", encoding="utf-8") as f:
            f.write(body)


def test_older_entries_get_their_chat_back():
    """After the recovery runs, a pickup sorts the OLD entries by thread too."""
    home = make_home({})
    try:
        setup_two_threads(home)
        a = run_attribute(home)
        check("recover: exited 0", a.returncode == 0, (a.stderr or "")[:200])
        p = run(home, "freshchat", "Context Guard -2 (18 Sep)")
        expect_clean(p, "recover")
        ctx = context_of(p)
        mine = ctx.find("the guard thing he asked for")
        theirs = ctx.find("the spotliar thing he asked for")
        split = ctx.find("OTHER CHATS")
        check("recover: the old entry is now claimed for this thread",
              "THIS THREAD" in ctx and -1 < mine < split,
              "mine=%d split=%d" % (mine, split))
        check("recover: and the other thread's stayed on its own side",
              split > -1 and theirs > split, "theirs=%d split=%d" % (theirs, split))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_attribute_never_edits_the_ledger():
    """The ledger's whole value is that nothing rewrites it. Recovery must be a sidecar."""
    home = make_home({})
    try:
        setup_two_threads(home)
        lp = os.path.join(home, ".claude", "handoff", KEY + ".requests.md")
        with open(lp, "rb") as f:
            before = f.read()
        run_attribute(home)
        with open(lp, "rb") as f:
            after = f.read()
        check("recover: the ledger is byte-for-byte untouched", before == after,
              "%d -> %d bytes" % (len(before), len(after)))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_attribute_refuses_to_guess_when_two_chats_said_it():
    """The words are the only evidence, so identical words in two chats prove nothing.
    Leaving it unattributed is right; putting it in somebody's thread is the bug."""
    home = make_home({})
    try:
        started = time.time() - 7200
        same = "fix the defects please"
        write_chat_msgs(home, "guardaaaa", started, [same])
        write_chat_msgs(home, "spotlibbb", started + 10, [same])
        write_old_ledger(home, [("2026-09-17 10:00:00", same)])
        for sid, body in (("guardaaaa", "HANDOFF LABEL: Context Guard -2 (18 Sep)\n\n# n\n"),
                          ("spotlibbb", "HANDOFF LABEL: Spotliar -4 (18 Sep)\n\n# n\n")):
            with open(os.path.join(home, ".claude", "handoff",
                                   KEY + "." + sid[:8] + ".md"), "w", encoding="utf-8") as f:
                f.write(body)
        run_attribute(home)
        p = run(home, "freshchat", "Context Guard -2 (18 Sep)")
        expect_clean(p, "ambiguous")
        ctx = context_of(p)
        check("ambiguous: the words still reach the new chat", same in ctx)
        check("ambiguous: but were not guessed into this thread",
              ctx.find(same) > ctx.find("OTHER CHATS") > -1,
              "at=%d split=%d" % (ctx.find(same), ctx.find("OTHER CHATS")))
    finally:
        shutil.rmtree(home, ignore_errors=True)


# ------------- change 8: the resumed thread's own words must not scroll off the tail
# Measured 18 Sep 2026 on his real ledger: a pickup of "Credo Calc" or "Spotliar" got 30
# entries and NOT ONE of them belonged to that thread - both had been quiet while the EGX
# and StreamBERT chats filled the shared file. The thread being resumed is exactly the one
# whose requests matter, so it gets its own share of the tail instead of the last 30 overall.
def test_a_quiet_thread_still_gets_its_own_words():
    """A thread that has been quiet while its neighbours talked must still inherit its
    own open requests - that is the entire job of handing the tail over."""
    home = make_home({})
    try:
        started = time.time() - 7200
        write_chat_msgs(home, "guardaaaa", started, ["the quiet thread's open request"])
        write_chat_msgs(home, "spotlibbb", started + 10,
                        ["loud chat message %d" % i for i in range(40)])
        expect_clean(run_stop(home, "guardaaaa"), "quiet-a")
        expect_clean(run_stop(home, "spotlibbb"), "quiet-b")
        for sid, body in (("guardaaaa", "HANDOFF LABEL: Context Guard -2 (18 Sep)\n\n# n\n"),
                          ("spotlibbb", "HANDOFF LABEL: Spotliar -4 (18 Sep)\n\n# n\n")):
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
    tmp = tempfile.mkdtemp(prefix="guardtemp-")
    try:
        sid = "jobschat"
        write_transcript(home, sid, 200_000)
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
# 30-entry window, so "spotliar" - which owns 278 entries - handed a pickup 30 and dropped
# 248 of his own words with no sign they existed. Meanwhile FIVE of the six threads would
# have fitted their ENTIRE history in under 7.3 KB and were being cut by a count that has
# nothing to do with size. A recency window also hides a thread's OLDEST requests, which
# are the ones most likely to be still open - Spotliar's player glitch, asked twice and
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
    return context_of(p)


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
    """Silent truncation is the actual contract violation: a Spotliar pickup had no way to
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
              sorted(e.get("matcher") for e in ours(s["hooks"]["PreToolUse"])) == ["Bash", "Read"],
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
        write_big_chat(home, "compctl", time.time() - 3600, 203_980)
        c = run(home, "compctl", "carry on")
        expect_clean(c, "compaction-rearm/control")
        control = sysmsg(c)
        check("compaction-rearm: CONTROL - a chat this size with no history warns",
              bool(control), repr((c.stdout or "")[:200]))
    finally:
        shutil.rmtree(home, ignore_errors=True)

    home = make_home({})
    try:
        write_big_chat(home, "compacted", time.time() - 3600, 203_980)
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
# never wrote a handoff note at all. Zack opened a fresh chat, typed its label,
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


def test_a_chat_that_never_reaches_the_ceiling_still_leaves_a_stub():
    """The exact failure that prompted this: well under the ceiling, no note, chat ends,
    thread unresumable. After this, ending a turn is enough to leave something behind."""
    home = make_home({})
    try:
        write_big_chat(home, "stubaaa1", time.time() - 3600, 130_000)
        note = os.path.join(home, ".claude", "handoff", KEY + ".stubaaa1.md")
        check("stub: CONTROL - no note exists before the Stop hook runs",
              not os.path.exists(note), note)
        p = run_stop(home, "stubaaa1")
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

    130k is not an arbitrary number: it is where chat "Context Guard -10" actually died,
    just under the 150k first tier, which is why nobody was ever asked for a note."""
    home = make_home({})
    try:
        write_big_chat(home, "earlyaaa", time.time() - 3600, 130_000)
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




# ---------------------------------------------------------------------------
# The away indicator. Measured failure, 19 Sep 2026: Zack typed "Afk on" at
# 20:30 on 18 Sep and seventeen hours later asked, about a DIFFERENT chat,
# "EGX handover -13 (18 Sep) havent told me to change chat why is that". The
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
        write_transcript(home, "arch0004", 200_000)
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

    EGX -21 picked up a 24,089-char note and never archived anything. guard.py's own
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


# ---------------------------------------------------------------- the bootstrap
# His words, 19 Sep 2026: "we need to make sure once we do the 'write Context Guard -14
# (19 Sep) in a new chat' thingy it needs to create a folder then and there and organize
# it automatically can we do that??"
#
# He is not asking for 109 files to be sorted by hand. He is asking that the FIRST chat
# opened in a project's own directory furnish its own memory folder from the shared one.
# A hand-sorted copy would satisfy "do that yes" and miss the request entirely.

BOOT_MANIFEST = {
    "t1_claude_md": ["alsaad-laptop-hardware"],
    "projects": {
        "taza": {"dir": r"D:\AI Projects\Taza", "also": [],
                 "files": ["food-expiry-scanner-app"]},
        "streambert": {"dir": r"D:\AI Projects\StreamBERT APK",
                       "also": [r"D:\AI Projects\StreamBERT PC"],
                       "files": ["streambert-pc-port", "youtubedl-android-traps"]},
    },
    "_craft": ["regex-char-class-range-trap"],
}

BOOT_FILES = {
    "food-expiry-scanner-app": "body-TAZA",
    "streambert-pc-port": "body-SBPC",
    "youtubedl-android-traps": "body-YTDL",
    "alsaad-laptop-hardware": "body-LAPTOP",
    "regex-char-class-range-trap": "body-REGEX",
}

BOOT_INDEX = "\n".join([
    "# Memory Index",
    "- [Taza / food expiry scanner](food-expiry-scanner-app.md) - v0.1.0 beta APK at D:\\AI Projects\\Taza",
    "- [StreamBERT PC port](streambert-pc-port.md) - 5 files differ from the APK tree",
    "- [youtubedl-android traps](youtubedl-android-traps.md) - the progress callback only sees '[' lines",
    "- [Laptop hardware](alsaad-laptop-hardware.md) - HP EliteBook 845 G7, no discrete GPU",
    "- [Regex char-class range trap](regex-char-class-range-trap.md) - it ate table rows for weeks",
]) + "\n"


def make_boot_home(manifest=None, files=None):
    """A fake ~ holding the SHARED memory folder and the manifest, and nothing else."""
    home = tempfile.mkdtemp(prefix="guardboot-")
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
    D--Claude, D--TEST and D--sticker-project---Gemini. Note the triple hyphen: the
    runs are NOT collapsed, and a tidier-looking rule that collapses them produces a
    folder Claude Code will never read. Five of ten project directories were wrong on
    the manifest's first draft; this is the same failure one level down."""
    pairs = [(r"D:\Claude", "D--Claude"),
             (r"D:\TEST", "D--TEST"),
             (r"D:\sticker-project - Gemini", "D--sticker-project---Gemini")]
    man = {"t1_claude_md": [], "_craft": [], "projects": {}}
    for i, (cwd, _key) in enumerate(pairs):
        man["projects"]["p%d" % i] = {"dir": cwd, "also": [],
                                      "files": ["food-expiry-scanner-app"]}
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
        p = boot(home, r"D:\AI Projects\Taza")
        expect_clean(p, "bootstrap/fresh")
        got = boot_memory(home, "D--AI-Projects-Taza")
        check("bootstrap/fresh: the folder was created", got is not None, str(got))
        check("bootstrap/fresh: its memory is listed, and its file stays shared",
              "food-expiry-scanner-app" in (boot_listed(home, "D--AI-Projects-Taza") or [])
              and got is not None and "food-expiry-scanner-app.md" not in got, str(got))
        check("bootstrap/fresh: an index was written",
              got is not None and "MEMORY.md" in got, str(got))
        if got:
            with open(os.path.join(home, ".claude", "projects", "D--AI-Projects-Taza",
                                   "memory", "MEMORY.md"), encoding="utf-8") as f:
                idx = f.read()
            check("bootstrap/fresh: the index line was carried over, not invented",
                  "(food-expiry-scanner-app.md)" in idx, repr(idx[:200]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_bootstrap_gives_the_new_folder_only_its_own_memories():
    """The point of the split. A Taza chat must not inherit StreamBERT's 9 files."""
    home = make_boot_home()
    try:
        boot(home, r"D:\AI Projects\Taza")
        got = [s + ".md" for s in (boot_listed(home, "D--AI-Projects-Taza") or [])]
        # POSITIVE CONTROL. Every other check here asserts an ABSENCE, and an absence is
        # trivially true while the feature does not exist at all - that is how a no-op
        # step reports green. This one line makes the test able to fail.
        check("bootstrap/only-own: its own memory did arrive",
              "food-expiry-scanner-app.md" in got, str(got))
        check("bootstrap/only-own: no other project's memories",
              "streambert-pc-port.md" not in got and "youtubedl-android-traps.md" not in got,
              str(got))
        check("bootstrap/only-own: no craft memories in a project folder",
              "regex-char-class-range-trap.md" not in got, str(got))
        check("bootstrap/only-own: no tier-1 memories in a project folder",
              "alsaad-laptop-hardware.md" not in got, str(got))
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
        p = boot(home, r"D:\AI Projects\Taza")
        ctx = context_of(p)
        check("bootstrap/turn-one: said something", bool(ctx.strip()), repr(ctx[:120]))
        check("bootstrap/turn-one: the index is in context immediately",
              "food-expiry-scanner-app" in ctx, repr(ctx[:400]))
        check("bootstrap/turn-one: names the folder it made",
              "D--AI-Projects-Taza" in ctx, repr(ctx[:400]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


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


def test_bootstrap_never_overwrites_an_existing_memory_folder():
    """A folder that already has memories is HIS. Never write over it."""
    home = make_boot_home()
    try:
        mem = os.path.join(home, ".claude", "projects", "D--AI-Projects-Taza", "memory")
        os.makedirs(mem)
        with open(os.path.join(mem, "MEMORY.md"), "w", encoding="utf-8") as f:
            f.write("# Memory Index\n- [his own](his-own.md) - written by hand\n")
        p = boot(home, r"D:\AI Projects\Taza")
        expect_clean(p, "bootstrap/no-clobber")
        body = open(os.path.join(mem, "MEMORY.md"), encoding="utf-8").read()
        check("bootstrap/no-clobber: his index survived", "his-own.md" in body, repr(body))
        check("bootstrap/no-clobber: nothing was added",
              "food-expiry-scanner-app" not in body, repr(body))
        check("bootstrap/no-clobber: and it said nothing", not context_of(p).strip(),
              repr(context_of(p)[:120]))
        # POSITIVE CONTROL, in the SAME home: a folder that does not exist yet is still
        # furnished. Without this the whole test passes against a feature that is dead.
        boot(home, r"D:\AI Projects\StreamBERT APK", sid="boot0009")
        check("bootstrap/no-clobber: control - a fresh folder IS still furnished",
              "streambert-pc-port" in (boot_listed(home, "D--AI-Projects-StreamBERT-APK") or []),
              str(boot_listed(home, "D--AI-Projects-StreamBERT-APK")))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_bootstrap_is_idempotent():
    """SessionStart fires on every startup AND every resume. It must run exactly once."""
    home = make_boot_home()
    try:
        boot(home, r"D:\AI Projects\Taza", sid="boot0001")
        first = boot_memory(home, "D--AI-Projects-Taza")
        first_list = list_bytes(home, "D--AI-Projects-Taza")
        # POSITIVE CONTROL - "unchanged" is trivially true when the first run did nothing.
        check("bootstrap/idempotent: the first run actually furnished it",
              "food-expiry-scanner-app" in (boot_listed(home, "D--AI-Projects-Taza") or []),
              str(first))
        p2 = boot(home, r"D:\AI Projects\Taza", sid="boot0002")
        expect_clean(p2, "bootstrap/idempotent")
        check("bootstrap/idempotent: the folder is unchanged",
              boot_memory(home, "D--AI-Projects-Taza") == first
              and list_bytes(home, "D--AI-Projects-Taza") == first_list, str(first))
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
        boot(home, r"D:\AI Projects\Taza", sid="boot0008")
        check("bootstrap/unknown: control - a known folder IS furnished",
              "food-expiry-scanner-app" in (boot_listed(home, "D--AI-Projects-Taza") or []),
              str(boot_listed(home, "D--AI-Projects-Taza")))
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
    """StreamBERT lives in two folders and Spotliar in four. `also` is not decoration."""
    home = make_boot_home()
    try:
        p = boot(home, r"D:\AI Projects\StreamBERT PC")
        expect_clean(p, "bootstrap/also")
        got = [s + ".md" for s in (boot_listed(home, "D--AI-Projects-StreamBERT-PC") or [])]
        check("bootstrap/also: the alias folder was furnished too",
              "streambert-pc-port.md" in got and "youtubedl-android-traps.md" in got, str(got))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_bootstrap_survives_a_manifest_entry_with_no_file_on_disk():
    """The manifest is judgement written by hand; a name in it can rot.

    A missing file must not abort the other eight, and must not be silently skipped
    either - a bootstrap that quietly drops memories is worse than one that fails."""
    files = dict(BOOT_FILES)
    del files["food-expiry-scanner-app"]
    home = make_boot_home(files=files)
    try:
        p = boot(home, r"D:\AI Projects\Taza")
        expect_clean(p, "bootstrap/missing-file")
        ctx = context_of(p)
        check("bootstrap/missing-file: it says which name it could not find",
              "food-expiry-scanner-app" in ctx, repr(ctx[:300]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


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
        boot(home, r"D:\AI Projects\Taza", sid="probe001")
        probe, answer = probe_paths(home)
        check("ordering/arm: furnishing a fresh folder arms the probe",
              os.path.isfile(probe), str(sorted(os.listdir(os.path.dirname(probe)))))
        if os.path.isfile(probe):
            with open(probe, encoding="utf-8") as f:
                rec = json.load(f)
            check("ordering/arm: the probe records the session it must read back",
                  rec.get("sid") == "probe001", json.dumps(rec)[:200])
            check("ordering/arm: the probe records the project key it furnished",
                  rec.get("key") == "D--AI-Projects-Taza", json.dumps(rec)[:200])
        check("ordering/arm: nothing is answered yet", not os.path.exists(answer),
              answer)
        # control 1: the same directory a second time - the folder is already there
        if os.path.exists(probe):
            os.remove(probe)
        boot(home, r"D:\AI Projects\Taza", sid="probe002")
        check("ordering/arm: an existing memory folder arms no probe",
              not os.path.exists(probe), "re-armed on a folder it left alone")
    finally:
        shutil.rmtree(home, ignore_errors=True)
    # control 2: a manifest entry whose file has rotted - folder made, nothing copied
    files = dict(BOOT_FILES)
    del files["food-expiry-scanner-app"]
    home = make_boot_home(files=files)
    try:
        boot(home, r"D:\AI Projects\Taza", sid="probe003")
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
            boot(home, r"D:\AI Projects\Taza", sid="ord" + tag)
            probe, answer = probe_paths(home)
            fake_transcript(home, "D--AI-Projects-Taza", "ord" + tag, saw_memory=saw)
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
                  % tag, "D--AI-Projects-Taza" in got, repr(got[:300]))
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
        boot(home, r"D:\AI Projects\Taza", sid="ordwait")
        probe, answer = probe_paths(home)
        boot(home, r"D:\Nowhere At All", sid="later1")      # no transcript at all
        check("ordering/wait: no transcript yet - nothing is claimed",
              not os.path.exists(answer), "answered with no evidence")
        check("ordering/wait: and the probe is kept for later",
              os.path.isfile(probe), "probe thrown away unanswered")
        # a transcript that exists but has not reached a user turn yet
        fake_transcript(home, "D--AI-Projects-Taza", "ordwait", saw_memory=False, turns=0)
        boot(home, r"D:\Nowhere At All", sid="later2")
        check("ordering/wait: an empty transcript is not a 'no'",
              not os.path.exists(answer), "answered off a transcript with no user turn")
        check("ordering/wait: the probe is still waiting",
              os.path.isfile(probe), "probe thrown away unanswered")
        # ...and once the turn arrives it answers normally
        fake_transcript(home, "D--AI-Projects-Taza", "ordwait", saw_memory=True)
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
            json.dump({"sid": "probe001", "key": "D--AI-Projects-Taza"}, f)
        out = run_report(home).stdout
        check("report-ordering: a pending probe is reported as waiting",
              "waiting" in out.lower() and "D--AI-Projects-Taza" in out,
              repr(line_with(out, "aiting")))
        os.remove(os.path.join(state, "ordering-probe.json"))
        with open(os.path.join(state, "ordering-answer.txt"), "w", encoding="utf-8") as f:
            f.write("verdict : same-session\nproject : D--AI-Projects-Taza\n")
        out = run_report(home).stdout
        check("report-ordering: the verdict itself is printed once it lands",
              "same-session" in out, repr(out[-400:]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


# --------------- change 12: the budget tool must count what the pickup actually counts
# Measured 19 Sep 2026 on his real ledger, the same file on the same day:
#   guard.py --report   ->  "ledger: budget dropped 34 of 83 request(s) for this thread"
#   ledger-budget.py    ->  "context guard  43 reqs ... 0 dropped"
#                           "verdict: 12000 is enough today - no thread loses a request."
# ledger_tail() falls back to the .attrib.json sidecar for entries written before the chat
# id went inline; ledger-budget.py did not, so 399 recovered attributions were written off
# as "unattributed". The tool that SIZES the budget was measuring a smaller population
# than the code that spends it, and a verdict from the wrong population is worse than no
# verdict at all: it reads as reassurance. Same family as [[a-no-op-step-reports-green]].
BUDGET = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ledger-budget.py")


def run_budget(home, key=KEY):
    return subprocess.run([sys.executable, BUDGET, key],
                          capture_output=True, text=True, env=child_env(home))


def budget_rows(out):
    """{thread: (reqs, kept)} parsed off the table STRUCTURALLY - the row shape, never a
    sentence. The knee table and the header cannot match: both start with whitespace or
    a non-numeric column."""
    rows = {}
    for line in out.splitlines():
        m = re.match(r"^(\S.*?)\s{2,}(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*$",
                     line)
        if m:
            rows[m.group(1).strip()] = (int(m.group(2)), int(m.group(4)))
    return rows


def budget_unattributed(out):
    m = re.search(r"(\d+) unattributed", out)
    return int(m.group(1)) if m else -1


def append_new_entry(home, ts, sid8, body):
    """An entry in the POST-18-Sep format, which carries its chat id inline."""
    lp = os.path.join(home, ".claude", "handoff", KEY + ".requests.md")
    with open(lp, "a", encoding="utf-8") as f:
        f.write(chr(10) + "### " + ts + " - chat " + sid8 + chr(10) + chr(10)
                + body + chr(10))


def setup_recovered_thread(home):
    """One thread whose words arrived BOTH ways - three before attribution existed and one
    after - plus a neighbour, so a tool that simply claims everything is caught."""
    started = time.time() - 7200
    write_chat_msgs(home, "guardaaaa", started,
                    ["the first guard thing he asked for",
                     "the second guard thing he asked for",
                     "the third guard thing he asked for"])
    write_chat_msgs(home, "spotlibbb", started + 10, ["the spotliar thing he asked for"])
    write_old_ledger(home, [("2026-09-17 10:00:00", "the first guard thing he asked for"),
                            ("2026-09-17 10:01:00", "the second guard thing he asked for"),
                            ("2026-09-17 10:02:00", "the third guard thing he asked for"),
                            ("2026-09-17 10:03:00", "the spotliar thing he asked for")])
    append_new_entry(home, "2026-09-18 09:00:00", "guardbbb",
                     "the fourth guard thing he asked for")
    for sid, body in (("guardaaaa", "HANDOFF LABEL: Context Guard -2 (18 Sep)\n\n# n\n"),
                      ("guardbbbb", "HANDOFF LABEL: Context Guard -3 (18 Sep)\n\n# n\n"),
                      ("spotlibbb", "HANDOFF LABEL: Spotliar -4 (18 Sep)\n\n# n\n")):
        with open(os.path.join(home, ".claude", "handoff",
                               KEY + "." + sid[:8] + ".md"), "w", encoding="utf-8") as f:
            f.write(body)


def test_the_budget_tool_reads_the_recovered_attribution_too():
    """The sidecar is how 399 of his requests know whose they are. A budget measured
    without it is measured on a different ledger than the one that ships."""
    home = make_home({})
    try:
        setup_recovered_thread(home)
        a = run_attribute(home)
        check("budget-attrib: the recovery ran", a.returncode == 0, (a.stderr or "")[:200])
        b = run_budget(home)
        check("budget-attrib: the tool exited 0", b.returncode == 0, (b.stderr or "")[:300])
        rows = budget_rows(b.stdout)
        check("budget-attrib: the recovered entries are counted for their thread",
              rows.get("context guard", (0, 0))[0] == 4,
              "rows=%r" % (rows,))
        check("budget-attrib: and none of them is left unattributed",
              budget_unattributed(b.stdout) == 0,
              "unattributed=%d out=%r" % (budget_unattributed(b.stdout),
                                          b.stdout[:400]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_budget_tool_and_the_pickup_agree_on_the_thread():
    """The check that would have caught this: not a number, but AGREEMENT. Whatever the
    budget tool says a thread keeps is what a real pickup must actually carry."""
    home = make_home({})
    try:
        setup_recovered_thread(home)
        run_attribute(home)
        rows = budget_rows(run_budget(home).stdout)
        kept = rows.get("context guard", (0, 0))[1]
        p = run(home, "freshchat", "Context Guard -3 (18 Sep)")
        expect_clean(p, "budget-agree")
        ctx = context_of(p)
        split = ctx.find("OTHER CHATS")
        mine = ctx[ctx.find("THIS THREAD"):split if split > -1 else len(ctx)]
        carried = mine.count(chr(10) + "### ")
        check("budget-agree: the pickup carries exactly what the tool says it keeps",
              kept > 0 and carried == kept,
              "tool kept=%d, pickup carried=%d" % (kept, carried))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_budget_tool_still_refuses_to_guess_without_the_sidecar():
    """Positive control, and the one that stops the fix being 'claim everything'. With no
    recovery run, those entries have no evidence of an owner and must stay unattributed."""
    home = make_home({})
    try:
        setup_recovered_thread(home)          # deliberately NO run_attribute()
        b = run_budget(home)
        check("budget-noguess: the tool exited 0", b.returncode == 0,
              (b.stderr or "")[:300])
        rows = budget_rows(b.stdout)
        check("budget-noguess: only the inline-attributed entry is claimed",
              rows.get("context guard", (0, 0))[0] == 1, "rows=%r" % (rows,))
        check("budget-noguess: the rest are reported as unattributed, not guessed",
              budget_unattributed(b.stdout) == 4,
              "unattributed=%d" % budget_unattributed(b.stdout))
    finally:
        shutil.rmtree(home, ignore_errors=True)



def test_the_budget_verdict_always_names_a_number():
    """Measured 19 Sep 2026 the moment the attribution fix let the real ledger through:
    the largest thread was 69,706 chars, every rung in the table stopped at 50,000, and
    the tool printed "Nothing is dropped at None." A verdict that cannot name the number
    it is recommending is not a recommendation. The knee is arithmetic - the largest
    thread's own size - so it can be computed rather than searched for."""
    home = make_home({})
    try:
        started = time.time() - 7200
        with open(os.path.join(home, ".claude", "handoff",
                               KEY + ".guardaaa.md"), "w", encoding="utf-8") as f:
            f.write("HANDOFF LABEL: Context Guard -2 (18 Sep)" + chr(10) * 2 + "# n" + chr(10))
        lp = os.path.join(home, ".claude", "handoff", KEY + ".requests.md")
        with open(lp, "w", encoding="utf-8") as f:
            f.write("# What the user actually asked for - his own words" + chr(10))
        for i in range(40):
            append_new_entry(home, "2026-09-1%d 10:00:00" % (i % 9), "guardaaa",
                             "request number %d: " % i + ("word " * 400))
        b = run_budget(home)
        check("budget-knee: the tool exited 0", b.returncode == 0, (b.stderr or "")[:300])
        check("budget-knee: the verdict never says None",
              "at None" not in b.stdout, b.stdout[-200:])
        m = re.search(r"Nothing is dropped at (\d+)", b.stdout)
        check("budget-knee: it names the budget that would drop nothing",
              m is not None, b.stdout[-300:])
        if m:
            knee = int(m.group(1))
            check("budget-knee: and the table proves that budget drops nothing",
                  re.search(r"^\s*%d\s+0\s" % knee, b.stdout, re.M) is not None,
                  "knee=%d, table=%r" % (knee, b.stdout[b.stdout.find("budget"):][:400]))
    finally:
        shutil.rmtree(home, ignore_errors=True)



def test_the_token_column_is_the_cost_of_one_pickup():
    """A pickup injects ONE thread's share, never every thread's at once. Summing all
    threads made the 69,706 rung read "28,968 tokens" while the verdict on the next line
    said "~17,426" - two numbers for one decision, from one run. The column is bounded by
    the budget by construction, so the invariant is checkable without a fixed number."""
    home = make_home({})
    try:
        for sid, lab in (("guardaaa", "Context Guard -2 (18 Sep)"),
                         ("spotliba", "Spotliar -4 (18 Sep)")):
            with open(os.path.join(home, ".claude", "handoff",
                                   KEY + "." + sid + ".md"), "w", encoding="utf-8") as f:
                f.write("HANDOFF LABEL: " + lab + chr(10) * 2 + "# n" + chr(10))
        lp = os.path.join(home, ".claude", "handoff", KEY + ".requests.md")
        with open(lp, "w", encoding="utf-8") as f:
            f.write("# What the user actually asked for - his own words" + chr(10))
        for sid in ("guardaaa", "spotliba"):
            for i in range(25):
                append_new_entry(home, "2026-09-1%d 10:00:00" % (i % 9), sid,
                                 "%s request %d: " % (sid, i) + ("word " * 300))
        b = run_budget(home)
        check("budget-tok: the tool exited 0", b.returncode == 0, (b.stderr or "")[:300])
        rows = re.findall(r"^\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)",
                          b.stdout, re.M)
        check("budget-tok: the knee table has rows to check", len(rows) >= 5,
              "rows=%r" % (rows[:3],))
        bad = [(int(r[0]), int(r[4])) for r in rows
               if int(r[4]) > int(r[0]) // 4 + 1]
        check("budget-tok: no rung claims a pickup bigger than its own budget",
              not bad, "budget/tokens offenders: %r" % (bad,))
    finally:
        shutil.rmtree(home, ignore_errors=True)



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
                               KEY + ".spotliba.md"), "w", encoding="utf-8") as f:
            f.write("HANDOFF LABEL: Spotliar -4 (18 Sep)" + chr(10) * 2 + "# n" + chr(10))
        lp = os.path.join(home, ".claude", "handoff", KEY + ".requests.md")
        with open(lp, "w", encoding="utf-8") as f:
            f.write("# What the user actually asked for - his own words" + chr(10))
        for i in range(2):
            append_new_entry(home, "2026-09-18 10:0%d:00" % i, "guardaaa",
                             "my own request %d" % i)
        # a loud neighbour: far more entries and far more chars than either bound allows
        for i in range(40):
            append_new_entry(home, "2026-09-18 11:%02d:00" % i, "spotliba",
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
        moved = [ln for ln in lines if "(food-expiry-scanner-app.md)" in ln]
        check("bootstrap/pointer: the fixture really has that line to move",
              len(moved) == 1, "lines=%r" % (lines[:6],))
        rest = [ln for ln in lines if "(food-expiry-scanner-app.md)" not in ln]
        with open(idxp, "w", encoding="utf-8") as f:
            f.write(chr(10).join(rest) + chr(10))
        with open(os.path.join(home, ".claude", "context-guard", "projects-index.md"),
                  "w", encoding="utf-8") as f:
            f.write("# Other projects - full index" + chr(10) * 2
                    + chr(10).join(moved) + chr(10))
        p = boot(home, r"D:\AI Projects\Taza")
        expect_clean(p, "bootstrap/pointer")
        with open(os.path.join(home, ".claude", "projects", "D--AI-Projects-Taza",
                               "memory", "MEMORY.md"), encoding="utf-8") as f:
            got = f.read()
        check("bootstrap/pointer: his line was carried over from the pointer file",
              moved[0].strip() in got, repr(got[:300]))
        check("bootstrap/pointer: and it was not replaced by an invented summary",
              got.count("(food-expiry-scanner-app.md)") == 1, repr(got[:300]))
    finally:
        shutil.rmtree(home, ignore_errors=True)



# --------------- change 13: the keyword he types must hand him THAT project's memory
# His words, 20 Sep 2026: "this needs to be automated when the keyword is first written in
# a new chat cause idk how to do that to begin with". He was being told to open a chat in
# D:\AI Projects\EGX-Books-Pipeline so it would load EGX's memories instead of the shared
# index - and being told is the bug. A chat started in D:\Claude gets the D--Claude folder
# whatever thread it is about, because the memory folder keys off the DIRECTORY.
#
# So the label he already types to summon a note now also fetches that project's own
# MEMORY.md. The mapping is explicit in memory-manifest.json ("threads"), never fuzzy:
# "egx bot" and "egx handover" are one project by his review, not by luck.
MANIFEST_THREADS = {
    "_README": "test fixture",
    "projects": {
        "egx": {"dir": "D:\\AI Projects\\EgxScannerzBot", "also": [],
                "threads": ["egx handover", "egx bot"], "files": []},
        "spotliar": {"dir": "D:\\AI Projects\\Spotliar", "also": [],
                     "threads": ["spotliar"], "files": []},
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
    home = make_home({KEY + ".9e19c7ab.md": NOTE_CTX, KEY + ".b0b0b0b0.md": NOTE_EGX})
    try:
        write_threads_manifest(home)
        write_project_index(home, "D--AI-Projects-EgxScannerzBot",
                            "# Memory Index\n- [a](b.md) - INDEX-EGX\n")
        p = run(home, "aa11-label-mem", "EGX bot -3 (20 Sep)")
        ctx = context_of(p)
        expect_clean(p, "label-memory")
        check("label-memory: the note still arrives", "body-EGXBOT" in ctx, repr(ctx[:200]))
        check("label-memory: that project's own index came with it",
              "INDEX-EGX" in ctx, repr(ctx[-400:]))
        check("label-memory: and it says which index it is and where it came from",
              "MEMORY INDEX for egx" in ctx and "D--AI-Projects-EgxScannerzBot" in ctx,
              repr(ctx[-400:]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_a_label_that_maps_to_no_project_appends_nothing():
    """THE CONTROL. Without it, "append every index on disk" passes the test above.

    context-guard is in the manifest with an EMPTY threads list - reviewed and mapped to
    nothing on purpose - and its index sits on disk next to EGX's."""
    home = make_home({KEY + ".9e19c7ab.md": NOTE_CTX})
    try:
        write_threads_manifest(home)
        write_project_index(home, "D--AI-Projects-EgxScannerzBot",
                            "# Memory Index\n- [a](b.md) - INDEX-EGX\n")
        write_project_index(home, "D--Claude-context-guard",
                            "# Memory Index\n- [a](b.md) - INDEX-GUARD\n")
        p = run(home, "bb22-label-none", "context guard")
        ctx = context_of(p)
        expect_clean(p, "label-none")
        check("label-none: the note still arrives", "body-CONTEXTGUARD" in ctx, repr(ctx[:200]))
        check("label-none: no index was appended",
              "INDEX-EGX" not in ctx and "INDEX-GUARD" not in ctx, repr(ctx[-300:]))
        check("label-none: and it said nothing about memory at all",
              "MEMORY INDEX for" not in ctx, repr(ctx[-300:]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_a_project_with_no_memory_folder_is_skipped_not_crashed():
    """Nine `also` directories have no memory folder. Silence, not a traceback."""
    home = make_home({KEY + ".143f0320.md": NOTE_SPOT})
    try:
        write_threads_manifest(home)          # spotliar maps; nothing ever furnished it
        p = run(home, "cc33-label-nofolder", "Spotliar")
        ctx = context_of(p)
        expect_clean(p, "label-nofolder")     # rc 0 and no traceback IS the assertion
        check("label-nofolder: the note still arrives", "body-SPOTLIAR" in ctx, repr(ctx[:200]))
        check("label-nofolder: nothing was invented",
              "MEMORY INDEX for" not in ctx, repr(ctx[-300:]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_project_index_is_not_re_injected_every_turn():
    """It rides the pickup turn only. Anywhere else re-injects the index forever, which
    is the exact bloat this tool exists to remove."""
    home = make_home({KEY + ".b0b0b0b0.md": NOTE_EGX})
    try:
        write_threads_manifest(home)
        write_project_index(home, "D--AI-Projects-EgxScannerzBot",
                            "# Memory Index\n- [a](b.md) - INDEX-EGX\n")
        first = context_of(run(home, "dd44-once", "EGX bot"))
        check("label-once: the first turn carried it", "INDEX-EGX" in first, repr(first[-300:]))
        second = run(home, "dd44-once", "EGX bot")
        check("label-once: the next turn does not",
              "INDEX-EGX" not in context_of(second), repr(context_of(second)[:300]))
        expect_clean(second, "label-once")
    finally:
        shutil.rmtree(home, ignore_errors=True)


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


if __name__ == "__main__":
    for t in (test_handoff_instruction_demands_memory_consolidation,
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
              test_skills_writes_nothing_at_all,
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
              test_harness_placeholders_are_not_recorded_as_his_words,
              test_the_injected_tail_separates_this_thread_from_the_others,
              test_the_tail_still_reads_when_nothing_is_attributed,
              test_older_entries_get_their_chat_back,
              test_attribute_never_edits_the_ledger,
              test_attribute_refuses_to_guess_when_two_chats_said_it,
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
              test_the_budget_tool_reads_the_recovered_attribution_too,
              test_the_budget_tool_and_the_pickup_agree_on_the_thread,
              test_the_budget_tool_still_refuses_to_guess_without_the_sidecar,
              test_the_budget_verdict_always_names_a_number,
              test_the_token_column_is_the_cost_of_one_pickup,
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
              test_a_list_that_changes_during_the_write_is_left_alone):
        print(t.__name__)
        t()
    print()
    if FAILED:
        print("FAILED %d check(s): %s" % (len(FAILED), "; ".join(FAILED)))
        sys.exit(1)
    print("all checks passed")
