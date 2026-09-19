"""Live context guards for Claude Code, wired as hooks.

--size    UserPromptSubmit : measures THIS chat's live context and warns when it
                             crosses a threshold (once per threshold, no nagging).
--reread  PreToolUse/Read  : stops re-reading an image whose bytes have not
                             changed since it was already read in this chat.
"""
import json, sys, os, glob, hashlib, datetime, re, time, calendar, shlex

HOME = os.path.expanduser("~")
PROJECTS = os.path.join(HOME, ".claude", "projects")
STATE = os.path.join(HOME, ".claude", "context-guard")
LOG = os.path.join(HOME, ".claude", "context-audit.log")

# Context thresholds in tokens. Measured 2026-09-11: a turn at 316k costs about
# 4x the same turn at 73k, and total cost is (context size) x (turn count).
LEVELS = [
    # 100k measured 19 Sep 2026. Chat "Context Guard -10" ran 19 hours, peaked at
    # 132k and so sat UNDER the 150k tier for its whole life - it was never once
    # asked for a handoff note, and the thread became unresumable. A tier nothing
    # reaches is not a tier.
    (100_000, "worth handing off"),
    (150_000, "getting expensive"),
    (250_000, "expensive"),
    (320_000, "very expensive"),
]
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
FREE_REREADS = 2   # allow this many identical reads (covers a post-compaction re-read)
# How much of a handoff note to inject. ~40k chars is ~10k tokens - cheap against a
# 110k context, and worth it. The old 6k cap silently cut a real 17,672-char note
# off mid-word, which is exactly how detail gets lost. Past this we point at the file
# instead of truncating in silence.
NOTE_CHARS = 40_000
TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "handoff-template.md")
# Re-warn after this much FURTHER growth. Warning once per level left session
# 143f0320 parked at ~211k in silence forever: it is over 150k but never reaches
# 250k, so the old "already warned at this level" check muted it permanently.
REWARN_STEP = 40_000
# A handoff note is addressed to a FRESH chat. Sessions in one project share a single
# note slot, so without a freshness gate a big old chat in the same folder swallows the
# note meant for someone else. Measured 11 Sep: this session, sitting at 213k, ate the
# note the Spotliar chat had written seconds earlier. A fresh chat's floor is ~73k.
FRESH_CTX = 120_000
# ...and live context ALONE is not enough. Compaction drops a 300k chat back to ~30k,
# so an expensive old session looks brand new for one turn. Measured 11 Sep 20:04: this
# session, 456 turns and 5.4 MB deep, was compacted and immediately swallowed the
# corrected Spotliar note - the THIRD time that note went to the wrong chat. A chat's own
# transcript size cannot be reset by compaction, a resume, or a failed clear, so gate on
# it too. A genuinely fresh chat is a few hundred KB; 1.5 MB is already many turns in.
FRESH_BYTES = 1_500_000
LEDGER_MAX_CHARS = 2000   # longer "user messages" are pasted skill bodies, not requests
LEDGER_TAIL = 30          # how many recent requests to inject alongside a handoff note
LEDGER_OTHERS = 15        # ...of which this many may come from the OTHER threads sharing
                          # the folder, once the resumed thread has been given its own
# A COUNT was the wrong unit for the resumed thread's own share. Measured 18 Sep 2026 over
# his real 426-entry ledger: FIVE of the six threads fitted their ENTIRE history in under
# 7.3 KB and were being cut at 30 anyway, while "spotliar" owns 278 and was handed 30 -
# 248 of his own words dropped, in silence, with nothing to say they existed. Budget the
# share in CHARACTERS instead: a thread that fits inherits all of it, and one that does
# not is told what was left out and how to find it.
# 10,000 was judgement and stayed unmeasured for eight chats. Measured 19 Sep 2026 against
# the real 573-entry ledger: of the three attributed threads only ONE overflowed - "context
# guard" itself, 38 requests / 10,933 chars, 5 of his own requests pushed into the "... N
# more ..." line. 12,000 drops nothing at all for any thread today (115 of 115 kept, 25,627
# chars across the folder) and costs at most 2,000 extra chars - about 500 tokens - on the
# one turn a pickup happens. The thread that was losing requests is the one whose oldest
# requests keep turning out to still be open, so the trade is the right way round.
LEDGER_OWN_CHARS = 20000    # the resumed thread's share of the injection
# 19 Sep 2026, chosen from the real 505-request ledger once ledger-budget.py could see
# all of it. Not the no-drop knee: that is 69,706 chars, ~17.4k tokens on turn one of
# every pickup, which would make this tool the bloat it exists to remove. Picked on
# MARGINAL value instead - requests rescued per 1,000 extra tokens of injection:
#     12,000 -> 20,000   +1.9k tokens, 63 requests   33 per 1k   <- best
#     20,000 -> 30,000   +2.6k tokens, 30 requests   12 per 1k
#     30,000 -> 50,000   +5.0k tokens, 82 requests   16 per 1k
#     50,000 -> 69,706   +5.0k tokens, 84 requests   17 per 1k
# 20,000 is where each extra token buys the most of his own words back; past it the
# curve flattens and then only pays off by buying the whole tail at once.
LEDGER_OTHERS_CHARS = 4000  # ...and the neighbours', once the thread has taken its own
LEDGER_OLDEST_SHARE = 0.2   # of a thread's budget, reserved for its OLDEST entries -
                            # a newest-first window is structurally blind to exactly the
                            # requests most likely to still be open.
# Drop this file in STATE and the Stop hook stops checking that memories were saved.
# He approved the check on 17 Sep 2026 and asked for the switch in the same breath -
# his standing rule is that anything automated leaves him an override.
NAG_OFF = "no-memory-nag"
# ...and a hard cap on top of stop_hook_active. That flag is something THIS build happens
# to send; the design must not rest on it. A chat that decides there is genuinely nothing
# durable to save never changes a memory mtime, so an uncapped nag would block its own Stop
# forever and burn his tokens doing it.
NAG_MAX = 2
# Past this, handing off stops being the chat's decision. Measured 17 Sep 2026: the
# StreamBERT chat ran 524 turns / 82M tokens re-read straight through repeated warnings,
# because clause (c) ("you are mid-build, finish it") is always true during a long build;
# and 6d115b8e, born 22 Jul, peaks at 859k and was STILL being written on 17 Sep. Warning
# is not retiring. The ceiling sits above the top warning level so it stays a last
# resort, not a second warning - if it fires on ordinary chats he will switch it off.
#
# ...but it must also sit BELOW the point the app compacts at, and that is what the
# first version got wrong. Measured 17 Sep 2026: his autoCompactWindow is 350000 and
# Claude Code compacts at 91% of it (318,500), so a hardcoded 350k ceiling was above
# the roof. Across 49 transcripts not ONE session since that setting existed has ever
# reached 320k - the highest modern peak is 317,619 - and the ceiling fired 0 times in
# six days. Compaction then drops the meter back to ~30k, so a chat that slips past is
# never caught at all. Derive it from his own settings so a window change cannot strand
# it above the roof a second time; CEILING_CAP keeps it a last resort on a big window.
CEILING_CAP = 300_000       # never higher than this, however large the window
CEILING_HEADROOM = 18_000   # room to actually write the note before compaction hits


def _ceiling():
    """The ceiling, kept under the point the window auto-compacts at."""
    try:
        with open(os.path.join(HOME, ".claude", "settings.json"), encoding="utf-8") as f:
            window = int((json.load(f) or {}).get("autoCompactWindow") or 0)
    except Exception:
        window = 0      # no settings file, or unreadable - fall back to the flat cap
    if window > 0:
        return min(CEILING_CAP, int(window * 0.91) - CEILING_HEADROOM)
    return CEILING_CAP


CEILING = _ceiling()
CEILING_MAX = 3            # a Stop hook that blocks forever is a chat he cannot end
CEILING_OFF = "no-ceiling"  # his override, same shape as NAG_OFF
# AWAY MODE. His words, 18 Sep 2026: "we need to have an exception if it was ever addresed
# that am currently remote/afk from the console and responding from the phone on claude
# mobile app so it would understand that i cant open a new session thus the naggin should
# stop until i return". Every warning this tool prints ends in the same instruction - open
# a new chat and type this label - and on a phone that instruction cannot be followed. So
# while he is away the chat still SAVES (he chose "save silently, never nag": a chat that
# dies unattended with no note is the exact disaster this tool exists to prevent) and only
# the part he cannot act on is withheld.
#
# He chose EXPLICIT toggling over sniffing his sentences, so the WHOLE message must be the
# phrase. "fix this before i go afk" must never arm it - a guard that silently switches
# itself off mid-sentence is worse than no guard, because he would never know.
AWAY_FLAG = "away"
# Written already normalised: lowercase, punctuation stripped, so "I'm AFK!" -> "i m afk".
AWAY_ON = ("afk", "afk on", "im afk", "i m afk", "afk mode", "going afk",
           "on my phone", "im on my phone", "i m on my phone", "on the phone",
           "on mobile", "im on mobile", "i m on mobile", "remote", "im remote", "i m remote")
# "back" is only read as a toggle while away mode is ALREADY on - otherwise an ordinary
# "back" (go back, revert that) would be swallowed as a command he never issued.
AWAY_OFF = ("back", "im back", "i m back", "afk off", "away off", "afk done",
            "back at the pc", "im at the pc", "i m at the pc", "im here", "i m here")
# --skills scan. A repeat inside ONE chat is somebody iterating; the same shape turning up
# in several chats is a workflow being re-derived from scratch every time, which is the
# thing a skill actually fixes. Ranked by runs x distinct chats, his rule from 17 Sep 2026.
SKILL_MIN_CHATS = 2
SKILL_MIN_RUNS = 3
SKILL_TOP = 6
# Scanning must never cost more than it saves: his transcripts run to 321 MB and the
# UserPromptSubmit hook is killed at 10 seconds, which would silently lose the whole
# handoff warning. Read only the tail of each file, newest first, under a wall-clock budget.
SKILL_TAIL_BYTES = 4_000_000
SKILL_SCAN_BUDGET = 24_000_000
SKILL_SCAN_SECONDS = 3.0
# Looking around a folder is how every chat starts and is never a workflow worth a skill.
# Measured 17 Sep 2026 on 12 of his real transcripts, the first scan proposed exactly this:
# "python" 122 runs, "grep" 23, "cat" 17, "ls" 10, "echo" 7 - and "sam" 23, which is not
# a command at all but the front half of C:/Users/Sam Rivera/... after the unquoted space in
# his own username. A counter is only as good as the event under it.
LOOK_AROUND = {"cat", "ls", "dir", "echo", "cd", "head", "tail", "wc", "find", "grep", "rg",
               "sed", "awk", "cp", "mv", "rm", "mkdir", "touch", "chmod", "which", "type",
               "more", "less", "diff", "file", "stat", "sort", "uniq", "tr", "cut", "pwd",
               "export", "set", "printf", "date", "du", "df", "tree", "sleep", "clear"}
# a bare interpreter name says nothing; a script run by path with only flags after it does
SCRIPT_EXT = (".sh", ".py", ".bat", ".cmd", ".ps1", ".exe", ".js", ".ts")
CMD_NAME = re.compile(r"[a-z0-9._+-]+$")
MIDTURN = "The user sent a new message while you were working:"
SKILL_MARKERS = ("Base directory for this skill", "Path: bundled:", "Path: plugin:",
                 "<command-message>", "<local-command")
# machine-generated text that arrives on the user channel but that he never typed
NOISE = ("<task-notification>", "<ci-monitor-event>", "<summary>", "<system-reminder>",
         "Background command", "Caveat: The messages below were generated")


def log(msg):
    try:
        with open(LOG, "a") as f:
            print(datetime.datetime.now().isoformat(timespec="seconds"), msg, file=f)
    except Exception:
        pass


def state_path(sid):
    os.makedirs(STATE, exist_ok=True)
    return os.path.join(STATE, (sid or "unknown")[:40] + ".json")


def load_state(sid):
    try:
        with open(state_path(sid)) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(sid, st):
    try:
        with open(state_path(sid), "w") as f:
            json.dump(st, f)
    except Exception:
        pass


def checkpoint_path(sid):
    os.makedirs(STATE, exist_ok=True)
    return os.path.join(STATE, (sid or "unknown")[:40] + ".checkpoints")


def add_checkpoint(sid, text, ctx):
    """One finished milestone. Recorded with the CONTEXT SIZE it was reached at, not a
    turn number: "how long ago" is the only interesting question about a checkpoint, and
    context is the number that maps to what it cost."""
    line = "\t".join([datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                      str(int(ctx)), " ".join(str(text).split())])
    with open(checkpoint_path(sid), "a", encoding="utf-8") as f:
        f.write(line + chr(10))
    return line


def last_checkpoint(sid):
    """(context it was reached at, what was finished) - or None if this chat has never
    recorded one, which is itself the thing worth saying out loud."""
    try:
        with open(checkpoint_path(sid), encoding="utf-8", errors="replace") as f:
            rows = [l.rstrip(chr(10)) for l in f if l.strip()]
    except Exception:
        return None
    for row in reversed(rows):
        parts = row.split("\t")
        if len(parts) >= 3:
            try:
                return int(parts[1]), parts[2]
            except Exception:
                continue
    return None


def checkpoint_note(sid, ctx):
    """What the handoff warning says about clean breaks.

    His point, 17 Sep 2026: "sometimes we need to make it end in a checkpoint as you saw
    with StreamBERT APK it was a very long task that ended up doing tons of tokens". The
    guard measures SIZE; it cannot see whether anything is half-done. Only the chat knows
    that, so the chat records it - and the ABSENCE is reported loudly, because a quiet
    "last checkpoint: none" is exactly how a counter ends up measuring nothing at all."""
    cmd = ('python "' + os.path.abspath(__file__) + '" --checkpoint ' + str(sid or "")
           + ' "<what is now finished>"')
    last = last_checkpoint(sid)
    if not last:
        return (" THIS CHAT HAS RECORDED NO checkpoint, so there is no clean break to hand "
                "off at and nothing to tell the next chat where the work stands. RECORD A "
                "CHECKPOINT the moment anything is finished - one Bash call, it writes "
                "nothing else and needs no permission: " + cmd + " ")
    cctx, text = last
    return (" Last checkpoint: '" + text + "', recorded at " + str(round(cctx / 1000))
            + "k - " + str(round(max(0, ctx - cctx) / 1000)) + "k of context ago. HAND OFF "
            "AT THE NEXT ONE rather than at a byte count: finish what is in flight, RECORD "
            "A CHECKPOINT for it (" + cmd + "), and hand off there. ")


