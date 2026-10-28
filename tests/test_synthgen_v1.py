"""synthgen v1 (Tier 2 Block 2): the plan, the quirks, the layouts and the degradation.

Every expected literal here was either produced by a script that did not call the code
under test (noted beside it) or read off a document hand-checked in an earlier block.
Offline: no `.env`, no database, no API key. The one database check skips without one.
"""

import json
import os
from collections import Counter
from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from carbon_r.models.quirks import INJECTED_QUIRKS, QUIRKS, injected_for
from carbon_r.synthgen import electricity, fuel_card, lpg
from carbon_r.synthgen.degrade import apply, jpeg_roundtrip, params_for
from carbon_r.synthgen.layouts import LAYOUTS
from carbon_r.synthgen.plan import (
    CROSS_MONTH,
    CROSS_YEAR,
    ELEC,
    FACTOR_YEARS,
    FUEL,
    LAST_PERIOD_MONTH,
    LPG,
    MAX_FACTOR_YEAR_GAP,
    MIN_PER_QUIRK,
    SERIES,
    Window,
    plan_all,
    plan_kind,
    quirk_table,
    shortfalls,
)
from carbon_r.synthgen.profiles import (
    CUSTOMER_PROFILES,
    FUEL_CUSTOMERS,
    FUEL_DISCOUNT_PER_LITRE_SGD,
    LPG_CUSTOMERS,
    CustomerProfile,
    FuelCustomer,
    LPGCustomer,
    customer_by_key,
)
from carbon_r.synthgen.quirks import INJECTORS
from carbon_r.synthgen.render import render_html

SEED = electricity.DEFAULT_SEED
ELEC_SET = electricity.build_v1()
LPG_SET = lpg.build_v1()
FUEL_SET = fuel_card.build_v1()
ALL = (*ELEC_SET, *LPG_SET, *FUEL_SET)
PLANS = plan_all(SEED)


# --- added requirement 1: appending a profile changes no existing document's dress ------


def _dress(plans):
    return {p.doc_id: (p.layout, p.tier, p.quirks) for p in plans}


@pytest.mark.parametrize(
    ("kind", "newcomer", "window"),
    [
        (
            ELEC,
            CustomerProfile(
                key="newcomer",
                name="Paya Lebar Bakery Pte Ltd",
                account_number="8820-0001-09",
                plan_code="SPR-LT-FIX-25",
                base_kwh=4_400,
                seasonal_amplitude=0.09,
                start_year=2025,
                start_month=3,
                first_doc_number=0,
                street="Jalan Roti Manis",
                postal_sector="53",
                ground_floor_only=True,
            ),
            Window(ELEC, "newcomer", 94, (2026, 1), 8),
        ),
        (
            LPG,
            LPGCustomer(
                key="newcomer",
                account_number="GAS-0001-09",
                base_kg=120,
                seasonal_amplitude=0.05,
                cylinder_size_kg=Decimal("50"),
                start_year=2025,
                start_month=3,
                first_doc_number=0,
            ),
            Window(LPG, "newcomer", 52, (2025, 3), 8),
        ),
        (
            FUEL,
            FuelCustomer(
                key="newcomer",
                account_number="FLT-0001-09",
                fleet=FUEL_CUSTOMERS[0].fleet,
                start_year=2025,
                start_month=3,
                first_doc_number=0,
            ),
            Window(FUEL, "newcomer", 55, (2025, 3), 8),
        ),
    ],
)
def test_appending_a_profile_leaves_every_existing_documents_dress_unchanged(
    kind, newcomer, window
):
    """A profile appended at the END, numbered after everything, moves no layout, tier or
    quirk on any existing document - because assignment visits documents in number order
    and least-used-first only looks at documents numbered before the one being dressed."""
    accounts = {ELEC: CUSTOMER_PROFILES, LPG: LPG_CUSTOMERS, FUEL: FUEL_CUSTOMERS}[kind]
    before = _dress(plan_kind(kind, SEED))
    after = _dress(plan_kind(kind, SEED, series=(*SERIES, window), accounts=(*accounts, newcomer)))
    assert len(after) == len(before) + window.count
    assert {doc_id: after[doc_id] for doc_id in before} == before


