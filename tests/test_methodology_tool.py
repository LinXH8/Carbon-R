"""`search_methodology`: both kinds of "I don't know", verified quotes, and the search filter.

Offline. The passages are invented for the test, not taken from the GHG Protocol.
"""

from contextlib import contextmanager

import pytest

import carbon_r.tools.methodology as methodology
from carbon_r.agent.tools import Toolkit
from carbon_r.agent.toolspec import SearchMethodologyArgs, schema_for
from carbon_r.rag.chunking import CHUNKER_VERSION
from carbon_r.tools.base import ok
from carbon_r.tools.methodology import (
    AnswerabilityJudgement,
    JudgedCitation,
    Passage,
    _resolve,
    _search_sql,
    quote_in_passage,
    search_methodology,
    verified_citations,
)

TEXT = (
    "A company that leases a delivery van and controls how it is driven reports the "
    "fuel it burns as a direct emission. Emissions from a van it does not control are "
    "reported elsewhere."
)
QUOTE = "reports the fuel it burns as a direct emission"


def passage(chunk_id=1, similarity=0.6, text=TEXT, printed_page=12) -> Passage:
    return Passage(
        chunk_id=chunk_id,
        source_id="ghgp_corporate",
        title="Test Standard",
        publisher="Test Publisher",
        year=2004,
        url="https://example.org/standard",
        pdf_page=14,
        printed_page=printed_page,
        text=text,
        similarity=similarity,
    )


def judged(answered=True, quote=QUOTE, chunk_id=1) -> AnswerabilityJudgement:
    citations = [JudgedCitation(chunk_id=chunk_id, quote=quote)] if quote else []
    return AnswerabilityJudgement(answered=answered, citations=citations, reason="test")


# --- the search query -------------------------------------------------------------------


@pytest.mark.parametrize("filter_source", [False, True])
def test_every_search_filters_on_corpus_model_and_chunker_version(filter_source):
    sql = _search_sql(filter_source=filter_source)
    assert "c.corpus = 'methodology'" in sql
    assert "c.embedding_model = %(embedding_model)s" in sql
    assert "c.chunker_version = %(chunker_version)s" in sql
    assert ("c.source_id = %(source_id)s" in sql) is filter_source


def test_the_search_binds_the_configured_model_and_current_version(monkeypatch):
    """The parameters that reach Postgres, captured from the real fetch path."""
    captured = {}

    class Cursor:
        def execute(self, sql, params):
            captured.update(sql=sql, params=params)
            return self

        def fetchall(self):
            return []

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    class Conn:
        def cursor(self, **_):
            return Cursor()

    @contextmanager
    def fake_connect():
        yield Conn()

    monkeypatch.setattr(methodology, "connect", fake_connect)
    monkeypatch.setattr(methodology, "configured_model", lambda: "model-under-test")
    methodology._db_fetch([0.0] * 1536, None, 5)
    assert captured["params"]["embedding_model"] == "model-under-test"
    assert captured["params"]["chunker_version"] == CHUNKER_VERSION
    assert "c.embedding_model = %(embedding_model)s" in captured["sql"]


# --- quote verification -----------------------------------------------------------------


def test_a_verbatim_quote_is_accepted_across_whitespace_differences():
    assert quote_in_passage("reports the fuel  it\nburns as a direct emission", TEXT)


def test_a_paraphrase_is_rejected():
    assert not quote_in_passage("reports the fuel it uses as a direct emission", TEXT)


def test_a_quote_too_short_to_prove_anything_is_rejected():
    assert not quote_in_passage("a direct emission", TEXT)


def test_a_quote_from_a_passage_it_does_not_name_is_dropped():
    other = passage(chunk_id=2, text="Unrelated words about something else entirely here.")
    kept, dropped = verified_citations(judged(chunk_id=2), [passage(), other])
    assert kept == [] and dropped == 1


# --- the decision -----------------------------------------------------------------------


def test_ok_carries_the_verified_quote_and_its_citation():
    result = _resolve("q", [passage()], judged(), threshold=0.3)
    assert result.status == "ok"
    (quote,) = result.data["quotes"]
    assert quote["quote"] == QUOTE
    assert quote["citation"] == "Test Standard, p. 12 (PDF p. 14)"
    assert result.data["refusal_level"] is None


def test_below_the_threshold_is_a_retrieval_level_no_results():
    result = _resolve("q", [passage(similarity=0.2)], None, threshold=0.3)
    assert result.status == "no_results"
    assert result.data["refusal_level"] == "retrieval"
    assert result.data["best_similarity"] == 0.2


def test_no_passages_at_all_is_a_retrieval_level_no_results():
    result = _resolve("q", [], None, threshold=0.0)
    assert result.status == "no_results" and result.data["refusal_level"] == "retrieval"


def test_the_judge_saying_no_is_a_generation_level_no_results():
    result = _resolve("q", [passage()], judged(answered=False, quote=None), threshold=0.3)
    assert result.status == "no_results"
    assert result.data["refusal_level"] == "generation"
    assert result.data["passages"], "what WAS found is still shown"


def test_a_yes_backed_only_by_a_fabricated_quote_is_a_generation_level_no_results():
    fabricated = judged(quote="companies must always report leased vans as Scope 1")
    result = _resolve("q", [passage()], fabricated, threshold=0.3)
    assert result.status == "no_results"
    assert result.data["refusal_level"] == "generation"
    assert result.data["quotes_dropped"] == 1


def test_a_passage_without_a_printed_page_is_cited_by_pdf_page_and_says_so():
    assert passage(printed_page=None).citation == (
        "Test Standard, PDF p. 14 (no printed page number)"
    )


# --- the composed tool ------------------------------------------------------------------


def fake_embed(texts):
    return [[0.0] * 1536 for _ in texts]


def test_the_judge_is_not_called_below_the_threshold():
    def judge(*_):
        raise AssertionError("judge must not be called")

    result = search_methodology(
        "q",
        threshold=0.5,
        embed=fake_embed,
        fetch=lambda *_: [passage(similarity=0.1).model_dump()],
        judge=judge,
    )
    assert result.status == "no_results" and result.data["refusal_level"] == "retrieval"


def test_the_composed_tool_answers_through_the_judge():
    result = search_methodology(
        "q",
        threshold=0.3,
        embed=fake_embed,
        fetch=lambda *_: [passage().model_dump()],
        judge=lambda q, passages: judged(),
    )
    assert result.status == "ok"


def test_an_empty_question_is_an_error():
    assert search_methodology("  ").status == "error"


# --- the agent's view -------------------------------------------------------------------


def test_the_arguments_are_strict_and_source_is_optional():
    schema = schema_for(SearchMethodologyArgs)
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"question", "source_id"}
    assert SearchMethodologyArgs(question="q").source_id is None
    with pytest.raises(ValueError):
        SearchMethodologyArgs(question="q", source_id="ema_ses_2025")


def test_the_toolkit_passes_the_question_through_and_creates_no_handle(session):
    seen = []

    def search(question, source_id):
        seen.append((question, source_id))
        return ok({"quotes": []}, "fine")

    toolkit = Toolkit(session, search=search)
    result = toolkit.dispatch(
        "search_methodology", '{"question": "how?", "source_id": "ghgp_scope2"}'
    )
    assert result.status == "ok"
    assert seen == [("how?", "ghgp_scope2")]
    assert session.factors == {} and session.activities == {}