def cmd_checkpoint():
    """python guard.py --checkpoint <session-id> <what is now finished>

    Called by Claude from Bash, never by a hook, so there is no stdin payload and the
    transcript has to be found from the session id alone."""
    a = [x for x in sys.argv[1:] if x != "--checkpoint"]
    if not a:
        print("usage: guard.py --checkpoint <session-id> <what is now finished>")
        return
    sid, text = a[0], " ".join(a[1:]).strip()
    if not text:
        print("usage: guard.py --checkpoint <session-id> <what is now finished>")
        return
    path = find_transcript(sid, None)
    ctx = live_context(path) if path else 0
    add_checkpoint(sid, text, ctx)
    log("checkpoint at %d: %s" % (ctx, text))
    print("checkpoint recorded at %dk: %s" % (round(ctx / 1000), text))


def read_stdin():
    try:
        return json.loads(sys.stdin.read() or "{}")
    except Exception:
        return {}


def virtual_transcript(d, sid):
    """The path a brand-new chat WILL have.

    Measured 11 Sep 2026 20:41 and 20:42: UserPromptSubmit fires BEFORE the transcript file
    exists, so `find_transcript` returned None and the hook bailed with "no transcript found"
    - on the first message of a fresh chat, which is the one moment the whole handoff design
    depends on. He started a new chat, typed the label, and got nothing. Only the project key
    is actually needed to find his notes, and cwd gives that without any file on disk."""
    given = d.get("transcript_path")
    if given:
        return given          # may not exist yet; size/ctx then read as 0, i.e. fresh
    cwd = d.get("cwd") or os.getcwd()
    key = "".join(c if c.isalnum() else "-" for c in cwd)
    return os.path.join(PROJECTS, key, (sid or "unknown") + ".jsonl")


def find_transcript(sid, given):
    if given and os.path.exists(given):
        return given
    hits = glob.glob(os.path.join(PROJECTS, "*", (sid or "") + ".jsonl"))
    return hits[0] if hits else None


def handoff_dir():
    d = os.path.join(HOME, ".claude", "handoff")
    os.makedirs(d, exist_ok=True)
    return d


def project_key(transcript_path):
    """D--Claude, from the transcript's parent folder."""
    return os.path.basename(os.path.dirname(transcript_path)) or "unknown"


def memory_dir(transcript_path):
    """Where this project's memories live: ~/.claude/projects/<KEY>/memory/, with
    MEMORY.md as the index.

    That index is loaded into EVERY turn of EVERY chat, which is why the handoff step
    insists on consolidating into an existing file instead of always adding a new one.
    Measured 17 Sep 2026: MEMORY.md was already 14,996 bytes / ~3,749 tokens per turn,
    with 69 files and 377,355 bytes behind it. A memory step that only ever ADDS raises
    the exact floor this whole tool exists to lower."""
    return os.path.join(PROJECTS, project_key(transcript_path), "memory")


