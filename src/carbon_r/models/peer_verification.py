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

import hashlib
import re
import unicodedata
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from carbon_r.models.peer_disclosure import cell_year, parse_figure

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
    "correct_method (location/market/none)",
    "note",
]


def _blank(value: str | None) -> str | None:
    return value.strip() or None if value is not None else None


class WorksheetEntry(BaseModel):
    """One filled-in worksheet line. `row_label` and `method` are AS DRAFTED (they identify
    the row); a corrected label goes in `correct_row_label`, a corrected method in
    `correct_method`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pdf_page: int
    row_label: str
    column_header: str
    method: Literal["location", "market"] | None
    blind_value: str | None
    outcome: Outcome
    correct_figure: str | None
    correct_row_label: str | None
    # "none" sets the method to not stated; blank leaves the drafted method.
    correct_method: Literal["location", "market", "none"] | None
    note: str | None

    @model_validator(mode="after")
    def _corrections_match_outcome(self) -> WorksheetEntry:
        corrected = any(
            v is not None
            for v in (self.correct_figure, self.correct_row_label, self.correct_method)
        )
        if self.outcome == "corrected" and not corrected:
            raise ValueError(f"{self.row_label!r}: 'corrected' but no corrected value given")
        if self.outcome != "corrected" and corrected:
            raise ValueError(
                f"{self.row_label!r}: a correction given with outcome {self.outcome!r}"
            )
        return self

    @property
    def key(self) -> tuple[int, str, str, str | None]:
        return (self.pdf_page, self.row_label, self.column_header, self.method)

    @classmethod
    def from_csv_row(cls, row: dict[str, str]) -> WorksheetEntry:
        return cls(
            pdf_page=int(row["pdf_page"]),
            row_label=row["row_label"],
            column_header=row["column_header"],
            method=_blank(row["method"]),
            blind_value=_blank(row["blind_value_by_author"]),
            outcome=(row["outcome (ok/corrected/rejected)"] or "").strip().lower(),
            correct_figure=_blank(row["correct_figure_as_printed"]),
            correct_row_label=_blank(row["correct_row_label_as_printed"]),
            # Absent from worksheets written before 2b's method correction.
            correct_method=_blank(row.get("correct_method (location/market/none)")),
            note=_blank(row["note"]),
        )


class DraftRow(BaseModel):
    """What verification needs to know about one unverified row in the table."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int
    pdf_page: int
    drafted_row_label: str
    column_header: str
    method: str | None  # as drafted
    drafted_figure_printed: str
    blind_sample: bool
    notes: str | None

    @property
    def key(self) -> tuple[int, str, str, str | None]:
        """Page, label as drafted, column and method: what makes a cell distinct (0007)."""
        return (self.pdf_page, self.drafted_row_label, self.column_header, self.method)


