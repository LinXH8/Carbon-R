"""The methodology retrieval eval: hit rate, and both kinds of refusal, per phrasing style.

    uv run python scripts/eval_retrieval.py --split dev            (or: make eval-retrieval)
    uv run python scripts/eval_retrieval.py --split dev --no-judge     retrieval only
    uv run python scripts/eval_retrieval.py --split test               once, after freezing

What is scored, against the hand-drafted key in data/evals/methodology_questions.json:

    hit@1, hit@5, MRR    answerable questions: is an expected page among the top passages?
                         A hit is a PAGE match - the key labels pages, not chunks.
    retrieval refusals   the nearest passage is below the threshold: right for an
                         unanswerable question, wrong for an answerable one
    generation refusals  passages passed the threshold but the judge's answer did not
                         survive quote verification: right / wrong in the same sense
    cited page           of the answerable questions that were answered, how many cite an
                         expected page with a verified quote

The threshold
-------------
`--split dev` prints the nearest-passage similarity of every question, grouped by whether
it is answerable, and a sweep of candidate thresholds with how many answerable and
unanswerable questions each would refuse. Choosing the value is a judgement made from that
table and recorded in docs/blocks/TIER2_BLOCK4.md; it is then frozen as RETRIEVAL_THRESHOLD in
tools/methodology.py. `--split test` refuses to run while that constant is still the
uncalibrated 0.0, so the test split cannot be used to choose it.

What the run JSON holds, and what it does not
----------------------------------------------
It is committed (data/evals/runs/), so it holds NO GHG Protocol text - not the passages,
not the judge's quotes, not its reasons, which may paraphrase. Only ids, pages, similarity
scores, counts and booleans. See `carbon_r.sources` for why.

Local only: needs the ingested corpus, a database and an API key. CI does not run it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import subprocess
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from carbon_r.config import PROJECT_ROOT, get_settings
from carbon_r.rag.chunking import CHUNKER_VERSION
from carbon_r.rag.embed import configured_model, openai_embedder
from carbon_r.rag.questions import Question, load_questions
from carbon_r.tools import methodology
from carbon_r.tools.methodology import (
    TOP_K,
    AnswerabilityJudgement,
    Passage,
    openai_judge,
    retrieve,
    verified_citations,
)

RUNS_DIR = PROJECT_ROOT / "data" / "evals" / "runs"
CACHE_DIR = PROJECT_ROOT / "data" / "generated" / "eval_cache" / "retrieval"
STYLES = ("textbook", "user", "singlish")


def git_state() -> dict:
    """The commit measured, and whether anything other than this eval's own output is dirty.

    `data/evals/runs/` is excluded: the eval writes its run file there, so counting it would
    mark the second of two back-to-back runs dirty even though no code had changed.
    """

    def run(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=PROJECT_ROOT, capture_output=True, text=True, check=False
        ).stdout.strip()

    status = run("status", "--porcelain", "--", ".", ":(exclude)data/evals/runs")
    return {"commit": run("rev-parse", "HEAD"), "dirty": bool(status)}


def _digest(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:24]


class Cache:
    """Question embeddings and judgements, keyed by everything that determines them."""

    def __init__(self, root: Path, enabled: bool) -> None:
        self.root, self.enabled = root, enabled

    def get(self, kind: str, key: str):
        path = self.root / kind / f"{key}.json"
        if self.enabled and path.exists():
            return json.loads(path.read_text())
        return None

    def put(self, kind: str, key: str, value) -> None:
        path = self.root / kind / f"{key}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))


def score_question(
    q: Question,
    *,
    embed,
    judge,
    cache: Cache,
    model: str,
    judge_model: str,
    threshold: float,
    use_judge: bool,
) -> dict:
    key = _digest(model, q.question)
    vector = cache.get("embedding", key)
    if vector is None:
        (vector,) = embed([q.question])
        cache.put("embedding", key, vector)
    passages: list[Passage] = retrieve(q.question, embed=lambda _: [vector])

    expected = {(e.source_id, e.pdf_page) for e in q.expected}
    ranks = [i for i, p in enumerate(passages, 1) if (p.source_id, p.pdf_page) in expected]
    best = passages[0].similarity if passages else None
    row = {
        "id": q.id,
        "phrasing": q.phrasing,
        "split": q.split,
        "answerable": q.answerable,
        "unanswerable_kind": q.unanswerable_kind,
        "document": q.document,
        "ranked": [[p.source_id, p.pdf_page, round(p.similarity, 4)] for p in passages],
        "best_similarity": None if best is None else round(best, 4),
        "first_hit_rank": ranks[0] if ranks else None,
        "retrieval_refused": best is None or best < threshold,
        "judged": False,
    }
    if row["retrieval_refused"] or not use_judge:
        return row

    jkey = _digest(judge_model, q.question, *(str(p.chunk_id) for p in passages))
    cached = cache.get("judgement", jkey)
    if cached is None:
        judgement = judge(q.question, passages)
        cache.put("judgement", jkey, judgement.model_dump())
    else:
        judgement = AnswerabilityJudgement.model_validate(cached)
    kept, dropped = verified_citations(judgement, passages)
    by_id = {p.chunk_id: p for p in passages}
    cited = sorted({(by_id[c.chunk_id].source_id, by_id[c.chunk_id].pdf_page) for c in kept})
    row |= {
        "judged": True,
        "judge_answered": judgement.answered,
        "quotes_kept": len(kept),
        "quotes_dropped": dropped,
        "cited_pages": [list(c) for c in cited],
        "generation_refused": not (judgement.answered and kept),
        "cited_expected_page": bool(set(cited) & expected),
    }
    return row


def _n_of(n: int, total: int) -> str:
    return f"{n} of {total}" if total else "-"


def summarise(rows: list[dict]) -> dict:
    answerable = [r for r in rows if r["answerable"]]
    unanswerable = [r for r in rows if not r["answerable"]]
    hit1 = sum(r["first_hit_rank"] == 1 for r in answerable)
    hit5 = sum(r["first_hit_rank"] is not None for r in answerable)
    mrr = (
        sum(1 / r["first_hit_rank"] for r in answerable if r["first_hit_rank"]) / len(answerable)
        if answerable
        else None
    )
    judged = [r for r in rows if r["judged"]]
    out = {
        "answerable": len(answerable),
        "unanswerable": len(unanswerable),
        "hit_at_1": hit1,
        "hit_at_5": hit5,
        "mrr": None if mrr is None else round(mrr, 3),
        "retrieval_refused_unanswerable": sum(r["retrieval_refused"] for r in unanswerable),
        "retrieval_refused_answerable": sum(r["retrieval_refused"] for r in answerable),
        "judged": len(judged),
    }
    if judged:
        out |= {
            "generation_refused_unanswerable": sum(
                r["generation_refused"] for r in judged if not r["answerable"]
            ),
            "generation_refused_answerable": sum(
                r["generation_refused"] for r in judged if r["answerable"]
            ),
            "answered_answerable": sum(
                not r["generation_refused"] for r in judged if r["answerable"]
            ),
            "answered_citing_expected_page": sum(
                (not r["generation_refused"]) and r["cited_expected_page"]
                for r in judged
                if r["answerable"]
            ),
            "quotes_dropped": sum(r["quotes_dropped"] for r in judged),
        }
    return out


def print_summary(label: str, s: dict) -> None:
    a, u = s["answerable"], s["unanswerable"]
    line = (
        f"  {label:22} hit@1 {_n_of(s['hit_at_1'], a):9} hit@5 {_n_of(s['hit_at_5'], a):9} "
        f"MRR {s['mrr'] if s['mrr'] is not None else '-':5} | retrieval refused: "
        f"unans {_n_of(s['retrieval_refused_unanswerable'], u):7} "
        f"ans {_n_of(s['retrieval_refused_answerable'], a):8}"
    )
    if s["judged"]:
        line += (
            f" | generation refused: unans {s['generation_refused_unanswerable']}"
            f"  ans {s['generation_refused_answerable']}"
            f" | answered {s['answered_answerable']} of {a}, citing expected page "
            f"{s['answered_citing_expected_page']}"
        )
    print(line)


def threshold_table(rows: list[dict]) -> list[dict]:
    """For each candidate threshold, how many of each kind would be refused at retrieval."""
    best = sorted({r["best_similarity"] for r in rows if r["best_similarity"] is not None})
    table = []
    for t in [0.0, *best]:
        table.append(
            {
                "threshold": t,
                "refuses_unanswerable": sum(
                    r["best_similarity"] < t for r in rows if not r["answerable"]
                ),
                "refuses_answerable": sum(
                    r["best_similarity"] < t for r in rows if r["answerable"]
                ),
            }
        )
    return table


def distribution(rows: list[dict]) -> dict:
    groups = defaultdict(list)
    for r in rows:
        name = "answerable" if r["answerable"] else r["unanswerable_kind"]
        groups[name].append(r["best_similarity"])
    return {
        name: {
            "n": len(v),
            "min": min(v),
            "median": round(statistics.median(v), 4),
            "max": max(v),
        }
        for name, v in groups.items()
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--split", choices=("dev", "test"), required=True)
    parser.add_argument("--no-judge", action="store_true", help="retrieval metrics only")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument(
        "--allow-draft",
        action="store_true",
        help="score a question set not yet reviewed (results are not a measurement)",
    )
    args = parser.parse_args(argv)

    qs = load_questions()
    if qs.review_status != "reviewed" and not args.allow_draft:
        print("The question set is still a draft. It is reviewed before the first run.")
        return 1
    threshold = methodology.RETRIEVAL_THRESHOLD
    if args.split == "test" and threshold == 0.0:
        print("RETRIEVAL_THRESHOLD is still uncalibrated. Freeze it from the dev split first.")
        return 1

    model = configured_model()
    judge_model = get_settings().openai_model
    usage: list[dict] = []
    embed, judge = openai_embedder(model, usage), openai_judge(usage)
    cache = Cache(CACHE_DIR, enabled=not args.no_cache)

    questions = qs.split(args.split)
    rows = [
        score_question(
            q,
            embed=embed,
            judge=judge,
            cache=cache,
            model=model,
            judge_model=judge_model,
            threshold=threshold,
            use_judge=not args.no_judge,
        )
        for q in questions
    ]

    git = git_state()
    dirty = " (DIRTY TREE - not a clean measurement)" if git["dirty"] else ""
    print(
        f"retrieval eval, split {args.split}: {len(rows)} questions; commit "
        f"{git['commit'][:7]}{dirty}\n  embedding {model}; judge {judge_model}; chunker "
        f"{CHUNKER_VERSION}; k={TOP_K}; threshold {threshold}; question set "
        f"v{qs.version} ({qs.review_status})\n"
    )
    summary = {"overall": summarise(rows)}
    print_summary("overall", summary["overall"])
    for style in STYLES:
        summary[style] = summarise([r for r in rows if r["phrasing"] == style])
        print_summary(style, summary[style])
    for doc in sorted({r["document"] for r in rows if r["answerable"]}):
        summary[doc] = summarise([r for r in rows if r["document"] == doc])
        print_summary(doc, summary[doc])

    dist = distribution(rows)
    sweep = threshold_table(rows)
    print("\n  nearest-passage similarity:")
    for name, d in dist.items():
        print(f"    {name:12} n={d['n']:2} min {d['min']} median {d['median']} max {d['max']}")
    if args.split == "dev":
        print("\n  threshold sweep (refused at retrieval if nearest similarity < threshold):")
        for t in sweep:
            print(
                f"    {t['threshold']:.4f}  unanswerable {t['refuses_unanswerable']:2} of "
                f"{summary['overall']['unanswerable']}   answerable {t['refuses_answerable']:2} "
                f"of {summary['overall']['answerable']}"
            )

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = RUNS_DIR / f"retrieval_{args.split}_{stamp}.json"
    record = {
        "split": args.split,
        "git": git,
        "embedding_model": model,
        "judge_model": judge_model if not args.no_judge else None,
        "chunker_version": CHUNKER_VERSION,
        "top_k": TOP_K,
        "threshold": threshold,
        "question_set": {"version": qs.version, "review_status": qs.review_status},
        "summary": summary,
        "similarity_distribution": dist,
        "threshold_sweep": sweep,
        "usage_tokens": sum(u.get("total_tokens", 0) for u in usage),
        "rows": rows,
    }
    out.write_text(json.dumps(record, indent=2) + "\n")
    print(f"\n  run written to {out.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
