"""Normalising an LPG invoice reading, and the kg the page never prints.

The relation under test is the one that makes this document kind interesting:

    "4 x 50 kg"  ->  200 kg

computed in Python, with the arithmetic recorded on the Activity. And the money decoys -
delivery charge, cylinder rental, returned empties - must not reach it.

No model, no network: `normalise_lpg` is pure.
"""

from decimal import Decimal

import pytest

from carbon_r.models.extraction_lpg import (
    ExtractedLPGInvoice,
    PrintedCylinderDelivery,
    PrintedInvoiceLine,
    activities_for_lpg,
    check_consistency_lpg,
    normalise_lpg,
)


def delivery(count="4", size_kg="50", description="LPG cylinder 50 kg"):
    return PrintedCylinderDelivery(count=count, size_kg=size_kg, description=description)


def line(description, amount, quantity=None, unit_price=None):
    return PrintedInvoiceLine(
        description=description, quantity=quantity, unit_price=unit_price, amount=amount
    )


def reading(**overrides) -> ExtractedLPGInvoice:
    """A complete, internally consistent reading. Every field overridable."""
    defaults = {
        "vendor_name": "Island Gas Supply Pte Ltd",
        "vendor_uen": "SYN-0000-0002",
        "customer_name": "Kopi Corner Pte Ltd",
        "customer_address": "12 Lorong Tempua, #01-04, Singapore 530123",
        "account_number": "GAS-4401-07",
        "invoice_number": "IGS-202601-4107",
        "document_date": "04 Jan 2026",
        "period_start": "01 Dec 2025",
        "period_end": "31 Dec 2025",
        "cylinder_deliveries": [delivery()],
        "total_kg_printed": None,
        "returned_cylinders": "4",
        "lines": [
            line("LPG cylinder 50 kg", "660.00", quantity="4", unit_price="165.00"),
            line("Delivery charge", "10.00"),
            line("Cylinder rental", "20.00", quantity="4", unit_price="5.00"),
        ],
        "currency": "SGD",
        "subtotal": "690.00",
        "gst_percent": "9",
        "gst_amount": "62.10",
        "total": "752.10",
        "credit_adjustment": None,
        "notes": None,
    }
    return ExtractedLPGInvoice(**{**defaults, **overrides})


# --- the cylinder arithmetic ------------------------------------------------------------


def test_kg_is_computed_from_the_cylinder_line_not_read():
    """4 x 50 = 200. The page prints neither 200 nor any other kg figure."""
    normalised, problems = normalise_lpg(reading())
    assert problems == []
    assert normalised.lpg_kg == Decimal(200)
    assert normalised.total_kg_printed is None


def test_a_fractional_cylinder_size_is_not_rounded():
    """3 x 12.7 = 38.1 kg. A reading that rounded to whole kg would pass on 50 kg only."""
    normalised, problems = normalise_lpg(
        reading(cylinder_deliveries=[delivery(count="3", size_kg="12.7")])
    )
    assert problems == []
    assert normalised.lpg_kg == Decimal("38.1")


def test_several_delivery_lines_are_summed():
    normalised, _ = normalise_lpg(
        reading(
            cylinder_deliveries=[
                delivery(count="2", size_kg="50"),
                delivery(count="3", size_kg="12.7"),
            ]
        )
    )
    assert normalised.lpg_kg == Decimal("138.1")


def test_an_inline_unit_on_the_size_is_tolerated():
    """A faithful transcription of "50 kg" includes the unit."""
    normalised, problems = normalise_lpg(reading(cylinder_deliveries=[delivery(size_kg="50 kg")]))
    assert problems == []
    assert normalised.lpg_kg == Decimal(200)


def test_the_activity_carries_the_arithmetic_that_produced_it():
    """A derived quantity whose derivation is not recorded is a bare number."""
    normalised, _ = normalise_lpg(reading())
    activity = activities_for_lpg(normalised)[0]
    assert activity.kind == "lpg"
    assert activity.amount == Decimal(200)
    assert activity.unit == "kg"
    assert activity.derivation == "4 cylinders x 50 kg = 200 kg"
    assert activity.source_field == "cylinder_deliveries"


def test_two_delivery_lines_are_written_out_in_the_derivation():
    normalised, _ = normalise_lpg(
        reading(
            cylinder_deliveries=[
                delivery(count="2", size_kg="50"),
                delivery(count="3", size_kg="12.7"),
            ]
        )
    )
    assert activities_for_lpg(normalised)[0].derivation == "2 x 50 kg + 3 x 12.7 kg = 138.1 kg"


# --- the decoys -------------------------------------------------------------------------


def test_rental_and_delivery_charges_do_not_reach_the_kg():
    """The invoice's quantity column includes 4 rental cylinders. They are not gas."""
    normalised, _ = normalise_lpg(reading())
    assert normalised.lpg_kg == Decimal(200)  # not 400, which pooling the quantities gives
    assert len(normalised.lines) == 3
    assert {line.description for line in normalised.lines} == {
        "LPG cylinder 50 kg",
        "Delivery charge",
        "Cylinder rental",
    }


