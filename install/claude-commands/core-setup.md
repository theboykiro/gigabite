---
description: Fill in your operating protocol (~/.core/core.md) by answering a few questions
argument-hint: (nothing — just run it; stop any time and re-run to pick up where you left off)
allowed-tools: Bash(__GIGABITE_BIN__:*)
---
<!-- gigabite:managed — this file is reinstalled by gigabite's install.sh. Edits here are replaced on the next run; rename it to keep your own version. -->
The user's operating protocol is loaded into every session, and the sections marked
`[FILL]` are the ones that make it theirs rather than everyone's. Your job is to fill
them in **as a conversation**, one question at a time, and to write nothing they have
not just approved.

Here is what is still outstanding:

!`__GIGABITE_BIN__ core interview --json`

## How to run it

**Ask one question per message.** Never dump the list. Never show them JSON, slot ids,
or the word "slot". Work through `ask_next` in order — those are the sections currently
marked `[FILL]`, and they are the whole of the required set. Anything with status
`answered`, `declined` or `shipped` is settled: do not raise it.

**Offer, don't interrogate.** Each question carries two concrete `options`. Put the
question in your own plain English, then offer the first option as a recommendation in
one line and the second as the alternative — "or the other way round, if you'd rather".
They can accept one by name, edit its wording, or give their own. If they accept an
option, use its `body` exactly. If they give their own words, write the line yourself in
the same shape — one or two markdown bullets, their voice, no preamble — and read it
back before it counts as approved.

**`autonomy.grid` is three quick questions, not an essay.** Ask each entry of
`action_classes` in turn, using its `ask` text, and offer the `levels` as concrete
choices with its `suggested` one named as the default. Three short answers, then move
on. Its answer is an object: `{"local_reversible": "...", "local_destructive": "...",
"outward_facing": "..."}`, one level per class, all three required.

**Keep it short enough to finish.** Six questions plus the three autonomy ones. No
follow-ups unless an answer is genuinely unusable. Say at the start roughly how long it
is and that they can stop whenever they like.

## Writing

Nothing is written until they approve it. Save as you go — after every two or three
approved answers, and again at the end — so an interrupted session keeps what was
already settled:

```
__GIGABITE_BIN__ core apply --file - <<'JSON'
{"answers": {"<id>": "- **Their line.** …"}, "declined": []}
JSON
```

Send only slots they approved in this session; previous answers merge in on their own.
A slot they'd rather not answer goes in `declined` — that is a valid ending, and the
section keeps its honest `[FILL]` marker rather than getting a line they did not mean.

When the required ones are done, mention `ask_optional` once, in a sentence — those are
tone details that already have a working default, so "leave them as they are" is a fine
answer. Then confirm what was written: the path the tool printed, and anything still
marked `[FILL]`. Do not edit `~/.core/core.md` yourself with any other tool — this
command is the only way it gets written, and it sets the previous version aside first.

If `remaining_required` is already 0, say the protocol is complete, name what it covers
in a line, and offer to revisit any single part rather than running the whole thing again.
