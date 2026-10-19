"""`FactorCitation` now carries what the factor IS, not only where it came from.

Scope, method and gas basis were module constants in `tools/calculate.py` until Tier 2
Block 1. That was tenable only while every row in the table was Singapore grid
electricity: `METHOD = "location-based"` printed beside a fuel-combustion figure is a
wrong label on a correct number.
"""

from datetime import date
from decimal import Decimal

import pytest

from carbon_r.models.factors import FactorCitation

PROVENANCE = {
    "category": "fuel_lpg",
    "factor": Decimal("2939.36095"),
    "unit": "kg CO2e / tonnes",
    "year_requested": 2026,
    "year_used": 2026,
    "publisher": "Department for Energy Security and Net Zero",
    "source_document": "GHG Conversion Factors 2026, sheet 'Fuels'",
    "source_url": "https://www.gov.uk/government/publications/x",
    "source_page": "sheet 'Fuels', row 35, column D",
    "retrieved_at": date(2026, 10, 18),
}


def citation(**overrides) -> FactorCitation:
    return FactorCitation(**{**PROVENANCE, **overrides})


def test_a_uk_factor_is_flagged_as_non_singapore():
    assert citation(jurisdiction="UK").is_non_singapore is True


def test_a_singapore_factor_is_not():
    assert citation(jurisdiction="SG").is_non_singapore is False


def test_the_classification_reads_as_a_citation_line():
    factor = citation(scope=1, method="combustion", gas_basis="CO2e")
    assert factor.classification == "Scope 1, combustion, CO2e"


def test_an_unrecorded_scope_says_so_rather_than_claiming_one():
    assert citation().classification.startswith("scope not recorded")


def test_a_scope_outside_one_to_three_is_refused():
    for scope in (0, 4):
        with pytest.raises(ValueError):
            citation(scope=scope)


def test_a_gas_basis_outside_co2_and_co2e_is_refused():
    with pytest.raises(ValueError):
        citation(gas_basis="CH4")


@pytest.mark.parametrize("basis", ["CO2", "CO2e"])
def test_both_gas_bases_are_accepted(basis):
    assert citation(gas_basis=basis).gas_basis == basis


def test_provenance_is_still_mandatory():
    """Rule 2 as a type: there is no way to construct a citation anonymously."""
    for field in ("publisher", "source_document", "source_url", "unit"):
        payload = dict(PROVENANCE)
        del payload[field]
        with pytest.raises(ValueError):
            FactorCitation(**payload)
