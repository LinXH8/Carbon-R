"""Normalising a fuel-card statement reading, and the litres per product.

The relations under test are the ones this document kind exists to try:

    litres summed PER PRODUCT, never pooled   (two products, two factors)
    a discount reduces money, never litres
    octane grades collapse onto one petrol factor
    an unrecognised fuel is refused rather than mapped to the closest one

No model, no network: `normalise_fuel` is pure.
"""

from decimal import Decimal

import pytest

from carbon_r.models.extraction_fuel import (
    ExtractedFuelCardStatement,
    PrintedProductTotal,
    PrintedTransaction,
    activities_for_fuel,
    check_consistency_fuel,
    kind_for_product,
    normalise_fuel,
)


def txn(product, litres, unit_price, amount, day="03", vehicle="SYN 1241 J"):
    return PrintedTransaction(
        date=f"{day} Mar 2026 07:42",
        vehicle=vehicle,
        station="Pandan Loop Station",
        product=product,
        litres=litres,
        unit_price=unit_price,
        amount=amount,
    )


def total(product, litres, amount):
    return PrintedProductTotal(product=product, litres=litres, amount=amount)


# 62.41 x 2.99 = 186.61 (rounded); 41.08 x 3.17 = 130.22 (rounded)
DIESEL = txn("Diesel", "62.41", "2.99", "186.61")
PETROL = txn("Petrol 95", "41.08", "3.17", "130.22", day="11", vehicle="SYN 5582 K")


def reading(**overrides) -> ExtractedFuelCardStatement:
    """A complete, internally consistent two-product reading."""
    defaults = {
        "vendor_name": "Straits Fleet Card Pte Ltd",
        "vendor_uen": "SYN-0000-0003",
        "customer_name": "Jurong Auto Services Pte Ltd",
        "customer_address": "41 Jalan Pesawat Lama, #01-07, Singapore 610234",
        "account_number": "FLT-4487-01",
        "card_number": "6677-****-****-2201",
        "statement_number": "SFC-202603-8701",
        "document_date": "05 Apr 2026",
        "period_start": "01 Mar 2026",
        "period_end": "31 Mar 2026",
        "transactions": [DIESEL, PETROL],
        "product_totals": [
            total("Diesel", "62.41", "186.61"),
            total("Petrol 95", "41.08", "130.22"),
        ],
        "currency": "SGD",
        "gross_amount": "316.83",
        "discount_amount": "-6.21",
        "subtotal": "310.62",
        "gst_percent": "9",
        "gst_amount": "27.96",
        "total": "338.58",
        "credit_adjustment": None,
        "notes": None,
    }
    return ExtractedFuelCardStatement(**{**defaults, **overrides})


# --- product mapping --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("printed", "kind"),
    [
        ("Diesel", "diesel"),
        ("diesel", "diesel"),
        ("  Diesel  ", "diesel"),
        ("Euro V Diesel", "diesel"),
        ("Petrol 95", "petrol"),
        ("Petrol 98", "petrol"),
        ("Unleaded 95", "petrol"),
    ],
)
def test_printed_products_map_to_the_kind_their_factor_is_published_for(printed, kind):
    assert kind_for_product(printed) == kind


def test_octane_grades_share_one_petrol_factor():
    """DESNZ publishes one petrol row, not one per grade."""
    assert kind_for_product("Petrol 95") == kind_for_product("Petrol 98")


def test_an_unrecognised_fuel_is_refused_rather_than_mapped_to_the_closest():
    """Gas oil is chemically close to diesel and has its OWN, different, DESNZ row."""
    with pytest.raises(ValueError) as caught:
        kind_for_product("Gas oil")
    assert "not a fuel this project has a factor for" in str(caught.value)
    assert "diesel" in str(caught.value)  # the message lists what IS known


# --- litres per product -----------------------------------------------------------------


def test_litres_are_summed_per_product_not_pooled():
    normalised, problems = normalise_fuel(reading())
    assert problems == []
    assert normalised.litres_by_kind == {
        "diesel": Decimal("62.41"),
        "petrol": Decimal("41.08"),
    }


def test_two_octane_grades_become_one_petrol_line():
    """41.08 + 38.50 = 79.58 litres of petrol, under one factor."""
    normalised, _ = normalise_fuel(
        reading(
            transactions=[
                PETROL,
                txn("Petrol 98", "38.50", "3.05", "117.43", day="19", vehicle="SYN 5582 K"),
            ],
            product_totals=[],
        )
    )
    assert normalised.litres_by_kind == {"petrol": Decimal("79.58")}


