"""Peer-report emissions figures: what a vision model drafts from a table page, and the rows
Python makes of it (Tier 2 Block 4 Part B, pass 2a).

Why page images. Part B's first pass found that the reports' PDF text layers fuse footnote
superscripts onto table figures and lose which column is which year (DBS PDF p.79). A model
looking at the rendered page sees a superscript as a superscript and a column under its
header, so the drafting reads images, through the same path as `extract_from_document`.

The division of labour is the one in `extraction.py`: the model transcribes, Python decides.

- The model returns strings as printed - row label, column header, figure, unit - plus two
  classifications it may make (which scope a row is, and whether the page states location-
  or market-based), each with the printed words that justify it.
- It never supplies a page, a period or a number. The page is the one the script rendered;
  the period is the report's own, from `data/peer_sources.json`; the number is parsed here.
- A cell is kept only if its printed column header names the report's own year and nothing
  else (`report_year_of_header`). Comparative columns are dropped rather than given a
  period inferred from their header, and every drop is counted by reason.

A row made here is a DRAFT. It counts for nothing until the author has checked it against
the PDF and set `verified_by` / `verified_at` (CLAUDE.md rule 4, the peer exception).
"""

from __future__ import annotations

import random
import re
from collections import Counter
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from carbon_r.agent.audit import has_malformed_thousands
from carbon_r.peer_sources import PRINTED_PAGE_OFFSETS, PeerSource

Scope = Literal["1", "2", "3", "1+2", "1+2+3"]
Method = Literal["location", "market"]


class DraftedCell(BaseModel):
    """One figure in an emissions table, as printed. Strings only: nothing is converted."""

    model_config = ConfigDict(extra="forbid")

    row_label_printed: str = Field(
        description="The row's label exactly as printed. If the same label appears under "
        "more than one section heading on this page, write 'Section heading > Row label', "
        "both as printed."
    )
    column_header_printed: str = Field(
        description="The header of this figure's column exactly as printed, e.g. a year. "
        "If a heading spans the columns above it (a region, a segment, an entity), write "
        "'Spanning heading > Column header', both as printed."
    )
    figure_printed: str = Field(
        description="The figure exactly as printed, digits and separators only, WITHOUT any "
        "footnote marker or superscript."
    )
    footnote_markers_printed: str | None = Field(
        description="Any footnote marker or superscript printed on this figure or its row "
        "label, e.g. '4' or 'a'. Null if there is none."
    )
    unit_printed: str = Field(
        description="The unit that applies to this figure as printed (on the row, the column "
        "or the table heading), e.g. 'tCO2e'. Do not convert."
    )
    scope: Scope = Field(
        description="Which scope this row reports. '1+2' or '1+2+3' only for a total the "
        "page itself prints as combined."
    )
    scope3_category: int | None = Field(
        description="The Scope 3 category number, only if the page prints it for this row."
    )
    method: Method | None = Field(
        description="For a row that includes Scope 2 (Scope 2, or a total that includes "
        "it): 'location' or 'market' when the page states which. Null when it does not say."
    )
    method_evidence_printed: str | None = Field(
        description="The printed words that state the method, e.g. 'Scope 2 (market-based)'. "
        "Null when method is null."
    )


class PeerTableReading(BaseModel):
    """What the model read off one page. It names no page, period or parsed value: those come
    from the script and from Python, never from the model."""

    model_config = ConfigDict(extra="forbid")

    page_has_ghg_table: bool = Field(
        description="True only if the page prints a table of absolute GHG emissions."
    )
    table_title_printed: str | None
    cells: list[DraftedCell]
    notes: str | None = Field(
        description="Anything ambiguous: an illegible figure, a unit that may not apply to "
        "every row, a restatement marker, a table that continues on another page."
    )


