# gigabite

gigabite gives Claude Code a memory of your own past work. It indexes your Claude Code
sessions, claude.ai chats, meeting notes and files on your Mac, and before Claude answers
it pulls in what's relevant from the project you're working in. Nothing leaves your machine.

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

## How to use it

1. **Restart Claude Code** after installing.
2. **Open Claude Code in a project folder.** On your first real question there, Claude asks
   which project the folder belongs to. Answer it and Claude runs `gigabite project bind <name>`, which
   creates the project if it's new. For a folder that isn't project work, the answer is
   `gigabite project bind --none`. Once you've answered, that folder is never asked
   about again. If you ignore the question, it comes back once per session.
3. **Then just talk.** Relevant past sessions, chats and notes from *that project only*
   are added to your prompt automatically, and Claude cites them. To reach another
   project for one prompt, name it: `@acme what did we decide about pricing?`
4. **Search across everything** with `/search <words>` in Claude Code, or
   `gigabite search "<words>"` in a terminal.
5. **Once:** run `/core-setup` to teach it your voice and working rules. It fills in
   `~/.core/core.md`, which is loaded into every session. You can also edit that file
   by hand.

The index refreshes in the background whenever a Claude Code session starts. Your notes
live in `~/Knowledge`, one folder per project, and you can open it in Finder. To add
something, drop a file into `~/Knowledge/<project>/`, or run `gigabite add <file>` or
`gigabite paste` (paste reads a copied transcript from your clipboard).

If you start Claude Code from your home folder (`~`), nothing is recalled unless your
prompt names a project. This is deliberate: with no project, recall can't know which
client's history is safe to show.

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

- **Nothing gets recalled.** Did you restart Claude Code after installing? Is the folder
  bound to a project? Run `gigabite project bind <name>` in it (or add `--dir <folder>`). Started from `~`? Open
  the project folder, or put `@project` in your prompt. Claude only asks in folders that
  look like a project (a git repo, a package manifest, a `CLAUDE.md` or `.claude/`); bind
  any other folder by hand. Short replies like "ok, go on" never trigger recall.
- **Bound the wrong project.** Run `gigabite project bind <right-name>` in the folder,
  or `gigabite project bind --forget` to be asked again.
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

- [docs/ROUTING.md](docs/ROUTING.md): how a prompt is matched to a project, and where files land
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): how it works
- [docs/CORE_SETUP.md](docs/CORE_SETUP.md): what `/core-setup` asks and how it writes `core.md`
- [CONTRIBUTING.md](CONTRIBUTING.md): tests (`python3 -m unittest discover -s tests`) and the one rule: only code goes in this repo

MIT licensed. See [LICENSE](LICENSE).
