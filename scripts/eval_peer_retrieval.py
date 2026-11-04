"""Eval 5b, retrieval: the peer search against the hand-checked key, per phrasing style.

    uv run python scripts/eval_peer_retrieval.py --split dev
    uv run python scripts/eval_peer_retrieval.py --split dev --no-judge     retrieval only
    uv run python scripts/eval_peer_retrieval.py --split test               once, after dev

What is scored, against data/evals/peer_questions.json (frozen before the first run; its
sha256 is recorded in every run):

    hit@1, hit@5, MRR   answerable questions: is an expected page among the fused passages
                        (TOP_K of them)? A hit is a PAGE match. Each question is retrieved
                        with the company filter it names, as the agent would pass it.
    refusals            the tool's own decision, by refusal level: `_resolve` - the pure
                        function behind `search_peer_disclosures` - is applied to the same
                        passages and judgement, so the eval scores the tool, not a copy of
                        its logic. Right for an unanswerable question, wrong for an
                        answerable one.
    judge false         an unanswerable question the tool ANSWERED (verified quotes), and
    positives           an answerable one answered with no quote from an expected page -
                        the Keppel shape: real words, from pages that do not answer.
    cited page          of the answerable questions answered, how many quote an expected page

There is no similarity floor to calibrate (see `tools/peer.py`): the refusals are the judge's.

The run JSON is committed (data/evals/runs/), so it holds NO report text - not passages,
not quotes, not the judge's reasons. Only ids, pages, ranks, counts and booleans.

Local only: needs the ingested peer corpus, a database and an API key. CI does not run it.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from carbon_r.config import PROJECT_ROOT, get_settings
from carbon_r.rag.chunking import CHUNKER_VERSION
from carbon_r.rag.embed import configured_model, openai_embedder
from carbon_r.rag.questions import PEER_QUESTIONS_PATH, Question, load_questions
from carbon_r.rag.scoring import (
    Cache,
    digest,
    file_sha256,
    first_hit_rank,
    git_state,
    hit_metrics,
    n_of,
)
from carbon_r.sources import PEERS
from carbon_r.tools.methodology import AnswerabilityJudgement, openai_judge
from carbon_r.tools.peer import (
    PEER_CORPUS,
    TOP_K,
    PeerFilter,
    PeerPassage,
    _resolve,
    retrieve,
)

RUNS_DIR = PROJECT_ROOT / "data" / "evals" / "runs"
CACHE_DIR = PROJECT_ROOT / "data" / "generated" / "eval_cache" / "peer_retrieval"
STYLES = ("textbook", "user", "singlish")


def score_question(
    q: Question,
    *,
    embed: Callable[[Sequence[str]], list[list[float]]],
    judge,
    cache: Cache,
    model: str,
    judge_model: str,
    use_judge: bool,
    fetch=None,
) -> dict:
    """One question: ranks, and - when judged - the tool's decision on those passages."""
    key = digest(model, q.question)
    vector = cache.get("embedding", key)
    if vector is None:
        (vector,) = embed([q.question])
        cache.put("embedding", key, vector)
    f = PeerFilter(company=q.company)
    passages: list[PeerPassage] = retrieve(q.question, f, embed=lambda _: [vector], fetch=fetch)

    expected = {(e.source_id, e.pdf_page) for e in q.expected}
    row = {
        "id": q.id,
        "phrasing": q.phrasing,
        "split": q.split,
        "answerable": q.answerable,
        "unanswerable_kind": q.unanswerable_kind,
        "company": q.company,
        "ranked": [[p.source_id, p.pdf_page] for p in passages],
        "first_hit_rank": first_hit_rank([(p.source_id, p.pdf_page) for p in passages], expected),
        "judged": False,
    }
    if not use_judge:
        return row

    judgement = None
    if passages:
        jkey = digest(judge_model, q.question, *(str(p.chunk_id) for p in passages))
        cached = cache.get("judgement", jkey)
        if cached is None:
            judgement = judge(q.question, passages)
            cache.put("judgement", jkey, judgement.model_dump())
        else:
            judgement = AnswerabilityJudgement.model_validate(cached)
    result = _resolve(q.question, f, passages, judgement)
    by_id = {p.chunk_id: p for p in passages}
    quotes = result.data.get("quotes", []) if result.status == "ok" else []
    cited = sorted(
        {(by_id[c["chunk_id"]].source_id, by_id[c["chunk_id"]].pdf_page) for c in quotes}
    )
    answered = result.status == "ok"
    row |= {
        "judged": True,
        "status": result.status,
        "refusal_level": result.data.get("refusal_level"),
        "judge_answered": result.data.get("judge_answered"),
        "quotes_kept": len(quotes),
        "quotes_withheld": result.data.get("quotes_withheld", 0),
        "quotes_dropped": result.data.get("quotes_dropped", 0),
        "cited_pages": [list(c) for c in cited],
        "cited_expected_page": bool(set(cited) & expected),
        # Answered, and nothing quoted is from a page that answers: the judge's yes is wrong.
        "judge_false_positive": answered and not (q.answerable and set(cited) & expected),
    }
    return row


