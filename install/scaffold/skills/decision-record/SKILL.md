---
name: decision-record
description: Capture a decision at the moment it is made — what was decided, what was rejected and why, and what would change the answer — and persist it into the knowledge base through gigabite.
when_to_use: A decision has just been reached in conversation and should not be lost — "we've decided to…", "let's go with the widget approach", "record that", "log this decision", "note that we ruled out contoso". Also when the user is closing out a choice and wants the reasoning kept, not only the outcome.
allowed-tools: Bash(gigabite:*) Read
---
<!-- gigabite:managed — this file is reinstalled by gigabite's install.sh. Edits here are replaced on the next run; rename it to keep your own version. -->

A decision was just made. Write it down while the reasoning is still in the room — in
a month the outcome will be remembered and the reasoning will not.

1. **Load the operating protocol.** `gigabite core` prints `~/.core/core.md`. Follow
   its tone: answer first, no validation openers, no hedge-stacking, push back plainly
   when the reasoning is off.
2. **Check the record first.** `gigabite search "<the subject>"`. If this reverses or
   refines something already on file, cite it *(source · title · date)* and record the
   change rather than a fresh decision — do not re-litigate what is already settled.
   Never invent history: if recall is empty, say so plainly and carry on.
3. **Draft the record.** Four parts, no padding:
   - **Decision** — one sentence, active voice.
   - **Rejected** — each option that was on the table and the reason it lost.
   - **What would change this** — the condition, number or event that would make the
     answer different. Nobody writes this down and it is the part most worth recalling
     later; if you cannot name it, ask rather than drop it.
   - **Date, and who decided.**
4. **Show the wording, then persist it.**

**Persist only through the tool:**

    gigabite save "<the record>" --project <project> [--layer <layer>]

That routes to `~/Knowledge/{project}/{layer}/` and indexes it in the same pass. Use
`--stdin` when the record is long enough to fight with the shell. Never write into
`~/Knowledge` yourself and never into the working directory — the tool is the one
intake path, and a hand-written file lands in the wrong shape even when it lands in
the right folder.

**Do not invent a project.** `gigabite project list` shows the ones that exist; use
one of those, or the project the conversation is plainly in. Never derive a project
from the working directory — a folder name is not a project. Pick a layer that
already exists in that project, and `decisions` only if none fits.

If no project resolves, say so and hand the text over unfiled with
`gigabite paste --stdin --source note`, which leaves it at the top of `~/Knowledge`
for the user to file themselves. That is the correct outcome, not a failure to patch
by guessing — a confidently misfiled decision is worse than an unfiled one.

Report the path the tool prints. That is where the decision now lives.
