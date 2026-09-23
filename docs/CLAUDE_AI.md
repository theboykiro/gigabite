# claude.ai chats

The steps are in the README ("Import your claude.ai chats"). This page covers why the
route is shaped that way, and what to do when it doesn't quite fit.

## Why a browser script

claude.ai has no official API for your web chats. The developer Messages API cannot see
them, and the internal endpoints the web app uses are behind Cloudflare, which rejects
requests that don't come from a real browser session. So the export runs inside the page:
`install/scripts/claude-ai-safari-export.js` uses your logged-in session, lists your
projects and conversations, fetches each one in full, tags it with its project name, and
downloads a `conversations.json` that the importer reads.

It uses undocumented endpoints that can change without notice, and automated access sits
in a grey area under claude.ai's terms. It's your own data, fetched with your own session
and stored only on your machine, but that's a judgement you make.

## Getting chats into recall

Recall is scoped to a project, so a chat only shows up in recall when its project matches
a gigabite project. The match is exact: a chat from the claude.ai project `Acme Website`
lands under a project called `Acme Website`, not `acme`. When the names differ, map them
in `~/Knowledge/.gigabite/aliases.json` before you run `gigabite ingest`:

```json
{"Acme Website": "acme"}
```

Chats that aren't in any claude.ai project have no project, so they only show up in
`/search`. Run `gigabite materialize` to write each indexed chat out as a markdown file
under `~/Knowledge/<project>/conversations/`. It places a chat in a project when the
chat's text matches that project's keywords, and skips chats it can't place.
`--include-unfiled` writes those too, loose at the top of `~/Knowledge`. `--dry-run`
shows what it would do.

## Anthropic's own export

claude.ai → Settings → Privacy → Export data also works. Put the `.zip` you receive, or
the `conversations.json` inside it, in `~/Knowledge/.gigabite/imports/claude_ai/` and run
`gigabite ingest`. That export has no project tags, so every chat behaves like a chat
outside a project.

## Caveats

- Re-exporting and replacing the file updates chats that are already indexed. Nothing
  is duplicated, because entries are keyed by conversation id.
- The JSON shape isn't a contract. The parser tolerates renamed fields and reports what
  it couldn't map rather than failing silently.
- The export script logs how many conversations it found. A suspiciously round number
  may mean the list was capped.
- An older version stored a claude.ai token in the keychain. If you have one, remove it
  with `security delete-generic-password -s gigabite:claude_ai`.