DRAFT_INSTRUCTION = """You are transcribing the greenhouse-gas emissions table on this page of a \
company sustainability report.

Transcribe ABSOLUTE GHG emissions figures only: Scope 1, Scope 2, Scope 3 and totals the page \
prints. Leave out intensity figures (anything per unit, per m2, per employee, per revenue), \
energy, water, waste, targets and percentages.

One cell per figure: give its row label and its column header exactly as printed. Include every \
year column the table prints; do not choose between them. When a heading spans several columns \
(a region such as "Singapore", a segment, an entity), include it in the column header as \
"Spanning heading > Column header" so each figure says which table it belongs to.

Footnote markers: a small raised number or letter beside a figure or label is a footnote marker, \
not part of the figure. Put it in footnote_markers_printed and leave it OUT of figure_printed. \
Look closely: a marker printed right after the last digit is easy to read as another digit.

Report ONLY what is printed, exactly as printed:
- Keep digits as shown, including thousands separators ("139,600" stays "139,600").
- Keep units as shown. Do not convert tonnes to kilotonnes or anything else.
- Set method only when the page says location-based or market-based for that row; quote those \
words in method_evidence_printed. Never infer it.

Never compute, add, infer or correct a figure. Do not write a total the page does not print. If \
a figure is not printed or not legible, leave that cell out and say so in notes. If the page has \
no emissions table, return page_has_ghg_table false and no cells."""


# --- header -> report year ----------------------------------------------------------------
#
# A column counts only if its header names exactly one year and that year is the report's
# own (the year its period ends, `PeerSource.report_year`). "FY2025/26" names one financial
# year, ending 2026. "2025 vs 2024" names two and is dropped; so is "% change", which names
# none. Anything this cannot read is dropped and counted, never guessed.

_FY = re.compile(r"\bFY\s?'?(\d{4}|\d{2})(?:\s?[/\-–]\s?(\d{4}|\d{2}))?\b", re.IGNORECASE)
_SPAN = re.compile(r"\b((?:19|20)\d{2})\s?[/\-–]\s?(\d{4}|\d{2})\b")
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")


def _full_year(digits: str) -> int:
    return int(digits) if len(digits) == 4 else 2000 + int(digits)


def report_year_of_header(header: str) -> int | None:
    """The single year a column header names (the END year of a split year), or None when it
    names none or more than one."""
    years = _years_named(header)
    return years[0] if len(years) == 1 else None


def cell_year(header: str, declared_year: int | None = None) -> int | None:
    """The year a cell is for: the one its header names, or - only when the header names no
    year at all - the year the author declared for the page (`peer_sources.PAGE_YEARS`). A
    header naming two years ("2025 vs 2024") stays None either way."""
    years = _years_named(header)
    if not years:
        return declared_year
    return years[0] if len(years) == 1 else None


def _years_named(header: str) -> list[int]:
    years: list[int] = []
    rest = header
    for pattern in (_FY, _SPAN):
        for match in pattern.finditer(rest):
            start, end = match.group(1), match.group(2)
            if end is None:
                years.append(_full_year(start))
            elif _full_year(end) - _full_year(start) == 1:  # a split year: 2025/26
                years.append(_full_year(end))
            else:  # a range of years, "2019-2025": more than one year, so not a column of one
                years += [_full_year(start), _full_year(end)]
        rest = pattern.sub(" ", rest)
    years += [int(y) for y in _YEAR.findall(rest)]
    return years


# --- the figure ---------------------------------------------------------------------------

_PLAIN_NUMBER = re.compile(r"^-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?$")
_BLANK = {"", "-", "–", "—", "n.a.", "na", "n/a", "nil"}


def parse_figure(printed: str) -> Decimal | None:
    """A plain printed number -> Decimal, commas stripped, never rescaled. Anything else -
    a malformed thousands group, a stray letter, a unit - is None, never a best guess."""
    text = printed.strip().replace("−", "-")
    if not _PLAIN_NUMBER.match(text):
        return None
    return Decimal(text.replace(",", ""))


