"""`search_methodology` - GHG Protocol passages, cited by document and page, or an honest "no".

This is the one tool in Carbon R that ranks by embedding distance, and CLAUDE.md rule 3 is
why it is allowed: it retrieves methodology PROSE, never a factor value. Nothing it returns
is fed to `calculate_emissions`.

Two kinds of "I don't know", both decided in Python (`_resolve`), never left to the agent:

    retrieval   the closest passage is below RETRIEVAL_THRESHOLD in cosine similarity, so
                nothing is close enough to be worth showing a model. no_results, with the
                best score and where it came from.
    generation  passages were close, but a judging model, asked whether they answer the
                question, either says no or cannot back a yes with a quote that is really
                there. no_results, with the passages in `data` so the agent can say what
                WAS found.

The judge's "yes" is not trusted on its own. Each citation must carry a quote, and Python
keeps a citation only if the quote, whitespace-normalised, is a verbatim substring of the
passage it names and is at least MIN_QUOTE_WORDS long. A judge that paraphrases, or invents a
sentence, produces no surviving citation and the answer is a generation-level no_results.

Every search filters on the configured embedding model and the current chunker version as
well as the corpus (`_search_sql`). Vectors from two models are not comparable, and chunks
from a retired chunker version are not the text a citation would point to.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, Literal

import psycopg
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field

from carbon_r.config import get_settings
from carbon_r.db import DatabaseNotConfiguredError, connect
from carbon_r.rag.chunking import CHUNKER_VERSION, normalise
from carbon_r.rag.embed import EmbeddingNotConfiguredError, configured_model, to_pgvector
from carbon_r.tools.base import ToolResult, error, no_results, ok

# Passages retrieved per question.
TOP_K = 5

# Cosine similarity below which nothing is shown to the judge. Frozen at 0.30 in Tier 2
# Block 4 from the dev split (docs/blocks/TIER2_BLOCK4.md), as a FLOOR, not a filter: on
# dev the nearest passage scored 0.360-0.798 for answerable questions and 0.357-0.609 for
# unanswerable ones, and every threshold above 0.3603 refused an answerable question while
# refusing only unanswerable ones the generation-level check already refused. So on dev this
# check does no measurable work; the refusals come from the judge. It stays as a guard
# against a question with nothing remotely near it in the corpus.
RETRIEVAL_THRESHOLD = 0.30

# A quote shorter than this proves nothing: "the company" is a substring of most passages.
MIN_QUOTE_WORDS = 5

RefusalLevel = Literal["not_ingested", "retrieval", "generation"]

NOT_INGESTED_MESSAGE = (
    "The GHG Protocol methodology corpus is not loaded in this database: it is not "
    "distributed with Carbon R, because its terms do not allow redistribution. See the "
    "README section 'Methodology corpus' to download the three standards and ingest them. "
    "Tell the user that methodology questions cannot be answered until then; do not answer "
    "from memory."
)

# The filter every query against the corpus applies: one definition, so the count that
# decides "not ingested" and the search that ranks can never disagree about what is there.
_CORPUS_FILTER = """c.corpus = 'methodology'
      and c.embedding_model = %(embedding_model)s
      and c.chunker_version = %(chunker_version)s"""


def _count_sql(*, filter_source: bool) -> str:
    """How many searchable chunks exist, under exactly the search's filter."""
    source_clause = "and c.source_id = %(source_id)s" if filter_source else ""
    return f"select count(*) from document_chunks c where {_CORPUS_FILTER} {source_clause}"


def _search_sql(*, filter_source: bool) -> str:
    """The only search query. Corpus, model and chunker version are ALWAYS filtered."""
    source_clause = "and c.source_id = %(source_id)s" if filter_source else ""
    return f"""
    select c.id as chunk_id, c.source_id, s.title, s.publisher, s.year, s.url,
           c.pdf_page, c.printed_page, c.section, c.text,
           1 - (c.embedding <=> %(query)s::vector) as similarity
    from document_chunks c
    join sources s on s.id = c.source_id
    where {_CORPUS_FILTER}
      {source_clause}
    order by c.embedding <=> %(query)s::vector, c.id
    limit %(k)s
    """


class Passage(BaseModel):
    model_config = ConfigDict(frozen=True)

    chunk_id: int
    source_id: str
    title: str
    publisher: str
    year: int | None
    url: str
    pdf_page: int
    printed_page: int | None
    section: str | None = None
    text: str
    similarity: float

    @property
    def citation(self) -> str:
        if self.printed_page is not None:
            return f"{self.title}, p. {self.printed_page} (PDF p. {self.pdf_page})"
        return f"{self.title}, PDF p. {self.pdf_page} (no printed page number)"


class JudgedCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: int = Field(description="chunk_id of the passage the quote is taken from")
    quote: str = Field(description="Words copied exactly from that passage, unaltered")


class AnswerabilityJudgement(BaseModel):
    """What the judging model returns. Its `answered` is checked, not believed."""

    model_config = ConfigDict(extra="forbid")

    answered: bool = Field(description="True only if the passages state the answer")
    citations: list[JudgedCitation] = Field(
        description="The exact sentences that answer the question; empty if not answered"
    )
    reason: str = Field(description="One sentence: why the passages do or do not answer it")


# `{corpus}` names what the passages are. The methodology judge fills it with
# METHODOLOGY_CORPUS, which reproduces the instruction Part A's eval was scored with byte for
# byte (a test pins it); the peer search (`tools.peer`) reuses the same judge and rules.
METHODOLOGY_CORPUS = "GHG Protocol documents"

JUDGE_INSTRUCTION = """\
You are checking whether passages from {corpus} answer a question.

Rules:
- Use only the passages below. Do not use anything you know from elsewhere.
- answered = true only if a passage itself states the answer. A passage on the same topic
  that does not state the answer is NOT an answer.
- For each passage that answers, give its chunk_id and quote the answering words EXACTLY as
  they appear in that passage: copy, never paraphrase, never join sentences from two places.
  A quote should be a full sentence or clause, not a few words.
- If no passage answers, answered = false and citations is empty.

Question: {question}

Passages:
{passages}
"""

Fetcher = Callable[[list[float], str | None, int], list[dict[str, Any]]]
Counter = Callable[[str | None], int]
Judge = Callable[[str, Sequence[Passage]], AnswerabilityJudgement]


def quote_in_passage(quote: str, passage_text: str) -> bool:
    """A verbatim, whitespace-normalised substring of at least MIN_QUOTE_WORDS words."""
    needle = normalise(quote)
    return len(needle.split()) >= MIN_QUOTE_WORDS and needle in normalise(passage_text)


def verified_citations(
    judgement: AnswerabilityJudgement, passages: Sequence[Passage]
) -> tuple[list[JudgedCitation], int]:
    """The judge's citations that hold up, and how many were dropped."""
    by_id = {p.chunk_id: p for p in passages}
    kept = [
        c
        for c in judgement.citations
        if c.chunk_id in by_id and quote_in_passage(c.quote, by_id[c.chunk_id].text)
    ]
    return kept, len(judgement.citations) - len(kept)


def _passage_data(passage: Passage) -> dict:
    return passage.model_dump(mode="json") | {"citation": passage.citation}


def _resolve(
    question: str,
    passages: Sequence[Passage],
    judgement: AnswerabilityJudgement | None,
    *,
    threshold: float,
) -> ToolResult:
    """Every branch of the decision, with no database, model or network. Pure."""
    context: dict[str, Any] = {"question": question, "threshold": threshold}
    if not passages:
        return no_results(
            "The methodology corpus returned no passages at all for this embedding model and "
            "chunker version. It may not have been ingested (scripts/ingest_methodology.py).",
            context | {"refusal_level": "retrieval", "best_similarity": None},
        )

    best = passages[0]
    context["best_similarity"] = round(best.similarity, 4)
    if best.similarity < threshold:
        return no_results(
            f"Nothing in the GHG Protocol corpus is close enough to this question to rely on: "
            f"the nearest passage ({best.citation}) has similarity {best.similarity:.3f}, "
            f"below the {threshold} threshold. Say that the methodology documents do not "
            "appear to cover it, and ask.",
            context | {"refusal_level": "retrieval", "nearest": best.citation},
        )

    if judgement is None:
        raise ValueError("A judgement is required once the retrieval threshold is passed.")

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
            f"Passages on a related topic were found, but {why}. Do not answer from memory: "
            "say what the passages do cover, cite them, and say the question is not answered.",
            context | {"refusal_level": "generation"},
        )

    by_id = {p.chunk_id: p for p in passages}
    quotes = [
        {"quote": c.quote, "chunk_id": c.chunk_id, "citation": by_id[c.chunk_id].citation}
        for c in kept
    ]
    sources = "; ".join(dict.fromkeys(q["citation"] for q in quotes))
    return ok(
        context | {"refusal_level": None, "quotes": quotes},
        f"Answered by {len(quotes)} verified quote(s) from: {sources}. Quote them and cite "
        "the document and page; do not add to them from memory.",
    )