def session_start_ts(path):
    """When this chat began, taken from the FIRST transcript record's own timestamp.

    NOT ctime. ctime means "created" on Windows but "inode last changed" on Linux, and
    for a file being appended to all session long that is just "now" - every memory file
    would then look older than the session and the nag below would fire forever. The
    record's own ISO timestamp is the same on every platform. Returns None when the
    session cannot be dated at all, and the caller then stays QUIET and logs it rather
    than guessing - an unreadable transcript must not turn into a permanent nag."""
    try:
        with open(path, "rb") as f:
            head = f.readline()
        ts = (json.loads(head.decode("utf-8", "replace")) or {}).get("timestamp")
        if ts:
            return calendar.timegm(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        pass
    try:
        return os.path.getctime(path)
    except Exception:
        return None


def memory_touched_since(transcript_path, since):
    """Did anything in this project's memory folder actually change during the session?

    Files AND the folder itself: consolidating by merging one memory into another and
    deleting the loser leaves no newer FILE behind, only a newer directory. Missing
    folder = nothing was ever saved."""
    d = memory_dir(transcript_path)
    try:
        if os.path.getmtime(d) > since:
            return True
    except Exception:
        return False
    for root, _dirs, files in os.walk(d):
        for n in files:
            try:
                if os.path.getmtime(os.path.join(root, n)) > since:
                    return True
            except Exception:
                continue
    return False


def chat_wrote_memory(transcript_path):
    """Did THIS chat write to the memory folder? True / False / None if unreadable.

    memory_touched_since() answers a different question - "did ANYONE touch the shared
    folder" - and with five chats live in one folder a neighbour's save silences this
    chat's nag. The transcript knows who actually wrote, so ask it.

    POSITIONAL on purpose, and this is the part that must not be simplified: the memory
    folder's path also appears in the hook's OWN instruction text, which is in the
    transcript too, so a grep over the file would call every chat a saver. Only a
    tool_use block counts - a Write/Edit with a file_path inside the folder, or a Bash
    command that names it (a prune script writes there without a file_path)."""
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except Exception:
        return None
    mdir = os.path.normcase(memory_dir(transcript_path))
    for line in lines:
        if "tool_use" not in line:
            continue                    # cheap reject before the JSON cost
        try:
            rec = json.loads(line)
        except Exception:
            continue
        content = ((rec.get("message") or {}).get("content")
                   if isinstance(rec.get("message"), dict) else None)
        for block in (content if isinstance(content, list) else []):
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            inp = block.get("input") if isinstance(block.get("input"), dict) else {}
            for key in ("file_path", "path", "notebook_path", "command"):
                val = inp.get(key)
                if isinstance(val, str) and mdir in os.path.normcase(val):
                    return True
    return False


def handoff_path(transcript_path, sid=None):
    """Where THIS chat writes its note - ONE FILE PER CHAT.

    Measured 11 Sep 2026: every chat of his lives in the single project folder D--Claude
    (Spotliar, Credo Calc and the context-guard work all run with cwd D:/Claude). With one
    shared slot, whichever chat handed off second silently destroyed the first one's note.
    The Spotliar note was sitting unclaimed when the Credo chat was about to overwrite it."""
    key = project_key(transcript_path)
    name = key + ("." + str(sid)[:8] + ".md" if sid else ".md")
    return os.path.join(handoff_dir(), name)


# Written into every auto-generated note so the next chat knows it is reading
# machine output, not a curated handoff - and so write_stub() can tell its own
# earlier output apart from a real note and refresh it without destroying prose.
STUB_MARKER = "context-guard:auto-stub"


# The marker is POSITIONAL, and that is not fussiness. MEASURED IN THE WILD 19 Sep
# 2026, and it destroyed a real artefact: a curated 287-line handoff note told the next
# chat to grep the folder for this marker, so the string appeared in its BODY. A bare
# substring test over the whole file then classified that note as machine output,
# write_stub() replaced it with 21 lines, and the ceiling fired saying no note existed -
# which by then was true. Three symptoms, one cause: a guard that read prose instead of
# structure. write_stub() writes the tag as an HTML comment near the top, so only the
# top is ever consulted.
STUB_TAG = "<!-- " + STUB_MARKER + " -->"
STUB_HEAD_LINES = 6


def is_stub(p):
    """True only for a file THIS tool generated, judged by its opening lines alone.

    Anything further down is prose, and prose is allowed to talk about the marker."""
    try:
        with open(p, encoding="utf-8") as f:
            for i, line in enumerate(f):
                if i >= STUB_HEAD_LINES:
                    break
                if STUB_TAG in line:
                    return True
    except Exception:
        return False        # unreadable: assume it is a real note, never gamble
    return False


def real_note(p):
    """A note a CHAT wrote, as opposed to one this tool generated.

    The distinction is load-bearing in both directions: a stub must never satisfy the
    ceiling (or the ceiling silently stops working the day stubs ship), and it must
    never satisfy the memory nag (or the chat is scolded about a note no one wrote)."""
    return os.path.exists(p) and not is_stub(p)


def write_stub(transcript_path, sid, ctx):
    """Leave SOMETHING behind for a chat that never handed off.

    His words, 19 Sep 2026: "fix this issue i never want for this to happen again".
    The ceiling has never fired in ten chats, so a note written only at the ceiling
    is a note essentially never written. This runs on every Stop instead, costs
    nothing, and needs no model: facts only, clearly labelled as facts only.

    It NEVER touches a real note. A curated note is worth far more than this, and a
    stub that could overwrite one would destroy the thing it exists to protect."""
    p = handoff_path(transcript_path, sid)
    if real_note(p):
        return False      # a note a chat wrote. NEVER touch it - one predicate, shared
                          # with the ceiling, so the two can never disagree about what
                          # counts as a real note.
    today = datetime.date.today().isoformat()
    body = [
        "HANDOFF LABEL: Unsaved chat " + str(sid)[:8] + " (" + today + ")",
        "",
        "<!-- " + STUB_MARKER + " -->",
        "",
        "# Automatic stub - NOT a curated handoff note",
        "",
        "This chat ended without writing a real note, so the guard wrote this one",
        "mechanically. It records WHAT and WHERE, and it cannot record WHY: nothing",
        "here says what was decided, what is half-done, or what the user asked for",
        "and did not get. Read it as a pointer, not as a handoff.",
        "",
        "- session id: " + str(sid),
        "- project key: " + project_key(transcript_path),
        "- context at close: " + str(round((ctx or 0) / 1000)) + "k",
        "- stub written: " + datetime.datetime.now().isoformat(timespec="seconds"),
        "- transcript: " + str(transcript_path),
        "- the user's own words, in order: " + ledger_path(transcript_path),
    ]
    last = last_checkpoint(sid)
    if last:
        body.append("- last checkpoint: '" + str(last[1]) + "' at "
                    + str(round((last[0] or 0) / 1000)) + "k")
    else:
        body.append("- last checkpoint: NONE recorded, so there is no known clean break")
    body += [
        "",
        "**First thing to do:** read the ledger above - it is verbatim and complete -",
        "then ask the user what he wants from this thread rather than guessing from it.",
        "",
    ]
    try:
        d = handoff_dir()
        if not os.path.isdir(d):
            os.makedirs(d)
        with open(p, "w", encoding="utf-8") as f:
            f.write(chr(10).join(body))
        log("stub: wrote " + p + " at ctx=" + str(ctx))
        return True
    except Exception as e:
        log("stub: could not write " + p + ": " + str(e))
        return False


def waiting_notes(transcript_path):
    """Every unclaimed note for this project, oldest first. Matches the legacy flat
    <KEY>.md as well as the per-chat <KEY>.<sid8>.md, so nothing already written is lost."""
    key = project_key(transcript_path)
    out = []
    # NOT key + "*.md": that would hand project "D--Claude" the notes of a future
    # "D--Claude-2". Require a literal dot after the key, plus the legacy flat name.
    for f in (glob.glob(os.path.join(handoff_dir(), key + ".*.md"))
              + glob.glob(os.path.join(handoff_dir(), key + ".md"))):
        b = os.path.basename(f)
        if ".used" in b or b.endswith(".requests.md"):
            continue
        if f not in out:
            out.append(f)
    return sorted(out, key=lambda f: os.path.getmtime(f))


def live_context(path):
    """Current context size = the last recorded turn's cache read + cache write.
    0 when the transcript does not exist yet - a brand-new chat, the freshest there is."""
    try:
        size = os.path.getsize(path)
    except Exception:
        return 0
    with open(path, "rb") as f:
        f.seek(max(0, size - 400_000))
        chunk = f.read().decode("utf-8", "replace")
    for line in reversed(chunk.splitlines()):
        try:
            u = (json.loads(line).get("message") or {}).get("usage") or {}
        except Exception:
            continue
        if u:
            v = (u.get("cache_read_input_tokens", 0) or 0) + (u.get("cache_creation_input_tokens", 0) or 0)
            # An all-zero record is NOT a reading. Measured 17 Sep 2026 across his 49
            # transcripts: 3 of them end on a turn reporting cache_read=0,
            # cache_creation=0, input=0, output=0 - the app writes one for an aborted or
            # empty turn - and because the `usage` key IS present the scan stopped there
            # and returned 0. Session 7e5cfde2 was holding 225,915 tokens and reported
            # "brand new chat". That silences the size warning, the ceiling and the
            # checkpoint line at once, and - worse - lets a big chat pass the FRESH_CTX
            # gate and swallow a note meant for a fresh one, which is the exact failure
            # FRESH_CTX exists to stop. Keep walking back to a turn that measured something.
            if v:
                return v
    return 0


def dropped_clear(path):
    """A clear_session call sitting in the CURRENT transcript means it never took effect -
    a clear that works starts a fresh transcript, so the call would not be in this one.

    Measured 11 Sep 2026: the Spotliar chat called clear_session at 12:42:05 and announced
    it ("Cleared - see you on the other side"). Zack typed into the window before the
    session went idle, which silently DROPS a queued clear. The chat carried straight on,
    247k -> 268k, with everyone believing the handoff had happened."""
    try:
        size = os.path.getsize(path)
    except Exception:
        return None
    start = max(0, size - 3_000_000)
    found = None
    with open(path, "rb") as f:
        f.seek(start)
        if start:
            f.readline()      # discard the partial line ONLY if we actually seeked -
                              # doing it unconditionally skipped line 1 of a small file
        for raw in f:
            if b"clear_session" not in raw:
                continue              # cheap prefilter - most lines never touch this
            try:
                d = json.loads(raw.decode("utf-8", "replace"))
            except Exception:
                continue
            c = (d.get("message") or {}).get("content")
            if not isinstance(c, list):
                continue
            for b in c:
                if isinstance(b, dict) and b.get("type") == "tool_use" \
                        and "clear_session" in str(b.get("name", "")):
                    # keep going: return the LATEST dropped clear, not the first. Returning
                    # the first meant a second failed clear matched told_dropped and was
                    # suppressed as "already reported" - measured 11 Sep, two clears at
                    # 12:42:05 and 13:13:27 both dropped and only the first was ever named.
                    found = (d.get("timestamp") or "")[11:19] or "earlier"
    return found


# background_jobs() lived here until 18 Sep 2026. It globbed TEMP/claude/*/<sid>/tasks/
# *.output and called every hit a background job this session had launched.
#
# REMOVED, not fixed. Measured in a live chat: it named two ids and BOTH were ordinary Bash
# tool output - a large result the app had spilled to disk, and the command that was running
# at that moment. Nothing had been backgrounded at all, so the warning was wrong in every
# chat that had run any Bash. Every one of the 70 files under tasks/ on this machine is a
# .output, so nothing on disk separates a real background job from a foreground command:
# there is no fix available here, only a confident wrong answer or silence.
# [[counters-must-be-measured]]


# The app writes messages into a transcript that the user never typed: the placeholder
# for an image he pasted, the auto-continue line that fires when he hits a usage limit, and
# the interrupt marker. This ledger calls itself "his own words", so none of them belong in
# it. Measured 18 Sep 2026 across his 45 D--Claude transcripts: 246 of the 292 messages the
# old project-wide dedup was silently dropping were one of these three.
NOISE_PREFIXES = ("[Image:", "[Request interrupted by user")
NOISE_EXACT = ("Continue from where you left off.",)


def ledger_noise(text):
    s = (text or "").strip()
    return s.startswith(NOISE_PREFIXES) or s in NOISE_EXACT


def session_sid8(transcript_path):
    """The chat a transcript belongs to - its filename IS the session id."""
    b = os.path.basename(transcript_path or "")
    return os.path.splitext(b)[0][:8]


def note_sid8(path):
    """The chat that wrote a note, from its filename <KEY>.<sid8>.md - and from the
    .used-* archives too, which is where a picked-up thread's history actually lives.
    "" for the legacy flat <KEY>.md, which predates one-note-per-chat."""
    b = os.path.basename(path or "")
    if b.endswith(".md"):
        b = b[:-3]
    parts = b.split(".")
    if len(parts) > 1 and len(parts[1]) == 8 and not parts[1].startswith("used"):
        return parts[1]
    return ""


def chat_threads(transcript_path):
    """{chat id: thread name} - which thread each chat belonged to, read off the note it
    wrote. Newest note wins. A chat that never handed off is absent, and its words then
    stay unattributed rather than being guessed into somebody's thread."""
    out = {}
    for f in all_notes(transcript_path):
        sid8 = note_sid8(f)
        if not sid8 or sid8 in out:
            continue
        base, _n = split_label_number(note_label(f))
        if base:
            out[sid8] = base.lower()
    return out


def attrib_path(transcript_path):
    """Sidecar holding {entry: chat id} for ledger entries written before the chat id was
    recorded inline. Separate from the ledger on purpose - deleting this file undoes the
    recovery and costs nothing."""
    return ledger_path(transcript_path)[:-3] + ".attrib.json"


def entry_key(block):
    """Stable key for a ledger entry: its BODY, never its header. The header is the part
    that changed when attribution arrived, so keying on it would match nothing."""
    body = block.split(chr(10), 1)[1] if chr(10) in block else ""
    return hashlib.sha256(body.strip().encode("utf-8", "replace")).hexdigest()[:16]


def ledger_path(transcript_path):
    """A permanent record of the user's own words, per project. NEVER consumed -
    unlike the handoff note, which is read once and archived. This is the backstop for
    the failure that started all this: a request asked on two different days and dropped
    both times, because nobody wrote down what he actually said."""
    return handoff_path(transcript_path)[:-3] + ".requests.md"


def user_messages(path, start=0):
    """Every real user message in a transcript, oldest first, VERBATIM. Excludes tool
    results, hook injections, system reminders and pasted skill bodies - none of those
    are things the user asked for.

    Scans from byte `start` so the Stop hook does not re-read the whole file on every
    turn - session 143f0320's transcript is 321 MB. Returns (messages, new_offset).
    Binary mode, because a text-mode seek to an arbitrary byte is not safe."""
    out = []
    try:
        size = os.path.getsize(path)
    except Exception:
        return out, start
    if start > size:
        start = 0                       # file replaced or truncated - rescan
    try:
        fh = open(path, "rb")
    except Exception:
        return out, start
    with fh:
        fh.seek(start)
        for raw in fh:
            try:
                d = json.loads(raw.decode("utf-8", "replace"))
            except Exception:
                continue
            m = d.get("message") or {}
            if d.get("type") == "attachment":
                # Anything he types WHILE a turn is running is filed as an attachment
                # record, NOT as a user message - measured 11 Sep. These are real
                # requests and are the easiest of all to lose.
                a = d.get("attachment") or {}
                if a.get("type") != "queued_command":
                    continue
                c = a.get("prompt")
            elif m.get("role") == "user":
                c = m.get("content")
            else:
                continue
            parts = []
            if isinstance(c, str):
                parts = [c]
            elif isinstance(c, list):
                for b in c:
                    if isinstance(b, dict) and b.get("type") == "text":
                        parts.append(b.get("text", ""))
            for t in parts:
                t = (t or "").strip()
                if not t:
                    continue
                if MIDTURN in t:
                    # something he typed WHILE a turn was running arrives wrapped in a
                    # system reminder - unwrap it, do not drop it. These are real asks
                    # and are the easiest of all to lose.
                    t = t.split(MIDTURN, 1)[1].split("This is how Claude Code surfaces", 1)[0].strip()
                    if not t:
                        continue
                elif "<system-reminder>" in t or t.startswith("<command") or t.startswith("<local-command"):
                    continue      # machine-generated, not him
                if len(t) > LEDGER_MAX_CHARS:
                    continue      # too long to be a request - a pasted reference
                if t.startswith(SKILL_MARKERS):
                    continue      # a skill body that happened to be short
                if any(n in t for n in NOISE):
                    continue      # task notifications and the like - he never typed these
                out.append(((d.get("timestamp") or "")[:19].replace("T", " "), t))
        end = fh.tell()
    return out, end


def cmd_ledger():
    """Stop hook. Two jobs, in order: record the user's own words, then - if this chat
    has handed off without saving anything to memory - say so before the chat closes."""
    d = read_stdin()
    path = find_transcript(d.get("session_id"), d.get("transcript_path"))
    if not path:
        return
    append_ledger(path)
    sid = d.get("session_id")
    if sid:
        # Before any decision below: a chat that closes having written nothing is
        # the failure this whole tool exists to prevent, and it is silent.
        write_stub(path, sid, live_context(path))
    if ceiling_block(d, path):
        return          # exactly one decision per Stop, and the ceiling outranks the
                        # nag: with no note written there is nothing for the nag to check
    memory_nag(d, path)


def append_ledger(path):
    """Appends any of the user's words not already recorded. Mechanical - it cannot be
    lazy, cannot summarise, and cannot forget to do it."""
    lp = ledger_path(path)
    sidecar = lp[:-3] + ".seen.json"
    seen, offsets, legacy, migrated = set(), {}, set(), set()
    try:
        with open(sidecar) as f:
            saved = json.load(f)
        offsets = saved.get("offsets") or {}
        if "legacy" in saved:
            seen = set(saved.get("seen") or ())
            legacy = set(saved.get("legacy") or ())
            migrated = set(saved.get("migrated") or ())
        else:
            # One-time migration. The old sidecar keyed on the CONTENT of a message alone,
            # project-wide, so his words were dropped whenever ANOTHER chat had said the
            # same thing first. Those hashes cannot be re-keyed - nothing recorded who said
            # what - so they are kept aside and consulted ONCE per transcript, just long
            # enough to stop the 64KB rewind below re-appending what is already recorded.
            legacy = set(saved.get("seen") or ())
    except Exception:
        pass
    # Offsets MUST be per transcript. The ledger is per PROJECT and a project holds many
    # sessions, so a single shared offset made a scan of session B start wherever session
    # A had finished - which silently skipped the first 3 MB of a 321 MB transcript.
    key = os.path.normcase(os.path.abspath(path))
    # rewind a little: the previous run may have stopped on a half-written line.
    # Re-reading it is free because the hashes below drop anything already recorded.
    offset = max(0, int(offsets.get(key, 0)) - 65536)
    fresh = []
    sid8 = session_sid8(path)
    first_run = key not in migrated
    msgs, new_offset = user_messages(path, offset)
    for ts, t in msgs:
        if ledger_noise(t):
            continue
        # Keyed on the CHAT as well as the words. The same sentence typed in two different
        # chats is two different requests, and dropping the second is the exact failure
        # this ledger exists to prevent - measured 18 Sep 2026, "the wallet thingy needs a
        # better approach what do you think?" was asked in two chats and recorded once.
        h = hashlib.sha256((sid8 + chr(0) + t).encode("utf-8", "replace")).hexdigest()[:16]
        if h in seen:
            continue
        seen.add(h)
        if first_run and hashlib.sha256(
                t.encode("utf-8", "replace")).hexdigest()[:16] in legacy:
            continue            # already in the ledger under the old project-wide key
        fresh.append((ts, t))
    migrated.add(key)

    def _save():
        # must run even when nothing is new, or the offset never advances and every
        # turn rescans the entire transcript from byte zero
        try:
            offsets[key] = new_offset
            with open(sidecar, "w") as f:
                json.dump({"seen": sorted(seen), "offsets": offsets,
                           "legacy": sorted(legacy), "migrated": sorted(migrated)}, f)
        except Exception:
            pass

    if not fresh:
        _save()
        return
    try:
        first = not os.path.exists(lp)
        with open(lp, "a", encoding="utf-8") as f:
            if first:
                f.write("# What the user actually asked for - his own words" + chr(10) + chr(10)
                        + "Recorded automatically from the transcript. Never edited, never "
                        "summarised, never consumed. If something here was never delivered, it is "
                        "STILL OPEN no matter how old it is." + chr(10))
            for ts, t in fresh:
                # the chat id is what lets a future pickup tell its own thread's requests
                # from the ten other threads that share this one file
                f.write(chr(10) + "### " + (ts or "unknown time")
                        + (" - chat " + sid8 if sid8 else "")
                        + chr(10) + chr(10) + t + chr(10))
        _save()
        log("ledger: +" + str(len(fresh)) + " -> " + os.path.basename(lp))
    except Exception as e:
        log("ledger: failed " + str(e))


def normalised_prompt(p):
    """Lowercased, punctuation replaced by spaces, runs of space squashed. So "I'm AFK!"
    and "im afk" land on the same string and the phrase lists stay readable."""
    s = "".join(c if (c.isalnum() or c.isspace()) else " " for c in (p or "").lower())
    return " ".join(s.split())


def is_away():
    """He has told this machine he is away from the console. Deliberately a file, not
    per-session state: he says it in one chat and means it for all of them."""
    return os.path.exists(os.path.join(STATE, AWAY_FLAG))


def away_since():
    """When he armed it, as the flag recorded it, for the indicator to quote back.

    An unreadable flag must not hide the whole message - knowing away mode is on matters
    far more than knowing when - so this degrades to "" and the caller drops the clause."""
    try:
        with open(os.path.join(STATE, AWAY_FLAG), encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def set_away(on):
    p = os.path.join(STATE, AWAY_FLAG)
    try:
        if on:
            if not os.path.isdir(STATE):
                os.makedirs(STATE)
            with open(p, "w", encoding="utf-8") as f:
                f.write(datetime.datetime.now().isoformat() + chr(10))
        elif os.path.exists(p):
            os.remove(p)
    except Exception as e:
        log("away: could not " + ("set" if on else "clear") + ": " + str(e))


def away_toggle(prompt):
    """True when he just went away, False when he just got back, None when he said neither.

    EXPLICIT ONLY - the whole message must be the phrase. This is the half of the feature
    he actually chose, over auto-detection, and the reason is that the failure mode is
    invisible: a guard that disarms itself because a sentence contained "afk" would go on
    saying nothing while his context bill climbed."""
    s = normalised_prompt(prompt)
    if not s:
        return None
    if s in AWAY_ON:
        set_away(True)
        log("away: armed by " + repr(s))
        return True
    if s in AWAY_OFF and is_away():
        set_away(False)
        log("away: cleared by " + repr(s))
        return False
    return None


def away_notice(sid, st, ctx):
    """The once-per-chat away indicator, or "" when it is not due. Spends itself: the
    caller that gets a string MUST show it, because the state is already saved.

    MEASURED 19 Sep 2026, and this is why it lived in cmd_size for weeks without him
    ever seeing it. The indicator used to be written inline BELOW the handoff pickup,
    which prints its own systemMessage and returns. In D:\\Claude a note is always
    waiting, so turn one always took that branch, and by turn two the chat was already
    past LEVELS[0][0] and the indicator was correctly silent for good. The feature was
    unreachable in the only folder that uses it. It is a function now so every branch
    that prints to him can carry it.

    ctx < LEVELS[0][0] is load-bearing, not a tidy-up. Above the first tier the
    away-aware warning is already running, and it is SILENT to him on purpose - a
    banner there would break "save silently, never nag", which is his decision, not
    mine. A brand-new chat reports ctx 0, so a pickup turn always qualifies."""
    if not (is_away() and ctx < LEVELS[0][0] and not st.get("away_told")):
        return ""
    st["away_told"] = True
    save_state(sid, st)
    since = away_since()
    return ("Away mode is ON"
            + (" (since " + since[:16].replace("T", " ") + ")" if since else "")
            + " - notes are still being saved, but nothing will ask you to open a new "
              "chat. Type 'back' or 'afk off' to resume normal prompts.")


def with_away(msg, sid, st, ctx):
    """A message he is about to be shown, with the away indicator appended when it is
    due. The flag is machine-wide and permanent, so the chat that inherits it is usually
    not the one that set it - being told on the turn he arrives is the whole point."""
    notice = away_notice(sid, st, ctx)
    return msg + ("  " + notice if notice else "")


def handoff_steps(today):
    """Steps 3 and 4 of the handoff instruction - the only part that differs while away.

    At the console the whole point is the label he types into a new chat. On a phone he
    cannot open one, so the note is still written and he is simply not sent anywhere."""
    if is_away():
        return (
            "(3) AWAY MODE IS ON - he told this machine he is away from the console and on "
            "the phone, so he CANNOT open a new chat and must not be told to. Do NOT mention "
            "the cost, the label, or starting a new session; do not ask him to do anything "
            "about it. Save the note and carry on with whatever he actually asked for, in "
            "your normal voice, as though this warning had not appeared; "
            "(4) the note is written and nothing is lost, so when he says he is back the very "
            "next chat picks it up as usual. He clears away mode by typing 'back'. ")
    return (
        "(3) tell the user briefly and warmly that this chat has grown expensive, that you have "
        "ALREADY saved everything to that note, and that the next chat carries on exactly where "
        "you left off - nothing is lost; (4) LAST action of the turn: ask him to START A NEW CHAT "
        "in this same folder and QUOTE HIM THE EXACT WORDS TO TYPE - the label from step 2c and "
        "nothing more, e.g. 'open a new chat here and just say: Credo Calc'. Say it in one short "
        "line, and quote the label EXACTLY as you wrote it in step 2c, number and date included "
        "('open a new chat here and just say: Credo Calc -5 (" + today + ")'). "
        "Typing that label loads this note and only this note, so the other threads waiting "
        "in this folder stay untouched for later. He need do nothing else, and this conversation "
        "stays readable under 'Resume previous session'. ")


def ceiling_block(d, path):
    """Past CEILING, refuse to let the chat close until the handoff note exists.

    Chosen by the user 17 Sep 2026 ("Checkpoints + a hard ceiling") after being shown
    that warnings alone do not retire a chat. Deliberately the SAME mechanism as the
    memory nag, which he already approved and which already works: a check on disk that
    a claim cannot satisfy, plus four independent brakes. Returns True when it blocked,
    so the caller can skip the nag and print exactly one decision."""
    if d.get("stop_hook_active"):
        return False    # Claude Code is ALREADY continuing because this hook blocked
    if os.path.exists(os.path.join(STATE, CEILING_OFF)):
        return False    # his override - [[automate-it-dont-tell-him]]
    sid = d.get("session_id")
    if not sid:
        return False    # handoff_path() with no sid falls back to the LEGACY flat
                        # <KEY>.md name and every chat would look like it had handed off
    ctx = live_context(path)
    if ctx < CEILING:
        return False
    note = handoff_path(path, sid)
    if real_note(note):
        return False    # a note a chat wrote is on disk - all the ceiling ever wanted.
                        # An auto-stub is NOT that: it records where the work was, never
                        # what was decided, so it must not buy a chat its way past here.
    st = load_state(sid)
    said = int(st.get("ceiling_blocks", 0) or 0)
    if said >= CEILING_MAX:
        log("ceiling: gave up after %d blocks at ctx=%d" % (said, ctx))
        return False
    st["ceiling_blocks"] = said + 1
    save_state(sid, st)
    log("ceiling: blocked stop #%d at ctx=%d" % (said + 1, ctx))
    print(json.dumps({"decision": "block", "reason": (
        "CEILING REACHED. This chat is at about " + str(round(ctx / 1000)) + "k of context, "
        "past the " + str(CEILING // 1000) + "k ceiling, and it has NOT written a handoff "
        "note. Every further reply re-reads all " + str(round(ctx / 1000)) + "k, so this is "
        "no longer a judgement call. Do it NOW, in this order: save what this chat learned "
        "to memory, then write the full handoff note to " + note + " following the template, "
        + ("then say NOTHING to him about any of it - away mode is on, he is not at the "
           "console and cannot open a new chat, so carry on with his actual work in your "
           "normal voice. " if is_away() else
           "then tell the user in one line to open a new chat and quote him the exact label to "
           "type - name, running number and date. ")
        + "Finish or abandon whatever is half-done "
        "first and record it as a checkpoint, so the note lands on a clean break. If this "
        "is genuinely wrong for what he is doing, tell HIM he can switch it off by creating "
        "an empty file at " + os.path.join(STATE, CEILING_OFF) + " - do not simply ignore "
        "it, and do not claim the note is written when it is not."),
    }))
    return True


def memory_nag(d, path):
    """This chat wrote a handoff note but nothing in the memory folder was touched.

    The note is read once and archived; memory is what every FUTURE chat inherits, so a
    handoff with no memory silently drops everything durable the chat learned. Claude
    saying "I saved the memories" is not evidence, so this reads file timestamps instead
    - a claim cannot satisfy it. Approved by the user 17 Sep 2026 after the mechanism was
    explained to him in plain words, together with the NAG_OFF override."""
    if d.get("stop_hook_active"):
        return          # Claude Code is ALREADY continuing because this hook blocked;
                        # blocking again is a loop nobody can interrupt
    if os.path.exists(os.path.join(STATE, NAG_OFF)):
        return
    sid = d.get("session_id")
    if not sid:
        return          # handoff_path() with no sid falls back to the LEGACY flat
                        # <KEY>.md name, and every chat in the folder would then
                        # think it had handed off
    note = handoff_path(path, sid)
    if not real_note(note):
        return          # this chat never handed off - and most chats never do. Tying the
                        # nag to the note is what stops it firing on every ordinary reply
    since = session_start_ts(path)
    if since is None:
        log("memory-nag: could not date this session - staying quiet")
        return
    # Two questions, and until 19 Sep 2026 only the weaker one was asked. mtimes answer
    # "did anyone touch the shared folder"; the transcript answers "did THIS chat write
    # to it". Ask the transcript first and fall back to mtimes only when it cannot be
    # read, because an unreadable transcript must never turn into a wrong accusation.
    wrote = chat_wrote_memory(path)
    if wrote is True:
        log("memory-nag: skipped - this chat wrote to the memory folder")
        return
    if wrote is None and memory_touched_since(path, since):
        log("memory-nag: skipped - no transcript to read, but the folder changed")
        return
    if wrote is False and memory_touched_since(path, since):
        # The hole this closes: `--report` read "memory nags 0, never fired" for eight
        # days and 82 pickups, and with five chats sharing one folder a neighbour's save
        # was enough to clear every one of them. One folder per project removes the
        # ambiguity entirely; this removes the false silence in the meantime.
        log("memory-nag: the folder changed but this chat wrote nothing - another chat")
    st = load_state(sid)
    said = int(st.get("memory_nags", 0) or 0)
    if said >= NAG_MAX:
        log("memory-nag: already said it %d times - letting the chat go" % said)
        return
    st["memory_nags"] = said + 1
    save_state(sid, st)
    mdir = memory_dir(path)
    log("memory-nag: note written, nothing saved to memory since the session started")
    print(json.dumps({"decision": "block", "reason": (
        "THE HANDOFF NOTE IS WRITTEN BUT NOTHING WAS SAVED TO MEMORY. Not one file in "
        + mdir + " has changed since this chat started. The note is read once and then "
        "archived; memory is what every FUTURE chat inherits - so everything durable this "
        "chat learned is about to be lost. Save it NOW, before you stop: the measured "
        "facts, the traps, the decisions the user made, and the milestones this chat "
        "actually reached. CONSOLIDATE BEFORE CREATING - read " + os.path.join(mdir, "MEMORY.md")
        + " first, EDIT the file that already covers the subject, and only add a new file "
        "(plus exactly one line in the index) when nothing there covers it. Then tell the "
        "user in one short line what you saved. If there is genuinely nothing durable to "
        "save, say THAT to the user in one line and stop - do not invent something to "
        "satisfy this check. This check reads file timestamps, so saying it was done does "
        "not clear it. The user can switch it off for good by creating an empty file at "
        + os.path.join(STATE, NAG_OFF) + " .")}))


def is_summon_label(body, stems):
    """True when the "request" is only the label he typed to SUMMON a note - "Context
    Guard -4 (18 Sep)". Six of context guard's 48 ledger entries are these. They are his
    words, so the ledger keeps them forever; they are not requests, so a pickup should not
    spend its budget handing them back to him."""
    base, _n = split_label_number((body or "").strip())
    return bool(base) and base.lower() in stems


def fit_budget(blocks, budget, oldest_share=0.0):
    """(oldest, newest, omitted) - the entries that fit in `budget` characters.

    Newest-first, because that is what a pickup needs in order to continue. But a slice of
    the budget is reserved for the OLDEST entries when asked for, because "anything never
    delivered is STILL OPEN however old it is" is the ledger's whole contract and a pure
    recency window cannot honour it. At least one entry always comes back, even if it is
    on its own larger than the budget."""
    if sum(len(b) for b in blocks) <= budget:
        return [], list(blocks), 0
    head = []
    if oldest_share > 0:
        room, used = int(budget * oldest_share), 0
        for b in blocks:
            if used + len(b) > room:
                break
            head.append(b)
            used += len(b)
    used = sum(len(b) for b in head)
    tail = []
    for b in reversed(blocks[len(head):]):
        if tail and used + len(b) > budget:
            break
        tail.insert(0, b)
        used += len(b)
    return head, tail, len(blocks) - len(head) - len(tail)


def ledger_tail(transcript_path, labels=None):
    """The most recent things the user asked for, to hand a fresh chat alongside the note.

    Split by thread. Every chat in a folder appends to ONE ledger - measured 18 Sep 2026,
    a single 30-entry tail carried the words of ELEVEN different chats and only three of
    them belonged to the thread being picked up. Telling the new chat to work out which
    were its own was never a fix; the file knows, so it says."""
    lp = ledger_path(transcript_path)
    if not os.path.exists(lp):
        return ""
    try:
        with open(lp, "r", encoding="utf-8", errors="replace") as f:
            blocks = f.read().split(chr(10) + "### ")
    except Exception:
        return ""
    if len(blocks) < 2:
        return ""
    entries = blocks[1:]

    # The thread(s) being picked up, by label stem - "Context Guard -5 (18 Sep)" is the
    # same thread as "Context Guard -2 (17 Sep)", which is what split_label_number is for.
    # LABELS, not paths: the caller has already archived each chosen note to .used-*, so
    # reading the label off the original path finds nothing and every entry looks foreign.
    mine = set()
    for lab in (labels or []):
        base, _n = split_label_number(lab)
        if base:
            mine.add(base.lower())
    # computed even when nothing is being picked up: it is also the vocabulary of thread
    # names that tells a summon label apart from a request. ~72 ms on his real folder.
    stems = chat_threads(transcript_path)
    known = set(stems.values())
    attrib = {}
    if mine:
        # entries older than attribution, recovered once by --attribute. Absent file just
        # means the recovery was never run: everything then falls to the other group,
        # which is the honest answer rather than a guess.
        try:
            with open(attrib_path(transcript_path), encoding="utf-8") as f:
                attrib = json.load(f) or {}
        except Exception:
            attrib = {}
    ours, theirs, mine_ids = [], [], []
    for b in entries:
        m = LEDGER_CHAT_RE.search(b.split(chr(10), 1)[0])
        sid = m.group(1) if m else (attrib.get(entry_key(b)) if attrib else None)
        stem = stems.get(sid) if sid else None
        body = b.split(chr(10), 1)[1].strip() if chr(10) in b else ""
        # ledger_noise is checked HERE as well as on the append path, because the append
        # path only protects entries written after it existed. Measured 18 Sep 2026 on the
        # real 444-entry ledger: 12 entries predate it - image-paste artifacts and
        # interruption markers, 1,321 chars - and they are permanent, since the ledger is
        # append-only and is never rewritten. They cost nothing today (all six threads
        # view zero noise chars) but the oldest slice reaches backwards by design, so
        # "not today" is not "not ever".
        if is_summon_label(body, known) or ledger_noise(body):
            continue
        if stem and stem in mine:
            if sid and sid not in mine_ids:
                mine_ids.append(sid)
            ours.append("### " + b)
        else:
            theirs.append("### " + b)
    # The resumed thread keeps its own share rather than competing for the last 30 overall.
    # Measured 18 Sep 2026: a "Credo Calc" pickup got 30 entries and NOT ONE was its own,
    # because the EGX and StreamBERT chats had filled the shared ledger while it was quiet -
    # so the one thread whose open requests actually mattered inherited none of them. A
    # folder with no attribution yet still gets the full 30, which is every other project
    # on this machine today.
    oldest, newest, omitted = fit_budget(ours, LEDGER_OWN_CHARS, LEDGER_OLDEST_SHARE)
    if omitted:
        # The budget was last sized by measuring this file by hand. The ledger is
        # append-only, so the next overflow is a certainty rather than a risk, and his
        # own words being summarised away is precisely what it must not do quietly.
        log("ledger: budget dropped %d of %d request(s) for this thread - %d chars "
            "offered, budget %d" % (omitted, len(ours),
                                    sum(len(b) for b in ours), LEDGER_OWN_CHARS))
    theirs = theirs[-(LEDGER_OTHERS if ours else LEDGER_TAIL):]
    # the neighbours are bounded by size too - 15 entries of 2,000 chars is 30 KB, which
    # on its own would eat most of the injection the note itself needs.
    _o, theirs, _c = fit_budget(theirs, LEDGER_OTHERS_CHARS if ours else LEDGER_OWN_CHARS)

    out = [chr(10) + chr(10) + "THE USER'S OWN WORDS, most recent last, recorded mechanically "
           "from the transcript (full history at " + lp + "). Anything he asked for that was "
           "never delivered is STILL OPEN, however old it is. Do not summarise these away."
           + chr(10)]
    if ours:
        block = list(oldest)
        if omitted:
            # Silent truncation was the actual contract violation: a Spotliar pickup was
            # handed 30 of 278 and had no way to learn the other 248 existed.
            block.append("[... " + str(omitted) + " more requests from this thread are not "
                         "shown here - they sit between the oldest above and the newest "
                         "below. They are in the ledger named above; search it for "
                         + ", ".join('"- chat ' + s + '"' for s in mine_ids[:6])
                         + ". Anything there that was never delivered is STILL OPEN. ...]")
        block += newest
        out.append(chr(10) + "-- THIS THREAD - the chats that led to the note above --"
                   + chr(10) + chr(10) + (chr(10)).join(block))
    if theirs:
        out.append(chr(10) + "-- OTHER CHATS IN THIS FOLDER" + (
            " - different work. Do not adopt it as yours; check it against the note above "
            "before acting on any of it." if ours else
            ". Every chat here shares one ledger, so some of this belongs to a different "
            "thread - check it against the note above before adopting any of it. Entries "
            "with no chat id were recorded before attribution existed.")
            + " --" + chr(10) + chr(10) + (chr(10)).join(theirs))
    return "".join(out)


# "### 2026-09-18 01:23:45 - chat cf4c2cc4" - who said it, written by append_ledger.
# Entries older than the change have no such marker and stay deliberately unattributed.
LEDGER_CHAT_RE = re.compile(r"-\s*chat\s+([0-9A-Za-z_-]{8})\s*$")
LABEL_RE = re.compile(r"HANDOFF\s+LABEL\s*:\s*(.+)", re.I)
# his label format, chosen 17 Sep 2026: "Context Guard -3 (17 Sep)" - name, running
# number for THAT thread, date. The closing bracket is optional in the date pattern
# because note_label() truncates at 40 chars and could cut one off.
LABEL_NUM_RE = re.compile(r"\s*-\s*(\d+)\s*$")
LABEL_DATE_RE = re.compile(r"\s*\([^)]*\)?\s*$")
LABEL_SCAN_MAX = 80        # newest notes+archives to read a label out of; ~2KB each
LEDGER_LABEL_SCAN = 4000   # trailing ledger lines scanned for a label he has already typed
# words too generic to identify a thread by
LABEL_STOP = {"the", "and", "for", "with", "from", "this", "that", "chat", "note", "work",
              "handoff", "session", "project", "continue", "please", "about", "sept", "2026"}


WRITER_PREFIX = "WRITTEN BY:"
WRITER_HEAD_LINES = 6


def note_writer(path):
    """The TITLE of the chat that wrote this note, or "" if it did not say.

    POSITIONAL, and that is not fussiness. 19 Sep 2026 a sentinel matched anywhere in a
    file, so the note EXPLAINING the sentinel was classified as machine output and 287
    lines were destroyed. A note is allowed to discuss this convention; only the head of
    the file is structure.

    A title rather than an id, because there is no id to use. MEASURED 19 Sep 2026: a
    transcript never records the app's session id - its own `sessionId` field holds the
    transcript uuid, while the sidebar archives by `local_<uuid>`, a different id - so a
    hook has nothing in reach to map one to the other. The title is the only handle both
    sides can see."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i >= WRITER_HEAD_LINES:
                    break
                s = line.strip()
                if s.upper().startswith(WRITER_PREFIX):
                    return s[len(WRITER_PREFIX):].strip()
    except Exception:
        return ""            # unreadable: say nothing, never guess at a chat to close
    return ""


def archive_tail(writers):
    """Name the chats that are now provably finished, for the new chat to archive.

    A note being consumed is the ONE moment this tool can be certain the chat that wrote
    it is done: it handed off, and something else has taken over. 19 Sep 2026 his sidebar
    held 47 dead chats and he asked whether they could go automatically."""
    writers = [w for w in writers if w]
    if not writers:
        return ""            # every note written before today lacks the line. Silence.
    if len(writers) == 1:
        who = "The chat that wrote this note is FINISHED - it is titled '" + writers[0] + "'."
    else:
        who = ("The chats that wrote these notes are FINISHED - they are titled "
               + "; ".join("'" + w + "'" for w in writers) + ".")
    return (chr(10) + chr(10) + "-" * 72 + chr(10) + who + " Handing a note over is proof "
            "the work moved on. AFTER you have answered him, tidy his sidebar without "
            "mentioning it: call mcp__ccd_session_mgmt__list_sessions, and for each title "
            "above find the ONE session matching it EXACTLY that is not running and is not "
            "this chat, then call mcp__ccd_session_mgmt__archive_session on it. If a title "
            "matches zero sessions or more than one, archive NOTHING for that title and say "
            "nothing about it - a wrong archive costs his trust, a stale row costs him a "
            "scroll. Do not ask permission and do not announce it; archiving is reversible "
            "with unarchive_session. If you have no such tool, skip it silently.")


def note_label(path):
    """The phrase the user types to summon this note. Explicit "HANDOFF LABEL:" line wins;
    otherwise fall back to the first heading, so notes written before labels existed still
    answer to something sensible."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            head = f.read(2000)
    except Exception:
        return ""
    m = LABEL_RE.search(head)
    if m:
        return m.group(1).strip().strip("*_`# ")[:40]
    for line in head.splitlines():
        t = line.strip()
        if not t.startswith("#"):
            continue
        t = t.lstrip("#").strip()
        for sep in ("—", " - ", ":"):          # "Handoff - Spotliar, 11 Sep"
            if t.lower().startswith("handoff") and sep in t:
                t = t.split(sep, 1)[1]
                break
        return t.split(",")[0].split("(")[0].strip().strip("*_`. ")[:40]
    return ""


def split_label_number(label):
    """'Context Guard -3 (17 Sep)' -> ('Context Guard', 3). No number -> (name, 0).

    Both suffixes come off before matching AND before counting, so one thread is
    recognised as itself whatever number it has reached."""
    s = (label or "").strip()
    s = LABEL_DATE_RE.sub("", s)
    m = LABEL_NUM_RE.search(s)
    if not m:
        return s.strip(), 0
    return s[:m.start()].strip(), int(m.group(1))


def all_notes(transcript_path):
    """Every note this project ever wrote, newest first - live AND .used-* archived."""
    key = project_key(transcript_path)
    d = handoff_dir()
    files = set(glob.glob(os.path.join(d, key + ".*.md"))
                + glob.glob(os.path.join(d, key + ".md")))
    files = [f for f in files if not os.path.basename(f).endswith(".requests.md")]
    try:
        files.sort(key=lambda f: os.path.getmtime(f), reverse=True)
    except Exception:
        files.sort(reverse=True)
    return files[:LABEL_SCAN_MAX]


def ledger_label_numbers(transcript_path):
    """The FLOOR under the numbering, read from the labels he has typed himself.

    label_numbers() alone counts only notes whose label ALREADY carries a number. The
    day numbering was introduced, every older note was bare, so every thread restarted
    at -1: he typed 'StreamBERT APK -5 (17 Sep)' at 18:51 and was handed '-2' at 19:27
    (measured 17 Sep 2026, from this ledger). The ledger is append-only and is never
    consumed, so the highest number he has ever been given is a floor that cannot fall.

    Deliberately strict about what counts as a label - two to four short words - because
    a sentence of his that happens to end in a number would otherwise invent a thread."""
    out = {}
    try:
        with open(ledger_path(transcript_path), encoding="utf-8", errors="replace") as f:
            tail = f.readlines()[-LEDGER_LABEL_SCAN:]
    except Exception:
        return out
    for line in tail:
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        base, n = split_label_number(s)
        if n <= 0 or not base or len(base) > 40 or len(base.split()) > 4:
            continue
        if not re.search("[A-Za-z]", base):
            continue
        k = base.lower()
        if k not in out or n > out[k][0]:
            out[k] = (n, base)
    return out


def label_numbers(transcript_path):
    """{thread name lowercased: (highest number reached, the name as written)}.

    Counted from the ARCHIVES as well as the live notes: by the time a chat hands off,
    the note it picked up is already a .used-* file, so the archives are the only
    record of the order he asked to be able to see. A fresh chat cannot know how many
    of its thread came before it, which is why this is read off disk and never guessed."""
    out = {}
    for f in all_notes(transcript_path):
        base, n = split_label_number(note_label(f))
        if not base:
            continue
        k = base.lower()
        if k not in out or n > out[k][0]:
            out[k] = (n, base)
    for k, (n, base) in ledger_label_numbers(transcript_path).items():
        if k not in out or n > out[k][0]:
            out[k] = (n, base)
    return out


def label_number_hint(transcript_path):
    """The sentence that hands Claude the number instead of letting it invent one."""
    nums = label_numbers(transcript_path)
    if not nums:
        return "No note has ever been written in this folder, so this thread starts at -1. "
    parts = []
    for n, base in sorted(nums.values(), reverse=True)[:8]:
        parts.append("'" + base + "'" + (" is at -" + str(n) + " (next: -" + str(n + 1) + ")"
                                          if n else " has no number yet (next: -1)"))
    return ("The numbers this folder has already reached, read from its notes and its "
            ".used-* archives: " + "; ".join(parts) + ". If this chat's thread is one of "
            "those, use its NEXT number. A thread not in that list starts at -1. ")


def _tokens(text):
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower())
            if len(w) >= 4 and w not in LABEL_STOP}


def named_match(notes, prompt):
    """Notes whose LABEL the prompt actually names. Strict - no single-note shortcut, because
    this is what reopens the door for a chat that has already taken one."""
    words = set(re.findall(r"[a-z0-9]+", (prompt or "").lower()))
    return [f for f in notes if _label_named(note_label(f), words)]

def _label_named(label, words):
    """Does `words` name this label? Any strong (4+ char) token is enough. But a label made
    ENTIRELY of short words - "EGX bot" - has no strong tokens at all, so _tokens() handed
    back the empty set, which intersects nothing and left the note permanently unsummonable
    (measured 17 Sep 2026 on his real EGX note: he typed the exact name the menu had just
    offered him and the hook printed nothing). Those fall back to naming the WHOLE label,
    so "EGX bot" lands and a stray "bot" still does not."""
    label, _n = split_label_number(label)   # "EGX bot -2 (16 Sep)" -> "EGX bot".
    # Without this the number and the date JOIN the all-short-words fallback set, and
    # typing "EGX bot" stops being a superset of it - the exact 14k-note-unreachable
    # bug fixed on 17 Sep, reintroduced by the numbering he asked for.
    strong = _tokens(label)
    if strong:
        return bool(strong & words)
    short = set(re.findall(r"[a-z0-9]+", (label or "").lower()))
    return bool(short) and short <= words


def pick_notes(notes, prompt):
    """(chosen, unchosen). One waiting note needs no keyword. Several, and the user's own
    first message decides which - that is the whole point of the label."""
    if len(notes) <= 1:
        return notes, []
    hit = named_match(notes, prompt)
    if hit:
        return hit, [f for f in notes if f not in hit]
    return [], notes


def pending_handoff(transcript_path, prompt=""):
    """Pick up a handoff note the previous chat left, if SessionStart did not.
    Whichever hook gets there first renames it .used.md, so it surfaces once.

    Returns (text, consumed). `consumed` is True ONLY when a note was actually taken off
    disk - the menu returns text with consumed=False, because it eats nothing. The caller
    burns the chat's one-note-ever slot on `consumed`, never on the text: measured 11 Sep
    2026, a fresh chat that said "hi" first got the menu, had its slot burned anyway, and
    then silently got NOTHING when it typed the label on the very next turn."""
    notes = waiting_notes(transcript_path)
    if not notes:
        return None, False
    notes, rest = pick_notes(notes, prompt)
    if not notes:
        # several threads waiting and his message names none of them. Do NOT guess and do
        # NOT consume any: ask, in one cheap line, and the answer loads the right one.
        labels = [note_label(f) or os.path.basename(f) for f in rest]
        log("handoff: %d notes waiting, prompt matched none - offered the menu" % len(rest))
        return ("SAVED THREADS ARE WAITING in this folder - " + str(len(rest)) + " of them, from "
                + str(len(rest)) + " different chats that ran out of room. His message does not say "
                "which one he means. Do NOT guess, do NOT read them off disk, and do NOT start "
                "other work first. Answer him with one short friendly line offering exactly these, "
                "by name: " + "; ".join(labels) + ". Tell him to reply with just the name and it "
                "loads itself. Then answer whatever he actually asked, if anything."), False
    # share the budget, but never squeeze a real note below 20k chars just because a
    # second chat also handed off - three notes at ~19k each is ~15k tokens, still a
    # rounding error against the 300k context they are replacing
    cap = max(NOTE_CHARS // len(notes), 20_000)
    # read the labels while the notes are still where they are - the loop below renames them
    picked = [note_label(f) for f in notes]
    # read BEFORE the loop renames the files out from under us
    writers = [note_writer(f) for f in notes]
    chunks = []
    for f in notes:
        # archive under a timestamp, never a fixed .used.md - that overwrote the previous
        # note every time and quietly destroyed the history of what past chats were doing
        used = f[:-3] + ".used-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + ".md"
        try:
            with open(f, "r", encoding="utf-8", errors="replace") as fh:
                body = fh.read()
            os.replace(f, used)
        except Exception:
            continue
        if len(body) > cap:
            body = (body[:cap] + chr(10) + chr(10)
                    + "... NOTE TRUNCATED HERE - it is " + str(len(body)) + " chars long. "
                    "READ THE REST NOW with the Read tool from: " + used)
        chunks.append(body)
    if not chunks:
        return None, False
    sep = (chr(10) + chr(10) + "=" * 72 + chr(10)
           + "NEXT NOTE - a DIFFERENT chat in this project also handed off. It is separate "
           "work; do not merge the two." + chr(10) + "=" * 72 + chr(10) + chr(10))
    body = sep.join(chunks) + ledger_tail(transcript_path, picked)
    log("handoff picked up by UserPromptSubmit (%d note(s), %d chars)" % (len(chunks), len(body)))
    count = ("HANDOFF NOTE" if len(chunks) == 1 else
             str(len(chunks)) + " HANDOFF NOTES, from " + str(len(chunks)) + " different chats,")
    return (count + " from the previous chat(s) in this project. They ended because they grew "
            "too expensive, NOT because the work finished. Read them, ask the user which thread "
            "he wants first if there is more than one, and tell him in one line that you have "
            "picked up where things left off. FIRST ACTION, before anything else: rename "
            "THIS chat so he can see the order in his sidebar - call mcp__ccd_session_mgmt__"
            "set_session_title with session_id 'self' and this note's label with its "
            "number one higher, keeping the date (a note labelled 'Foo -3 (1 Jan)' makes "
            "this chat 'Foo -4 (1 Jan)'). The app writes the title from the conversation "
            "and drops the number - measured 17 Sep 2026: he typed 'EGX handover -2 "
            "(17 Sep)' and the chat was titled 'EGX handover' - so nothing else will do "
            "it. If you have no such tool, skip it silently and do not mention it. "
            "IF THERE IS MORE THAN ONE NOTE: the moment he "
            "picks a thread, re-save each OTHER note verbatim to ~/.claude/handoff/<PROJECT "
            "KEY>.<that chat's 8-char id>.md before doing anything else. They were consumed "
            "to reach you and would otherwise be lost to the next chat - the archived copies "
            "sit beside them as .used-* if you need to copy from disk instead."
            # archive_tail FIRST: the app truncates the tail of a long injected
            # message, and on 19 Sep 2026 a 24kB note ate this instruction whole.
            # The note body is the part that may safely be cut.
            + archive_tail(writers) + chr(10) + chr(10) + body), True


def _bash_shape(cmd):
    """"python D:/Claude/context-guard/test_guard.py" -> "python test_guard.py", and ""
    for anything that cannot be identified as repeatable work.

    Paths collapse to basenames so the same command run from two chats, with two different
    absolute paths, counts as ONE shape. Everything else here is a refusal: measured on his
    real transcripts, the loose version reported the shell itself back to him."""
    c = (cmd or "").strip()
    if c.lower().startswith("cd ") and "&&" in c:
        c = c.split("&&", 1)[1].strip()        # the cd prefix is scaffolding, not the work
    try:
        parts = shlex.split(c, posix=False)
    except Exception:
        parts = c.split()
    parts = [p.strip(chr(34) + chr(39)) for p in parts]
    parts = [p for p in parts if p]
    # VAR=value is setup, not the command. This is where "sam" came from: shlex splits
    # N="C:/Users/Sam Rivera/..." at the space, leaving N="C:/Users/Sam as token zero.
    while parts and re.match(r"[A-Za-z_][A-Za-z0-9_]*=", parts[0]):
        parts = parts[1:]
    if not parts:
        return ""
    raw0 = parts[0].replace(chr(92), "/")
    exe = os.path.basename(raw0).lower()
    if not CMD_NAME.match(exe or " "):
        return ""                              # quotes, redirects, path wreckage
    if exe in LOOK_AROUND:
        return ""
    ext = os.path.splitext(exe)[1]
    if "/" in raw0 and not ext:
        return ""                              # a path fragment, not a command
    arg = ""
    for p in parts[1:]:
        if p.startswith("-"):
            continue                           # flags do not identify the work
        a = os.path.basename(p.replace(chr(92), "/"))
        if len(a) > 40 or any(ch in a for ch in "|><$`;*?" + chr(34) + chr(39)):
            break                              # a pipeline, a here-doc or a long one-off
        arg = a
        break
    if not arg and ext not in SCRIPT_EXT:
        return ""                              # "python" on its own is not a candidate
    return (exe + " " + arg).strip()


def skill_candidates(transcript_path):
    """Command shapes this project keeps redoing, across chats. (ranked, scanned).

    PROPOSES ONLY. The user decided on 17 Sep 2026 - "propose-only for skills" - that
    nothing here may write a skill by itself, and that is not to be reopened."""
    key = project_key(transcript_path)
    try:
        files = sorted(glob.glob(os.path.join(PROJECTS, key, "*.jsonl")),
                       key=lambda f: -os.path.getmtime(f))
    except Exception:
        return [], 0
    runs, chats, scanned = {}, {}, 0
    budget = SKILL_SCAN_BUDGET
    deadline = time.time() + SKILL_SCAN_SECONDS
    for fp in files:
        if budget <= 0 or time.time() > deadline:
            break
        sid = os.path.basename(fp)[:-6]
        try:
            size = os.path.getsize(fp)
        except Exception:
            continue
        take = min(size, SKILL_TAIL_BYTES, budget)
        budget -= take
        scanned += 1
        try:
            with open(fp, "rb") as f:
                if size > take:
                    f.seek(size - take)
                    f.readline()               # drop the half line the seek landed in
                for n, raw in enumerate(f):
                    if not n % 2000 and time.time() > deadline:
                        break
                    if b"tool_use" not in raw:
                        continue               # cheap prefilter - most lines never match
                    try:
                        rec = json.loads(raw.decode("utf-8", "replace"))
                    except Exception:
                        continue
                    for b in ((rec.get("message") or {}).get("content") or []):
                        if not isinstance(b, dict) or b.get("type") != "tool_use":
                            continue
                        name = b.get("name") or ""
                        if name == "Bash":
                            shape = _bash_shape((b.get("input") or {}).get("command"))
                        elif name.startswith("mcp__"):
                            shape = name       # a connector call is a workflow by itself
                        else:
                            continue           # Read/Edit/Grep fire constantly and mean
                                               # nothing - counting them buries the signal
                        if not shape:
                            continue
                        runs[shape] = runs.get(shape, 0) + 1
                        chats.setdefault(shape, set()).add(sid)
        except Exception:
            continue
    out = []
    for shape, n in runs.items():
        c = len(chats[shape])
        if c < SKILL_MIN_CHATS or n < SKILL_MIN_RUNS:
            continue
        out.append((n * c, n, c, shape))
    out.sort(reverse=True)
    return out[:SKILL_TOP], scanned


def skill_proposals(transcript_path):
    """The candidates as one block for the handoff warning, or "" when there are none.

    Silent when nothing repeats: a warning that always ends in an empty list is a warning
    he learns to stop reading to the end of."""
    try:
        cands, scanned = skill_candidates(transcript_path)
    except Exception as e:
        log("skills: scan failed " + str(e))
        return ""
    if not cands:
        return ""
    body = "".join(chr(10) + "  - " + shape + "   (" + str(n) + " runs across " + str(c)
                   + " chats)" for _s, n, c, shape in cands)
    return (chr(10) + chr(10) + "SKILL CANDIDATES - work this project keeps redoing, counted "
            "across " + str(scanned) + " chat transcript(s) in this folder. PROPOSE THESE, "
            "DO NOT BUILD THEM: the user decided on 17 Sep 2026 that nothing may create a "
            "skill by itself, and he picks." + body + chr(10)
            + "Put them in the handoff note under what is still open, and give him one short "
            "line now. Judge them first - a shape that repeats because it is cheap and "
            "obvious does not need a skill. Say which ONE you would actually build, and why.")


def cmd_skills():
    """Run by hand: python guard.py --skills [PROJECT-KEY]. Proposes, never writes."""
    a = [x for x in sys.argv[1:] if x != "--skills"]
    key = a[0] if a else "".join(c if c.isalnum() else "-" for c in os.getcwd())
    d = os.path.join(PROJECTS, key)
    if not os.path.isdir(d):
        print("no transcripts for project key: " + key)
        print("available keys:")
        for p in sorted(glob.glob(os.path.join(PROJECTS, "*"))):
            if glob.glob(os.path.join(p, "*.jsonl")):
                print("  " + os.path.basename(p))
        return
    cands, scanned = skill_candidates(os.path.join(d, "x.jsonl"))
    print("SKILL CANDIDATES for " + key + " - scanned " + str(scanned) + " transcript(s)")
    print("=" * 64)
    print("Proposals only. This command never creates a skill - you decide.")
    if not cands:
        print("  nothing repeated across " + str(SKILL_MIN_CHATS) + "+ chats yet")
        return
    for _s, n, c, shape in cands:
        print("  %-40s %3d runs across %d chats" % (shape[:40], n, c))


def cmd_attribute():
    """Run by hand: python guard.py --attribute [PROJECT-KEY].

    Every chat in a folder appends to ONE ledger. Entries written from 18 Sep 2026 carry
    the chat id that said them; the 419 before that do not, so a pickup still met a wall
    of requests belonging to nobody in particular. The words are still sitting in the
    transcript that said them, so this matches them back and writes the result to a
    sidecar. It does NOT touch the ledger - "never edited, never summarised, never
    consumed" is the whole reason that file is trustworthy.

    Identical words in two different chats are left unattributed ON PURPOSE. The words are
    the only evidence, so they prove nothing, and filing a request under the wrong thread
    is worse than leaving it unsorted. Reading every transcript takes far too long for a
    10-second hook, which is why this is a command and not automatic."""
    a = [x for x in sys.argv[1:] if x != "--attribute"]
    key = a[0] if a else "".join(c if c.isalnum() else "-" for c in os.getcwd())
    d = os.path.join(PROJECTS, key)
    if not os.path.isdir(d):
        print("no transcripts for project key: " + key)
        return
    probe = os.path.join(d, "x.jsonl")
    lp = ledger_path(probe)
    if not os.path.exists(lp):
        print("no ledger yet for project key: " + key)
        return
    owner, scanned = {}, 0
    for p in sorted(glob.glob(os.path.join(d, "*.jsonl"))):
        sid8 = session_sid8(p)
        scanned += 1
        try:
            msgs, _off = user_messages(p, 0)
        except Exception:
            continue
        for _ts, t in msgs:
            if ledger_noise(t):
                continue
            k = hashlib.sha256(t.strip().encode("utf-8", "replace")).hexdigest()[:16]
            owner[k] = sid8 if owner.get(k, sid8) == sid8 else ""
    try:
        with open(lp, encoding="utf-8", errors="replace") as f:
            blocks = f.read().split(chr(10) + "### ")[1:]
    except Exception as e:
        print("could not read the ledger: " + str(e))
        return
    out, already, ambiguous, unknown = {}, 0, 0, 0
    for b in blocks:
        if LEDGER_CHAT_RE.search(b.split(chr(10), 1)[0]):
            already += 1
            continue
        sid = owner.get(entry_key(b))
        if sid:
            out[entry_key(b)] = sid
        elif sid == "":
            ambiguous += 1
        else:
            unknown += 1
    try:
        with open(attrib_path(probe), "w", encoding="utf-8") as f:
            json.dump(out, f)
    except Exception as e:
        print("could not write the sidecar: " + str(e))
        return
    print("ATTRIBUTION RECOVERED for " + key + " - read " + str(scanned) + " transcript(s)")
    print("=" * 64)
    print("  ledger entries              : %d" % len(blocks))
    print("  already carried a chat id   : %d" % already)
    print("  recovered into the sidecar  : %d" % len(out))
    print("  same words in two chats     : %d  (left unattributed on purpose)" % ambiguous)
    print("  no transcript holds them    : %d" % unknown)
    print("  sidecar                     : " + attrib_path(probe))
    print("  the ledger itself was not touched. Delete the sidecar to undo this.")


# --- the heredoc rule, enforced rather than asked for -----------------------------------
# His words, 17 Sep 2026: "Heredoc choked on the content. Writing the note with the file tool
# instead. i see this a lot can we fix this "Heredoc "". The previous chat answered it by
# writing ~/.claude/CLAUDE.md, which says never to put file CONTENT through a Bash heredoc
# and why: quoted heredocs are not reliably literal in Git Bash here, a backslash-r collapses
# into a real CR, em-dashes and curly quotes get mangled, and PowerShell has no heredocs at
# all.
#
# Measured 18 Sep 2026: the instruction alone did NOT hold. 2056 of 14043 Bash calls in this
# folder carry a heredoc, and AFTER the CLAUDE.md existed three separate chats still used one
# (81e1b972, feb0c0b7, and the chat doing the measuring). An instruction the model can
# reconsider mid-turn is not the same thing as a rule the harness applies every time.
# So this refuses the two shapes that actually carry content, and leaves every other heredoc
# alone - a few lines of DATA handed to a query tool was never the problem.
# [[automate-it-dont-tell-him]] [[sed-i-strips-crlf-on-windows]]
HEREDOC_OFF = "no-heredoc-guard"   # his override, same shape as NAG_OFF and CEILING_OFF
HEREDOC_OK = "heredoc-ok"          # per-command escape, named in the refusal itself
HEREDOC_RE = re.compile("<<-?\\s*([\"']?)([A-Za-z_][A-Za-z0-9_]*)\\1")
# "> f" and ">> f" write the body into a file. "2>" and ">&2" are stderr plumbing and are NOT
# a file write - measured while building this, a naive ">" rule called `... 2>/dev/null |
# head -1` a file write and would have refused an ordinary read-only command.
HEREDOC_TO_FILE = re.compile("(?<![0-9&>])>>?\\s*(?![&>])[^\\s|;&<]+")
HEREDOC_TO_TEE = re.compile("\\b(?:tee|dd\\s+of=)")
# an interpreter taking its SCRIPT from stdin: python - , node - , bash -s
HEREDOC_TO_PROG = re.compile(
    "\\b(?:python3?|py|node|deno|perl|ruby|Rscript|bash|sh|zsh|pwsh|powershell)\\b"
    "[^|;&]*?\\s-(?:s\\b|\\s|$)")


def heredoc_offence(cmd):
    """Why this command is pushing content through a heredoc, or "" if it is not.

    Only the text BEFORE the << is examined. That is where the redirection and the
    interpreter live; the body after it is the content itself and may contain anything,
    including the very characters that make this unsafe."""
    m = HEREDOC_RE.search(cmd or "")
    if not m:
        return ""
    before = (cmd or "")[:m.start()]
    if HEREDOC_TO_TEE.search(before) or HEREDOC_TO_FILE.search(before):
        return "it redirects the heredoc body straight into a file"
    if HEREDOC_TO_PROG.search(before):
        return "it feeds a script to an interpreter on stdin"
    return ""


def cmd_bash():
    """PreToolUse on Bash. Silent for everything that is not a heredoc carrying content -
    which is the overwhelming majority of calls, and this runs before every one of them."""
    d = read_stdin()
    cmd = (d.get("tool_input") or {}).get("command") or ""
    if HEREDOC_OK in cmd:
        return                      # he (or Claude) asked for this one explicitly
    if os.path.exists(os.path.join(STATE, HEREDOC_OFF)):
        return
    why = heredoc_offence(cmd)
    if not why:
        return
    log("heredoc: blocked - " + why)
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": (
            "BLOCKED: " + why + ". Heredocs are not reliable on this machine. Measured "
            "across this account's own transcripts: quoted heredocs are NOT literal here, a "
            "backslash-r collapses into a real CR, em-dashes and curly quotes get mangled "
            "repeatedly, and PowerShell has no heredocs at all. Use the Write tool to create "
            "the file, or the Edit tool to change one - and if it is a script, write it with "
            "Write and then RUN it with Bash. Bash stays the right tool for running things: "
            "git, tests, greps, builds. Anchor any in-place edit on a unique string and abort "
            "if it is not unique. If a heredoc genuinely is the right tool for this one call, "
            "put the marker # " + HEREDOC_OK + " in the command and it will be allowed. To "
            "switch this check off for good, create an empty file at "
            + os.path.join(STATE, HEREDOC_OFF) + " .")}}))


