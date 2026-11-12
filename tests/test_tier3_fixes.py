"""Tier 3 Block 1 fix pass (follow-ups 6 and 7), from agent cases v3.

6. The agent added a year the question never named (FLCT FY2025 looked up as report_year
   2024, then 2026) and got no_results. A year the user did not name is now dropped in code
   before the lookup, so every year held comes back, each figure with its own period.
7. The "verified by hand" check read page numbers, dates and cell references as figures and
   flagged two sentences that stated none. Those are now set aside before the check.
"""

import pytest

from carbon_r.agent.audit import unsupported_hand_verification
from carbon_r.agent.tools import Toolkit
from carbon_r.agent.toolspec import LookupPeerDisclosureArgs, SearchPeerDisclosuresArgs
from carbon_r.tools.base import ok
from carbon_r.tools.peer_figures import year_named_in

# --- 6: a year nobody asked for ---------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "year", "kept"),
    [
        ("What are FLCT's total Scope 3 emissions?", 2024, False),
        ("What are FLCT's total Scope 3 emissions?", 2026, False),
        ("What were FLCT's Scope 2 emissions in FY2025?", 2025, True),
        ("What did Example Trust report for FY2025/26?", 2026, True),
        ("What did Example Trust report for FY25/26?", 2026, True),
        ("What did Example Trust report in 2025?", 2026, True),  # a year can end in the next
        ("What did DBS report in 2025?", 2025, True),
        ("What did DBS report in 2025?", 2023, False),
        ("Scope 1 for 2024 and 2025, please", 2024, True),
    ],
)
def test_a_report_year_counts_only_when_the_question_names_it(question, year, kept):
    assert year_named_in(question, year) is kept


def _toolkit(session, calls):
    def fake_lookup_peers(company, scope, **kw):
        calls.append(kw["report_year"])
        return ok({"figures": []}, "0 verified figures.")

    def fake_search_peers(question, **kw):
        calls.append(kw["report_year"])
        return ok({"passages": []}, "0 passages.")

    return Toolkit(session, lookup_peers=fake_lookup_peers, search_peers=fake_search_peers)


def test_the_lookup_drops_an_invented_year_and_says_so(session):
    calls: list = []
    session.user_messages.append("What are FLCT's total Scope 3 emissions?")
    tools = _toolkit(session, calls)
    args = LookupPeerDisclosureArgs(
        company="Frasers Logistics & Commercial Trust", scope="3", report_year=2024
    )
    result = tools.lookup_peer_disclosure(args)
    assert calls == [None]
    assert "report_year 2024 was not applied" in result.message


def test_the_lookup_keeps_a_year_the_question_names(session):
    calls: list = []
    session.user_messages.append("What were FLCT's Scope 2 emissions in FY2025?")
    tools = _toolkit(session, calls)
    args = LookupPeerDisclosureArgs(
        company="Frasers Logistics & Commercial Trust", scope="2", report_year=2025
    )
    result = tools.lookup_peer_disclosure(args)
    assert calls == [2025] and "not applied" not in result.message


def test_the_peer_search_drops_an_invented_year_too(session):
    calls: list = []
    session.user_messages.append("How does UOB work out its Scope 2?")
    tools = _toolkit(session, calls)
    tools.search_peer_disclosures(
        SearchPeerDisclosuresArgs(question="Scope 2 method", company="UOB", report_year=2024)
    )
    assert calls == [None]


# --- 7: pages, dates and cells are not figures ------------------------------------------

# The two sentences v3 flagged (runs peer_agent_20261111T161732Z), as the replies wrote them.
FLAGGED_IN_V3 = [
    "Source: **ST Engineering Sustainability Report (1 Jan 2025 to 31 Dec 2025), p. 37 "
    "(PDF p. 37)**; verified by hand against that page.",
    "Source: Frasers Logistics & Commercial Trust Sustainability Databook (1 Oct 2024 to 30 Sep "
    "2025), sheet “Climate Action - Scope 3”, cells L18, L30, L42 and L54; verified by hand "
    "against the workbook on 2026-11-11.",
]


def _lookup(*figures: dict) -> list[dict]:
    return [{"tool": "lookup_peer_disclosure", "status": "ok", "data": {"figures": list(figures)}}]


# What each turn's lookup returned: the figure's page, or its cells (values invented).
_RETURNED = [
    _lookup({"figure_printed": "6.6", "pdf_page": 37, "printed_page": 37}),
    _lookup(*({"figure_printed": "1.2", "pdf_page": None, "printed_page": None,
               "cell_ref": cell} for cell in ("L18", "L30", "L42", "L54"))),
]  # fmt: skip
_LOOKUP_OK = _RETURNED[0]


@pytest.mark.parametrize(("sentence", "payloads"), list(zip(FLAGGED_IN_V3, _RETURNED, strict=True)))
def test_a_citation_of_the_looked_up_pages_and_cells_is_not_flagged(sentence, payloads):
    assert unsupported_hand_verification(sentence, payloads) == []


def test_a_citation_of_pages_or_cells_no_lookup_returned_is_still_flagged():
    other_page = FLAGGED_IN_V3[0].replace("p. 37 (PDF p. 37)", "p. 88 (PDF p. 89)")
    assert unsupported_hand_verification(other_page, _RETURNED[0]) == [other_page]
    other_cell = FLAGGED_IN_V3[1].replace("L54", "L99")
    assert unsupported_hand_verification(other_cell, _RETURNED[1]) == [other_cell]


def test_a_figure_that_was_not_looked_up_is_still_flagged():
    sentence = "Scope 1 was 3,456 tCO2e (p. 37), verified by hand."
    assert unsupported_hand_verification(sentence, _LOOKUP_OK) == [sentence]
    assert unsupported_hand_verification("Scope 1 was 6.6 kt, verified by hand.", _LOOKUP_OK) == []


def test_without_a_lookup_this_turn_a_citation_is_still_flagged():
    assert unsupported_hand_verification(FLAGGED_IN_V3[0], []) == [FLAGGED_IN_V3[0].strip()]
