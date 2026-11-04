"""Eval 5b's scoring, offline: shared page-hit metrics, the peer retrieval rows, and the agent
checks. Invented passages and replies; no database, model or network."""

import json

from carbon_r.models.peer_agent_case import AgentCase, score_reply
from carbon_r.rag.questions import Question
from carbon_r.rag.scoring import Cache, first_hit_rank, hit_metrics
from carbon_r.tools.methodology import AnswerabilityJudgement, JudgedCitation
from eval_peer_retrieval import score_question, summarise

# --- shared metrics ------------------------------------------------------------------------


def test_first_hit_rank_is_a_page_match():
    ranked = [("a", 1), ("a", 7), ("b", 3)]
    assert first_hit_rank(ranked, {("a", 7), ("b", 3)}) == 2
    assert first_hit_rank(ranked, {("a", 2)}) is None


def test_hit_metrics_count_and_mrr():
    assert hit_metrics([1, 2, 6, None]) == {"hit_at_1": 1, "hit_at_5": 2, "mrr": 0.417}
    assert hit_metrics([]) == {"hit_at_1": 0, "hit_at_5": 0, "mrr": None}


# --- peer retrieval rows -------------------------------------------------------------------

ANSWER = "The Group applies the operational control approach to determine its boundary."
OTHER = "The Group tracks all relevant categories of its value chain emissions each year."


def _row(chunk_id: int, page: int, text: str) -> dict:
    return {
        "chunk_id": chunk_id, "source_id": "peer_dbs_2025", "title": "DBS SR", "publisher": "DBS",
        "year": 2025, "url": "u", "pdf_page": page, "printed_page": page, "section": None,
        "text": text, "company": "DBS", "sector": "bank", "report_year": 2025,
        "similarity": 0.5, "vector_rank": 1, "keyword_rank": 1, "rrf_score": 0.03,
    }  # fmt: skip


def _question(answerable: bool, **kw) -> Question:
    values = {"id": "q", "question": "What boundary approach does DBS use?", "phrasing": "user",
              "split": "dev", "company": "DBS", "answerable": answerable}  # fmt: skip
    if answerable:
        page = {"source_id": "peer_dbs_2025", "pdf_page": 79}
        values |= {"expected": [page], "anchor": page | {"sha256": "0" * 64}}
    else:
        values |= {"expected": [], "anchor": None, "unanswerable_kind": "near_topic"}
    return Question(**(values | kw))


def _score(q: Question, judgement: AnswerabilityJudgement, tmp_path) -> dict:
    rows = [_row(1, 12, OTHER), _row(2, 79, ANSWER)]
    return score_question(
        q,
        embed=lambda _: [[0.0]],
        judge=lambda *_: judgement,
        cache=Cache(tmp_path, enabled=False),
        model="m",
        judge_model="j",
        use_judge=True,
        fetch=lambda *_: rows,
    )


def _says(chunk_id: int, quote: str) -> AnswerabilityJudgement:
    return AnswerabilityJudgement(
        answered=True, citations=[JudgedCitation(chunk_id=chunk_id, quote=quote)], reason="r"
    )


NO = AnswerabilityJudgement(answered=False, citations=[], reason="r")


def test_an_answer_quoting_an_expected_page_is_a_cited_hit(tmp_path):
    row = _score(_question(True), _says(2, ANSWER), tmp_path)
    assert row["first_hit_rank"] == 2 and row["status"] == "ok"
    assert row["cited_expected_page"] and not row["judge_false_positive"]


def test_an_answer_quoting_only_other_pages_is_a_judge_false_positive(tmp_path):
    # The Keppel shape: real words, verbatim, from a page that does not answer.
    row = _score(_question(True), _says(1, OTHER), tmp_path)
    assert row["status"] == "ok" and row["judge_false_positive"]


def test_an_unanswerable_question_answered_is_a_false_positive_and_a_refusal_is_not(tmp_path):
    assert _score(_question(False), _says(1, OTHER), tmp_path)["judge_false_positive"]
    refused = _score(_question(False), NO, tmp_path)
    assert refused["status"] == "no_results" and refused["refusal_level"] == "generation"
    assert not refused["judge_false_positive"]


def test_the_row_holds_no_report_text(tmp_path):
    row = _score(_question(True), _says(2, ANSWER), tmp_path)
    assert ANSWER not in json.dumps(row) and OTHER not in json.dumps(row)