def summarise(rows: list[dict]) -> dict:
    answerable = [r for r in rows if r["answerable"]]
    unanswerable = [r for r in rows if not r["answerable"]]
    out = {
        "questions": len(rows),
        "answerable": len(answerable),
        "unanswerable": len(unanswerable),
        **hit_metrics([r["first_hit_rank"] for r in answerable]),
        "judged": sum(r["judged"] for r in rows),
    }
    if not out["judged"]:
        return out

    def refused(group: list[dict]) -> dict:
        levels: dict[str, int] = {}
        for r in group:
            if r["status"] != "ok":
                levels[r["refusal_level"]] = levels.get(r["refusal_level"], 0) + 1
        return levels

    out |= {
        "refused_unanswerable": sum(r["status"] != "ok" for r in unanswerable),
        "refused_answerable": sum(r["status"] != "ok" for r in answerable),
        "refusal_levels_unanswerable": refused(unanswerable),
        "refusal_levels_answerable": refused(answerable),
        "answered_answerable": sum(r["status"] == "ok" for r in answerable),
        "answered_citing_expected_page": sum(
            r["status"] == "ok" and r["cited_expected_page"] for r in answerable
        ),
        "judge_false_positive_unanswerable": sum(r["judge_false_positive"] for r in unanswerable),
        "judge_false_positive_answerable": sum(r["judge_false_positive"] for r in answerable),
        "quotes_dropped": sum(r["quotes_dropped"] for r in rows),
    }
    return out


def print_summary(label: str, s: dict) -> None:
    a, u = s["answerable"], s["unanswerable"]
    line = (
        f"  {label:38} hit@1 {n_of(s['hit_at_1'], a):8} hit@5 {n_of(s['hit_at_5'], a):8} "
        f"MRR {s['mrr'] if s['mrr'] is not None else '-'}"
    )
    if s["judged"]:
        line += (
            f" | refused: unans {n_of(s['refused_unanswerable'], u)}, ans "
            f"{n_of(s['refused_answerable'], a)} | judge FP: unans "
            f"{n_of(s['judge_false_positive_unanswerable'], u)}, ans "
            f"{n_of(s['judge_false_positive_answerable'], a)}"
        )
    print(line)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--split", choices=("dev", "test"), required=True)
    parser.add_argument("--no-judge", action="store_true", help="retrieval metrics only")
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args(argv)

    qs = load_questions(PEER_QUESTIONS_PATH)
    if qs.review_status != "reviewed":
        print("The peer question set is still a draft. It is reviewed before the first run.")
        return 1

    model = configured_model()
    judge_model = get_settings().openai_model
    usage: list[dict] = []
    embed, judge = openai_embedder(model, usage), openai_judge(usage, corpus=PEER_CORPUS)
    cache = Cache(CACHE_DIR, enabled=not args.no_cache)

    rows = [
        score_question(
            q,
            embed=embed,
            judge=judge,
            cache=cache,
            model=model,
            judge_model=judge_model,
            use_judge=not args.no_judge,
        )
        for q in qs.split(args.split)
    ]

    git = git_state()
    key_sha = file_sha256(PEER_QUESTIONS_PATH)
    dirty = " (DIRTY TREE - not a clean measurement)" if git["dirty"] else ""
    print(
        f"peer retrieval eval, split {args.split}: {len(rows)} questions; commit "
        f"{git['commit'][:7]}{dirty}\n  embedding {model}; judge "
        f"{judge_model if not args.no_judge else '-'}; chunker {CHUNKER_VERSION}; k={TOP_K}; "
        f"question set v{qs.version} ({qs.review_status}), sha256 {key_sha[:16]}\n"
    )
    summary = {"overall": summarise(rows)}
    print_summary("overall", summary["overall"])
    for style in STYLES:
        summary[style] = summarise([r for r in rows if r["phrasing"] == style])
        print_summary(style, summary[style])
    for company in sorted({PEERS[s].company for s in PEERS}):
        group = [r for r in rows if r["company"] == company]
        if group:
            summary[company] = summarise(group)
            print_summary(company, summary[company])

    print("\n  per question:")
    for r in rows:
        verdict = ""
        if r["judged"]:
            verdict = f"{r['status']}{'/' + r['refusal_level'] if r['refusal_level'] else ''}"
            if r["status"] == "ok":
                verdict += f", cites {[p for _, p in r['cited_pages']]}"
            if r["judge_false_positive"]:
                verdict += "  JUDGE FALSE POSITIVE"
        kind = "answerable" if r["answerable"] else r["unanswerable_kind"]
        print(f"    {r['id']:28} {kind:10} first hit {r['first_hit_rank'] or '-':>2}  {verdict}")

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = RUNS_DIR / f"peer_retrieval_{args.split}_{stamp}.json"
    record = {
        "split": args.split,
        "git": git,
        "embedding_model": model,
        "judge_model": judge_model if not args.no_judge else None,
        "chunker_version": CHUNKER_VERSION,
        "top_k": TOP_K,
        "question_set": {
            "version": qs.version,
            "review_status": qs.review_status,
            "sha256": key_sha,
        },
        "summary": summary,
        "usage_tokens": sum(u.get("total_tokens", 0) for u in usage),
        "rows": rows,
    }
    out.write_text(json.dumps(record, indent=2) + "\n")
    print(f"\n  run written to {out.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