def test_new_profiles_are_appended_at_the_end_of_every_profile_tuple():
    """The rule the test above relies on, checked on the profiles Block 2 added."""
    assert CUSTOMER_PROFILES[-1].key == "tampines_catering"
    assert [c.key for c in FUEL_CUSTOMERS[-3:]] == [
        "kopi_corner",
        "kranji_cold_store",
        "tampines_catering",
    ]
    block1_numbers = [p.number for p in PLANS[ELEC] if p.group == "block1"]
    assert max(block1_numbers) < min(p.number for p in PLANS[ELEC] if p.group != "block1")


# --- added requirement 2: the hard-tier confound is visible in the suite -----------------


def test_hard_lpg_and_fuel_documents_carry_all_three_injected_quirks():
    """LPG and fuel have only three injected quirks, and the hard tier takes 3, so every
    hard LPG or fuel document carries the same three. A hard-tier failure there cannot be
    attributed to one quirk - recorded as a known limit in docs/blocks/TIER2_BLOCK2.md."""
    for document in (*LPG_SET, *FUEL_SET):
        if document.difficulty_tier == "hard":
            injected = [q for q in document.quirks if not QUIRKS[q].structural]
            assert injected == list(injected_for(document.doc_kind))
    assert len(injected_for(LPG)) == len(injected_for(FUEL)) == 3


def test_lpg_and_fuel_injected_quirks_are_all_money_decoys():
    """So a hard LPG or fuel document is harder to READ, not harder to interpret: the
    activity-changing traps there (cylinders, mixed fuel) are structural."""
    assert (
        set(injected_for(LPG))
        == set(injected_for(FUEL))
        == {
            "credit_adjustment",
            "balance_forward",
            "gst_confusion",
        }
    )
    assert QUIRKS["lpg_cylinders"].structural and QUIRKS["mixed_fuel"].structural


# --- added requirement 3: the two period quirks never meet --------------------------------


def test_cross_month_and_cross_year_never_appear_on_the_same_document():
    for document in ELEC_SET:
        assert not {"cross_month_period", "cross_year_period"} <= set(document.quirks)
    for plan in PLANS[ELEC]:
        assert not {"cross_month_period", "cross_year_period"} <= set(plan.quirks)


def test_a_key_claiming_both_period_quirks_cannot_be_built():
    bill = next(b for b in ELEC_SET if "cross_year_period" in b.quirks)
    with pytest.raises(ValidationError, match="cross_month_period"):
        type(bill).model_validate(
            {**bill.model_dump(), "quirks": [*bill.quirks, "cross_month_period"]}
        )


# --- added requirement 4: the Block 1 fleets keep the old rebate ---------------------------


def test_the_block1_fleets_keep_the_old_global_rebate():
    pinned = {c.key: c.discount_per_litre_sgd for c in FUEL_CUSTOMERS[:2]}
    assert pinned == {
        "jurong_auto": Decimal("0.06"),
        "lion_city_laundry": Decimal("0.06"),
    }
    assert Decimal("0.06") == FUEL_DISCOUNT_PER_LITRE_SGD
    assert {c.discount_per_litre_sgd for c in FUEL_CUSTOMERS[2:]} == {
        Decimal("0.05"),
        Decimal("0.08"),
        Decimal("0.04"),
    }


def test_fuel_002_money_matches_the_block1_hand_check():
    """Hand-checked with a calculator in Tier 2 Block 1: rebate 258.23 L x 0.06 = 15.49,
    gross 773.22, total 825.93. A rebate change would move all three."""
    statement = next(s for s in FUEL_SET if s.doc_id == "fuel_002")
    assert statement.discount_amount_sgd == Decimal("15.49")
    assert statement.gross_amount_sgd == Decimal("773.22")
    assert statement.total_sgd == Decimal("825.93")


# --- the plan --------------------------------------------------------------------------


def test_every_injected_quirk_reaches_the_floor_per_kind():
    assert shortfalls(PLANS) == []
    for row in quirk_table(PLANS):
        assert row["total"] >= MIN_PER_QUIRK, row


def test_the_plan_is_deterministic():
    assert plan_all(SEED) == PLANS


def test_the_set_is_inside_the_spec_size():
    assert len(ALL) == 198
    assert 100 <= len(ALL) <= 200
    assert Counter(d.doc_kind for d in ALL) == {
        "electricity_bill": 93,
        "lpg_invoice": 51,
        "fuel_card_statement": 54,
    }