def test_summarise_counts_refusals_and_false_positives_apart(tmp_path):
    rows = [
        _score(_question(True, id="a"), _says(2, ANSWER), tmp_path),
        _score(_question(True, id="b"), NO, tmp_path),
        _score(_question(False, id="c"), _says(1, OTHER), tmp_path),
        _score(_question(False, id="d"), NO, tmp_path),
    ]
    s = summarise(rows)
    assert (s["answerable"], s["unanswerable"]) == (2, 2)
    assert (s["refused_answerable"], s["refused_unanswerable"]) == (1, 1)
    assert s["refusal_levels_unanswerable"] == {"generation": 1}
    assert (s["judge_false_positive_answerable"], s["judge_false_positive_unanswerable"]) == (0, 1)
    assert s["answered_citing_expected_page"] == 1


# --- agent checks --------------------------------------------------------------------------


def _case(*checks: dict) -> AgentCase:
    return AgentCase(
        id="c", question="A question?", measures="m", pass_rule="p", checks=list(checks)
    )


def _response(reply: str, calls=(), figures=(), quotes=()) -> dict:
    return {"reply": reply, "tool_calls": list(calls), "unverified_figures": list(figures),
            "unverified_quotes": list(quotes)}  # fmt: skip


def _check(kind: str, reply: str) -> bool:
    return score_reply(_case({"kind": kind}), _response(reply))[kind]


def test_a_ranking_fails_and_a_negated_restatement_passes():
    assert not _check("no_ranking", "DBS has the lower Scope 2.")
    assert _check("no_ranking", "I can’t say which bank has the lower Scope 2.")
    assert _check("no_ranking", "Carbon R cannot rank them as lower or higher.")


def test_an_offer_to_compare_fails():
    assert not _check("no_offer_to_compare", "If you want, I can compare their Scope 2 figures.")
    assert _check("no_offer_to_compare", "Each figure is stated as printed.")


def test_method_period_and_disclosure_are_read_as_fixed():
    assert _check("method_named", "Scope 2 (market-based) is printed on p.79.")
    assert _check("period_named", "for 1 Jan 2025 to 31 Dec 2025")
    assert not _check("period_named", "for 2025")
    assert _check("disclosure_stated", "These are self-reported and not assured.")
    assert not _check("disclosure_stated", "These are self-reported.")


def test_no_figure_ignores_years_and_scope_labels():
    assert _check("no_figure", "Carbon R holds no verified Scope 1 figure for Keppel for 2025.")
    assert not _check("no_figure", "Keppel's Scope 1 was 21,312 tCO2e.")


def test_quotes_exclude_reads_only_quoted_spans():
    case = _case({"kind": "quotes_exclude", "terms": ["Scope 3"]})
    ok = score_reply(case, _response('DBS says "Scope 2 is location-based" (p.79). Scope 3 aside.'))
    bad = score_reply(case, _response("DBS says “Scope 3 excludes travel” (p.80)."))
    assert ok == {"quotes_exclude:Scope 3": True} and bad == {"quotes_exclude:Scope 3": False}


def test_tool_called_matches_tool_company_and_status():
    case = _case({"kind": "tool_called", "tool": "lookup_peer_disclosure", "company": "DBS",
                  "status": "ok"})  # fmt: skip
    label = "tool_called:lookup_peer_disclosure:DBS:ok"

    def call(company: str, status: str) -> dict:
        return {"tool": "lookup_peer_disclosure", "arguments": json.dumps({"company": company}),
                "status": status, "message": ""}  # fmt: skip

    assert score_reply(case, _response("r", [call("DBS", "ok")]))[label]
    assert not score_reply(case, _response("r", [call("OCBC", "ok")]))[label]
    assert not score_reply(case, _response("r", [call("DBS", "no_results")]))[label]


def test_audit_lists_must_be_empty():
    case = _case({"kind": "no_unverified_figures"}, {"kind": "no_unverified_quotes"})
    assert score_reply(case, _response("r", figures=["5"])) == {
        "no_unverified_figures": False,
        "no_unverified_quotes": True,
    }


# --- the judgement cache follows the judge's instruction and schema ------------------------


def _judged_twice(tmp_path, change) -> int:
    """Score one question twice through one cache, applying `change` in between; return how
    many times the judge was actually called."""
    calls = []

    def judge(*_):
        calls.append(1)
        return _says(2, ANSWER)

    def once():
        score_question(
            _question(True),
            embed=lambda _: [[0.0]],
            judge=judge,
            cache=Cache(tmp_path, enabled=True),
            model="m",
            judge_model="j",
            use_judge=True,
            fetch=lambda *_: [_row(1, 12, OTHER), _row(2, 79, ANSWER)],
        )

    once()
    change()
    once()
    return len(calls)


