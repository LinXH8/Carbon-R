"""The retrieval eval's answer key: questions, the pages that answer them, and anchors.

Rule 4: the key is labelled from the primary document, never from retrieval output. Every
answerable question was drafted while reading a specific page, and carries an ANCHOR - a
verbatim sentence from that page - so the label can be re-checked mechanically against the
PDF: the anchor must be on the labelled page of the extracted text.

No GHG Protocol text is committed (see `carbon_r.sources` for why). The committed file
holds only the anchor's sha256 and page; the anchor text lives in a git-ignored local file,
`data/evals/local/methodology_anchors.json`, for review and for the anchor test. Anyone
with the PDF can re-derive a hash from the page; nobody without it gets any text.

Phrasing styles exist because questions drafted while reading a page tend to reuse its
words, which flatters retrieval. `textbook` uses the standard's own vocabulary; `user` is how
an SME finance or operations person would ask; `singlish` is the same kind of question as a
Singaporean user might type it. Results are reported per style.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from carbon_r.config import PROJECT_ROOT

QUESTIONS_PATH = PROJECT_ROOT / "data" / "evals" / "methodology_questions.json"
LOCAL_ANCHORS_PATH = PROJECT_ROOT / "data" / "evals" / "local" / "methodology_anchors.json"

Phrasing = Literal["textbook", "user", "singlish"]
Split = Literal["dev", "test"]
MethodologySource = Literal["ghgp_corporate", "ghgp_scope2", "ghgp_scope3"]


class ExpectedPage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: MethodologySource
    pdf_page: int = Field(ge=1)


class Anchor(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: MethodologySource
    pdf_page: int = Field(ge=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$", description="Of the normalised anchor text")


class Question(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9_]+$")
    question: str = Field(min_length=5)
    answerable: bool
    phrasing: Phrasing
    split: Split
    # Any of these pages answers the question. Empty for an unanswerable question.
    expected: list[ExpectedPage]
    anchor: Anchor | None
    # Why an unanswerable question is unanswerable: off_corpus (not GHG Protocol material
    # at all) or near_topic (on a topic the standards cover, but not stated in them).
    unanswerable_kind: Literal["off_corpus", "near_topic"] | None = None
    note: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Question:
        if self.answerable:
            if not self.expected or self.anchor is None or self.unanswerable_kind:
                raise ValueError(f"{self.id}: answerable needs expected pages and an anchor")
            anchor_page = ExpectedPage(
                source_id=self.anchor.source_id, pdf_page=self.anchor.pdf_page
            )
            if anchor_page not in self.expected:
                raise ValueError(f"{self.id}: the anchor's page must be an expected page")
        elif self.expected or self.anchor is not None or self.unanswerable_kind is None:
            raise ValueError(f"{self.id}: unanswerable takes no pages, no anchor, and a kind")
        return self

    @property
    def document(self) -> str:
        return self.expected[0].source_id if self.expected else "none"


class QuestionSet(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int
    drafted: str
    review_status: Literal["draft", "reviewed"]
    questions: list[Question]

    @model_validator(mode="after")
    def _unique(self) -> QuestionSet:
        ids = Counter(q.id for q in self.questions)
        duplicates = [i for i, n in ids.items() if n > 1]
        if duplicates:
            raise ValueError(f"duplicate question ids: {duplicates}")
        return self

    def split(self, name: Split) -> list[Question]:
        return [q for q in self.questions if q.split == name]


def load_questions(path: Path = QUESTIONS_PATH) -> QuestionSet:
    return QuestionSet.model_validate_json(path.read_text())


def load_local_anchors(path: Path = LOCAL_ANCHORS_PATH) -> dict[str, str] | None:
    """`{question_id: anchor text}`, or None when this checkout has no local anchors."""
    if not path.exists():
        return None
    return json.loads(path.read_text())
