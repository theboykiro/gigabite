---
description: Read a pasted calendar screenshot, file the meetings, and show today's agenda with prep
argument-hint: (paste a calendar screenshot first)
allowed-tools: Bash(__GIGABITE_BIN__:*), Write
---
The user has shared (or is about to share) a screenshot of their calendar. The
calendar itself has no AI access, so the screenshot is the input — you do the
reading; gigabite does the filing and prep.

1. **Read the screenshot** and extract every meeting as a JSON array of objects
   with keys: `title`, `start` (ISO-8601 — infer the date shown; if only a time
   is visible use today's date), `end`, `attendees` (list of names), `location`,
   `notes`. Do not invent meetings; if the image is unclear, say what you couldn't read.
2. **Write** that JSON array to `/tmp/gigabite-cal.json` (Write tool).
3. **File it:** `__GIGABITE_BIN__ calendar add --json-file /tmp/gigabite-cal.json`
4. **Show the agenda with prep:** `__GIGABITE_BIN__ calendar agenda --day today`
5. **Brief the user on their day:** one line per meeting, next meeting first. For
   each, surface the prep gigabite attached — the user's own prior context for that
   meeting — citing *(source · title)*. Keep it tight.

If no screenshot was shared, ask for one.
