"""The LPG and fuel-card generators, and the answer keys they produce.

Spec Part 8's requirements, applied to the two kinds added in Tier 2 Block 1:

- same seed, same output (and a different seed, different output);
- every key validates, and its arithmetic is rechecked independently of the validator;
- adding a customer does not change any existing document;
- worked examples asserted with injected random values, so the expectation does not
  depend on what the RNG happened to draw;
- at least one document per kind whose period year differs from its document-date year.

Offline: no `.env`, no database, no API key. WeasyPrint is not imported - rendering is
tested at the HTML level only.
"""

from decimal import Decimal

import pytest

from carbon_r.models.fuel_card_statement import FuelCardStatement, FuelTransaction
from carbon_r.models.lpg_invoice import CylinderLine, LPGInvoice
from carbon_r.money import round2
from carbon_r.synthgen import fuel_card, lpg
from carbon_r.synthgen.profiles import (
    FUEL_CUSTOMERS,
    FUEL_PRICE_LISTS,
    LPG_CUSTOMERS,
    customer_by_key,
    fuel_price_list,
    lpg_price_list,
)
from carbon_r.synthgen.render import render_html, template_for

LPG_SET = lpg.build_invoice_series()
FUEL_SET = fuel_card.build_statement_series()


# --- determinism and stability ----------------------------------------------------------


def test_the_same_seed_gives_the_same_lpg_set():
    assert [i.model_dump() for i in lpg.build_invoice_series()] == [i.model_dump() for i in LPG_SET]


def test_the_same_seed_gives_the_same_fuel_set():
    assert [s.model_dump() for s in fuel_card.build_statement_series()] == [
        s.model_dump() for s in FUEL_SET
    ]


def test_a_different_seed_gives_different_documents():
    other = lpg.build_invoice_series(seed=lpg.DEFAULT_SEED + 1)
    assert [i.lpg_kg for i in other] != [i.lpg_kg for i in LPG_SET]


def test_adding_a_customer_does_not_change_any_existing_document():
    """Document numbers come from `first_doc_number`, not from iteration order (spec 1.4).

    Since Tier 2 Block 2 a customer carries no price list of its own - the list is chosen
    by the supply period's year - so the newcomer is built without one, and the existing
    invoices must still be untouched.
    """
    extra = LPG_CUSTOMERS[0].__class__(
        key="jurong_auto",
        account_number="GAS-9999-99",
        base_kg=100,
        seasonal_amplitude=0.05,
        cylinder_size_kg=Decimal("50"),
        start_year=2026,
        start_month=4,
        first_doc_number=90,
    )
    extended = lpg.build_invoice_series(customers=(*LPG_CUSTOMERS, extra))
    before = {i.doc_id: i.model_dump() for i in LPG_SET}
    assert len(extended) == len(LPG_SET) + 3
    for invoice in extended:
        if invoice.doc_id in before:
            assert invoice.model_dump() == before[invoice.doc_id]


def test_document_ids_are_unique_and_prefixed_by_kind():
    """So an eval that mixes kinds cannot collide two documents (spec 3.2)."""
    lpg_ids = [i.doc_id for i in LPG_SET]
    fuel_ids = [s.doc_id for s in FUEL_SET]
    assert len(set(lpg_ids)) == len(lpg_ids)
    assert len(set(fuel_ids)) == len(fuel_ids)
    assert all(i.startswith("lpg_") for i in lpg_ids)
    assert all(f.startswith("fuel_") for f in fuel_ids)
    assert not set(lpg_ids) & set(fuel_ids)


# --- the cylinder model -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("wanted", "size", "expected"),
    [
        # Rounds UP: a part cylinder cannot be delivered.
        ("190", "50", 4),
        ("200", "50", 4),  # exactly on a boundary: no spurious extra cylinder
        ("200.01", "50", 5),
        ("1", "50", 1),
        ("0", "50", 1),  # at least one: a delivery of nothing is not a delivery
        ("38.1", "12.7", 3),
        ("38.2", "12.7", 4),
    ],
)
def test_cylinders_round_up_to_cover_the_gas_wanted(wanted, size, expected):
    assert lpg.cylinders_for(Decimal(wanted), Decimal(size)) == expected


