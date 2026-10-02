"""Install Context Guard's hooks into your Claude Code settings.

    python install.py --dry-run     show exactly what would change, write nothing
    python install.py               merge the hooks in, after backing the file up
    python install.py --uninstall   take only Context Guard's hooks back out
    python install.py --update      pull the newest Context Guard into this clone
                                    (add --dry-run to see what it would do first)

It MERGES. Your existing hooks, themes and settings are left alone - the only entries it
touches are its own, which it recognises by the script path rather than by position. Run
it twice and the second run reports "already up to date"; move the folder and re-run and
it repoints the old entries instead of adding a second copy.

Every write is preceded by a timestamped backup next to the file.

The hooks run from an INSTALLED COPY: the runtime files are copied into
~/.claude/context-guard/bin/ and settings.json points there, not at this folder. Editing
the files in this clone changes nothing live until you run install.py again (or
install.py --update, which re-runs it); a re-run copies only the files that differ and
refuses to copy a Python file that does not parse. --uninstall removes the hooks but LEAVES
bin/: chats that are already open keep running their hooks from it, and a missing script
would block every prompt in them. It prints the path to delete by hand once they are restarted.
"""
import argparse
import ast
import datetime
import difflib
import hashlib
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
GUARD = os.path.join(HERE, "guard.py").replace(os.sep, "/")      # the clone's own (source) path
AUDIT = os.path.join(HERE, "audit.py").replace(os.sep, "/")
PY = sys.executable.replace(os.sep, "/")

# Everything the three scripts open beside themselves (TEMPLATE, TOUR_FILE, tour_text's
# {GUARD}/{AUDIT}, load_audit, update_line). A test scans the scripts and fails when one
# is missing from here.
RUNTIME = ("guard.py", "audit.py", "update.py", "handoff-template.md", "tour.md")

# (event, matcher, script, flag, timeout, statusMessage). `script` is a file name in RUNTIME;
# build() turns it into the path inside the installed bin/ folder.
HOOKS = [
    ("SessionStart", None, "audit.py", "--alert", 15, "Checking context cost"),
    # Furnishes a project directory's own memory folder the first time a chat opens there.
    # Without a memory-manifest.json in ~/.claude/context-guard/ this is a silent no-op, so
    # it is safe to ship to someone who has never written one.
    ("SessionStart", None, "guard.py", "--bootstrap", 15, "Setting up project memory"),
    ("UserPromptSubmit", None, "guard.py", "--size", 10, None),
    ("PreToolUse", "Bash", "guard.py", "--bash", 10, None),
    ("Stop", None, "guard.py", "--ledger", 30, None),
    # Leaves the stub note for a chat that ends without writing one (no longer done on Stop).
    ("SessionEnd", None, "guard.py", "--session-end", 10, None),
]


def bin_dir(home=None):
    return os.path.join(home or os.path.expanduser("~"), ".claude", "context-guard", "bin")


def _read(path):
    with open(path, "rb") as f:
        return f.read()


def check_sources():
    """A problem string for the first runtime file in this clone that is missing or is Python
    that does not parse, else "". Run before anything is written."""
    for name in RUNTIME:
        path = os.path.join(HERE, name)
        if not os.path.isfile(path):
            return "%s is missing from %s" % (name, HERE)
        if name.endswith(".py"):
            try:
                ast.parse(_read(path))
            except (SyntaxError, ValueError) as e:
                return "%s does not parse (%s)" % (name, e)
    return ""


def copy_plan(home=None):
    """The RUNTIME names whose bytes in bin/ differ from this clone's (missing counts)."""
    b = bin_dir(home)
    out = []
    for name in RUNTIME:
        dst = os.path.join(b, name)
        if not os.path.isfile(dst) or _read(dst) != _read(os.path.join(HERE, name)):
            out.append(name)
    return out


def source_meta():
    return json.dumps({"clone": HERE,
                       "files": {n: hashlib.sha256(_read(os.path.join(HERE, n))).hexdigest()
                                 for n in RUNTIME}}, indent=2) + "\n"


def meta_current(home=None):
    p = os.path.join(bin_dir(home), "source.json")
    return os.path.isfile(p) and _read(p) == source_meta().encode("utf-8")


