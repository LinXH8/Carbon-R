"""The source registry: every document Carbon R reads, as literals.

Literals rather than rows discovered on disk, for the same reason `scripts/seed_factors.py`
carries its factors as literals: `data/raw/` is git-ignored, and a fresh clone must still be
able to register its sources. The sha256 is what was measured from the local copy when it
was registered; an ingest that finds different bytes refuses to read them.

Each licence below was read from the document itself or the publisher's own page - see
SOURCES.md for the verbatim wording. `None` means not yet read, which is not the same as
"no restriction".

The GHG Protocol entries matter most here. ghgprotocol.org's Terms of Use ("Last Updated:
February 2023") restrict reproducing or displaying GHG Protocol Content "for any public or
commercial purpose" and extracting it with "data mining technology". The Corporate and Scope
3 Standards carry a bare copyright notice; only the Scope 2 Guidance has an open licence,
and it is NonCommercial-NoDerivatives. So the methodology corpus is local only: chunks live
in the project's own database, no GHG Protocol text is committed, and a public deployment
cites passages rather than displaying them. Permission is to be requested from GHG Protocol.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from carbon_r.config import PROJECT_ROOT

SourceKind = Literal["methodology", "factor_dataset", "peer_report"]
VerificationStatus = Literal["verified from primary", "secondary only"]

GHGP_TERMS = (
    "ghgprotocol.org Terms of Use (Last Updated: February 2023): no reproduction, display "
    "or distribution for any public or commercial purpose, and no extraction by data mining. "
    "Local use only; never committed or publicly displayed."
)


class SourceRecord(BaseModel):
    """One registry row. Mirrors `db/migrations/0003_sources.sql`, plus the local path."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    title: str
    publisher: str
    year: int | None
    url: str
    licence: str | None
    retrieved_at: date
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    kind: SourceKind
    verification_status: VerificationStatus
    printed_page_offset: int | None = None
    notes: str | None = None
    # Relative to the project root. Not stored: a path is a fact about this checkout.
    local_path: str

    @property
    def path(self) -> Path:
        return PROJECT_ROOT / self.local_path

    def row(self) -> dict:
        return self.model_dump(exclude={"local_path"})


