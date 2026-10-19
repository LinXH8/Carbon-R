"""What kind of document is this? The one classification the model is asked to make.

Tier 1 had one document kind, so the question never arose: `extract_from_document` sent
every page to one schema. With three kinds there is a routing decision, and sending a page
to the wrong schema produces the most misleading failure in the pipeline - a reading full
of plausible nulls, which looks like an illegible document rather than a misrouted one.

So the kind is established first, by its own small model call, before any field is read.

This is classification, not arithmetic, and so it is work the model is allowed to do (the
same category as `match_factor_category` picking from retrieved candidates in Tier 3). It
returns a label from a closed set and never a quantity.

Two of the five labels are refusals, and they exist because the honest answers to "what is
this?" include "I cannot tell" and "more than one thing":

    unknown   not one of the three kinds, or not a document at all
    mixed     the page carries more than one kind at once

Both make `extract_from_document` return `no_results` and ask the user, rather than
extracting under a guess (CLAUDE.md rule 7). `confidence` does the same job below a
threshold: a kind named with low confidence is a guess wearing a label.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# The three kinds the pipeline can read, plus the two refusals. Keep in step with
# `EXTRACTORS` in `tools/extract.py`: a kind here with no extractor there is a routing
# dead end, which `tests/test_doc_kind.py` asserts against.
DocumentKind = Literal[
    "electricity_bill",
    "lpg_invoice",
    "fuel_card_statement",
    "unknown",
    "mixed",
]

READABLE_KINDS: tuple[str, ...] = ("electricity_bill", "lpg_invoice", "fuel_card_statement")
REFUSAL_KINDS: tuple[str, ...] = ("unknown", "mixed")

# Below this, a named kind is treated as `unknown`. Set at 0.7 rather than tuned: there is
# no labelled set of ambiguous documents to tune it against yet, and a number chosen by eye
# that is documented as chosen by eye is more honest than one that looks derived. Tier 2
# Block 3's eval is where it earns a value.
MIN_CONFIDENCE = 0.7


class DocumentClassification(BaseModel):
    """The model's answer to "what kind of document is this?", and how sure it is."""

    model_config = ConfigDict(extra="forbid")

    document_kind: DocumentKind = Field(
        description=(
            "electricity_bill: a bill for grid electricity, with a meter reading and kWh. "
            "lpg_invoice: an invoice for delivered LPG / cooking gas cylinders. "
            "fuel_card_statement: a statement of vehicle fuel purchases, in litres. "
            "unknown: none of those three, or not a business document at all. "
            "mixed: this file contains more than one of those kinds."
        )
    )
    confidence: float = Field(
        ge=0.0, le=1.0, description="How confident you are in the kind, from 0.0 to 1.0"
    )
    why: str = Field(
        description=(
            "The evidence on the page that decided it - a heading, a column, a unit. "
            "One short sentence. This is shown to the user when the kind is refused."
        )
    )

    def resolved(self) -> tuple[str, str | None]:
        """`(kind, refusal reason)`. The kind is `unknown` when confidence is too low.

        Downgrading a low-confidence kind to `unknown` here, rather than at the call site,
        keeps the threshold in one place and means a caller cannot forget to apply it.
        """
        if self.document_kind in REFUSAL_KINDS:
            return self.document_kind, self.why
        if self.confidence < MIN_CONFIDENCE:
            return "unknown", (
                f"it looks like a {self.document_kind.replace('_', ' ')}, but only with "
                f"confidence {self.confidence:.2f}: {self.why}"
            )
        return self.document_kind, None


REFUSAL_MESSAGES: dict[str, str] = {
    "unknown": (
        "I could not tell what kind of document this is, so I have not tried to read it. "
        "I can read electricity bills, LPG invoices and vehicle fuel-card statements. "
        "What is this one?"
    ),
    "mixed": (
        "This file looks like it holds more than one kind of document. I have not read it, "
        "because figures from two documents read as one would be attributed to the wrong "
        "activity. Could you split them into separate files, or tell me which one to use?"
    ),
}


def refusal_message(kind: str, why: str | None) -> str:
    """The sentence a refused classification is reported with, with its evidence."""
    base = REFUSAL_MESSAGES.get(kind, REFUSAL_MESSAGES["unknown"])
    return f"{base} (What I saw: {why})" if why else base


__all__ = [
    "MIN_CONFIDENCE",
    "READABLE_KINDS",
    "REFUSAL_KINDS",
    "REFUSAL_MESSAGES",
    "DocumentClassification",
    "DocumentKind",
    "refusal_message",
]
