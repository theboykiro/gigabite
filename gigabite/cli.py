"""gigabite command-line interface.

    gigabite ingest [--source S] [--force]
    gigabite search QUERY [--source S] [--project P] [--limit N] [--context C] [--raw] [--json]
    gigabite status [--json]
    gigabite doc DOC_ID [--json]
    gigabite reindex
    gigabite granola-connect [--diagnose]
    gigabite paths
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Optional

from . import __version__, config, ingest as ingest_mod
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
    reports = ingest_mod.run(store, sources=sources, force=args.force,
                             remote=not getattr(args, "no_remote", False))
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
    if not hits and not args.all:
        # transparent restore-on-access: retry across archived docs; a matching
        # archived hit is restored to active by the store's record_access.
        hits = store.search(
            query, raw=args.raw,
            sources=[args.source] if args.source else None,
            project=args.project, limit=args.limit,
            include_historical=True,
        )
        if hits:
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


def cmd_reindex(args) -> int:
    if config.DB_PATH.exists():
        config.DB_PATH.unlink()
        for suffix in ("-wal", "-shm"):
            p = config.DB_PATH.with_name(config.DB_PATH.name + suffix)
            if p.exists():
                p.unlink()
    print(dim("index cleared; rebuilding…"))
    return cmd_ingest(argparse.Namespace(source=None, force=True))


def cmd_granola_connect(args) -> int:
    from .sources import granola_live
    print(bold("Granola live connect ") + yellow("(experimental)"))
    print(dim("This reads the Granola key from your keychain — approve the macOS prompt.\n"
              "If it can't decrypt, use the export path in GRANOLA.md.\n"))
    store = _open()
    rep = granola_live.connect(store, diagnose_only=args.diagnose)
    for note in rep.notes:
        print(f"  {dim('· ' + note)}")
    for err in rep.errors:
        print(f"  {yellow('! ' + err)}")
    if rep.changed:
        print(green(f"\n✓ Indexed {rep.changed} Granola meeting(s)."))
    return 0 if not rep.errors else 1


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


def cmd_route(args) -> int:
    from .features import routing
    store = _open()
    prompt = " ".join(args.prompt)
    result = routing.route(store, prompt, limit=args.limit)
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
        return 0
    ctx = result["context"]
    print(dim(f"context: {ctx['project'] or '(unscoped)'}"
              + (f":{ctx['layer']}" if ctx['layer'] else "")
              + f"  [{ctx['confidence']}] — {ctx['reason']}"))
    hits = result["hits"]
    if not hits:
        print(dim("no prior context found"))
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
    from .features import save as savemod
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


def cmd_synthesize(args) -> int:
    from .features import synthesis
    store = _open()
    if args.list:
        for p in synthesis.list_proposals():
            print(f"  {p}")
        return 0
    if args.print:
        digest = synthesis.build_digest(store, since_days=args.since_days)
        print(json.dumps(digest, indent=2, ensure_ascii=False) if args.json
              else _render_digest(digest))
        return 0
    path = synthesis.write_proposal(store, since_days=args.since_days)
    digest = synthesis.build_digest(store, since_days=args.since_days)
    print(green(f"✓ proposal written: {path}"))
    print(dim(f"  {digest.get('document_count', 0)} document(s) from the last "
              f"{args.since_days} day(s). Review and apply accepted items yourself — "
              f"nothing is written to core.md/knowledge automatically."))
    return 0


def _render_digest(d: dict) -> str:
    lines = [bold(f"Digest — last {d.get('since_days')} day(s), "
                  f"{d.get('document_count', 0)} document(s)")]
    for g in d.get("groups", []):
        lines.append(f"\n  {cyan(g.get('project') or '(unscoped)')}")
        for doc in g.get("documents", []):
            lines.append(f"    · {doc.get('title', '?')} {dim((doc.get('updated_utc') or '')[:10])}")
            ex = " ".join((doc.get("excerpt") or "").split())[:160]
            if ex:
                lines.append(dim(f"      {ex}"))
    return "\n".join(lines)


def cmd_decay(args) -> int:
    from .features import decay
    store = _open()
    if args.status:
        s = decay.status(store)
        print(bold("decay status"))
        print(f"  active:   {s['active']}")
        print(f"  archived: {s['archived']}")
        if s.get("oldest_active"):
            print(dim("  oldest active:"))
            for d in s["oldest_active"]:
                print(dim(f"    · {(d.get('last_touch') or '')[:10]}  {d.get('title','?')}"))
        return 0
    if args.restore:
        ok = decay.restore(store, args.restore)
        print(green(f"✓ restored {args.restore}") if ok else yellow(f"not found: {args.restore}"))
        return 0 if ok else 1
    dry = not args.apply
    result = decay.run(store, window_days=args.window_days, dry_run=dry)
    verb = "would archive" if dry else "archived"
    print(bold(f"{verb} {result['count']} document(s) untouched for "
               f">{args.window_days} days"))
    for d in result["archived"][:20]:
        print(dim(f"  · {(d.get('last_touch') or '')[:10]}  {d.get('title','?')}"))
    if dry and result["count"]:
        print(dim("\nrun with --apply to archive (non-destructive; re-access restores)."))
    return 0


def cmd_paths(args) -> int:
    config.ensure_dirs()
    print(bold("gigabite paths"))
    print(f"  core:      {config.CORE_DIR}")
    print(f"  knowledge: {config.KNOWLEDGE_DIR}")
    print(f"  index db:  {config.DB_PATH}")
    print(f"  inbox:     {config.INBOX_DIR}")
    print(dim("    · Claude.ai export → ") + str(config.INBOX_CLAUDE_AI))
    print(dim("    · Granola export   → ") + str(config.INBOX_GRANOLA))
    return 0


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="gigabite", description="Local search across all your conversations.")
    p.add_argument("--version", action="version", version=f"gigabite {__version__}")
    sub = p.add_subparsers(dest="command")

    pi = sub.add_parser("ingest", help="scan sources and update the index")
    pi.add_argument("--source", choices=config.ALL_SOURCES)
    pi.add_argument("--force", action="store_true", help="re-read everything, ignore sync state")
    pi.add_argument("--no-remote", action="store_true", help="skip the live claude.ai pull (local only)")
    pi.set_defaults(func=cmd_ingest)

    ps = sub.add_parser("search", help="full-text search the index")
    ps.add_argument("query", nargs="+")
    ps.add_argument("--source", choices=config.ALL_SOURCES)
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

    pd = sub.add_parser("doc", help="print a full conversation by doc_id")
    pd.add_argument("doc_id")
    pd.add_argument("--json", action="store_true")
    pd.set_defaults(func=cmd_doc)

    pr = sub.add_parser("reindex", help="clear and rebuild the index")
    pr.set_defaults(func=cmd_reindex)

    pg = sub.add_parser("granola-connect", help="EXPERIMENTAL: pull Granola notes live via keychain")
    pg.add_argument("--diagnose", action="store_true", help="test decryption only; index nothing")
    pg.set_defaults(func=cmd_granola_connect)

    pl = sub.add_parser("claude-login", help="securely store your claude.ai session token in the keychain")
    pl.set_defaults(func=cmd_claude_login)

    pcs = sub.add_parser("claude-sync", help="pull all claude.ai chats (in/out of projects) via the stored token")
    pcs.add_argument("--force", action="store_true", help="re-fetch every conversation")
    pcs.set_defaults(func=cmd_claude_sync)

    pv = sub.add_parser("save", help="persist a note into ~/.knowledge/{project}/{layer}/ (never the working dir)")
    pv.add_argument("text", nargs="*", help="note text (or - / --stdin to read stdin)")
    pv.add_argument("--project", "-p", required=True)
    pv.add_argument("--layer", "-l")
    pv.add_argument("--title", "-t")
    pv.add_argument("--date")
    pv.add_argument("--stdin", action="store_true")
    pv.set_defaults(func=cmd_save)

    pj = sub.add_parser("project", help="create or list projects (name + keywords drive context detection)")
    pj.add_argument("action", choices=["add", "list"])
    pj.add_argument("name", nargs="?")
    pj.add_argument("--keywords")
    pj.add_argument("--layers")
    pj.set_defaults(func=cmd_project)

    pc = sub.add_parser("calendar", help="file meetings from a parsed screenshot + show agenda with prep")
    pc.add_argument("action", choices=["add", "agenda"])
    pc.add_argument("--json-file", help="path to a JSON list of meetings (add)")
    pc.add_argument("--stdin", action="store_true", help="read meetings JSON from stdin (add)")
    pc.add_argument("--day", help="agenda scope: next (default) | today | YYYY-MM-DD | all")
    pc.set_defaults(func=cmd_calendar)

    psy = sub.add_parser("synthesize", help="build a gated end-of-day proposal from recent activity")
    psy.add_argument("--since-days", type=int, default=1)
    psy.add_argument("--print", action="store_true", help="print the digest without writing a proposal")
    psy.add_argument("--list", action="store_true", help="list existing proposals")
    psy.add_argument("--json", action="store_true")
    psy.set_defaults(func=cmd_synthesize)

    pdc = sub.add_parser("decay", help="archive untouched documents (non-destructive; restore on access)")
    pdc.add_argument("--apply", action="store_true", help="actually archive (default is a dry run)")
    pdc.add_argument("--window-days", type=int, default=30)
    pdc.add_argument("--status", action="store_true")
    pdc.add_argument("--restore", metavar="DOC_ID")
    pdc.set_defaults(func=cmd_decay)

    prt = sub.add_parser("route", help="resolve context + recall relevant prior conversations (powers /gg)")
    prt.add_argument("prompt", nargs="+")
    prt.add_argument("--limit", type=int, default=6)
    prt.add_argument("--json", action="store_true")
    prt.set_defaults(func=cmd_route)

    pp = sub.add_parser("paths", help="show where things live")
    pp.set_defaults(func=cmd_paths)

    return p


def main(argv: Optional[list] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    return args.func(args)
