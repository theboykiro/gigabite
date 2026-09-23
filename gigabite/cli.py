"""gigabite command-line interface.

    gigabite ingest [--source S] [--force]
    gigabite search QUERY [--source S] [--project P] [--limit N] [--context C] [--raw] [--json]
    gigabite status [--json]
    gigabite welcome
    gigabite doc DOC_ID [--json]
    gigabite add PATH [--project P] [--layer L] [--move]
    gigabite materialize [--dry-run] [--source S] [--layer L] [--limit N]
    gigabite reindex
    gigabite paths
    gigabite integrations
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from . import __version__, config, ingest as ingest_mod, util
from .store import Store, connect

# ---- tiny ANSI helpers (auto-disabled when piped) --------------------------
_TTY = sys.stdout.isatty()


def _c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if _TTY else s


def bold(s): return _c("1", s)
def dim(s): return _c("2", s)
def cyan(s): return _c("36", s)
def yellow(s): return _c("33", s)
def green(s): return _c("32", s)


def _open() -> Store:
    return Store(connect())


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def cmd_ingest(args) -> int:
    store = _open()
    sources = [args.source] if args.source else None
    # local-only by default; live claude.ai pull only with --remote
    reports = ingest_mod.run(store, sources=sources, force=args.force,
                             remote=getattr(args, "remote", False))
    total_changed = 0
    for src, rep in reports.items():
        label = config.SOURCE_LABELS.get(src, src)
        line = (f"{green('✓')} {bold(label):<22} "
                f"changed {rep.changed}, unchanged {rep.skipped}, scanned {rep.scanned}")
        print(line)
        total_changed += rep.changed
        for note in rep.notes:
            print(f"    {dim('· ' + note)}")
        for err in rep.errors:
            print(f"    {yellow('! ' + err)}")
    print(dim(f"\n{total_changed} document(s) added/updated."))
    return 0


def cmd_search(args) -> int:
    store = _open()
    query = " ".join(args.query)
    hits = store.search(
        query, raw=args.raw,
        sources=[args.source] if args.source else None,
        project=args.project, limit=args.limit,
        include_historical=args.all,
    )
    if any(h.get("historical") for h in hits):
        # Store.search fell back across archived documents because nothing active
        # matched, and record_access has restored what it returned. Say so: the
        # results are older than the ones a default search normally shows.
        print(dim("(no active matches — searched archived; matches are now restored)\n"))
    if args.json:
        print(json.dumps(hits, indent=2, ensure_ascii=False))
        return 0
    if not hits:
        print(dim(f"No matches for {query!r}. Try broader terms, or `gigabite status` to check the index."))
        return 0

    # group by document, preserve best-rank order
    seen: dict[str, list] = {}
    order: list[str] = []
    for h in hits:
        seen.setdefault(h["doc_id"], []).append(h)
        if h["doc_id"] not in order:
            order.append(h["doc_id"])

    print(dim(f"{len(hits)} hit(s) across {len(order)} conversation(s) for {bold(query)}:\n"))
    for doc_id in order:
        group = seen[doc_id]
        top = group[0]
        label = config.SOURCE_LABELS.get(top["source"], top["source"])
        date = (top.get("created_utc") or "")[:10] or "—"
        proj = f" · {top['project']}" if top.get("project") else ""
        print(f"{bold(top['title'] or '(untitled)')}")
        print(dim(f"  {cyan(label)}{proj} · {date} · {doc_id}"))
        for h in group[: args.context + 1 if args.context else 3]:
            role = h.get("role", "")
            snip = " ".join((h.get("snippet") or "").split())
            print(f"    {dim(role + ':'):<14} {snip}")
        print()
    print(dim("Open a full conversation with: ") + f"gigabite doc <doc_id>")
    return 0


def cmd_status(args) -> int:
    store = _open()
    s = store.stats()
    if args.json:
        print(json.dumps(s, indent=2, ensure_ascii=False))
        return 0
    print(bold("gigabite index"))
    print(f"  db:        {config.DB_PATH}")
    print(f"  documents: {s['documents']}")
    print(f"  messages:  {s['messages']}")
    if s.get("earliest"):
        print(f"  span:      {s['earliest'][:10]} → {(s.get('latest') or '')[:10]}")
    print(bold("\n  by source"))
    for src, info in s["by_source"].items():
        label = config.SOURCE_LABELS.get(src, src)
        print(f"    {label:<14} {info['documents']:>5} docs   {info['words']:>9,} words")
    if not s["by_source"]:
        print(dim("    (empty — run `gigabite ingest`)"))
    return 0


# ---- welcome: the first-run brief ------------------------------------------
#
# `status` answers "what is in the index". This answers "what do I do now",
# which is the only question a new user actually has, and the reason the
# installer ends here instead of on a table of counts.
#
# Read-only by construction, so it can be re-run whenever someone forgets the
# first command — including before anything has ever been indexed, where opening
# the store would create the database it is reporting as absent.

# Sources grouped into words a non-engineer already owns. "claude_code" and
# "claude_ai" are our vocabulary, not theirs.
_WELCOME_KINDS = (
    ("conversation", (config.SOURCE_CLAUDE_CODE, config.SOURCE_CLAUDE_AI)),
    ("meeting", (config.SOURCE_MEETING, config.SOURCE_CALENDAR)),
    ("note", (config.SOURCE_NOTE,)),
)

# Some source labels only make sense to us. "Note" is the row in `status`; what the
# user did was put a file somewhere. "Meeting" is worse here — it is a fine row in
# `status` but this sentence lists where things came FROM, and "meetings" has already
# been counted in the half before it.
_WELCOME_SOURCE_LABELS = {
    config.SOURCE_NOTE: "files you added yourself",
    config.SOURCE_CALENDAR: "your calendar",
    config.SOURCE_MEETING: "transcripts you filed",
}

_WORD = re.compile(r"[^\W_]{4,}", re.UNICODE)


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _and_list(items: list) -> str:
    if len(items) < 2:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _human_date(iso: str) -> str:
    """'5 Apr 2026' — a date someone reads, not a timestamp they parse."""
    try:
        d = datetime.strptime((iso or "")[:10], "%Y-%m-%d")
    except ValueError:
        return util.short_date(iso)
    return f"{d.day} {d.strftime('%b %Y')}"


def _claude_code_present() -> bool:
    """Whether Claude Code has ever run on this machine.

    The projects directory is the signal because Claude Code is what creates it;
    its parent `~/.claude` is not, since other tools write there too. Read
    through config so a test — and a relocated install — can point it elsewhere.
    """
    return config.CLAUDE_CODE_PROJECTS_DIR.is_dir()


def _claude_code_history() -> bool:
    """Whether any Claude Code transcript exists on disk.

    Existence, not a count: the answer only picks a sentence, and the directory
    can hold thousands of files. It is asked because "the index is empty" and
    "you have no history" are different facts — transcripts present with an
    empty index means nobody has run `ingest` yet, and saying otherwise would
    tell the user their own work does not exist.
    """
    if not _claude_code_present():
        return False
    return next(config.CLAUDE_CODE_PROJECTS_DIR.rglob("*.jsonl"), None) is not None


def _tilde(path) -> str:
    """`~/Knowledge` rather than `/Users/jane/Knowledge` — shorter to read, and
    the same string the docs and the README use."""
    text = str(path)
    home = str(Path.home())
    return "~" + text[len(home):] if text.startswith(home) else text


def _welcome_example(store: Store, docs: list) -> Optional[tuple]:
    """A search that is certain to return one of the user's own documents.

    Verified by running it rather than assumed. A word lifted from a title can
    still rank nowhere — too common across the corpus, or dropped as a stop word
    — and a first command that returns nothing is the exact outcome this whole
    command exists to prevent. `record=False` keeps the check read-only: the
    ordinary search path stamps accessed_utc and un-archives what it matched.
    """
    for d in docs:
        words = [w for w in _WORD.findall(d.get("title") or "")
                 if w.lower() not in util._STOPWORDS]
        if not words:
            continue
        query = " ".join(words[:2])
        hits = store.search(query, limit=5, record=False)
        if any(h["doc_id"] == d["doc_id"] for h in hits):
            return query, d
    return None


def _welcome_candidates(store: Store) -> list:
    """Titled documents, best example first, capped so this stays cheap.

    Claude Code sessions lead because they are the ones nobody had to file: on a
    fresh install they are the whole point, and recognising your own session
    title is what makes the index believable.
    """
    order = {config.SOURCE_CLAUDE_CODE: 0, config.SOURCE_CLAUDE_AI: 1}
    docs = [d for d in store.iter_documents(include_historical=False)
            if (d.get("title") or "").strip()]
    # Two passes rather than one composite key: the dates are strings, so newest
    # first cannot be expressed in the same key as ascending source order.
    docs.sort(key=lambda d: d.get("created_utc") or "", reverse=True)
    docs.sort(key=lambda d: order.get(d["source"], 2))
    return docs[:25]


def _core_unfinished() -> None:
    """Say so when the protocol still has `[FILL]` sections, and name the way out.

    The reliable front door for setup (CORE_SETUP §6): the documented install is
    piped through `bash`, so nothing interactive can run during it, and this brief
    is the one screen that path is guaranteed to reach. Silent when the protocol
    is finished — a brief that nags a user with a complete `core.md` is noise.
    """
    from .features import core_slots

    path = config.CORE_FILE
    if not path.exists():
        return
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    if core_slots.FILL_MARKER not in text:
        return
    print(bold("Your operating protocol is still half-written."))
    print()
    print(f"  Some sections of {_tilde(path)} are marked [FILL] — how you want")
    print("  calls made, what counts as done, when to act without asking. Until")
    print("  they are filled in, you get a generic assistant over your own history.")
    if _claude_code_present():
        print()
        print("    /core-setup      in Claude Code")
        print(dim("  It asks in plain English, one question at a time, and writes"))
        print(dim("  nothing you have not approved. Stop whenever you like — it picks"))
        print(dim("  up where you left off."))
    else:
        print(dim(f"  Open {_tilde(path)} and replace the [FILL] sections, or run"))
        print(dim("  `gigabite core interview` to see what is outstanding."))
    print()


def _projects_notice() -> None:
    """Point a project-less user at `project add`. Silent once they have one.

    A project is what recall is scoped to, so with none there is nothing any
    prompt can resolve to and the tool is inert — the one state where this
    command has to be named, and it was previously named once, in the README.
    """
    from .features import save as savemod
    if savemod.list_projects():
        return
    print(bold("There are no projects yet, and recall is scoped to projects."))
    print()
    print("    gigabite project add <name> --keywords <a,b>")
    print(dim("  Until one exists, an unrecognised folder resolves to nothing and"))
    print(dim("  recalls nothing. One project is enough to start."))
    print()


def _welcome_empty() -> None:
    know = _tilde(config.KNOWLEDGE_DIR)
    unindexed = _claude_code_history()
    print(bold("gigabite is installed — and the index is empty."))
    print()
    if unindexed:
        # Transcripts on disk with nothing indexed: an ingest that has not run
        # or did not finish, which is a different problem with a one-line fix.
        print("  Nothing is indexed yet, but there are Claude Code sessions on this")
        print(f"  machine, under {_tilde(config.CLAUDE_CODE_PROJECTS_DIR)}.")
        print()
        print(bold("  One command reads all of them:"))
        print("""
    1.  gigabite ingest
    2.  gigabite welcome        (this brief, with your own work in it)
