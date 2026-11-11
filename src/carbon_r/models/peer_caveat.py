"""Caveats printed with verified peer figures (Tier 3 Block 1, proposal 1; migration 0009).

A footnote can qualify a figure: ST Engineering's 2025 environmental data are actual for nine
months and best estimates for the last three. Rule 7 asks for an estimated reading to be
flagged, so the footnote enters the verified rows it qualifies as a caveat: the text as
printed, its kind, and where it is printed (a PDF page, or a workbook cell).

- The author transcribes the caveat from the page into a git-ignored CSV
  (`scripts/verify_peer_disclosures.py caveat-sheet`), one line per verified row, blank where
  none applies. `plan_caveats` turns it into updates of the caveat columns ONLY, on rows
  already verified; a line for no verified row stops everything. An identical caveat is left
  unchanged, so a second run updates nothing; a different one is refused, never overwritten.
- A caveat may not carry an emissions figure (`has_emissions_figure`). A footnote that gives
  one - another method's Scope 2, say - should become a verified row of its own, not text the
  agent could quote without a lookup.
- The lookup shows the caveat with its figure, and the loop attaches the caveats of the turn's
  figures to the reply in code (`attached_caveats`), as it attaches `PEER_CAVEAT`: an
  estimate is never left to the model to mention.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

CaveatKind = Literal["estimate", "restated", "boundary", "method", "other"]

_CELL = r"^.+![A-Z]{1,3}[1-9][0-9]*$"
_NUMBER = re.compile(r"(?<![\w.,/])(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?(?![\w])")
_LABEL_WORD = re.compile(r"(?:scope|category|cat|table|note|footnote|page|p\.)\s*$", re.I)
_UNIT_AFTER = re.compile(r"^\s*(?:[\w-]+\s+){0,2}?(?:k?t|kg|mt)?\s*co[2₂]|^\s*(?:kilo)?tonnes?\b"
                         r"|^\s*(?:k?t|kg)\b", re.I)  # fmt: skip


def has_emissions_figure(text: str) -> bool:
    """Whether the text states a quantity: a decimal, a number with thousands separators, a
    whole number of 100 or more that is not a year, or any number followed by a CO2e or
    tonnage unit. A year, and a number naming a scope, category, table or page, are not."""
    for match in _NUMBER.finditer(text):
        whole, fraction = match.group(1), match.group(2)
        if fraction or "," in whole:
            return True
        if _UNIT_AFTER.match(text[match.end() : match.end() + 30]):
            return True
        if _LABEL_WORD.search(text[: match.start()]):
            continue
        if int(whole) >= 100 and not 1900 <= int(whole) <= 2100:
            return True
    return False


class Caveat(BaseModel):
    """A caveat as stored: the text as printed, its kind, and one place it is printed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    printed: str
    kind: CaveatKind
    pdf_page: int | None = Field(ge=1)
    cell: str | None = Field(pattern=_CELL)

    @model_validator(mode="after")
    def _whole(self) -> Caveat:
        if not self.printed.strip():
            raise ValueError("a caveat needs its printed text")
        if (self.pdf_page is None) == (self.cell is None):
            raise ValueError("a caveat is printed on one PDF page or in one workbook cell")
        if has_emissions_figure(self.printed):
            raise ValueError(
                "a caveat may not carry an emissions figure; make that figure a verified row"
            )
        return self

    def where(self, report: str) -> str:
        if self.pdf_page is not None:
            return f"{report}, PDF p. {self.pdf_page}"
        sheet, cell = (self.cell or "").rsplit("!", 1)
        return f"{report}, sheet '{sheet}', cell {cell}"


def _key(page, sheet, cell, label, header, method) -> tuple:
    return (page or 0, sheet or "", cell or "", label, header, method)


class CaveatEntry(BaseModel):
    """One line of the author's caveat sheet: the verified row it qualifies (by page or cell,
    label as drafted, column and method, as on a worksheet) and the caveat, blank for none."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pdf_page: int | None
    sheet_name: str | None
    cell_ref: str | None
    row_label: str
    column_header: str
    method: str | None
    caveat_printed: str
    caveat_kind: str
    caveat_pdf_page: int | None
    caveat_cell: str | None

    @property
    def key(self) -> tuple:
        return _key(self.pdf_page, self.sheet_name, self.cell_ref, self.row_label,
                    self.column_header, self.method)  # fmt: skip

    @property
    def blank(self) -> bool:
        return not self.caveat_printed.strip()

    @classmethod
    def from_csv_row(cls, row: dict[str, str]) -> CaveatEntry:
        def text(key: str) -> str:
            return (row.get(key) or "").strip()

        def number(key: str) -> int | None:
            return int(text(key)) if text(key) else None

        return cls(
            pdf_page=number("pdf_page"),
            sheet_name=text("sheet_name") or None,
            cell_ref=text("cell_ref") or None,
            row_label=text("row_label"),
            column_header=text("column_header"),
            method=text("method") or None,
            caveat_printed=text("caveat_printed"),
            caveat_kind=text("caveat_kind").lower(),
            caveat_pdf_page=number("caveat_pdf_page"),
            caveat_cell=text("caveat_cell") or None,
        )


class CaveatTarget(BaseModel):
    """A verified row as the plan sees it, with the caveat it already has, if any."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int
    pdf_page: int | None
    sheet_name: str | None
    cell_ref: str | None
    drafted_row_label: str
    column_header: str
    method: str | None  # as drafted
    caveat: Caveat | None

    @property
    def key(self) -> tuple:
        return _key(self.pdf_page, self.sheet_name, self.cell_ref, self.drafted_row_label,
                    self.column_header, self.method)  # fmt: skip


