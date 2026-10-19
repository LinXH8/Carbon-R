"""`calculate_emissions` against answers computed by hand.

The expected figures here are NOT taken from the function under test. Each was computed by
an independent integer route - 0.402 kg/kWh is 402 thousandths, so 4016 kWh is
4016 x 402 = 1,614,432 thousandths of a kg = 1614.432 kg - and that is what these tests
assert. A test that asserted whatever the function returned would pass just as happily
with a broken multiplication.

Since Tier 2 Block 1 the function takes a LIST of (activity, factor) pairs and returns the
lines plus a total. Three groups of tests exist because of that change: the per-line
arithmetic (unchanged in substance), the totalling, and the all-or-nothing rule.

No database, no API key, no `.env` (spec Part 8).
"""

from datetime import date
from decimal import Decimal

import pytest

from carbon_r.models.activity import Activity
from carbon_r.models.factors import FactorCitation
from carbon_r.tools.calculate import calculate_emissions

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
    "scope": 2,
    "method": "location-based",
    "gas_basis": "CO2",
}

# The real DESNZ provenance, for the Scope 1 fuel rows.
DESNZ_PROVENANCE = {
    "publisher": "Department for Energy Security and Net Zero",
    "source_document": (
        "UK Government GHG Conversion Factors for Company Reporting 2026, full set, sheet 'Fuels'"
    ),
    "source_url": (
        "https://www.gov.uk/government/publications/"
        "greenhouse-gas-reporting-conversion-factors-2026"
    ),
    "retrieved_at": date(2026, 10, 18),
    "jurisdiction": "UK",
    "verified": True,
    "scope": 1,
    "method": "combustion",
    "gas_basis": "CO2e",
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


def factor_lpg(**overrides) -> FactorCitation:
    """DESNZ's 2026 LPG factor. Published per TONNE, which forces the conversion."""
    defaults = {
        **DESNZ_PROVENANCE,
        "category": "fuel_lpg",
        "factor": Decimal("2939.36095"),
        "unit": "kg CO2e / tonnes",
        "year_requested": 2026,
        "year_used": 2026,
        "source_page": "sheet 'Fuels', row 35, column D (LPG, tonnes)",
    }
    return FactorCitation(**{**defaults, **overrides})


def factor_diesel(**overrides) -> FactorCitation:
    defaults = {
        **DESNZ_PROVENANCE,
        "category": "fuel_diesel",
        "factor": Decimal("2.66155"),
        "unit": "kg CO2e / litres",
        "year_requested": 2026,
        "year_used": 2026,
        "source_page": "sheet 'Fuels', row 76, column D",
    }
    return FactorCitation(**{**defaults, **overrides})


def factor_petrol(**overrides) -> FactorCitation:
    defaults = {
        **DESNZ_PROVENANCE,
        "category": "fuel_petrol",
        "factor": Decimal("2.35372"),
        "unit": "kg CO2e / litres",
        "year_requested": 2026,
        "year_used": 2026,
        "source_page": "sheet 'Fuels', row 100, column D",
    }
    return FactorCitation(**{**defaults, **overrides})


def electricity(kwh, **overrides) -> Activity:
    defaults = {
        "kind": "electricity",
        "amount": Decimal(kwh),
        "unit": "kWh",
        "source_field": "consumption",
    }
    return Activity(**{**defaults, **overrides})


def one(activity, factor):
    """The common case: a single line."""
    return calculate_emissions(pairs=[{"activity": activity, "factor": factor}])


# --- the per-line arithmetic ------------------------------------------------------------


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
    result = one(electricity(kwh), factor_2024())
    assert result.status == "ok"
    assert result.data["kg_co2e"] == expected_kg
    assert result.data["tonnes_co2e"] == expected_tonnes


def test_the_exact_product_is_published_alongside_the_rounded_figures():
    """56,119.2 exactly. `tonnes` comes from the exact product, not from the rounded kg."""
    result = one(electricity(139600), factor_2024())
    line = result.data["lines"][0]
    assert line["kg_co2e_exact"] == "56119.200"
    assert Decimal(result.data["kg_co2e_exact"]) == Decimal("56119.2")
    assert Decimal(result.data["tonnes_co2e"]) == Decimal(result.data["kg_co2e_exact"]) / 1000


def test_the_derivation_reads_as_a_hand_check():
    result = one(electricity(4423), factor_2024())
    assert result.data["derivation"] == "4,423 kWh x 0.402 kg CO2 / kWh = 1,778.046 kg CO2e"


# --- the LPG conversion -----------------------------------------------------------------


def test_kg_is_converted_to_tonnes_before_a_per_tonne_factor_is_applied():
    """200 kg = 0.2 t; 0.2 x 2939.36095 = 587.87219. Hand-computed, independently.

    DESNZ publishes no per-kg LPG row, so this conversion is the only route from an LPG
    invoice to a figure. It is also the only conversion the project performs.
    """
    activity = Activity(
        kind="lpg",
        amount=Decimal(200),
        unit="kg",
        source_field="cylinder_deliveries",
        derivation="4 cylinders x 50 kg = 200 kg",
    )
    result = one(activity, factor_lpg())
    assert result.status == "ok"
    assert result.data["kg_co2e"] == "587.872"
    # Numerically, not as a string: the exact product's trailing-zero count follows from
    # the operands' precision (3 dp on the converted tonnes, 5 on the factor), which is an
    # artefact of Decimal rather than a property worth pinning.
    assert Decimal(result.data["lines"][0]["kg_co2e_exact"]) == Decimal("587.87219")


def test_the_conversion_is_shown_with_its_authority_rather_than_absorbed():
    """A conversion that did not appear in the output would be a hidden step."""
    activity = Activity(
        kind="lpg", amount=Decimal(200), unit="kg", source_field="cylinder_deliveries"
    )
    result = one(activity, factor_lpg())
    conversion = result.data["lines"][0]["conversion"]
    assert conversion["from_unit"] == "kg"
    assert conversion["to_unit"] == "tonnes"
    assert conversion["statement"] == "1 tonne = 1000 kg"
    assert "Conversions" in conversion["source"]
    assert "200 kg = 0.2 tonnes (1 tonne = 1000 kg)" in result.data["derivation"]
    assert result.data["disclosures"]["unit_converted"] is True


def test_no_conversion_is_recorded_when_the_units_already_agree():
    result = one(electricity(4016), factor_2024())
    assert result.data["lines"][0]["conversion"] is None
    assert result.data["disclosures"]["unit_converted"] is False


# --- totalling --------------------------------------------------------------------------


def diesel_and_petrol():
    """A mixed fuel-card statement: 62.41 L diesel and 41.08 L petrol."""
    return [
        {
            "activity": Activity(
                kind="diesel",
                amount=Decimal("62.41"),
                unit="litres",
                source_field="transactions.litres",
            ),
            "factor": factor_diesel(),
        },
        {
            "activity": Activity(
                kind="petrol",
                amount=Decimal("41.08"),
                unit="litres",
                source_field="transactions.litres",
            ),
            "factor": factor_petrol(),
        },
    ]


def test_two_products_are_totalled_in_python():
    """62.41 x 2.66155 = 166.1073355; 41.08 x 2.35372 = 96.6908176. Sum 262.7981531.

    Both products computed by hand from the published factors, then added. This is the
    number the model is never allowed to produce.
    """
    result = calculate_emissions(pairs=diesel_and_petrol())
    assert result.status == "ok"
    assert [line["kg_co2e_exact"] for line in result.data["lines"]] == [
        "166.1073355",
        "96.6908176",
    ]
    assert result.data["kg_co2e_exact"] == "262.7981531"
    assert result.data["kg_co2e"] == "262.798"


def test_the_total_is_summed_from_the_exact_products_not_the_rounded_lines():
    """Rounding each line and then adding would let the total disagree with its addends."""
    result = calculate_emissions(pairs=diesel_and_petrol())
    exact_sum = sum(Decimal(line["kg_co2e_exact"]) for line in result.data["lines"])
    assert Decimal(result.data["kg_co2e_exact"]) == exact_sum
    # And the rounded total is the rounding of that sum, not the sum of the roundings.
    assert Decimal(result.data["kg_co2e"]) == exact_sum.quantize(Decimal("0.001"))


def test_the_multi_line_derivation_writes_out_every_line_and_the_sum():
    result = calculate_emissions(pairs=diesel_and_petrol())
    derivation = result.data["derivation"]
    assert "62.41 litres x 2.66155 kg CO2e / litres = 166.1073355 kg CO2e" in derivation
    assert "41.08 litres x 2.35372 kg CO2e / litres = 96.6908176 kg CO2e" in derivation
    assert "= 262.798 kg CO2e in total" in derivation


def test_a_single_line_keeps_a_single_line_derivation():
    """No "in total" on a figure with one line: there is nothing it is a total of."""
    result = one(electricity(4016), factor_2024())
    assert "in total" not in result.data["derivation"]
    assert result.data["derivation"] == result.data["lines"][0]["derivation"]


def test_mixed_scopes_are_broken_out_as_well_as_totalled():
    """4016 kWh -> 1614.432 kg Scope 2; 62.41 L diesel -> 166.1073355 kg Scope 1."""
    result = calculate_emissions(
        pairs=[
            {"activity": electricity(4016), "factor": factor_2024()},
            *diesel_and_petrol()[:1],
        ]
    )
    assert result.data["scopes"] == [1, 2]
    assert result.data["kg_co2e_by_scope"] == {"1": "166.107", "2": "1614.432"}
    assert result.data["disclosures"]["mixed_scopes"] is True


def test_mixing_a_co2_factor_with_a_co2e_factor_is_disclosed():
    """EMA's factor is CO2 only; DESNZ's is CO2e. A total of both understates the first."""
    result = calculate_emissions(
        pairs=[
            {"activity": electricity(4016), "factor": factor_2024()},
            *diesel_and_petrol()[:1],
        ]
    )
    assert result.data["gas_bases"] == ["CO2", "CO2e"]
    assert result.data["disclosures"]["mixed_gas_basis"] is True


def test_one_basis_throughout_is_not_a_mix():
    result = calculate_emissions(pairs=diesel_and_petrol())
    assert result.data["gas_bases"] == ["CO2e"]
    assert result.data["disclosures"]["mixed_gas_basis"] is False
    assert result.data["disclosures"]["mixed_scopes"] is False


# --- all or nothing ---------------------------------------------------------------------


def test_one_bad_line_fails_the_whole_call_and_returns_no_figure():
    """A total over the lines that worked would look like an answer and be short.

    This is the most important behaviour in the module: a partial total is worse than an
    error, because nothing on its face says a line is missing.
    """
    pairs = diesel_and_petrol()
    # Petrol litres against a factor published per kWh: no conversion is declared.
    pairs[1]["factor"] = factor_2024()
    result = calculate_emissions(pairs=pairs)

    assert result.status == "error"
    assert result.data is None
    assert "line 2" in result.message
    assert "short" in result.message


def test_every_bad_line_is_named_in_one_message():
    """One turn should be enough to fix them all, so they are not reported one at a time."""
    pairs = diesel_and_petrol()
    pairs[0]["factor"] = factor_2024()
    pairs[1]["factor"] = factor_2024()
    result = calculate_emissions(pairs=pairs)
    assert result.status == "error"
    assert "line 1" in result.message
    assert "line 2" in result.message
    assert "2 of 2 line(s)" in result.message


def test_no_pairs_is_an_error_not_a_zero():
    """A total of nothing is 0, which would be a figure nobody asked for."""
    result = calculate_emissions(pairs=[])
    assert result.status == "error"
    assert result.data is None


def test_a_pair_missing_a_side_is_refused():
    result = calculate_emissions(pairs=[{"activity": electricity(10)}])
    assert result.status == "error"
    assert "both an activity and a factor" in result.message


# --- provenance and refusals ------------------------------------------------------------


def test_result_returns_the_inputs_it_used():
    """Rule 2: a figure travels with everything needed to re-derive it."""
    result = one(electricity(4016), factor_2024())
    line = result.data["lines"][0]
    assert line["activity"]["amount"] == "4016"
    assert line["activity"]["unit"] == "kWh"
    assert line["factor"]["factor"] == "0.402"
    assert line["factor"]["publisher"] == "EMA"
    assert line["factor"]["source_document"] == "Singapore Energy Statistics 2025, Table 2.4"
    assert line["factor"]["source_page"] == "Table 2.4"
    assert line["factor"]["year_used"] == 2024


def test_scope_and_method_come_from_the_factor_not_from_a_constant():
    """They were module constants until Tier 2; `location-based` on an LPG line is wrong."""
    electric = one(electricity(4016), factor_2024()).data["lines"][0]
    assert (electric["scope"], electric["method"], electric["gas_basis"]) == (
        2,
        "location-based",
        "CO2",
    )

    gas = one(
        Activity(kind="lpg", amount=Decimal(200), unit="kg", source_field="c"), factor_lpg()
    ).data["lines"][0]
    assert (gas["scope"], gas["method"], gas["gas_basis"]) == (1, "combustion", "CO2e")


def test_a_factor_without_provenance_is_refused():
    result = calculate_emissions(
        pairs=[{"activity": electricity(4016), "factor": {"factor": "0.402"}}]
    )
    assert result.status == "error"
    assert result.data is None
    assert "provenance" in result.message


def test_a_factor_that_does_not_record_its_scope_is_refused():
    """Without a scope the figure could not say what it is a figure OF."""
    payload = factor_2024().model_dump(mode="json")
    payload["scope"] = None
    result = calculate_emissions(pairs=[{"activity": electricity(4016), "factor": payload}])
    assert result.status == "error"
    assert "scope or gas basis" in result.message


def test_a_factor_dict_from_lookup_is_accepted_whole():
    """The agent layer passes the tool payload straight through; it is still validated."""
    payload = factor_2024().model_dump(mode="json")
    result = calculate_emissions(pairs=[{"activity": electricity(4016), "factor": payload}])
    assert result.status == "ok"
    assert result.data["kg_co2e"] == "1614.432"


def test_unit_mismatch_is_an_error_not_a_conversion():
    activity = Activity(
        kind="diesel", amount=Decimal(100), unit="litres", source_field="transactions.litres"
    )
    result = one(activity, factor_2024())
    assert result.status == "error"
    assert "no conversion between them is declared" in result.message


def test_an_unrecognised_factor_unit_is_refused_rather_than_guessed():
    result = one(electricity(100), factor_2024(unit="kg CO2 / therm"))
    assert result.status == "error"
    assert "no known activity unit" in result.message.lower()


def test_negative_activity_is_refused():
    """A credit line is not negative consumption; `Activity` rejects it at construction."""
    with pytest.raises(ValueError):
        electricity(-100)


# --- disclosures ------------------------------------------------------------------------


def test_a_clean_singapore_verified_factor_discloses_only_non_assurance():
    result = one(electricity(4016), factor_2024())
    assert result.data["disclosures"] == {
        "estimated_reading": False,
        "factor_year_substituted": False,
        "non_singapore_factor": False,
        "factor_unverified": False,
        "unit_converted": False,
        "mixed_scopes": False,
        "mixed_gas_basis": False,
        "not_assured": True,
    }


def test_substitution_estimate_and_jurisdiction_each_raise_their_flag():
    result = one(
        electricity(4016, is_estimated=True),
        factor_2024(
            year_requested=2026,
            year_used=2024,
            substituted=True,
            substitution_note="used 2024",
            jurisdiction="UK",
            verified=False,
        ),
    )
    disclosures = result.data["disclosures"]
    assert disclosures["estimated_reading"] is True
    assert disclosures["factor_year_substituted"] is True
    assert disclosures["non_singapore_factor"] is True
    assert disclosures["factor_unverified"] is True


def test_a_flag_on_any_line_raises_it_for_the_whole_result():
    """One UK factor among several makes the result a non-Singapore result."""
    result = calculate_emissions(
        pairs=[
            {"activity": electricity(4016), "factor": factor_2024()},
            *diesel_and_petrol()[:1],
        ]
    )
    assert result.data["disclosures"]["non_singapore_factor"] is True


# --- the factor year (Tier 2 Block 2) ----------------------------------------------------
#
# An activity carries the year it belongs to, and a factor looked up for another year is
# refused. Expected figures by the integer route: 4645 x 412 = 1,913,740 and
# 4355 x 402 = 1,750,710 thousandths of a kg; total 3,664,450 -> 3664.450 kg.


def split_activity(amount: str, year: int) -> Activity:
    return Activity(
        kind="electricity",
        amount=Decimal(amount),
        unit="kWh",
        source_field="consumption",
        factor_year=year,
    )


def test_a_factor_for_the_wrong_year_is_refused():
    result = calculate_emissions(
        pairs=[{"activity": split_activity("4645", 2023), "factor": factor_2024()}]
    )
    assert result.status == "error"
    assert result.data is None
    assert "belongs to 2023" in result.message
    assert "looked up for 2024" in result.message


def test_a_substituted_factor_is_still_the_right_year():
    """Asking for 2025 and being given 2024 is a disclosed substitution, not a mismatch."""
    result = calculate_emissions(
        pairs=[
            {
                "activity": split_activity("100", 2025),
                "factor": factor_2024(year_requested=2025, substituted=True),
            }
        ]
    )
    assert result.status == "ok"
    assert result.data["disclosures"]["factor_year_substituted"] is True


def test_one_years_factor_on_both_halves_is_refused_whole():
    result = calculate_emissions(
        pairs=[
            {"activity": split_activity("4645", 2023), "factor": factor_2024()},
            {"activity": split_activity("4355", 2024), "factor": factor_2024()},
        ]
    )
    assert result.status == "error"
    assert "1 of 2 line(s)" in result.message


def test_a_bill_split_across_a_year_boundary_uses_both_years():
    result = calculate_emissions(
        pairs=[
            {
                "activity": split_activity("4645", 2023),
                "factor": factor_2024(factor=Decimal("0.412"), year_requested=2023, year_used=2023),
            },
            {"activity": split_activity("4355", 2024), "factor": factor_2024()},
        ]
    )
    assert result.status == "ok"
    assert [line["kg_co2e"] for line in result.data["lines"]] == ["1913.740", "1750.710"]
    assert result.data["kg_co2e"] == "3664.450"
    assert [line["factor"]["year_used"] for line in result.data["lines"]] == [2023, 2024]


def test_an_activity_without_a_year_is_not_year_checked():
    """A hand-built activity with no year keeps the Tier 1 behaviour."""
    activity = Activity(kind="electricity", amount=Decimal("10"), unit="kWh", source_field="x")
    result = calculate_emissions(pairs=[{"activity": activity, "factor": factor_2024()}])
    assert result.status == "ok"
