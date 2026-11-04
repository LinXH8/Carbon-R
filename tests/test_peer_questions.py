"""Eval 5b's answer keys: the peer retrieval questions and the agent cases.

Shape and balance run anywhere. The anchor test needs the local reports and the git-ignored
anchor file, so it is skipped in CI: it is the check that every expected page was labelled
from the page itself (rule 4), never from search output.
"""

from collections import Counter
from functools import cache

import pytest
from pydantic import ValidationError

from carbon_r.models.peer_agent_case import AgentCaseSet, load_agent_cases
from carbon_r.rag.chunking import extract_pages, normalise, sha256_text
from carbon_r.rag.questions import (
    PEER_LOCAL_ANCHORS_PATH,
    PEER_QUESTIONS_PATH,
    QuestionSet,
    load_local_anchors,
    load_questions,
)
from carbon_r.sources import BY_ID, PEERS

QUESTIONS = load_questions(PEER_QUESTIONS_PATH)
STYLES = ("textbook", "user", "singlish")


def test_the_peer_set_declares_its_corpus_and_loads():
    assert QUESTIONS.corpus == "peer" and len(QUESTIONS.questions) == 30


def test_every_company_has_an_answerable_question():
    asked = {PEERS[q.expected[0].source_id].company for q in QUESTIONS.questions if q.answerable}
    assert asked == {p.company for p in PEERS.values()}


def test_each_style_has_answerable_and_unanswerable_questions():
    answerable = Counter(q.phrasing for q in QUESTIONS.questions if q.answerable)
    unanswerable = Counter(q.phrasing for q in QUESTIONS.questions if not q.answerable)
    assert answerable == {s: 7 for s in STYLES}
    assert unanswerable == {s: 3 for s in STYLES}


def test_the_split_is_stratified_by_style_and_answerability():
    cells: dict[tuple, Counter] = {}
    for q in QUESTIONS.questions:
        cells.setdefault((q.phrasing, q.answerable), Counter())[q.split] += 1
    for cell, splits in cells.items():
        assert abs(splits["dev"] - splits["test"]) <= 1, cell


def test_unanswerable_questions_are_split_evenly_by_kind():
    kinds: dict[str, Counter] = {}
    for q in (q for q in QUESTIONS.questions if not q.answerable):
        kinds.setdefault(q.unanswerable_kind, Counter())[q.split] += 1
    assert set(kinds) == {"off_corpus", "near_topic"}
    for kind, splits in kinds.items():
        assert abs(splits["dev"] - splits["test"]) <= 1, kind


def test_the_keppel_probe_is_asked_verbatim():
    (probe,) = [q for q in QUESTIONS.questions if q.id == "keppel_scope3_targets"]
    assert probe.question == "Does Keppel set targets for its Scope 3 emissions?"


def _set(**question) -> dict:
    base = {"id": "x", "question": "A question?", "answerable": False, "phrasing": "user",
            "split": "dev", "expected": [], "anchor": None,
            "unanswerable_kind": "off_corpus"}  # fmt: skip
    return {"version": 1, "drafted": "d", "review_status": "draft", "questions": [base | question]}


def test_a_set_refuses_a_page_from_the_other_corpus():
    page = {"source_id": "peer_dbs_2025", "pdf_page": 79}
    anchor = page | {"sha256": "0" * 64}
    answerable = {"answerable": True, "expected": [page], "anchor": anchor,
                  "unanswerable_kind": None}  # fmt: skip
    with pytest.raises(ValidationError, match="not in the methodology corpus"):
        QuestionSet.model_validate(_set(**answerable))
    assert QuestionSet.model_validate(_set(**answerable) | {"corpus": "peer"})


def test_a_company_filter_must_be_a_peer_company_and_match_the_pages():
    with pytest.raises(ValidationError, match="not a peer company"):
        QuestionSet.model_validate(_set(company="DBS"))  # a methodology set
    with pytest.raises(ValidationError, match="not a peer company"):
        QuestionSet.model_validate(_set(company="Nobody Ltd") | {"corpus": "peer"})
    page = {"source_id": "peer_dbs_2025", "pdf_page": 79}
    wrong = {"answerable": True, "expected": [page], "anchor": page | {"sha256": "0" * 64},
             "unanswerable_kind": None, "company": "OCBC"}  # fmt: skip
    with pytest.raises(ValidationError, match="filtered to OCBC"):
        QuestionSet.model_validate(_set(**wrong) | {"corpus": "peer"})


@cache
def _pages(source_id: str) -> list[str]:
    return [page.text for page in extract_pages(BY_ID[source_id].path)]


def _reports_present() -> bool:
    return all(BY_ID[s].path.exists() for s in PEERS)


@pytest.mark.skipif(
    not _reports_present() or load_local_anchors(PEER_LOCAL_ANCHORS_PATH) is None,
    reason="needs the local peer reports and data/evals/local/peer_anchors.json",
)
def test_every_peer_anchor_is_on_its_labelled_page_and_matches_its_hash():
    anchors = load_local_anchors(PEER_LOCAL_ANCHORS_PATH)
    for q in (q for q in QUESTIONS.questions if q.answerable):
        text = normalise(anchors[q.id])
        assert sha256_text(text) == q.anchor.sha256, f"{q.id}: local anchor is not the hashed one"
        page = _pages(q.anchor.source_id)[q.anchor.pdf_page - 1]
        assert text in page, f"{q.id}: anchor not on {q.anchor.source_id} PDF p.{q.anchor.pdf_page}"


# --- the agent cases -----------------------------------------------------------------------


def test_the_agent_cases_load_and_run_three_times():
    cases = load_agent_cases()
    assert cases.runs == 3
    assert [c.id for c in cases.cases] == [
        "bank_scope2_ranking",
        "cross_sector_scope1",
        "no_verified_figure_keppel",
        "scope2_answer_scope3_quote",
    ]


def test_an_agent_case_cannot_name_a_check_the_scorer_lacks():
    case = {"id": "x", "question": "A question?", "measures": "m", "pass_rule": "p",
            "checks": [{"kind": "sounds_right"}]}  # fmt: skip
    with pytest.raises(ValidationError):
        AgentCaseSet.model_validate(
            {"version": 1, "drafted": "d", "review_status": "draft", "runs": 3, "cases": [case]}
        )


def test_the_v2_agent_cases_load_with_four_new_cases():
    from carbon_r.config import PROJECT_ROOT

    v1 = load_agent_cases()
    v2 = load_agent_cases(PROJECT_ROOT / "data" / "evals" / "peer_agent_questions_v2.json")
    assert v2.runs == 3 and len(v2.cases) == 4
    assert not {c.question for c in v1.cases} & {c.question for c in v2.cases}
