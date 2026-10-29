"""`data/peer_disclosures_verified.json`: the verified peer figures, committed.

The one tracked file allowed to hold real peer emissions figures (CLAUDE.md). Every row in
it was drafted by a vision model from a page image and then checked by the author against
that page (CLAUDE.md rule 4, the peer exception); unverified drafts and rejected rows never
reach it. It exists so a fresh clone - which has no reports and no database - can seed
`peer_disclosures` with exactly the verified rows, the same reason the factor loaders carry
their values as literals.

Each row keeps what was checked and how:

- the figure as printed and as parsed (`figure` is re-derived by `parse_figure` on load and
  must agree, so the file cannot carry a number Python did not compute - rule 1);
- its citation: report, PDF and printed page, row label and column header as printed;
- `verification`: who checked it, when, and the outcome (confirmed / corrected);
- `draft`: what the model read, labelled as such - including a value the author corrected;
- `blind`: whether the row was in the blind sample, and the author's blind reading.

Figures are as self-reported by each company, checked against the cited page on the stated
date, and not independently assured. Rows are ordered by (source, PDF page, drafted row
label, column header), and the JSON is written with sorted keys, so a diff shows only what
changed.

`load` is idempotent and conservative: a row not in the table is inserted; an identical row
is left alone; a row that exists but DIFFERS is a conflict, reported, and nothing is written.
The file never silently overwrites the database, nor the database the file.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal

import psycopg
from pydantic import BaseModel, ConfigDict, Field, model_validator

from carbon_r.config import PROJECT_ROOT
from carbon_r.models.peer_disclosure import Method, Scope, parse_figure

VERIFIED_FILE = PROJECT_ROOT / "data" / "peer_disclosures_verified.json"

FILE_NOTE = (
    "Peer emissions figures as self-reported by each company in its own report, drafted by a "
    "vision model from the cited page and checked by hand against that page on the date in "
    "`verification.verified_at`. Not independently assured. Each company reports under its "
    "own boundary, consolidation approach and period; figures are not comparable "
    "like-for-like. `draft` holds what the model read, kept for measurement; it is not a "
    "verified value. The reports themselves are not redistributed."
)


class Verification(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    verified_by: str
    verified_at: str  # ISO 8601, UTC
    outcome: Literal["confirmed", "corrected"]


class Draft(BaseModel):
    """What the drafting model read. Not a verified value."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    figure_printed: str
    row_label: str
    drafted_by: str
    drafted_at: str
    draft_request_hash: str
    page_image_sha256: str