def test_one_activity_per_product_each_in_litres():
    normalised, _ = normalise_fuel(reading())
    activities = activities_for_fuel(normalised)
    assert [(a.kind, a.amount, a.unit) for a in activities] == [
        ("diesel", Decimal("62.41"), "litres"),
        ("petrol", Decimal("41.08"), "litres"),
    ]
    assert all(a.source_field == "transactions.litres" for a in activities)


def test_activities_are_ordered_by_kind_so_two_runs_diff_cleanly():
    normalised, _ = normalise_fuel(reading(transactions=[PETROL, DIESEL]))
    assert [a.kind for a in activities_for_fuel(normalised)] == ["diesel", "petrol"]


def test_the_activity_records_the_sum_that_produced_it():
    normalised, _ = normalise_fuel(
        reading(
            transactions=[
                DIESEL,
                txn("Diesel", "40.00", "2.99", "119.60", day="17"),
            ],
            product_totals=[],
        )
    )
    activity = activities_for_fuel(normalised)[0]
    assert activity.derivation == "2 diesel transaction(s): 62.41 + 40.00 = 102.41 litres"


def test_no_transactions_means_no_activity():
    normalised, _ = normalise_fuel(reading(transactions=[], product_totals=[]))
    assert normalised.litres_by_kind == {}
    assert activities_for_fuel(normalised) == []


# --- the money decoys -------------------------------------------------------------------


def test_the_discount_reduces_money_and_not_litres():
    """The trap: a rebate looks like a correction and is in the direction of smaller."""
    normalised, _ = normalise_fuel(reading())
    assert normalised.discount_amount_sgd == Decimal("6.21")
    assert normalised.litres_by_kind["diesel"] == Decimal("62.41")
    assert sum(normalised.litres_by_kind.values()) == Decimal("103.49")


def test_a_printed_minus_parses_and_then_becomes_a_magnitude():
    """The sign has to parse - losing it was a defect - but it is not what is stored."""
    normalised, problems = normalise_fuel(reading(discount_amount="-S$6.21"))
    assert problems == []
    assert normalised.discount_amount_sgd == Decimal("6.21")


def test_money_totals_are_not_mistaken_for_quantities():
    normalised, _ = normalise_fuel(reading())
    assert normalised.gross_amount_sgd == Decimal("316.83")
    assert normalised.total_sgd == Decimal("338.58")
    # Neither appears among the litres.
    assert Decimal("316.83") not in normalised.litres_by_kind.values()


# --- one miss costs one row -------------------------------------------------------------


def test_a_row_without_litres_is_one_problem_and_is_left_out():
    normalised, problems = normalise_fuel(
        reading(
            transactions=[DIESEL, txn("Petrol 95", None, "3.17", "130.22")],
            product_totals=[],
        )
    )
    assert [p.field for p in problems] == ["transactions[2]"]
    assert "readable litres" in problems[0].reason
    assert normalised.litres_by_kind == {"diesel": Decimal("62.41")}


def test_a_row_without_a_product_is_one_problem():
    """No product means no factor applies, so the litres cannot be used."""
    normalised, problems = normalise_fuel(
        reading(transactions=[DIESEL, txn(None, "41.08", "3.17", "130.22")], product_totals=[])
    )
    assert [p.field for p in problems] == ["transactions[2]"]
    assert "no emission factor applies" in problems[0].reason


def test_an_unknown_product_on_one_row_does_not_lose_the_others():
    normalised, problems = normalise_fuel(
        reading(
            transactions=[DIESEL, txn("Kerosene", "41.08", "3.17", "130.22")],
            product_totals=[],
        )
    )
    assert [p.field for p in problems] == ["transactions[2]"]
    assert normalised.litres_by_kind == {"diesel": Decimal("62.41")}


def test_a_transaction_time_does_not_defeat_the_date_parser():
    normalised, problems = normalise_fuel(reading())
    assert problems == []
    assert normalised.transactions[0].date.isoformat() == "2026-03-03"


def test_an_empty_reading_returns_no_statement_at_all():
    empty = ExtractedFuelCardStatement(
        **{
            name: None
            for name in ExtractedFuelCardStatement.model_fields
            if name not in ("transactions", "product_totals")
        },
        transactions=[],
        product_totals=[],
    )
    normalised, problems = normalise_fuel(empty)
    assert normalised is None
    assert problems[0].field == "(document)"


# --- consistency ------------------------------------------------------------------------


def test_a_statement_that_agrees_with_itself_raises_no_flag():
    normalised, _ = normalise_fuel(reading())
    assert check_consistency_fuel(normalised) == []


