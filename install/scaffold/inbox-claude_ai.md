# Claude.ai chats

**Preferred: live pull (all chats, in & out of projects, kept fresh).**

```bash
gigabite claude-login      # store your claude.ai sessionKey in the keychain (secure prompt)
gigabite claude-sync       # pull everything; incremental on every ingest afterwards
```

See docs/CLAUDE_AI.md in the repo for details and caveats.

---

**Fallback: manual export** (if you'd rather not use a token).

1. claude.ai → Settings → Privacy → Export data → you get a `.zip` with
   `conversations.json`.
2. Drop the `.zip` **or** the extracted `conversations.json` into this folder.
3. Run `gigabite ingest`.

Both paths write to the same index and de-duplicate by conversation id, so you
can mix them. Re-dropping a newer export updates in place.
