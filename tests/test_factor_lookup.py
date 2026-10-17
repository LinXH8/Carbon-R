"""The fallback-year rule, tested without a database.

`_resolve` holds the whole rule and takes a plain row dict, so every branch - exact hit,
substitution within the cap, substitution past the cap, nothing published at all - is
testable with no Postgres, no `.env` and no API key (spec Part 8). One integration test at
the bottom runs against the real table and skips when DATABASE_URL is absent.

The cap is 2, sized to EMA's publication lag: SES 2025 carries the 2024 factor, so a bill
for the current year needs a 2-year reach. See the module docstring of `tools/factors.py`.
"""

from datetime import date
from decimal import Decimal

import pytest

from carbon_r.config import get_settings
from carbon_r.tools.factors import (
    MAX_FACTOR_YEAR_GAP,
    _resolve,
    lookup_emission_factor,
)

NEWEST_YEAR = 2024  # what the loaded EMA series actually ends at

# A row as psycopg returns it with dict_row, from the real seeded table.
ROW_2024 = {
    "category": "electricity_grid_sg",
    "factor": Decimal("0.402"),
    "unit": "kg CO2 / kWh",
    "year": 2024,
    "publisher": "EMA",
    "source_document": "Singapore Energy Statistics 2025, Table 2.4",
    "source_url": "https://www.ema.gov.sg/resources/singapore-energy-statistics/chapter2",
    "source_page": "Table 2.4",
    "retrieved_at": date(2026, 10, 15),
    "jurisdiction": "SG",
    "verified": True,
    "notes": "Grid Emission Factor (GEF). Location-based.",
}
AVAILABLE = {"earliest": 2005, "latest": 2024, "rows": 20}


def resolve(row, requested_year, available=AVAILABLE):
    return _resolve(
        row,
        requested_year,
        category="electricity_grid_sg",
        unit="kg CO2 / kWh",
        available=available,
    )


def test_the_cap_is_two_years_sized_to_the_publication_lag():
    """If this changes, the reasoning in tools/factors.py must change with it."""
    assert MAX_FACTOR_YEAR_GAP == 2


# --- one case per row of the rule's table ----------------------------------------------


def test_exact_year_is_not_a_substitution():
    result = resolve(ROW_2024, 2024)
    assert result.status == "ok"
    factor = result.data["factor"]
    assert factor["year_requested"] == factor["year_used"] == 2024
    assert factor["substituted"] is False
    assert factor["substitution_note"] is None


@pytest.mark.parametrize(("requested", "gap"), [(2025, 1), (2026, 2)])
def test_a_gap_within_the_cap_substitutes_and_says_so(requested, gap):
    result = resolve(ROW_2024, requested)
    assert result.status == "ok"

    factor = result.data["factor"]
    assert factor["year_requested"] == requested
    assert factor["year_used"] == NEWEST_YEAR
    assert factor["substituted"] is True
    # The note names BOTH years and the size of the substitution.
    note = factor["substitution_note"]
    assert str(requested) in note
    assert str(NEWEST_YEAR) in note
    assert f"{gap}-year" in note
    # And the factor value itself is unchanged - a substitution is not an adjustment.
    assert Decimal(factor["factor"]) == Decimal("0.402")


def test_a_gap_past_the_cap_is_no_results_not_a_quiet_substitution():
    result = resolve(ROW_2024, 2027)
    assert result.status == "no_results"
    assert result.data["gap_years"] == 3
    assert result.data["newest_year_available"] == NEWEST_YEAR
    assert "exceeds" in result.message
    # Crucially: no factor is handed back for the caller to use anyway.
    assert "factor" not in result.data


def test_nothing_published_at_or_before_is_a_distinct_message_from_exceeding_the_cap():
    """Two different failures: an unpublished early year, and a stale newest year."""
    nothing = resolve(None, 2004)
    exceeded = resolve(ROW_2024, 2027)

    assert nothing.status == exceeded.status == "no_results"
    assert "2005-2024" in nothing.message
    assert "or any earlier year" in nothing.message
    assert "exceeds" not in nothing.message
    assert "exceeds" in exceeded.message
    assert nothing.message != exceeded.message


def test_an_unloaded_category_points_at_the_unit_string():
    result = resolve(None, 2024, available={"earliest": None, "latest": None, "rows": 0})
    assert result.status == "no_results"
    assert "unit string" in result.message


# --- the citation is complete whichever branch produced it ------------------------------


@pytest.mark.parametrize("requested", [2024, 2025, 2026])
def test_every_ok_result_carries_full_provenance(requested):
    factor = resolve(ROW_2024, requested).data["factor"]
    for required in (
        "factor",
        "unit",
        "year_requested",
        "year_used",
        "publisher",
        "source_document",
        "source_url",
        "source_page",
        "retrieved_at",
    ):
        assert factor[required] not in (None, ""), f"{required} missing"
    assert factor["publisher"] == "EMA"
    assert factor["verified"] is True


def test_a_non_singapore_factor_is_flagged_for_disclosure():
    uk_row = {**ROW_2024, "jurisdiction": "UK", "publisher": "DESNZ"}
    result = resolve(uk_row, 2024)
    assert result.data["is_non_singapore"] is True
    assert resolve(ROW_2024, 2024).data["is_non_singapore"] is False


# --- integration: the real table -------------------------------------------------------


@pytest.mark.skipif(not get_settings().database_configured, reason="DATABASE_URL not configured")
def test_live_table_resolves_2024_exactly_and_2026_by_substitution():
    exact = lookup_emission_factor("electricity_grid_sg", "kg CO2 / kWh", 2024)
    assert exact.status == "ok"
    assert Decimal(exact.data["factor"]["factor"]) == Decimal("0.402")
    assert exact.data["factor"]["substituted"] is False

    substituted = lookup_emission_factor("electricity_grid_sg", "kg CO2 / kWh", 2026)
    assert substituted.status == "ok"
    assert substituted.data["factor"]["year_used"] == NEWEST_YEAR
    assert substituted.data["factor"]["substituted"] is True

    refused = lookup_emission_factor("electricity_grid_sg", "kg CO2 / kWh", 2027)
    assert refused.status == "no_results"

    missing = lookup_emission_factor("no_such_category", "kg CO2 / kWh", 2024)
    assert missing.status == "no_results"
