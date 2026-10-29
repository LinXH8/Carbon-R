"""`search_peer_disclosures` - passages from 12 companies' sustainability reports, cited, or "no".

The second tool that ranks by similarity, and CLAUDE.md rule 3 still governs it: it
retrieves PROSE - how a company draws its boundary, what it targets, how it treats financed
emissions - and never a figure. Nothing it returns is fed to `calculate_emissions`.

Why no figure, even when a passage plainly contains one: the reports are designed PDFs, and
their text layer does not keep a table's meaning. Measured on DBS's report (PDF p.79), in
both pdftotext modes, footnote markers fuse onto the numbers - a six-digit total with
its footnote number attached, the Scope 1 label likewise (invented examples of the
shape: "240,5763", "Total scope 17") - and reading order loses which column is which
year. A number read out of a passage is therefore not a number the report states.
The agent cites the page instead, and the figure audit (`agent/audit.py`) refuses any number
that only a peer passage could have supplied. Verified figures belong to a later
`peer_disclosures` table, checked by hand against the page.

Hybrid retrieval, deterministic end to end:

    vector    the VECTOR_DEPTH nearest chunks by cosine distance - an exact scan, as for
              methodology, so a commit SHA reproduces a result.
    keyword   the KEYWORD_DEPTH best chunks by `ts_rank_cd` against the question's words
              OR-ed together. A question's words AND-ed (plainto_tsquery's default) almost
              never all appear in one chunk; OR keeps exact terms like "Scope 3" or
              "financed emissions" in play, which embeddings blur.
    fusion    reciprocal rank fusion in Python (`fuse`), ties broken by chunk id. Both legs
              apply one filter (`_peer_filter`): corpus, embedding model and chunker
              version always, plus company, sector and report year when given.

No similarity floor. The methodology floor (0.30) refused nothing on its dev split, and a
fused rank is not a similarity to set one on. The refusals are:

    not_ingested         no peer chunk under this model and chunker version; nothing is
                         embedded or searched.
    no_match_for_filter  the corpus is there but the filter excludes all of it - typically
                         a year the company did not report under. The message lists what
                         that company or sector does have, and the report-year convention.
    generation           the judge (the methodology judge, reused) says no passage states
                         the answer, or no quote it gives is verbatim in its passage.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

import psycopg
from psycopg.rows import dict_row

from carbon_r.agent.audit import is_table_like
from carbon_r.db import DatabaseNotConfiguredError, connect
from carbon_r.peer_sources import PeerSource
from carbon_r.rag.chunking import CHUNKER_VERSION, normalise
from carbon_r.rag.embed import EmbeddingNotConfiguredError, configured_model, to_pgvector
from carbon_r.sources import PEERS
from carbon_r.tools.base import ToolResult, error, no_results, ok
from carbon_r.tools.methodology import (
    AnswerabilityJudgement,
    Judge,
    JudgedCitation,
    Passage,
    openai_judge,
    verified_citations,
)

# Candidates per leg, and passages returned after fusion. Tables and their prose sit on
# different chunks, so more passages are shown than for methodology (5).
VECTOR_DEPTH = 50
KEYWORD_DEPTH = 50
TOP_K = 8

# The usual reciprocal-rank-fusion constant: a rank-1 hit in one leg is worth about as much
# as rank ~30 in both, so neither leg dominates.
RRF_K = 60

PEER_CORPUS = "company sustainability reports"

RefusalLevel = Literal["not_ingested", "no_match_for_filter", "generation"]

NOT_INGESTED_MESSAGE = (
    "The peer sustainability reports are not loaded in this database: they are the issuers' "
    "copyright and are not distributed with Carbon R. See the README section 'Peer corpus' "
    "to ingest them. Tell the user that peer questions cannot be answered until then; do "
    "not answer from memory."
)


def _non_calendar() -> str:
    years = sorted(
        {
            f"{p.company} ({p.period_start.day} {p.period_start:%b} to "
            f"{p.period_end.day} {p.period_end:%b})"
            for p in PEERS.values()
            if (p.period_start.month, p.period_start.day) != (1, 1)
        }
    )
    return "; ".join(years)


DISCLOSURE = (
    "Peer disclosures are self-reported by each company, under its own boundary, "
    "consolidation approach and reporting period, and are not assured by Carbon R; they "
    "are not comparable like-for-like. Non-calendar financial years: "
    f"{_non_calendar()}. Numbers inside these passages come from PDF text extraction, which "
    "fuses footnote markers onto figures and loses table columns: state no figure from a "
    "passage - cite the company, report and page instead."
)


@dataclass(frozen=True)
class PeerFilter:
    company: str | None = None
    sector: str | None = None
    report_year: int | None = None

    def is_empty(self) -> bool:
        return self.company is None and self.sector is None and self.report_year is None

    def describe(self) -> str:
        parts = [
            f"{name} {value}"
            for name, value in (
                ("company", self.company),
                ("sector", self.sector),
                ("report_year", self.report_year),
            )
            if value is not None
        ]
        return ", ".join(parts) or "no filter"


class PeerPassage(Passage):
    company: str
    sector: str
    report_year: int
    period_start: date
    period_end: date
    document_kind: str
    vector_rank: int | None = None
    keyword_rank: int | None = None
    rrf_score: float = 0.0


# --- SQL: one filter, two legs, one fetch --------------------------------------------------


def _peer_filter(f: PeerFilter) -> str:
    """Corpus, model and version ALWAYS; company, sector and year when given."""
    clauses = [
        "c.corpus = 'peer'",
        "c.embedding_model = %(embedding_model)s",
        "c.chunker_version = %(chunker_version)s",
    ]
    if f.company is not None:
        clauses.append("c.company = %(company)s")
    if f.sector is not None:
        clauses.append("c.sector = %(sector)s")
    if f.report_year is not None:
        clauses.append("c.report_year = %(report_year)s")
    return "\n      and ".join(clauses)


def _count_sql(f: PeerFilter) -> str:
    return f"select count(*) from document_chunks c where {_peer_filter(f)}"


def _vector_sql(f: PeerFilter) -> str:
    return f"""
    select c.id from document_chunks c
    where {_peer_filter(f)}
    order by c.embedding <=> %(query)s::vector, c.id
    limit %(vector_depth)s
    """


# The question's lexemes OR-ed: plainto_tsquery gives 'a' & 'b' & 'c'; the replace turns it
# into 'a' | 'b' | 'c'. A question of only stop words gives an empty query, which matches
# nothing, so the keyword leg is simply empty.
# It sits in a one-row subselect: a cast is not allowed as a FROM item (the first live run
# failed on exactly that, which the offline SQL-string tests could not catch).
_OR_QUERY = "replace(plainto_tsquery('english', %(question)s)::text, ' & ', ' | ')::tsquery"


def _keyword_sql(f: PeerFilter) -> str:
    return f"""
    select c.id from document_chunks c
    cross join (select {_OR_QUERY} as q) as w
    where {_peer_filter(f)}
      and c.tsv @@ w.q
    order by ts_rank_cd(c.tsv, w.q) desc, c.id
    limit %(keyword_depth)s
    """


_ROWS_SQL = """
select c.id as chunk_id, c.source_id, s.title, s.publisher, s.year, s.url,
       c.pdf_page, c.printed_page, c.section, c.text, c.company, c.sector, c.report_year,
       1 - (c.embedding <=> %(query)s::vector) as similarity
