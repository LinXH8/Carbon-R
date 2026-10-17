"""`calculate_emissions` against answers computed by hand.

The expected figures here are NOT taken from the function under test. Each was computed by
an independent integer route - 0.402 kg/kWh is 402 thousandths, so 4016 kWh is
4016 x 402 = 1,614,432 thousandths of a kg = 1614.432 kg - and that is what these tests
assert. A test that asserted whatever the function returned would pass just as happily
with a broken multiplication.

No database, no API key, no `.env` (spec Part 8).
"""

from datetime import date
from decimal import Decimal

import pytest

from carbon_r.models.factors import FactorCitation
from carbon_r.tools.calculate import METHOD, calculate_emissions

# The real EMA provenance, as `lookup_emission_factor` will supply it.
EMA_PROVENANCE = {
    "category": "electricity_grid_sg",
    "unit": "kg CO2 / kWh",
    "publisher": "EMA",
    "source_document": "Singapore Energy Statistics 2025, Table 2.4",
    "source_url": "https://www.ema.gov.sg/resources/singapore-energy-statistics/chapter2",
    "source_page": "Table 2.4",
    "retrieved_at": date(2026, 10, 15),
    "jurisdiction": "SG",
    "verified": True,
}


def factor_2024(**overrides) -> FactorCitation:
    """The 2024 EMA factor, with any field overridden. Every default is overridable."""
    defaults = {
        **EMA_PROVENANCE,
        "factor": Decimal("0.402"),
        "year_requested": 2024,
        "year_used": 2024,
    }
    return FactorCitation(**{**defaults, **overrides})


# --- the arithmetic ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwh", "expected_kg", "expected_tonnes"),
    [
        # elec_001, low tension: 4016 x 402 = 1,614,432 thousandths
        (4016, "1614.432", "1.614432"),
        # elec_009, high tension: 139600 x 402 = 56,119,200 thousandths
        (139600, "56119.200", "56.119200"),
        # a single unit, and zero: the edges of the smallest claim the tool can make
        (1, "0.402", "0.000402"),
        (0, "0.000", "0.000000"),
    ],
)
def test_kwh_times_factor_matches_hand_computed_answer(kwh, expected_kg, expected_tonnes):
    result = calculate_emissions(
        activity_amount=Decimal(kwh), activity_unit="kWh", factor=factor_2024()
    )
    assert result.status == "ok"
    assert Decimal(result.data["kg_co2e"]) == Decimal(expected_kg)
    assert Decimal(result.data["tonnes_co2e"]) == Decimal(expected_tonnes)


def test_the_exact_product_is_published_alongside_the_rounded_figures():
    """A figure that rounds must still be re-derivable, so the exact product is kept.

    At 3 decimal places for kg and 6 for tonnes the two rounded views agree by
    construction (round6(x/1000) is round3(x)/1000), so this does not test that they
    cannot diverge - it tests that nothing is lost: 0.4025 kg is reported exactly, and
    the rounded view rounds half UP rather than to even.
    """
    result = calculate_emissions(
        activity_amount=Decimal(1),
        activity_unit="kWh",
        factor=factor_2024(factor=Decimal("0.4025")),
    )
    assert result.data["kg_co2e_exact"] == "0.4025"
    assert Decimal(result.data["kg_co2e"]) == Decimal("0.403")  # half up, not half even
    assert Decimal(result.data["tonnes_co2e"]) == Decimal("0.000403")
    # The published rounding never loses the input: 0.4025 is still there to re-derive from.
    assert Decimal(result.data["kg_co2e_exact"]) == Decimal(1) * Decimal("0.4025")


def test_the_derivation_reads_as_a_hand_check():
    result = calculate_emissions(
        activity_amount=Decimal(139600), activity_unit="kWh", factor=factor_2024()
    )
    assert result.data["derivation"] == ("139,600 kWh x 0.402 kg CO2 / kWh = 56,119.2 kg CO2e")
    assert METHOD in result.message


# --- rule 2: the result carries everything it was computed from -------------------------


