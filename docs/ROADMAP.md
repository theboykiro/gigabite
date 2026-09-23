# Roadmap

What is still open, and the done-condition for each. Each item says what it is, how
you know it works, and what would make it the wrong thing to build.

**Status legend:** ✅ done · ◐ in progress · ○ not started

---

## ◐ Register router (spar / brief / execute)

**What.** A mode resolved from cheap signals — prompt length, imperative verbs, whether a file or
link is named, inter-prompt latency, whether the last turn was a correction — that
decides how much recall is injected, whether tools are available, and how long the
answer should be.

**Why here.** Smallest item on the list, entirely self-contained, and it fixes the
most-felt problem: recall currently fires on every prompt over two words and
injects up to five snippets regardless of whether the exchange is a three-word
volley or a two-hour research task.

**Shape.**
- Mode resolution in `features/routing.py` alongside project resolution — it is the
  second routing axis, and the module is already named for the job.
- The hook consumes it: `spar` suppresses recall entirely, `brief` injects fully
  and cites, `execute` injects fully and permits tools.
- Never switch mode inside one answer. Announce only the `spar → execute`
  transition, because that is the one where the user's data starts moving.

**Done when.** A short conversational turn injects nothing and adds no measurable
latency; a research prompt injects the same recall it does today. Both asserted in
tests against fixed prompts, so the classifier can be tuned without re-measuring by
hand.

**Wrong if.** It needs a model call to decide. That puts a round-trip in front of
every prompt to save a round-trip.

**Landed so far — the recall half only.** `resolve_register` in
`features/routing.py` classifies spar / brief / execute from prompt text alone, no
model call and no disk read; `route` returns it and skips the query entirely on
`spar`; `install/hooks/gg-recall.sh` injects nothing for that mode. Pinned by
`tests/test_register.py` (the classifier, as a fixed-prompt table) and
`TestTheRecallHook` in `tests/test_cli.py` (the real script, end to end).

**Still open before this is ✅.** Tools gated by mode, answer shape/length by mode, and the `spar → execute` announcement. The
latency clause of *Done when* is also unmet and was mis-stated — `search` is
~1-4ms of a ~220ms hook, so suppressing it saves the injected snippets and the
`search(record=True)` write, not measurable time. The ~220ms is the subprocess
spawn, and no mode can avoid it from inside. Two producers are also unwired:
`--after-correction` and `--seconds-since-last` have no caller, so the
inter-prompt-latency signal is unreachable in production.

---

## ◐ Distribution hygiene

Each of these is survivable while the only user is the person who wrote it. None is
survivable on someone else's laptop, where each becomes a silent failure the user
cannot diagnose and has no reason to trust.

| | Item | Why it becomes urgent when distributed |
|---|---|---|
| ○ | **Deletion detection** | Delete a file in `~/Knowledge` and its document, messages and passages stay indexed forever. `delete_document` exists but is unreachable. On someone else's machine this is "I deleted it and it still comes back." |
| ○ | **No index writes on the recall path** | `route` calls `search(record=True)`, so every prompt runs a write transaction — and merely *matching* a decayed document un-archives it, defeating decay by retrieval. |
| ○ | **No inline ingest in interactive commands** | `/search` runs a full ingest before answering. The heaviest operation in the codebase, on the path where latency is most visible. |
| ✅ | **CLI characterisation tests** | Done — `tests/test_cli.py`. Every command the shipped slash commands call is now pinned, including the six-key `route --json` contract the recall hook parses. These exist so the rest of this table is a refactor with a safety net. |
| ○ | **Real logging** | Zero `logging` calls. The scheduled daily job sends all three of its stages to `/dev/null 2>&1 \|\| true`, so it cannot report a failure at all — the launchd log it writes to will always be empty. |
| ○ | **Installer robustness** | `install.sh` is 220 lines of bash embedding two Python programs that rewrite `~/.claude/settings.json`, `~/.claude/CLAUDE.md`, `~/.zshrc` and `~/.bash_profile`, with zero test coverage. Same for the 52-line recall hook, which is the primary product surface and has none either. |
| ○ | **The baked-in binary path** | The hook hard-codes the install-time path to the `gigabite` binary and `/usr/bin/python3`. Move or rename the directory and recall dies silently, permanently, with no error anywhere. |
| ○ | **Packaging + CI** | No `pyproject.toml`, no pinned dependencies, no CI. It runs on system Python 3.9 by accident rather than by decision. |
| ○ | **Config file** | ~15 tuning constants live in source: BM25 weights, the transcript penalty, the hook score threshold and snippet cap, passage target length. All of them are per-corpus and none of them is reachable without editing the package. |

---

## Deliberately not on this list

**Multi-tenancy, a server, cross-instance sync.** Separate installs do not need to
talk to each other. Keeping it local-first and single-user is what makes the whole
privacy model simple enough to be true; adding a server would trade that for a
capability nobody asked for.

**Embeddings and reranking.** BM25 over normalised passages measures well at the
current corpus size, and the retrieval quality problem was row shape rather than
ranking. Revisit when a measured eval — not an impression — shows keyword retrieval
missing. `tools/eval_recall.py` is where that argument gets settled.

**A GUI.** One interface, and it is Claude Code.
