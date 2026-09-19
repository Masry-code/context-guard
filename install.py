"""Install Context Guard's hooks into your Claude Code settings.

    python install.py --dry-run     show exactly what would change, write nothing
    python install.py               merge the hooks in, after backing the file up
    python install.py --uninstall   take only Context Guard's hooks back out

It MERGES. Your existing hooks, themes and settings are left alone - the only entries it
touches are its own, which it recognises by the script path rather than by position. Run
it twice and the second run reports "already up to date"; move the folder and re-run and
it repoints the old entries instead of adding a second copy.

Every write is preceded by a timestamped backup next to the file.
"""
import argparse
import datetime
import difflib
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
GUARD = os.path.join(HERE, "guard.py").replace(os.sep, "/")
AUDIT = os.path.join(HERE, "audit.py").replace(os.sep, "/")
PY = sys.executable.replace(os.sep, "/")

# (event, matcher, script, flag, timeout, statusMessage)
HOOKS = [
    ("SessionStart", None, AUDIT, "--alert", 15, "Checking context cost"),
    ("UserPromptSubmit", None, GUARD, "--size", 10, None),
    ("PreToolUse", "Read", GUARD, "--reread", 10, None),
    ("PreToolUse", "Bash", GUARD, "--bash", 10, None),
    ("Stop", None, GUARD, "--ledger", 30, None),
]

# How an entry is recognised as OURS on a re-run or an uninstall. Deliberately the script
# FILENAME and not the full path: someone who moves the folder must get their old entries
# repointed, not silently duplicated - which is the failure mode that makes an installer
# worse than editing the file by hand.
MARKERS = ("guard.py", "audit.py")


def settings_path(home=None):
    return os.path.join(home or os.path.expanduser("~"), ".claude", "settings.json")


def command_for(script, flag):
    return '"%s" "%s" %s' % (PY, script, flag)


FLAGS = ("--alert", "--size", "--reread", "--bash", "--ledger")


def is_ours(entry):
    """True when this hook entry was put there by Context Guard - ANY version, ANY path.

    Matched on the script filename plus one of our flags, never on the full path, so that
    someone who moves or renames the checkout gets their old entries REPOINTED instead of
    silently gaining a second copy that runs the old location."""
    for h in entry.get("hooks") or []:
        cmd = h.get("command") or ""
        if any(m in cmd for m in MARKERS) and any((" " + f) in cmd for f in FLAGS):
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


def build(current, remove=False):
    """Return the settings dict Context Guard wants, merged onto `current`."""
    out = json.loads(json.dumps(current))          # deep copy, never mutate the original
    hooks = out.setdefault("hooks", {})
    for event in sorted(set(e for e, _m, _s, _f, _t, _st in HOOKS)):
        # Drop every entry of OURS for this event and keep everyone else's, in their own
        # order, then re-add ours. That is what makes a second run a no-op instead of a
        # duplicate, and what leaves a friend's own hooks untouched.
        kept = [e for e in hooks.get(event, []) if not is_ours(e)]
        mine = [] if remove else [
            desired_entry(matcher, script, flag, timeout, status)
            for ev, matcher, script, flag, timeout, status in HOOKS if ev == event]
        if kept or mine:
            hooks[event] = kept + mine
        else:
            hooks.pop(event, None)
    if remove and not hooks:
        out.pop("hooks", None)
    return out


def dumps(d):
    return json.dumps(d, indent=2, ensure_ascii=False) + "\n"


def main():
    ap = argparse.ArgumentParser(description="Install Context Guard's Claude Code hooks.")
    ap.add_argument("--dry-run", action="store_true", help="print the diff and write nothing")
    ap.add_argument("--uninstall", action="store_true", help="remove Context Guard's hooks")
    ap.add_argument("--home", default=None, help="treat this directory as the home directory")
    a = ap.parse_args()

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

    wanted = build(current, remove=a.uninstall)
    before, after = dumps(current), dumps(wanted)
    if before == after:
        print("Already up to date - nothing to change in " + path)
        return 0

    diff = "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                        fromfile=path + " (now)", tofile=path + " (after)"))
    print(diff if diff.strip() else "(no textual diff)")
    if a.dry_run:
        print("--dry-run: nothing was written.")
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
    if not a.uninstall:
        print("")
        print("One optional extra: set \"autoCompactWindow\" in that file to your context")
        print("window (e.g. 350000). Context Guard derives its ceiling from it, and without")
        print("it the ceiling falls back to a flat 300k.")
        print("Restart Claude Code, or open a new chat, for the hooks to load.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
