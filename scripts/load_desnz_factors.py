"""Load UK DESNZ fuel combustion factors. Idempotent, and checkable against the workbook.

    uv run python scripts/load_desnz_factors.py            (or: make seed-desnz)
    uv run python scripts/load_desnz_factors.py --verify    re-read the xlsx, no database

Why literals rather than parsing at load time
---------------------------------------------
Same reason as `seed_factors.py`: the workbooks are git-ignored, and a fresh clone must
still be able to seed. The literals below are the loaded values; `--verify` re-reads the
publisher's own spreadsheet and diffs against them, so the transcription is checkable
whenever the file is present rather than trusted once.

The workbook stores IEEE-754 doubles, so cell D35 reads `2939.3609499999998` where DESNZ
publishes `2939.36095`. `--verify` compares at the published precision (5 dp); the residue
is float noise of order 1e-13 and is reported if it ever exceeds that.

Which rows, and why
-------------------
Sheet `Fuels`, column D (`kg CO2e`) - the total of CO2, CH4 and N2O. Columns E/F/G break
that total down but are each already expressed *in CO2e*, so there is no kg-CO2 column to
pick instead. The sheet states its own scope in cell B6: `Scope 1`.

NOT the `WTT- fuels` sheet: well-to-tank is upstream Scope 3, and loading it beside the
combustion factor would let a lookup return the wrong one.

**100% mineral, not average biofuel blend.** DESNZ's own guidance (cell A12) tells
forecourt purchasers to use the average blend - but that is advice about UK forecourt
fuel, which carries a mandated biofuel content. No Singapore road-fuel blending mandate
was found, and NEA has capped FAME content in pump petrol and diesel since 1 July 2019, so
the mineral rows are the closer match for Singapore pump fuel. See docs/SOURCES.md: this is a
documented assumption, NOT verified - NEA's own fuel specification has not been read.
Choosing the blend would also pull in DESNZ's `Outside of scopes` requirement to report
the biogenic portion separately, an obligation attached to a blend our documents do not
claim to contain.

**Gross vs net CV does not apply to these rows.** Each fuel is published in four units -
tonnes, litres, kWh (Net CV), kWh (Gross CV) - and only the two kWh rows carry a CV basis.
The mass and volume rows have a single value. No kWh row is loaded.

**LPG is loaded per tonne only.** The sheet publishes LPG in tonnes and litres and has no
per-kg row; our invoices give kg, so `calculate_emissions` converts kg to tonnes as an
explicit, cited step. The per-litre LPG row is deliberately not loaded: nothing in this
project measures LPG by volume, and an unused row is a row a lookup can land on by
mistake.

**No back series.** A DESNZ publication is a standalone set for its own year, not a
history. Two publications are loaded, so the table holds 2025-2026 and nothing earlier -
which is why an activity year of 2024 correctly returns `no_results`.
"""

import argparse
import sys
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

import psycopg

from carbon_r.config import PROJECT_ROOT
from carbon_r.db import DatabaseNotConfiguredError, connect
from carbon_r.xlsx import SheetNotFoundError, read_sheet

MIGRATIONS_DIR = PROJECT_ROOT / "db" / "migrations"
WORKBOOK_DIR = PROJECT_ROOT / "data" / "raw" / "desnz"

SHEET = "Fuels"
TOTAL_COLUMN = "D"  # 'kg CO2e': the total of CO2, CH4 and N2O
PUBLISHED_PLACES = Decimal("0.00001")  # DESNZ publishes these rows to 5 dp
FLOAT_NOISE = Decimal("0.000000001")  # anything larger is a transcription error, not IEEE-754

PUBLISHER = "Department for Energy Security and Net Zero"
LICENCE = "Open Government Licence v3.0"