def test_returned_cylinders_are_recorded_but_are_not_an_activity():
    normalised, _ = normalise_lpg(reading(returned_cylinders="4"))
    assert normalised.returned_cylinders == 4
    assert normalised.lpg_kg == Decimal(200)


def test_no_delivery_line_means_no_activity_rather_than_zero_kg():
    """ "Nothing delivered" and "not read" are different, and 0 kg would claim the first."""
    normalised, _ = normalise_lpg(reading(cylinder_deliveries=[]))
    assert normalised.lpg_kg is None
    assert activities_for_lpg(normalised) == []


# --- one miss costs one field -----------------------------------------------------------


def test_a_delivery_line_missing_its_size_is_one_problem_not_an_exception():
    normalised, problems = normalise_lpg(
        reading(
            cylinder_deliveries=[
                delivery(),
                PrintedCylinderDelivery(count="2", size_kg=None, description="LPG cylinder"),
            ]
        )
    )
    assert [p.field for p in problems] == ["cylinders[2]"]
    assert "count and a cylinder size" in problems[0].reason
    # The readable line is still counted, and the total is still offered.
    assert normalised.lpg_kg == Decimal(200)


def test_an_unparseable_count_is_reported_and_left_out():
    normalised, problems = normalise_lpg(
        reading(cylinder_deliveries=[delivery(), delivery(count="two")])
    )
    assert [p.field for p in problems] == ["cylinders[2]"]
    assert normalised.lpg_kg == Decimal(200)


def test_a_money_line_without_an_amount_is_a_problem():
    normalised, problems = normalise_lpg(
        reading(lines=[line("Delivery charge", None), line("Cylinder rental", "20.00")])
    )
    assert [p.field for p in problems] == ["lines[1]"]
    assert len(normalised.lines) == 1


def test_an_empty_reading_returns_no_bill_at_all():
    empty = ExtractedLPGInvoice(
        **{
            name: None
            for name in ExtractedLPGInvoice.model_fields
            if name not in ("cylinder_deliveries", "lines")
        },
        cylinder_deliveries=[],
        lines=[],
    )
    normalised, problems = normalise_lpg(empty)
    assert normalised is None
    assert problems[0].field == "(document)"


def test_notes_alone_is_not_a_reading():
    empty = ExtractedLPGInvoice(
        **{
            name: None
            for name in ExtractedLPGInvoice.model_fields
            if name not in ("cylinder_deliveries", "lines", "notes")
        },
        cylinder_deliveries=[],
        lines=[],
        notes="The page was too dark to read.",
    )
    assert empty.is_empty()


# --- consistency ------------------------------------------------------------------------


def test_a_page_that_agrees_with_itself_raises_no_flag():
    normalised, _ = normalise_lpg(reading())
    assert check_consistency_lpg(normalised) == []


def test_a_printed_kg_total_that_disagrees_with_the_cylinders_is_flagged():
    """The check that catches a misread cylinder count on a layout that prints the kg."""
    normalised, _ = normalise_lpg(reading(total_kg_printed="150"))
    flags = check_consistency_lpg(normalised)
    assert len(flags) == 1
    assert flags[0].expected == Decimal(200)
    assert flags[0].printed == Decimal(150)
    assert flags[0].difference == Decimal(-50)


def test_a_line_amount_that_does_not_match_quantity_times_price_is_flagged():
    normalised, _ = normalise_lpg(
        reading(lines=[line("LPG cylinder 50 kg", "650.00", quantity="4", unit_price="165.00")])
    )
    relations = [flag.relation for flag in check_consistency_lpg(normalised)]
    assert "lines[1] amount = round2(quantity x unit_price)" in relations


def test_a_subtotal_that_does_not_match_the_lines_is_flagged():
    normalised, _ = normalise_lpg(reading(subtotal="700.00"))
    relations = [flag.relation for flag in check_consistency_lpg(normalised)]
    assert "subtotal = sum(line amounts)" in relations


def test_gst_and_total_are_each_checked_from_printed_inputs():
    normalised, _ = normalise_lpg(reading(gst_amount="60.00", total="740.00"))
    relations = [flag.relation for flag in check_consistency_lpg(normalised)]
    assert "gst_amount = round2(subtotal x gst_rate)" in relations
    assert "total = subtotal + gst_amount" in relations


def test_each_relation_is_checked_from_printed_inputs_so_one_miss_does_not_cascade():
    """A wrong GST with a total that matches it flags the GST only.

    690.00 + 60.00 = 750.00, so the total relation genuinely holds on the printed figures
    even though the GST is wrong. Checking the total against a RE-DERIVED GST instead
    would raise a second flag for one misread field, and an agent would report two
    problems where there is one.
    """
    normalised, _ = normalise_lpg(reading(gst_amount="60.00", total="750.00"))
    relations = [flag.relation for flag in check_consistency_lpg(normalised)]
    assert relations == ["gst_amount = round2(subtotal x gst_rate)"]