def install_copy(home, names):
    """Write each named file (and source.json) into bin/. Every .tmp file is written first and
    only then are they all moved into place, so a failure while writing changes nothing live.
    On OSError: one clean line, the .tmp files are deleted, return 1. Binary, so guard.py
    keeps its CRLF."""
    b = bin_dir(home)
    jobs = [(os.path.join(b, n), _read(os.path.join(HERE, n))) for n in names]
    jobs.append((os.path.join(b, "source.json"), source_meta().encode("utf-8")))
    tmps = []
    try:
        if not os.path.isdir(b):
            os.makedirs(b)
        for path, data in jobs:
            tmp = path + ".tmp"
            tmps.append(tmp)
            with open(tmp, "wb") as f:
                f.write(data)
        for path, _data in jobs:
            os.replace(path + ".tmp", path)
    except OSError as e:
        for tmp in tmps:
            try:
                os.remove(tmp)
            except OSError:
                pass
        print("Could not copy Context Guard into %s (%s). Settings were not changed." % (b, e))
        return 1
    return 0

# How an entry is recognised as OURS on a re-run or an uninstall. Deliberately the script
# FILENAME and not the full path: someone who moves the folder must get their old entries
# repointed, not silently duplicated - which is the failure mode that makes an installer
# worse than editing the file by hand.
MARKERS = ("guard.py", "audit.py")


def settings_path(home=None):
    return os.path.join(home or os.path.expanduser("~"), ".claude", "settings.json")


def command_for(script, flag):
    return '"%s" "%s" %s' % (PY, script, flag)


FLAGS = ("--alert", "--size", "--bash", "--ledger", "--bootstrap", "--session-end")
# An older install wrote these; a re-run (or an uninstall) must take them out.
RETIRED_FLAGS = ("--reread",)


def is_ours(entry):
    """True when this hook entry was put there by Context Guard - ANY version, ANY path.

    Matched on the script filename plus one of our flags, never on the full path, so that
    someone who moves or renames the checkout gets their old entries REPOINTED instead of
    silently gaining a second copy that runs the old location."""
    for h in entry.get("hooks") or []:
        cmd = h.get("command") or ""
        if any(m in cmd for m in MARKERS) and any((" " + f) in cmd for f in FLAGS + RETIRED_FLAGS):
            return True
    return False


def desired_entry(matcher, script, flag, timeout, status):
    hook = {"type": "command", "command": command_for(script, flag), "timeout": timeout}
    if status:
        hook["statusMessage"] = status
    entry = {"hooks": [hook]}
    if matcher:
        entry["matcher"] = matcher
    return entry


def build(current, remove=False, home=None):
    """Return the settings dict Context Guard wants, merged onto `current`. The hook
    commands name the scripts inside <home>/.claude/context-guard/bin/."""
    b = bin_dir(home)
    out = json.loads(json.dumps(current))          # deep copy, never mutate the original
    hooks = out.setdefault("hooks", {})
    # Every event that holds an entry of ours, not only the events in HOOKS: a retired hook
    # may sit under an event we no longer install anything for.
    # Another tool's malformed entry must not turn this scan into a traceback, so a surprise
    # counts as "not ours".
    def holds_ours(lst):
        try:
            return isinstance(lst, list) and any(is_ours(e) for e in lst if isinstance(e, dict))
        except Exception:
            return False
    events = set(e for e, _m, _s, _f, _t, _st in HOOKS) | {
        ev for ev, lst in hooks.items() if holds_ours(lst)}
    for event in sorted(events):
        # Drop every entry of OURS for this event and keep everyone else's, in their own
        # order, then re-add ours. That is what makes a second run a no-op instead of a
        # duplicate, and what leaves a friend's own hooks untouched.
        kept = [e for e in hooks.get(event, []) if not is_ours(e)]
        mine = [] if remove else [
            desired_entry(matcher, os.path.join(b, script).replace(os.sep, "/"), flag, timeout,
                          status)
            for ev, matcher, script, flag, timeout, status in HOOKS if ev == event]
        if kept or mine:
            hooks[event] = kept + mine
        else:
            hooks.pop(event, None)
    if remove and not hooks:
        out.pop("hooks", None)
    return out


TOUR_LINES = ("Open a new chat in Claude Code - it will show you around once.",
              'Type "context guard tour" in any chat to see it again.')


