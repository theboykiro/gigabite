---
name: meeting-prep
description: Assemble a brief for an upcoming or named meeting out of the user's own history — what has already been said and decided on the subject, which threads are still open, and which questions were asked and never answered.
when_to_use: The user is heading into a meeting, or names one, and wants to walk in prepared — "what do I need to know before the acme call", "prep me for my 3pm", "where did we leave things with contoso". Also when they ask what was already agreed, or what is still outstanding, ahead of a conversation.
allowed-tools: Bash(gigabite:*) Read
---
<!-- gigabite:managed — this file is reinstalled by gigabite's install.sh. Edits here are replaced on the next run; rename it to keep your own version. -->

The user has a meeting coming up. They already own the context for it — it is in the
index. Assemble the brief from that; do not ask them to hand you back things they have
already said.

1. **Load the operating protocol.** `gigabite core` prints `~/.core/core.md`. Follow
   its tone: answer first, no validation openers, no hedge-stacking, push back plainly
   when the reasoning is off.
2. **Locate the meeting.** If they named one, use that. If they said "my next" or
   "today's", run `gigabite calendar agenda --day today` (or `--day next`) and take the
   title, time and attendees from what it prints. If nothing is scheduled and nothing
   was named, ask which meeting — do not guess at one.
3. **Search the record before writing a line of the brief.**
   `gigabite search "<meeting title>"`, then again on the subject, the attendees and
   the project. Vary the terms if the first pass is thin, and pull the whole thread
   with `gigabite doc <doc_id>` when a hit looks central.

Then write the brief, short enough to read standing up:

- **Where this stands** — two or three lines of what has already been decided, each
  cited *(source · title · date)*.
- **Open threads** — what was raised and never closed. This is the part that earns
  the brief.
- **Unanswered questions** — questions that were put and got no answer, kept close to
  how they were asked.
- **Worth raising** — at most three, and only where the history actually supports them.

Never invent history. If recall comes back empty, say so plainly — nothing in their
history on this — and stop there. A brief assembled from general knowledge is worse
than no brief, because it reads exactly like one that came off the record.