class Blind(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    in_sample: bool
    author_value: str | None

    @model_validator(mode="after")
    def _only_in_sample(self) -> Blind:
        if self.author_value is not None and not self.in_sample:
            raise ValueError("a blind value on a row outside the blind sample")
        return self


class VerifiedRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    company: str
    period_start: date
    period_end: date
    scope: Scope
    scope3_category: int | None = Field(ge=1, le=15)
    figure_printed: str
    figure: Decimal
    unit: str
    method: Method | None
    pdf_page: int = Field(ge=1)
    printed_page: int | None
    row_label: str
    column_header: str
    notes: str | None
    verification: Verification
    draft: Draft
    blind: Blind

    @model_validator(mode="after")
    def _figure_is_the_parse(self) -> VerifiedRow:
        if parse_figure(self.figure_printed) != self.figure:
            raise ValueError(
                f"{self.source_id} p.{self.pdf_page} {self.row_label!r}: figure does not equal "
                "parse_figure(figure_printed)"
            )
        return self

    @property
    def key(self) -> tuple[str, int, str, str]:
        return (self.source_id, self.pdf_page, self.draft.row_label, self.column_header)


class VerifiedFile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal[1]
    note: str
    rows: list[VerifiedRow]

    @model_validator(mode="after")
    def _ordered_and_unique(self) -> VerifiedFile:
        keys = [row.key for row in self.rows]
        if keys != sorted(keys):
            raise ValueError("rows are not in (source, page, drafted label, column) order")
        if len(set(keys)) != len(keys):
            raise ValueError("a row appears twice")
        return self


# --- export -------------------------------------------------------------------------------

_SELECT = """
select source_id, company, period_start, period_end, scope, scope3_category, figure_printed,
       figure, unit, method, pdf_page, printed_page, row_label, column_header, notes,
       verified_by, verified_at, verification, drafted_figure_printed, drafted_row_label,
       drafted_by, drafted_at, draft_request_hash, page_image_sha256, blind_sample,
       blind_figure_printed
from peer_disclosures_verified
"""


def _iso(moment: datetime) -> str:
    """UTC, at the stored precision, so the file round-trips exactly."""
    return moment.astimezone(UTC).isoformat()


def export_rows(conn: psycopg.Connection, source_ids: list[str] | None = None) -> VerifiedFile:
    """The verified view as a `VerifiedFile`, in key order."""
    sql = _SELECT + (" where source_id = any(%(ids)s)" if source_ids is not None else "")
    rows = []
    for r in conn.execute(sql, {"ids": source_ids}).fetchall():
        (sid, company, start, end, scope, cat, fig_p, fig, unit, method, page, printed, label,
         header, notes, by, at, outcome, d_fig, d_label, d_by, d_at, d_hash, d_img, in_blind,
         blind_value) = r  # fmt: skip
        rows.append(
            VerifiedRow(
                source_id=sid,
                company=company,
                period_start=start,
                period_end=end,
                scope=scope,
                scope3_category=cat,
                figure_printed=fig_p,
                figure=fig,
                unit=unit,
                method=method,
                pdf_page=page,
                printed_page=printed,
                row_label=label,
                column_header=header,
                notes=notes,
                verification=Verification(verified_by=by, verified_at=_iso(at), outcome=outcome),
                draft=Draft(
                    figure_printed=d_fig,
                    row_label=d_label,
                    drafted_by=d_by,
                    drafted_at=_iso(d_at),
                    draft_request_hash=d_hash,
                    page_image_sha256=d_img,
                ),
                blind=Blind(in_sample=in_blind, author_value=blind_value),
            )
        )
    return VerifiedFile(version=1, note=FILE_NOTE, rows=sorted(rows, key=lambda row: row.key))


def dumps(verified: VerifiedFile) -> str:
    """Deterministic JSON: key order fixed by the model, sorted keys, figures as strings."""
    return (
        json.dumps(verified.model_dump(mode="json"), indent=2, sort_keys=True, ensure_ascii=False)
        + "\n"
    )


def read_file(path: Path = VERIFIED_FILE) -> VerifiedFile:
    return VerifiedFile.model_validate_json(path.read_text())


# --- load ---------------------------------------------------------------------------------


def _db_row(row: VerifiedRow) -> dict:
    return {
        "source_id": row.source_id,
        "company": row.company,
        "period_start": row.period_start,
        "period_end": row.period_end,
        "scope": row.scope,
        "scope3_category": row.scope3_category,
        "figure_printed": row.figure_printed,
        "figure": row.figure,
        "drafted_figure_printed": row.draft.figure_printed,
        "unit": row.unit,
        "method": row.method,
        "pdf_page": row.pdf_page,
        "printed_page": row.printed_page,
        "row_label": row.row_label,
        "drafted_row_label": row.draft.row_label,
        "column_header": row.column_header,
        "notes": row.notes,
        "drafted_by": row.draft.drafted_by,
        "drafted_at": datetime.fromisoformat(row.draft.drafted_at),
        "draft_request_hash": row.draft.draft_request_hash,
        "page_image_sha256": row.draft.page_image_sha256,
        "blind_sample": row.blind.in_sample,
        "blind_figure_printed": row.blind.author_value,
        "verified_by": row.verification.verified_by,
        "verified_at": datetime.fromisoformat(row.verification.verified_at),
        "verification": row.verification.outcome,
    }


_COLUMNS = [
    "source_id", "company", "period_start", "period_end", "scope", "scope3_category",
    "figure_printed", "figure", "drafted_figure_printed", "unit", "method", "pdf_page",
    "printed_page", "row_label", "drafted_row_label", "column_header", "notes", "drafted_by",
    "drafted_at", "draft_request_hash", "page_image_sha256", "blind_sample",
    "blind_figure_printed", "verified_by", "verified_at", "verification",
]  # fmt: skip

_FIND = f"""
select {", ".join(_COLUMNS)} from peer_disclosures
where source_id = %(source_id)s and pdf_page = %(pdf_page)s
  and drafted_row_label = %(drafted_row_label)s and column_header = %(column_header)s
"""
_INSERT = f"""
insert into peer_disclosures ({", ".join(_COLUMNS)})
values ({", ".join(f"%({c})s" for c in _COLUMNS)})
"""


class LoadResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    inserted: int
    unchanged: int
    conflicts: list[str]


def load_rows(conn: psycopg.Connection, verified: VerifiedFile) -> LoadResult:
    """Insert what is missing, leave what is identical, refuse what differs. On any conflict
    nothing is inserted (the caller rolls back); the conflicts say which rows and fields."""
    inserted = unchanged = 0
    conflicts: list[str] = []
    for row in verified.rows:
        want = _db_row(row)
        have = conn.execute(_FIND, want).fetchone()
        if have is None:
            conn.execute(_INSERT, want)
            inserted += 1
            continue
        differ = [c for c, value in zip(_COLUMNS, have, strict=True) if value != want[c]]
        if differ:
            conflicts.append(
                f"{row.source_id} p.{row.pdf_page} {row.draft.row_label!r} | "
                f"{row.column_header!r}: differs in {', '.join(differ)}"
            )
        else:
            unchanged += 1
    return LoadResult(inserted=inserted, unchanged=unchanged, conflicts=conflicts)


__all__ = [
    "FILE_NOTE",
    "VERIFIED_FILE",
    "LoadResult",
    "VerifiedFile",
    "VerifiedRow",
    "dumps",
    "export_rows",
    "load_rows",
    "read_file",
]
