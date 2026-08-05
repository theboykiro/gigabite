# Your knowledge

This folder is everything you've worked on: meetings, notes, conversations,
screenshots. It's yours to open, read, rename and reorganise in Finder. Nothing in
it leaves this machine.

## Where things go

Each folder here is a **project**. Inside a project, subfolders are **layers** —
usually `meetings/`, sometimes `delivery/` or `strategy/`. So your meetings are in
`<project>/meetings/`.

To add something, put the file where it belongs and you're done. There's no inbox
and nothing to run: next time gigabite looks, it indexes the file where you left
it. Move a file to another project later and it belongs to that project instead.

If gigabite couldn't tell which project something belonged to, it left the file
loose at the top of this folder rather than guessing. Drag it into a project
whenever you like.

## Finding things

```
gigabite search "traffic drop"      # from the terminal
/search traffic drop                # from Claude Code, in any folder
```

Screenshots and PDFs are kept as files and found by filename and date — gigabite
doesn't read what's inside them.

## Handing something over

```
gigabite paste                       # a transcript you've copied
gigabite add path/to/screenshot.png  # a file from anywhere
```

Both land in a project folder here, exactly as if you'd dragged them in.

---

`.gigabite/` holds the search index and other machinery. You never need to open it.