REGISTRY: tuple[SourceRecord, ...] = (
    SourceRecord(
        id="ghgp_corporate",
        title="The Greenhouse Gas Protocol: A Corporate Accounting and Reporting Standard "
        "(Revised Edition)",
        publisher="WRI / WBCSD",
        year=2004,
        url="https://ghgprotocol.org/corporate-standard",
        licence="Copyright © World Resources Institute and World Business Council for "
        "Sustainable Development, March 2004; no licence granted in the document. " + GHGP_TERMS,
        retrieved_at=date(2026, 10, 16),
        sha256="cfcda4dd20a0b0936b30e5e5c7c635c26efdfa770d29c1004f549bf082c0fe3c",
        kind="methodology",
        verification_status="verified from primary",
        printed_page_offset=2,
        local_path="data/raw/ghgp/ghg-protocol-revised.pdf",
    ),
    SourceRecord(
        id="ghgp_scope2",
        title="GHG Protocol Scope 2 Guidance",
        publisher="WRI",
        year=2015,
        url="https://ghgprotocol.org/scope-2-guidance",
        licence="Copyright 2015 World Resources Institute; Creative Commons "
        "Attribution-NonCommercial-NoDerivative Works 3.0, as stated in the document. "
        + GHGP_TERMS,
        retrieved_at=date(2026, 10, 16),
        sha256="b4f17030e5d723d5b9b65ed85592c5449dafe4f798eb845ed9db0465893c894a",
        kind="methodology",
        verification_status="verified from primary",
        printed_page_offset=2,
        local_path="data/raw/ghgp/Scope 2 Guidance.pdf",
    ),
    SourceRecord(
        id="ghgp_scope3",
        title="Corporate Value Chain (Scope 3) Accounting and Reporting Standard",
        publisher="WRI / WBCSD",
        year=2011,
        url="https://ghgprotocol.org/sites/default/files/standards/"
        "Corporate-Value-Chain-Accounting-Reporing-Standard_041613_2.pdf",
        licence="Copyright © World Resources Institute and World Business Council for "
        "Sustainable Development, September 2011; no licence granted in the document. "
        + GHGP_TERMS,
        retrieved_at=date(2026, 10, 22),
        sha256="c748e35216aedd90fff3dc580b233f37cbaf9e6cefd211c88e92965bded54b91",
        kind="methodology",
        verification_status="verified from primary",
        printed_page_offset=2,
        notes="The PDF link on https://ghgprotocol.org/corporate-value-chain-scope-3-standard. "
        "The site also lists a 'List of Corrections for Scope 3 Standard', not ingested.",
        local_path="data/raw/ghgp/Scope 3 Standard.pdf",
    ),
    SourceRecord(
        id="ema_ses_2025",
        title="Singapore Energy Statistics 2025, Table 2.4",
        publisher="EMA",
        year=2025,
        url="https://www.ema.gov.sg/resources/singapore-energy-statistics/chapter2",
        licence=None,
        retrieved_at=date(2026, 10, 15),
        sha256="a857b1bf1645a80335b50932be8f29047c2e47ecd7a18925ec1d88497503c563",
        kind="factor_dataset",
        verification_status="verified from primary",
        notes="Licence not yet read from EMA's own terms.",
        local_path="data/raw/ema/SES_2025_tabular.xlsx",
    ),
    SourceRecord(
        id="desnz_2025",
        title="UK Government GHG Conversion Factors for Company Reporting 2025, full set",
        publisher="Department for Energy Security and Net Zero",
        year=2025,
        url="https://www.gov.uk/government/publications/"
        "greenhouse-gas-reporting-conversion-factors-2025",
        licence="Open Government Licence v3.0, per the gov.uk publication page",
        retrieved_at=date(2026, 10, 18),
        sha256="c08f24dac724a7598f54a7c7b28ad21fa88ca48d8ebd98cb9e172ffc184b5e5a",
        kind="factor_dataset",
        verification_status="verified from primary",
        local_path="data/raw/desnz/ghg-conversion-factors-2025-full-set.xlsx",
    ),
    SourceRecord(
        id="desnz_2026",
        title="UK Government GHG Conversion Factors for Company Reporting 2026, full set",
        publisher="Department for Energy Security and Net Zero",
        year=2026,
        url="https://www.gov.uk/government/publications/"
        "greenhouse-gas-reporting-conversion-factors-2026",
        licence="Open Government Licence v3.0, per the gov.uk publication page",
        retrieved_at=date(2026, 10, 18),
        sha256="b5c4f2f86cdc74707337b2fb013b44ded189a1b528f5e29ea99856ba65ae0205",
        kind="factor_dataset",
        verification_status="verified from primary",
        local_path="data/raw/desnz/ghg-conversion-factors-2026-full-set.xlsx",
    ),
)

BY_ID: dict[str, SourceRecord] = {record.id: record for record in REGISTRY}

METHODOLOGY_IDS: tuple[str, ...] = tuple(r.id for r in REGISTRY if r.kind == "methodology")

UPSERT = """
insert into sources (id, title, publisher, year, url, licence, retrieved_at, sha256, kind,
                     verification_status, printed_page_offset, notes)
values (%(id)s, %(title)s, %(publisher)s, %(year)s, %(url)s, %(licence)s, %(retrieved_at)s,
        %(sha256)s, %(kind)s, %(verification_status)s, %(printed_page_offset)s, %(notes)s)
on conflict (id) do update set
    title = excluded.title, publisher = excluded.publisher, year = excluded.year,
    url = excluded.url, licence = excluded.licence, retrieved_at = excluded.retrieved_at,
    sha256 = excluded.sha256, kind = excluded.kind,
    verification_status = excluded.verification_status,
    printed_page_offset = excluded.printed_page_offset, notes = excluded.notes
returning (xmax = 0) as inserted
"""


__all__ = ["BY_ID", "METHODOLOGY_IDS", "REGISTRY", "UPSERT", "SourceRecord"]
