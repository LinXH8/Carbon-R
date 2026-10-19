"""The `Activity` type: one quantity, its unit, and how it was arrived at.

Small, but it is the type that replaced `ACTIVITY_UNIT = "kWh"` as a module constant, so
the things it refuses matter.
"""

from decimal import Decimal

import pytest

from carbon_r.models.activity import KIND_UNITS, Activity, unit_for


def test_each_kind_has_the_unit_its_documents_measure_it_in():
    assert KIND_UNITS == {
        "electricity": "kWh",
        "lpg": "kg",
        "diesel": "litres",
        "petrol": "litres",
    }


def test_unit_for_refuses_an_unknown_kind_rather_than_guessing():
    with pytest.raises(ValueError) as caught:
        unit_for("town_gas")
    assert "town_gas" in str(caught.value)
    assert "electricity" in str(caught.value)  # the message lists what IS known


def test_a_negative_amount_is_refused():
    """A credit line is not negative consumption."""
    with pytest.raises(ValueError):
        Activity(kind="lpg", amount=Decimal(-1), unit="kg", source_field="c")


def test_an_unknown_kind_is_refused_by_the_type():
    with pytest.raises(ValueError):
        Activity(kind="kerosene", amount=Decimal(1), unit="litres", source_field="c")


def test_extra_fields_are_refused():
    """Nothing can be smuggled onto an activity on the way to a calculation."""
    with pytest.raises(ValueError):
        Activity(
            kind="lpg",
            amount=Decimal(1),
            unit="kg",
            source_field="c",
            factor=Decimal("2939.36095"),
        )


def test_an_activity_is_frozen():
    activity = Activity(kind="lpg", amount=Decimal(200), unit="kg", source_field="c")
    with pytest.raises(ValueError):
        activity.amount = Decimal(1)


def test_a_printed_amount_records_no_derivation():
    """There is nothing to explain when the page printed the number."""
    activity = Activity(
        kind="electricity", amount=Decimal(4423), unit="kWh", source_field="consumption"
    )
    assert activity.derivation is None


def test_a_computed_amount_carries_its_arithmetic():
    activity = Activity(
        kind="lpg",
        amount=Decimal(200),
        unit="kg",
        source_field="cylinder_deliveries",
        derivation="4 cylinders x 50 kg = 200 kg",
    )
    assert activity.derivation == "4 cylinders x 50 kg = 200 kg"


def test_the_label_is_for_a_message_and_groups_its_thousands():
    activity = Activity(
        kind="electricity", amount=Decimal(139600), unit="kWh", source_field="consumption"
    )
    assert activity.label == "139,600 kWh of electricity"


def test_an_estimate_flag_travels_with_the_activity_not_the_document():
    """A document can carry several activities and they need not share a basis."""
    activity = Activity(
        kind="electricity",
        amount=Decimal(1),
        unit="kWh",
        source_field="consumption",
        is_estimated=True,
    )
    assert activity.is_estimated is True
