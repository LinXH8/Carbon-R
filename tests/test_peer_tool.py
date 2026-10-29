"""`search_peer_disclosures`: fusion, the shared filter, every refusal, and the audit's view.

Offline. The passages are invented for the test, not taken from any company's report.
"""

import hashlib

import pytest

from carbon_r.agent.audit import audit_figures, audit_quotes
from carbon_r.agent.tools import Toolkit
from carbon_r.agent.toolspec import PEER_COMPANIES, SearchPeerDisclosuresArgs, schema_for
from carbon_r.config import get_settings
from carbon_r.peer_sources import load_peer_sources
from carbon_r.tools.base import ok
from carbon_r.tools.methodology import (
    JUDGE_INSTRUCTION,
    METHODOLOGY_CORPUS,
    AnswerabilityJudgement,
    JudgedCitation,
)
from carbon_r.tools.peer import (
    DISCLOSURE,
    PeerFilter,
    _count_sql,
    _keyword_sql,
    _resolve,
    _vector_sql,
    fuse,
    is_table_like,
    no_match_message,
    retrieve,
    search_peer_disclosures,
)

TEXT = (
    "Our inventory takes the equity share approach for every leased depot we hold. "
    "Total scope 17 2,5113 tCO2e."
)
PROSE_QUOTE = "takes the equity share approach for every leased depot"
FIGURE_QUOTE = "Total scope 17 2,5113 tCO2e."


def row(chunk_id=1, source_id="peer_dbs_2025", text=TEXT, pdf_page=79, printed_page=79):
    return {
        "chunk_id": chunk_id,
        "source_id": source_id,
        "title": "DBS Sustainability Report (1 Jan 2025 to 31 Dec 2025)",
        "publisher": "DBS",
        "year": 2025,
        "url": "https://example.invalid",
        "pdf_page": pdf_page,
        "printed_page": printed_page,
        "section": None,
        "text": text,
        "company": "DBS",
        "sector": "bank",
        "report_year": 2025,
        "similarity": 0.5,
        "vector_rank": 1,
        "keyword_rank": None,
        "rrf_score": 1 / 61,
    }


def passages(*rows):
    return retrieve("q", embed=fake_embed, fetch=lambda *_: list(rows or [row()]))


def judged(answered=True, *quotes):
    return AnswerabilityJudgement(
        answered=answered,
        citations=[JudgedCitation(chunk_id=1, quote=q) for q in quotes],
        reason="Total scope 17 is 2,5113 on that page",
    )


def fake_embed(texts):
    return [[0.0] * 1536 for _ in texts]


# --- fusion ---------------------------------------------------------------------------------


def test_a_chunk_in_both_legs_outranks_one_in_a_single_leg():
    fused = fuse([1, 2, 3], [3, 4])
    assert fused[0].chunk_id == 3
    assert (fused[0].vector_rank, fused[0].keyword_rank) == (3, 1)


def test_ties_go_to_the_lower_chunk_id():
    # 7 is first in the vector leg, 5 first in the keyword leg: equal scores.
    assert [f.chunk_id for f in fuse([7], [5])] == [5, 7]


def test_one_empty_leg_leaves_the_other_ranking_intact():
    assert [f.chunk_id for f in fuse([9, 8, 7], [])] == [9, 8, 7]
    assert [f.chunk_id for f in fuse([], [4, 2])] == [4, 2]
    assert fuse([], []) == []


def test_fusion_keeps_only_the_top():
    assert len(fuse(list(range(100)), list(range(50, 150)), top=8)) == 8


# --- the one filter -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "f",
    [PeerFilter(), PeerFilter(company="DBS"), PeerFilter(sector="reit", report_year=2026)],
)
def test_every_query_applies_corpus_model_and_version(f):
    for sql in (_count_sql(f), _vector_sql(f), _keyword_sql(f)):
        for clause in (
            "c.corpus = 'peer'",
            "c.embedding_model = %(embedding_model)s",
            "c.chunker_version = %(chunker_version)s",
        ):
            assert clause in sql
        assert ("c.company = %(company)s" in sql) is (f.company is not None)
        assert ("c.sector = %(sector)s" in sql) is (f.sector is not None)
        assert ("c.report_year = %(report_year)s" in sql) is (f.report_year is not None)