def _db_fetch(vector: list[float], source_id: str | None, k: int) -> list[dict[str, Any]]:
    params = {
        "query": to_pgvector(vector),
        "embedding_model": configured_model(),
        "chunker_version": CHUNKER_VERSION,
        "source_id": source_id,
        "k": k,
    }
    with connect() as conn, conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(_search_sql(filter_source=source_id is not None), params).fetchall()


def _db_count(source_id: str | None) -> int:
    params = {
        "embedding_model": configured_model(),
        "chunker_version": CHUNKER_VERSION,
        "source_id": source_id,
    }
    with connect() as conn:
        return conn.execute(_count_sql(filter_source=source_id is not None), params).fetchone()[0]


def openai_judge(usage: list[dict] | None = None, corpus: str = METHODOLOGY_CORPUS) -> Judge:
    from openai import OpenAI

    settings = get_settings()
    client = OpenAI(api_key=settings.openai_api_key)

    def judge(question: str, passages: Sequence[Passage]) -> AnswerabilityJudgement:
        listing = "\n\n".join(f"[chunk_id {p.chunk_id}] {p.citation}\n{p.text}" for p in passages)
        response = client.responses.parse(
            model=settings.openai_model,
            input=JUDGE_INSTRUCTION.format(corpus=corpus, question=question, passages=listing),
            text_format=AnswerabilityJudgement,
        )
        if usage is not None and getattr(response, "usage", None) is not None:
            usage.append(response.usage.model_dump())
        if response.output_parsed is None:
            raise RuntimeError(f"Judge returned no parsed output (status {response.status!r})")
        return response.output_parsed

    return judge


def retrieve(
    question: str,
    source_id: str | None = None,
    *,
    k: int = TOP_K,
    embed: Callable[[Sequence[str]], list[list[float]]] | None = None,
    fetch: Fetcher | None = None,
) -> list[Passage]:
    """The ranked passages, with no threshold and no judge. The eval scores this directly."""
    if embed is None:
        from carbon_r.rag.embed import openai_embedder

        embed = openai_embedder()
    (vector,) = embed([question])
    rows = (fetch or _db_fetch)(vector, source_id, k)
    return [Passage(**row) for row in rows]


def search_methodology(
    question: str,
    source_id: str | None = None,
    *,
    k: int = TOP_K,
    threshold: float = RETRIEVAL_THRESHOLD,
    embed: Callable[[Sequence[str]], list[list[float]]] | None = None,
    fetch: Fetcher | None = None,
    judge: Judge | None = None,
    count: Counter | None = None,
) -> ToolResult:
    """Passages from the GHG Protocol standards that answer `question`, or no_results.

    An empty corpus - the normal state of a fresh clone, which cannot ship the standards -
    is reported before anything is embedded, so it costs no API call and is never confused
    with "nothing relevant was found".
    """
    if not question.strip():
        return error("Cannot search methodology: the question is empty.")
    try:
        if (count or _db_count)(source_id) == 0:
            return no_results(
                NOT_INGESTED_MESSAGE,
                {"question": question, "refusal_level": "not_ingested"},
            )
        passages = retrieve(question, source_id, k=k, embed=embed, fetch=fetch)
        judgement = None
        if passages and passages[0].similarity >= threshold:
            judgement = (judge or openai_judge())(question, passages)
    except (DatabaseNotConfiguredError, EmbeddingNotConfiguredError) as exc:
        return error(f"Cannot search methodology: {exc}")
    except psycopg.Error as exc:
        return error(f"Database error during methodology search: {exc}")
    return _resolve(question, passages, judgement, threshold=threshold)


def _demo() -> None:  # pragma: no cover - manual verification aid
    import sys

    for question in sys.argv[1:]:
        result = search_methodology(question)
        print(f"\nQ: {question}\n{result.status}: {result.message}")
        for quote in (result.data or {}).get("quotes", []):
            print(f"  - {quote['citation']}: {quote['quote'][:160]}")


if __name__ == "__main__":  # pragma: no cover
    _demo()


__all__ = [
    "MIN_QUOTE_WORDS",
    "RETRIEVAL_THRESHOLD",
    "TOP_K",
    "AnswerabilityJudgement",
    "Passage",
    "retrieve",
    "search_methodology",
]
