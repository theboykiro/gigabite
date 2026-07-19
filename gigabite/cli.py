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
    reports = ingest_mod.run(store, sources=sources, force=args.force)
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
    )
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
    pi.set_defaults(func=cmd_ingest)

    ps = sub.add_parser("search", help="full-text search the index")
    ps.add_argument("query", nargs="+")
    ps.add_argument("--source", choices=config.ALL_SOURCES)
    ps.add_argument("--project")
    ps.add_argument("--limit", type=int, default=20)
    ps.add_argument("--context", type=int, default=0, help="show up to N+1 snippets per conversation")
    ps.add_argument("--raw", action="store_true", help="pass query verbatim as an FTS5 expression")
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