# One entry per publication year. `source_url` is the publication page, not the file, so
# the citation stays valid when the asset path changes.
PUBLICATIONS = {
    2025: {
        "source_document": (
            "UK Government GHG Conversion Factors for Company Reporting 2025, "
            "full set, sheet 'Fuels'"
        ),
        "source_url": (
            "https://www.gov.uk/government/publications/"
            "greenhouse-gas-reporting-conversion-factors-2025"
        ),
        "workbook": "ghg-conversion-factors-2025-full-set.xlsx",
        "retrieved_at": date(2026, 10, 18),
    },
    2026: {
        "source_document": (
            "UK Government GHG Conversion Factors for Company Reporting 2026, "
            "full set, sheet 'Fuels'"
        ),
        "source_url": (
            "https://www.gov.uk/government/publications/"
            "greenhouse-gas-reporting-conversion-factors-2026"
        ),
        "workbook": "ghg-conversion-factors-2026-full-set.xlsx",
        "retrieved_at": date(2026, 10, 18),
    },
}

# The unit string is COMPOSED from two of the sheet's own cells - column D's header
# ('kg CO2e') over column C's unit ('tonnes', 'litres') - because DESNZ splits across two
# cells what EMA put in one. Both tokens are verbatim, including DESNZ's plural
# denominators, which is why these read 'per tonnes' rather than 'per tonne': the agent
# matches this string exactly, so it reproduces the publisher rather than tidying it.
UNIT_TONNES = "kg CO2e / tonnes"
UNIT_LITRES = "kg CO2e / litres"

# category -> (DESNZ fuel label, sheet unit, composed unit string, {year: (factor, row)})
#
# The row number is recorded so a person can open the workbook and find the cell. All six
# values were read with `--verify` and are reproduced by it.
FUEL_FACTORS: dict[str, dict] = {
    "fuel_lpg": {
        "fuel_label": "LPG",
        "sheet_unit": "tonnes",
        "unit": UNIT_TONNES,
        "notes": (
            "LPG combustion, all greenhouse gases as CO2e. DESNZ publishes LPG in tonnes "
            "and litres only; there is no per-kg row, so a kg activity is converted to "
            "tonnes as an explicit step before this factor is applied."
        ),
        "values": {2025: (Decimal("2939.36095"), 35), 2026: (Decimal("2939.36095"), 35)},
    },
    "fuel_diesel": {
        "fuel_label": "Diesel (100% mineral diesel)",
        "sheet_unit": "litres",
        "unit": UNIT_LITRES,
        "notes": (
            "Diesel combustion, all greenhouse gases as CO2e. The 100% mineral row, not "
            "'Diesel (average biofuel blend)': the blend figure assumes the UK's mandated "
            "biofuel content. See SOURCES.md - documented assumption, not verified."
        ),
        "values": {2025: (Decimal("2.66155"), 76), 2026: (Decimal("2.66155"), 76)},
    },
    "fuel_petrol": {
        "fuel_label": "Petrol (100% mineral petrol)",
        "sheet_unit": "litres",
        "unit": UNIT_LITRES,
        "notes": (
            "Petrol combustion, all greenhouse gases as CO2e. The 100% mineral row, not "
            "'Petrol (average biofuel blend)'. See SOURCES.md - documented assumption, "
            "not verified."
        ),
        "values": {2025: (Decimal("2.33984"), 100), 2026: (Decimal("2.35372"), 100)},
    },
}

# Every row loaded here is combusted fuel: Scope 1, and CO2e rather than CO2 only.
SCOPE = 1
METHOD = "combustion"
GAS_BASIS = "CO2e"
JURISDICTION = "UK"

UPSERT = """
insert into emission_factors (
    category, factor, unit, year, publisher, source_document, source_url,
    source_page, retrieved_at, jurisdiction, verified, notes, scope, method, gas_basis
) values (
    %(category)s, %(factor)s, %(unit)s, %(year)s, %(publisher)s, %(source_document)s,
    %(source_url)s, %(source_page)s, %(retrieved_at)s, %(jurisdiction)s, %(verified)s,
    %(notes)s, %(scope)s, %(method)s, %(gas_basis)s
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
    notes           = excluded.notes,
    scope           = excluded.scope,
    method          = excluded.method,
    gas_basis       = excluded.gas_basis
returning (xmax = 0) as inserted
"""