def includes_scope_2(scope: str) -> bool:
    """Scope 2, or a printed total that includes it. Location- and market-based describe the
    Scope 2 part, so a total carries the method of the Scope 2 inside it (DBS prints one
    total of each)."""
    return "2" in scope.split("+")


def _is_intensity(unit: str) -> bool:
    lowered = unit.lower()
    return "/" in lowered or " per " in f" {lowered} "


def _is_ghg_unit(unit: str) -> bool:
    return "co2" in unit.lower().replace("₂", "2")


# --- rows ---------------------------------------------------------------------------------


class PeerDisclosure(BaseModel):
    """One `peer_disclosures` row (db/migrations/0006). A draft until verified by hand."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    company: str
    period_start: date
    period_end: date
    scope: Scope
    scope3_category: int | None = Field(default=None, ge=1, le=15)
    figure_printed: str
    figure: Decimal | None
    drafted_figure_printed: str
    unit: str
    method: Method | None
    pdf_page: int = Field(ge=1)
    printed_page: int | None
    row_label: str
    # The label as the model read it. Never corrected, so it identifies the row when
    # `row_label` is fixed by the author (and a re-load cannot re-insert the misread).
    drafted_row_label: str
    column_header: str
    notes: str | None
    # The method as drafted; `method` may be corrected at verification (migration 0007).
    drafted_method: Method | None = None
    blind_sample: bool = False

    @model_validator(mode="after")
    def _consistent(self) -> PeerDisclosure:
        if self.method is not None and not includes_scope_2(self.scope):
            raise ValueError("method is only meaningful for a row that includes Scope 2")
        if self.scope3_category is not None and self.scope != "3":
            raise ValueError("scope3_category is only meaningful for Scope 3")
        return self

    @property
    def key(self) -> tuple[str, int, str, str, str]:
        """What makes a cell distinct (migration 0007): the drafted method too, "" when none."""
        return (
            self.source_id,
            self.pdf_page,
            self.drafted_row_label,
            self.column_header,
            self.drafted_method or "",
        )


_COLUMNS = [
    "source_id",
    "company",
    "period_start",
    "period_end",
    "scope",
    "scope3_category",
    "figure_printed",
    "figure",
    "drafted_figure_printed",
    "unit",
    "method",
    "pdf_page",
    "printed_page",
    "row_label",
    "drafted_row_label",
    "column_header",
    "notes",
    "drafted_method",
    "drafted_by",
    "drafted_at",
    "draft_request_hash",
    "page_image_sha256",
    "blind_sample",
]
_KEY = ("source_id", "pdf_page", "drafted_row_label", "column_header", "drafted_method")
_UPDATABLE = [c for c in _COLUMNS if c not in _KEY]

# The loader's upsert. A verified row is never touched; an unchanged draft is not
# rewritten, so a second load is visibly a no-op. `xmax = 0` is true for a row this
# statement inserted.
UPSERT_DISCLOSURE = f"""
insert into peer_disclosures ({", ".join(_COLUMNS)})
values ({", ".join(f"%({c})s" for c in _COLUMNS)})
on conflict ({", ".join(_KEY)}) do update set
    {", ".join(f"{c} = excluded.{c}" for c in _UPDATABLE)}
where peer_disclosures.verified_at is null
  and ({", ".join(f"peer_disclosures.{c}" for c in _UPDATABLE)})
      is distinct from ({", ".join(f"excluded.{c}" for c in _UPDATABLE)})
returning (xmax = 0) as inserted
"""


class PageDraft(BaseModel):
    """The rows kept from one page, every cell that was not counted by reason, and each cell's
    outcome in reading order ("kept" or its drop reason), which completeness reads."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rows: list[PeerDisclosure]
    dropped: dict[str, int]
    problems: list[str]
    outcomes: list[str] = []