from document_chunks c
join sources s on s.id = c.source_id
where c.id = any(%(ids)s)
"""


# --- fusion (pure) -------------------------------------------------------------------------


@dataclass(frozen=True)
class Fused:
    chunk_id: int
    score: float
    vector_rank: int | None
    keyword_rank: int | None


def fuse(
    vector_ids: Sequence[int], keyword_ids: Sequence[int], *, k: int = RRF_K, top: int = TOP_K
) -> list[Fused]:
    """Reciprocal rank fusion of two rankings (1-based ranks). Ties go to the lower chunk id."""
    vector_rank = {cid: rank for rank, cid in enumerate(vector_ids, start=1)}
    keyword_rank = {cid: rank for rank, cid in enumerate(keyword_ids, start=1)}
    fused = [
        Fused(
            cid,
            sum(1 / (k + r) for r in (vector_rank.get(cid), keyword_rank.get(cid)) if r),
            vector_rank.get(cid),
            keyword_rank.get(cid),
        )
        for cid in vector_rank.keys() | keyword_rank.keys()
    ]
    fused.sort(key=lambda f: (-f.score, f.chunk_id))
    return fused[:top]


# --- the decision (pure) -------------------------------------------------------------------


def _period(p: PeerSource) -> str:
    text = f"{p.period_start.day} {p.period_start:%b %Y} to {p.period_end.day} {p.period_end:%b %Y}"
    if (p.period_end.month, p.period_end.day) != (12, 31):
        text += f", a financial year ending {p.period_end.day} {p.period_end:%B}"
    return text


def no_match_message(f: PeerFilter, peers: Sequence[PeerSource] | None = None) -> str:
    """Why a filter matched nothing, and what the same company or sector does have."""
    peers = list(PEERS.values()) if peers is None else list(peers)
    near = [
        p
        for p in peers
        if (f.company is None or p.company == f.company)
        and (f.sector is None or p.sector == f.sector)
    ]
    lines = [
        f"No peer passages match {f.describe()}. report_year is the calendar year in which "
        "a report's period ENDS: for example, a financial year from 1 April 2025 to 31 "
        "March 2026 is report_year 2026, not 2025."
    ]
    if near:
        lines.append("What is ingested for that company or sector instead:")
        lines += [
            f"- {p.company}: report_year {p.report_year}, {p.title.split(' (')[0]}, {_period(p)}"
            for p in sorted(near, key=lambda p: (p.company, p.report_year, p.source_id))
        ]
    else:
        lines.append(
            "No ingested report matches that company and sector together. Companies: "
            + ", ".join(sorted({p.company for p in peers}))
            + "."
        )
    lines.append(
        "Tell the user what exists, and ask whether to search that instead; do not answer "
        "from another year without saying so."
    )
    return "\n".join(lines)


def _passage_data(p: PeerPassage) -> dict:
    return p.model_dump(mode="json") | {"citation": p.citation}


def _resolve(
    question: str,
    f: PeerFilter,
    passages: Sequence[PeerPassage],
    judgement: AnswerabilityJudgement | None,
) -> ToolResult:
    """Every branch after retrieval, with no database, model or network. Pure."""
    context: dict[str, Any] = {
        "question": question,
        "filter": f.describe(),
        "disclosure": DISCLOSURE,
    }
    if not passages:
        return no_results(
            "The search returned no peer passages under this filter. " + DISCLOSURE,
            context | {"refusal_level": "generation", "passages": []},
        )
    if judgement is None:
        raise ValueError("A judgement is required once passages were retrieved.")

    kept, dropped = verified_citations(judgement, passages)
    context |= {
        "passages": [_passage_data(p) for p in passages],
        "judge_answered": judgement.answered,
        "judge_reason": judgement.reason,
        "quotes_dropped": dropped,
    }
    if not judgement.answered or not kept:
        why = (
            "none of them states the answer"
            if not judgement.answered
            else "the judge's quotes could not be found verbatim in the passages"
        )
        return no_results(
            f"Peer passages on a related topic were found, but {why}. Do not answer from "
            "memory: say what the passages do cover, cite company, report and page, and say "
            "the question is not answered. " + DISCLOSURE,
            context | {"refusal_level": "generation"},
        )

    unique = _first_of_each_quote(kept)
    by_id = {p.chunk_id: p for p in passages}
    quotes = [_quote_data(c.quote, by_id[c.chunk_id]) for c in unique]
    withheld = sum(1 for q in quotes if q["withheld"])
    sources = "; ".join(dict.fromkeys(q["citation"] for q in quotes))
    instruction = (
        f", of which {withheld} table-like, withheld: cite their page only. Quote the others"
        if withheld
        else ". Quote them"
    )
    return ok(
        context
        | {
            "refusal_level": None,
            "quotes": quotes,
            "quotes_withheld": withheld,
            "quotes_repeated": len(kept) - len(unique),
        },
        f"Answered by {len(quotes)} verified quote(s) from: {sources}{instruction} exactly "
        "and cite company, report and page; do not add to them from memory. " + DISCLOSURE,
    )


def _first_of_each_quote(citations: Sequence[JudgedCitation]) -> list[JudgedCitation]:
    """Overlapping chunks can yield the same sentence twice; keep its first citation."""
    seen: set[str] = set()
    unique = []
    for c in citations:
        key = normalise(c.quote).casefold()
        if key not in seen:
            seen.add(key)
            unique.append(c)
    return unique


def _quote_data(quote: str, passage: PeerPassage) -> dict[str, Any]:
    base = {
        "chunk_id": passage.chunk_id,
        "citation": passage.citation,
        "company": passage.company,
    }
    if not is_table_like(quote):
        return base | {"quote": quote, "withheld": False}
    page = (
        f"p. {passage.printed_page}"
        if passage.printed_page is not None
        else f"PDF p. {passage.pdf_page}"
    )
    return base | {"quote": None, "withheld": True, "note": f"see the table on {page}"}


# --- the database, and the composed tool ---------------------------------------------------

Fetcher = Callable[[list[float], str, PeerFilter], list[dict[str, Any]]]
Counter = Callable[[PeerFilter], int]


def _params(f: PeerFilter) -> dict[str, Any]:
    return {
        "embedding_model": configured_model(),
        "chunker_version": CHUNKER_VERSION,
        "company": f.company,
        "sector": f.sector,
        "report_year": f.report_year,
    }


def _db_count(f: PeerFilter) -> int:
    with connect() as conn:
        return conn.execute(_count_sql(f), _params(f)).fetchone()[0]


def _db_fetch(vector: list[float], question: str, f: PeerFilter) -> list[dict[str, Any]]:
    """The fused passages, best first, each with its ranks in both legs."""
    params = _params(f) | {
        "query": to_pgvector(vector),
        "question": question,
        "vector_depth": VECTOR_DEPTH,
        "keyword_depth": KEYWORD_DEPTH,
    }
    with connect() as conn, conn.cursor(row_factory=dict_row) as cur:
        vector_ids = [r["id"] for r in cur.execute(_vector_sql(f), params).fetchall()]
        keyword_ids = [r["id"] for r in cur.execute(_keyword_sql(f), params).fetchall()]
        fused = fuse(vector_ids, keyword_ids)
        rows = cur.execute(_ROWS_SQL, params | {"ids": [x.chunk_id for x in fused]}).fetchall()
    by_id = {r["chunk_id"]: r for r in rows}
    return [
        by_id[x.chunk_id]
        | {"vector_rank": x.vector_rank, "keyword_rank": x.keyword_rank, "rrf_score": x.score}
        for x in fused
    ]


def _with_source(row: dict[str, Any]) -> PeerPassage:
    peer = PEERS[row["source_id"]]
    return PeerPassage(
        **row,
        period_start=peer.period_start,
        period_end=peer.period_end,
        document_kind=peer.document_kind,
    )


def retrieve(
    question: str,
    f: PeerFilter | None = None,
    *,
    embed: Callable[[Sequence[str]], list[list[float]]] | None = None,
    fetch: Fetcher | None = None,
) -> list[PeerPassage]:
    """The fused passages, with no judge. A later retrieval eval scores this directly."""
    if embed is None:
        from carbon_r.rag.embed import openai_embedder

        embed = openai_embedder()
    (vector,) = embed([question])
    rows = (fetch or _db_fetch)(vector, question, f or PeerFilter())
    return [_with_source(row) for row in rows]


def search_peer_disclosures(
    question: str,
    company: str | None = None,
    sector: str | None = None,
    report_year: int | None = None,
    *,
    embed: Callable[[Sequence[str]], list[list[float]]] | None = None,
    fetch: Fetcher | None = None,
    judge: Judge | None = None,
    count: Counter | None = None,
) -> ToolResult:
    """Passages from the peer reports that answer `question`, or no_results.

    Both refusals that need no search - nothing ingested, or a filter that excludes every
    chunk - are decided before anything is embedded, so they cost no API call.
    """
    if not question.strip():
        return error("Cannot search peer reports: the question is empty.")
    f = PeerFilter(company, sector, report_year)
    if company is not None and company not in {p.company for p in PEERS.values()}:
        return error(f"Cannot search peer reports: no ingested report is from {company!r}.")
    count = count or _db_count
    try:
        if count(PeerFilter()) == 0:
            return no_results(
                NOT_INGESTED_MESSAGE,
                {"question": question, "refusal_level": "not_ingested"},
            )
        if not f.is_empty() and count(f) == 0:
            return no_results(
                no_match_message(f),
                {
                    "question": question,
                    "filter": f.describe(),
                    "refusal_level": "no_match_for_filter",
                },
            )
        passages = retrieve(question, f, embed=embed, fetch=fetch)
        judgement = None
        if passages:
            judgement = (judge or openai_judge(corpus=PEER_CORPUS))(question, passages)
    except (DatabaseNotConfiguredError, EmbeddingNotConfiguredError) as exc:
        return error(f"Cannot search peer reports: {exc}")
    except psycopg.Error as exc:
        return error(f"Database error during peer search: {exc}")
    return _resolve(question, f, passages, judgement)


def _demo() -> None:  # pragma: no cover - manual verification aid
    """`python -m carbon_r.tools.peer "question" [company] [report_year]`, one per call."""
    import sys

    question, *rest = sys.argv[1:]
    company = rest[0] if len(rest) > 0 and rest[0] else None
    year = int(rest[1]) if len(rest) > 1 else None
    result = search_peer_disclosures(question, company=company, report_year=year)
    data = result.data or {}
    print(f"Q: {question}  [{PeerFilter(company, None, year).describe()}]")
    print(f"{result.status} ({data.get('refusal_level')}): {result.message}")
    for p in data.get("passages", []):
        print(
            f"  - {p['citation']}  sim={p['similarity']:.3f} "
            f"v={p['vector_rank']} k={p['keyword_rank']}"
        )
    for q in data.get("quotes", []):
        text = q["note"] if q["withheld"] else q["quote"][:200]
        print(f"  quote [{q['citation']}] withheld={q['withheld']}: {text}")
    if "judge_reason" in data:
        print(f"  judge: {data['judge_reason']}")


if __name__ == "__main__":  # pragma: no cover
    _demo()


__all__ = [
    "DISCLOSURE",
    "PeerFilter",
    "PeerPassage",
    "fuse",
    "is_table_like",
    "no_match_message",
    "retrieve",
    "search_peer_disclosures",
]