def cmd_size():
    d = read_stdin()
    sid = d.get("session_id")
    # Away mode is toggled before anything else, so the very turn he says it is already
    # quiet. Confirming it out loud is not nagging - a switch he cannot see is a switch he
    # cannot trust, and this is the one turn where he asked to be told.
    toggled = away_toggle(d.get("prompt") or "")
    if toggled is True:
        print(json.dumps({"systemMessage": (
            "Away mode ON. Context Guard will still save a handoff note if this chat gets "
            "expensive, but it will stop telling you to open a new chat. Say 'back' or "
            "'afk off' when you are at the console again - both work, and so does any "
            "other phrase in AWAY_OFF.")}))
        return
    if toggled is False:
        print(json.dumps({"systemMessage": "Away mode OFF - normal handoff prompts are back."}))
        return
    path = find_transcript(sid, d.get("transcript_path"))
    if not path:
        path = virtual_transcript(d, sid)
        log("size: no transcript on disk yet (new chat) - using " + path)
    ctx = live_context(path)
    st = load_state(sid)
    # does this build actually hand the hook the user's message? the label picker needs it
    log("size: prompt=%d chars %r" % (len(d.get("prompt") or ""),
                                      (d.get("prompt") or "")[:60]))
    if not os.path.exists(path):
        own_bytes = 0                     # nothing written yet = brand new
    else:
        try:
            own_bytes = os.path.getsize(path)
        except Exception:
            own_bytes = FRESH_BYTES + 1   # cannot prove it is fresh -> assume it is not
    fresh = ctx < FRESH_CTX and own_bytes < FRESH_BYTES
    # took_handoff stops a chat SILENTLY hoovering up notes. It must not stop him asking
    # for a second thread by name later - "Credo Calc" in a chat that already loaded the
    # guard note should still work. Only an explicit label match reopens the door.
    named = bool(st.get("took_handoff")) and bool(
        named_match(waiting_notes(path), d.get("prompt") or ""))
    if fresh and (not st.get("took_handoff") or named):
        hand, consumed = pending_handoff(path, d.get("prompt") or "")
        if hand and consumed:
            st["took_handoff"] = True     # one note per chat, ever
            save_state(sid, st)
            print(json.dumps({
                "hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": hand},
                "systemMessage": with_away(
                    "Picked up the handoff note from your last chat.", sid, st, ctx),
            }))
            return
        if hand and not st.get("menu_shown"):
            # the menu: several threads waiting, his message named none. It consumes
            # NOTHING, so the slot stays open and the label still works next turn.
            # Shown once - the picker runs on every turn regardless, so a label typed
            # ten turns later still lands.
            st["menu_shown"] = True
            save_state(sid, st)
            print(json.dumps({
                "hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": hand},
                "systemMessage": with_away(
                    "Saved threads are waiting in this folder.", sid, st, ctx),
            }))
            return
    elif waiting_notes(path):
        log("size: note left for a fresher chat (ctx=%d, own=%.1fMB, took=%s)"
            % (ctx, own_bytes / 1e6, st.get("took_handoff")))
    # AWAY INDICATOR. The flag is machine-wide and never expires (he declined auto-expiry
    # and that is NOT revisited here), so a chat inherits it from something he typed in a
    # different window hours earlier. Measured 19 Sep 2026: he armed it at 20:30 and
    # seventeen hours later asked why a COMPLETELY DIFFERENT chat had gone quiet, then
    # guessed at the way out. Once per chat is an indicator; every turn would be the very
    # nagging away mode exists to stop. It carries the exit because he had to guess it.
    # ctx < LEVELS[0][0] is load-bearing, not a tidy-up. Above the first tier the
    # away-aware warning is already running, and it is SILENT to him on purpose - a
    # banner there would break "save silently, never nag", which is his decision, not
    # mine. Every chat starts below the floor, and the flag is machine-wide and
    # permanent, so the next small chat he opens tells him anyway.
    notice = away_notice(sid, st, ctx)
    if notice:
        print(json.dumps({"systemMessage": notice}))
        return

    last = st.get("warned_ctx", 0) or st.get("warned_at", 0)   # migrate pre-fix state
    if ctx < LEVELS[0][0]:
        # a compaction or a clear dropped us back under the floor - re-arm, so the
        # next climb warns again instead of staying muted at the old high-water mark
        if last:
            st["warned_ctx"] = 0
            st.pop("warned_at", None)
            save_state(sid, st)
            log("size: ctx=" + str(ctx) + " - back under the floor, re-armed")
        else:
            log("size: ctx=" + str(ctx) + " level=None")
        return
    level = LEVELS[0]
    for threshold, word in LEVELS:
        if ctx >= threshold:
            level = (threshold, word)
    dropped = dropped_clear(path)
    if dropped and st.get("told_dropped") == dropped:
        dropped = None      # already reported THIS one; the call stays in the transcript
                            # forever, so without this it would shout on every single turn
    # a dropped clear always speaks, whatever the re-warn cadence says - the handoff was
    # announced as done and silently was not, which is the worst possible state to sit in
    due = (not last) or (ctx >= last + REWARN_STEP) or bool(dropped)
    log("size: ctx=" + str(ctx) + " level=" + str(level[0]) + " last=" + str(last)
        + " due=" + str(due) + (" DROPPED-CLEAR@" + dropped if dropped else ""))
    if not due:
        return                      # warned recently and it has barely grown - do not nag
    st["warned_ctx"] = ctx
    st.pop("warned_at", None)
    if dropped:
        st["told_dropped"] = dropped
    save_state(sid, st)
    threshold, word = level
    ctx_k = round(ctx / 1000)
    mult = max(1, round(ctx / 73000.0))   # 73k = measured cost of a fresh chat's first turn
    msg = ("STOP - THIS CHAT IS NOW " + word.upper() + ". Live context is about "
           + str(ctx_k) + "k tokens, roughly " + str(mult) + "x what a fresh chat costs, "
           "and it is paid again on EVERY reply from here on.")
    hpath = handoff_path(path, sid)      # per-chat: never clobber another chat's note
    drop_note = ("" if not dropped else
                 "A clear_session call is ALREADY in this transcript (at " + dropped + "), which "
                 "PROVES it did nothing - a clear that works starts a fresh transcript. Do NOT "
                 "issue another one; see step 4. The note may already be written: check "
                 + handoff_path(path) + " and the .used-* archives beside it before writing "
                 "another, and UPDATE it rather than duplicating. ")
    today = datetime.datetime.now().strftime("%d %b").lstrip("0")
    mdir = memory_dir(path)
    mindex = os.path.join(mdir, "MEMORY.md")
    guidance = (
        msg + " " + drop_note + "ACT NOW WITHOUT ASKING PERMISSION - this is a standing instruction from the "
        "user, already given. Steps, in order: (1) finish any half-done step so nothing is left "
        "broken; (2a) FIRST, save what this chat learned to MEMORY - memory outlives the note, "
        "which is read once and archived. This project's memories are in " + mdir + " and the "
        "index is " + mindex + ". Write down the measured facts, the traps, the decisions the "
        "user made and the milestones this chat actually reached. CONSOLIDATE BEFORE CREATING: "
        "read the index first, find the memory file that already covers the subject and EDIT "
        "that one. Only create a new file when nothing there covers it, and then add exactly "
        "one line to the index. This is not tidiness: the index is loaded into every turn of "
        "every chat, so a memory step that only ever adds raises the floor this tool exists "
        "to lower. Do not write down what the code or git history already records; "
        "(2b) READ the required "
        "structure at " + TEMPLATE + " and WRITE a handoff note to " + hpath + " that fills in "
        "EVERY numbered section of that template. Detail is the whole point and the user asked "
        "for it explicitly - a thin note is the failure mode, not a saving. Under roughly 150 "
        "lines for a working session means you have not written enough. CRITICAL: for anything the "
        "user asked for that is NOT yet delivered, quote their OWN WORDS verbatim and say plainly "
        "what is still unknown about it. A request summarised in your words loses the detail that "
        "makes it fixable - that is exactly how a bug got asked for twice on two different days "
        "and dropped both times. If you do not know what they meant, write that down as the open "
        "question instead of guessing; "
        "(2c) THE FIRST LINE of that note must be exactly 'HANDOFF LABEL: <name> -N (" + today
        + ")' - two or three words naming the THREAD, as he would say it out loud ('Credo "
        "Calc', 'Spotliar', 'Context Guard'), then a running number for THAT thread and "
        "today's date. Several chats share this folder and the label is how he summons this "
        "one specifically; without it he gets a menu instead of his work. THE NUMBER IS HIS "
        "ORDERING - he asked for it by name so he can see which chat came first. It goes up "
        "by ONE each time this thread hands off, and it counts per thread, not per folder. "
        + label_number_hint(path) +
        "Do not invent it and do not restart it. He does NOT have to type the number or the "
        "date to summon the note - the name on its own still works - but quote him the whole "
        "label in step 4 anyway, because seeing the order is the entire point of it; "
        "(2d) THE SECOND LINE must be exactly 'WRITTEN BY: <this chat's own title, "
        "exactly as it reads in his sidebar>'. That is how the next chat learns which "
        "session is finished and can archive it for him - on 19 Sep 2026 his sidebar "
        "held 47 dead chats. Use the title you set with set_session_title. If you "
        "never set one, write 'WRITTEN BY: (untitled)' - a WRONG title is far worse "
        "than none, because it can match a chat that is still alive. A transcript "
        "never records the app's session id, so the title is the only handle "
        "that exists; "
        + handoff_steps(today) +
        "DO NOT CALL mcp__ccd_session_mgmt__clear_session. Measured three times on 11 Sep 2026 "
        "(12:42, 13:13, 20:12): it returns success, announces a clear, and the session carries "
        "straight on with its full context. The 20:12 attempt sat idle for eight minutes before "
        "the next message, which rules out the documented 'a message arrived first' drop - the "
        "tool is simply inert in this build. Claiming a chat was cleared when it was not is "
        "worse than not clearing: he stops watching the cost. Do NOT ask whether to write the "
        "note. Do not carry on as if this had not appeared."
        + chr(10) + chr(10)
        + "WHEN NOT TO HAND OFF YET - check these before step 4, every time. If any applies, "
        "still do steps 1-3 (the note costs nothing and protects you either way), then SAY which "
        "one applies and hand off at the next clean break instead. Deferring is safe: this warning "
        "comes back every " + str(REWARN_STEP // 1000) + "k of further growth, so nothing is "
        "forgotten." + chr(10)
        + "  (a) A background job may still be running. Handing off with one in flight is UNTESTED, "
        "so treat it as unsafe - check your task notifications and task output first, and wait "
        "for it if in any doubt." + chr(10)
        + "  (b) The user has told you to finish something first. THE USER OUTRANKS THIS WARNING, "
        "always. Honour that without arguing and without re-raising it the same turn." + chr(10)
        + "  (c) You are mid-edit, mid-build, or anything is left broken. Finish it - step 1 "
        "exists for exactly this. This clause is the one that gets abused: during a long "
        "build it is ALWAYS true, which is how a chat reaches 500 turns having deferred "
        "every single warning. It buys you the time to reach a clean break, not a licence "
        "to carry on indefinitely."
        + checkpoint_note(sid, ctx)
        + "Past " + str(CEILING // 1000) + "k the Stop hook will refuse to let this chat "
        "close until the note exists, so reaching a checkpoint before then is the whole "
        "game.") + skill_proposals(path)
    out = {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                  "additionalContext": guidance}}
    if not is_away():
        # This is the one line he actually SEES, and while he is away it is also the one he
        # cannot act on. The guidance above still orders the save either way, so dropping
        # this costs nothing but the nagging - which is exactly what he asked for.
        out["systemMessage"] = msg + " Claude is saving a handoff note now - nothing will be lost."
    print(json.dumps(out))


def cmd_reread():
    d = read_stdin()
    sid = d.get("session_id")
    fp = (d.get("tool_input") or {}).get("file_path") or ""
    if os.path.splitext(fp)[1].lower() not in IMAGE_EXT or not os.path.exists(fp):
        return
    try:
        with open(fp, "rb") as f:
            h = hashlib.sha256(f.read()).hexdigest()[:16]
    except Exception:
        return
    st = load_state(sid)
    seen = st.setdefault("reads", {})
    key = os.path.normcase(os.path.abspath(fp))
    prev = seen.get(key)
    if prev and prev.get("hash") == h:
        prev["n"] = prev.get("n", 1) + 1
        save_state(sid, st)
        if prev["n"] > FREE_REREADS:
            log("reread: flagged " + os.path.basename(fp) + " (x" + str(prev["n"]) + ")")
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    "BLOCKED to save context. This image is byte-identical to the copy already "
                    "read " + str(prev["n"] - 1) + "x in this chat (first at "
                    + str(prev.get("first")) + "). Re-reading adds the whole image to the context "
                    "again and it is paid for on every remaining turn. The file has NOT changed - "
                    "the hash matches, so use what you already described. Two reads are allowed "
                    "free; this is beyond that. If you genuinely lost it to compaction, say so to "
                    "the user rather than re-reading."),
            }}))
            return
    else:
        seen[key] = {"hash": h, "n": 1, "first": datetime.datetime.now().strftime("%H:%M")}
        save_state(sid, st)


