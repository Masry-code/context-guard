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
