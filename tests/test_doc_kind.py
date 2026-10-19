"""Document-kind classification, and the two answers that are refusals.

The routing decision is new in Tier 2 Block 1, and the failure it exists to prevent is
subtle: a page read with the wrong schema comes back full of plausible nulls, which looks
like an illegible document rather than a misrouted one. So `unknown` and `mixed` are real
answers rather than fallbacks, and each gets a test here.

Offline: `resolved()` and `refusal_message()` are pure.
"""

import pytest

from carbon_r.models.doc_kind import (
    MIN_CONFIDENCE,
    READABLE_KINDS,
    REFUSAL_KINDS,
    DocumentClassification,
    refusal_message,
)
from carbon_r.tools.extract import EXTRACTORS


def classify(kind: str, confidence: float = 0.95, why: str = "a heading") -> DocumentClassification:
    return DocumentClassification(document_kind=kind, confidence=confidence, why=why)


def test_every_readable_kind_has_an_extractor():
    """A kind the classifier can return with nowhere to route it is a dead end."""
    assert set(EXTRACTORS) == set(READABLE_KINDS)


def test_no_refusal_kind_has_an_extractor():
    """`unknown` must not be routable: it means the kind was not established."""
    assert not set(REFUSAL_KINDS) & set(EXTRACTORS)


@pytest.mark.parametrize("kind", READABLE_KINDS)
def test_a_confident_readable_kind_resolves_to_itself(kind):
    resolved, why = classify(kind).resolved()
    assert resolved == kind
    assert why is None


@pytest.mark.parametrize("kind", REFUSAL_KINDS)
def test_a_refusal_kind_resolves_to_a_refusal_with_its_evidence(kind):
    resolved, why = classify(kind, why="two invoices on one page").resolved()
    assert resolved == kind
    assert why == "two invoices on one page"


def test_low_confidence_is_downgraded_to_unknown():
    """A kind named with low confidence is a guess wearing a label."""
    resolved, why = classify("lpg_invoice", confidence=MIN_CONFIDENCE - 0.01).resolved()
    assert resolved == "unknown"
    assert "lpg invoice" in why
    assert "confidence" in why


def test_confidence_exactly_at_the_threshold_is_accepted():
    """The boundary is inclusive, so the threshold means what it says."""
    resolved, _ = classify("lpg_invoice", confidence=MIN_CONFIDENCE).resolved()
    assert resolved == "lpg_invoice"


def test_confidence_outside_zero_to_one_is_rejected():
    with pytest.raises(ValueError):
        classify("lpg_invoice", confidence=1.4)


def test_an_invented_kind_is_rejected_by_the_type():
    """The set is closed, so the model cannot route to a kind that does not exist."""
    with pytest.raises(ValueError):
        classify("town_gas_bill")


def test_the_unknown_refusal_names_what_can_be_read_and_asks():
    message = refusal_message("unknown", "a bank statement")
    assert "electricity bills" in message
    assert "LPG invoices" in message
    assert "fuel-card statements" in message
    assert message.rstrip().endswith(")")
    assert "a bank statement" in message


def test_the_mixed_refusal_explains_why_it_will_not_guess():
    """Two documents read as one would attribute figures to the wrong activity."""
    message = refusal_message("mixed", "an invoice and a statement")
    assert "more than one kind" in message
    assert "wrong activity" in message
    assert "split" in message


def test_a_refusal_without_evidence_still_reads_as_a_sentence():
    assert refusal_message("unknown", None).endswith("What is this one?")
