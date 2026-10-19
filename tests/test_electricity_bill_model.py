"""The ground-truth model's arithmetic invariants.

The answer key is the arbiter of truth for every eval downstream, so these tests are about
proving the validator is load-bearing rather than decorative: each broken relation must be
rejected. Pass with no database and no API key.
"""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from carbon_r.models.electricity_bill import ElectricityBill

# A consistent bill, computed with the rules in carbon_r.money. Used as a base that each
# test then breaks in exactly one way.
GOOD = {
    "doc_id": "elec_001",
    "layout_id": "elec_a",
    "generator_seed": 1,
    "generator_version": "test",
    "vendor_name": "Straits Power Retail Pte Ltd",
    "vendor_uen": "202411234K",
    "customer_name": "Lion City Laundry Pte Ltd",
    "customer_address": "12 Jalan Sinar Bulan, #03-07, Singapore 388421",
    "account_number": "8820-7741-06",
    "document_date": date(2024, 6, 4),
    "period_start": date(2024, 5, 1),
    "period_end": date(2024, 5, 31),
    "meter_number": "SP41882307",
    "previous_reading_kwh": 412_823,
    "current_reading_kwh": 422_682,
    "consumption_kwh": 9_859,
    "tariff_plan_code": "SPR-LT-FIX-24",
    "tariff_plan_name": "Business Fixed 24",
    "tension": "Low Tension",
    "tariff_cents_per_kwh": Decimal("29.86"),
    "energy_charge_sgd": Decimal("2943.90"),
    "fixed_charge_sgd": Decimal("12.00"),
    "subtotal_sgd": Decimal("2955.90"),
    "gst_rate": Decimal("0.09"),
    "gst_amount_sgd": Decimal("266.03"),
    "total_sgd": Decimal("3221.93"),
}


def test_a_consistent_bill_constructs():
    bill = ElectricityBill(**GOOD)
    assert bill.consumption_kwh == 9_859
    assert bill.total_sgd == Decimal("3221.93")


def test_quirks_defaults_to_empty():
    assert ElectricityBill(**GOOD).quirks == []


def test_days_in_period_counts_inclusively():
    assert ElectricityBill(**GOOD).days_in_period == 31


def test_factor_year_comes_from_the_period_not_the_bill_date():
    """The rule Block 3's lookup must follow. elec_009 is the document where it bites."""
    bill = ElectricityBill(
        **{
            **GOOD,
            "period_start": date(2024, 12, 1),
            "period_end": date(2024, 12, 31),
            "document_date": date(2025, 1, 4),
        }
    )
    assert bill.document_date.year == 2025
    assert bill.factor_year == 2024


def test_decimals_survive_a_json_round_trip():
    bill = ElectricityBill(**GOOD)
    again = ElectricityBill.model_validate_json(bill.model_dump_json())
    assert again == bill
    assert again.total_sgd == Decimal("3221.93")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("consumption_kwh", 9_858),  # no longer current - previous
        ("current_reading_kwh", 422_683),  # readings no longer match consumption
        ("energy_charge_sgd", Decimal("2943.91")),  # one cent off kWh x rate
        ("subtotal_sgd", Decimal("2955.91")),  # not energy + fixed
        ("gst_amount_sgd", Decimal("266.04")),  # one cent off 9% of subtotal
        ("total_sgd", Decimal("3221.94")),  # not subtotal + gst
    ],
)
def test_each_broken_arithmetic_relation_is_rejected(field, value):
    with pytest.raises(ValidationError):
        ElectricityBill(**{**GOOD, field: value})


def test_period_start_after_period_end_is_rejected():
    with pytest.raises(ValidationError):
        ElectricityBill(**{**GOOD, "period_start": date(2024, 6, 1)})


def test_a_bill_dated_before_its_period_ends_is_rejected():
    with pytest.raises(ValidationError):
        ElectricityBill(**{**GOOD, "document_date": date(2024, 5, 30)})


def test_common_spec_fields_default_sensibly_for_a_clean_document():
    """SYNTHGEN_SPEC.md 3.3/1.8: the fields every document kind must carry."""
    bill = ElectricityBill(**GOOD)
    assert bill.doc_kind == "electricity_bill"
    assert bill.difficulty_tier == "clean"
    assert bill.degradation == {}
    assert bill.is_holdout_layout is False
    assert bill.layout_id == "elec_a"


def test_unknown_fields_are_rejected():
    with pytest.raises(ValidationError):
        ElectricityBill(**{**GOOD, "monthly_split_kwh": {"2024-05": 9859}})
