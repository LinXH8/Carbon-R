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
    period_pdf_page: int = Field(ge=1)
    period_printed_page: int | None
    url: str
    url_note: str | None
    local_filename: str = Field(pattern=r"^peer/.+\.pdf$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    page_count: int = Field(ge=1)
    licence_note: str
    notes: str

    @model_validator(mode="after")
    def _period(self) -> PeerSource:
        if self.period_end <= self.period_start:
            raise ValueError(f"{self.local_filename}: period ends before it starts")
        return self

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
}


__all__ = [
    "COMPANY_SLUGS",
    "PEER_SOURCES",
    "PRINTED_PAGE_OFFSETS",
    "TABLE_PAGES",
    "DocumentKind",
    "PeerSource",
    "PeerSources",
    "Sector",
    "load_peer_sources",
]
