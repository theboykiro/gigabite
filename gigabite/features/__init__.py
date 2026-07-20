"""gigabite features: the self-maintaining layer on top of the search index.

decay     — reference-frequency archival of untouched documents (§6).
synthesis — the gated daily synthesis feedback loop (§5).

Both operate only through the Store API and never delete content or write to
the operating system (core.md / knowledge) without explicit human approval.
"""
