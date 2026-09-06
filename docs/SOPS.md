# SOPs & subagents

SOPs (standard operating procedures) are markdown files that define **how work moves
between roles** — who does what, in what order, and what must be true before it passes
to the next role. They are separate from the core protocol on purpose: the core governs
*how the operator works* in general; an SOP governs the *hand-offs within one chain*.

## What is not an SOP

The test is whether it describes a hand-off. If it says what one role gives another
and the gate the work must clear first, it is an SOP. If it says how to produce an
output for a person, it is a **skill** — a different mechanism, matched to a task
rather than loaded by a role.

Two files here failed that test and were removed: one described how to make a product
decision, the other how to run a research pass. Both were useful and neither was a
process; they were skills carrying a `role:` field, and shipping them as SOPs made the
category meaningless. If you want them, they belong in a skill.

## What's here

Scaffold SOPs (`install/scaffold/sops/`), installed to `~/.core/capability/sops/`:

| SOP | Role(s) | What it governs |
|---|---|---|
| `sop-build-qa-review.md` | builder, qa, reviewer | Build → QA → user-review chain with a hand-off gate at each step and a definition of done before work reaches the user. |

Scaffold subagents (`install/scaffold/agents/`), installed to `~/.claude/agents/`:

| Agent | Loads SOP | When to use |
|---|---|---|
| `gg-builder.md` | `sop-build-qa-review` (builder role) | Build a feature/deliverable the user will act on. |
| `gg-reviewer.md` | `sop-build-qa-review` (QA + reviewer roles) | QA and review built work before it reaches the user. |
| `gg-researcher.md` | none — carries its own gather → egress-check → synthesize → cite sequence | Research/analysis needing facts from more than one place. |

## How subagents load SOPs

Each agent body instructs the agent to, before working:

1. Load its SOP from `~/.core/capability/sops/<sop-file>` and follow it.
2. Run `gigabite search "<query>"` to pull relevant prior context from the user's own
   history before doing new work or going external.
3. Load and obey the tone in `~/.core/core.md`.

The SOP file is the operating instructions; the agent frontmatter's `description`
tells Claude Code *when* to spin the agent up.

## Reading SOPs from code

`gigabite/features/sops.py`:

- `sops_dir() -> Path` — `~/.core/capability/sops/`.
- `list_sops() -> list[dict]` — each `{name, title, role, summary, path}`, parsed from
  frontmatter (title falls back to the body's H1).
- `load_sop(name) -> str | None` — full markdown by name; matches with or without the
  `.md` suffix and `sop-` prefix.

## Adding a new SOP + agent

1. **Write the SOP** in `install/scaffold/sops/sop-<name>.md` with frontmatter:
   ```
   ---
   name: <name>
   role: <role(s)>
   summary: <one line>
   ---
   ```
   Keep it tight — purpose, when to use, the sequence, hand-off criteria, definition
   of done. It has to read as usable operating instructions, not documentation.
2. **Write the agent** in `install/scaffold/agents/gg-<name>.md` with frontmatter
   `name`, `description` (when to use it), optional `tools`. The body must load the SOP
   from `~/.core/capability/sops/sop-<name>.md`, use `gigabite search`, and obey
   `~/.core/core.md`.
3. **Reinstall** so the files land in `~/.core/capability/sops/` and `~/.claude/agents/`.
   `list_sops()` picks up the new SOP automatically — no code change.
