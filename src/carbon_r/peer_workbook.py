"""Peer rows read by code from an issuer's own workbook (Tier 3 Block 1).

FLCT and CICT print no GHG table in their reports; the figures are in the issuers'
workbooks. There is no model here. The author declares each table
(`peer_sources.WorkbookTable`, in `peer_sources.WORKBOOK_TABLES`) and lists each cell in an
inventory (`WorkbookInventoryEntry`): sheet, cell, row label and column header as printed,
scope, Scope 3 category and method. The inventory is the classification. Python then reads each
listed cell and checks it against the workbook:

- the label in the table's label column and the header in its header row must read exactly
  as listed, and the header must name the report's own year (`cell_year`);
- the unit comes from the table's unit column, or from a title cell that must contain the
  unit the table declares;
- the value is what the workbook DISPLAYS under the cell's number format (`xlsx.displayed`),
  which is the figure as printed - not the stored float behind it; a blank ("-", "n.a.") or
  an unsupported format is a problem, never a guess;
- every report-year cell in a declared table that holds a figure must be listed, and every
  listed cell must hold one: completeness is checked in both directions at read time. A
  note where a figure would be ("Included in Category 1") is not a figure;
- a table may name a heading cell (FLCT's region); its rows are then recorded as
  "Heading > Row label", the page drafter's convention.

Any problem, or any unlisted cell, and the caller writes nothing. Each row cites its sheet
and cell instead of a page (migration 0008) and is tagged `drafted_by = READER`, so it is
never mistaken for a model's draft - and never scored by eval 5a, which measures reading
pages. Every row is still verified by hand (CLAUDE.md rule 4).
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from carbon_r.models.peer_disclosure import (
    Method,
    PeerDisclosure,
    Scope,
    _is_ghg_unit,
    _is_intensity,
    cell_year,
    parse_figure,
)
from carbon_r.peer_sources import PeerSource, WorkbookTable
from carbon_r.xlsx import Cell, UnsupportedNumberFormatError, column, displayed, read_cells

READER = "carbon_r.workbook_reader v1"

_CELL = r"^[A-Z]{1,3}[1-9][0-9]*$"
_BLANK = {"", "-", "–", "—", "n.a.", "na", "n/a", "nil"}

WORKBOOK_INVENTORY_COLUMNS = [
    "sheet_name",
    "cell_ref",
    "row_label_as_printed",
    "column_header",
    "scope",
    "scope3_category",
    "method",
]


class WorkbookInventoryEntry(BaseModel):
    """One cell the author listed: where it is, its label and header as printed, and how to
    classify it. No figure."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sheet_name: str
    cell_ref: str = Field(pattern=_CELL)
    row_label: str
    column_header: str
    scope: Scope
    scope3_category: int | None = Field(default=None, ge=1, le=15)
    method: Method | None = None

    @classmethod
    def from_csv_row(cls, row: dict[str, str]) -> WorkbookInventoryEntry:
        def blank(key: str) -> str | None:
            return (row.get(key) or "").strip() or None

        category = blank("scope3_category")
        return cls(
            sheet_name=(row.get("sheet_name") or "").strip(),
            cell_ref=(row.get("cell_ref") or "").strip().upper(),
            row_label=(row.get("row_label_as_printed") or "").strip(),
            column_header=(row.get("column_header") or "").strip(),
            scope=(row.get("scope") or "").strip(),
            scope3_category=int(category) if category else None,
            method=blank("method"),
        )


