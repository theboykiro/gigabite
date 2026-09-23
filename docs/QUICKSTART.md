# gigabite: give Claude a memory

**Stop re-briefing Claude. It remembers your work.**

Every Claude conversation normally starts from zero: you re-explain the project, the
decisions, the history. gigabite fixes that. It quietly keeps track of your past Claude
sessions, chats, meeting notes and documents, and brings back the right context as you
work. It all stays on your Mac.

## What you get

- **Context without the copy-paste.** Past decisions and discussions come back when you need them.
- **Answers you can check.** Claude says where each recalled point came from.
- **Clients kept apart.** Recall only draws from the project you're working in.
- **Private by design.** No cloud and no server. Nothing leaves your machine.
- **Your knowledge in plain sight.** Everything lives in a `Knowledge` folder you can open in Finder.

## Before you start

- A Mac, with Claude Code installed and opened at least once.
- Apple's developer tools. If you're not sure you have them, open Terminal and run
  `xcode-select --install`.

## Install (about 5 minutes)

1. Open **Terminal**, paste this line and press Enter:

   ```bash
   curl -fsSL https://raw.githubusercontent.com/theboykiro/gigabite/main/bootstrap.sh | bash
   ```

2. **Quit Claude Code and open it again.** It picks up gigabite on the next start.

Running the same line again later updates gigabite.

## Make it yours: set up your core (one time)

Your **core** is a short operating manual that Claude reads at the start of every
session, on every project. Memory tells Claude what you've worked on. The core tells it
how you work. Out of the box it has sensible defaults and firm confidentiality rules.
The parts only you can answer are left blank.

1. In Claude Code, type `/core-setup`.
2. Answer a few questions in conversation: how you want tricky calls handled, how
   Claude should flag what it isn't sure of, when it can act on its own and when it
   should check with you first, and, if you like, your tone of voice.

The result is a Claude that makes calls the way you would, from the first message of
every session. You can run `/core-setup` again at any time, or edit the file directly at
`~/.core/core.md`.

## How to work with it

1. **In Claude Code, choose your `Documents` folder.** Any folder except `Knowledge`
   itself works: gigabite goes by what you say, not where you are.
2. **Mention your project in your message.** That's how Claude knows which history to
   bring back. Its name is enough, as are any keywords you've given it.
3. **Feed it.** Everything gigabite knows lives in your `Knowledge` folder, one folder
   per project. Drop files in, or paste a transcript into Claude and ask it to file it.
   See [Your Knowledge folder](KNOWLEDGE.md) for how that works.

## Worth knowing

- **Search everything:** type `/search` followed by a few words.
- **Be explicit:** start a message with `@project-name` to point Claude at that project.

## If something seems off

- **Nothing recalled?** Restart Claude Code, and check that your message mentions the
  project's name or one of its keywords.
- **Something filed in the wrong project?** Drag it into the right project's folder in
  `Knowledge`.
- **Want more detail?** Everything else is in the [README](../README.md).