# Every guard that writes to the log, with the exact string it writes and whether its
# actual lines are worth printing. Measured 17 Sep 2026, and this list is why the change
# exists: the old report counted four events and had never been updated, so three guards
# built since (the memory nag, the ceiling, checkpoints) were invisible - the report
# quietly described an older tool than the one running.
REPORT_EVENTS = [
    ("warnings fired",          "due=True",                 False),
    ("image re-reads blocked",  "reread: flagged",          True),
    ("handoff notes picked up", "handoff picked up",        False),
    ("handoff menus offered",   "offered the menu",         False),
    ("memory nags",             "memory-nag: note written", True),
    # ...and the reason the row above can honestly read zero. Without this line the zero
    # is unfalsifiable, which is how a dead feature hid for eight days once already.
    ("nags skipped, mem fresh", "memory-nag: skipped",       False),
    ("ledger budget drops",     "ledger: budget dropped",    True),
    ("ceiling blocks",          "ceiling: blocked",         True),
    ("checkpoints recorded",    "checkpoint at ",           True),
    ("ledger appends",          "ledger: +",                False),
]


def log_day(line):
    """'2026-09-11T10:29:39 reread: ...' -> '11 Sep'. Empty if there is no timestamp."""
    try:
        return (datetime.datetime.strptime(line[:19], "%Y-%m-%dT%H:%M:%S")
                .strftime("%d %b").lstrip("0"))
    except Exception:
        return ""


