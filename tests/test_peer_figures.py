"""`lookup_peer_disclosure`: verified rows as printed, honest misses, and the audit's view.

Offline except the last test. Every figure, label and page here is invented for the test,
not taken from any company's report.
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from carbon_r.agent.audit import audit_figures
from carbon_r.agent.prompt import SYSTEM_PROMPT
from carbon_r.agent.tools import Toolkit
from carbon_r.agent.toolspec import (
    PEER_COMPANIES,
    TOOL_NAMES,
    LookupPeerDisclosureArgs,
    schema_for,
)
from carbon_r.config import get_settings
from carbon_r.db import DatabaseNotConfiguredError
from carbon_r.tools.base import ok
from carbon_r.tools.methodology import AnswerabilityJudgement, JudgedCitation
from carbon_r.tools.peer import PEER_CAVEAT, PeerFilter
from carbon_r.tools.peer import _resolve as resolve_search
from carbon_r.tools.peer_figures import (
    _COMPANIES_SQL,
    _HELD_SQL,
    _ROWS_SQL,
    DISCLOSURE,
    PeerQuery,
    _resolve,
    lookup_peer_disclosure,
)


def db_row(**overrides):
    return {
        "source_id": "peer_dbs_2025",
        "title": "Example Bank Sustainability Report 2025",
        "company": "DBS",
        "period_start": date(2025, 1, 1),
        "period_end": date(2025, 12, 31),
        "scope": "2",
        "scope3_category": None,
        "method": "market",
        "figure_printed": "4,321",
        "figure": Decimal("4321"),
        "unit": "tCO2e",
        "pdf_page": 61,
        "printed_page": 59,
        "row_label": "Scope 2 (market-based)",
        "column_header": "2025",
        "notes": None,
        "verification": "confirmed",
        "verified_at": datetime(2026, 10, 29, 9, 30, tzinfo=UTC),
        "drafted_by": "drafter-model",
    } | overrides


def query(**overrides):
    return PeerQuery(**({"company": "DBS", "scope": "2"} | overrides))


def payload(result, tool="lookup_peer_disclosure"):
    return {"tool": tool, **result.model_dump(mode="json")}


# --- ok: each row as printed ----------------------------------------------------------------


def test_every_matching_row_comes_back_as_printed_with_its_citation():
    rows = [
        db_row(),
        db_row(method="location", figure_printed="5,678", figure=Decimal("5678"),
               row_label="Scope 2 (location-based)"),
    ]  # fmt: skip
    result = _resolve(query(), rows, [], [])
    assert result.status == "ok"
    figures = result.data["figures"]
    assert [f["figure_printed"] for f in figures] == ["4,321", "5,678"]
    assert [f["method"] for f in figures] == ["market", "location"]
    assert figures[0]["citation"] == "Example Bank Sustainability Report 2025, p. 59 (PDF p. 61)"
    assert figures[0]["verified_on"] == "2026-10-29"
    assert figures[0]["period"] == "1 Jan 2025 to 31 Dec 2025"
    assert "4,321 tCO2e" in result.message and "5,678 tCO2e" in result.message
    assert "never sum them" in result.message
    assert result.data["disclosure"] == DISCLOSURE


def test_a_row_without_a_printed_page_cites_the_pdf_page():
    result = _resolve(query(), [db_row(printed_page=None, pdf_page=40)], [], [])
    assert result.data["figures"][0]["citation"].endswith(", PDF p. 40")


def test_a_non_calendar_period_is_named():
    row = db_row(period_start=date(2025, 4, 1), period_end=date(2026, 3, 31))
    period = _resolve(query(), [row], [], []).data["figures"][0]["period"]
    assert period == "1 Apr 2025 to 31 Mar 2026, a financial year ending 31 March"


def test_the_disclosure_keeps_the_peer_caveat_and_says_how_the_figure_was_checked():
    assert DISCLOSURE.startswith(PEER_CAVEAT)
    assert "verified by hand against that page" in DISCLOSURE
    assert "text extraction" not in DISCLOSURE  # that sentence is about passages


def test_nothing_is_summed():
    rows = [db_row(figure_printed="1,000", figure=Decimal("1000")),
            db_row(figure_printed="2,000", figure=Decimal("2000"))]  # fmt: skip
    result = _resolve(query(), rows, [], [])
    assert "3,000" not in result.message and "3000" not in str(result.data)


# --- no_results: absence is not evidence ----------------------------------------------------


def test_a_miss_for_a_company_with_rows_lists_what_is_held_and_never_says_not_disclosed():
    held = [
        {"report_year": 2025, "scope": "1", "method": None, "scope3_category": None},
        {"report_year": 2025, "scope": "2", "method": "location", "scope3_category": None},
        {"report_year": 2025, "scope": "3", "method": None, "scope3_category": 6},
    ]
    result = _resolve(query(method="market"), [], held, ["DBS"])
    assert result.status == "no_results"
    assert "holds no verified figure for DBS, Scope 2, market-based" in result.message
    assert "does not mean the company did not disclose one" in result.message
    assert "- report_year 2025: Scope 2, location-based" in result.message
    assert "- report_year 2025: Scope 3 category 6" in result.message
    assert "report_year is the calendar year" not in result.message
    assert "not disclosed" not in result.message


def test_a_miss_on_the_year_explains_the_year_convention():
    held = [{"report_year": 2025, "scope": "2", "method": None, "scope3_category": None}]
    result = _resolve(query(report_year=2024), [], held, ["DBS"])
    assert "report_year is the calendar year in which a report's period ENDS" in result.message


def test_a_company_with_no_verified_rows_is_told_which_companies_have_them():
    result = _resolve(query(company="Keppel"), [], [], ["DBS", "Sembcorp"])
    assert result.status == "no_results"
    assert "holds no verified figure for Keppel at all" in result.message
    assert "Companies with verified figures: DBS, Sembcorp." in result.message
    assert result.data["companies_with_verified_figures"] == ["DBS", "Sembcorp"]


# --- the composed tool: argument errors and the database ------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"scope": "4"},
        {"scope": "2", "method": "residual"},
        {"scope": "3", "scope3_category": 16},
        {"scope": "2", "scope3_category": 6},
    ],
)
def test_bad_selectors_are_errors_not_lookups(kwargs):
    called = []
    result = lookup_peer_disclosure("DBS", fetch=lambda q: called.append(q), **kwargs)
    assert result.status == "error" and called == []


def test_no_database_is_an_error():
    def fetch(q):
        raise DatabaseNotConfiguredError("DATABASE_URL is not set")

    result = lookup_peer_disclosure("DBS", "1", fetch=fetch)
    assert result.status == "error" and "DATABASE_URL" in result.message


def test_the_query_reaches_the_fetcher_unchanged():
    seen = []

    def fetch(q):
        seen.append(q)
        return [db_row(scope="3", method=None, scope3_category=6)], [], []

    result = lookup_peer_disclosure("DBS", "3", scope3_category=6, report_year=2025, fetch=fetch)
    assert result.status == "ok"
    assert seen == [PeerQuery(company="DBS", scope="3", scope3_category=6, report_year=2025)]


# --- the agent's view -----------------------------------------------------------------------


def test_the_schema_is_strict_and_the_company_must_exist():
    schema = schema_for(LookupPeerDisclosureArgs)
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
        "company", "scope", "method", "scope3_category", "report_year",
    }  # fmt: skip
    assert set(schema["properties"]["company"]["enum"]) == set(PEER_COMPANIES)
    with pytest.raises(ValueError):
        LookupPeerDisclosureArgs(company="Acme", scope="1")


def test_every_tool_is_named_in_the_prompt():
    assert "lookup_peer_disclosure" in TOOL_NAMES
    for name in TOOL_NAMES:
        assert name in SYSTEM_PROMPT, name


def test_the_prompt_no_longer_says_it_holds_no_verified_figures():
    assert "does not yet hold verified peer" not in SYSTEM_PROMPT


def test_the_toolkit_passes_the_selectors_through_and_creates_no_handle(session):
    seen = []

    def lookup(company, scope, **kwargs):
        seen.append((company, scope, kwargs))
        return ok({"figures": []}, "fine")

    toolkit = Toolkit(session, lookup_peers=lookup)
    result = toolkit.dispatch(
        "lookup_peer_disclosure",
        '{"company": "DBS", "scope": "2", "method": "market", "scope3_category": null, '
        '"report_year": 2025}',
    )
    assert result.status == "ok"
    assert seen == [
        ("DBS", "2", {"method": "market", "scope3_category": None, "report_year": 2025})
    ]
    assert session.factors == {} and session.activities == {}


# --- the audit: a verified figure counts; the same number from a passage does not -----------


def search_payload_holding(text: str):
    from carbon_r.tools.peer import retrieve

    row = {
        "chunk_id": 1, "source_id": "peer_dbs_2025", "title": "t", "publisher": "p",
        "year": 2025, "url": "u", "pdf_page": 61, "printed_page": 59, "section": None,
        "text": text, "company": "DBS", "sector": "bank", "report_year": 2025,
        "similarity": 0.5, "vector_rank": 1, "keyword_rank": None, "rrf_score": 1 / 61,
    }  # fmt: skip
    passages = retrieve("q", embed=lambda t: [[0.0] * 1536], fetch=lambda *_: [row])
    judgement = AnswerabilityJudgement(
        answered=True, citations=[JudgedCitation(chunk_id=1, quote="x")], reason="r"
    )
    return payload(
        resolve_search("q", PeerFilter(), passages, judgement), "search_peer_disclosures"
    )


def test_a_figure_from_the_lookup_passes_the_audit():
    result = _resolve(query(), [db_row()], [], [])
    reply = "DBS reports 4,321 tCO2e of Scope 2 (market-based), p. 59, verified 2026-10-29."
    assert audit_figures(reply, [payload(result)]) == []


def test_the_same_number_from_a_peer_passage_alone_is_still_flagged():
    passage = search_payload_holding("Scope 2 market-based emissions were 4,321 tCO2e.")
    reply = "DBS reports 4,321 tCO2e of Scope 2 (market-based)."
    assert audit_figures(reply, [passage]) == ["4,321"]
    lookup = payload(_resolve(query(), [db_row()], [], []))
    assert audit_figures(reply, [passage, lookup]) == []


# --- the SQL, executed (needs a database; skipped in CI) ------------------------------------


@pytest.mark.skipif(not get_settings().database_configured, reason="DATABASE_URL not configured")
def test_the_queries_read_only_verified_rows():
    """Runs every lookup query against Postgres inside a transaction that is rolled back."""
    from psycopg.rows import dict_row

    from carbon_r.db import connect

    insert = """
        insert into peer_disclosures (source_id, company, period_start, period_end, scope,
            scope3_category, figure_printed, figure, drafted_figure_printed, unit, method,
            pdf_page, printed_page, row_label, drafted_row_label, column_header, drafted_by,
            drafted_at, draft_request_hash, page_image_sha256, verified_by, verified_at,
            verification)
        values ('zz_test_source', 'ZZ test company', '1999-01-01', '1999-12-31', %(scope)s,
            null, %(fig)s, %(num)s, %(fig)s, 'tCO2e', %(method)s, 3, null, %(label)s,
            %(label)s, '1999', 'zz-model', now(), 'zz', repeat('0', 64), %(by)s, %(at)s,
            %(verification)s)
    """
    verified = {"by": "author", "at": datetime.now(UTC), "verification": "confirmed"}
    draft = {"by": None, "at": None, "verification": None}
    with connect() as conn, conn.cursor(row_factory=dict_row) as cur:
        try:
            cur.execute(
                "insert into sources (id, title, publisher, year, url, retrieved_at, sha256, "
                "kind, verification_status) values ('zz_test_source', 'ZZ report', 'p', 1999, "
                "'u', '2026-10-30', repeat('0', 64), 'peer_report', 'secondary only')"
            )
            cur.execute(insert, {"scope": "2", "fig": "1,111", "num": 1111, "method": "market",
                                 "label": "S2 market"} | verified)  # fmt: skip
            cur.execute(insert, {"scope": "2", "fig": "2,222", "num": 2222, "method": "location",
                                 "label": "S2 location"} | draft)  # fmt: skip
            cur.execute(insert, {"scope": "1", "fig": "3,333", "num": 3333, "method": None,
                                 "label": "S1 rejected"}
                        | verified | {"verification": "rejected"})  # fmt: skip

            def rows(**q):
                params = PeerQuery(company="ZZ test company", **q).model_dump()
                return cur.execute(_ROWS_SQL, params).fetchall()

            scope2 = rows(scope="2")
            location = rows(scope="2", method="location")
            year_hit = rows(scope="2", report_year=1999)
            year_miss = rows(scope="2", report_year=2000)
            scope1 = rows(scope="1")
            held = cur.execute(_HELD_SQL, {"company": "ZZ test company"}).fetchall()
            companies = [r["company"] for r in cur.execute(_COMPANIES_SQL).fetchall()]
        finally:
            conn.rollback()

    assert [r["figure_printed"] for r in scope2] == ["1,111"]  # the draft is not returned
    assert scope2[0]["title"] == "ZZ report"
    assert location == []  # only a draft has that method
    assert len(year_hit) == 1 and year_miss == []
    assert scope1 == []  # rejected rows are out of the view
    assert held == [
        {"report_year": 1999, "scope": "2", "method": "market", "scope3_category": None}
    ]
    assert "ZZ test company" in companies
    result = _resolve(PeerQuery(company="ZZ test company", scope="2"), scope2, [], [])
    assert result.status == "ok" and result.data["figures"][0]["verified_on"]
