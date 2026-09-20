# Handoff note — REQUIRED STRUCTURE

The next chat starts cold. It has the memory files and this note, nothing else.
Write for someone competent who was not in the room. Every section below is
mandatory; write "none" only when it is genuinely empty.

A note under ~150 lines for a working session is almost certainly too thin.

---

## 1. The one thing that matters
What the next chat must do FIRST, before opening anything else. One paragraph.
If the user asked for something that is still not delivered, that is this section.

## 2. The user's own words
Quote VERBATIM every request that is still open, in his words, not a summary.
A paraphrase drops the detail that makes a bug fixable. If a request was made
more than once, say so and give the dates — a repeated ask is the loudest signal
in the file.

## 3. What is NOT known
The open questions, spelled out. "Unknown: which gesture triggers it" is worth
more than a confident guess. Never write a guess as though it were established.
If something needs to be asked, write the exact question to ask.

## 4. State of play
- **Files in play** — full paths, and what each one is for
- **Done** — what actually landed, and how it was proven (the command, the output)
- **In progress** — anything half-finished and exactly where it stops
- **Still open** — numbered, so it can be checked off

## 5. Measurements, not conclusions
Every claim that cost effort to establish, with the number and the command that
produced it. "It is slow" is worthless; "307x slower over 9p, measured with X"
survives. Include what was RULED OUT and why, so dead ends are not retried.

## 6. Traps and gotchas
Things that will silently mislead the next chat: a check that gives false
confidence, a stale package that answers plausibly, a tool that lies about its
exit code. Name the trap that HID the problem, not just the fix.

## 7. Standing instructions and constraints
Anything the user has decided that must not be relitigated — settings he chose,
things he declined, security rules, formatting he expects. Include "do not raise
this again" items explicitly.

## 8. Environment specifics
Device serials, package names, ports, paths, which build is actually installed,
shell quirks. Anything that took a command to discover.

---

**Before finishing:** re-read section 2. If any open request is in your words
instead of his, go back and fix it. That single failure is why a bug was asked
for twice on two different days and dropped both times.

**Never hand him a filesystem path.** Not to this note, not to the log, not to
anything he is expected to act on - he cannot type a path to summon anything. The
LABEL on line 1 is the only thing that works. His words, 21 Sep 2026:
"where is the keyword why is it saying the whole path?"

**Durable lessons do NOT belong here** — they go to a memory file first, because
this note is consumed once and memory is forever.