def rows() -> list[dict]:
    """Every factor row to load, in a stable order. Pure - no file, no database."""
    out: list[dict] = []
    for category, spec in FUEL_FACTORS.items():
        for year, (factor, sheet_row) in sorted(spec["values"].items()):
            publication = PUBLICATIONS[year]
            out.append(
                {
                    "category": category,
                    "factor": factor,
                    "unit": spec["unit"],
                    "year": year,
                    "publisher": PUBLISHER,
                    "source_document": publication["source_document"],
                    "source_url": publication["source_url"],
                    # A sheet reference, not a page: this is a spreadsheet source. Precise
                    # enough that a person can open the file and land on the cell.
                    "source_page": (
                        f"sheet '{SHEET}', row {sheet_row}, column {TOTAL_COLUMN} "
                        f"({spec['fuel_label']}, {spec['sheet_unit']})"
                    ),
                    "retrieved_at": publication["retrieved_at"],
                    "jurisdiction": JURISDICTION,
                    "verified": True,
                    "notes": f"{spec['notes']} Licence: {LICENCE}.",
                    "scope": SCOPE,
                    "method": METHOD,
                    "gas_basis": GAS_BASIS,
                }
            )
    return out


# --- verification against the publisher's own file --------------------------------------


def read_fuel_totals(path: Path) -> dict[tuple[str, str], tuple[Decimal, int]]:
    """`{(fuel label, sheet unit): (total kg CO2e, row)}` from one workbook's Fuels sheet.

    Two things make this less trivial than it looks.

    The fuel label **carries forward**: the sheet names a fuel once in column B and leaves
    it blank on that fuel's remaining unit rows. A reader that did not carry it forward
    would attribute LPG's litres row to whichever fuel was labelled last - which is exactly
    the mistake a first pass at this made, and the reason the row number is asserted in
    `verify`.

    Collection starts only **after a data header row** (`Activity | Fuel | Unit | ...`).
    Above the first one sit the sheet's own metadata and several paragraphs of guidance,
    and row 5 of that metadata is `Emissions source: | Fuels | Next publication date: |
    June 2026` - which a column-B reader takes for a fuel called "Fuels" with a total of
    "June 2026". Requiring a header first is what distinguishes the tables from the prose.
    """
    sheet = read_sheet(path, SHEET)
    totals: dict[tuple[str, str], tuple[Decimal, int]] = {}
    in_table = False
    fuel: str | None = None
    for number in sorted(sheet):
        cells = sheet[number]
        if cells.get(f"A{number}") == "Activity":  # a header: a table starts here
            in_table = True
            fuel = None
            continue
        if not in_table:
            continue
        label = cells.get(f"B{number}")
        if label:
            fuel = label
        unit = cells.get(f"C{number}")
        total = cells.get(f"{TOTAL_COLUMN}{number}")
        if not (fuel and unit and total):
            continue
        try:
            totals[(fuel, unit)] = (Decimal(total), number)
        except InvalidOperation:
            # A non-numeric cell inside a table body is a layout change, not a value.
            # Skipped rather than raised: `verify` reports anything it cannot find.
            continue
    return totals