def test_a_zero_cylinder_size_is_refused_rather_than_dividing():
    with pytest.raises(ValueError):
        lpg.cylinders_for(Decimal(100), Decimal(0))


def test_monthly_kg_with_injected_randomness_is_exactly_computable():
    """base x seasonal x noise x days / 30.4, with both random terms fixed (spec 1.5).

    200 x 1.0 x 1.0 x 31 / 30.4 = 203.947368..., rounded to 203.95 by the generator.
    """
    value = lpg.monthly_lpg_kg(200, 2026, 3, 0.0, noise=1.0, seasonal=1.0)
    assert value == Decimal("203.95")


def test_the_day_count_term_makes_february_smaller_than_march():
    march = lpg.monthly_lpg_kg(200, 2026, 3, 0.0, noise=1.0, seasonal=1.0)
    february = lpg.monthly_lpg_kg(200, 2026, 2, 0.0, noise=1.0, seasonal=1.0)
    assert february < march
    # 2024 is a leap year, so February is one day longer than in 2026.
    assert lpg.monthly_lpg_kg(200, 2024, 2, 0.0, noise=1.0, seasonal=1.0) > february


def test_monthly_kg_needs_either_an_rng_or_an_injected_noise():
    with pytest.raises(ValueError):
        lpg.monthly_lpg_kg(200, 2026, 3, 0.1)


# --- LPG keys ---------------------------------------------------------------------------


def test_every_lpg_key_validates_and_its_kg_is_the_cylinder_product():
    for invoice in LPG_SET:
        gas = sum((Decimal(c.count) * c.size_kg for c in invoice.cylinders), Decimal(0))
        assert invoice.lpg_kg == gas


def test_lpg_money_rechecked_independently_of_the_validator():
    """Spec 1.3: a validator cannot be its own witness."""
    for invoice in LPG_SET:
        cylinders = sum(
            (round2(Decimal(c.count) * c.price_per_cylinder_sgd) for c in invoice.cylinders),
            Decimal(0),
        )
        rental = round2(Decimal(invoice.cylinder_count) * invoice.rental_per_cylinder_sgd)
        subtotal = cylinders + invoice.delivery_charge_sgd + rental
        gst = round2(subtotal * invoice.gst_rate)
        assert invoice.subtotal_sgd == subtotal
        assert invoice.gst_amount_sgd == gst
        assert invoice.total_sgd == subtotal + gst


def test_an_lpg_key_whose_kg_does_not_match_its_cylinders_cannot_be_built():
    """The relation that matters most on this document kind."""
    base = LPG_SET[0].model_dump()
    base["lpg_kg"] = Decimal("999")
    with pytest.raises(ValueError) as caught:
        LPGInvoice.model_validate(base)
    assert "sum(count x size_kg)" in str(caught.value)


def test_an_lpg_key_with_inconsistent_money_cannot_be_built():
    base = LPG_SET[0].model_dump()
    base["total_sgd"] = Decimal("1.00")
    with pytest.raises(ValueError):
        LPGInvoice.model_validate(base)


def test_an_invoice_cannot_predate_the_period_it_bills():
    base = LPG_SET[0].model_dump()
    base["document_date"] = base["period_start"]
    with pytest.raises(ValueError) as caught:
        LPGInvoice.model_validate(base)
    assert "cannot be issued before" in str(caught.value)


def test_the_cylinder_line_computes_its_own_kg_and_amount():
    line = CylinderLine(count=3, size_kg=Decimal("12.7"), price_per_cylinder_sgd=Decimal("48.50"))
    assert line.kg == Decimal("38.1")
    assert line.amount_sgd == Decimal("145.50")


def test_the_base_lpg_layout_never_prints_a_kg_total():
    """The activity has to be computed from the cylinder line - the `lpg_cylinders` case."""
    assert all(not invoice.prints_total_kg for invoice in LPG_SET)


