# Memory keywords: a chat finds another project's memory when it needs one

- **Date:** 2026-10-02
- **Status:** design approved in conversation (chat -48). The author picked "both" (automatic hint plus a search command) and "Claude writes the keywords".
- **The author's words, 25 Sep:** *"can you put keywords for each memory so when a new chat with a different topic needs sth it can take it from the other folder memory?? so it would be all in sync in a way when needed??"*

## Background

Since the one-home merge (spec 2026-09-25), every memory file lives in one shared folder, and each project keeps only a short list (`MEMORY.md`) in its own folder. A chat sees two lists: the one Claude Code loads for its working directory, and, on a label pickup, the project list that `label_memory_index()` fetches. Every other memory is reachable only through the big pointer files (`projects-index.md`, `craft-index.md`), and only when the chat thinks to open them. It usually doesn't: the memory that would have saved an hour sits one folder away, unread.

What's missing is a way for the chat to **know** that a memory fits the moment. This spec adds keywords to every memory, plus two ways to use them.

## 1. Keywords live in the memory file

One extra line in the frontmatter, straight after `description:`:

```
keywords: adb, android, phone, apk, usb debugging, install fails
```

- 3 to 8 comma-separated entries, lowercase. An entry may be a phrase of up to 3 words.
- Include the other words the author actually uses for the same thing, not only the technical term.
- Top level, not under `metadata:`, so a reader never needs a YAML parser.

**Reader: `memory_keywords(path)`** returns `(slug, title, description, keywords, derived)`.
- It reads the frontmatter only, at most the first 40 lines, and stops at the closing `---`.
- The title is the text of the first `[...]` link that points at the slug in any list. If there's no list line, the title is the slug with `-` replaced by spaces.
- **Fallback:** a file with no `keywords:` line still works. Its keywords are derived from the slug's words and the description's words. A derived word must have 4 or more characters, must not be in `LABEL_STOP` or a short stop-word list, and is capped at 8. `derived` is then True.
- If the file is unreadable or has no frontmatter, the reader returns None, logs it, and moves on.

## 2. The catalogue

**`memory_catalogue()`** gives every memory on the machine, one entry per slug.
- It scans `PROJECTS/*/memory/*.md`, skipping `MEMORY.md`.
- When a slug has copies in several folders, the shared folder's copy wins, then the first copy in sorted folder order. The spare copies left until sweep Task 15 are therefore never shown twice.
- **Project of a slug, in this order:**
  1. the manifest project whose `files` list names it;
  2. the one project list that has a line for it;
  3. otherwise `general`.
- **Cache:** `STATE/memory-catalogue.json`, keyed per file by path, mtime and size. Only changed files are re-read, so a prompt doesn't pay for 141 file reads. If the cache is corrupt, it's rebuilt from scratch, never trusted.
- **Budget:** under 150 ms warm and under 1 s cold, measured on the author's real folders, 141 files today.

## 3. The automatic hint (UserPromptSubmit, `--size`)

**`memory_hints(out, d, sid)`** takes the hook's output object and returns it, with the hint merged in as `additionalContext` or unchanged. `cmd_size` calls it after `archive_step`, so the hook still prints ONE JSON object. It runs on paused chats too, because the hint isn't a handoff warning.
- **A busy turn gets no hint.** When the object already carries `additionalContext` (a pickup, a menu, a size warning or an archive offer), the hint waits for the next ordinary prompt and nothing is recorded. The app cuts the tail of a long injection, and an archive offer outranks a hint.

**Already in the chat, so never hinted:**
- every slug linked in this chat's own `memory_dir(path)/MEMORY.md`;
- every slug linked in the project list that `label_memory_index()` fetched for this chat. That function records the project's key in the chat's state when it appends a list;
- every `[[slug]]` named in `~/.claude/CLAUDE.md`, the standing rules;
- every slug already hinted in this chat (`st["memory_hints"]`).

**Matching:**
- The prompt is lowercased and split into words. Letters and digits count, in any script, so Arabic words match Arabic keywords.
- A keyword matches when all of its words appear in the prompt, consecutively.
- A memory's score is the number of its distinct keywords that match.
- A keyword is **rare** when 2 or fewer memories carry it.
- A memory qualifies with a score of 2 or more, OR with a score of 1 on a rare keyword of 3 or more characters. Derived keywords always need a score of 2 or more.
- Prompts of 3 words or fewer are skipped, because "a", "yes" or "go" carry no topic. So are prompts that `ledger_noise()` rejects.

**Output:**
- At most 3 memories, highest score first. A tie goes to the newer mtime.
- Each memory is one line: `- <title> (<project>) - <description, cut to 140 chars>: <absolute path>`.
- The block opens with one line: *"MEMORIES FROM OTHER PROJECTS that match his message. Titles only: open a file only if it helps with what he just asked, and say nothing about this list otherwise."*
- The first hint of a chat also carries, once, the search command from section 4.
- The whole block is capped at 900 characters.
- The shown slugs are added to `st["memory_hints"]`.