class WorkbookDraft(BaseModel):
    """What one read produced. Write nothing unless `problems` and `unlisted` are empty."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rows: list[PeerDisclosure]
    problems: list[str]
    unlisted: list[str]
    listed: int
    drafted_by: str = READER


def _names_unit(title: str, unit: str) -> bool:
    """The title prints the unit as a whole token: "tCO2e" is not found in "ktCO2e"."""
    return re.search(rf"(?<![A-Za-z0-9]){re.escape(unit)}(?![A-Za-z0-9])", title) is not None


def _is_blank(text: str) -> bool:
    return text.strip().casefold() in _BLANK


def _note(cell: Cell, shown: str) -> str | None:
    """Say when the workbook shows something other than what it stores."""
    if cell.kind == "text":
        return "Number typed as text in the workbook; recorded as typed."
    try:
        stored = Decimal(cell.value)
    except InvalidOperation:
        return None
    if stored != Decimal(shown.replace(",", "")):
        return (
            f"Recorded as the workbook displays it (number format {cell.number_format!r}); "
            "the cell stores more decimals."
        )
    return None


def read_workbook(
    path: Path,
    source: PeerSource,
    tables: list[WorkbookTable],
    inventory: list[WorkbookInventoryEntry],
) -> WorkbookDraft:
    """Read every listed cell and check it against the workbook (module docstring)."""
    sheets: dict[str, dict[str, Cell]] = {}

    def cells_of(sheet: str) -> dict[str, Cell]:
        if sheet not in sheets:
            sheets[sheet] = read_cells(path, sheet)
        return sheets[sheet]

    def shown(sheet: str, ref: str) -> str | None:
        cell = cells_of(sheet).get(ref)
        try:
            return displayed(cell) if cell is not None else None
        except UnsupportedNumberFormatError:
            return None

    problems: list[str] = []
    rows: list[PeerDisclosure] = []
    seen: set[tuple[str, str]] = set()
    for entry in inventory:
        where = f"{entry.sheet_name}!{entry.cell_ref}"
        if (entry.sheet_name, entry.cell_ref) in seen:
            problems.append(f"{where}: listed twice")
            continue
        seen.add((entry.sheet_name, entry.cell_ref))
        table = next((t for t in tables if t.holds(entry.sheet_name, entry.cell_ref)), None)
        if table is None:
            problems.append(f"{where}: outside every declared table")
            continue
        row_number = entry.cell_ref[len(column(entry.cell_ref)) :]
        label = shown(table.sheet, f"{table.label_column}{row_number}")
        if label != entry.row_label:
            problems.append(f"{where}: label as printed is {label!r}, listed {entry.row_label!r}")
            continue
        header = shown(table.sheet, f"{column(entry.cell_ref)}{table.header_row}") or ""
        if header != entry.column_header:
            problems.append(f"{where}: header as printed is {header!r}, listed "
                            f"{entry.column_header!r}")  # fmt: skip
            continue
        if cell_year(header) != source.report_year:
            problems.append(f"{where}: header {header!r} is not the report year "
                            f"{source.report_year}")  # fmt: skip
            continue
        if table.unit_column is not None:
            unit = shown(table.sheet, f"{table.unit_column}{row_number}") or ""
        else:
            title = shown(table.sheet, table.unit_cell or "") or ""
            unit = table.unit_printed if _names_unit(title, table.unit_printed or "") else ""
            if not unit:
                problems.append(f"{where}: unit {table.unit_printed!r} is not printed in "
                                f"{table.unit_cell} ({title!r})")  # fmt: skip
                continue
        label_recorded = entry.row_label
        if table.heading_cell is not None:
            heading = shown(table.sheet, table.heading_cell)
            if not heading:
                problems.append(f"{where}: heading cell {table.heading_cell} is empty")
                continue
            label_recorded = f"{heading} > {entry.row_label}"
        if not _is_ghg_unit(unit) or _is_intensity(unit):
            problems.append(f"{where}: unit {unit!r} is not an absolute CO2e unit")
            continue
        cell = cells_of(table.sheet).get(entry.cell_ref)
        if cell is None or _is_blank(cell.value):
            problems.append(f"{where}: listed but blank")
            continue
        try:
            figure_printed = displayed(cell)
        except UnsupportedNumberFormatError as exc:
            problems.append(f"{where}: {exc}")
            continue
        figure = parse_figure(figure_printed)
        if figure is None:
            problems.append(f"{where}: {figure_printed!r} does not parse as a figure")
            continue
        try:
            rows.append(
                PeerDisclosure(
                    source_id=source.source_id,
                    company=source.company,
                    period_start=source.period_start,
                    period_end=source.period_end,
                    scope=entry.scope,
                    scope3_category=entry.scope3_category,
                    figure_printed=figure_printed,
                    figure=figure,
                    drafted_figure_printed=figure_printed,
                    unit=unit,
                    method=entry.method,
                    pdf_page=None,
                    printed_page=None,
                    sheet_name=entry.sheet_name,
                    cell_ref=entry.cell_ref,
                    row_label=label_recorded,
                    drafted_row_label=label_recorded,
                    column_header=header,
                    notes=_note(cell, figure_printed),
                    drafted_method=entry.method,
                )
            )
        except ValidationError as exc:
            problems.append(f"{where}: {exc.errors()[0]['msg']} (scope {entry.scope}, method "
                            f"{entry.method}, category {entry.scope3_category})")  # fmt: skip

    unlisted: list[str] = []
    for table in tables:
        cells = cells_of(table.sheet)
        header_cells = [c for c in cells.values() if c.ref[len(column(c.ref)) :] == str(
            table.header_row)]  # fmt: skip
        for header_cell in header_cells:
            col = column(header_cell.ref)
            if col == table.label_column or cell_year(shown(table.sheet, header_cell.ref) or "") \
                    != source.report_year:  # fmt: skip
                continue
            for row in range(table.first_row, table.last_row + 1):
                ref = f"{col}{row}"
                cell = cells.get(ref)
                if cell is None or _is_blank(cell.value) or (table.sheet, ref) in seen:
                    continue
                if parse_figure(shown(table.sheet, ref) or cell.value) is None:
                    continue  # a note where a figure would be ("Included in Category 1")
                label = shown(table.sheet, f"{table.label_column}{row}") or ""
                unlisted.append(f"{table.sheet}!{ref} {label!r}")

    return WorkbookDraft(rows=rows, problems=problems, unlisted=unlisted, listed=len(inventory))


__all__ = [
    "READER",
    "WORKBOOK_INVENTORY_COLUMNS",
    "WorkbookDraft",
    "WorkbookInventoryEntry",
    "WorkbookTable",
    "read_workbook",
]