def test_the_rendered_lpg_page_shows_the_cylinders_but_not_the_kg_total():
    """The catering kitchen's invoice: 50 kg and 12.7 kg lines, and no kg total printed.

    Block 1 asserted this on the dental clinic's single 12.7 kg line. The clinic was
    replaced (a clinic does not cook); the kitchen keeps the fractional size on a second
    line, which is the harder reading - two products of count x size to add.
    """
    invoice = next(i for i in LPG_SET if i.doc_id == "lpg_007")
    assert invoice.customer_name == "Tampines Catering Kitchen Pte Ltd"
    assert [line.size_kg for line in invoice.cylinders] == [Decimal("50"), Decimal("12.7")]
    html = render_html(invoice)
    for line in invoice.cylinders:
        assert f"LPG cylinder, {_size(line.size_kg)} kg" in html
        assert f'<td class="num">{line.count}</td>' in html
    assert str(invoice.lpg_kg) not in html  # the 188.1 kg activity appears nowhere
    assert invoice.quirks == ["lpg_cylinders"]
    assert template_for(invoice) == "lpg_invoice_a.html.j2"


def _size(value: Decimal) -> str:
    return "50" if value == Decimal("50") else str(value)


def test_the_lpg_page_labels_rental_as_not_a_supply_of_gas():
    """The decoy is a decoy, but the page is not dishonest about what it is."""
    html = render_html(LPG_SET[0])
    assert "not a supply of gas" in html


# --- the fuel-card model ----------------------------------------------------------------


def test_litres_for_a_fill_with_an_injected_fraction_is_exactly_computable():
    vehicle = FUEL_CUSTOMERS[0].fleet.vehicles[0]
    assert vehicle.tank_litres == 70
    assert fuel_card.litres_for_fill(vehicle, None, fraction=0.6) == Decimal("42.00")


def test_a_fill_is_never_the_whole_tank_nor_trivially_small():
    low, high = fuel_card.FILL_FRACTION_RANGE
    assert 0.4 < low < high < 1.0


def test_an_injected_fill_count_fixes_the_number_of_transactions():
    assert fuel_card.fills_for_vehicle(3, None, count=2) == 2


def test_the_fill_count_is_never_zero():
    class AlwaysMinus:
        def randint(self, *_):
            return -5

    assert fuel_card.fills_for_vehicle(1, AlwaysMinus()) == 1


def test_every_fuel_key_validates_and_litres_are_per_product():
    for statement in FUEL_SET:
        derived: dict[str, Decimal] = {}
        for t in statement.transactions:
            derived[t.product] = derived.get(t.product, Decimal(0)) + t.litres
        assert dict(statement.litres_by_product) == derived


def test_fuel_money_rechecked_independently_of_the_validator():
    for statement in FUEL_SET:
        gross = sum(
            (round2(t.litres * t.unit_price_sgd) for t in statement.transactions), Decimal(0)
        )
        litres = sum(statement.litres_by_product.values(), Decimal(0))
        discount = round2(litres * statement.discount_per_litre_sgd)
        subtotal = gross - discount
        gst = round2(subtotal * statement.gst_rate)
        assert statement.gross_amount_sgd == gross
        assert statement.discount_amount_sgd == discount
        assert statement.total_sgd == subtotal + gst


def test_a_statement_with_pooled_litres_cannot_be_built():
    """The field is per product; a single pooled figure is not a quantity it can hold."""
    base = FUEL_SET[0].model_dump()
    base["litres_by_product"] = {"Diesel": sum(base["litres_by_product"].values())}
    with pytest.raises(ValueError) as caught:
        FuelCardStatement.model_validate(base)
    assert "sum of transaction litres per product" in str(caught.value)


def test_a_transaction_amount_that_does_not_match_its_litres_cannot_be_built():
    with pytest.raises(ValueError) as caught:
        FuelTransaction(
            date=FUEL_SET[0].period_start,
            time="07:42",
            vehicle="SYN 1241 J",
            station="S",
            product="Diesel",
            litres=Decimal("62.41"),
            unit_price_sgd=Decimal("2.99"),
            amount_sgd=Decimal("1.00"),
        )
    assert "round2(62.41 x 2.99)" in str(caught.value)


def test_a_transaction_outside_the_statement_period_cannot_be_built():
    base = FUEL_SET[0].model_dump()
    rows = list(base["transactions"])
    rows[0] = {**rows[0], "date": base["period_start"].replace(year=2020)}
    base["transactions"] = tuple(rows)
    with pytest.raises(ValueError) as caught:
        FuelCardStatement.model_validate(base)
    assert "outside the statement period" in str(caught.value)