**Off switch:** `STATE/no-memory-hints`, same shape as the other switches.

**Errors:** any exception is logged and the function returns "". It never raises into `cmd_size`.

## 4. The search command (`--recall`)

`python guard.py --recall <words...>` prints the top 5 memories for the words, scored as above. Here a score of 1 qualifies, because the chat asked on purpose.
- Each result is two lines: `<title> (<project>) - <path>`, then `  keywords: <list>`.
- Nothing found: `No memory matches: <words>`.
- Exit code 0 either way, and the command reads no stdin.
- The author's `~/.claude/CLAUDE.md` gets one standing line at rollout: *"Mid-task and hit something another project may have solved? Run `python "D:/Claude/context-guard/guard.py" --recall <words>`."* `install.py` does NOT write that line for other users. The hint's once-per-chat mention of the command covers them.

## 5. Keeping keywords written

- **Saving instructions:** `memory_nag_text()` and `label_memory_index()` gain one sentence: *"Give every new memory a `keywords:` line in its frontmatter, straight after `description:` - 3 to 8 words he would actually use."*
- **The Stop check, `keywords_block(d, path)`:**
  1. It finds the memory topic files this chat wrote: Write/Edit `tool_use` blocks whose `file_path` sits in any `PROJECTS/*/memory/` and isn't `MEMORY.md`. It reads positionally, like `chat_wrote_memory()`.
  2. If any of those files still exists and has no `keywords:` line, it blocks once, naming those files and asking for the line to be added with the Edit tool. The block says nothing else.
  3. **Brakes**, the same shape as `note_head_block`: skip when `stop_hook_active` is set; the switch `STATE/no-keywords-check`; at most `NAG_MAX` blocks per chat; never twice for the same set of files and mtimes.
- **Order in `cmd_ledger`:** ceiling, then note head, then keywords, then the memory nag. That's still one decision per Stop. `keywords_block` never needs to carry the memory nag: it only fires when this chat wrote a memory, and `chat_wrote_memory()` then keeps the nag quiet anyway.
- **Known gap:** a chat whose last Stop is spent on a note-head block can still end with an unkeyworded memory. The fallback in section 1 covers it until a later chat edits the file.

## 6. The one-time fill (rollout, outside the repo)

1. **Backup:** copy the shared folder to `~/.claude/memory-backups/2026-10-02-keywords/` and check it byte for byte.
2. **Write:** an agent reads every topic file in the shared folder and writes `keywords.json` (slug -> list) in the scratchpad. Nothing in the memory folder changes yet.
3. **Review:** a review page, one table per project with slug, title and keywords, is sent to the author with SendUserFile. It's never published, because project names are private. He approves or edits it.
4. **Apply:** a script, written with the Write tool, inserts the line after `description:` in each file.
   - It keeps each file's line endings and counts CRLF and bare LF before and after.
   - It skips any file that already has a `keywords:` line.
   - It refuses a file whose frontmatter has no `description:` line, or more than one.
   - It proves every file changed by exactly one added line.
5. **Prove:** `--recall adb` lists the Android memory, and a cold catalogue build stays within the section 2 budget.

## Testing

- Write a failing test first for each unit, and check it fails for the right reason. Every new test goes into the runner tuple at the bottom of `test_guard.py`.
- Tests use temp `PROJECTS` and `STATE` folders only, and every fixture name is invented: no real project names.
- **Cases:**
  - The reader handles a keywords line, the derived fallback, a CRLF file, a BOM, and a file with no frontmatter.
  - The catalogue: the shared copy wins over a spare; project attribution follows manifest, then list, then `general`; the cache re-reads only a changed file; a corrupt cache is rebuilt.
  - Matching:
    - a phrase keyword needs consecutive words;
    - a score of 2 qualifies and a score of 1 doesn't, unless the keyword is rare;
    - a derived keyword needs a score of 2;
    - Arabic matches Arabic;
    - a prompt of 3 words or fewer gets no hint.
  - Exclusions: the chat's own list, the label-fetched list, CLAUDE.md `[[slug]]`s, and memories already hinted.
  - Output: at most 3 memories; the 900-character cap holds, tip included; the search tip appears once per chat; the off switch silences everything; the hook prints ONE JSON object.
  - `--recall` gives the top 5, and the empty case prints its one line.
  - `keywords_block`:
    - it fires on an unkeyworded memory this chat wrote;
    - it doesn't fire on a memory only mentioned in a hook's text;
    - it doesn't fire on `MEMORY.md`;
    - the brakes hold: `stop_hook_active`, the switch, the cap, and the same files and mtimes not blocked twice.
  - A busy turn (output that already carries `additionalContext`) is returned unchanged, and nothing is recorded.
- Run the full suite, and prove the commits on a fresh clone. Assert on structure, never on prose.

## Out of scope

- Hints from the tools Claude runs mid-task, through PreToolUse. The `--recall` command covers that moment.
- Loading a hinted memory's body automatically.
- Rewriting the project lists or the pointer indexes.
- Safeguard (c) Tasks 11-16, which are unchanged.