class VerificationUpdate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int
    verification: Literal["confirmed", "corrected", "rejected"]
    figure_printed: str
    figure: Decimal | None
    row_label: str
    method: str | None
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
    seen: set[tuple[int, str, str, str | None]] = set()
    updates: list[VerificationUpdate] = []
    blind_checked = blind_matched = 0

    for entry in entries:
        where = (
            f"PDF p.{entry.pdf_page} {entry.row_label!r} | {entry.column_header!r}"
            f"{f' | {entry.method}' if entry.method else ''}"
        )
        draft = by_key.get(entry.key)
        if draft is None:
            problems.append(
                f"{where}: no unverified draft with this page, label, column and method"
            )
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
        method = draft.method
        method_note = None
        if entry.correct_method is not None:
            method = None if entry.correct_method == "none" else entry.correct_method
            method_note = (
                f"method corrected: drafted {draft.method!r}, verified as "
                f"{repr(method) if method else 'not stated'}"
            )
        note = f"verified ({_VERIFICATION[entry.outcome]}): {entry.note}" if entry.note else None
        updates.append(
            VerificationUpdate(
                id=draft.id,
                verification=_VERIFICATION[entry.outcome],
                figure_printed=figure_printed,
                figure=figure,
                row_label=entry.correct_row_label or draft.drafted_row_label,
                method=method,
                blind_figure_printed=entry.blind_value,
                notes="; ".join(n for n in (draft.notes, method_note, note) if n) or None,
            )
        )

    for draft in drafts:
        if draft.key not in seen:
            problems.append(
                f"PDF p.{draft.pdf_page} {draft.drafted_row_label!r} | {draft.column_header!r}"
                f"{f' | {draft.method}' if draft.method else ''}: not in the worksheet"
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
    method = %(method)s,
    blind_figure_printed = %(blind_figure_printed)s,
    notes = %(notes)s,
    verified_by = %(verified_by)s,
    verified_at = %(verified_at)s
where id = %(id)s and verified_at is null
"""


# --- completeness: did the drafter skip a cell the page prints? ----------------------------
#
# Everything above measures whether a drafted cell matches the page. This measures the other
# direction. Before a report is drafted, the author lists every report-year cell on each table
# page (the inventory, `verify_peer_disclosures.py inventory`); after drafting, `match_cells`
# pairs the list with every report-year cell the drafter READ - kept or not. A listed cell
# with no read cell is one the drafter skipped; a read cell nobody listed is an extra, to be
# looked at on the page. A cell read and then dropped by a Python rule (a unit, a blank) was
# not skipped: it is matched, and counted under `dropped_by_rule` with its reason.
#
# Matching is exact after `normalise_label`, never fuzzy: a guessed pair would make "skipped"
# a judgement of this code. Two fixed allowances: a drafted label "Heading > Row" (the
# drafter's form for a row under a group heading) also matches "Row", the last segment whole;
# and a listed cell that agrees with a read cell in everything but the Scope 2 method is
# paired in a second pass and reported as `method_differs`, never as a clean match.
#
# When the author and the drafter spell one row differently, the author records the pairing
# in `paired_draft_label` after seeing the report, and the pair is reported as
# `paired_by_hand`, apart from exact matches and from skips - that column
# says which drafted row a listed cell is, never whether the cell exists, which was settled
# before the draft. `inventory_digest` covers every column except that one, and the drafter
# records it, so an inventory edited after drafting is reported as such.

INVENTORY_COLUMNS = [
    "pdf_page",
    "row_label_as_printed",
    "column_header",
    "scope",
    "method",
    "paired_draft_label",
]

InventoryScope = Literal["1", "2", "3", "1+2", "1+2+3"]

_MARKERS = re.compile("[\u2070\u00b9\u00b2\u00b3\u2074-\u2079*\u2020\u2021]")
_NOT_WORD = re.compile(r"[\W_]+")


# The column header to list for a cell on a page with no year column (`PAGE_YEARS`). What
# the model prints there is unknown in advance - blank, a unit - so it is not compared; the
# drafted header must only name no year.
NO_YEAR_COLUMN = "(no year column)"


def normalise_label(text: str) -> str:
    """Case, spacing, punctuation and footnote markers ignored: "Scope 2 (market-based)\u00b9\u00b2"
    and "scope 2 market based" are one label. Superscripts are dropped before NFKC, which
    would otherwise turn a marker into a digit; subscripts (CO\u2082) become digits."""
    text = unicodedata.normalize("NFKC", _MARKERS.sub("", text)).casefold()
    return " ".join(_NOT_WORD.sub(" ", text).split())


class InventoryEntry(BaseModel):
    """One cell the author listed from the page before the draft existed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pdf_page: int
    row_label: str
    column_header: str
    scope: InventoryScope
    method: Literal["location", "market"] | None
    paired_draft_label: str | None

    @classmethod
    def from_csv_row(cls, row: dict[str, str]) -> InventoryEntry:
        label = (row.get("row_label_as_printed") or "").strip()
        header = (row.get("column_header") or "").strip()
        if not label or not header:
            raise ValueError(f"p.{row.get('pdf_page')}: a row label and column header are needed")
        return cls(
            pdf_page=int(row["pdf_page"]),
            row_label=label,
            column_header=header,
            scope=(row.get("scope") or "").strip(),
            method=_blank(row.get("method")),
            paired_draft_label=_blank(row.get("paired_draft_label")),
        )

    @property
    def listed(self) -> tuple[int, str, str, str, str]:
        """What was listed before drafting: everything but the later pairing."""
        return (self.pdf_page, self.row_label, self.column_header, self.scope, self.method or "")


class DraftedCellRef(BaseModel):
    """A cell the drafter read, as completeness sees it: where it is, what it says it is, and
    what `to_disclosures` did with it ("kept", or the drop reason). No figure."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pdf_page: int
    drafted_row_label: str
    column_header: str
    scope: str
    method: str | None
    outcome: str = "kept"


class CellMatch(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    listed: InventoryEntry
    drafted: DraftedCellRef


class Completeness(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    listed: int
    read: int
    matched: list[CellMatch]
    # Paired through the author's `paired_draft_label`: the drafter read the cell, but under
    # different wording, so it is neither an exact match nor a skip.
    paired_by_hand: list[CellMatch]
    method_differs: list[CellMatch]
    skipped: list[InventoryEntry]
    extra: list[DraftedCellRef]
    problems: list[str]

    @property
    def dropped_by_rule(self) -> dict[str, int]:
        """Listed cells the drafter read that a Python rule then dropped, by reason."""
        reasons = [
            m.drafted.outcome for m in self.matched + self.paired_by_hand + self.method_differs
        ]
        counts: dict[str, int] = {}
        for reason in reasons:
            if reason != "kept":
                counts[reason] = counts.get(reason, 0) + 1
        return counts


def inventory_digest(entries: list[InventoryEntry]) -> str:
    lines = sorted("\t".join(map(str, e.listed)) for e in entries)
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def _label_forms(drafted_label: str) -> set[str]:
    """The drafted label whole, and its last "Heading > Row" segment whole."""
    return {normalise_label(drafted_label), normalise_label(drafted_label.split(">")[-1])}


def header_matches(listed: str, drafted: str) -> bool:
    """The listed column header names the drafted one, or the end of its heading path. The
    drafter writes a nested header as "Spanning heading > Column header" (DRAFT_INSTRUCTION);
    the author may list only its last part(s): "2025" and "Scope 1 > 2025" both match
    "SCOPE 1 & 2 > Scope 1 > 2025*" (markers ignored, as in `normalise_label`). A listed header
    longer than the drafted one never matches: it names a heading the drafter did not read.

    Known limit: a bare "2025" cannot tell apart two tables on one page that differ only by a
    spanning heading; the row label and scope must. Added 2026-11-11, after the Seatrium p.70
    draft (TIER3_BLOCK1.md)."""
    want = [normalise_label(part) for part in listed.split(">")]
    have = [normalise_label(part) for part in drafted.split(">")]
    return len(want) <= len(have) and have[len(have) - len(want) :] == want


def _same_cell(entry: InventoryEntry, cell: DraftedCellRef, *, method: bool = True) -> bool:
    if (entry.pdf_page, entry.scope) != (cell.pdf_page, cell.scope):
        return False
    if method and entry.method != cell.method:
        return False
    if entry.column_header == NO_YEAR_COLUMN:
        if cell_year(cell.column_header) is not None:
            return False
    elif not header_matches(entry.column_header, cell.column_header):
        return False
    if entry.paired_draft_label is not None:
        return cell.drafted_row_label == entry.paired_draft_label
    return normalise_label(entry.row_label) in _label_forms(cell.drafted_row_label)


def report_year_cells(pages: list[dict], report_year: int) -> list[DraftedCellRef]:
    """Every cell the drafter READ in a report-year column (or with no year column on a page
    whose year the author declared), from a draft file's `pages`, each with its outcome - not
    only the kept rows. Feeding the kept rows was the first run's
    defect: a cell dropped by a rule then looked like a cell the drafter skipped."""
    cells: list[DraftedCellRef] = []
    for page in pages:
        read = page["reading"]["cells"]
        outcomes = page.get("outcomes")
        declared = page.get("declared_year")
        if outcomes is None or len(outcomes) != len(read):
            raise ValueError(
                f"p.{page['pdf_page']}: the draft has no per-cell outcomes; re-run `draft` "
                "(cached replies, no model call)"
            )
        for cell, outcome in zip(read, outcomes, strict=True):
            if cell_year(cell["column_header_printed"], declared) == report_year:
                cells.append(
                    DraftedCellRef(
                        pdf_page=page["pdf_page"],
                        drafted_row_label=cell["row_label_printed"].strip(),
                        column_header=cell["column_header_printed"].strip(),
                        scope=cell["scope"],
                        method=cell["method"],
                        outcome=outcome,
                    )
                )
    return cells


def match_cells(
    inventory: list[InventoryEntry], drafts: list[DraftedCellRef], pages: list[int]
) -> Completeness:
    """Pair listed cells with the report-year cells the drafter read. Pure; each read cell
    pairs at most once. The author's hand pairings and the exact pairs come first, across the
    whole list, so two rows told apart only by method ("Total", location and market) pair
    correctly before any method-differs pass."""
    problems: list[str] = []
    seen: set[tuple] = set()
    for e in inventory:
        if e.pdf_page not in pages:
            problems.append(f"p.{e.pdf_page} is listed but is not a table page ({pages})")
        if e.listed in seen:
            problems.append(f"p.{e.pdf_page} {e.row_label!r} [{e.column_header}] listed twice")
        seen.add(e.listed)

    unused = list(drafts)

    def pair(entries: list[InventoryEntry], *, method: bool) -> tuple[list, list]:
        found, left = [], []
        for entry in entries:
            cell = next((c for c in unused if _same_cell(entry, c, method=method)), None)
            if cell is None:
                left.append(entry)
            else:
                unused.remove(cell)
                found.append(CellMatch(listed=entry, drafted=cell))
        return found, left

    by_hand = [e for e in inventory if e.paired_draft_label is not None]
    paired_by_hand, left_by_hand = pair(by_hand, method=True)
    matched, left = pair([e for e in inventory if e.paired_draft_label is None], method=True)
    method_differs, skipped = pair(left_by_hand + left, method=False)
    for entry in skipped:
        if entry.paired_draft_label is not None:
            problems.append(
                f"p.{entry.pdf_page} {entry.row_label!r}: paired with "
                f"{entry.paired_draft_label!r}, which is no unpaired drafted cell"
            )
    return Completeness(
        listed=len(inventory),
        read=len(drafts),
        matched=matched,
        paired_by_hand=paired_by_hand,
        method_differs=method_differs,
        skipped=skipped,
        extra=unused,
        problems=problems,
    )


__all__ = [
    "APPLY_UPDATE",
    "INVENTORY_COLUMNS",
    "NO_YEAR_COLUMN",
    "WORKSHEET_COLUMNS",
    "CellMatch",
    "Completeness",
    "DraftRow",
    "DraftedCellRef",
    "InventoryEntry",
    "VerificationPlan",
    "VerificationUpdate",
    "WorksheetEntry",
    "header_matches",
    "inventory_digest",
    "match_cells",
    "normalise_label",
    "report_year_cells",
    "plan_verification",
]