def test_there_is_no_total_litres_field_to_apply_one_factor_to():
    """Litres of diesel and litres of petrol are not the same quantity."""
    assert "total_litres" not in FuelCardStatement.model_fields
    # The document statistic exists, named at length, and is not a model field.
    assert "total_litres_all_products" not in FuelCardStatement.model_fields
    assert FUEL_SET[0].total_litres_all_products > 0


def test_at_least_one_statement_carries_two_products():
    """The case the pairs-based calculation exists for has to be in the set."""
    mixed = [s for s in FUEL_SET if len(s.litres_by_product) > 1]
    assert mixed, "no multi-product statement in the set"
    assert all(len(s.litres_by_product) == 2 for s in mixed)


def test_prices_belong_to_the_vendor_so_two_customers_pay_the_same():
    """A price list is the vendor's, like a tariff plan (spec Part 2, lesson 4).

    Since Tier 2 Block 2 there is one list per year, chosen by the statement period's year,
    so every customer's statement in a year names the same list and pays its prices.
    """
    for statement in fuel_card.build_v1():
        year = statement.period_start.year
        prices = fuel_price_list(year)
        assert statement.price_list_code == prices.code == f"SFC-PUMP-{year % 100:02d}"
        month = prices.prices[(year, statement.period_start.month)]
        for transaction in statement.transactions:
            assert transaction.unit_price_sgd == month[transaction.product]
    for code, prices in FUEL_PRICE_LISTS.items():
        for (year, month), by_product in prices.prices.items():
            assert code.endswith(f"{year % 100:02d}")
            assert by_product["Petrol 95"] > by_product["Diesel"], f"{year}-{month}"


def test_each_lpg_invoice_uses_its_supply_years_price_list():
    """Block 1 printed IGS-COM-26 on a December 2024 invoice; the code must match the year."""
    for invoice in lpg.build_v1():
        prices = lpg_price_list(invoice.period_start.year)
        assert invoice.price_list_code == prices.code
        for line in invoice.cylinders:
            assert line.price_per_cylinder_sgd == prices.price_per_cylinder_sgd[line.size_kg]
        assert invoice.delivery_fee_sgd == prices.delivery_charge_sgd
        assert invoice.rental_per_cylinder_sgd == prices.rental_per_cylinder_sgd


def test_a_month_with_no_published_price_is_refused_rather_than_substituted():
    """Falling back to another month's - or another year's - price would be a figure from
    nowhere. Since Tier 2 Block 2 the year's list is looked up first, so a year with no
    list is refused by name."""
    customer = FUEL_CUSTOMERS[0]
    with pytest.raises(ValueError) as caught:
        fuel_card.build_statement(
            customer,
            2099,
            1,
            "fuel_099",
            fuel_card.DEFAULT_SEED,
            customer_address="somewhere",
        )
    assert "No fuel price list SFC-PUMP-99" in str(caught.value)


def test_a_month_missing_from_an_existing_years_list_is_also_refused():
    customer = FUEL_CUSTOMERS[0]
    with pytest.raises(ValueError, match="no prices for 2024-01"):
        fuel_card.build_statement(
            customer, 2024, 1, "fuel_099", fuel_card.DEFAULT_SEED, customer_address="somewhere"
        )


def test_transactions_are_chronological_and_totally_ordered():
    """Two fills at the same minute must not order differently between runs."""
    for statement in FUEL_SET:
        keys = [(t.date, t.time, t.vehicle) for t in statement.transactions]
        assert keys == sorted(keys)


def test_the_rendered_fuel_page_shows_litres_and_the_rebate():
    statement = next(s for s in FUEL_SET if len(s.litres_by_product) > 1)
    html = render_html(statement)
    for product in statement.products:
        assert product in html
    assert "Fleet rebate" in html
    assert "does not change the volume of fuel drawn" in html
    assert template_for(statement) == "fuel_card_statement_a.html.j2"


# --- cross-kind properties --------------------------------------------------------------


def test_each_kind_has_a_document_whose_period_year_differs_from_its_document_date_year():
    """Spec Part 8, item 11: so an eval can catch a model reading the wrong date."""
    assert any(i.period_start.year != i.document_date.year for i in LPG_SET)
    assert any(s.period_start.year != s.document_date.year for s in FUEL_SET)


