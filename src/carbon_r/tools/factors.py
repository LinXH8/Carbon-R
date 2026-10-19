"""`lookup_emission_factor` - exact SQL, full provenance, and an honest substitution.

CLAUDE.md rule 3: a factor value is fetched by `where category = ... and unit = ... and
year = ...` and never by embedding distance. Similarity search has its place in this
project (methodology prose, matching a spend description to a category) but not here: a
factor retrieved by "close enough" is a wrong number with a citation attached, which is
worse than no number.

Rule 3's corollary is also honoured: there is no fallback to a public API. A missing
factor is a `no_results`, which the agent reports and asks about.

The fallback-YEAR rule is a different thing from a fallback SOURCE, and it is allowed:

    Use the latest published year at or before the activity year, state the substitution,
    and refuse if the gap exceeds MAX_FACTOR_YEAR_GAP.

`MAX_FACTOR_YEAR_GAP = 2` is sized to EMA's publication lag, not to gaps inside the
series. EMA publishes in arrears - Singapore Energy Statistics 2025 carries the 2024
factor - so the newest available year always trails the current activity year by about
two. A cap of 1 would refuse every bill for the current year. The earlier reasoning for a
cap of 1 ("the series has no missing years") confused holes *inside* the series with the
gap at its *end*; the binding gap is at the end.

Anything past the cap is `no_results`, never a quiet substitution, and a substitution
always sets `substituted=True` with a note naming both years.
"""

from decimal import Decimal
from typing import Any

import psycopg
from psycopg.rows import dict_row

from carbon_r.db import DatabaseNotConfiguredError, connect
from carbon_r.models.factors import FactorCitation
from carbon_r.tools.base import ToolResult, error, no_results, ok

# The furthest back a substitution may reach, in years. See the module docstring.
MAX_FACTOR_YEAR_GAP = 2

_COLUMNS = """
    category, factor, unit, year, publisher, source_document, source_url,
    source_page, retrieved_at, jurisdiction, verified, notes, scope, method, gas_basis
"""

# Exact identity: (category, unit, year) is the table's UNIQUE key.
_EXACT = f"select {_COLUMNS} from emission_factors where category = %s and unit = %s and year = %s"

# The single fallback query. `order by year desc limit 1` is what makes it "the latest
# published year at or before", and the (category, year desc) index serves it.
_LATEST_AT_OR_BEFORE = f"""
select {_COLUMNS} from emission_factors
where category = %s and unit = %s and year <= %s
order by year desc limit 1
"""

# Context for the agent's explanation when there is nothing usable.
_AVAILABLE = """
select min(year) as earliest, max(year) as latest, count(*) as rows
from emission_factors where category = %s and unit = %s
"""


def _citation(row: dict[str, Any], requested_year: int) -> FactorCitation:
    """Build the citation for a row, recording the substitution if there is one.

    Pure: no database, no settings. This is where `substituted` and its note are decided,
    so the substitution logic can be tested without a live table.
    """
    year_used = int(row["year"])
    gap = requested_year - year_used
    note = None
    if gap > 0:
        note = (
            f"No {requested_year} factor is published for this category; used the "
            f"{year_used} factor instead (a {gap}-year substitution). "
            f"{row['publisher']} publishes in arrears."
        )
    return FactorCitation(
        category=row["category"],
        factor=Decimal(str(row["factor"])),
        unit=row["unit"],
        year_requested=requested_year,
        year_used=year_used,
        substituted=gap > 0,
        substitution_note=note,
        publisher=row["publisher"],
        source_document=row["source_document"],
        source_url=row["source_url"],
        source_page=row["source_page"],
        retrieved_at=row["retrieved_at"],
        jurisdiction=row["jurisdiction"],
        verified=row["verified"],
        notes=row["notes"],
        scope=row["scope"],
        method=row["method"],
        gas_basis=row["gas_basis"],
    )


def _resolve(
    row: dict[str, Any] | None,
    requested_year: int,
    *,
    category: str,
    unit: str,
    available: dict[str, Any] | None = None,
) -> ToolResult:
    """Turn the best candidate row into a result. Pure - the whole rule lives here.

    `row` is the latest row at or before `requested_year`, or None if there is none. Every
    branch of the fallback rule is decided here, with no database access, which is why the
    year logic is unit-testable without Postgres.
    """
    context = {"category": category, "unit": unit, "year_requested": requested_year}
    if available:
        context["available_years"] = {
            "earliest": available["earliest"],
            "latest": available["latest"],
        }

    if row is None:
        if available and available["rows"]:
            message = (
                f"No {category} factor in '{unit}' is published for {requested_year} or any "
                f"earlier year. The table holds {available['earliest']}-{available['latest']}."
            )
        else:
            message = (
                f"No {category} factor in '{unit}' is loaded at all. "
                "Check the unit string: it must match the publisher's own, exactly."
            )
        return no_results(message, context)

    year_used = int(row["year"])
    gap = requested_year - year_used
    if gap > MAX_FACTOR_YEAR_GAP:
        return no_results(
            f"The newest {category} factor is for {year_used}, which is {gap} years before "
            f"the requested {requested_year}. That exceeds the {MAX_FACTOR_YEAR_GAP}-year "
            "limit on substituting an older factor, so no figure is offered. A newer "
            "publication is needed.",
            context | {"newest_year_available": year_used, "gap_years": gap},
        )

    citation = _citation(row, requested_year)
    data = {
        "factor": citation.model_dump(mode="json"),
        "is_non_singapore": citation.is_non_singapore,
    }
    if citation.substituted:
        message = (
            f"{citation.factor} {citation.unit} ({citation.publisher}, {year_used}). "
            f"{citation.substitution_note}"
        )
    else:
        message = (
            f"{citation.factor} {citation.unit} ({citation.publisher} {year_used}, "
            f"{citation.source_document}). {citation.classification}."
        )
    return ok(data, message)


def lookup_emission_factor(category: str, unit: str, year: int) -> ToolResult:
    """The emission factor for a category and unit in a given year.

    `year` is the ACTIVITY year - from the billing period, never the document date. See
    `ElectricityBill.factor_year`.
    """
    try:
        with connect() as conn, conn.cursor(row_factory=dict_row) as cur:
            row = cur.execute(_EXACT, (category, unit, year)).fetchone()
            available = None
            if row is None:
                row = cur.execute(_LATEST_AT_OR_BEFORE, (category, unit, year)).fetchone()
                available = cur.execute(_AVAILABLE, (category, unit)).fetchone()
    except DatabaseNotConfiguredError as exc:
        return error(f"Cannot look up a factor: {exc}")
    except psycopg.Error as exc:
        return error(f"Database error during factor lookup: {exc}")

    return _resolve(row, year, category=category, unit=unit, available=available)


def _demo() -> None:  # pragma: no cover - manual verification aid
    for year in (2024, 2025, 2026, 2027, 2004):
        result = lookup_emission_factor("electricity_grid_sg", "kg CO2 / kWh", year)
        factor = (result.data or {}).get("factor") or {}
        print(
            f"{year}: {result.status:11} "
            f"year_used={factor.get('year_used', '-')} "
            f"substituted={factor.get('substituted', '-')}"
        )
        print(f"     {result.message}")


if __name__ == "__main__":  # pragma: no cover
    _demo()


__all__ = ["MAX_FACTOR_YEAR_GAP", "lookup_emission_factor"]