def test_every_layout_and_tier_cell_is_populated_including_the_holdout():
    for documents in (ELEC_SET, LPG_SET, FUEL_SET):
        cells = {(d.layout_id, d.difficulty_tier) for d in documents}
        layouts = {d.layout_id for d in documents}
        assert len(layouts) == 4
        for layout_id in layouts:
            for tier in ("clean", "moderate", "hard"):
                assert (layout_id, tier) in cells, (layout_id, tier)


def test_exactly_one_holdout_layout_per_kind():
    """Spec 8.12."""
    for prefix in ("elec", "lpg", "fuel"):
        held = [
            lid for lid, shape in LAYOUTS.items() if lid.startswith(prefix) and shape.is_holdout
        ]
        assert held == [f"{prefix}_d"]
    for document in ALL:
        assert document.is_holdout_layout == LAYOUTS[document.layout_id].is_holdout


def test_block1_documents_stay_clean_on_layout_a():
    block1 = {p.doc_id for plans in PLANS.values() for p in plans if p.group == "block1"}
    assert len(block1) == 36
    for document in ALL:
        if document.doc_id in block1:
            assert document.difficulty_tier == "clean"
            assert document.layout_id.endswith("_a")
            assert not [q for q in document.quirks if not QUIRKS[q].structural]


def test_clean_documents_carry_no_injected_quirk_and_tiers_carry_their_count():
    for document in ALL:
        injected = [q for q in document.quirks if not QUIRKS[q].structural]
        if document.difficulty_tier == "clean":
            assert injected == []
        elif document.difficulty_tier == "moderate":
            assert 1 <= len(injected) <= 2, document.doc_id
        else:
            assert len(injected) >= 3, document.doc_id


def test_standalone_period_bills_are_never_on_the_holdout_or_clean():
    for document in ELEC_SET:
        if {"cross_month_period", "cross_year_period"} & set(document.quirks):
            assert not document.is_holdout_layout
            assert document.difficulty_tier != "clean"


# --- windows and dates -----------------------------------------------------------------


def test_nothing_is_dated_after_the_generation_window():
    last_year, last_month = LAST_PERIOD_MONTH
    for document in ALL:
        assert (document.period_end.year, document.period_end.month) <= (last_year, last_month)
        assert document.document_date <= date(2026, 10, 19)


def test_lpg_and_fuel_periods_sit_inside_the_desnz_years():
    """Every LPG and fuel period is in 2025-2026, bar lpg_001, the deliberate refusal."""
    for document in (*LPG_SET, *FUEL_SET):
        if document.doc_id == "lpg_001":
            assert document.period_start.year == 2024
            continue
        assert document.period_start >= date(2025, 1, 1), document.doc_id
        assert document.period_start.year == document.period_end.year


def test_no_account_has_two_documents_covering_the_same_day():
    by_account: dict[tuple[str, str], list] = {}
    for document in ALL:
        by_account.setdefault((document.doc_kind, document.account_number), []).append(document)
    for documents in by_account.values():
        ordered = sorted(documents, key=lambda d: d.period_start)
        for earlier, later in zip(ordered, ordered[1:], strict=False):
            assert earlier.period_end < later.period_start, (earlier.doc_id, later.doc_id)


def test_cross_year_bills_span_four_crossings_and_every_account():
    crossings = Counter(
        tuple(sorted(b.split_kwh_by_year)) for b in ELEC_SET if "cross_year_period" in b.quirks
    )
    assert crossings == {(2022, 2023): 4, (2023, 2024): 4, (2024, 2025): 4, (2025, 2026): 3}
    accounts = {account for account, _, _ in CROSS_YEAR}
    assert accounts == {p.key for p in CUSTOMER_PROFILES}
    assert len(CROSS_MONTH) == 15


def _resolves(category: str, year: int) -> bool:
    held = [y for y in FACTOR_YEARS[category] if y <= year]
    return bool(held) and year - max(held) <= MAX_FACTOR_YEAR_GAP


def test_every_year_in_every_split_resolves_a_factor():
    for bill in ELEC_SET:
        for year in bill.split_kwh_by_year:
            assert _resolves("electricity", year), (bill.doc_id, year)
    for document in (*LPG_SET, *FUEL_SET):
        if document.doc_id != "lpg_001":
            assert document.factor_year in FACTOR_YEARS["lpg"]