""")
    else:
        if _claude_code_present():
            print("  Nothing failed. There was simply nothing to read: Claude Code has no")
            print(f"  saved sessions here, and no files have been put under {know}.")
        else:
            print("  Nothing failed. There was simply nothing to read: Claude Code is not")
            print(f"  installed here, and no files have been put under {know}.")
        print()
        print(bold("  Shortest path to something useful — about a minute:"))
        print(f"""
    1.  mkdir -p {know}/acme
        cp <any document worth finding later> {know}/acme/
    2.  gigabite ingest
    3.  gigabite search "a word you know is in that document"
""")
    print(dim("  Optional, and worth it if you have them:"))
    print(dim("    · your Claude.ai chats — claude.ai → Settings → Export data, then"))
    print(dim(f"      unzip into {_tilde(config.SOURCES_CLAUDE_AI)}/ and run `gigabite ingest`"))
    # Redundant in the branch above, which already told them to run `ingest`.
    if _claude_code_present() and not unindexed:
        print(dim("    · every Claude Code session from now on is picked up the next time"))
        print(dim("      you run `gigabite ingest` — no filing, no export"))
    print()
    print(dim("  Run `gigabite welcome` again once there is something in there."))
    print()
    _projects_notice()
    _core_unfinished()


def cmd_welcome(args) -> int:
    if not config.DB_PATH.exists():
        _welcome_empty()
        return 0
    store = _open()
    s = store.stats()
    if not s["documents"]:
        _welcome_empty()
        return 0

    per_source = {src: info["documents"] for src, info in s["by_source"].items()}
    kinds = [_count(n, noun) for noun, srcs in _WELCOME_KINDS
             if (n := sum(per_source.get(src, 0) for src in srcs))]
    labels = [_WELCOME_SOURCE_LABELS.get(src) or config.SOURCE_LABELS.get(src, src)
              for src in config.ALL_SOURCES if per_source.get(src)]

    print(bold("gigabite is installed, and it has already read your own work."))
    print()
    print(f"  {bold(_and_list(kinds))} — from {_and_list(labels)}")
    if s.get("earliest"):
        print(f"  spanning {_human_date(s['earliest'])} to {_human_date(s.get('latest') or '')}")
    print(dim("  Indexed on this machine only. Nothing was uploaded anywhere."))
    print()

    example = _welcome_example(store, _welcome_candidates(store))
    if example:
        query, d = example
        label = config.SOURCE_LABELS.get(d["source"], d["source"])
        terminal_cmd = f'    gigabite search "{query}"'
        terminal_hit = dim(f"      \u2192 {d['title']}   ({label}, "
                           f"{_human_date(d.get('created_utc') or '')})")
    else:
        # No title-derived query could be verified, so the honest fallback is the
        # one command that cannot miss: a document opened by its own id.
        d = next(iter(store.iter_documents(include_historical=False)), None)
        query = None
        terminal_cmd = f"    gigabite doc {d['doc_id']}"
        terminal_hit = dim('      then: gigabite search "<any word you saw in it>"')

    if _claude_code_present():
        # Said first, and as an instruction rather than a note. The ambient recall
        # hook lives in settings.json and the router protocol in CLAUDE.md, and
        # Claude Code reads both only when a session starts — so someone who
        # installs with it already open sees none of this work, and concludes the
        # tool is broken rather than that it is waiting.
        print(bold("One thing before it works — reopen Claude Code."))
        print()
        print("    quit Claude Code, then start it again")
        print(dim("  It reads its settings once, when it starts. Nothing below is live"))
        print(dim("  until you have done that."))
        print()
        if query:
            print(bold("Then just ask it, the way you would ask a colleague:"))
            print()
            print(f'    "what did we decide about {query}?"')
            print(dim("  Your own history is pulled in before it answers. There is no"))
            print(dim("  command to remember, and it works in any folder."))
        else:
            print(bold("Then ask it about anything you have worked on before."))
            print(dim("  Your own history is pulled in before it answers."))
        print()
        print(dim("Prefer the terminal? This works now, without restarting:"))
        print(dim(terminal_cmd))
        print(terminal_hit)
    else:
        print(bold("Start here — this finds something, because it is yours already:"))
        print()
        print(terminal_cmd)
        print(terminal_hit)

    print()
    _projects_notice()
    _core_unfinished()
    print(dim("Also worth knowing:"))
    if _claude_code_present():
        # Demoted rather than dropped. Asking in plain English is the interface
        # worth teaching first, but a command you can reach for on purpose is the
        # thing people want the moment recall does not surface what they meant.
        print(dim("  \u00b7 /search <anything> in Claude Code, to search on purpose"))
    print(dim(f"  \u00b7 anything you drop in {_tilde(config.KNOWLEDGE_DIR)}/<project>/ is indexed"))
    print(dim("    where it sits — no filing step, no import"))
    if not per_source.get(config.SOURCE_CLAUDE_AI):
        print(dim("  \u00b7 your Claude.ai chats are not in here — only Claude Code is local."))
        print(dim("    claude.ai \u2192 Settings \u2192 Export data, unzip into"))
        print(dim(f"    {_tilde(config.SOURCES_CLAUDE_AI)}/, then run `gigabite ingest`"))
    print()
    print(dim("`gigabite status` for the index itself. This brief re-runs any time: "
              "`gigabite welcome`."))
    return 0


def cmd_doc(args) -> int:
    store = _open()
    doc = store.get_document(args.doc_id)
    if not doc:
        print(yellow(f"No document {args.doc_id!r}."))
        return 1
    if args.json:
        print(json.dumps(doc, indent=2, ensure_ascii=False))
        return 0
    label = config.SOURCE_LABELS.get(doc["source"], doc["source"])
    print(bold(doc["title"] or "(untitled)"))
    print(dim(f"{label} · {doc.get('project') or ''} · {(doc.get('created_utc') or '')[:10]}"))
    print(dim(f"{doc['doc_id']} · {doc.get('ref') or ''}"))
    print()
    for m in doc["messages"]:
        who = m["role"]
        print(bold(f"[{who}]") + dim(f"  {(m.get('ts_utc') or '')[:19]}"))
        print(m["text"])
        print()
    return 0


def cmd_add(args) -> int:
    """Store a file — a screenshot, a PDF, a transcript — in ~/Knowledge."""
    from .features import intake
    config.ensure_dirs()
    src = Path(args.path).expanduser()
    if not src.is_file():
        print(yellow(f"not a file: {src}"))
        return 1
    try:
        path, project, triaged = intake.place_file(
            src, project=args.project or "", layer=args.layer or "", move=args.move)
    except (OSError, ValueError) as e:
        print(yellow(f"couldn't store {src.name}: {e}"))
        return 1

    where = "unfiled" if triaged else project + (
        f" · {args.layer}" if args.layer else "")
    print(green(f"✓ stored: {path.name}") + dim(f"  [{where}]"))
    print(dim(f"  {path}"))
    if triaged:
        print(dim("  no project matched its name, so it is at the top of "
                  "~/Knowledge — drag it into a project folder, or re-run "
                  "with --project."))
    ingest_mod.run(_open(), sources=[config.SOURCE_NOTE])
    print(dim("indexed."))
    return 0


def cmd_materialize(args) -> int:
    """Write every indexed document out as a readable file in ~/Knowledge."""
    from .features import materialize as mat
    config.ensure_dirs()
    store = _open()
    plan, retired = mat.run(store, source=args.source, project=args.project,
                            layer=args.layer, limit=args.limit,
                            dry_run=args.dry_run, retire=not args.keep_sources,
                            include_unfiled=args.include_unfiled)

    print(bold("materialize ") + dim(str(config.KNOWLEDGE_DIR))
          + (yellow("   (dry run — nothing written or moved)") if args.dry_run else ""))
    for item in plan.actionable:
        where = item.project + (f" · {item.layer}" if item.layer else "")
        mark = yellow("?") if item.triaged else green("✓")
        print(f"  {mark} {item.date}  {item.title[:56]:<56} → {bold(where)}")
        if item.path:
            print(dim(f"      {item.path}"))

    reasons: dict = {}
    for item in plan.skipped:
        key = item.skip.split(" →")[0]
        reasons[key] = reasons.get(key, 0) + 1
    for reason, n in sorted(reasons.items()):
        print(dim(f"  · {n} skipped: {reason}"))
    unresolved = [i for i in plan.skipped if i.skip == mat.UNRESOLVED]
    if unresolved:
        print(yellow(f"\n  {len(unresolved)} document(s) have no project and were "
                     f"left alone rather than guessed at:"))
        for item in unresolved[:12]:
            print(dim(f"      {item.date}  {item.title[:64]}"))
        if len(unresolved) > 12:
            print(dim(f"      … and {len(unresolved) - 12} more"))

    moved = [e for e in retired if e["moved_to"]]
    for e in moved:
        print(dim(f"  · retired original {Path(e['path']).name} → {e['moved_to']}"))

    triaged = sum(1 for i in plan.actionable if i.triaged)
    print(dim(f"\n{len(plan.actionable)} materialized "
              f"({triaged} with no project, filed under {config.PERSONAL_PROJECT}/), "
              f"{len(plan.skipped)} skipped, {len(moved)} original(s) retired."))
    if not args.dry_run and plan.actionable:
        ingest_mod.run(store, sources=[config.SOURCE_NOTE])
        print(dim("indexed."))
    return 0


def cmd_reindex(args) -> int:
    if config.DB_PATH.exists():
        config.DB_PATH.unlink()
        for suffix in ("-wal", "-shm"):
            p = config.DB_PATH.with_name(config.DB_PATH.name + suffix)
            if p.exists():
                p.unlink()
    print(dim("index cleared; rebuilding…"))
    return cmd_ingest(argparse.Namespace(source=None, force=True))


def cmd_claude_login(args) -> int:
    from .sources import claude_ai_live
    print(bold("Store your claude.ai session token (stays on this machine)"))
    print(dim(
        "Get it (Safari): claude.ai → ⌥⌘I → Storage tab → Cookies → claude.ai →\n"
        "  copy the `sessionKey` value (starts sk-ant-sid…; double-click to grab all of it).\n"))
    print("When you press Enter, macOS's " + bold("security") + " tool will show:")
    print(cyan("    password data for new item:"))
    print(dim("That hidden line is where you PASTE the token (you won't see characters). "
              "Press Enter, then paste again at ") + cyan("retype password for new item:") + dim(".\n"))
    try:
        input("Press Enter to open the secure prompt (Ctrl-C to cancel)… ")
    except (EOFError, KeyboardInterrupt):
        print(yellow("\ncancelled."))
        return 1
    rc = claude_ai_live.store_token_interactive()
    if rc == 0:
        print(green("\n✓ Token saved to keychain. Now run: ") + "gigabite claude-sync")
        print(dim("If a keychain access prompt appears on first sync, choose \"Always Allow\"."))
    else:
        print(yellow("Token was not saved (prompt cancelled or failed)."))
    return rc


def cmd_claude_sync(args) -> int:
    from .sources import claude_ai_live
    store = _open()
    print(dim("Pulling claude.ai conversations (projectless + inside projects)…"))
    rep = claude_ai_live.ingest(store, force=args.force)
    for note in rep.notes:
        print(f"  {dim('· ' + note)}")
    for err in rep.errors[:10]:
        print(f"  {yellow('! ' + err)}")
    if rep.errors and not rep.changed:
        return 1
    print(green(f"✓ claude.ai: {rep.changed} added/updated, {rep.skipped} unchanged, "
                f"{rep.scanned} scanned."))
    return 0


def cmd_granola_login(args) -> int:
    from .features import integrations
    from .sources import granola_live
    print(bold("Store your Granola API key (stays on this machine)"))
    print(dim(
        "Get it: Granola desktop app -> Settings -> Connectors -> API keys\n"
        "  (requires Granola Business). Copy the key (starts grn_…).\n"))
    # The secure prompt itself (the "When you press Enter…" block through the
    # call to store_token_interactive()) is not duplicated here — it lives in
    # features.integrations, shared with `gigabite integrations`. Output stays
    # byte-identical to before for anyone already using this command directly.
    rc = integrations.run_secure_prompt(granola_live)
    if rc is None:
        return 1
    if rc == 0:
        print(green("\n✓ Key saved to keychain. Now run: ") + "gigabite granola-sync")
        print(dim("If a keychain access prompt appears on first sync, choose \"Always Allow\"."))
    else:
        print(yellow("Key was not saved (prompt cancelled or failed)."))
    return rc


def cmd_integrations(args) -> int:
    from .features import integrations
    integrations.run_interactive(config.REPO_ROOT)
    return 0


def cmd_granola_sync(args) -> int:
    from .features import materialize
    from .sources import granola_live
    store = _open()
    print(dim("Pulling Granola meetings (transcript + AI summary) since the last pull…"))
    rep = granola_live.ingest(store, force=args.force)
    for note in rep.notes:
        print(f"  {dim('· ' + note)}")
    for err in rep.errors[:10]:
        print(f"  {yellow('! ' + err)}")
    if rep.errors and not rep.changed:
        return 1
    print(green(f"✓ granola: {rep.changed} added/updated, {rep.skipped} unchanged, "
                f"{rep.scanned} scanned."))

    # Route each new meeting into its project folder by keyword — never trust
    # whatever folder/workspace Granola itself assigned. A meeting that resolves
    # nothing is never silently dropped: it is written loose at the top of
    # ~/Knowledge (config.UNFILED_PROJECT), same as any other unrouted intake, so
    # it's one drag away from the right project folder instead of gone.
    plan, _retired = materialize.run(store, source=config.SOURCE_MEETING,
                                     include_unfiled=True,
                                     unfiled_project=config.UNFILED_PROJECT)
    for item in plan.actionable:
        if item.triaged:
            print(f"  {yellow('?')} {item.title}: no project resolved — "
                  f"left at the top of {config.KNOWLEDGE_DIR}, drag it into "
                  f"a project folder")
        else:
            print(f"  {green('→')} {item.title} → {item.project}/{item.layer}/")
    return 0


def cmd_route(args) -> int:
    from .features import routing
    store = _open()
    prompt = " ".join(args.prompt)
    result = routing.route(store, prompt, limit=args.limit,
                           previous_was_correction=args.after_correction,
                           seconds_since_last=args.seconds_since_last)
    ask = result.get("ask") or None
    if ask:
        # Emitting the block is what spends the one question, so the record is
        # written here rather than inside `route` — a caller that only inspects a
        # routing result must not use it up. Never raises (features/bindings.py).
        from .features import bindings
        bindings.record_ask(ask["dir"])
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
        return 0
    ctx = result["context"]
    reg = result["register"]
    print(dim(f"context: {ctx['project'] or '(unscoped)'}"
              + (f":{ctx['layer']}" if ctx['layer'] else "")
              + f"  [{ctx['confidence']}] — {ctx['reason']}"))
    print(dim(f"register: {reg['mode']}  [{reg['confidence']}] — {reg['reason']}"))
    hits = result["hits"]
    if not hits:
        if ask:
            # The hook shows this; so must the human path, or the one place a user
            # runs `route` by hand is the one place the tool looks inert.
            print(ask["text"])
            return 0
        print(dim("recall suppressed for a sparring turn" if reg["mode"] == routing.SPAR
                  else "no prior context found"))
        return 0
    print(dim(f"\nrecalled {len(hits)} passage(s):"))
    for h in hits:
        label = config.SOURCE_LABELS.get(h["source"], h["source"])
        date = (h.get("created_utc") or "")[:10] or "—"
        proj = f" · {h['project']}" if h.get("project") else ""
        meta = f"{label}{proj} · {date} · {h['doc_id']}"
        print(f"  {bold(h['title'] or '(untitled)')}  {dim(meta)}")
        print(f"    {' '.join((h.get('snippet') or '').split())}")
    return 0


def cmd_save(args) -> int:
    from .features import save as savemod
    text = sys.stdin.read() if args.stdin or (args.text == ["-"]) else " ".join(args.text)
    if not text.strip():
        print(yellow("nothing to save (empty text)."))
        return 1
    try:
        # save_note validates + creates dirs; run it first so a bad project/layer
        # doesn't leave an orphan project folder behind.
        path = savemod.save_note(text, args.project, layer=args.layer,
                                 title=args.title, ts=args.date)
        savemod.ensure_project(args.project)   # add _project.md scaffold if absent
    except ValueError as e:
        print(yellow(f"can't save: {e} (check --project/--layer)"))
        return 1
    # index it immediately so it's searchable now
    store = _open()
    from .sources import notes as notes_src
    notes_src.ingest(store)
    print(green(f"✓ saved & indexed: {path}"))
    return 0


def cmd_project(args) -> int:
    from .features import bindings, save as savemod
    if args.action == "bind":
        # The answer to the hook's question. It records which project a directory
        # belongs to — or, with --none, that it is not project work — so the
        # question is asked once and never again. It creates nothing: binding to a
        # project that does not exist yet is refused, because a directory is not
        # permission to invent a project.
        # From a subfolder, bind the workspace it belongs to — the same directory
        # the hook's question names — not the subfolder the shell happens to be in.
        target = args.dir or str(bindings.workspace_root(os.getcwd()) or os.getcwd())
        if args.forget:
            # The undo, and the way to get the question back after ignoring it.
            if bindings.forget(target):
                print(green(f"✓ forgotten: {target}"))
                print(dim("  this folder will be asked about again."))
            else:
                print(dim(f"nothing recorded for {target}."))
            return 0
        reason = bindings.refuse_reason(target)
        if reason:
            # A binding covers everything under it, so a binding on $HOME or / is a
            # machine-wide one. Refuse loudly: silence here is how it went unnoticed.
            print(yellow(f"won't bind {target}: {reason}."))
            print(dim("  bind the project's own folder instead "
                      "(`--dir <path>`), or use --none there."))
            return 1
        if args.none:
            key = bindings.bind(target, None)
            print(green(f"✓ not project work: {key}"))
            print(dim("  nothing is recalled here, and you will not be asked again."))
            return 0
        if not args.name:
            print(yellow("bind what? `gigabite project bind <name> --dir <path>` "
                         "or `--none` for 'not project work'."))
            return 1
        known = {p["name"].lower(): p["name"] for p in savemod.list_projects()}
        real = known.get(args.name.strip().lower())
        if real is None:
            print(yellow(f"no project named {args.name!r} yet — create it first:"))
            print(f"    gigabite project add {args.name} --keywords <a,b>")
            return 1
        key = bindings.bind(target, real)
        print(green(f"✓ bound to {real}: {key}"))
        return 0
    if args.action == "add":
        kws = [k.strip() for k in (args.keywords or "").split(",") if k.strip()]
        lys = [l.strip() for l in (args.layers or "").split(",") if l.strip()]
        p = savemod.ensure_project(args.name, keywords=kws or None, layers=lys or None)
        print(green(f"✓ project ready: {p}"))
        return 0
    # list
    projs = savemod.list_projects()
    if not projs:
        print(dim("no projects yet — `gigabite project add <name> --keywords a,b`"))
        return 0
    print(bold("projects"))
    for p in projs:
        print(f"  {bold(p['name'])}  {dim('layers: ' + (', '.join(p['layers']) or '—'))}")
        if p["keywords"]:
            print(dim(f"    keywords: {', '.join(p['keywords'])}"))
    return 0


def _parse_meeting_header(text: str) -> dict:
    """Pull title/date from a header at the top of a transcript.

    Recognises lines like 'Meeting Title: …', 'Title: …', 'Date: Jul 20',
    'Date: 2026-07-20'. Only scans the first ~12 lines. Dates without a year
    assume the current year.
    """
    import re
    from datetime import date as _date, datetime as _dt
    out: dict = {}
    head = "\n".join(text.splitlines()[:12])
    m = re.search(r"^(?:meeting\s+)?title:\s*(.+)$", head, re.IGNORECASE | re.MULTILINE)
    if m:
        out["title"] = m.group(1).strip()[:120]
    m = re.search(r"^date:\s*(.+)$", head, re.IGNORECASE | re.MULTILINE)
    if m:
        raw = m.group(1).strip()
        iso = util.to_iso_utc(raw)
        if iso[:4].isdigit():
            out["date"] = iso[:10]
        else:  # e.g. "Jul 20" — no year in the string
            for fmt in ("%b %d", "%B %d", "%d %b", "%d %B"):
                try:
                    out["date"] = _dt.strptime(raw, fmt).replace(year=_date.today().year).date().isoformat()
                    break
                except ValueError:
                    continue
    return out


def cmd_paste(args) -> int:
    """Save whatever's on the clipboard (or stdin) into ~/Knowledge.

    Fast intake for a meeting transcript: copy it, then run this. It lands
    in exactly the place a file dragged into a project folder would — there is one
    destination, so "where did my meeting go?" has one answer however you handed
    it over.
    """
    import subprocess as _sp
    from .features import intake
    if args.stdin:
        text = sys.stdin.read()
    else:
        try:
            text = _sp.run(["pbpaste"], capture_output=True, text=True, timeout=10).stdout
        except (FileNotFoundError, _sp.TimeoutExpired):
            print(yellow("couldn't read the clipboard (pbpaste). Use --stdin instead."))
            return 1
    text = text.strip()
    if not text:
        print(yellow("clipboard/stdin is empty — copy the transcript first, then re-run."))
        return 1

    # A copied transcript often carries a header (Meeting Title:/Date:/Participants:).
    hdr = _parse_meeting_header(text)
    title = args.title or hdr.get("title") or text.splitlines()[0][:80]
    day = args.date or hdr.get("date") or __import__("datetime").date.today().isoformat()

    # A project is only ever *forced* here. Left off, routing detects it, so there
    # is one routing decision for every intake route rather than several that can
    # disagree.
    project = (args.project or "").strip()
    config.ensure_dirs()

    path, resolved, unfiled = intake.place_text(
        text, title=title, day=day, project=project, layer=args.layer or "",
        source=args.source, origin="pasted from the clipboard")

    store = _open()
    ingest_mod.run(store, sources=[config.SOURCE_NOTE])   # index what was just written

    where = "unfiled" if unfiled else resolved + (
        f" · {args.layer}" if args.layer else "")
    print(green(f"✓ saved & indexed: {title}") + dim(f"  [{where} · {day}]"))
    print(dim(f"  {path}"))
    if unfiled:
        print(dim("  no project matched it, so it is at the top of ~/Knowledge — "
                  "drag it into a project folder, or re-run with --project."))
    return 0


def cmd_calendar(args) -> int:
    from .features import calendar as cal
    store = _open()
    if args.action == "add":
        raw = sys.stdin.read() if args.stdin or args.json_file in (None, "-") else open(args.json_file).read()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            print(yellow(f"couldn't parse meetings JSON: {e}"))
            return 1
        meetings = data if isinstance(data, list) else data.get("meetings", [])
        ids = cal.add_meetings(store, meetings)
        print(green(f"✓ filed {len(ids)} meeting(s) into the index."))
        return 0
    # agenda
    items = cal.agenda(store, day=args.day)
    if not items:
        print(dim("no meetings found. Paste a calendar screenshot and I'll file them "
                  "(or `gigabite calendar add --stdin` with JSON)."))
        return 0
    for it in items:
        m = it["meeting"]
        when = (m.get("created_utc") or "")[:16].replace("T", " ") or "—"
        proj = f" · {m['project']}" if m.get("project") else ""
        print(f"{bold(m['title'])}  {dim(when + proj)}")
        for h in it["prep"]:
            label = config.SOURCE_LABELS.get(h["source"], h["source"])
            print(dim(f"    prep: {h['title']} [{label}] — ") + " ".join((h.get("snippet") or "").split())[:120])
        if not it["prep"]:
            print(dim("    (no prior context found)"))
        print()
    return 0


def cmd_core(args) -> int:
    """Print the operating protocol (~/.core/core.md) on stdout.

    This exists so a slash command can load the protocol through the gigabite
    binary it is already allowed to run. Shelling out to `cat ~/.core/core.md`
    does not work: Claude Code only permits reads inside the session's working
    directory, so the whole command substitution fails and the turn silently
    loses its protocol *and* its recall.
    """
    path = config.CORE_FILE
    if not path.exists():
        print(f"(no operating protocol at {path} — run ./install.sh)")
        return 1
    print(path.read_text(encoding="utf-8", errors="replace"), end="")
    return 0


_STATE_COLOUR = {
    "shipped": dim,
    "evidenced": green,
    "thin": yellow,
    "empty": dim,
}


def cmd_core_interview(args) -> int:
    """What the setup interview still has to ask. Read-only.

    The interview itself is `/core-setup` in Claude Code — gigabite has no model
    (CORE_SETUP §4), so the CLI half only retrieves. `--json` is what that command
    consumes; the plain output is for someone standing in a terminal.
    """
    from .features import core_interview

    plan = core_interview.plan()
    if args.json:
        print(json.dumps(plan, indent=2, ensure_ascii=False))
        return 0

    print(bold("core setup — the interview"))
    print(f"  {plan['core_path']}")
    for slot in plan["slots"]:
        if slot["status"] == "shipped":
            continue
        paint = green if slot["status"] == "answered" else (
            dim if slot["status"] == "declined" else yellow)
        mark = "required" if slot["required"] and slot["status"] == "unanswered" else ""
        print(f"  {paint(slot['status'].ljust(10))} {cyan(slot['id'].ljust(22))} "
              f"{slot['title']}{dim('  ' + mark) if mark else ''}")
    print()
    if plan["remaining_required"]:
        print(f"  {plan['remaining_required']} section(s) still marked [FILL].")
        print(dim("  Run /core-setup in Claude Code — it asks these in plain English,"))
        print(dim("  one at a time, and writes nothing you have not approved."))
    else:
        print(dim("  Nothing outstanding — every section is answered or ships filled."))
    return 0


def cmd_core_apply(args) -> int:
    """Write the slots the user approved. Only ever called with their approval.

    Input is JSON on stdin or in a file: `{"answers": {slot_id: body}, "declined": [...]}`.
    There is no "apply everything" — a slot absent from `answers` renders as it
    was, which for an unanswered one means `[FILL]`.
    """
    from .features import core_interview

    raw = sys.stdin.read() if args.file in (None, "-") else \
        Path(args.file).read_text(encoding="utf-8")
    try:
        payload = json.loads(raw or "{}")
    except ValueError as exc:
        print(yellow(f"core setup — could not read the approved answers: {exc}"))
        return 1
    if not isinstance(payload, dict):
        print(yellow("core setup — expected a JSON object with an 'answers' key"))
        return 1
    answers = payload.get("answers") or {}
    if not isinstance(answers, dict):
        print(yellow("core setup — 'answers' must be an object of slot id -> text"))
        return 1

    result = core_interview.apply_answers(answers, declined=payload.get("declined") or ())
    if result["unknown_slots"]:
        print(yellow("  ignored unknown slot(s): " + ", ".join(result["unknown_slots"])))
    if not result["written"]:
        print(yellow("core setup — nothing approved, so nothing was written"))
        return 0
    print(bold("core setup — protocol updated"))
    print(f"  {result['core_path']}")
    print(dim(f"  answers recorded in {result['answers_path']} — a later run resumes"))
    if result["remaining_required"]:
        print(dim(f"  still [FILL]: {', '.join(result['remaining_required'])}"))
    return 0


def cmd_paths(args) -> int:
    config.ensure_dirs()
    print(bold("gigabite paths"))
    print(f"  core:      {config.CORE_DIR}")
    print(f"  knowledge: {config.KNOWLEDGE_DIR}")
    print(dim("    · one folder per project; a project's subfolders are its layers"))
    print(dim("    · put a file anywhere under it — it is indexed where it sits"))
    projects = [p["name"] for p in _projects_for_paths()]
    print(dim("    · projects: " + (", ".join(projects) if projects else "none yet")))
    print(f"  machinery: {config.MACHINE_DIR}")
    print(dim("    · hidden, and nothing in it needs opening: index, raw imports,"))
    print(dim("      routing aliases"))
    return 0


def _projects_for_paths() -> list:
    from .features import save as savemod
    return savemod.list_projects()


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="gigabite", description="Local search across all your conversations.")
    p.add_argument("--version", action="version", version=f"gigabite {__version__}")
    # metavar replaces the brace-list of every choice, which would otherwise
    # re-list the commands hidden below.
    sub = p.add_subparsers(dest="command", metavar="<command>")

    pi = sub.add_parser("ingest", help="scan sources and update the index")
    pi.add_argument("--source", choices=config.ALL_SOURCES,
                     type=config.canonical_source)
    pi.add_argument("--force", action="store_true", help="re-read everything, ignore sync state")
    pi.add_argument("--remote", action="store_true", help="also run the live claude.ai pull (Cloudflare-gated; usually use the browser export)")
    pi.add_argument("--no-remote", action="store_true", help=argparse.SUPPRESS)  # back-compat (default is already local-only)
    pi.set_defaults(func=cmd_ingest)

    ps = sub.add_parser("search", help="full-text search the index")
    ps.add_argument("query", nargs="+")
    ps.add_argument("--source", choices=config.ALL_SOURCES,
                     type=config.canonical_source)
    ps.add_argument("--project")
    ps.add_argument("--limit", type=int, default=20)
    ps.add_argument("--context", type=int, default=0, help="show up to N+1 snippets per conversation")
    ps.add_argument("--raw", action="store_true", help="pass query verbatim as an FTS5 expression")
    ps.add_argument("--all", action="store_true", help="include archived (decayed) documents")
    ps.add_argument("--json", action="store_true")
    ps.set_defaults(func=cmd_search)

    pt = sub.add_parser("status", help="show index statistics")
    pt.add_argument("--json", action="store_true")
    pt.set_defaults(func=cmd_status)

    pw = sub.add_parser("welcome", help="what is already indexed, and the one command to run first")
    pw.set_defaults(func=cmd_welcome)

    pd = sub.add_parser("doc", help="print a full conversation by doc_id")
    pd.add_argument("doc_id")
    pd.add_argument("--json", action="store_true")
    pd.set_defaults(func=cmd_doc)

    pad = sub.add_parser("add", help="store a file (screenshot, PDF, transcript) in ~/Knowledge")
    pad.add_argument("path", help="the file to store")
    pad.add_argument("--project", "-p", help="force a project (default: detected from the name)")
    pad.add_argument("--layer", "-l", help="force a layer, e.g. attachments")
    pad.add_argument("--move", action="store_true",
                     help="move the original instead of copying it")
    pad.set_defaults(func=cmd_add)

    pmz = sub.add_parser("materialize",
                         help="write every indexed conversation out as a readable file")
    pmz.add_argument("--dry-run", action="store_true",
                     help="report what would happen; write and move nothing")
    pmz.add_argument("--source", choices=config.ALL_SOURCES,
                     type=config.canonical_source, help="only this source")
    pmz.add_argument("--project", "-p",
                     help="place everything in this run under one project "
                          "(use when you know where they belong and routing can't tell)")
    pmz.add_argument("--layer", help="layer to file them under (default: per source)")
    pmz.add_argument("--include-unfiled", action="store_true",
                     help="also write documents with no resolvable project, "
                          f"under {config.PERSONAL_PROJECT}/")
    pmz.add_argument("--limit", type=int, help="stop after N documents")
    pmz.add_argument("--keep-sources", action="store_true",
                     help="leave raw imports in place even once they are readable files")
    pmz.set_defaults(func=cmd_materialize)

    pr = sub.add_parser("reindex", help="clear and rebuild the index")
    pr.set_defaults(func=cmd_reindex)

    pl = sub.add_parser("claude-login", help="securely store your claude.ai session token in the keychain")
    pl.set_defaults(func=cmd_claude_login)

    pcs = sub.add_parser("claude-sync", help="pull all claude.ai chats (in/out of projects) via the stored token")
    pcs.add_argument("--force", action="store_true", help="re-fetch every conversation")
    pcs.set_defaults(func=cmd_claude_sync)

    pgl = sub.add_parser("granola-login", help="securely store your Granola API key in the keychain")
    pgl.set_defaults(func=cmd_granola_login)

    pin = sub.add_parser("integrations", help="enable/manage the \"AI brain\" integrations (Granola, more soon)")
    pin.set_defaults(func=cmd_integrations)

    pgs = sub.add_parser("granola-sync", help="pull Granola meetings (transcript + summary) and file them by project")
    pgs.add_argument("--force", action="store_true", help="re-fetch every note")
    pgs.set_defaults(func=cmd_granola_sync)

    pv = sub.add_parser("save", help="persist a note into ~/Knowledge/{project}/{layer}/ (never the working dir)")
    pv.add_argument("text", nargs="*", help="note text (or - / --stdin to read stdin)")
    pv.add_argument("--project", "-p", required=True)
    pv.add_argument("--layer", "-l")
    pv.add_argument("--title", "-t")
    pv.add_argument("--date")
    pv.add_argument("--stdin", action="store_true")
    pv.set_defaults(func=cmd_save)

    pj = sub.add_parser("project", help="create, list or bind projects (name + keywords drive context detection)")
    pj.add_argument("action", choices=["add", "list", "bind"])
    pj.add_argument("name", nargs="?")
    pj.add_argument("--keywords")
    pj.add_argument("--layers")
    pj.add_argument("--dir", help="the directory to bind (bind; default: the current one)")
    pj.add_argument("--none", action="store_true",
                    help="bind: this directory is not project work — stay quiet here")
    pj.add_argument("--forget", action="store_true",
                    help="bind: drop this directory's binding and ask about it again")
    pj.set_defaults(func=cmd_project)

    ppa = sub.add_parser("paste", help="save clipboard contents (e.g. a copied meeting transcript) into ~/Knowledge")
    ppa.add_argument("--title", "-t", help="title (default: the transcript header, else the first line)")
    ppa.add_argument("--date", "-d", help="YYYY-MM-DD (default: the transcript header, else today)")
    ppa.add_argument("--project", "-p", help="force a project (default: auto-detected)")
    ppa.add_argument("--layer", "-l", help="force a layer, e.g. meetings (needs --project)")
    ppa.add_argument("--source", choices=[config.SOURCE_MEETING, config.SOURCE_NOTE],
                     default=config.SOURCE_MEETING, type=config.canonical_source,
                     help="what the text is, recorded as provenance on the note")
    ppa.add_argument("--stdin", action="store_true", help="read from stdin instead of the clipboard")
    ppa.set_defaults(func=cmd_paste)

    pc = sub.add_parser("calendar", help="file meetings from a parsed screenshot + show agenda with prep")
    pc.add_argument("action", choices=["add", "agenda"])
    pc.add_argument("--json-file", help="path to a JSON list of meetings (add)")
    pc.add_argument("--stdin", action="store_true", help="read meetings JSON from stdin (add)")
    pc.add_argument("--day", help="agenda scope: next (default) | today | YYYY-MM-DD | all")
    pc.set_defaults(func=cmd_calendar)

    prt = sub.add_parser("route", help="resolve context + recall relevant prior conversations (powers the recall hook)")
    prt.add_argument("prompt", nargs="+")
    prt.add_argument("--limit", type=int, default=6)
    prt.add_argument("--json", action="store_true")
    # Register signals a caller may know and the recall hook does not. Both are
    # optional on purpose: the resolver must never depend on being told.
    prt.add_argument("--after-correction", action="store_true",
                     help="the previous turn corrected the answer (biases away from spar)")
    prt.add_argument("--seconds-since-last", type=float, default=None,
                     help="seconds since the previous prompt (a long pause vetoes spar)")
    prt.set_defaults(func=cmd_route)

    pco = sub.add_parser("core", help="print the operating protocol (~/.core/core.md)")
    pco.set_defaults(func=cmd_core)
    cosub = pco.add_subparsers(dest="core_command")

    pci = cosub.add_parser("interview",
                           help="which protocol slots the setup interview still has to ask")
    pci.add_argument("--json", action="store_true",
                     help="emit the plan for /core-setup to consume")
    pci.set_defaults(func=cmd_core_interview)

    pca = cosub.add_parser("apply", help="write the slots the user approved in /core-setup")
    pca.add_argument("--file", help="JSON file of approved answers ('-' or omitted = stdin)")
    pca.set_defaults(func=cmd_core_apply)

    pp = sub.add_parser("paths", help="show where things live")
    pp.set_defaults(func=cmd_paths)

    return p


def main(argv: Optional[list] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    # A command with subcommands (`run`) sets no func until one is chosen.
    if not getattr(args, "func", None):
        parser.parse_args([args.command, "--help"])
        return 0
    return args.func(args)
