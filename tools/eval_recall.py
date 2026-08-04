#!/usr/bin/env python3
"""Measure recall quality against the real index — known-item retrieval.

Why this exists
---------------
Ranking was previously tuned by trying a number and eyeballing a few searches.
That is how ``TRANSCRIPT_RANK_PENALTY`` ended up with a calibration comment that
no longer matched reality: the corpus grew, and nobody re-measured. This harness
makes the question answerable in one command.

The method is *known-item retrieval*. For each document already in the index we
build queries we know the answer to, ask the index, and check whether the right
document came back. Two query styles, because they fail differently:

  phrase  a contiguous span lifted from the document. Easy: tests that the
          document is reachable at all.
  bag     a handful of that document's distinctive words, out of order and out
          of context. Realistic: this is what a half-remembered search looks
          like, and it is where length-normalisation bias shows up.

Ground truth is derived from the live corpus at runtime and never written to
disk, so no client or meeting content can leak into the repository.

Usage
-----
    python3 tools/eval_recall.py                 # evaluate current ranking
    python3 tools/eval_recall.py --json out.json # machine-readable, for diffing
    python3 tools/eval_recall.py --baseline b.json  # compare against a previous run

Every query runs with ``record=False`` so evaluating never disturbs the access
timestamps that drive decay.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gigabite import config, store as S  # noqa: E402

# A fixed seed keeps the generated query set identical between runs, so a score
# change means the ranking changed and not that the dice rolled differently.
SEED = 20260805

# Words too common to identify anything. Deliberately broader than the FTS stop
# list: here we are picking *distinctive* terms, not filtering a user's query.
_COMMON = set("""
a about above after again against all am an and any are as at be because been
before being below between both but by can cannot could did do does doing don
down during each few for from further had has have having he her here hers him
his how i if in into is it its itself just me more most my no nor not now of
off on once only or other ought our ours out over own same she should so some
such than that the their theirs them then there these they this those through
to too under until up very was we were what when where which while who whom why
will with would you your yours yeah okay ok like really think know going get got
one two three also thing things lot bit way going want need make made take
""".split())

_WORD = re.compile(r"[A-Za-z][A-Za-z'-]{2,}")


def _content_words(text: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(text) if w.lower() not in _COMMON]


def _doc_texts(conn) -> dict[str, list[str]]:
    out: dict[str, list[str]] = defaultdict(list)
    for r in conn.execute("SELECT doc_id, text FROM fts ORDER BY doc_id, seq"):
        out[r["doc_id"]].append(r["text"])
    return out


def build_queries(conn, per_doc: int = 2) -> list[dict]:
    """Generate known-item queries whose correct answer is a specific document.

    A term only qualifies if it is *distinctive*: it must appear in at most three
    documents corpus-wide. Without that filter the 'bag' queries are ambiguous by
    construction and every ranking looks equally bad.
    """
    rng = random.Random(SEED)
    texts = _doc_texts(conn)
    meta = {
        r["doc_id"]: dict(r)
        for r in conn.execute("SELECT doc_id, source, title, project FROM documents")
    }

    df: Counter = Counter()
    per_doc_words: dict[str, Counter] = {}
    for doc_id, chunks in texts.items():
        words = Counter(_content_words("\n".join(chunks)))
        per_doc_words[doc_id] = words
        df.update(words.keys())

    queries: list[dict] = []
    for doc_id, words in per_doc_words.items():
        if doc_id not in meta:
            continue
        distinctive = [w for w, c in words.items() if df[w] <= 3 and c >= 2 and len(w) > 3]
        if len(distinctive) < 4:
            continue
        distinctive.sort()

        # bag-of-terms: the realistic half-remembered search
        for i in range(per_doc):
            pick = rng.sample(distinctive, min(4, len(distinctive)))
            queries.append({
                "kind": "bag", "query": " ".join(pick),
                "expect": doc_id, "source": meta[doc_id]["source"],
                "title": meta[doc_id]["title"] or "", "n": i,
            })

        # phrase: a contiguous span that really is in the document
        joined = "\n".join(texts[doc_id])
        spans = [ln.strip() for ln in joined.split("\n") if len(ln.split()) >= 8]
        if spans:
            span = rng.choice(spans)
            toks = span.split()
            start = rng.randrange(0, max(1, len(toks) - 7))
            queries.append({
                "kind": "phrase", "query": " ".join(toks[start:start + 7]),
                "expect": doc_id, "source": meta[doc_id]["source"],
                "title": meta[doc_id]["title"] or "", "n": 0,
            })
    return queries


def evaluate(st: S.Store, queries: list[dict], limit: int = 5) -> dict:
    """Run every query and score top-1, MRR and recall@limit, sliced by source."""
    by_source: dict[str, Counter] = defaultdict(Counter)
    by_kind: dict[str, Counter] = defaultdict(Counter)
    overall = Counter()
    mrr_total = 0.0
    transcript_top = 0
    hijacked = 0        # transcript won when the answer was source material
    non_transcript_q = 0
    empty = 0
    failures: list[dict] = []

    for q in queries:
        rows = st.search(q["query"], limit=limit, record=False)
        if not rows:
            empty += 1
        # rank of the first hit belonging to the expected document
        rank = next((i for i, r in enumerate(rows) if r["doc_id"] == q["expect"]), None)
        hit1 = rank == 0
        hitk = rank is not None
        rr = 1.0 / (rank + 1) if rank is not None else 0.0
        mrr_total += rr

        overall["n"] += 1
        overall["top1"] += int(hit1)
        overall[f"recall@{limit}"] += int(hitk)
        by_source[q["source"]]["n"] += 1
        by_source[q["source"]]["top1"] += int(hit1)
        by_source[q["source"]][f"recall@{limit}"] += int(hitk)
        by_kind[q["kind"]]["n"] += 1
        by_kind[q["kind"]]["top1"] += int(hit1)
        by_kind[q["kind"]][f"recall@{limit}"] += int(hitk)

        if rows and rows[0]["source"] == config.SOURCE_CLAUDE_CODE:
            transcript_top += 1
        # The metric that actually reflects the complaint: a transcript beating
        # the source material when source material was the right answer. The raw
        # 'took top slot' figure is confounded, because it also counts the
        # queries where a transcript genuinely is what you were looking for.
        if q["source"] != config.SOURCE_CLAUDE_CODE:
            non_transcript_q += 1
            if rows and rows[0]["source"] == config.SOURCE_CLAUDE_CODE:
                hijacked += 1
        if not hit1:
            failures.append({
                "query": q["query"], "kind": q["kind"], "expected_source": q["source"],
                "expected_title": q["title"],
                "got_source": rows[0]["source"] if rows else None,
                "got_title": (rows[0]["title"] or "") if rows else None,
                "rank_of_expected": rank,
            })

    n = max(overall["n"], 1)
    return {
        "n": overall["n"],
        "top1": overall["top1"] / n,
        f"recall@{limit}": overall[f"recall@{limit}"] / n,
        "mrr": mrr_total / n,
        "empty_results": empty / n,
        "transcript_took_top_slot": transcript_top / n,
        "transcript_hijacked": hijacked / max(non_transcript_q, 1),
        "by_source": {
            s: {"n": c["n"], "top1": c["top1"] / max(c["n"], 1),
                f"recall@{limit}": c[f"recall@{limit}"] / max(c["n"], 1)}
            for s, c in sorted(by_source.items())
        },
        "by_kind": {
            k: {"n": c["n"], "top1": c["top1"] / max(c["n"], 1),
                f"recall@{limit}": c[f"recall@{limit}"] / max(c["n"], 1)}
            for k, c in sorted(by_kind.items())
        },
        "failures": failures,
    }


def _pct(x: float) -> str:
    return f"{100 * x:5.1f}%"


def report(res: dict, baseline: dict | None = None, show_failures: int = 0) -> None:
    limit_key = next(k for k in res if k.startswith("recall@"))

    def delta(key, sub=None):
        if not baseline:
            return ""
        try:
            old = baseline[sub][key] if sub else baseline[key]
        except (KeyError, TypeError):
            return ""
        d = (res[sub][key] if sub else res[key]) - old
        if abs(d) < 0.0005:
            return "     ="
        return f"  {d*100:+5.1f}"

    print(f"queries: {res['n']}")
    print(f"  top-1 accuracy        {_pct(res['top1'])}{delta('top1')}")
    print(f"  {limit_key:21s} {_pct(res[limit_key])}{delta(limit_key)}")
    print(f"  MRR                   {res['mrr']:6.3f}{delta('mrr')}")
    print(f"  empty result sets     {_pct(res['empty_results'])}{delta('empty_results')}")
    print(f"  transcript took top   {_pct(res['transcript_took_top_slot'])}"
          f"{delta('transcript_took_top_slot')}")
    print(f"  transcript hijacked   {_pct(res.get('transcript_hijacked', 0))}"
          f"{delta('transcript_hijacked')}   (lower is better)")

    print("\n  by source (top-1 / n):")
    for s, c in res["by_source"].items():
        b = ""
        if baseline and s in baseline.get("by_source", {}):
            d = c["top1"] - baseline["by_source"][s]["top1"]
            b = "     =" if abs(d) < 0.0005 else f"  {d*100:+5.1f}"
        print(f"    {s:12s} {_pct(c['top1'])}  (n={c['n']}){b}")

    print("\n  by query kind (top-1 / n):")
    for k, c in res["by_kind"].items():
        b = ""
        if baseline and k in baseline.get("by_kind", {}):
            d = c["top1"] - baseline["by_kind"][k]["top1"]
            b = "     =" if abs(d) < 0.0005 else f"  {d*100:+5.1f}"
        print(f"    {k:12s} {_pct(c['top1'])}  (n={c['n']}){b}")

    if show_failures:
        print(f"\n  first {show_failures} misses:")
        for f in res["failures"][:show_failures]:
            got = f"{f['got_source']}/{(f['got_title'] or '')[:34]}" if f["got_source"] else "nothing"
            print(f"    [{f['kind']:6s}] expected {f['expected_source']:11s} "
                  f"rank={f['rank_of_expected']}  got {got}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=None, help="index to evaluate (default: configured DB)")
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--per-doc", type=int, default=2, help="bag queries generated per document")
    ap.add_argument("--json", dest="json_out", default=None, help="write results to this file")
    ap.add_argument("--baseline", default=None, help="compare against a previous --json run")
    ap.add_argument("--failures", type=int, default=0, help="print this many misses")
    args = ap.parse_args()

    conn = S.connect(Path(args.db) if args.db else config.DB_PATH)
    st = S.Store(conn)

    queries = build_queries(conn, per_doc=args.per_doc)
    if not queries:
        print("no queries could be generated — is the index empty?", file=sys.stderr)
        return 1

    res = evaluate(st, queries, limit=args.limit)

    baseline = None
    if args.baseline and Path(args.baseline).exists():
        baseline = json.loads(Path(args.baseline).read_text())
        print(f"(comparing against {args.baseline})\n")

    report(res, baseline, show_failures=args.failures)

    if args.json_out:
        # Failures embed real corpus text; keep them out of anything persisted.
        persisted = {k: v for k, v in res.items() if k != "failures"}
        Path(args.json_out).write_text(json.dumps(persisted, indent=2))
        print(f"\nwrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
