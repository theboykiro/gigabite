# ~/.core/capability

Reusable, general-purpose capability that sits *beside* the core protocol —
always available, never a project. PM frameworks, methods, command templates,
and modular SOPs (standard operating procedures) live here.

An SOP defines how a process runs (e.g. build → QA → user-review agent chain).
Agents spawned for a task load the SOP relevant to their role. Keep each SOP a
separate, versioned file so they can be mixed and matched per workflow.

Nothing here is project knowledge — that belongs in `~/.knowledge/{project}/`.
