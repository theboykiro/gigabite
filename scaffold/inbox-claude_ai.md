# Drop your Claude.ai export here

Browser chats can't be pulled programmatically, so this is the supported path.

1. Go to **claude.ai → Settings → Privacy → Export data**. You'll get an email
   with a `.zip` (or `.dms`) containing `conversations.json`.
2. Drop the `.zip` **or** the extracted `conversations.json` into this folder.
3. Run `gigabite ingest` (or `/search` in Claude Code — it refreshes first).

Re-dropping a newer export updates the index; unchanged files are skipped.
