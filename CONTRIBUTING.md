# Contributing

gigabite is small and opinionated, and contributions are welcome. This page covers
the one rule that is not negotiable, then the mechanics of getting a change merged —
written for people who have never sent a pull request before, because most of the
people using this are not full-time engineers.

## The one rule

**Only code goes in this repository. Never content.**

gigabite indexes your working memory, which for most people means client work,
meetings, and half-finished thinking. None of that belongs in git. That includes
examples: a test fixture named after a real client is still a real client's name in a
public repository, and it is permanent, because git keeps deleted files in history.

Concretely, before you open a pull request:

- No real client, employer, or stakeholder names — in code, docs, tests, fixtures,
  commit messages, or screenshots. Use `acme`, `example`, or a project called
  `project-a`.
- No transcripts, meeting notes, exports, or `conversations.json` files.
- No API keys, tokens, cookies, or connection strings. There is nothing in this
  project that needs one; if you think you have found an exception, open an issue
  first.
- No absolute paths from your own machine that reveal personal content.

If you accidentally commit something in this category, say so in the pull request
rather than quietly force-pushing over it. History rewriting is easier before a
branch is public than after.

## Reporting a bug or suggesting an idea

Open an issue at
[github.com/theboykiro/giga-bite/issues](https://github.com/theboykiro/giga-bite/issues).

For a bug, the useful things are: what you ran, what happened, what you expected, and
the output of `gigabite status` and `gigabite paths`. Scrub any project names before
pasting.

For an idea, describe the problem before the solution. The most valuable issues here
have been "this thing confused me and I could not tell where my file went", not
"add feature X".

You do not need to fix something to report it. A clear issue is a contribution.

## Sending a change

If you have never done this before, the whole flow is four commands and a button, and
nothing you do can damage the main project — you are working on your own copy until a
maintainer chooses to merge it.

**1. Fork.** Press "Fork" at the top of the GitHub page. You now have your own full
copy of the repository under your own account.

**2. Clone your fork and make a branch.**

```bash
git clone https://github.com/<your-username>/giga-bite.git ~/gigabite
cd ~/gigabite
git checkout -b short-description-of-change
```

**3. Make the change and run the tests.**

```bash
python3 -m unittest discover -s tests -v
```

The suite touches no network and writes nothing to your home directory — it redirects
the stores into a temporary directory. If you changed behaviour, add a test for it. If
you fixed a bug, add the test that would have caught it.

**4. Commit and push.**

```bash
git add <the files you changed>
git commit -m "fix(search): stop dropping quoted phrases"
git push -u origin short-description-of-change
```

**5. Open the pull request.** GitHub will show a "Compare & pull request" button after
the push. Describe what changed and why, and how you tested it. Then it is a
conversation — expect questions, and expect to be asked to change things. That is the
normal path, not a rejection.

## What gets merged

The bar is not code quality in the abstract. It is whether the change makes the tool
easier to trust. In practice that means:

- **Small and complete beats large and partial.** A change that does one thing fully
  is easier to review, and easier to revert when it turns out to be wrong.
- **Explain the why, not the what.** The code says what it does. Commit messages and
  comments should carry the reasoning that is not recoverable from reading it.
- **Match what is already there.** Pure Python standard library, no dependencies, no
  server, no embedding model. A pull request that adds a package to install is
  unlikely to be merged unless it removes more complexity than it adds.
- **Never guess on the user's behalf.** The design refuses to invent a project folder
  for a file it cannot place, because a confidently misfiled note is worse than an
  unfiled one. Changes that trade that away for convenience will be pushed back on.

If you are planning something substantial, open an issue before you build it. It is a
cheap way to find out that a design decision you are about to undo was deliberate, and
it saves you writing something that cannot be merged.

## Security

If you find something with security or privacy implications — a path where content
could leak out of `~/Knowledge`, or a credential handled badly — please raise it
privately with the maintainer rather than in a public issue.
