# Safeguard (c): one home for every memory file

- **Date:** 2026-09-25
- **Status:** design approved in conversation, part by part. Part 1 is already done by hand, outside the repo. Parts 2 and 3 are what this spec asks to build.
- **Scope:** part (c) of the memory safeguard, "the project copies match the shared ones". Parts (a), (b) and (d) follow in that order, each with its own spec.

## Background

Claude Code keys its memory folder off the working directory. The author runs every chat from one directory, so every chat reads and writes one memory folder, `~/.claude/projects/<SOURCE_KEY>/memory`. This spec calls it **the shared folder**. Its `MEMORY.md` is loaded into every chat.

On 19 Sep Context Guard split project memories out to keep that index small. Each project got its own folder, `~/.claude/projects/<project key>/memory`, and the `MEMORY.md` in it is **the project list**. When a chat opens with a project's label, `label_memory_index()` shows it that list. `cmd_bootstrap()` furnished each project folder by copying the project's topic files into it.

That gave every project memory two homes, and the two copies drifted apart. We scanned every transcript on the machine on 24-25 Sep. Chats saved **new** memories into the shared folder, because auto-memory follows the working directory. They **edited** existing ones in the project folder, because the label fetch said the files "are in the same folder" as the list. One memory ended up with about 230 lines unique to each copy.

## Decision

**One home.** The shared folder holds every topic file. A project folder holds only its `MEMORY.md`.

Rejected:
- **Two-way sync.** Both copies stay writable, so they keep drifting. It needs a remembered base per file, and a file changed on both sides still needs the author.
- **Detect and warn only.** The author has to sort out every drift by hand.
- **Newest wins.** It silently destroys one side of a file changed on both.

## Part 1 - the one-time merge (DONE 25 Sep, outside the repo)

- All 12 memory folders (192 files) were backed up, and the backup was checked byte for byte.
- One file had changed on both sides. It got a 3-way merge, using a Claude Code file-history snapshot (`~/.claude/file-history/<session>/<path hash>@vN`) as the base; a snapshot keeps the source file's mtime. The author saw the finished file before it was applied.
- Files that were ahead on the project side were copied to the shared folder. Files that existed only in a project folder were added to it.
- The read-only drift check then read: 56 project copies identical to the shared copy, 4 different, 0 only in a project folder. Each of the 4 is either fully contained in its shared copy or was superseded by the approved merge.
- The spare copies were left in place on purpose. Until part 2 ships, the label fetch still sends chats to them.

## Part 2 - where chats read and save

### 2.1 `label_memory_index()`

Today its text ends: *"These are the index lines from <project list>, and the files they point at are in the same folder, to be read on demand."*

The new text says three things:

1. These lines come from <project list>.
2. The files they point at live in the shared folder, <absolute path>. Read and edit them there. The project folder holds only this list.
3. For a new memory about this project, save the file in the shared folder as usual, but put its one index line in <project list>, not in the shared `MEMORY.md`.

Nothing else changes. It is still called from one place, the turn a note is consumed, and it still returns "" and logs on every failure.

### 2.2 `cmd_bootstrap()`

- It stops copying topic files and writes only the project list.
- For each manifest slug that exists in the shared folder, the list carries over that slug's line, as `index_lines()` does today. One header line above the entries names the shared folder as the place the files live.
- A manifest slug that is not in the shared folder is reported as it is today ("NOT listed - the manifest names these and they are not on disk").
- The SessionStart message says "listed N memories" instead of "copied N memories".
- `arm_ordering_probe()` is armed when at least one line was listed. Today it is armed when at least one file was copied.

### 2.3 The existing project lists

Every existing project `MEMORY.md` gets the same header line, in rollout step 2. The step is idempotent and keeps each file's own line endings. The header must not match `index_lines()`'s link pattern (`](<slug>.md)`), so it is never read as an entry.

## Part 3 - the sweep (the author's rule)

The author's rule, 25 Sep: *"make sure moving forward all chats saves the memories in the correct folder or at least move them when possible at the start of chat with a hook"*. Part 2 is the first half. The sweep is the second.

### Where it runs

`sweep_memory_strays()` is called at the top of `cmd_bootstrap()`, right after `resolve_ordering_probe()` and before any give-up path. `--bootstrap` is registered for SessionStart with no matcher. So the sweep runs at the start of **every** chat, labelled or not, and on startup, resume, clear and compact alike. It needs nothing from stdin.

### Who claims a slug

A slug's claimants are:

- every project whose list has a line for it, and
- the project the manifest assigns it to, if there is one.

When a slug has exactly one claimant, the sweep is sure which project it belongs to. With zero claimants, or two or more, it is not.

### What it does (silently, recorded in the log only)

It visits every `~/.claude/projects/*/memory` folder other than the shared one, in sorted order.

1. **A topic file with no shared copy.** A topic file is any `*.md` other than `MEMORY.md`. The sweep moves it to the shared folder. Its line stays in the project list, which now points at the shared copy.
2. **A topic file identical to its shared copy.** "Identical" means byte-identical with the frontmatter `modified:` line left out of both. The sweep moves it to the backup folder.
3. **A line in the shared `MEMORY.md` whose slug has exactly one claimant, and that claimant's list already has it.** The sweep removes the line from the shared list.
4. **A line in the shared `MEMORY.md` whose slug has exactly one claimant, and that claimant's list lacks it.** The sweep appends the line to that list, then removes it from the shared list.