def test_the_factor_year_comes_from_the_period_and_not_the_document_date():
    for document in (*LPG_SET, *FUEL_SET):
        assert document.factor_year == document.period_start.year


def test_one_lpg_invoice_predates_the_loaded_desnz_series():
    """Deliberate: it is the live `no_results` path, which Tier 1 never reached.

    DESNZ publishes a standalone set per publication year and only 2025 and 2026 are
    loaded, so a 2024 supply period has no factor at or before it.
    """
    early = [i for i in LPG_SET if i.factor_year < 2025]
    assert len(early) == 1, "exactly one document should reach the refusal"
    assert early[0].doc_id == "lpg_001"


def test_identities_are_shared_with_the_electricity_profiles():
    """One business has one legal name, whichever document it appears on.

    Checked over the whole v1 set: the three fleets added in Block 2 have no Block 1
    statements, so the Block 1 set alone would leave them untested.
    """
    v1 = (*lpg.build_v1(), *fuel_card.build_v1())
    for customer in (*LPG_CUSTOMERS, *FUEL_CUSTOMERS):
        profile = customer_by_key(customer.key)
        documents = [d for d in v1 if d.account_number == customer.account_number]
        assert documents, customer.key
        assert all(d.customer_name == profile.name for d in documents)


def test_block1_documents_carry_only_their_structural_quirks():
    """The Block 1 documents stay clean: no INJECTED quirk. What they do carry is what
    they structurally are (Tier 2 Block 2): every layout-`a` LPG invoice prints no kg
    total, so it is `lpg_cylinders`; a statement with two products is `mixed_fuel`."""
    assert all(i.quirks == ["lpg_cylinders"] and not i.prints_total_kg for i in LPG_SET)
    for statement in FUEL_SET:
        expected = ["mixed_fuel"] if len(statement.litres_by_product) > 1 else []
        assert statement.quirks == expected
    assert any(s.quirks == ["mixed_fuel"] for s in FUEL_SET)
    assert all(d.difficulty_tier == "clean" for d in (*LPG_SET, *FUEL_SET))


def test_no_layout_is_held_out_while_there_is_only_one():
    """A holdout only means something once there are layouts to tune on."""
    assert all(not d.is_holdout_layout for d in (*LPG_SET, *FUEL_SET))
    assert {d.layout_id for d in LPG_SET} == {"lpg_a"}
    assert {d.layout_id for d in FUEL_SET} == {"fuel_a"}


def test_every_identifier_is_self_evidently_synthetic():
    """Spec 1.7: nothing that could collide with a real UEN, account or plate."""
    for document in (*LPG_SET, *FUEL_SET):
        assert document.vendor_uen.startswith("SYN-")
    for statement in FUEL_SET:
        for transaction in statement.transactions:
            assert transaction.vehicle.startswith("SYN ")


def test_the_generator_records_no_emission_factor_and_no_emissions(tmp_path):
    """Spec 3.4: the generator produces activity data and its truth, nothing else."""
    for document in (*LPG_SET, *FUEL_SET):
        serialised = document.model_dump_json()
        for token in ("co2", "emission", "factor_kg", "2939.36095", "2.66155"):
            assert token not in serialised.lower().replace("price_list_code", "")


def test_cylinder_quantisation_is_recorded_as_a_limit_not_hidden():
    """Gas comes in whole cylinders, so an account's monthly kg barely varies.

    This is a real property of the document kind rather than a defect, and it is a known
    limit of this set for eval purposes: an account's three invoices often carry the same
    kg. Asserted so that the limit is visible in the suite rather than only in prose - if
    a later change makes the set vary more, this test should be updated deliberately.
    """
    distinct = {i.account_number: set() for i in LPG_SET}
    for invoice in LPG_SET:
        distinct[invoice.account_number].add(invoice.lpg_kg)
    assert any(len(values) == 1 for values in distinct.values())
    # Across the whole set there is still real variation to score against.
    assert len({i.lpg_kg for i in LPG_SET}) >= 4


def test_fuel_statements_vary_because_litres_are_summed_from_individual_fills():
    """The variation the LPG set cannot have: no two statements carry the same litres."""
    totals = [s.total_litres_all_products for s in FUEL_SET]
    assert len(set(totals)) == len(totals)
