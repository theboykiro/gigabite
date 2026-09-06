---
name: design-critique
description: Review UI or UX work — a screenshot, mock or built screen — against its stated intent and the decisions already made on that surface, separating what contradicts a decision the user has already taken from what is only an aesthetic preference.
when_to_use: The user shares or points at a design, screen, mock, flow or component and wants it reviewed — "what do you think of this screen", "critique this layout", "does this journey work", "review the widget picker". For interface and experience judgement, not code review.
allowed-tools: Bash(gigabite:*) Read Glob
---
<!-- gigabite:managed — this file is reinstalled by gigabite's install.sh. Edits here are replaced on the next run; rename it to keep your own version. -->

Design work is rarely new. There is almost always a stated intent and a trail of
decisions behind the surface being shown. Find both before offering an opinion —
reacting to a screen fresh is how a settled decision gets reopened as taste.

1. **Load the operating protocol.** `gigabite core` prints `~/.core/core.md`. Follow
   its tone: answer first, no validation openers, no hedge-stacking, push back plainly
   when the reasoning is off.
2. **Look at the work.** Read the image or file the user shared — `Read` handles
   screenshots and mocks. Describe what is actually there, not what you expect to be;
   if part of it is unreadable at that resolution, name which part.
3. **Get the intent.** What is this surface for, and what is the user trying to do on
   it? If they did not state it, ask. A critique with no intent behind it is only taste.
4. **Search the record before any judgement.**
   `gigabite search "<surface / component / flow>"`, then the project, then the design
   vocabulary — navigation, empty state, error copy, the component's own name. Pull the
   full thread with `gigabite doc <doc_id>` when a hit looks central.

Then critique in three labelled buckets. Keep the labels; the separation is the point:

- **Contradicts a decision you already made** — quote the decision and cite it
  *(source · title · date)*. This is the strongest thing you can say and the only
  bucket that is not a matter of opinion. Say it plainly and first.
- **Works against the stated intent** — name the intent, then the specific element
  fighting it, then what a user would get wrong as a result.
- **My preference** — labelled as such, unhedged, and short. Own it as opinion rather
  than dressing it up as a principle.

Rules:

- Never invent history. If recall is empty on this surface, say so and critique on
  intent alone. Do not imply a prior decision exists to make a preference land harder.
- Lead with the most serious finding. Do not open by praising the work.
- If it holds up against intent and against the record, say that in a line and stop.
  A manufactured objection costs more than it looks like it does.