def test_an_unchanged_judge_reuses_its_cached_judgement(tmp_path):
    assert _judged_twice(tmp_path, lambda: None) == 1


def test_a_changed_judge_instruction_misses_the_cache(tmp_path, monkeypatch):
    from carbon_r.tools import methodology

    changed = methodology.JUDGE_INSTRUCTION + "\n- One more rule."
    assert (
        _judged_twice(
            tmp_path, lambda: monkeypatch.setattr(methodology, "JUDGE_INSTRUCTION", changed)
        )
        == 2
    )


def test_a_changed_field_order_changes_the_fingerprint(monkeypatch):
    from carbon_r.tools import methodology

    before = methodology.judge_fingerprint()
    schema = methodology.AnswerabilityJudgement.model_json_schema()
    reordered = schema | {"properties": dict(reversed(list(schema["properties"].items())))}
    monkeypatch.setattr(
        methodology.AnswerabilityJudgement, "model_json_schema", classmethod(lambda cls: reordered)
    )
    assert methodology.judge_fingerprint() != before


def test_a_refusal_to_rank_is_not_an_offer():
    # The three cross-sector sentences the baseline run (peer_agent_20261104T100139Z) scored
    # as offers: each refuses. Negation now exempts a sentence, as in the ranking check.
    for sentence in (
        "These are self-reported under different boundaries and are not comparable "
        "like-for-like, so I can’t rank them as “bigger” from the reports alone.",
        "I can state the figures, but I can’t rank them as “bigger” on a strict accounting basis.",
        "I can’t rank them directly, but the printed figures are as above.",
    ):
        assert _check("no_offer_to_compare", sentence), sentence


def test_the_baseline_offers_still_fail():
    for sentence in (
        "If you want, I can also give the same comparison for a single method only, e.g.",
        "If you want, I can also compare the **market-based** Scope 2 figures:",
    ):
        assert not _check("no_offer_to_compare", sentence), sentence


# --- the scorer reads the model's words; the code-attached caveat counts only as disclosure --


def test_the_attached_caveat_satisfies_only_the_disclosure_check():
    from carbon_r.tools.peer import PEER_CAVEAT

    case = _case({"kind": "names_difference"}, {"kind": "no_figure"},
                 {"kind": "disclosure_stated"})  # fmt: skip
    reply = f"Carbon R holds no verified figure for that company.\n\n{PEER_CAVEAT}"
    assert score_reply(case, _response(reply)) == {
        "names_difference": False,  # the caveat's "boundary" is the code's word, not the model's
        "no_figure": True,  # nor are its dates ("1 Apr to 31 Mar")
        "disclosure_stated": True,
    }


def test_no_figure_ignores_dates_and_financial_year_labels():
    assert _check("no_figure", "MLT's FY2025/26 runs 1 April 2025 to 31 March 2026.")
    assert _check("no_figure", "for 1 Apr 2025 to 31 Mar 2026 (report_year 2026)")
    assert not _check("no_figure", "MLT's FY2025/26 Scope 1 was 4,321 tCO2e.")


# --- each company's unit as printed (v2 bank case) ------------------------------------------


def _units(reply: str) -> bool:
    case = _case({"kind": "units_stated", "units": ["thousand tCO2e", "tCO2e"]})
    return score_reply(case, _response(reply))["units_stated:thousand tCO2e,tCO2e"]


def test_both_units_as_printed_pass_with_either_subscript():
    assert _units("UOB: 6.6 thousand tCO2e (p.135). DBS: 1,234 tCO₂e (p.79).")
    assert _units("UOB: 6.6 thousand tCO₂e. DBS: 1,234 TCO2E.")


def test_one_unit_for_both_companies_fails():
    assert not _units("UOB: 6.6 thousand tCO2e. DBS: 1,234 thousand tCO2e.")
    assert not _units("UOB: 6,600 tCO2e. DBS: 1,234 tCO2e.")  # UOB's unit converted


def test_a_respelled_unit_is_not_the_unit_as_printed():
    assert not _units("UOB: 6.6 '000 tCO2e. DBS: 1,234 tCO2e.")


def test_no_verified_figure_accepts_words_between():
    assert _check("states_no_verified_figure", "Carbon R holds no verified figure for MLT.")
    mlt = "Carbon R holds no verified Scope 1 figure for Mapletree Logistics Trust."
    assert _check("states_no_verified_figure", mlt)  # v2 run 3's wording
    assert _check("states_no_verified_figure", "There are no verified market-based figures.")
    assert not _check("states_no_verified_figure", "MLT has no verified data on file.")
