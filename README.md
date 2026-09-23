# gigabite

gigabite gives Claude Code a memory of your own past work. It indexes your Claude Code
sessions, claude.ai chats, meeting notes and files on your Mac, and before Claude answers
it pulls in what's relevant from the project your message is about. Nothing leaves your
machine.

**New to gigabite?** Start with the [Quickstart](docs/QUICKSTART.md) and the guide to
[your Knowledge folder](docs/KNOWLEDGE.md). This README is the full reference.

## What you need

- macOS.
- Apple's command line tools: `xcode-select --install`. They provide `git` and
  `/usr/bin/python3`.
- Python 3.9 or later **at `/usr/bin/python3`** (Apple's). A Homebrew or pyenv Python
  doesn't count. Nothing to `pip install`.
- Claude Code, installed and run at least once.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/theboykiro/gigabite/main/bootstrap.sh | bash
```

Then **quit and restart Claude Code**. It only reads the new settings when it starts.

The one-liner checks your Mac, clones to `~/gigabite` and runs `install.sh`. Run it again
to update. If you'd rather not pipe a script into a shell, clone and run it yourself.
Clone into your home folder, not Desktop, Documents or Downloads, because macOS blocks
background jobs there:

```bash
git clone https://github.com/theboykiro/gigabite.git ~/gigabite
cd ~/gigabite && ./install.sh
```

The installer puts `gigabite` on your `PATH`, adds a managed block to
`~/.claude/CLAUDE.md`, registers its hooks in `~/.claude/settings.json`, installs `/search`
and `/core-setup`, creates `~/Knowledge` and `~/.core`, and builds the first index. It's
safe to re-run, and it never overwrites your notes or your `core.md`.

## How it works

The [Quickstart](docs/QUICKSTART.md) walks a new user through first use, and
[docs/KNOWLEDGE.md](docs/KNOWLEDGE.md) covers the Knowledge folder. The reference:

- **Recall follows the message.** When a message mentions a project's name or one of its
  keywords (the `keywords:` line in `~/Knowledge/<project>/_project.md`), that project's
  past sessions, chats and notes are added to the prompt, and Claude cites them. Start a
  message with `@project` to pick one explicitly. A message that points at no project
  recalls nothing. That's deliberate: without a project, recall can't know which
  client's history is safe to show.
- **Search across everything** with `/search <words>` in Claude Code, or
  `gigabite search "<words>"` in a terminal.
- **Your protocol:** `/core-setup` fills in `~/.core/core.md`, which is loaded into every
  session. You can also edit that file by hand.
- **Adding knowledge:** drop a file into `~/Knowledge/<project>/`, or run
  `gigabite add <file>` or `gigabite paste` (paste reads a copied transcript from your
  clipboard). The index refreshes in the background whenever a Claude Code session starts.

### Linking a folder to a project (optional)

If a folder only ever holds one project's work, link it: `gigabite project bind <name>`
in that folder (or add `--dir <folder>`). Every message there then recalls that project,
even ones that don't name it, and the link takes precedence over keywords. Claude offers
to do this in folders that look like a code workspace (a git repo, a package
manifest, a `CLAUDE.md` or `.claude/`). `gigabite project bind --none` marks a folder as
not project work, and `--forget` drops the link. Folders inside `~/Knowledge` and your
home folder can't be linked.

## Import your claude.ai chats

claude.ai has no API for your chats, so you export them from the browser:

1. Log in at claude.ai in Safari. Turn on **Safari → Settings → Advanced → Show features
   for web developers**, then open **Develop → Show JavaScript Console**.
2. Paste in the contents of `~/gigabite/install/scripts/claude-ai-safari-export.js` and
   press Enter. If pasting is blocked, type `allow pasting` first. The script downloads a
   `conversations.json` file.
3. In a terminal:
   ```bash
   mv ~/Downloads/conversations.json ~/Knowledge/.gigabite/imports/claude_ai/
   gigabite ingest
   ```

Chats inside a claude.ai project are tagged with that project's name. They show up in
recall when that name is exactly the name of a gigabite project;
[docs/CLAUDE_AI.md](docs/CLAUDE_AI.md) explains how to map names that differ. Chats
outside any project show up in `/search` only. Re-exporting later updates chats instead
of duplicating them.

## Meetings (optional)

Copy a transcript and run `gigabite paste`, or save an export into
`~/Knowledge/<project>/meetings/`. If you're on Granola with API access (Business plan),
run `gigabite integrations`. It stores your API key in the macOS keychain and schedules a
daily pull at 19:00. The installer offers this step when you run it in a terminal. See
[docs/MEETINGS.md](docs/MEETINGS.md).

## Troubleshooting

- **Nothing gets recalled.** Did you restart Claude Code after installing? Does the
  message mention the project's name or a keyword, or start with `@project`? Run
  `gigabite project list` to see each project's keywords. Short replies like "ok, go on"
  never trigger recall.
- **A linked folder recalls the wrong project.** Run `gigabite project bind <right-name>`
  in the folder, or `gigabite project bind --forget` to remove the link.
- **`gigabite: command not found`.** Open a new terminal window: the installer added
  gigabite to your `PATH` in `.zshrc` / `.bash_profile`. Or run
  `~/gigabite/bin/gigabite` directly.
- **Something recent is missing.** Run `gigabite ingest` to refresh now, then
  `gigabite status` to see what's indexed. `gigabite paths` shows where everything lives.
- **Recall is in the way.** Remove the gigabite hook from `~/.claude/settings.json`.
  `/search` still works.

## Uninstall

```bash
~/gigabite/uninstall.sh            # shows what it will remove, then asks
~/gigabite/uninstall.sh --dry-run  # only show
~/gigabite/uninstall.sh --yes      # no prompt (needed when not run from a terminal)
```

It removes the launcher, the commands, the hooks, the router block and the scheduled jobs.
**`~/Knowledge` and `~/.core` are never touched.** It then prints the `rm -rf ~/gigabite`
for you to run. To remove a stored Granola key:
`security delete-generic-password -s gigabite:granola`.

## Privacy

Everything stays on your Mac. The index is a SQLite file in `~/Knowledge/.gigabite/`,
your notes are plain files in `~/Knowledge`, and your protocol is `~/.core/core.md`.
There's no server, no cloud service and no embedding model. Apart from installing and
updating from GitHub, the only network call is the Granola pull, and only if you turn it
on. Its key is kept in the keychain.
Recall only ever shows the current project's history, so one client's material doesn't
show up in another client's folder. Keep your own backup of `~/Knowledge`: it's the one
thing a reinstall can't rebuild.

## More

- [docs/QUICKSTART.md](docs/QUICKSTART.md): install and first steps, for non-technical users
- [docs/KNOWLEDGE.md](docs/KNOWLEDGE.md): how the Knowledge folder works and how to add to it
- [docs/ROUTING.md](docs/ROUTING.md): how a prompt is matched to a project, and where files land
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): how it works
- [docs/CORE_SETUP.md](docs/CORE_SETUP.md): what `/core-setup` asks and how it writes `core.md`
- [CONTRIBUTING.md](CONTRIBUTING.md): tests (`python3 -m unittest discover -s tests`) and the one rule: only code goes in this repo

MIT licensed. See [LICENSE](LICENSE).
