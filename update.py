"""Tell people when a newer Context Guard exists, and apply it with one command.

    check()   the one line to show the user, or "". Never raises, never updates anything.
    apply()   what `python install.py --update` runs. Prints each step in plain words.

Context Guard never updates on its own: check() only looks, and apply() only runs when
someone asks. check() touches the network at most once a day (a `git fetch` bounded at 5 s)
and stays silent when offline. Switch it off with {"update_check": false} in
~/.claude/context-guard/config.json, or an empty ~/.claude/context-guard/no-update-check.
"""
import datetime
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
FETCH_TIMEOUT = 5
CHECK_EVERY = 24 * 3600


def _home(home=None):
    return home or os.path.expanduser("~")


def _base(home):
    return os.path.join(_home(home), ".claude", "context-guard")


def _log(home, msg):
    try:
        with open(os.path.join(_home(home), ".claude", "context-audit.log"), "a") as f:
            print(datetime.datetime.now().isoformat(timespec="seconds"), "update.py:", msg, file=f)
    except Exception:
        pass


def _git(clone, *args, timeout=30):
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"          # a credential prompt must never hang a hook
    p = subprocess.Popen(["git", "-C", clone] + list(args), stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, env=env)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        # subprocess.run would kill git.exe only; on Windows its git-remote-http child keeps
        # the pipes open and the hook hangs until that child gives up. Kill the whole tree.
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
        else:
            p.kill()
        try:
            p.communicate(timeout=2)
        except Exception:
            pass
        raise
    return subprocess.CompletedProcess(p.args, p.returncode, out, err)


def _branch(clone):
    """The checked-out branch's name, or "" when HEAD is detached."""
    p = _git(clone, "symbolic-ref", "--quiet", "--short", "HEAD")
    return p.stdout.strip() if p.returncode == 0 else ""


def _is_ancestor(clone, older, newer):
    return _git(clone, "merge-base", "--is-ancestor", older, newer).returncode == 0


def _sha(clone, ref):
    p = _git(clone, "rev-parse", "--verify", "--quiet", ref + "^{commit}")
    return p.stdout.strip() if p.returncode == 0 else ""


def _compare(clone, remote):
    """(behind, rewritten) of HEAD against the commit `remote`, using local objects only."""
    if not remote or not _sha(clone, remote):
        return 0, False
    p = _git(clone, "rev-list", "--count", "HEAD.." + remote)
    behind = int(p.stdout.strip() or 0) if p.returncode == 0 else 0
    if behind == 0:
        return 0, False
    # behind, and HEAD is not an ancestor of the remote: the history was replaced (or
    # this clone holds commits the remote never had). A plain fast-forward is not possible.
    rewritten = not _is_ancestor(clone, "HEAD", remote)
    return behind, rewritten


def _line(clone, behind, rewritten):
    if behind <= 0 and not rewritten:
        return ""
    what = "the history was rebuilt" if rewritten else "%d new change(s)" % behind
    return 'Context Guard update available (%s) - run: python "%s" --update' % (
        what, os.path.join(os.path.abspath(clone), "install.py"))