def test_the_keyword_leg_ors_the_question_words():
    assert "replace(plainto_tsquery('english', %(question)s)::text, ' & ', ' | ')" in (
        _keyword_sql(PeerFilter())
    )


# --- refusals -------------------------------------------------------------------------------


def test_nothing_ingested_is_reported_before_anything_is_embedded():
    def embed(_):
        raise AssertionError("must not embed")

    result = search_peer_disclosures("q", embed=embed, count=lambda f: 0)
    assert result.status == "no_results"
    assert result.data["refusal_level"] == "not_ingested"
    assert "Peer corpus" in result.message


def test_a_filter_that_matches_nothing_is_reported_before_anything_is_embedded():
    def embed(_):
        raise AssertionError("must not embed")

    result = search_peer_disclosures(
        "q",
        company="Mapletree Pan Asia Commercial Trust",
        report_year=2025,
        embed=embed,
        count=lambda f: 0 if not f.is_empty() else 10,
    )
    assert result.data["refusal_level"] == "no_match_for_filter"


def test_mpact_2025_names_the_year_it_has_and_the_convention():
    message = no_match_message(
        PeerFilter(company="Mapletree Pan Asia Commercial Trust", report_year=2025)
    )
    assert "report_year 2026" in message
    assert "1 Apr 2025 to 31 Mar 2026" in message
    assert "a financial year ending 31 March" in message
    assert "calendar year in which a report's period ENDS" in message


def test_cict_names_its_only_year():
    message = no_match_message(
        PeerFilter(company="CapitaLand Integrated Commercial Trust", report_year=2025)
    )
    assert "report_year 2024" in message
    assert "financial year ending" not in message.split("instead:")[1]  # calendar year


def test_an_unknown_company_is_an_error_not_a_search():
    assert search_peer_disclosures("q", company="Acme").status == "error"


def test_an_empty_question_is_an_error():
    assert search_peer_disclosures("  ").status == "error"


def test_a_no_from_the_judge_is_a_generation_refusal_that_still_shows_the_passages():
    result = _resolve("q", PeerFilter(), passages(), judged(False))
    assert result.status == "no_results"
    assert result.data["refusal_level"] == "generation"
    assert result.data["passages"][0]["citation"].endswith("p. 79 (PDF p. 79)")


def test_a_fabricated_quote_is_dropped():
    result = _resolve("q", PeerFilter(), passages(), judged(True, "DBS reports no Scope 3 at all"))
    assert result.data["refusal_level"] == "generation"
    assert result.data["quotes_dropped"] == 1


TABLE_ROW = "Total scope 1 tCO2e 9,870 9,120 Kitchen gas 611 580 Delivery vans 47 52"


def test_a_table_like_quote_is_withheld_and_cited_by_page():
    text = f"Scope 1 (tCO2e). {TABLE_ROW}. Refrigerants follow."
    result = _resolve("q", PeerFilter(), passages(row(text=text)), judged(True, TABLE_ROW))
    (quote,) = result.data["quotes"]
    assert quote["withheld"] is True and quote["quote"] is None
    assert quote["note"] == "see the table on p. 79"
    assert all(TABLE_ROW not in str(q) for q in result.data["quotes"])
    assert result.data["quotes_withheld"] == 1


def test_a_withheld_quote_without_a_printed_page_names_the_pdf_page():
    text = f"Scope 1 (tCO2e). {TABLE_ROW}. Refrigerants follow."
    rows = passages(row(text=text, printed_page=None, pdf_page=12))
    (quote,) = _resolve("q", PeerFilter(), rows, judged(True, TABLE_ROW)).data["quotes"]
    assert quote["note"] == "see the table on PDF p. 12"


def test_a_quote_with_a_fused_footnote_marker_is_withheld():
    # One figure in five tokens: the count and share rules alone would return it.
    result = _resolve("q", PeerFilter(), passages(), judged(True, FIGURE_QUOTE))
    (quote,) = result.data["quotes"]
    assert quote["withheld"] is True and quote["quote"] is None


def test_a_prose_quote_is_returned_whole():
    result = _resolve("q", PeerFilter(), passages(), judged(True, PROSE_QUOTE))
    assert result.data["quotes"][0] == {
        "chunk_id": 1,
        "citation": result.data["passages"][0]["citation"],
        "company": "DBS",
        "quote": PROSE_QUOTE,
        "withheld": False,
    }


