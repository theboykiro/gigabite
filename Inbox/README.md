# Inbox — drop files here

This is the one folder you need. Drag a file in, run one command, and it gets
filed into your knowledge base. **No AI required** — it's plain Python, so it
works when you're out of credits, offline, or just in a hurry.

## How to use it

1. Drag the file into this folder (`Inbox/`).
2. Run:

   ```
   bin/gigabite file
   ```

   (or just `gigabite file` if it's on your PATH — and it also happens
   automatically every time `gigabite ingest` runs.)

3. It prints what it filed and where. Done.

Want to see what *would* happen without changing anything?

```
bin/gigabite file --dry-run
```

## What you can drop

| Works | Extension |
|---|---|
| Notes, meeting notes, docs | `.md`, `.markdown`, `.txt` |
| Transcripts / subtitles | `.vtt` (timestamps stripped automatically) |
| Structured exports | `.json` |

Anything else (PDF, Word, images, audio) is **not** opened or guessed at — it
goes to `_needs-triage/` untouched. Nothing is ever deleted.

## Where it goes

Files are saved into `~/.knowledge/<project>/<layer>/`. The project is worked out
from the filename and the first part of the text, matched against the keywords in
each project's `_project.md`.

## Forcing a project (recommended when it matters)

Drop it in a subfolder named after the project instead of at the top:

```
Inbox/acme/pricing-call.md            -> project: acme
Inbox/acme/delivery/pricing-call.md   -> project: acme, layer: delivery
```

The folder name is matched loosely — `acme`, `Acme` and `ACME` all hit the same
project if that's what it's called. If the folder doesn't match a project you
already have, it's ignored and the contents are auto-detected as usual.

To see your projects and their keywords:

```
bin/gigabite project list
```

## The two special folders

- **`_filed/<date>/`** — your original files, moved here after being filed. The
  copy in your knowledge base is what gets searched; this is your safety net. If
  two files have the same name, the second becomes `name-2.md`. Nothing is
  overwritten and nothing is deleted. Clear it out yourself whenever you like.

- **`_needs-triage/`** — files that couldn't be filed confidently. It never
  guesses at a project. Common reasons:
  - no project matched → move it into an `Inbox/<project>/` folder and re-run
  - unsupported file type → convert or copy the text into a `.md` file
  - the file was empty, or wasn't readable text

  Fix the cause, move the file back up into `Inbox/`, and run `gigabite file`
  again.

## A note on privacy

Everything in this folder is ignored by git (only this README is committed), so
client and meeting content never reaches GitHub. Each filed note is stamped with
`origin: inbox/<the path you dropped it at>` and `share: private`.