def _read_state(home):
    try:
        with open(os.path.join(_base(home), "update-check.json"), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _write_state(home, last_check, behind, remote, rewritten=False):
    os.makedirs(_base(home), exist_ok=True)
    with open(os.path.join(_base(home), "update-check.json"), "w", encoding="utf-8") as f:
        json.dump({"last_check": last_check, "behind": behind, "remote": remote,
                   "rewritten": rewritten}, f)


def check(clone=None, home=None, now=None):
    """The update notice to show, or "". Any failure means "" (and a line in the log)."""
    try:
        return _check(clone or HERE, home, time.time() if now is None else now)
    except Exception as e:
        _log(home, "check failed: %r" % (e,))
        return ""


def _switched_off(home):
    """The empty file no-update-check, or {"update_check": false} in config.json."""
    if os.path.exists(os.path.join(_base(home), "no-update-check")):
        return True
    try:
        with open(os.path.join(_base(home), "config.json"), encoding="utf-8-sig") as f:
            data = json.load(f)
        return isinstance(data, dict) and data.get("update_check") is False
    except Exception:
        return False


def _check(clone, home, now):
    if _switched_off(home):
        return ""
    if not os.path.exists(os.path.join(clone, ".git")):
        return ""                              # installed from a zip: nothing to fetch
    if _branch(clone) not in ("", "main"):
        return ""                              # a branch of their own: not ours to compare
    st = _read_state(home)
    last = st.get("last_check")
    if isinstance(last, (int, float)) and 0 <= now - last < CHECK_EVERY:
        # inside the day: no network. Re-measure against the remembered remote commit with
        # local git only, so a `git pull` made since makes the notice disappear at once.
        remote = st.get("remote") or ""
        if remote and _sha(clone, remote):
            behind, rewritten = _compare(clone, remote)
        else:
            behind, rewritten = int(st.get("behind") or 0), bool(st.get("rewritten"))
        return _line(clone, behind, rewritten)
    try:
        p = _git(clone, "fetch", "--quiet", "origin", timeout=FETCH_TIMEOUT)
        ok = p.returncode == 0
    except subprocess.TimeoutExpired:
        ok = False
    remote = _sha(clone, "origin/main") if ok else ""
    if not remote:
        # offline or no origin/main: add no notice, but keep yesterday's if there was one
        _write_state(home, now, int(st.get("behind") or 0), st.get("remote") or "",
                     bool(st.get("rewritten")))
        return ""
    behind, rewritten = _compare(clone, remote)
    _write_state(home, now, behind, remote, rewritten)
    return _line(clone, behind, rewritten)


def apply(dry_run=False, clone=None, home=None):
    """Bring this clone up to date. Prints each step; returns an exit code."""
    try:
        return _apply(dry_run, clone or HERE, home, print)
    except FileNotFoundError:
        print("git was not found. Install git, then run --update again. Nothing was changed.")
        return 1


def _apply(dry_run, clone, home, say):
    if not os.path.exists(os.path.join(clone, ".git")):
        say("This folder is not a git clone (it looks like a downloaded zip), so it cannot")
        say("update itself. Download Context Guard again from")
        say("https://github.com/Masry-code/context-guard and replace this folder.")
        return 1
    st = _git(clone, "status", "--porcelain")
    if st.returncode != 0:
        say("Could not read the state of the clone: " + (st.stderr or "").strip())
        return 1
    changed = [ln for ln in st.stdout.splitlines() if ln.strip()]
    if changed:
        say("REFUSING to update: you have local changes, and nothing was touched:")
        for ln in changed:
            say("  " + ln)
        say("commit or copy them somewhere, then run --update again")
        return 1
    branch = _branch(clone)
    if branch not in ("", "main"):
        say("REFUSING to update: you are on your own branch '%s', and nothing was touched." % branch)
        say("switch back first (git checkout main), then run --update again")
        return 1
    old = _sha(clone, "HEAD")
    if dry_run:
        return _dry_run(clone, old, say)
    say("Fetching the latest Context Guard ...")
    try:
        f = _git(clone, "fetch", "origin", timeout=120)
    except subprocess.TimeoutExpired:
        say("Could not fetch: it took too long. Nothing was changed.")
        return 1
    if f.returncode != 0:
        say("Could not fetch from origin: " + (f.stderr or "").strip())
        say("Nothing was changed.")
        return 1
    remote = _sha(clone, "origin/main")
    if not remote:
        say("Could not fetch: origin has no main branch. Nothing was changed.")
        return 1
    if remote == old or _is_ancestor(clone, remote, "HEAD"):
        say("Context Guard is already up to date")
        return 0
    if _is_ancestor(clone, old, remote):
        say("Fast-forwarding to the newest version ...")
        m = _git(clone, "merge", "--ff-only", "origin/main")
        if m.returncode != 0:
            say("The fast-forward failed: " + (m.stderr or m.stdout or "").strip())
            return 1
        new = _sha(clone, "HEAD")
        say("Updated. What is new:")
        lg = _git(clone, "log", "--format=  - %s", old + ".." + new)
        say(lg.stdout.rstrip())
    else:
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        branch = "context-guard-backup-" + stamp
        say("The history was rebuilt (or this clone has commits of its own).")
        say("Saving your copy first as the branch " + branch + " ...")
        b = _git(clone, "branch", branch, "HEAD")
        if b.returncode != 0:
            say("Could not save the backup branch, so nothing was changed: "
                + (b.stderr or "").strip())
            return 1
        r = _git(clone, "reset", "--hard", "origin/main")
        if r.returncode != 0:
            say("The reset failed: " + (r.stderr or r.stdout or "").strip())
            say("Your copy is safe on the branch " + branch)
            return 1
        say("Now on the newest version. Your old copy is the branch " + branch + ";")
        say("to get back to it: git checkout " + branch)
    return _after_update(clone, home, say)


def _dry_run(clone, old, say):
    say("--dry-run: nothing will be changed, not even git's remote refs.")
    try:
        p = _git(clone, "ls-remote", "origin", "refs/heads/main", timeout=30)
    except subprocess.TimeoutExpired:
        say("Could not reach origin (it took too long).")
        return 1
    if p.returncode != 0 or not p.stdout.split():
        say("Could not reach origin: " + (p.stderr or "no main branch found").strip())
        return 1
    remote = p.stdout.split()[0]
    if remote == old:
        say("Context Guard is already up to date")
        return 0
    if _sha(clone, remote):
        if _is_ancestor(clone, remote, "HEAD"):
            say("Context Guard is already up to date")
        elif _is_ancestor(clone, "HEAD", remote):
            say("Would fast-forward to " + remote[:7] + ", then list the new changes.")
        else:
            say("Would save your copy as a context-guard-backup-<date> branch, then reset "
                "to " + remote[:7] + ".")
    else:
        say("Would fetch " + remote[:7] + " and fast-forward to it. If the history was "
            "rebuilt instead, it would save your copy as a context-guard-backup-<date> "
            "branch first.")
    say("Would then re-link any new hooks and reset the update notice.")
    return 0


def _after_update(clone, home, say):
    say("Re-linking the hooks ...")
    cmd = [sys.executable, os.path.join(clone, "install.py")]
    if home:
        cmd += ["--home", home]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        out = (r.stdout or "").strip()
        if out:
            say(out)
        if r.returncode != 0:
            say("The hook merge reported a problem: " + (r.stderr or "").strip())
            return r.returncode
    except Exception as e:
        say("Could not re-run install.py: %r. Run it yourself: python install.py" % (e,))
        return 1
    try:
        _write_state(home, time.time(), 0, _sha(clone, "HEAD"), False)
    except Exception as e:
        _log(home, "could not reset the notice: %r" % (e,))
    say("Restart Claude Code, or open a new chat, for the update to load.")
    return 0