# ---------------------------------------------------------------- the bootstrap
# His words, 19 Sep 2026: "we need to make sure once we do the 'write Context Guard -14
# (19 Sep) in a new chat' thingy it needs to create a folder then and there and organize
# it automatically can we do that??"
#
# Claude Code keys the memory folder on the working DIRECTORY, so a project that has never
# been worked in from its own folder has no memories there - it starts blind. The fix he
# asked for is not a hand migration, it is that the first chat in such a folder furnishes
# itself. The manifest is the assignment; this is the thing that acts on it.
#
# It COPIES, never moves. Every chat he currently has open runs in the shared folder, and
# moving would blind all of them at once to shrink a number. The shrink is a separate step
# he takes when he is satisfied - and copying is the version that can be undone.
MANIFEST = os.path.join(STATE, "memory-manifest.json")
# Where an index line lives once it has been moved OUT of the shared MEMORY.md. On
# 19 Sep 2026, with every project furnished with its own folder, 45 of the shared
# index's 48 lines moved here and cut 6,670 bytes off every turn of every chat started
# in D:\Claude. The lines are still his words; they are just no longer paid for.
PROJECTS_INDEX = os.path.join(STATE, "projects-index.md")
# The folder all 79+ of his chats have shared so far, and the source a new folder is
# furnished FROM. Not derived: it is a fact about this machine, and a wrong guess here
# fails by silently copying nothing, which is the failure mode that looks like success.
SOURCE_KEY = "D--Claude"

