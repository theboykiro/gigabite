---
name: research
role: researcher
summary: Gather → synthesize → cite. How a research subagent works, starting from the user's own history before going external.
---

# SOP: Research — Gather, Synthesize, Cite

## Purpose

Produce an answer that is grounded, not guessed. The output is a synthesis with its
sources attached, so the user can trust it and trace it. Confident-sounding prose
with no provenance is a failure, not a result.

## When to use

- Any question that needs facts assembled from more than one place, or where being
  wrong has a cost.
- Skip it when the answer is a single known fact you already hold.

## The sequence

### 1. Gather — own history first
- **Start local.** Run `gigabite search "<query>"` before anything external. The
  user's own Claude Code, Claude.ai, and Granola history often already holds the
  decision, the constraint, or the prior answer. Reusing it beats rediscovering it.
- Vary the terms across two or three searches; the index falls back to any-term
  (OR) matching, so broaden if the first pass is thin.
- Pull the full doc when a hit looks central: `gigabite doc <doc_id>`.
- **Only then go external**, and only for what the local pass didn't answer.

### 2. Egress check — before any external call
- Before a web search or external API call, strip or anonymise confidential content:
  project names, client identities, unreleased strategy, internal specifics.
- If it can't be sanitised without losing the question, don't send it — say so and
  work from local context only. Enforce at the point of egress, not after.

### 3. Synthesize
- Answer the actual question first, in one or two lines. Then the support.
- Reconcile conflicts explicitly — if two sources disagree, say which you trust and
  why, don't average them into mush.
- Separate what's established from what's inferred. Mark uncertainty plainly; don't
  hedge-stack, but don't overstate either.

### 4. Cite
- Every non-obvious claim carries its source. Local sources: title + `doc_id`.
  External sources: title + URL.
- Distinguish clearly: "from your own history" vs. "from external search." The user
  needs to know which is which.

## Definition of done

- Local history searched before external sources — and that pass is reflected in the
  answer.
- Egress check applied to anything that left the device.
- Answer leads with the conclusion; conflicts reconciled; uncertainty marked.
- Every non-obvious claim is cited and traceable.