def test_result_returns_the_inputs_it_used():
    result = calculate_emissions(
        activity_amount=Decimal(4016), activity_unit="kWh", factor=factor_2024()
    )
    inputs = result.data["inputs"]
    assert inputs["activity_amount"] == "4016"
    assert inputs["activity_unit"] == "kWh"

    cited = inputs["factor"]
    for required in (
        "factor",
        "unit",
        "year_used",
        "year_requested",
        "publisher",
        "source_document",
        "source_url",
        "source_page",
        "retrieved_at",
    ):
        assert cited[required] not in (None, ""), f"{required} missing from the citation"
    assert cited["factor"] == "0.402"
    assert cited["publisher"] == "EMA"
    assert cited["source_page"] == "Table 2.4"


def test_a_factor_without_provenance_is_refused():
    result = calculate_emissions(
        activity_amount=Decimal(4016),
        activity_unit="kWh",
        factor={"factor": "0.402", "unit": "kg CO2 / kWh"},
    )
    assert result.status == "error"
    assert "provenance" in result.message
    assert result.data is None


def test_a_factor_dict_from_lookup_is_accepted_whole():
    """The agent passes `data["factor"]` straight through; it must validate as-is."""
    payload = factor_2024().model_dump(mode="json")
    result = calculate_emissions(activity_amount=Decimal(4016), activity_unit="kWh", factor=payload)
    assert result.status == "ok"
    assert Decimal(result.data["kg_co2e"]) == Decimal("1614.432")


# --- units are never converted silently -------------------------------------------------


def test_unit_mismatch_is_an_error_not_a_conversion():
    result = calculate_emissions(
        activity_amount=Decimal(100), activity_unit="litres", factor=factor_2024()
    )
    assert result.status == "error"
    assert "Unit mismatch" in result.message
    assert "litres" in result.message


def test_an_unrecognised_factor_unit_is_refused_rather_than_guessed():
    result = calculate_emissions(
        activity_amount=Decimal(100),
        activity_unit="tonne-km",
        factor=factor_2024(unit="kg CO2 / tonne-km"),
    )
    assert result.status == "error"
    assert "No known activity unit" in result.message


def test_float_activity_amounts_are_refused():
    result = calculate_emissions(activity_amount=100.0, activity_unit="kWh", factor=factor_2024())
    assert result.status == "error"
    assert "Decimal" in result.message


def test_negative_activity_is_refused():
    result = calculate_emissions(
        activity_amount=Decimal(-1), activity_unit="kWh", factor=factor_2024()
    )
    assert result.status == "error"


# --- disclosures are flags the prompt renders, not prose -------------------------------


def test_a_clean_singapore_verified_factor_discloses_only_non_assurance():
    result = calculate_emissions(
        activity_amount=Decimal(4016), activity_unit="kWh", factor=factor_2024()
    )
    assert result.data["disclosures"] == {
        "estimated_reading": False,
        "factor_year_substituted": False,
        "non_singapore_factor": False,
        "factor_unverified": False,
        "not_assured": True,
    }
    assert result.data["method"] == "location-based"
    assert result.data["basis"] == "activity-based"


def test_substitution_estimate_and_jurisdiction_each_raise_their_flag():
    result = calculate_emissions(
        activity_amount=Decimal(4016),
        activity_unit="kWh",
        factor=factor_2024(
            year_requested=2026,
            year_used=2024,
            substituted=True,
            substitution_note="No 2026 factor published; used 2024 (gap 2 years).",
            jurisdiction="UK",
            verified=False,
        ),
        is_estimated_reading=True,
    )
    assert result.data["disclosures"] == {
        "estimated_reading": True,
        "factor_year_substituted": True,
        "non_singapore_factor": True,
        "factor_unverified": True,
        "not_assured": True,
    }
    # The substitution travels with the figure, naming both years.
    cited = result.data["inputs"]["factor"]
    assert cited["year_requested"] == 2026
    assert cited["year_used"] == 2024
    assert "2024" in cited["substitution_note"]
