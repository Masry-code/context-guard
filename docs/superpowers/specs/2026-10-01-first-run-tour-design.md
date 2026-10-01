# First-run tour - design

Date: 1 Oct 2026. Approved in chat -47 ("yeah sure it works and simple").

## Why

The user's words, 25 Sep 2026: *"for github users on first run give them the option list of
what they can do so they would understand what they will be doing with context guard okay??
like a small non heavy tutorial you know"*, and 30 Sep 2026: *"need to make new users
understand how it works"* (about the label handover). Today a new user sees only the
installer's last lines; nothing explains the handover, away mode or the other commands.

## Decisions (his picks)

1. **Where:** both - the installer prints 3 lines, the first chat shows the list once.
2. **Picking an item:** Claude shows it live (a tiny real demo), typing nothing carries on.
3. **Trigger:** a flag set by a fresh install only, plus the exact phrase `context guard tour`
   in any chat to see it again.

## What the user sees

After `python install.py` (a fresh install), three extra lines:

```
Open a new chat in Claude Code - it will show you around once.
Type "context guard tour" in any chat to see it again.
```

In the first chat, Claude's first reply opens with one sentence on what Context Guard does,
then a numbered list, then answers whatever the user actually asked:

1. **Hand a chat over** - demo: a sample label in the copy box, and the flow in plain words
   (copy it, open a new chat, paste it; the old chat archives itself and the new chat lands in
   the old chat's sidebar group).
2. **Away mode** - `afk` when leaving the desk, `back` on return. Explained, NOT armed.
3. **Pause it for one chat** - demo: Claude pauses THIS chat for real
   (`guard.py --pause <session-id> "<reason>"`) and says how to undo it (`--resume`).
4. **See what costs you** - demo: Claude runs `audit.py --deep` and sums up their own most
   expensive old chats in plain words.
5. **Updates** - it says once a day when one is ready; `python install.py --update` applies it.
   Explained only.
6. **Switch parts off** - the empty files `no-ceiling`, `no-memory-nag`, `no-update-check` in
   `~/.claude/context-guard/`. Explained only.

The list ends: "Type a number to try one, or just carry on."

## Components

- **`tour.md`** (repo root, new): the list above, as the instruction text Claude is given -
  item titles, what to say, what to demo. The ONE source of the wording. Generic: no private
  project names.
- **`install.py`**: on a FRESH install - the settings had none of Context Guard's hooks before
  this run, it is not `--dry-run`, `--uninstall` or `--update` - create the empty flag
  `~/.claude/context-guard/tour-pending` (respecting `--home`) and print the 3 lines.
  A re-install that changes nothing, an update, and an uninstall never create it.
- **`guard.py --bootstrap`** (SessionStart; it reads stdin, so it knows the session id the pause demo needs): when the
  flag exists, read `tour.md`, add it to `additionalContext` with a one-line instruction (show
  this once at the top of your first reply, then answer the user), and delete the flag - it
  spends itself, like a handoff note. A missing or unreadable `tour.md` logs and shows nothing
  (the flag is still removed, so a broken install does not nag every chat).
- **`guard.py --size`** (UserPromptSubmit): when the whole message, normalised the same way as
  the away phrases (`normalised_prompt`), equals `context guard tour`, add `tour.md` to the
  context with the same instruction. A sentence that merely mentions the tour does nothing.
  It must not disturb the away toggle or a handoff pickup on the same turn.

## Error handling

- Every file operation is wrapped; a failure logs and stays silent - the tour is never worth a
  broken hook.
- SessionStart also fires for a RESUMED chat, so the flag may be spent there. Accepted: the
  phrase brings the tour back.

## Testing (test_guard.py, through the real entry points)

1. Fresh install into a temp home creates `tour-pending` and prints the 3 lines; a second
   install, `--dry-run`, `--uninstall` and an install over existing hooks do not create it.
2. `guard.py --bootstrap` with the flag: the context carries the tour, and the flag is gone; a
   second run carries no tour. Without the flag: no tour.
3. `guard.py --size` with exactly `context guard tour` (any case, trailing punctuation as the
   away phrases allow) carries the tour; `what is the context guard tour?` does not.
4. `tour.md` contains none of the names in `~/.claude/context-guard/private-names.txt` when
   that file exists (blank and `#` lines stripped first).

## Out of scope

A clickable card, a web page, translations, any change to the handover itself.