class CaveatUpdate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int
    caveat: Caveat


class CaveatPlan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    updates: list[CaveatUpdate]
    unchanged: int
    problems: list[str]


def plan_caveats(targets: list[CaveatTarget], entries: list[CaveatEntry]) -> CaveatPlan:
    """Pure. Every non-blank line must name exactly one verified row; otherwise `problems` is
    non-empty and `updates` is empty - apply nothing."""
    by_key = {t.key: t for t in targets}
    problems: list[str] = []
    updates: list[CaveatUpdate] = []
    unchanged = 0
    seen: set[tuple] = set()
    for entry in entries:
        place = (
            f"PDF p.{entry.pdf_page}" if entry.pdf_page else f"{entry.sheet_name}!{entry.cell_ref}"
        )
        where = f"{place} {entry.row_label!r} | {entry.column_header!r}"
        if entry.key in seen:
            problems.append(f"{where}: listed twice")
            continue
        seen.add(entry.key)
        if entry.blank:
            continue
        target = by_key.get(entry.key)
        if target is None:
            problems.append(f"{where}: no verified row with this page or cell, label and column")
            continue
        try:
            caveat = Caveat(printed=entry.caveat_printed.strip(), kind=entry.caveat_kind,
                            pdf_page=entry.caveat_pdf_page, cell=entry.caveat_cell)  # fmt: skip
        except ValidationError as exc:
            problems.append(f"{where}: {exc.errors()[0]['msg']}")
            continue
        if target.caveat == caveat:
            unchanged += 1
        elif target.caveat is not None:
            problems.append(f"{where}: already has a different caveat; not overwritten")
        else:
            updates.append(CaveatUpdate(id=target.id, caveat=caveat))
    return CaveatPlan(updates=[] if problems else updates, unchanged=unchanged, problems=problems)


CAVEAT_COLUMNS = [
    "pdf_page",
    "sheet_name",
    "cell_ref",
    "row_label",
    "column_header",
    "method",
    "verified_label",
    "caveat_printed",
    "caveat_kind",
    "caveat_pdf_page",
    "caveat_cell",
]

APPLY_CAVEAT = """
update peer_disclosures set
    caveat_printed = %(printed)s,
    caveat_kind = %(kind)s,
    caveat_pdf_page = %(pdf_page)s,
    caveat_cell = %(cell)s,
    caveat_by = %(caveat_by)s,
    caveat_at = %(caveat_at)s
where id = %(id)s and verified_at is not null and caveat_printed is null
"""


def caveat_phrase(kind: str | None) -> str:
    return "partly estimated, as the report states:" if kind == "estimate" else (
        f"{kind} note printed with the figure:")  # fmt: skip


def attached_caveats(turn_payloads: list[dict]) -> list[str]:
    """One line per caveat on a figure that `lookup_peer_disclosure` returned this turn, each
    once, for the loop to attach to the reply in code."""
    lines: list[str] = []
    for payload in turn_payloads:
        if payload.get("tool") != "lookup_peer_disclosure" or payload.get("status") != "ok":
            continue
        for figure in (payload.get("data") or {}).get("figures", []):
            if not figure.get("caveat_printed"):
                continue
            line = (
                f"{figure['company']}, {figure['row_label']} [{figure['column_header']}]: "
                f'{caveat_phrase(figure.get("caveat_kind"))} "{figure["caveat_printed"]}" '
                f"({figure.get('caveat_citation')})."
            )
            if line not in lines:
                lines.append(line)
    return lines


__all__ = [
    "APPLY_CAVEAT",
    "CAVEAT_COLUMNS",
    "Caveat",
    "CaveatEntry",
    "CaveatKind",
    "CaveatPlan",
    "CaveatTarget",
    "CaveatUpdate",
    "attached_caveats",
    "caveat_phrase",
    "has_emissions_figure",
    "plan_caveats",
]
