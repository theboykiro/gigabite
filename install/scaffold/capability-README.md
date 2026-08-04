# ~/.core/capability

This folder holds reusable, general-purpose capability that sits *beside* the core
operating protocol: always available, never tied to a project. Frameworks, methods,
command templates, and the modular SOPs live here. Where `~/.core/core.md` describes
how you work in general, the files in this folder describe how particular kinds of
work get done.

The distinction that keeps this folder useful is that nothing in it is project
knowledge. A method for running a prioritisation call belongs here; what was decided
in last Tuesday's prioritisation call belongs in `~/Knowledge/<project>/`. If a file
in here names a client, a stakeholder, or a specific piece of internal detail, it is
in the wrong place.

## SOPs

An SOP is a standard operating procedure: a single markdown file defining how one
process runs, such as the build → QA → user-review chain, a research pass, or a
product decision. They live in `~/.core/capability/sops/`, one file per procedure,
each carrying front matter with a `name`, the `role` or roles it applies to, and a
one-line `summary`.

Subagents spawned for a task load the SOP relevant to their role before they start
work, so the procedure is instructions the agent follows rather than documentation
nobody reads. Keeping each SOP a separate, versioned file is what allows them to be
mixed and matched per workflow, and what makes a change to one procedure a change to
exactly one file. `docs/SOPS.md` in the gigabite repository covers how the loading
works and how to add a new SOP and its agent.

Anything you put in here is available on every turn, in every project, for as long as
it stays here — which is a good reason to keep it short and to prune it when a method
stops earning its place.