def is_fresh(current):
    """No Context Guard hook in these settings yet - the only install that earns the tour.

    Walks EVERY event, where build() walks only ours, so another tool's malformed entry
    reaches is_ours here first. Any surprise means "not fresh": no tour is the safe side,
    and an install that used to work must not become a traceback."""
    hooks = current.get("hooks")
    if not isinstance(hooks, dict):
        return True
    try:
        return not any(is_ours(e) for entries in hooks.values() if isinstance(entries, list)
                       for e in entries if isinstance(e, dict))
    except Exception:
        return False


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


def dumps(d):
    return json.dumps(d, indent=2, ensure_ascii=False) + "\n"


def _remove_bin(b, own, dry):
    """--uninstall: the hooks come out of settings, but bin/ STAYS. Chats that are already
    open keep running their hooks from it, and a missing script makes `python .../guard.py`
    exit 2 - which blocks every prompt and Bash call in those chats."""
    if not os.path.isdir(b):
        return 0
    print(("would leave " if dry else "Left ") + "the installed copy in place: " + b)
    print("Chats that are already open keep using it until they are restarted.")
    print("Once they have been, delete it by hand: " + b)
    return 0


def main():
    ap = argparse.ArgumentParser(description="Install Context Guard's Claude Code hooks.")
    ap.add_argument("--dry-run", action="store_true", help="print the diff and write nothing")
    ap.add_argument("--uninstall", action="store_true", help="remove Context Guard's hooks")
    ap.add_argument("--home", default=None, help="treat this directory as the home directory")
    ap.add_argument("--update", action="store_true",
                    help="update this clone to the newest Context Guard (never automatic)")
    a = ap.parse_args()

    if a.update:
        sys.path.insert(0, HERE)
        import update
        return update.apply(dry_run=a.dry_run, clone=HERE, home=a.home)

    path = settings_path(a.home)
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                current = json.load(f) or {}
        except ValueError as e:
            print("REFUSING: %s is not valid JSON (%s)." % (path, e))
            print("Fix or move that file first - this installer will not overwrite it.")
            return 2
    else:
        current = {}
        print("note: %s does not exist yet; it will be created." % path)

    b = bin_dir(a.home)
    names = []
    if not a.uninstall:
        problem = check_sources()
        if problem:
            print("REFUSING: " + problem + ". Nothing was copied or changed.")
            return 1
        names = copy_plan(a.home)
    meta_stale = not a.uninstall and not meta_current(a.home)

    wanted = build(current, remove=a.uninstall, home=a.home)
    fresh = not a.uninstall and is_fresh(current)
    before, after = dumps(current), dumps(wanted)
    own_bin = a.uninstall and os.path.isfile(os.path.join(b, "source.json"))
    if before == after and not names and not meta_stale:
        print("Already up to date - nothing to change in " + path)
        if a.uninstall:
            return _remove_bin(b, own_bin, a.dry_run)
        return 0

    if before != after:
        diff = "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                            fromfile=path + " (now)", tofile=path + " (after)"))
        print(diff if diff.strip() else "(no textual diff)")
    if a.dry_run:
        if names:
            print("would copy %d file(s) to %s: %s" % (len(names), b, ", ".join(names)))
        if meta_stale:
            print("would rewrite source.json in " + b)
        if a.uninstall:
            _remove_bin(b, own_bin, True)
        print("--dry-run: nothing was written.")
        return 0

    if names or meta_stale:
        if install_copy(a.home, names):
            return 1
        if names:
            print("refreshed: " + ", ".join(names) + "  (in " + b + ")")
    if before == after:
        return 0
    d = os.path.dirname(path)
    if not os.path.isdir(d):
        os.makedirs(d)
    if os.path.exists(path):
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = path + ".backup-" + stamp
        shutil.copy2(path, backup)
        print("backed up -> " + backup)
    with open(path, "w", encoding="utf-8") as f:
        f.write(after)
    print(("Removed" if a.uninstall else "Installed") + " Context Guard hooks in " + path)
    if a.uninstall:
        return _remove_bin(b, own_bin, False)
    if not a.uninstall:
        print("")
        print("One optional extra: set \"autoCompactWindow\" in that file to your context")
        print("window (e.g. 350000). Context Guard derives its ceiling from it, and without")
        print("it the ceiling falls back to a flat 320k.")
        print("Restart Claude Code, or open a new chat, for the hooks to load.")
        if fresh and flag_tour(a.home):
            print("")
            for line in TOUR_LINES:
                print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