# --- a unit printed on the page before ------------------------------------------------------
#
# Added in 2b for UOB, whose "Sustainability in Numbers" table runs over PDF p.135-136 and
# prints its unit heading ("... (thousand tCO2e)") only on p.135. The drafter, reading p.136
# alone, rightly left the unit blank, and every cell there was dropped for having none.
#
# The model is not asked to carry anything. Python carries a unit only when all of these hold:
# the cell's unit is blank (a printed unit is never replaced); the page before is the one
# drafted immediately before it (pdf_page - 1); the two pages are one table; and the page
# before printed exactly one CO2e unit that is not an intensity. Every carried row says so in
# its notes, and the author checks it against both pages at verification.
#
# "One table" is either the same transcribed table title, or the author's declaration in
# `peer_sources.TABLE_CONTINUES`. The declaration exists because the title test failed on the
# very pages it was written for: the model titled UOB p.135 by its table heading and p.136 by
# the running page header. A declaration replaces only the title test.


class CarriedUnit(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    unit: str
    from_pdf_page: int
    # Why the two pages count as one table: the same transcribed title, or the author said so.
    basis: Literal["same_title", "declared"]


def _title(reading: PeerTableReading) -> str:
    return " ".join((reading.table_title_printed or "").split()).casefold()


def continued_unit(
    reading: PeerTableReading,
    pdf_page: int,
    previous: tuple[int, PeerTableReading] | None,
    *,
    declared: bool = False,
) -> CarriedUnit | None:
    """The unit to carry onto this page's blank-unit cells, or None. Pure. `declared` is the
    author's statement that this page continues the table on the page before."""
    if previous is None:
        return None
    previous_page, before = previous
    if previous_page != pdf_page - 1:
        return None
    same_title = bool(_title(reading)) and _title(reading) == _title(before)
    if not (same_title or declared):
        return None
    if not any(not cell.unit_printed.strip() for cell in reading.cells):
        return None
    units = {
        cell.unit_printed.strip()
        for cell in before.cells
        if _is_ghg_unit(cell.unit_printed) and not _is_intensity(cell.unit_printed)
    }
    if len(units) != 1:
        return None
    return CarriedUnit(
        unit=units.pop(),
        from_pdf_page=previous_page,
        basis="same_title" if same_title else "declared",
    )


def to_disclosures(
    reading: PeerTableReading,
    source: PeerSource,
    *,
    pdf_page: int,
    carried_unit: CarriedUnit | None = None,
    declared_year: int | None = None,
) -> PageDraft:
    """Pure: one page's reading -> draft rows. Page, period and printed page come from the
    arguments and the registry; the figure is parsed here; the header decides the year.
    `carried_unit` (see `continued_unit`) fills a blank unit and never replaces a printed one.
    `declared_year` (see `cell_year`) dates only a cell whose header names no year."""
    offset = PRINTED_PAGE_OFFSETS.get(source.source_id)
    printed_page = pdf_page - offset if offset is not None else None
    dropped: Counter[str] = Counter()
    problems: list[str] = []
    rows: list[PeerDisclosure] = []
    outcomes: list[str] = []
    seen: set[tuple[str, str, str | None]] = set()

    def drop(reason: str) -> None:
        dropped[reason] += 1
        outcomes.append(reason)

    for cell in reading.cells:
        figure_text = cell.figure_printed.strip()
        unit = cell.unit_printed.strip()
        carried = not unit and carried_unit is not None
        if carried:
            unit = carried_unit.unit  # type: ignore[union-attr]
        if figure_text.lower() in _BLANK:
            drop("no_figure")
            continue
        year_named = report_year_of_header(cell.column_header_printed)
        if cell_year(cell.column_header_printed, declared_year) != source.report_year:
            drop("column_not_report_year")
            continue
        if not _is_ghg_unit(unit):
            drop("unit_not_ghg")
            continue
        if _is_intensity(unit):
            drop("intensity_unit")
            continue
        # A method is kept only on a row that includes Scope 2, and only a kept method can
        # tell two cells apart (UOB p.189 prints location and market under one label).
        method = cell.method if includes_scope_2(cell.scope) else None
        cell_key = (cell.row_label_printed, cell.column_header_printed, method)
        if cell_key in seen:
            drop("duplicate_cell_key")
            problems.append(
                f"p.{pdf_page}: two cells share row {cell.row_label_printed!r}, column "
                f"{cell.column_header_printed!r} and method {method}; the second was dropped"
            )
            continue
        seen.add(cell_key)
        outcomes.append("kept")

        notes: list[str] = []
        if year_named is None:
            notes.append(
                f"no year column printed: year {declared_year} declared by the author for this page"
            )
        if carried:
            reason = (
                "the table continues under the same title"
                if carried_unit.basis == "same_title"  # type: ignore[union-attr]
                else "the author declared that the table continues from that page"
            )
            notes.append(
                f"unit {unit!r} printed on PDF p.{carried_unit.from_pdf_page}, not on this "  # type: ignore[union-attr]
                f"page: carried because {reason}"
            )
        if cell.method is not None and method is None:
            problems.append(
                f"p.{pdf_page} {cell.row_label_printed!r}: method on scope {cell.scope}"
            )
            notes.append(f"model gave method {cell.method!r} on a scope {cell.scope} row; not kept")
        if method is not None:
            notes.append(f"method stated as: {cell.method_evidence_printed!r}")
        category = cell.scope3_category
        if category is not None and (cell.scope != "3" or not 1 <= category <= 15):
            problems.append(f"p.{pdf_page} {cell.row_label_printed!r}: category {category}")
            notes.append(f"model gave Scope 3 category {category}; not kept")
            category = None
        if cell.footnote_markers_printed:
            notes.append(f"footnote marker(s) printed: {cell.footnote_markers_printed!r}")
        figure = parse_figure(figure_text)
        if has_malformed_thousands(figure_text):
            notes.append("malformed thousands group: possibly a fused footnote marker")
        if figure is None:
            problems.append(f"p.{pdf_page} {cell.row_label_printed!r}: {figure_text!r} unparsed")
            notes.append("figure did not parse as a plain number")

        rows.append(
            PeerDisclosure(
                source_id=source.source_id,
                company=source.company,
                period_start=source.period_start,
                period_end=source.period_end,
                scope=cell.scope,
                scope3_category=category,
                figure_printed=figure_text,
                figure=figure,
                drafted_figure_printed=figure_text,
                unit=unit,
                method=method,
                pdf_page=pdf_page,
                printed_page=printed_page,
                row_label=cell.row_label_printed.strip(),
                drafted_row_label=cell.row_label_printed.strip(),
                column_header=cell.column_header_printed.strip(),
                notes="; ".join(notes) or None,
                drafted_method=method,
            )
        )
    return PageDraft(rows=rows, dropped=dict(dropped), problems=problems, outcomes=outcomes)


# --- the blind sample (CLAUDE.md rule 4) --------------------------------------------------
#
# Drawn when the rows are drafted, before anyone has seen a value, so the author can enter
# these figures blind first. Seeded and recorded; ordered by key so the draw depends on the
# rows, not on the order the model listed them.

BLIND_SHARE = 0.2
BLIND_SEED = 20261029


def blind_sample(
    keys: list[tuple], *, seed: int = BLIND_SEED, share: float = BLIND_SHARE
) -> set[tuple]:
    if not keys:
        return set()
    ordered = sorted(set(keys))
    size = max(1, round(share * len(ordered)))
    return set(random.Random(seed).sample(ordered, size))


__all__ = [
    "BLIND_SEED",
    "BLIND_SHARE",
    "DRAFT_INSTRUCTION",
    "CarriedUnit",
    "DraftedCell",
    "PageDraft",
    "PeerDisclosure",
    "PeerTableReading",
    "UPSERT_DISCLOSURE",
    "blind_sample",
    "continued_unit",
    "parse_figure",
    "cell_year",
    "report_year_of_header",
    "to_disclosures",
]