def test_the_planners_factor_gap_matches_the_lookup():
    from carbon_r.tools import factors

    assert MAX_FACTOR_YEAR_GAP == factors.MAX_FACTOR_YEAR_GAP


@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs the factor database")
def test_the_planners_factor_years_match_the_database():  # pragma: no cover - needs a DB
    from carbon_r.db import connect

    category_for = {"electricity": "electricity_grid_sg", "lpg": "fuel_lpg"}
    with connect() as conn:
        for short, category in category_for.items():
            rows = conn.execute(
                "select distinct year from emission_factors where category = %s", (category,)
            ).fetchall()
            assert sorted(r[0] for r in rows) == list(FACTOR_YEARS[short])


# --- realism fixes from the Block 1 spot check ------------------------------------------


def test_every_lpg_customer_burns_gas_on_the_ground_floor():
    for customer in LPG_CUSTOMERS:
        assert customer_by_key(customer.key).ground_floor_only, customer.key
    assert "serangoon_dental" not in {c.key for c in LPG_CUSTOMERS}


def test_every_bill_is_on_its_period_years_tariff_plan():
    for bill in ELEC_SET:
        assert bill.tariff_plan_code.endswith(f"-{bill.period_start.year % 100:02d}"), bill.doc_id
        assert bill.tariff_plan_name.endswith(f" {bill.period_start.year % 100:02d}")


def test_no_lpg_account_has_one_kg_figure_on_every_invoice():
    by_account: dict[str, set] = {}
    for invoice in LPG_SET:
        by_account.setdefault(invoice.account_number, set()).add(invoice.lpg_kg)
    for account, values in by_account.items():
        assert len(values) > 1, account


def test_the_lpg_set_has_top_ups_mixed_sizes_and_per_delivery_invoices():
    assert any(i.deliveries == 2 for i in LPG_SET)
    assert any(len({line.size_kg for line in i.cylinders}) == 2 for i in LPG_SET)
    assert any(i.billing_basis == "per_delivery" for i in LPG_SET)
    assert any(i.lpg_kg % 1 for i in LPG_SET)  # the 12.7 kg trap survives


# --- each injected quirk (spec 8.8) ------------------------------------------------------


def _first(documents, quirk):
    return next(d for d in documents if quirk in d.quirks)


@pytest.mark.parametrize("quirk", ["credit_adjustment", "balance_forward", "gst_confusion"])
@pytest.mark.parametrize("kind_set", ["elec", "lpg", "fuel"])
def test_a_shared_quirk_and_its_fields_must_agree(quirk, kind_set):
    documents = {"elec": ELEC_SET, "lpg": LPG_SET, "fuel": FUEL_SET}[kind_set]
    document = _first(documents, quirk)
    without = [q for q in document.quirks if q != quirk]
    with pytest.raises(ValidationError):
        type(document).model_validate({**document.model_dump(), "quirks": without})


def test_a_credit_never_changes_the_activity():
    for document in ALL:
        if "credit_adjustment" not in document.quirks:
            continue
        assert document.credit_reference
        if document.doc_kind == "electricity_bill":
            assert sum(document.split_kwh_by_year.values()) == document.consumption_kwh
            assert document.consumption_kwh == (
                document.current_reading_kwh - document.previous_reading_kwh
            )


def test_about_half_the_credits_print_a_decoy_quantity():
    credits = [d for d in ALL if "credit_adjustment" in d.quirks]
    with_quantity = [d for d in credits if d.credit_quantity is not None]
    assert 0 < len(with_quantity) < len(credits)
    units = {d.credit_quantity_unit for d in with_quantity}
    assert units == {"kWh", "kg", "litres"}


def test_amount_due_differs_from_this_periods_total_under_balance_forward():
    for document in ALL:
        if "balance_forward" in document.quirks:
            due = document.balance_forward_sgd - document.payment_received_sgd + document.total_sgd
            assert document.amount_due_sgd == due
    assert any(d.amount_due_sgd != d.total_sgd for d in ALL if "balance_forward" in d.quirks)


def test_the_injectors_cover_every_non_period_injected_quirk():
    assert set(INJECTORS) | {"cross_month_period", "cross_year_period"} == set(INJECTED_QUIRKS)