# The question SEVEN handoff notes have now carried: when the bootstrap below creates a
# project's memory folder during SessionStart, does THAT chat get to read it, or only the
# next one? Nothing here can spawn a session to find out - there is no claude CLI on this
# machine - and every note has ended with "write the answer down when it happens", which
# is a reminder, i.e. a bug in the tooling rather than advice. So the hook answers it
# itself: Claude Code's own memory loader leaves a fingerprint in the session transcript
# ("...\<KEY>\memory\MEMORY.md (user's auto-memory, persists across conversations)" -
# measured in his real 19 Sep transcript), so a furnish arms a probe and a LATER
# SessionStart reads that transcript back.
#
# Hung on SessionStart rather than on every prompt deliberately: it fires on every startup
# AND every resume, so the verdict lands within hours for the price of one os.path.exists
# per session instead of one per turn. Nothing depends on the answer - the bootstrap hands
# the index back as additionalContext either way - this exists so the question is never
# asked an eighth time.
ORDER_PROBE = os.path.join(STATE, "ordering-probe.json")
ORDER_ANSWER = os.path.join(STATE, "ordering-answer.txt")
USER_TURN_RE = re.compile(r'"type"\s*:\s*"user"')


def arm_ordering_probe(sid, key, given):
    """Record that THIS session created THIS project's folder, for a later one to check.

    A probe that is still pending when another furnish happens is simply replaced: both
    ask the identical question and the newer session is the one whose transcript is most
    likely to actually appear on disk."""
    if not sid or not key or os.path.exists(ORDER_ANSWER):
        return                      # answered once is answered for good
    try:
        os.makedirs(STATE, exist_ok=True)
        with open(ORDER_PROBE, "w", encoding="utf-8") as f:
            json.dump({"sid": sid, "key": key, "transcript": given or "",
                       "armed": datetime.datetime.now().isoformat(timespec="seconds")}, f)
        log("ordering: probe armed - session %s furnished %s" % (sid, key))
    except Exception:
        pass