@pytest.mark.parametrize("field", ["subtotal", "gst_amount", "total"])
def test_a_relation_whose_input_was_not_read_is_skipped_not_flagged(field):
    """Absence is a FieldProblem, not a disagreement."""
    normalised, _ = normalise_lpg(reading(**{field: None}))
    for flag in check_consistency_lpg(normalised):
        assert field.replace("_amount", "") not in flag.relation or "sum" in flag.relation


# --- rows that are not charges ----------------------------------------------------------
#
# All three cases below came out of the first live eval run, which read the page correctly
# and exposed defects in this module rather than in the model.


def test_summary_rows_are_not_counted_as_charges():
    """The model transcribed Subtotal, GST and Total as money lines - because they are.

    The first eval run inflated `sum(line amounts)` by S$1,874.80 on lpg_003 for exactly
    this reason. They have their own fields; as charges they would be double-counted.
    """
    normalised, problems = normalise_lpg(
        reading(
            lines=[
                line("LPG cylinder, 50 kg", "825.00", quantity="5", unit_price="165.00"),
                line("Delivery charge", "10.00"),
                line("Cylinder rental", "25.00", quantity="5", unit_price="5.00"),
                line("Subtotal", "860.00"),
                line("GST 9%", "77.40"),
                line("Total payable", "SGD 937.40"),
            ],
            subtotal="860.00",
            gst_amount="77.40",
            total="937.40",
            cylinder_deliveries=[delivery(count="5", size_kg="50 kg")],
        )
    )
    assert problems == []
    assert [line.description for line in normalised.lines] == [
        "LPG cylinder, 50 kg",
        "Delivery charge",
        "Cylinder rental",
    ]
    # And the subtotal relation now closes: 825 + 10 + 25 = 860.
    assert check_consistency_lpg(normalised) == []


def test_a_dash_in_a_money_column_is_no_amount_rather_than_a_parse_failure():
    """The returned-cylinders row prints "—": a count with no money against it."""
    normalised, problems = normalise_lpg(
        reading(
            lines=[
                line("LPG cylinder 50 kg", "660.00", quantity="4", unit_price="165.00"),
                line("Delivery charge", "10.00"),
                line("Cylinder rental", "20.00", quantity="4", unit_price="5.00"),
                line("Empty cylinders collected", "—", quantity="4"),
            ]
        )
    )
    assert problems == []
    assert len(normalised.lines) == 3
    assert "Empty cylinders collected" not in {line.description for line in normalised.lines}


def test_the_two_named_charges_are_recovered_from_their_printed_labels():
    """The key holds them flattened; the page shows them as rows. These bridge the two."""
    normalised, _ = normalise_lpg(reading())
    assert normalised.delivery_charge_sgd == Decimal("10.00")
    assert normalised.rental_charge_sgd == Decimal("20.00")


def test_a_charge_label_the_parser_does_not_know_leaves_the_field_none():
    """A miss the eval reports, rather than whichever row sat in the right position."""
    normalised, _ = normalise_lpg(
        reading(
            lines=[
                line("LPG cylinder 50 kg", "660.00", quantity="4", unit_price="165.00"),
                line("Haulage surcharge", "10.00"),
            ]
        )
    )
    assert normalised.delivery_charge_sgd is None
    assert normalised.rental_charge_sgd is None


def test_a_label_with_a_parenthetical_still_matches():
    normalised, _ = normalise_lpg(
        reading(
            lines=[
                line("LPG cylinder 50 kg", "660.00", quantity="4", unit_price="165.00"),
                line("Cylinder rental (4 x 5.00)", "20.00"),
            ]
        )
    )
    assert normalised.rental_charge_sgd == Decimal("20.00")


# --- charge lines whose quantity is a count word (Block 3, B4) ---------------------------


@pytest.mark.parametrize("quantity", ["1 visit", "2 visits", "1 trip(s)", "3 trips"])
def test_a_delivery_counted_in_words_keeps_its_charge(quantity):
    lines = [
        line("LPG cylinder 50 kg", "660.00", quantity="4", unit_price="165.00"),
        line("Delivery charge", "10.00", quantity=quantity, unit_price="10.00"),
        line("Cylinder rental", "20.00", quantity="4", unit_price="5.00"),
    ]
    invoice, problems = normalise_lpg(reading(lines=lines))
    assert problems == []
    assert invoice.delivery_charge_sgd == Decimal("10.00")


def test_an_unreadable_quantity_is_a_problem_but_the_charge_is_kept():
    lines = [
        line("LPG cylinder 50 kg", "660.00", quantity="4", unit_price="165.00"),
        line("Delivery charge", "10.00", quantity="two-ish", unit_price="10.00"),
        line("Cylinder rental", "20.00", quantity="4", unit_price="5.00"),
    ]
    invoice, problems = normalise_lpg(reading(lines=lines))
    assert [p.field for p in problems] == ["lines[2].quantity"]
    assert invoice.delivery_charge_sgd == Decimal("10.00")
    assert check_consistency_lpg(invoice) == []
