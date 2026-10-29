"""The author's verification of drafted `peer_disclosures` rows (CLAUDE.md rule 4).

The author checks each draft against the PDF page it cites and records the outcome in a
worksheet (a git-ignored CSV, `scripts/verify_peer_disclosures.py worksheet`). This module
turns that worksheet into updates, and refuses to produce any if the worksheet is incomplete
or inconsistent: one row unaccounted for stops the whole apply, because a partial apply would
make "verified" mean "verified, apart from the ones nobody looked at".

- `ok` -> `confirmed`: correct as drafted.
- `corrected` -> the corrected figure and/or row label, as printed. `figure` is re-parsed here
  by `parse_figure`; a number is never typed in. The drafted_* columns keep what the model
  read, so "n of N drafts needed correcting" stays measurable.
- `rejected` -> kept, marked, and left out of `peer_disclosures_verified`.

Rows are matched by (PDF page, row label as drafted, column header), never by database id:
ids change if the table is rebuilt from the drafts, and the worksheet must still apply.

A blind value is the author's reading of a blind-sample row, made before the draft value was
seen (the review and the worksheet both mask it). It is stored with the row and compared with
the draft, never used to correct it: a disagreement is settled by looking at the page again.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from carbon_r.models.peer_disclosure import parse_figure

Outcome = Literal["ok", "corrected", "rejected"]
_VERIFICATION = {"ok": "confirmed", "corrected": "corrected", "rejected": "rejected"}

# The worksheet's columns, in order. `draft_figure` shows "[blind]" for a blind-sample row.
WORKSHEET_COLUMNS = [
    "pdf_page",
    "row_label",
    "column_header",
    "draft_figure",
    "unit",
    "scope",
    "scope3_category",
    "method",
    "blind_sample",
    "blind_value_by_author",
    "outcome (ok/corrected/rejected)",
    "correct_figure_as_printed",
    "correct_row_label_as_printed",
    "note",
]


def _blank(value: str | None) -> str | None:
    return value.strip() or None if value is not None else None


class WorksheetEntry(BaseModel):
    """One filled-in worksheet line. `row_label` is the label AS DRAFTED (it identifies the
    row); a corrected label goes in `correct_row_label`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pdf_page: int
    row_label: str
    column_header: str
    blind_value: str | None
    outcome: Outcome
    correct_figure: str | None
    correct_row_label: str | None
    note: str | None

    @model_validator(mode="after")
    def _corrections_match_outcome(self) -> WorksheetEntry:
        corrected = self.correct_figure is not None or self.correct_row_label is not None
        if self.outcome == "corrected" and not corrected:
            raise ValueError(f"{self.row_label!r}: 'corrected' but no corrected value given")
        if self.outcome != "corrected" and corrected:
            raise ValueError(
                f"{self.row_label!r}: a correction given with outcome {self.outcome!r}"
            )
        return self

    @property
    def key(self) -> tuple[int, str, str]:
        return (self.pdf_page, self.row_label, self.column_header)

    @classmethod
    def from_csv_row(cls, row: dict[str, str]) -> WorksheetEntry:
        return cls(
            pdf_page=int(row["pdf_page"]),
            row_label=row["row_label"],
            column_header=row["column_header"],
            blind_value=_blank(row["blind_value_by_author"]),
            outcome=(row["outcome (ok/corrected/rejected)"] or "").strip().lower(),
            correct_figure=_blank(row["correct_figure_as_printed"]),
            correct_row_label=_blank(row["correct_row_label_as_printed"]),
            note=_blank(row["note"]),
        )


class DraftRow(BaseModel):
    """What verification needs to know about one unverified row in the table."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int
    pdf_page: int
    drafted_row_label: str
    column_header: str
    drafted_figure_printed: str
    blind_sample: bool
    notes: str | None

    @property
    def key(self) -> tuple[int, str, str]:
        return (self.pdf_page, self.drafted_row_label, self.column_header)


class VerificationUpdate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int
    verification: Literal["confirmed", "corrected", "rejected"]
    figure_printed: str
    figure: Decimal | None
    row_label: str
    blind_figure_printed: str | None
    notes: str | None


class VerificationPlan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    updates: list[VerificationUpdate]
    problems: list[str]
    blind_checked: int
    blind_matched: int


def plan_verification(drafts: list[DraftRow], entries: list[WorksheetEntry]) -> VerificationPlan:
    """Pure. Every unverified draft must have exactly one entry and every entry exactly one
    draft; otherwise `problems` is non-empty and `updates` is empty - apply nothing."""
    problems: list[str] = []
    by_key = {draft.key: draft for draft in drafts}
    seen: set[tuple[int, str, str]] = set()
    updates: list[VerificationUpdate] = []
    blind_checked = blind_matched = 0

    for entry in entries:
        where = f"PDF p.{entry.pdf_page} {entry.row_label!r} | {entry.column_header!r}"
        draft = by_key.get(entry.key)
        if draft is None:
            problems.append(f"{where}: no unverified draft with this page, label and column")
            continue
        if entry.key in seen:
            problems.append(f"{where}: listed twice")
            continue
        seen.add(entry.key)
        if entry.blind_value is not None and not draft.blind_sample:
            problems.append(f"{where}: a blind value on a row outside the blind sample")
        figure_printed = entry.correct_figure or draft.drafted_figure_printed
        figure = parse_figure(figure_printed)
        if entry.correct_figure is not None and figure is None:
            problems.append(f"{where}: corrected figure {entry.correct_figure!r} is not a number")
        if entry.blind_value is not None:
            blind_checked += 1
            blind_matched += entry.blind_value == draft.drafted_figure_printed
        note = f"verified ({_VERIFICATION[entry.outcome]}): {entry.note}" if entry.note else None
        updates.append(
            VerificationUpdate(
                id=draft.id,
                verification=_VERIFICATION[entry.outcome],
                figure_printed=figure_printed,
                figure=figure,
                row_label=entry.correct_row_label or draft.drafted_row_label,
                blind_figure_printed=entry.blind_value,
                notes="; ".join(n for n in (draft.notes, note) if n) or None,
            )
        )

    for draft in drafts:
        if draft.key not in seen:
            problems.append(
                f"PDF p.{draft.pdf_page} {draft.drafted_row_label!r} | {draft.column_header!r}: "
                "not in the worksheet"
            )
    return VerificationPlan(
        updates=[] if problems else updates,
        problems=problems,
        blind_checked=blind_checked,
        blind_matched=blind_matched,
    )


APPLY_UPDATE = """
update peer_disclosures set
    verification = %(verification)s,
    figure_printed = %(figure_printed)s,
    figure = %(figure)s,
    row_label = %(row_label)s,
    blind_figure_printed = %(blind_figure_printed)s,
    notes = %(notes)s,
    verified_by = %(verified_by)s,
    verified_at = %(verified_at)s
where id = %(id)s and verified_at is null
"""


__all__ = [
    "APPLY_UPDATE",
    "WORKSHEET_COLUMNS",
    "DraftRow",
    "VerificationPlan",
    "VerificationUpdate",
    "WorksheetEntry",
    "plan_verification",
]
