# Knowledge routing — working directory vs. the knowledge base

The one rule that keeps contexts from bleeding into each other (ARCHITECTURE §4).

## The rule

- **Code** → the working directory (the folder Claude Code is pointed at).
  Version-controlled, pushed to GitHub.
- **Knowledge** (notes, context, synthesised insight, project meta) → the fixed
  central store at `~/.knowledge/{project}/[{layer}/]`, **regardless** of the
  working directory.
- The knowledge base is always **read** from `~/.knowledge/`, never from the
  working dir.

**The trap this avoids:** if a knowledge file defaults to the working directory,
it gets misfiled into whatever repo happens to be selected — the wrong project —
and separate contexts mix. That is the exact failure the design must prevent.

## How `save_note` enforces it

`gigabite/features/save.py` is the *only* correct way to persist knowledge. Every
path it produces is resolved under `config.KNOWLEDGE_DIR` (`~/.knowledge`), built
from the detected `{project, layer}` — the caller's current working directory is
never consulted. Run it from inside any repo and the note still lands in the
central store.

Project and layer names are sanitised to a single safe folder (`slugify` →
`_safe_folder`): `/`, `\` and `..` are stripped, so path traversal can't escape
the knowledge base.

## Layout

```
~/.knowledge/
  {project}/
    _project.md                 project meta: name, keywords (for detection), layers
    {layer}/                    a nested context layer (e.g. delivery, strategy)
      {YYYY-MM-DD-slug}.md      a saved note
    {YYYY-MM-DD-slug}.md        a note with no layer -> project root
  _inbox/ _historical/ .index/  reserved (leading '_' / '.'), skipped by the notes ingester
```

- `ensure_project(project, keywords, layers)` creates `{project}/` and a
  `_project.md` from the template (idempotent — existing meta is never clobbered).
- `save_note(text, project, layer=None, title=None, ts=None)` writes the note and
  returns its path.
- `list_projects()` enumerates `{project}/_project.md` for context detection.

## Indexing

`gigabite/sources/notes.py` (source `note`) scans `~/.knowledge` and indexes every
`*.md` under `{project}/[{layer}/]`, skipping reserved top-level folders (leading
`_` or `.`) and the meta files `README.md` / `_project.md`. Ingest is incremental
via an `mtime:size` signature, so re-runs only touch changed files.