@pytest.mark.parametrize(
    ("quote", "table_like"),
    [
        # Count: 3 figures in 20 tokens (15%) is prose; 4 in 20 (20%) is a table.
        ("a b c d e f g h i j k l m n o p q 10 20 30", False),
        ("a b c d e f g h i j k l m n o p 10 20 30 40", True),
        # Share: 2 of 8 tokens (25%) is prose; 2 of 7 (28.6%) is a table.
        ("a b c d e f 10 20", False),
        ("a b c d e 10 20", True),
        # Identifiers and labels are not figures.
        ("Scope 1 Scope 2 Scope 3 R-32 CO2e PM2.5 R-410a gases", False),
    ],
)
def test_the_table_rule_at_its_boundaries(quote, table_like):
    assert is_table_like(quote) is table_like


@pytest.mark.parametrize("answered", [True, False])
def test_every_result_carries_the_disclosure(answered):
    result = _resolve("q", PeerFilter(), passages(), judged(answered, PROSE_QUOTE))
    assert DISCLOSURE in result.message
    assert result.data["disclosure"] == DISCLOSURE


def test_the_disclosure_names_the_non_calendar_reporters():
    for name in (
        "Mapletree Pan Asia Commercial Trust",
        "Mapletree Logistics Trust",
        "Frasers Logistics & Commercial Trust",
    ):
        assert name in DISCLOSURE
    assert "DBS" not in DISCLOSURE


def test_a_passage_carries_its_period_from_the_registry():
    (p,) = passages(row(source_id="peer_mpact_2026", printed_page=None))
    assert (p.period_start.isoformat(), p.period_end.isoformat()) == ("2025-04-01", "2026-03-31")
    assert p.citation.endswith("PDF p. 79 (no printed page number)")


# --- the audit: no figure from a peer passage -----------------------------------------------


def peer_payload():
    result = _resolve("q", PeerFilter(), passages(), judged(True, PROSE_QUOTE, FIGURE_QUOTE))
    return {"tool": "search_peer_disclosures", **result.model_dump()}


def test_a_number_found_only_in_a_peer_passage_is_flagged():
    assert audit_figures("DBS reported 2,5113 tCO2e of Scope 1.", [peer_payload()]) == ["2,5113"]


def test_the_page_and_report_year_of_a_peer_passage_are_not_flagged():
    assert audit_figures("See DBS's 2025 report, p. 79.", [peer_payload()]) == []


def test_a_peer_number_in_the_agents_prose_is_flagged_but_not_inside_a_verified_quote():
    sentence = "Fuel use is converted with 2024 national factors over a 100 year horizon"
    payload = {
        "tool": "search_peer_disclosures",
        **_resolve(
            "q", PeerFilter(), passages(row(text=f"Note. {sentence}.")), judged(True, sentence)
        ).model_dump(),
    }
    assert audit_figures("DBS uses a 100 year horizon.", [payload]) == ["100"]
    assert audit_figures(f'DBS says "{sentence}" (p. 79).', [payload]) == []


def test_a_table_row_quoted_from_the_passage_text_gets_no_exemption():
    text = f"Scope 1 (tCO2e). {TABLE_ROW}. Refrigerants follow."
    result = _resolve("q", PeerFilter(), passages(row(text=text)), judged(True, TABLE_ROW))
    payload = {"tool": "search_peer_disclosures", **result.model_dump()}
    flagged = audit_figures(f'The table reads "{TABLE_ROW}".', [payload])
    assert flagged == ["9,870", "9,120", "611", "580", "47", "52"]


def test_a_fused_marker_quote_quoted_verbatim_gets_no_exemption():
    # Table-like by the malformed-group and impossible-scope checks alone: one figure in five
    # tokens passes the count and share rules, so this pins the path those rules miss.
    text = f"Scope 1 (tCO2e). {FIGURE_QUOTE} Fleet follows."
    result = _resolve("q", PeerFilter(), passages(row(text=text)), judged(True, FIGURE_QUOTE))
    payload = {"tool": "search_peer_disclosures", **result.model_dump()}
    reply = f'The report says "{FIGURE_QUOTE}" (p. 79).'
    assert audit_quotes(reply, [payload]) == []  # verbatim in the passage text
    assert audit_figures(reply, [payload]) == ["2,5113"]


def test_a_short_quoted_number_gets_no_exemption():
    payload = peer_payload()
    assert audit_figures('Scope 1 was "2,5113" (p. 79).', [payload]) == ["2,5113"]


