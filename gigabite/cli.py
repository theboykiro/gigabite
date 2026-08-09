"""gigabite command-line interface.

    gigabite ingest [--source S] [--force]
    gigabite search QUERY [--source S] [--project P] [--limit N] [--context C] [--raw] [--json]
    gigabite status [--json]
    gigabite doc DOC_ID [--json]
    gigabite add PATH [--project P] [--layer L] [--move]
    gigabite materialize [--dry-run] [--source S] [--layer L] [--limit N]
    gigabite reindex
    gigabite paths
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from . import __version__, config, ingest as ingest_mod, util
from .features import relocate
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


def _parse_meeting_header(text: str) -> dict:
    """Pull title/date from a Granola-style header at the top of a transcript.

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

    Fast intake for Granola transcripts: copy in Granola, then run this. It lands
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

    # Granola copies carry a header (Meeting Title:/Date:/Participants:) — read it.
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


def cmd_core(args) -> int:
    """Print the operating protocol (~/.core/core.md) on stdout.

    This exists so the /gg command can load the protocol through the gigabite
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
    print(dim("      archive, proposals, routing aliases"))
    print(f"  ledger:    {config.LEDGER_PATH}")
    print(dim("    · run history: a primary record, so `reindex` cannot reach it"))
    if config.STOP_FILE.exists():
        print(yellow(f"  STOP:      {config.STOP_FILE}  (kill switch engaged)"))
    return 0


def _projects_for_paths() -> list:
    from .features import save as savemod
    return savemod.list_projects()


# ---- the autonomy ledger (docs/AUTONOMY.md) --------------------------------
#
# Deliberately small. The planner and executor talk to `features.ledger` in
# Python; the CLI exists so a human can see what is running, stop it, and read
# what it decided. Anything beyond that would be speculating about a caller that
# does not exist yet.

def _ledger():
    from .features import ledger as ledger_mod
    return ledger_mod, ledger_mod.Ledger.open()


def _run_line(run) -> str:
    mark = {"running": green("●"), "blocked": yellow("◐"), "halted": yellow("■"),
            "failed": _c("31", "✗"), "done": dim("✓")}.get(run.status, dim("·"))
    when = util.short_date(run.started_utc) if run.started_utc else "—"
    return f"  {mark} {cyan(run.run_id)}  {dim(when)}  {dim(run.authority):<12} {run.goal}"


def cmd_run_start(args) -> int:
    mod, led = _ledger()
    try:
        run = led.start_run(
            args.goal,
            done_definition=args.done or "",
            authority=args.authority,
            project=args.project or "",
            baseline_minutes=args.baseline,
        )
    except mod.Halted as exc:
        print(f"refusing to start: {exc}")
        print(dim("  release it with `gigabite run resume`"))
        return 1
    except mod.LedgerError as exc:
        print(f"cannot start run: {exc}")
        return 1
    print(bold(run.run_id))
    print(f"  goal:      {run.goal}")
    if run.done_definition:
        print(f"  done when: {run.done_definition}")
    print(f"  authority: {run.authority}")
    if run.baseline_minutes is not None:
        print(f"  baseline:  {run.baseline_minutes:g} min by hand")
    else:
        print(dim("  no baseline given — this run will not report hours saved"))
    return 0


def cmd_run_list(args) -> int:
    mod, led = _ledger()
    runs = led.list_runs(status=args.status or "", limit=args.limit)
    if mod.halted():
        reason = mod.stop_reason()
        print(yellow(f"kill switch ENGAGED — {reason}" if reason else "kill switch ENGAGED"))
    if not runs:
        print(dim("no runs yet."))
        return 0
    print(bold(f"runs ({len(runs)})"))
    for run in runs:
        print(_run_line(run))
    open_blockers = led.blockers()
    if open_blockers:
        print(yellow(f"\n{len(open_blockers)} open blocker(s)") +
              dim(" — `gigabite run blockers`"))
    return 0


def cmd_run_show(args) -> int:
    mod, led = _ledger()
    try:
        s = led.summary(args.run_id)
    except mod.UnknownRun:
        print(f"no such run: {args.run_id}")
        return 1
    if args.json:
        payload = dict(s)
        payload["run"] = vars(s["run"])
        print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
        return 0

    run = s["run"]
    print(bold(run.run_id) + f"  {dim(run.status)}")
    print(f"  goal:      {run.goal}")
    if run.done_definition:
        print(f"  done when: {run.done_definition}")
    print(f"  authority: {run.authority}    version: {run.version}")
    if run.halt_reason:
        print(yellow(f"  halted:    {run.halt_reason}"))

    if s["steps"]:
        counts = ", ".join(f"{k} {v}" for k, v in s["step_counts"].items() if v)
        print(bold(f"\nsteps ({counts})"))
        for st in s["steps"]:
            mark = {"done": dim("✓"), "failed": _c("31", "✗"),
                    "running": green("●"), "skipped": dim("–")}.get(st["status"], dim("·"))
            tail = f"  {dim('attempt ' + str(st['attempts']))}" if st["attempts"] > 1 else ""
            print(f"  {mark} {st['seq']:>2}. {st['kind']}  {st['summary'] or ''}{tail}")
            if st["error"]:
                # A step that succeeded on retry keeps the error that made it
                # retry. Dim it rather than dropping it: why a step needed two
                # goes is exactly what you want when reading a run back.
                if st["status"] == "failed":
                    print(_c("31", f"        {st['error']}"))
                else:
                    print(dim(f"        earlier attempt: {st['error']}"))

    if s["decisions"]:
        print(bold("\ndecisions"))
        for d in s["decisions"]:
            print(f"  {d['seq']}. {d['question']}")
            print(f"     chose {bold(d['chosen'])} — {d['why']}")
            rejected = json.loads(d["rejected_json"]) if d["rejected_json"] else None
            if rejected:
                print(dim(f"     rejected: {rejected}"))

    if s["blockers"]:
        print(bold("\nblockers"))
        for b in s["blockers"]:
            mark = yellow("open") if b["status"] == "open" else dim(b["status"])
            print(f"  [{b['blocker_id']}] {mark} {b['kind']}: {b['description']}")
            print(dim(f"        unblock by: {b['what_would_unblock']}"))

    if s["artifacts"]:
        print(bold("\nartifacts"))
        for a in s["artifacts"]:
            print(f"  {a['ref']} {dim(a['kind'] or '')}")

    print(bold("\ncost"))
    print(f"  your attention: {s['human_touch_minutes']:g} min")
    if s["wall_minutes"] is not None:
        print(dim(f"  wall clock:     {s['wall_minutes']:g} min (not charged to you)"))
    if s["hours_saved"] is not None:
        saved = s["hours_saved"]
        print(f"  hours saved:    {green(format(saved, 'g'))}")
    else:
        print(dim("  hours saved:    unmeasured (no baseline was set at start)"))
    return 0


def cmd_run_halt(args) -> int:
    mod, led = _ledger()
    try:
        run = led.halt_run(args.run_id, args.reason or "halted by user")
    except mod.UnknownRun:
        print(f"no such run: {args.run_id}")
        return 1
    print(f"{run.run_id} halted — {run.halt_reason}")
    return 0


def cmd_run_stop(args) -> int:
    """The global kill switch."""
    mod, led = _ledger()
    halted_runs = led.halt_all(args.reason or "")
    print(yellow("kill switch ENGAGED") + f"  ({mod.stop_file()})")
    if halted_runs:
        print(f"  halted {len(halted_runs)} run(s): {', '.join(halted_runs)}")
    else:
        print(dim("  nothing was in flight."))
    print(dim("  nothing new will start until `gigabite run resume`."))
    return 0


def cmd_run_resume(args) -> int:
    mod, _led = _ledger()
    if mod.release_stop():
        print("kill switch released.")
        print(dim("  halted runs stay halted — restart them individually, on purpose."))
    else:
        print(dim("kill switch was not set."))
    return 0


def cmd_run_blockers(args) -> int:
    _mod, led = _ledger()
    rows = led.blockers(run_id=args.run or "", status="" if args.all else "open")
    if not rows:
        print(dim("no open blockers."))
        return 0
    print(bold(f"blockers ({len(rows)})"))
    for b in rows:
        mark = yellow("open") if b["status"] == "open" else dim(b["status"])
        print(f"  [{b['blocker_id']}] {mark} {cyan(b['run_id'])} {b['kind']}: {b['description']}")
        print(dim(f"        unblock by: {b['what_would_unblock']}"))
    return 0


def cmd_run_resolve(args) -> int:
    _mod, led = _ledger()
    led.resolve_blocker(args.blocker_id, "abandoned" if args.abandon else "resolved")
    print(f"blocker {args.blocker_id} marked {'abandoned' if args.abandon else 'resolved'}.")
    return 0


def cmd_run_finish(args) -> int:
    mod, led = _ledger()
    try:
        run = led.finish_run(args.run_id, args.status)
    except mod.UnknownRun:
        print(f"no such run: {args.run_id}")
        return 1
    print(f"{run.run_id} → {run.status}")
    return 0


def cmd_run_touch(args) -> int:
    """Record attention spent on a run — the numerator of the oversight cost."""
    mod, led = _ledger()
    try:
        run = led.add_human_time(args.run_id, args.minutes * 60.0)
    except mod.UnknownRun:
        print(f"no such run: {args.run_id}")
        return 1
    print(f"{run.run_id}: {run.human_touch_seconds / 60.0:g} min of your attention so far")
    return 0


def _policy():
    from .features import policy as policy_mod
    return policy_mod


def cmd_policy_show(args) -> int:
    pol = _policy()
    try:
        rules = pol.load()
    except pol.PolicyError as exc:
        print(_c("31", str(exc)))
        return 1
    path = pol.policy_path()
    print(bold("action policy"))
    print(dim(f"  {path}" + ("" if path.exists() else "  (not written yet — defaults in force)")))
    print()
    colour = {"allow": green, "approve": yellow, "user-only": cyan, "never": lambda s: _c("31", s)}
    for name in pol.ACTION_CLASSES:
        rule = rules[name]
        paint = colour.get(rule["verdict"], dim)
        locked = dim("  [enforced in code]") if name in pol.HARD_REFUSED else ""
        print(f"  {name:<20} {paint(rule['verdict']):<12}{locked}")
        print(dim(f"      {rule['why']}"))
    print(dim("\n  run authority is a ceiling on top of this: "
              "passive → read only, advisory → read + local-write."))
    return 0


def cmd_policy_check(args) -> int:
    """Dry-run a decision. The way to answer 'would it let me?' without trying."""
    pol = _policy()
    try:
        decision = pol.authorize(args.action_class, args.action or "", run_id=args.run or "")
    except pol.PolicyError as exc:
        print(_c("31", str(exc)))
        return 1
    paint = {"allow": green, "approve": yellow}.get(decision.verdict, lambda s: _c("31", s))
    print(f"{paint(decision.verdict.upper())}  {decision.action_class}/{decision.action}")
    print(dim(f"  {decision.why}"))
    print(dim(f"  decided by: {decision.source}"))
    return 0 if decision.allowed else 1


def cmd_policy_init(args) -> int:
    pol = _policy()
    path = pol.write_default(overwrite=args.force)
    print(f"policy at {bold(str(path))}")
    print(dim("  edit `verdict` per class to widen or narrow what runs unattended."))
    print(dim("  `infra-security` is enforced in code and cannot be relaxed there."))
    return 0


def cmd_policy_grant(args) -> int:
    pol, (mod, led) = _policy(), _ledger()
    if args.action_class in pol.HARD_REFUSED:
        print(_c("31", f"{args.action_class} is refused in code — a grant would do nothing."))
        return 1
    if args.action_class not in pol.ACTION_CLASSES:
        print(_c("31", f"unknown action class {args.action_class!r}"))
        print(dim("  known: " + ", ".join(pol.ACTION_CLASSES)))
        return 1
    try:
        led.grant(args.run_id, args.action_class, args.note or "")
    except mod.UnknownRun:
        print(f"no such run: {args.run_id}")
        return 1
    print(f"{args.run_id}: {green(args.action_class)} approved for this run")
    print(dim("  revoke with `gigabite policy revoke`"))
    return 0


def cmd_policy_revoke(args) -> int:
    _mod, led = _ledger()
    n = led.revoke_grant(args.run_id, args.action_class)
    print(f"revoked {n} grant(s) for {args.action_class} on {args.run_id}")
    return 0


def cmd_policy_grants(args) -> int:
    mod, led = _ledger()
    try:
        rows = led.grants(args.run_id, live_only=not args.all)
    except Exception:
        rows = []
    if not rows:
        print(dim("no grants on this run."))
        return 0
    print(bold(f"grants on {args.run_id}"))
    for g in rows:
        state = dim("revoked") if g["revoked_utc"] else green("live")
        print(f"  {state}  {g['action_class']}  {dim(g['note'] or '')}")
    return 0


def cmd_audit(args) -> int:
    _mod, led = _ledger()
    rows = led.audit_trail(run_id=args.run or "", limit=args.limit)
    if not rows:
        print(dim("nothing audited yet."))
        return 0
    for r in rows:
        when = (r["ts_utc"] or "")[:19].replace("T", " ")
        print(f"  {dim(when)}  {cyan(r['run_id'] or '—')}  "
              f"{r['action_class']}/{r['action']}  {bold(r['disposition'])}")
    return 0


def cmd_relocate(args) -> int:
    """Move a pre-existing ~/Knowledge to the visible ~/Knowledge layout."""
    p = relocate.plan()
    print(bold("relocate knowledge base"))
    for line in relocate.describe(p):
        print(line)

    if not p.actionable:
        print(dim("\nnothing to do — the layout is already current."))
        return 0
    if p.warnings:
        print("\nrefusing to continue while the warnings above stand.")
        return 1
    if args.dry_run:
        print(dim("\ndry run — nothing was moved. Re-run without --dry-run to apply."))
        return 0

    for line in relocate.apply(p):
        print(f"  {line}")
    config.ensure_dirs()

    # The index stores absolute paths in `ref` and keys its incremental sync on
    # them, so it goes stale the moment the files move. Everything it holds is
    # derived from those files, so the honest response is to rebuild rather than
    # rewrite paths in place.
    print(dim("\nindex paths are now stale; rebuilding…"))
    rc = cmd_reindex(argparse.Namespace())
    print(f"\nknowledge base is now at {bold(str(config.KNOWLEDGE_DIR))}")
    return rc


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
    pi.add_argument("--remote", action="store_true", help="also run the live claude.ai pull (Cloudflare-gated; usually use the browser export)")
    pi.add_argument("--no-remote", action="store_true", help=argparse.SUPPRESS)  # back-compat (default is already local-only)
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
    pmz.add_argument("--source", choices=config.ALL_SOURCES, help="only this source")
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

    prl = sub.add_parser(
        "relocate",
        help="bring an older ~/Knowledge layout up to date (one folder, machinery hidden)",
    )
    prl.add_argument("--dry-run", action="store_true",
                     help="show what would move, change nothing")
    prl.set_defaults(func=cmd_relocate)

    pl = sub.add_parser("claude-login", help="securely store your claude.ai session token in the keychain")
    pl.set_defaults(func=cmd_claude_login)

    pcs = sub.add_parser("claude-sync", help="pull all claude.ai chats (in/out of projects) via the stored token")
    pcs.add_argument("--force", action="store_true", help="re-fetch every conversation")
    pcs.set_defaults(func=cmd_claude_sync)

    pv = sub.add_parser("save", help="persist a note into ~/Knowledge/{project}/{layer}/ (never the working dir)")
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

    ppa = sub.add_parser("paste", help="save clipboard contents (e.g. a copied Granola transcript) into ~/Knowledge")
    ppa.add_argument("--title", "-t", help="title (default: the transcript header, else the first line)")
    ppa.add_argument("--date", "-d", help="YYYY-MM-DD (default: the transcript header, else today)")
    ppa.add_argument("--project", "-p", help="force a project (default: auto-detected)")
    ppa.add_argument("--layer", "-l", help="force a layer, e.g. meetings (needs --project)")
    ppa.add_argument("--source", choices=[config.SOURCE_GRANOLA, config.SOURCE_NOTE],
                     default=config.SOURCE_GRANOLA,
                     help="what the text is, recorded as provenance on the note")
    ppa.add_argument("--stdin", action="store_true", help="read from stdin instead of the clipboard")
    ppa.set_defaults(func=cmd_paste)

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

    # -- the autonomy ledger -------------------------------------------------
    prn = sub.add_parser("run", help="the autonomy ledger: missions, steps, blockers, kill switch")
    rsub = prn.add_subparsers(dest="run_command")

    rs = rsub.add_parser("start", help="open a run (a mission that outlives this process)")
    rs.add_argument("goal", help="what this run is for")
    rs.add_argument("--done", help="definition of done — how we know it worked")
    rs.add_argument("--authority", choices=["passive", "advisory", "supervised", "full"],
                    default="supervised", help="default: supervised (autonomy is earned, see docs/AUTONOMY.md)")
    rs.add_argument("--project", "-p")
    rs.add_argument("--baseline", type=float, metavar="MIN",
                    help="minutes this would take by hand — without it the run cannot report hours saved")
    rs.set_defaults(func=cmd_run_start)

    rl = rsub.add_parser("list", help="what has run and what is running")
    rl.add_argument("--status", choices=list(("planned", "running", "blocked", "halted", "done", "failed")))
    rl.add_argument("--limit", type=int, default=20)
    rl.set_defaults(func=cmd_run_list)

    rsh = rsub.add_parser("show", help="one run in full: steps, decisions, blockers, cost")
    rsh.add_argument("run_id")
    rsh.add_argument("--json", action="store_true")
    rsh.set_defaults(func=cmd_run_show)

    rh = rsub.add_parser("halt", help="stop one run")
    rh.add_argument("run_id")
    rh.add_argument("--reason")
    rh.set_defaults(func=cmd_run_halt)

    rst = rsub.add_parser("stop", help="GLOBAL KILL SWITCH: halt everything, block anything new")
    rst.add_argument("--reason")
    rst.set_defaults(func=cmd_run_stop)

    rrs = rsub.add_parser("resume", help="release the kill switch (halted runs stay halted)")
    rrs.set_defaults(func=cmd_run_resume)

    rb = rsub.add_parser("blockers", help="what is parked, and what would unblock it")
    rb.add_argument("--run")
    rb.add_argument("--all", action="store_true", help="include resolved and abandoned")
    rb.set_defaults(func=cmd_run_blockers)

    rrv = rsub.add_parser("resolve", help="close a blocker")
    rrv.add_argument("blocker_id", type=int)
    rrv.add_argument("--abandon", action="store_true", help="close it as never-getting-done")
    rrv.set_defaults(func=cmd_run_resolve)

    rf = rsub.add_parser("finish", help="close a run")
    rf.add_argument("run_id")
    rf.add_argument("--status", choices=["done", "failed", "blocked"], default="done")
    rf.set_defaults(func=cmd_run_finish)

    rt = rsub.add_parser("touch", help="record minutes of YOUR attention this run cost")
    rt.add_argument("run_id")
    rt.add_argument("--minutes", type=float, required=True)
    rt.set_defaults(func=cmd_run_touch)

    ppl = sub.add_parser("policy", help="what the system may do unattended, and what needs you")
    plsub = ppl.add_subparsers(dest="policy_command")

    pls = plsub.add_parser("show", help="the effective policy table")
    pls.set_defaults(func=cmd_policy_show)

    plc = plsub.add_parser("check", help="dry-run one decision without attempting it")
    plc.add_argument("action_class")
    plc.add_argument("action", nargs="?")
    plc.add_argument("--run", help="decide in the context of a run (authority + grants apply)")
    plc.set_defaults(func=cmd_policy_check)

    pli = plsub.add_parser("init", help="write the default policy to ~/.core/policy.json")
    pli.add_argument("--force", action="store_true", help="overwrite an existing file")
    pli.set_defaults(func=cmd_policy_init)

    plg = plsub.add_parser("grant", help="approve a whole action class for one run")
    plg.add_argument("run_id")
    plg.add_argument("action_class")
    plg.add_argument("--note")
    plg.set_defaults(func=cmd_policy_grant)

    plr = plsub.add_parser("revoke", help="withdraw a grant")
    plr.add_argument("run_id")
    plr.add_argument("action_class")
    plr.set_defaults(func=cmd_policy_revoke)

    pll = plsub.add_parser("grants", help="what has been approved on a run")
    pll.add_argument("run_id")
    pll.add_argument("--all", action="store_true", help="include revoked")
    pll.set_defaults(func=cmd_policy_grants)

    pau = sub.add_parser("audit", help="every gated action and how it was dispositioned")
    pau.add_argument("--run")
    pau.add_argument("--limit", type=int, default=50)
    pau.set_defaults(func=cmd_audit)

    pco = sub.add_parser("core", help="print the operating protocol (~/.core/core.md)")
    pco.set_defaults(func=cmd_core)

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
