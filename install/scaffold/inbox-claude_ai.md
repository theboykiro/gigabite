# ~/Knowledge/_sources/claude_ai

This folder is where your claude.ai chat export lands. Like everything under
`_sources/`, it is read by the tool rather than by you — a `conversations.json` is
not something anybody wants to browse — and it exists so that the chats you have had
in the browser are searchable alongside your Claude Code sessions and your meetings.

Both projectless chats and chats inside claude.ai projects are imported, with the
project name carried across as a tag, so recall can tell them apart afterwards.

## The route that works: the browser export

claude.ai has no official API for your web chats, and the internal endpoints its own
web app uses are Cloudflare-gated against anything that is not a browser. The way
around that is to run the export from inside the browser, where it carries your real
session:

1. Open claude.ai and open the developer console.
2. Paste in `install/scripts/claude-ai-safari-export.js` from the gigabite repository
   and let it run. It downloads a `conversations.json`.
3. Move that file into this folder.
4. Run `gigabite ingest`.

You can also drop the `.zip` you get from claude.ai → Settings → Privacy → Export
data, or the `conversations.json` extracted from it. The importer understands both.

Re-running the export later and dropping the newer file updates the conversations
already indexed rather than duplicating them, because entries are de-duplicated by
conversation id.

## The token route, and why it is not the default

`gigabite claude-login` stores a claude.ai session token in your macOS keychain — via
the system's own hidden prompt, so it never reaches your shell history or a file —
and `gigabite claude-sync` then pulls everything through the same internal endpoints,
incrementally. It is built, it is tested, and Cloudflare currently blocks it, which
is why the browser export above is the supported path. The mechanism, the trade-offs,
and how to rotate or remove the token are all covered in `docs/CLAUDE_AI.md` in the
repository.

Both routes write to the same index and de-duplicate against each other, so mixing
them causes no harm if the token path starts working again.
