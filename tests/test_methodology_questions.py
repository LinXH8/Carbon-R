"""The retrieval eval's answer key: its shape, its balance, and its anchors against the PDFs.

The shape and balance tests run anywhere. The anchor test needs the local PDFs and the
git-ignored anchor file, so it is skipped in CI - it is the check that every label was
taken from the page it names (rule 4), and it is run wherever the corpus exists.
"""

from collections import Counter
from functools import cache

import pytest

from carbon_r.rag.chunking import extract_pages, normalise, sha256_text
from carbon_r.rag.questions import load_local_anchors, load_questions
from carbon_r.sources import BY_ID, METHODOLOGY_IDS

QUESTIONS = load_questions()


def test_the_set_loads_and_ids_are_unique():
    assert QUESTIONS.questions
    assert len({q.id for q in QUESTIONS.questions}) == len(QUESTIONS.questions)


def test_each_phrasing_style_has_enough_answerable_questions_to_report_alone():
    counts = Counter(q.phrasing for q in QUESTIONS.questions if q.answerable)
    assert counts == {"textbook": 12, "user": 12, "singlish": 12}


def test_each_phrasing_style_has_unanswerable_questions():
    counts = Counter(q.phrasing for q in QUESTIONS.questions if not q.answerable)
    assert all(counts[style] >= 3 for style in ("textbook", "user", "singlish"))


def test_every_standard_is_covered_in_every_style():
    cells = Counter((q.phrasing, q.document) for q in QUESTIONS.questions if q.answerable)
    for style in ("textbook", "user", "singlish"):
        for source in METHODOLOGY_IDS:
            assert cells[(style, source)] >= 3, (style, source)


def test_the_split_is_stratified_by_phrasing_and_document():
    cells: dict[tuple, Counter] = {}
    for q in QUESTIONS.questions:
        cells.setdefault((q.phrasing, q.document), Counter())[q.split] += 1
    for cell, splits in cells.items():
        assert abs(splits["dev"] - splits["test"]) <= 1, cell


def test_unanswerable_questions_are_split_evenly_by_kind_within_each_style():
    """Otherwise dev could hold only near-topic refusals, and test only off-corpus ones."""
    cells: dict[tuple, Counter] = {}
    for q in (q for q in QUESTIONS.questions if not q.answerable):
        cells.setdefault((q.phrasing, q.unanswerable_kind), Counter())[q.split] += 1
    for cell, splits in cells.items():
        assert abs(splits["dev"] - splits["test"]) <= 1, cell


def test_unanswerable_questions_include_both_kinds():
    kinds = Counter(q.unanswerable_kind for q in QUESTIONS.questions if not q.answerable)
    assert kinds["off_corpus"] and kinds["near_topic"]


@cache
def _pages(source_id: str) -> list[str]:
    return [page.text for page in extract_pages(BY_ID[source_id].path)]


def _corpus_present() -> bool:
    return all(BY_ID[s].path.exists() for s in METHODOLOGY_IDS)


@pytest.mark.skipif(
    not _corpus_present() or load_local_anchors() is None,
    reason="needs the local GHG Protocol PDFs and data/evals/local/methodology_anchors.json",
)
def test_every_anchor_is_on_its_labelled_page_and_matches_its_hash():
    anchors = load_local_anchors()
    for q in (q for q in QUESTIONS.questions if q.answerable):
        text = normalise(anchors[q.id])
        assert sha256_text(text) == q.anchor.sha256, f"{q.id}: local anchor is not the hashed one"
        page = _pages(q.anchor.source_id)[q.anchor.pdf_page - 1]
        assert text in page, f"{q.id}: anchor not on {q.anchor.source_id} PDF p.{q.anchor.pdf_page}"