def verify() -> int:
    """Diff every literal against the workbook. Returns a process exit code."""
    problems = 0
    checked = 0

    for year, publication in sorted(PUBLICATIONS.items()):
        path = WORKBOOK_DIR / publication["workbook"]
        print(f"\n{path.name}")
        if not path.exists():
            print(f"  MISSING - download it to {WORKBOOK_DIR.relative_to(PROJECT_ROOT)}/")
            problems += 1
            continue

        try:
            totals = read_fuel_totals(path)
        except (SheetNotFoundError, KeyError) as exc:
            print(f"  UNREADABLE: {exc}")
            problems += 1
            continue

        sheet = read_sheet(path, SHEET)
        stated_scope = sheet.get(6, {}).get("B6")
        stated_year = sheet.get(6, {}).get("F6")
        print(f"  sheet '{SHEET}' states scope {stated_scope!r}, year {stated_year!r}")
        if stated_year != str(year):
            print(f"  MISMATCH: workbook says year {stated_year!r}, loaded as {year}")
            problems += 1
        if stated_scope != f"Scope {SCOPE}":
            print(f"  MISMATCH: workbook says {stated_scope!r}, loaded as scope {SCOPE}")
            problems += 1

        for category, spec in FUEL_FACTORS.items():
            if year not in spec["values"]:
                continue
            expected, expected_row = spec["values"][year]
            found = totals.get((spec["fuel_label"], spec["sheet_unit"]))
            if found is None:
                print(f"  {category:13} NOT FOUND: {spec['fuel_label']} / {spec['sheet_unit']}")
                problems += 1
                continue
            raw, row = found
            rounded = raw.quantize(PUBLISHED_PLACES)
            residue = abs(raw - rounded)
            ok = rounded == expected and row == expected_row and residue <= FLOAT_NOISE
            print(
                f"  {category:13} {spec['sheet_unit']:7} row {row:>4} "
                f"cell={raw} -> {rounded}  loaded={expected}  "
                f"{'ok' if ok else 'MISMATCH'}"
            )
            if not ok:
                problems += 1
                if row != expected_row:
                    print(f"      row moved: workbook {row}, loader says {expected_row}")
                if residue > FLOAT_NOISE:
                    print(f"      residue {residue} exceeds float noise - check the precision")
            checked += 1

        for excluded in ("Diesel (average biofuel blend)", "Petrol (average biofuel blend)"):
            if (excluded, "litres") in totals:
                value, row = totals[(excluded, "litres")]
                print(f"  (not loaded) {excluded} litres row {row} = {value}")

    print(f"\n{checked} values checked against the publisher's workbook, {problems} problem(s)")
    if not problems:
        print(f"  all literals in FUEL_FACTORS reproduce the sheet at {PUBLISHED_PLACES} precision")
    return 0 if problems == 0 else 1


# --- loading ----------------------------------------------------------------------------


def apply_migrations(conn: psycopg.Connection) -> list[Path]:
    applied = sorted(MIGRATIONS_DIR.glob("*.sql"))
    for path in applied:
        conn.execute(path.read_text())
    return applied


def load() -> int:
    try:
        with connect() as conn:
            for path in apply_migrations(conn):
                print(f"migration  {path.name}")

            inserted = unchanged = 0
            for row in rows():
                result = conn.execute(UPSERT, row).fetchone()
                if result is not None and result[0]:
                    inserted += 1
                else:
                    unchanged += 1

            summary = conn.execute(
                """
                select category, unit, count(*) as years, min(year) as earliest,
                       max(year) as latest, bool_and(verified) as all_verified
                  from emission_factors
                 where publisher = %s
                 group by category, unit
                 order by category
                """,
                (PUBLISHER,),
            ).fetchall()
    except DatabaseNotConfiguredError as exc:
        print(f"{exc}", file=sys.stderr)
        return 2
    except psycopg.Error as exc:
        print(f"Database error: {exc}", file=sys.stderr)
        return 1

    print(f"\nDESNZ: {inserted} inserted, {unchanged} unchanged/updated")
    for category, unit, years, earliest, latest, all_verified in summary:
        print(
            f"  {category:13} {unit:18} {years} year(s) {earliest}-{latest}  "
            f"verified={all_verified}"
        )
    print(f"  publisher {PUBLISHER}")
    print(f"  jurisdiction {JURISDICTION}, scope {SCOPE}, method {METHOD}, basis {GAS_BASIS}")
    print(f"  licence {LICENCE}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verify",
        action="store_true",
        help="re-read the workbooks and diff against the literals; touches no database",
    )
    args = parser.parse_args()
    return verify() if args.verify else load()


if __name__ == "__main__":
    raise SystemExit(main())