### What it leaves alone

- **A topic file that differs from its shared copy.** Both copies stay untouched, and the sweep reports it (see "Telling the author" below).
- **A slug with zero claimants, or two or more.** It stays in the shared list, where every chat still sees it. This is not an error, and nothing is said.
- **Anything changed in the last 10 minutes** (by mtime), meaning the topic file itself or a list the step would rewrite. Another chat may be writing it. The sweep skips it without a word, and the next chat retries.
- **Anything that is not a `.md` file, and every topic file in the shared folder.**

Rejected: deciding the claimant from the chat that wrote the memory (`originSessionId` → transcript → label). Project chats also write general lessons, and those would disappear from every other chat.

### Telling the author

- Only one case produces a message: **two different copies**. The sweep adds one line to SessionStart's additionalContext, asking the chat to tell the author in one line:
  - which memory has two different copies;
  - that both were left untouched and nothing is lost;
  - that a chat will merge them when he asks.
- Each distinct pair of copies is reported once. The sweep keeps both sha256 values in `sweep-seen.json` in the state directory, so a copy that changes again is reported again. Every occurrence is logged.
- Nothing else produces output. When there is nothing to say, the hook prints nothing.

### One JSON object

A hook that prints two JSON objects reads as silence. So `cmd_bootstrap()` collects the sweep's report and its own bootstrap message, and prints at most ONE object that joins them. Every return path goes through that single print.

### Safety

- **Nothing is ever deleted.** A move copies the bytes to the destination, checks they are identical, then removes the source. A crash in between leaves two identical copies, which the next sweep tidies as case 2.
- **Backup folder:** `~/.claude/memory-backups/<YYYY-MM-DD>-sweep/<project key>/<file>`. If the name is already taken, the sweep adds a numeric suffix.
- **Rewriting a list:**
  - Re-read the list just before writing, and abort that step if it changed.
  - Write to a temp file, then `os.replace` it into place.
  - Keep the file's own line endings, measured per file, and its final newline. Today the shared `MEMORY.md` is CRLF and the project lists are LF.
- **Lock.** The sweep creates `sweep.lock` in the state directory with `O_EXCL`, and treats a lock older than 60 s as stale, so two chats starting at once do not race. While another sweep holds the lock, this one skips silently.
- **Errors.** Any exception is logged and stops the sweep, leaving everything else as it is. The sweep never blocks bootstrap or the session.
- **Time budget.** Well under a second on today's 12 folders and ~180 topic files. The dry run measures it.

## Rollout (once, after parts 2-3 are built and the suite passes)

1. **Re-merge any drift.** If a spare changed since part 1, 3-way merge it onto the shared copy first, using its merge-time copy as the base.
2. **Add the header line** to every project list.
3. **Dry run.** Run the sweep on a copy of the real memory folders, and show the author the list of moves before anything real moves. Measured today, the dry run should show:
   - 56 identical spares going to the backup;
   - 3 lines leaving the shared list: the Context Guard memories, which stay in Context Guard's own list, where both the list and the manifest claim them;
   - the other 7 shared-list lines staying put, because no project claims them;
   - nothing else.
4. **Real run.** Run the sweep for real. Then run a one-time script that moves the 4 known-different spares to the backup. It checks each one's sha256 against its merge-time value first, and stops if any has changed.
5. **Prove it.**
   - Every project folder holds only `MEMORY.md`.
   - Every line in every project list resolves to a file in the shared folder. That already holds today: 0 missing.
   - The drift check reads 0 project copies.

## Testing

- **Failing test first** for each unit, failing for the right reason. Every new test goes into the explicit runner tuple at the bottom of `test_guard.py`, because a test outside it never runs.
- **Temp folders only.** Tests patch `PROJECTS` and the state directory to temp folders, and never touch the real ones.
- **Cases:**
  - The label fetch text names both the shared folder and the project list.
  - Bootstrap writes only `MEMORY.md`, with the header, and lists every manifest slug present in the shared folder.
  - Sweep cases 1-4 each do what they say.
  - Two different copies: both untouched, one report, and the same pair not reported twice.
  - A file changed in the last 10 minutes is skipped.
  - Zero claimants and two claimants are both left alone.
  - With nothing to do, there is no output at all.
  - A CRLF list stays CRLF.
  - While the lock is held, the sweep skips.
  - A failure between copy and remove loses nothing, and the next run heals it.
  - Bootstrap and sweep together print exactly one JSON object.
- **Full suite**, and commits proven on a fresh clone.
- Assert on structure and agreement, never on prose.

## Out of scope

- Safeguard parts (a), (b) and (d).
- Memories added to the shared list since the 19 Sep split that no project claims. Where they belong is a judgement call, and that belongs to (d).
- Updating the manifest's file lists.
- Retiring the dead project folder. That is the author's call.
- The handoff warning's first tier firing on a picked-up chat's second turn.

## Left to the plan

- The exact header wording: one line, which must not match `index_lines()`'s link pattern.
- The read-only drift check (`memcheck.py`, outside the repo) needs to learn that project lists point at the shared folder. Otherwise it keeps counting list-only memories as unreachable.
