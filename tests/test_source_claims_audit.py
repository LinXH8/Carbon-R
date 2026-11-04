"""The two claims a reply makes about its sources, and the warnings shown with a flagged
reply (Part B 2b follow-ups). Each sentence under test is from, or shaped like, a real
eval 5b v2 reply."""

from carbon_r.agent.audit import (
    audit_warnings,
    no_results_unstated,
    unsupported_hand_verification,
)


def _lookup(*printed: str, status: str = "ok") -> dict:
    figures = [{"figure_printed": p} for p in printed]
    return {"tool": "lookup_peer_disclosure", "status": status, "data": {"figures": figures}}


def _search(tool: str, status: str) -> dict:
    return {"tool": tool, "status": status, "message": "", "data": {}}


# --- "verified by hand" ---------------------------------------------------------------------

# The figures and wording here are invented: real peer figures are committed in one file
# only (CLAUDE.md rule 4). The shapes are the v2 replies'.
BANK = (
    "Bank A’s Scope 1 total is 7.7 thousand tCO2e, row label “Scope 1 > Total,” for 1 Jan 2025 "
    "to 31 Dec 2025, on p. 134 (PDF p. 135), verified by hand against that page."
)
PASSAGE = (
    "It also says “its market-based figure is what remains once certificates are applied.” "
    "Bank A Sustainability Report, p. 188 (PDF p. 189), verified by hand against that page."
)


def test_a_claim_beside_a_looked_up_figure_passes():
    # "p. 134" must not split the sentence away from its figure.
    assert unsupported_hand_verification(BANK, [_lookup("7.7")]) == []


def test_a_claim_with_no_lookup_this_turn_is_flagged():
    assert unsupported_hand_verification(BANK, []) == [BANK]


def test_a_claim_about_a_passage_is_flagged_even_after_a_lookup():
    # The lookup returned 7.7; this sentence states only page numbers.
    flagged = unsupported_hand_verification(PASSAGE, [_lookup("7.7")])
    assert len(flagged) == 1 and "certificates" in flagged[0]


def test_a_figure_the_lookup_did_not_return_is_flagged():
    reply = "Bank A's location-based Scope 2 is 55.5 thousand tCO2e, verified by hand."
    assert unsupported_hand_verification(reply, [_lookup("7.7")]) != []


def test_a_claim_with_no_number_passes_only_after_a_lookup_call():
    reply = "Both figures above were verified by hand against the cited pages."
    assert unsupported_hand_verification(reply, [_lookup("3,456")]) == []
    assert unsupported_hand_verification(reply, []) == [reply]


def test_explaining_a_lookup_that_found_nothing_is_not_a_claim():
    # Shaped like all 3 v2 MLT replies, which the first version of this check flagged.
    reply = (
        "This does not mean the company did not disclose one: only figures verified by hand "
        "are held, and none has been checked for Mapletree Logistics Trust yet."
    )
    assert unsupported_hand_verification(reply, [_lookup(status="no_results")]) == []
    assert unsupported_hand_verification("Only figures checked by hand are held.", []) == []


def test_thousands_separators_and_other_wordings_are_read():
    reply = "Bank B's total is 3456 tCO₂e (hand-verified)."
    assert unsupported_hand_verification(reply, [_lookup("3,456")]) == []
    assert unsupported_hand_verification("Verified by hand: 9.9 t.", []) != []


def test_a_reply_making_no_claim_is_not_read():
    assert unsupported_hand_verification("Bank A reports 7.7 thousand tCO2e.", []) == []


# --- no_results must be said ----------------------------------------------------------------


def test_an_answer_after_no_results_is_flagged():
    reply = "Bank A reports both location-based and market-based Scope 2."
    payloads = [_search("search_peer_disclosures", "no_results")]
    assert no_results_unstated(reply, payloads) == ["search_peer_disclosures"]


def test_saying_so_passes_in_several_wordings():
    payloads = [_search("search_methodology", "no_results")]
    for reply in (
        "The GHG Protocol documents searched do not answer the question.",
        "That question is not answered by the passages found.",
        "I can’t answer that from the reports.",
        "The passages don't directly answer it.",
        "The question remains unanswered.",
    ):
        assert no_results_unstated(reply, payloads) == [], reply


def test_only_the_last_call_of_each_search_counts():
    retried = [_search("search_peer_disclosures", "no_results"),
               _search("search_peer_disclosures", "ok")]  # fmt: skip
    assert no_results_unstated("An answer.", retried) == []
    # v2 run 2: the peer search ended in no_results, then the methodology search was ok.
    mixed = [_search("search_peer_disclosures", "no_results"),
             _search("search_methodology", "ok")]  # fmt: skip
    assert no_results_unstated("An answer.", mixed) == ["search_peer_disclosures"]


def test_a_figure_lookup_with_no_results_is_not_a_search():
    assert no_results_unstated("No verified figure.", [_lookup(status="no_results")]) == []


# --- the warnings ---------------------------------------------------------------------------


def test_no_findings_no_warnings():
    assert audit_warnings([], [], [], []) == []


def test_one_warning_per_kind_in_a_fixed_order():
    warnings = audit_warnings(["6.6"], ["Exclude[s] Scope 2"], ["s"], ["search_peer_disclosures"])
    assert len(warnings) == 4
    assert "6.6" in warnings[0] and "Exclude[s] Scope 2" in warnings[1]
    assert "verified by hand" in warnings[2] and "not answered" in warnings[3]


def test_the_response_carries_the_findings_and_warnings():
    from carbon_r.agent.loop import AgentRun
    from carbon_r.api.models import ChatResponse

    run = AgentRun(session_id="s", reply="r", unsupported_hand_verification=["x"],
                   no_results_unstated=["search_methodology"], warnings=["w1", "w2"])  # fmt: skip
    body = ChatResponse.from_run(run).model_dump()
    assert body["unsupported_hand_verification"] == ["x"]
    assert body["no_results_unstated"] == ["search_methodology"]
    assert body["warnings"] == ["w1", "w2"]
