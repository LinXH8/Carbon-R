"""Load emission factors into the database. Idempotent: safe to re-run.

Applies every migration in db/migrations/ in filename order, then upserts the factor
rows below. Re-running changes nothing — the same property Tier 2's loader needs, worth
establishing on day one.

    uv run python scripts/seed_factors.py        (or: make seed)
"""

import sys
from datetime import date
from pathlib import Path

import psycopg

from carbon_r.config import PROJECT_ROOT
from carbon_r.db import DatabaseNotConfiguredError, connect

MIGRATIONS_DIR = PROJECT_ROOT / "db" / "migrations"

# Singapore grid emission factor (location-based, Scope 2).
#
# Transcribed by hand from the primary source:
#   Energy Market Authority, Singapore Energy Statistics 2025, Table 2.4
#   ("Electricity Grid Emission Factor and Upstream Fugitive Methane Emission Factor,
#    2005 - 2024"), row "Grid Emission Factor (GEF) (kg CO2 / kWh)".
#   Local copy: data/raw/ema/SES_2025_tabular.xlsx, sheet T2.4 (git-ignored).
#
# EMA's definition, verbatim from the table's footnote 1:
#   "The Grid Emission Factor (GEF) measures the average CO2 emissions emitted per unit
#    of net electricity generation in the system by all grid-connected power units."
#
# Transcribed as literals rather than parsed from the workbook at runtime: the workbook is
# git-ignored, and a fresh clone must still be able to seed. See SOURCES.md for why the
# data.gov.sg "Average Operating Margin" series is NOT used as a source here.
EMA_GRID_EMISSION_FACTOR = {
    2005: "0.526",
    2006: "0.530",
    2007: "0.505",
    2008: "0.497",
    2009: "0.497",
    2010: "0.508",
    2011: "0.509",
    2012: "0.478",
    2013: "0.439",
    2014: "0.428",
    2015: "0.422",
    2016: "0.424",
    2017: "0.421",
    2018: "0.421",
    2019: "0.409",
    2020: "0.407",
    2021: "0.409",
    2022: "0.417",
    2023: "0.412",
    2024: "0.402",
}

EMA_SOURCE = {
    "category": "electricity_grid_sg",
    "unit": "kg CO2 / kWh",  # EMA's own unit string, reproduced exactly
    "publisher": "EMA",
    "source_document": "Singapore Energy Statistics 2025, Table 2.4",
    "source_url": "https://www.ema.gov.sg/resources/singapore-energy-statistics/chapter2",
    "source_page": "Table 2.4",
    "jurisdiction": "SG",
    "verified": True,
    "retrieved_at": date(2026, 10, 15),
    "notes": (
        "Grid Emission Factor (GEF): average CO2 emissions per unit of net electricity "
        "generation by all grid-connected units. Location-based. Transcribed from the SES "
        "2025 tabular workbook, sheet T2.4."
    ),
}

UPSERT = """
insert into emission_factors (
    category, factor, unit, year, publisher, source_document, source_url,
    source_page, retrieved_at, jurisdiction, verified, notes
) values (
    %(category)s, %(factor)s, %(unit)s, %(year)s, %(publisher)s, %(source_document)s,
    %(source_url)s, %(source_page)s, %(retrieved_at)s, %(jurisdiction)s, %(verified)s,
    %(notes)s
)
on conflict (category, unit, year) do update set
    factor          = excluded.factor,
    publisher       = excluded.publisher,
    source_document = excluded.source_document,
    source_url      = excluded.source_url,
    source_page     = excluded.source_page,
    retrieved_at    = excluded.retrieved_at,
    jurisdiction    = excluded.jurisdiction,
    verified        = excluded.verified,
    notes           = excluded.notes
returning (xmax = 0) as inserted
"""


def rows() -> list[dict]:
    return [
        {**EMA_SOURCE, "year": year, "factor": factor}
        for year, factor in sorted(EMA_GRID_EMISSION_FACTOR.items())
    ]


def apply_migrations(conn: psycopg.Connection) -> list[Path]:
    applied = sorted(MIGRATIONS_DIR.glob("*.sql"))
    for path in applied:
        conn.execute(path.read_text())
    return applied


def main() -> int:
    try:
        with connect() as conn:
            for path in apply_migrations(conn):
                print(f"migration  {path.name}")

            inserted = updated = 0
            for row in rows():
                result = conn.execute(UPSERT, row).fetchone()
                if result is not None and result[0]:
                    inserted += 1
                else:
                    updated += 1

            total = conn.execute(
                "select count(*) from emission_factors where category = %s",
                (EMA_SOURCE["category"],),
            ).fetchone()
    except DatabaseNotConfiguredError as exc:
        print(f"{exc}", file=sys.stderr)
        return 2
    except psycopg.Error as exc:
        print(f"Database error: {exc}", file=sys.stderr)
        return 1

    print(f"\n{EMA_SOURCE['category']}: {inserted} inserted, {updated} unchanged/updated")
    print(f"  {total[0]} rows now present, {EMA_SOURCE['publisher']}, verified=True")
    print(f"  source: {EMA_SOURCE['source_document']}")
    print(f"  years:  {min(EMA_GRID_EMISSION_FACTOR)}-{max(EMA_GRID_EMISSION_FACTOR)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
