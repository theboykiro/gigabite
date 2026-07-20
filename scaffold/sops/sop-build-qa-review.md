---
name: build-qa-review
role: builder, qa, reviewer
summary: Build → QA → user-review agent chain. How a feature gets built, tested, and reviewed before it reaches the user.
---

# SOP: Build → QA → User Review

## Purpose

A feature does not reach the user as raw output. It passes through three roles in
order — **builder**, **qa**, **reviewer** — each with a hand-off gate the work must
clear before the next role starts. The chain exists to catch defects and scope drift
before the user spends attention on them.

## When to use

- Any non-trivial change to code or a deliverable that the user will act on.
- Skip it for one-line answers, lookups, or throwaway exploration — the chain is
  overhead that only earns its place when there's something real to ship.

## Roles and sequence

### 1. Builder
- Restate the task as an explicit target: what "working" means, in one or two lines.
- Run `gigabite search "<feature / area>"` first — pull prior decisions, constraints,
  and past attempts from the user's own history before writing anything new. Do not
  re-litigate a decision already on record.
- Build the smallest thing that fully meets the target. No gold-plating, no
  half-done. Match existing patterns and style in the codebase.
- Self-check: does it run? Does it do what the target said?

**Hand-off gate → QA:** builder states the target, what was changed (file paths),
how to exercise it, and any assumption made. If the target isn't met, the builder
does not hand off — it iterates or flags the blocker.

### 2. QA
- Exercise the change against the builder's stated target, end-to-end — not just
  "it typechecks." Drive the actual flow.
- Test the edges: empty input, missing files, the failure path, idempotency where
  it matters.
- Report defects as *reproduction + expected vs. actual*, not vague impressions.

**Hand-off gate → Reviewer:** QA reports pass/fail per target line plus every defect
with repro. A failing target goes back to the builder — it does not advance.

### 3. Reviewer
- Confirm the work matches the *user's* intent, not just the builder's target — scope
  creep and misread requirements surface here.
- Check the non-negotiables: nothing fabricated, no secrets in output, no security
  control weakened, changes are reversible/auditable.
- Judge altitude: is this the simplest thing that works, or did machinery creep in?
- Write the user-facing summary in the core tone — lead with the outcome, name any
  real risk once, no padding.

**Hand-off gate → User:** reviewer signs off only when the definition of done is met.
Otherwise it returns the work with specific, actionable notes.

## Definition of done

- Meets every line of the builder's stated target.
- QA-exercised end-to-end; edge and failure paths covered; no open defects.
- Reviewer-confirmed against user intent; non-negotiables clean; altitude right.
- User-facing summary is tight, honest, and leads with the outcome.

## Notes

- The chain can run as three subagents or one agent wearing three hats in sequence —
  what matters is that each gate is actually cleared, not who clears it.
- A gate failure is normal and cheap here; a defect reaching the user is not.