def transcript_read_its_memories(path, key):
    """Did Claude Code's OWN memory loader put that project's index into that session?

    True / False / None - and None means NOT YET, not no. Hooks fire before the transcript
    exists (measured 11 Sep 2026), so absence of evidence always arrives first and must
    never be read as a verdict: a wrong answer here would also stop the asking."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except Exception:
        return None
    if not USER_TURN_RE.search(text):
        return None                 # no user turn yet - the loader has had no chance
    for line in text.splitlines():
        if "auto-memory" in line and key in line:
            return True
    return False


def resolve_ordering_probe():
    """Write the verdict down once the evidence lands. Silent, and at most once ever."""
    if not os.path.exists(ORDER_PROBE):
        return
    try:
        with open(ORDER_PROBE, encoding="utf-8") as f:
            rec = json.load(f) or {}
    except Exception:
        return
    sid, key = rec.get("sid"), rec.get("key")
    path = find_transcript(sid, rec.get("transcript")) if sid else None
    saw = transcript_read_its_memories(path, key) if (path and key) else None
    if saw is None:
        return                      # keep waiting; the probe costs one stat per session
    out = ["THE SESSIONSTART ORDERING, ANSWERED BY MEASUREMENT - "
           + datetime.datetime.now().isoformat(timespec="seconds"),
           "verdict : %s" % ("same-session" if saw else "next-session"),
           "project : %s   (session %s)" % (key, sid),
           "evidence: %s" % path, ""]
    out += (["Claude Code's memory loader ran AFTER the SessionStart hook: the chat that",
             "created the folder read its own memories too. A first chat in a new project",
             "is not blind."] if saw else
            ["Claude Code's memory loader ran BEFORE the SessionStart hook: the chat that",
             "created the folder did NOT get the index from the loader - only the copy the",
             "bootstrap hands back as additionalContext. That belt-and-braces path is",
             "LOAD-BEARING. Do not remove it."])
    try:
        with open(ORDER_ANSWER, "w", encoding="utf-8") as f:
            f.write("\n".join(out) + "\n")
        os.remove(ORDER_PROBE)
        log("ordering: answered %s for %s" % (out[1], key))
    except Exception:
        pass


def dir_key(cwd):
    r"""Claude Code's own folder name for a working DIRECTORY.

    Deliberately not called project_key - that name is taken, by the function above that
    reads the key off a transcript PATH. Defining a second project_key here overrode it
    at import and broke 89 checks in one edit, silently, because Python's last definition
    simply wins. The suite caught it; a grep for the name would not have.

    Every character that is not a letter or a digit becomes a hyphen, and runs are NOT
    collapsed. Checked against the three keys that actually exist on his disk:
    D:\Claude -> D--Claude, D:\TEST -> D--TEST, and
    D:\sticker-project - Gemini -> D--sticker-project---Gemini (three hyphens, from
    space-hyphen-space). A tidier rule that collapses the runs produces a folder name
    Claude Code will never read, and nothing would ever say so."""
    return re.sub(r"[^A-Za-z0-9]", "-", cwd or "")


def _same_dir(a, b):
    """Two spellings of one folder. On Windows the separator and the case both vary."""
    return ((a or "").replace("/", "\\").rstrip("\\").lower()
            == (b or "").replace("/", "\\").rstrip("\\").lower())


def manifest_project(cwd, man):
    """The manifest entry whose `dir` - or one of its `also` spellings - is this cwd.

    `also` is not decoration: StreamBERT lives in two folders and Spotliar in four, and a
    chat opened in the wrong one of them is exactly the chat that starts blind."""
    for name, e in sorted((man.get("projects") or {}).items()):
        if _same_dir(cwd, e.get("dir")):
            return name, e
        for alt in (e.get("also") or []):
            if _same_dir(cwd, alt):
                return name, e
    return None, None


def index_lines(path):
    """slug -> its own line in MEMORY.md, so a new index is CARRIED OVER, not invented.

    The hook has no business writing its own one-line summary of a memory it has not
    read - his standing complaint is notes that are lazy, and a regenerated index line
    is a summary of a summary."""
    out = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                m = re.search(r"\]\(([^)]+)\.md\)", line)
                if m:
                    out[m.group(1)] = line.rstrip("\r\n")
    except Exception:
        pass
    return out


def cmd_bootstrap():
    """SessionStart: give this directory its own memory folder if it has none yet."""
    d = read_stdin()
    # First, before any of the give-up paths below: a probe left by an earlier session may
    # finally be answerable. Resolution must not be hostage to this session's own cwd.
    resolve_ordering_probe()
    cwd = d.get("cwd") or ""
    key = dir_key(cwd)
    if not key:
        # Measured 19 Sep 2026: the first end-to-end run of this hook did NOTHING and said
        # nothing, and it took a debugging session to find out why - the JSON on stdin had
        # been mangled by the shell that sent it, so cwd was empty. Every give-up path below
        # is silent BY DESIGN (SessionStart must not chatter), which means the log is the
        # only place a misconfiguration can ever show up. Say which branch declined, always.
        log("bootstrap: no cwd on stdin - nothing to do")
        return
    dest = os.path.join(PROJECTS, key, "memory")
    # A folder that already holds memories is HIS. Never overwrite one, never merge into
    # one, and say nothing - SessionStart fires on every resume as well as every startup.
    if os.path.exists(os.path.join(dest, "MEMORY.md")):
        log("bootstrap: %s already has a memory folder - left alone" % key)
        return
    try:
        with open(MANIFEST, encoding="utf-8") as f:
            man = json.load(f)
    except Exception as e:
        log("bootstrap: no usable manifest (%s) - declining to guess" % e)
        return                  # no manifest, or unreadable: nothing here is worth guessing
    name, entry = manifest_project(cwd, man)
    if not entry:
        log("bootstrap: %s is not in the manifest - declining to guess" % cwd)
        return                  # a directory nobody has classified. Silence beats a guess.
    src = os.path.join(PROJECTS, SOURCE_KEY, "memory")
    # The pointer file FIRST, so the live index wins on any slug listed in both. A line
    # that has moved is still the line he wrote, and this hook must never invent a
    # replacement - see index_lines().
    idx = index_lines(PROJECTS_INDEX)
    idx.update(index_lines(os.path.join(src, "MEMORY.md")))
    copied, missing = [], []
    try:
        os.makedirs(dest)
    except Exception:
        pass
    for slug in (entry.get("files") or []):
        s = os.path.join(src, slug + ".md")
        if not os.path.isfile(s):
            missing.append(slug)          # a name in the manifest that has since rotted
            continue
        try:
            # byte-for-byte, so the LF endings that folder requires survive the copy
            with open(s, "rb") as a:
                blob = a.read()
            with open(os.path.join(dest, slug + ".md"), "wb") as b:
                b.write(blob)
            copied.append(slug)
        except Exception:
            missing.append(slug)
    body = ["# Memory Index"]
    for slug in copied:
        body.append(idx.get(slug) or ("- [%s](%s.md)" % (slug, slug)))
    try:
        with open(os.path.join(dest, "MEMORY.md"), "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(body) + "\n")
    except Exception:
        return
    log("bootstrap: %s -> %s, %d copied, %d missing" % (name, key, len(copied), len(missing)))
    # Only when something was actually copied: an index with no entries gives the loader
    # nothing to inject, so its absence downstream would prove nothing about the ordering.
    if copied:
        arm_ordering_probe(d.get("session_id"), key, d.get("transcript_path"))
    out = ["Context Guard just created this project's own memory folder.",
           "",
           "  project : %s" % name,
           "  folder  : %s" % dest,
           "  copied  : %d memories (the shared folder was NOT changed)" % len(copied),
           ""]
    if missing:
        # Never let a bootstrap quietly drop a memory. A short set that looks complete is
        # worse than a short set that says what is missing.
        out += ["NOT copied - the manifest names these and they are not on disk:",
                "  " + ", ".join(missing),
                ""]
    # BELT AND BRACES. It is not known whether Claude Code's memory loader runs before or
    # after SessionStart. If it runs first, a folder created here is only read by the NEXT
    # chat - and that failure looks exactly like success, because chat #2 works fine.
    # Handing the index back as additionalContext makes the ordering stop mattering.
    out += ["This project's memories, in context now regardless of when the folder is read:",
            ""] + body
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                             "additionalContext": "\n".join(out)}}))


def cmd_report():
    """Human-readable: what the guards have actually been doing. Run by hand.

    Every row carries its dates, and the guards whose firing IS the feature print their
    actual lines. Measured 17 Sep 2026: an earlier chat read "image re-reads blocked: 3"
    off this report and concluded the re-read guard was broken and that text reads were
    the real cost. Both conclusions were wrong. The three blocks were real, of probe.png
    and hooktest.png, inside a two-minute window on 11 Sep while the guard was being
    tested - and nothing in the report said so. A bare number is unfalsifiable; a number
    with its dates and its filenames can be checked in one glance."""
    print("CONTEXT GUARD - what has actually happened")
    print("=" * 64)
    try:
        with open(LOG, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except Exception:
        lines = []
    for label, needle, show in REPORT_EVENTS:
        hits = [l for l in lines if needle in l]
        if not hits:
            print("  %-24s %5d   never fired" % (label, 0))
            continue
        days = [d for d in (log_day(l) for l in hits) if d] or ["?"]
        # two or more events all on ONE day is the tell that a counter is made of its
        # own tests. A single event is not evidence either way and is left alone, or
        # the warning becomes noise and stops being read.
        flag = ("   <- all on one day, check these are not just its own tests"
                if len(hits) >= 2 and days[0] == days[-1] else "")
        print("  %-24s %5d   %s -> %s%s" % (label, len(hits), days[0], days[-1], flag))
        if show:
            for l in hits[-5:]:
                print("        %-9s %s" % (log_day(l), l[20:].strip()))

    print(chr(10) + "-- sessions being tracked --")
    for p in sorted(glob.glob(os.path.join(STATE, "*.json"))):
        try:
            with open(p) as f:
                st = json.load(f)
        except Exception:
            continue
        w = st.get("warned_ctx", 0) or st.get("warned_at", 0)
        print("  %-40s last warned at %3dk, %d image(s) tracked"
              % (os.path.basename(p)[:-5][:40], round(w / 1000), len(st.get("reads", {}))))

    d = os.path.join(HOME, ".claude", "handoff")
    print(chr(10) + "-- request ledgers (his own words, never consumed) --")
    for p in sorted(glob.glob(os.path.join(d, "*.requests.md"))):
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                n = f.read().count(chr(10) + "### ")
        except Exception:
            n = 0
        print("  %-42s %d request(s)" % (os.path.basename(p), n))

    # ".used" in any form means already consumed - the legacy flat ".used.md" name was
    # being listed as still waiting, which reads as "a note nobody picked up"
    pend = [p for p in sorted(glob.glob(os.path.join(d, "*.md")))
            if not p.endswith(".requests.md") and ".used" not in os.path.basename(p)]
    print(chr(10) + "-- handoff notes waiting to be picked up --")
    for p in pend:
        print("  %s (%d chars)" % (os.path.basename(p), os.path.getsize(p)))
    if not pend:
        print("  none")
    report_ordering()


def report_ordering():
    """Surface the ordering verdict where somebody will actually see it.

    A file in a state directory that nothing prints is a reminder wearing a disguise: the
    probe would answer the question and the answer would sit unread. Printed only when
    there is something to say, so the report does not grow a permanent empty section."""
    if os.path.exists(ORDER_ANSWER):
        try:
            with open(ORDER_ANSWER, encoding="utf-8", errors="replace") as f:
                body = f.read().strip()
        except Exception:
            return
        print(chr(10) + "-- the SessionStart ordering, answered --")
        for line in body.splitlines():
            print("  " + line)
        return
    if os.path.exists(ORDER_PROBE):
        try:
            with open(ORDER_PROBE, encoding="utf-8") as f:
                rec = json.load(f) or {}
        except Exception:
            return
        print(chr(10) + "-- the SessionStart ordering --")
        print("  waiting on session %s, which furnished %s. The verdict lands here by"
              % (rec.get("sid"), rec.get("key")))
        print("  itself once that chat has taken a turn; nobody has to go and look.")


if __name__ == "__main__":
    a = sys.argv[1:]
    if "--size" in a:
        cmd_size()
    elif "--reread" in a:
        cmd_reread()
    elif "--bash" in a:
        cmd_bash()
    elif "--ledger" in a:
        cmd_ledger()
    elif "--checkpoint" in a:
        cmd_checkpoint()
    elif "--skills" in a:
        cmd_skills()
    elif "--attribute" in a:
        cmd_attribute()
    elif "--bootstrap" in a:
        cmd_bootstrap()
    elif "--report" in a:
        cmd_report()
