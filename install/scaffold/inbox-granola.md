# Drop your Granola meeting notes here

Granola v6 encrypts its local store behind a macOS keychain key, so notes can't
be read from disk unattended. Two ways to get them indexed:

## A. Export (guaranteed, no secrets)
In Granola, export a meeting (or select several) as **Markdown**, and drop the
`.md` files here. `.txt` and `.json` exports work too. Then `gigabite ingest`.

Both the notes and the transcript are indexed. Front-matter (`title:`, `date:`,
`project:`) is picked up if present.

## B. Live pull (experimental)
`gigabite granola-connect` reads the Granola keychain key (you approve a one-time
macOS prompt) and tries to decrypt the local cache. See docs/GRANOLA.md in the repo for
status and caveats. If it can't decrypt, fall back to A.