def test_printed_product_litres_that_disagree_with_the_rows_are_flagged():
    """The only check that can catch a misread litre figure."""
    normalised, _ = normalise_fuel(
        reading(
            product_totals=[
                total("Diesel", "72.41", "186.61"),
                total("Petrol 95", "41.08", "130.22"),
            ]
        )
    )
    flags = [f for f in check_consistency_fuel(normalised) if "litres[diesel]" in f.relation]
    assert len(flags) == 1
    assert flags[0].expected == Decimal("62.41")
    assert flags[0].printed == Decimal("72.41")


def test_a_product_totalled_on_the_page_with_no_rows_read_is_flagged():
    """The transaction table was read short, and the summary says so."""
    normalised, _ = normalise_fuel(reading(transactions=[DIESEL]))
    flags = [f for f in check_consistency_fuel(normalised) if "litres[petrol]" in f.relation]
    assert len(flags) == 1
    assert flags[0].expected == Decimal(0)
    assert flags[0].printed == Decimal("41.08")


def test_a_row_amount_that_does_not_match_litres_times_price_is_flagged():
    normalised, _ = normalise_fuel(
        reading(transactions=[txn("Diesel", "62.41", "2.99", "180.00")], product_totals=[])
    )
    relations = [f.relation for f in check_consistency_fuel(normalised)]
    assert "transactions[1] amount = round2(litres x unit_price)" in relations


def test_the_gross_amount_is_checked_against_the_rows():
    normalised, _ = normalise_fuel(reading(gross_amount="300.00"))
    relations = [f.relation for f in check_consistency_fuel(normalised)]
    assert "gross_amount = sum(transaction amounts)" in relations


def test_the_gross_total_is_not_checked_when_a_row_amount_was_not_read():
    """Comparing a short sum against the printed gross would flag a problem already known."""
    normalised, _ = normalise_fuel(
        reading(transactions=[DIESEL, txn("Petrol 95", "41.08", "3.17", None)])
    )
    relations = [f.relation for f in check_consistency_fuel(normalised)]
    assert "gross_amount = sum(transaction amounts)" not in relations


def test_the_discount_relation_is_checked():
    """Since Tier 2 Block 2 the relation also subtracts a credit for an earlier statement."""
    normalised, _ = normalise_fuel(reading(subtotal="300.00"))
    relations = [f.relation for f in check_consistency_fuel(normalised)]
    assert "subtotal = gross_amount - discount - credit_adjustment" in relations


def test_a_printed_credit_closes_the_subtotal_and_is_stored_as_a_magnitude():
    """310.62 - 12.40 = 298.22: the credit is a deduction on the page, a magnitude in the key."""
    normalised, problems = normalise_fuel(reading(credit_adjustment="−12.40", subtotal="298.22"))
    assert not problems
    assert normalised.credit_adjustment_sgd == Decimal("12.40")
    relations = [f.relation for f in check_consistency_fuel(normalised)]
    assert "subtotal = gross_amount - discount - credit_adjustment" not in relations


# --- minus signs ------------------------------------------------------------------------
#
# From the first live eval run, which lost every discount on every statement: the template
# writes `&minus;`, and the PDF text layer handed back U+2212 on some documents and U+2013
# on others. Only ASCII "-" was being treated as a sign.


@pytest.mark.parametrize(
    "printed",
    [
        "-6.21",  # hyphen-minus
        "−6.21",  # U+2212 minus sign, what &minus; renders as
        "–6.21",  # U+2013 en dash, what the text layer sometimes returns
        "−S$6.21",
        "S$-6.21",
    ],
)
def test_every_minus_glyph_a_layout_may_use_parses_rather_than_failing(printed):
    """Before the fix, each of these was an unparsed field and a lost discount."""
    normalised, problems = normalise_fuel(reading(discount_amount=printed))
    assert problems == []
    assert normalised.discount_amount_sgd == Decimal("6.21")


def test_the_discount_is_stored_as_a_magnitude_to_match_the_answer_key():
    """The key declares it `ge=0`; the page prints it as a deduction. Same quantity.

    Resolving the convention here rather than in the eval's comparison keeps the field
    name meaning one thing on both sides - which is the only reason the names match.
    """
    for printed in ("−15.49", "15.49", "-15.49"):
        normalised, problems = normalise_fuel(reading(discount_amount=printed))
        assert problems == []
        assert normalised.discount_amount_sgd == Decimal("15.49")


def test_the_discount_relation_holds_whichever_way_the_page_printed_the_sign():
    for printed in ("−6.21", "6.21"):
        normalised, _ = normalise_fuel(reading(discount_amount=printed))
        assert check_consistency_fuel(normalised) == []