def test_numbers_in_a_methodology_passage_still_count():
    methodology = {
        "tool": "search_methodology",
        "status": "ok",
        "message": "m",
        "data": {"passages": [{"text": "the 15 categories of scope 3"}]},
    }
    assert audit_figures("There are 15 categories.", [methodology]) == []


# --- the agent's view -----------------------------------------------------------------------


def test_the_company_choices_are_exactly_the_ingested_companies():
    assert set(PEER_COMPANIES) == {p.company for p in load_peer_sources().sources}
    schema = schema_for(SearchPeerDisclosuresArgs)
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"question", "company", "sector", "report_year"}
    with pytest.raises(ValueError):
        SearchPeerDisclosuresArgs(question="q", company="Acme")


def test_the_toolkit_passes_the_filter_through_and_creates_no_handle(session):
    seen = []

    def search(question, **kwargs):
        seen.append((question, kwargs))
        return ok({"quotes": []}, "fine")

    toolkit = Toolkit(session, search_peers=search)
    result = toolkit.dispatch(
        "search_peer_disclosures",
        '{"question": "boundary?", "company": "DBS", "sector": null, "report_year": 2025}',
    )
    assert result.status == "ok"
    assert seen == [("boundary?", {"company": "DBS", "sector": None, "report_year": 2025})]
    assert session.factors == {} and session.activities == {}


def test_the_methodology_judge_instruction_is_unchanged():
    # sha256 of Part A's instruction, formatted with question "Q" and passages "P", taken
    # from the commit before the {corpus} parameter was added. The eval was scored with it.
    text = JUDGE_INSTRUCTION.format(corpus=METHODOLOGY_CORPUS, question="Q", passages="P")
    assert hashlib.sha256(text.encode()).hexdigest() == (
        "59cf3ca77567729b4c2470e32c49a6c768677e387da02a1ea9d9bf3ed4e1dc86"
    )


# --- the SQL, executed (needs a database; skipped in CI) ------------------------------------


@pytest.mark.skipif(not get_settings().database_configured, reason="DATABASE_URL not configured")
def test_the_keyword_and_vector_queries_execute_and_find_a_row():
    """Runs every peer query against Postgres inside a transaction that is rolled back.

    Added after the keyword leg's first live run failed with a syntax error that a test of
    the SQL's text could not see.
    """
    from carbon_r.db import connect
    from carbon_r.rag.chunking import CHUNKER_VERSION
    from carbon_r.rag.embed import to_pgvector
    from carbon_r.tools import peer

    f = PeerFilter(company="ZZ test company", report_year=1999)
    params = peer._params(f) | {
        "chunker_version": CHUNKER_VERSION,
        "embedding_model": "zz-test-model",
        "query": to_pgvector([0.0] * 1535 + [1.0]),
        "question": "How are refrigerant leaks from chillers estimated?",
        "vector_depth": 5,
        "keyword_depth": 5,
    }
    with connect() as conn:
        try:
            conn.execute(
                "insert into sources (id, title, publisher, year, url, retrieved_at, sha256, "
                "kind, verification_status) values ('zz_test_source', 't', 'p', 1999, 'u', "
                "'2026-10-28', repeat('0', 64), 'peer_report', 'secondary only')"
            )
            (chunk_id,) = conn.execute(
                "insert into document_chunks (source_id, corpus, chunker_version, "
                "embedding_model, pdf_page, chunk_index, text, content_sha256, embedding, "
                "company, sector, report_year) values ('zz_test_source', 'peer', "
                "%(chunker_version)s, %(embedding_model)s, 1, 0, 'Refrigerant leakage from "
                "chillers is estimated from top-up records.', repeat('0', 64), "
                "%(query)s::vector, 'ZZ test company', 'bank', 1999) returning id",
                params,
            ).fetchone()
            keyword = [r[0] for r in conn.execute(peer._keyword_sql(f), params).fetchall()]
            vector = [r[0] for r in conn.execute(peer._vector_sql(f), params).fetchall()]
            (count,) = conn.execute(peer._count_sql(f), params).fetchone()
            stop_words = conn.execute(
                peer._keyword_sql(f), params | {"question": "the of and"}
            ).fetchall()
        finally:
            conn.rollback()
    assert keyword == [chunk_id] and vector == [chunk_id] and count == 1
    assert stop_words == []
