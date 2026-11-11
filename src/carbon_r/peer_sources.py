"""The peer reports (Tier 2 Block 4 Part B): what was downloaded, and how each is registered.

`data/peer_sources.json` is the committed record of the 14 files, read from each report
itself in B0 (`scripts/peer_intake.py`). This module validates it and turns each entry into
a source-registry record, so a fresh clone - which has the JSON but never the PDFs - can
still register them, the same reason the GHG Protocol entries in `carbon_r.sources` are
literals.

The reports are copyright of their issuers. They stay in git-ignored `data/raw/peer/`; the
chunks live only in the project's own database, and a public deployment would cite them
rather than display them.

`report_year` is the calendar year in which the reporting period ENDS. MPACT and MLT report
on 1 April to 31 March, so their FY2025/26 reports are 2026; FLCT's year ends 30 September,
so its FY2025 is 2025. A search filtered on a year therefore matches on that convention, and
every passage also carries the period's own dates so a reply can state the real period.

Two entries (Tier 3 Block 1) are issuers' workbooks, not reports: FLCT's ESG Databook and
CICT's data pack, where those companies' GHG figures are printed - their reports print none
as a table. A workbook entry cites a sheet and cell where a report cites a page
(`period_cell` for its period, `WORKBOOK_TABLES` for its tables), has no page count, and is
not part of the prose corpus.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from carbon_r.config import PROJECT_ROOT

PEER_SOURCES = PROJECT_ROOT / "data" / "peer_sources.json"

Sector = Literal["bank", "reit", "industrial", "energy"]  # Carbon R's labels, not SGX's
DocumentKind = Literal["sustainability_report", "data_pack", "databook", "annual_report"]


class PeerSource(BaseModel):
    """One file of one company's report, as committed in data/peer_sources.json."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    company: str
    sector: Sector
    document_kind: DocumentKind
    period_start: date
    period_end: date
    # stated: both dates read from the report. from_companion: taken from the companion
    # report named in `notes`. start_assumed: only the end date is stated.
    period_basis: Literal["stated", "from_companion", "start_assumed"]
    # Where the period is stated: a PDF page, or for a workbook "Sheet!A1".
    period_pdf_page: int | None = Field(ge=1)
    period_cell: str | None = Field(default=None, pattern=r"^.+![A-Z]{1,3}[1-9][0-9]*$")
    period_printed_page: int | None
    url: str
    url_note: str | None
    local_filename: str = Field(pattern=r"^peer/.+\.(pdf|xlsx)$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    page_count: int | None = Field(ge=1)
    licence_note: str
    notes: str
    # Set for files added after B0 (2026-10-23), which recorded none per file.
    retrieved_on: date | None = None

    @model_validator(mode="after")
    def _period(self) -> PeerSource:
        if self.period_end <= self.period_start:
            raise ValueError(f"{self.local_filename}: period ends before it starts")
        if self.is_workbook:
            if self.period_cell is None or self.period_pdf_page is not None:
                raise ValueError(f"{self.local_filename}: a workbook states its period in a cell")
            if self.page_count is not None or self.period_printed_page is not None:
                raise ValueError(f"{self.local_filename}: a workbook has no pages")
        elif self.period_pdf_page is None or self.page_count is None or self.period_cell:
            raise ValueError(f"{self.local_filename}: a PDF states its period on a page")
        return self

    @property
    def is_workbook(self) -> bool:
        return self.local_filename.endswith(".xlsx")

    @property
    def report_year(self) -> int:
        """The year the reporting period ends (see the module docstring)."""
        return self.period_end.year

    @property
    def source_id(self) -> str:
        suffix = _KIND_SUFFIX[self.document_kind]
        return f"peer_{COMPANY_SLUGS[self.company]}_{self.report_year}{suffix}"

    @property
    def title(self) -> str:
        start, end = self.period_start, self.period_end
        return (
            f"{self.company} {_KIND_TITLE[self.document_kind]} "
            f"({start.day} {start:%b %Y} to {end.day} {end:%b %Y})"
        )


_COLUMN = r"^[A-Z]{1,3}$"
_CELL_REF = r"^[A-Z]{1,3}[1-9][0-9]*$"


class WorkbookTable(BaseModel):
    """One table on one sheet, as the author declares it: where its labels and year headers
    are, which rows it spans, and where its unit is printed - in a column beside each row,
    or once in a title cell (FLCT prints "ktCO2e" in the table title only)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sheet: str
    label_column: str = Field(pattern=_COLUMN)
    header_row: int = Field(ge=1)
    first_row: int = Field(ge=1)
    last_row: int = Field(ge=1)
    # A heading that names the table (FLCT's region, e.g. "Singapore (including corporate
    # office)"), recorded as "Heading > Row label", the page drafter's convention, so rows
    # with one label in several tables say which table they are from.
    heading_cell: str | None = Field(default=None, pattern=_CELL_REF)
    unit_column: str | None = Field(default=None, pattern=_COLUMN)
    unit_printed: str | None = None
    unit_cell: str | None = Field(default=None, pattern=_CELL_REF)

    @model_validator(mode="after")
    def _shape(self) -> WorkbookTable:
        if not self.header_row < self.first_row <= self.last_row:
            raise ValueError(f"{self.sheet}: rows must run header < first <= last")
        by_column = self.unit_column is not None
        by_title = self.unit_printed is not None and self.unit_cell is not None
        if by_column == by_title or (not by_title and (self.unit_printed or self.unit_cell)):
            raise ValueError(
                f"{self.sheet}: declare the unit by a unit column, or by unit_printed and the "
                "unit_cell it is printed in - one, not both"
            )
        return self

    def holds(self, sheet: str, cell_ref: str) -> bool:
        row = int("".join(ch for ch in cell_ref if ch.isdigit()))
        return sheet == self.sheet and self.first_row <= row <= self.last_row


class PeerSources(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int
    drafted: str
    note: str
    sources: list[PeerSource]


def load_peer_sources(path: Path = PEER_SOURCES) -> PeerSources:
    return PeerSources.model_validate_json(path.read_text())


# Short, stable ids. Written out rather than derived from the name, so a renamed company in
# the JSON is a test failure, not a silently new source id.
COMPANY_SLUGS: dict[str, str] = {
    "DBS": "dbs",
    "OCBC": "ocbc",
    "UOB": "uob",
    "CapitaLand Ascendas REIT": "clar",
    "Mapletree Pan Asia Commercial Trust": "mpact",
    "Mapletree Logistics Trust": "mlt",
    "Frasers Logistics & Commercial Trust": "flct",
    "CapitaLand Integrated Commercial Trust": "cict",
    "Keppel": "keppel",
    "ST Engineering": "stengg",
    "Seatrium": "seatrium",
    "Sembcorp": "sembcorp",
}

_KIND_SUFFIX: dict[str, str] = {
    "sustainability_report": "",
    "annual_report": "",
    "data_pack": "_datapack",
    "databook": "_databook",
}

_KIND_TITLE: dict[str, str] = {
    "sustainability_report": "Sustainability Report",
    "annual_report": "Annual Report",
    "data_pack": "ESG Data Pack",
    "databook": "Sustainability Databook",
}

# The page-number offset (printed page = PDF page - offset) for each report, set ONLY where
# `scripts/ingest_peer.py --preview` shows one offset matching at least
# `chunking.OFFSET_MATCH_THRESHOLD` of the pages that carry a readable footer number. A report
# not listed has no offset, and its citations give the PDF page and say so.
#
# Set 2026-10-28 from the preview (candidates -2..+2, threshold 80%), matches over pages with
# a readable footer number. Spot-checked by hand: DBS PDF p.79 ends its footer with "79";
# OCBC PDF p.23 prints "OCBC Sustainability Report 2025 22". Left out, so cited by PDF page:
# MPACT and Sembcorp (two printed pages per PDF page: 2 x PDF - 4 and 2 x PDF - 3), CICT
# (footers split between -1 and -4, best 30/65), and both data books (13/69 and 14/39 pages
# carry any number at all).
PRINTED_PAGE_OFFSETS: dict[str, int] = {
    "peer_dbs_2025": 0,  # 126/126
    "peer_ocbc_2025": 1,  # 142/142
    "peer_uob_2025": 1,  # 188/188
    "peer_clar_2025": 2,  # 57/58
    "peer_mlt_2026": 1,  # 59/59
    "peer_flct_2025": 2,  # 46/47
    "peer_keppel_2025": -1,  # 94/99 - printed page = PDF page + 1
    "peer_stengg_2025": 0,  # 75/76
    "peer_seatrium_2025": 2,  # 110/111
}

# The PDF pages whose emissions tables are drafted into `peer_disclosures` (Part B, 2a), as
# page images (`scripts/draft_peer_disclosures.py`). Set by hand: from each entry's `notes` in
# data/peer_sources.json and the script's `scan`, which uses the text layer only to find
# pages, never to read a figure. A report not listed has not been drafted.
TABLE_PAGES: dict[str, list[int]] = {
    # notes: "Operational GHG pages include PDF p.74, 79". p.74 holds an intensity chart and no
    # table of absolute figures (the model said so; confirmed by viewing the page). Kept so the
    # record shows the page was read.
    "peer_dbs_2025": [74, 79],
    # Data pack Table 1.1, Singapore (p.7-8) and Total Portfolio (p.20-21), each split over two
    # pages. The scan's other hits: Australia (11-12), UK & EU (14-15), United States (17-18),
    # left for later; p.59 (IFRS S2 index) and p.66 (SGX core metrics index) are not tables of
    # figures.
    "peer_clar_2025_datapack": [7, 8, 20, 21],
    # notes: "GHG figures appear in the AR (e.g. PDF p.37-40)". The scan finds 37, 39, 40; p.38
    # (two-up, printed 72-73) is a chart of intensity trajectories, no absolute figures.
    "peer_sembcorp_2025": [37, 39, 40],
    # Chosen by the author by viewing each page (2b, 31 Oct). p.58 is the operational table.
    # p.23 (Net-Zero Progress) and p.28 (Oil & Gas) print absolute financed emissions for 2024
    # only, so every cell is expected to drop as a comparative; kept so the record shows they
    # were read. Dropped after viewing: p.24 (Power), p.33 (Steel), p.36 (Aviation), intensity
    # only.
    "peer_ocbc_2025": [23, 28, 58],
    # Chosen by the author by viewing each page (2b, 31 Oct). p.135 Scope 1, p.136 Scope 2 by
    # country, Scope 3 and totals. p.127 dropped: intensity and $ only. p.189 (the assurance
    # statement's selected GHG information) prints Scope 1, 2, 3 and totals inline, with no
    # year column: its year is declared in PAGE_YEARS.
    "peer_uob_2025": [135, 136, 189],
    # Confirmed by the author, 2026-11-10. Appendix 1 Environmental Data (printed p.45): Scope 1,
    # Scope 2 both methods, Scope 3 categories 1, 2, 5, 13. Not drafted: p.24 repeats the
    # Scope 3 categories as a smaller table; p.23 and p.29 are prose and charts.
    "peer_mlt_2026": [46],
    # Confirmed by the author, 2026-11-10. Two printed pages per PDF page. p.34 (printed 65)
    # Scope 1 by country; p.35 (printed 66) Scope 2 both methods by country and Scope 3
    # categories. No group totals in the table: they are printed only in prose (p.16). p.33
    # is methodology prose.
    "peer_mpact_2026": [34, 35],
    # Confirmed by the author, 2026-11-10. p.45 (printed 46) Scope 1 and 2 table and the Scope 3
    # table by category; p.50 (printed 51) Environmental Data, which repeats the totals.
    # Not drafted: p.47, emissions of Keppel-managed REITs and funds (a different boundary);
    # the scan's other hits are prose and charts.
    "peer_keppel_2025": [45, 50],
    # Confirmed by the author, 2026-11-10. "Our Environmental Metrics".
    # The unit ('000 tCO2e) is printed in the section heading only.
    # Footnote 1: the last three months of 2025 are estimates. p.29 is a chart.
    "peer_stengg_2025": [37],
    # Confirmed by the author, 2026-11-10. Scope 1 and 2 by country with totals. Scope 3 is an
    # infographic only (p.58), not drafted; p.104 is the SGX core metrics index.
    "peer_seatrium_2025": [70],
    # Confirmed by the author, 2026-11-10. The group GHG table: Scope 1, Scope 2 both methods,
    # totals, Scope 3 categories 1-15. p.30 is the SGX core metrics index.
    "peer_seatrium_2025_databook": [13],
}

# Table pages the author has checked continue the table on the page before, as
# {source_id: {page: page_before}}. Python may then carry that page's single CO2e unit onto
# cells printed without one (`peer_disclosure.continued_unit`); the title test it replaces
# failed here because the model titled the two pages differently.
TABLE_CONTINUES: dict[str, dict[int, int]] = {
    # "Sustainability in Numbers" runs over both pages; its unit heading, "Greenhouse gas (GHG)
    # emissions (thousand tCO2e)", is printed on p.135 only. Declared by the author, 1 Nov.
    "peer_uob_2025": {136: 135},
}


# The tables read by code from a workbook source (`carbon_r.peer_workbook`), as
# {source_id: [WorkbookTable, ...]}: the sheet, its label column and year-header row, the
# rows the table spans, and where its unit is printed. Declared by the author before any
# cell is read; the author's inventory then lists each report-year cell.
#
# Proposed by Claude on 2026-11-11 from the workbooks' labels and headers (values not read);
# AWAITING THE AUTHOR'S CHECK of sheet, rows and units before any inventory is filled.
_FLCT_S3 = "Climate Action - Scope 3"
WORKBOOK_TABLES: dict[str, list[WorkbookTable]] = {
    # ENVIRONMENTAL, "GREENHOUSE GAS EMISSIONS": Scope 1, Scope 2 both methods, Scope 3 and
    # its categories, in rows 7-17, with each row's unit in column B. Row 18 is intensity
    # (kgCO2e/m2/month) and is left out. Years are column headers in row 6; 2025 is column I.
    "peer_cict_2025_datapack": [
        WorkbookTable(
            sheet="ENVIRONMENTAL", label_column="A", header_row=6, first_row=7, last_row=17,
            unit_column="B",
        ),
    ],
    "peer_flct_2025_databook": [
        # Scopes 1 and 2 and their totals, rows 9-13; row 14 is intensity. The unit is printed
        # once, in the title (B7). Not proposed: the by-source tables (rows 19-22 and 25-28),
        # a split by energy source rather than by scope.
        WorkbookTable(
            sheet="Climate Action - Scopes 1 & 2", label_column="B", header_row=8,
            first_row=9, last_row=13, unit_printed="ktCO2e", unit_cell="B7",
        ),
        # Scope 3 by category, one table per region, each headed by its region (the heading
        # shares the year-header row). Unit in the sheet title (B7). Not proposed: the "FLCT
        # (all regions)" table (rows 56-58), whose total row prints "(tCO2e)" against the
        # title's ktCO2e and whose category row prints no unit.
        WorkbookTable(
            sheet=_FLCT_S3, label_column="B", header_row=8, first_row=9, last_row=18,
            heading_cell="B8", unit_printed="ktCO2e", unit_cell="B7",
        ),
        WorkbookTable(
            sheet=_FLCT_S3, label_column="B", header_row=20, first_row=21, last_row=30,
            heading_cell="B20", unit_printed="ktCO2e", unit_cell="B7",
        ),
        WorkbookTable(
            sheet=_FLCT_S3, label_column="B", header_row=32, first_row=33, last_row=42,
            heading_cell="B32", unit_printed="ktCO2e", unit_cell="B7",
        ),
        WorkbookTable(
            sheet=_FLCT_S3, label_column="B", header_row=44, first_row=45, last_row=54,
            heading_cell="B44", unit_printed="ktCO2e", unit_cell="B7",
        ),
    ],
}  # fmt: skip


# Table pages with no year column whose year the author has read from the page's text, as
# {source_id: {page: year}}. `to_disclosures` gives that year only to a cell whose header names
# no year at all (`peer_disclosure.cell_year`), and each such row says so in its notes.
PAGE_YEARS: dict[str, dict[int, int]] = {
    # The assurance statement's selected GHG information: 2025 Scope 1, 2, 3 and totals,
    # inline. Declared by the author, 31 Oct.
    "peer_uob_2025": {189: 2025},
    # The Scope 3 table by category prints no year column; the prose beside it gives the same
    # total for 2025. The page's Scope 1 and 2 table has year columns and is unaffected.
    # Declared by the author, 2026-11-10.
    "peer_keppel_2025": {45: 2025},
}


__all__ = [
    "COMPANY_SLUGS",
    "PAGE_YEARS",
    "PEER_SOURCES",
    "PRINTED_PAGE_OFFSETS",
    "TABLE_CONTINUES",
    "TABLE_PAGES",
    "WORKBOOK_TABLES",
    "WorkbookTable",
    "DocumentKind",
    "PeerSource",
    "PeerSources",
    "Sector",
    "load_peer_sources",
]