def test_mixed_fuel_tracks_the_products_on_every_statement():
    for statement in FUEL_SET:
        assert ("mixed_fuel" in statement.quirks) == (len(statement.litres_by_product) > 1)
    mixed = [s for s in FUEL_SET if "mixed_fuel" in s.quirks and not s.is_holdout_layout]
    assert len(mixed) >= MIN_PER_QUIRK


def test_lpg_cylinders_tracks_the_layout():
    for invoice in LPG_SET:
        assert invoice.prints_total_kg == LAYOUTS[invoice.layout_id].prints_total_kg
        assert ("lpg_cylinders" in invoice.quirks) == (not invoice.prints_total_kg)


# --- determinism -----------------------------------------------------------------------


def test_the_same_seed_gives_the_same_v1_keys():
    again = (*electricity.build_v1(), *lpg.build_v1(), *fuel_card.build_v1())
    assert [d.model_dump_json() for d in again] == [d.model_dump_json() for d in ALL]


def test_a_different_seed_gives_different_v1_keys():
    other = electricity.build_v1(SEED + 1)
    assert [b.consumption_kwh for b in other] != [b.consumption_kwh for b in ELEC_SET]


# --- rendering (spec 8.9, at the HTML level) ---------------------------------------------


def _printed(document) -> list[str]:
    """Values every layout must print, as the context formats them."""
    values = [
        document.customer_name,
        document.account_number,
        f"{document.total_sgd:,.2f}",
        f"{document.subtotal_sgd:,.2f}",
        f"{document.gst_amount_sgd:,.2f}",
    ]
    if document.credit_amount_sgd:
        values.append(f"{document.credit_amount_sgd:,.2f}")
    if document.amount_due_sgd is not None:
        values.append(f"{document.amount_due_sgd:,.2f}")
    return values


def test_every_document_prints_its_key_values_on_every_layout():
    for document in ALL:
        html = render_html(document)
        for value in _printed(document):
            assert value.replace("&", "&amp;") in html, (document.doc_id, value)
        assert "SYNTHETIC DOCUMENT" in html


def test_mwh_bills_print_megawatt_hours_and_never_the_kwh_figure():
    bill = _first(ELEC_SET, "unit_mwh")
    html = render_html(bill)
    assert f"{Decimal(bill.consumption_kwh) / Decimal(1000):,.3f}" in html
    assert f"{bill.consumption_kwh:,} kWh" not in html
    assert "MWh" in html


def test_a_kg_total_is_printed_only_where_the_layout_prints_it():
    for invoice in LPG_SET:
        html = render_html(invoice)
        printed = f"{invoice.lpg_kg.normalize():f}".rstrip(".") + " kg"
        if invoice.prints_total_kg:
            assert printed in html, invoice.doc_id


def test_each_layout_uses_its_own_date_format():
    for document in ALL:
        expected = document.document_date.strftime(LAYOUTS[document.layout_id].date_format)
        assert expected in render_html(document), document.doc_id


# --- degradation -----------------------------------------------------------------------


def test_degradation_parameters_are_recorded_and_deterministic():
    for document in ALL:
        assert document.degradation == params_for(SEED, document.doc_id, document.difficulty_tier)
    renderings = Counter(d.degradation.get("rendering", "clean") for d in ALL)
    assert set(renderings) == {"clean", "pdf", "scan", "photo"}


def test_different_labels_draw_different_parameters():
    a = params_for(SEED, "elec_020", "hard")
    b = params_for(SEED, "elec_023", "hard")
    assert a != b


def test_the_same_parameters_give_the_same_pixels():
    from PIL import Image, ImageDraw

    page = Image.new("RGB", (200, 260), "white")
    ImageDraw.Draw(page).text((20, 40), "Total payable 1,234.56", fill="black")
    for tier in ("moderate", "hard"):
        params = next(
            p
            for p in (params_for(SEED, f"elec_{n:03d}", tier) for n in range(19, 90))
            if p.get("rendering") in ("scan", "photo")
        )
        first = jpeg_roundtrip(apply(page, params), params["jpeg_quality"])
        second = jpeg_roundtrip(apply(page, params), params["jpeg_quality"])
        assert first.tobytes() == second.tobytes()
        assert first.tobytes() != page.tobytes()


def test_keys_serialise_with_their_quirk_fields():
    document = _first(ELEC_SET, "balance_forward")
    data = json.loads(document.model_dump_json())
    assert data["schema_version"] == "2"
    assert data["amount_due_sgd"] is not None
