"""The generator: determinism, structure, and arithmetic checked independently.

No database, no API key, no native libraries: these tests compare HTML rather than PDF
bytes, because WeasyPrint stamps a creation timestamp into PDF metadata and identical
input therefore yields differing bytes.
"""

from decimal import Decimal
from pathlib import Path

import pytest

from carbon_r.config import get_settings
from carbon_r.models.electricity_bill import ElectricityBill
from carbon_r.money import round2
from carbon_r.synthgen.electricity import (
    AVG_DAYS_PER_MONTH,
    BILL_LAG_DAYS_RANGE,
    DEFAULT_SEED,
    DOC_KIND,
    MONTHS_IN_SERVICE_RANGE,
    MONTHS_PER_ACCOUNT,
    NOISE,
    PEAK_MONTH,
    build_bill,
    build_bill_series,
    days_in_month,
    monthly_consumption_kwh,
    opening_reading_kwh,
    seasonal_multiplier,
)
from carbon_r.synthgen.profiles import (
    CUSTOMER_PROFILES,
    POSTAL_SECTORS,
    TARIFF_PLANS,
    CustomerProfile,
    incidental_details,
)
from carbon_r.synthgen.render import render_html

BILLS = build_bill_series()


def _expected_kwh(base_kwh: int, month: int, amplitude: float, year: int = 2024) -> int:
    """The consumption a given amplitude implies, at noise 1.0. Module scope so it does not
    close over loop variables (ruff B023)."""
    day_factor = days_in_month(year, month) / AVG_DAYS_PER_MONTH
    return round(base_kwh * seasonal_multiplier(month, amplitude) * day_factor)


def profile(key: str):
    return next(p for p in CUSTOMER_PROFILES if p.key == key)


# --- the generated set ------------------------------------------------------------------


def test_every_bill_validates_against_the_model():
    assert len(BILLS) == 18
    for bill in BILLS:
        assert isinstance(bill, ElectricityBill)
        ElectricityBill.model_validate_json(bill.model_dump_json())


def test_six_accounts_of_three_consecutive_non_overlapping_months():
    by_account: dict[str, list[ElectricityBill]] = {}
    for bill in BILLS:
        by_account.setdefault(bill.account_number, []).append(bill)
    assert len(by_account) == 6

    for run in by_account.values():
        assert len(run) == MONTHS_PER_ACCOUNT
        for earlier, later in zip(run, run[1:], strict=False):
            assert earlier.period_end < later.period_start
            assert later.previous_reading_kwh == earlier.current_reading_kwh


def test_nine_bills_in_each_factor_year():
    years = [bill.factor_year for bill in BILLS]
    assert years.count(2024) == 9
    assert years.count(2025) == 9


def test_quirks_are_empty_in_v0():
    assert all(bill.quirks == [] for bill in BILLS)


def test_gst_rate_comes_from_settings():
    expected = Decimal(str(get_settings().gst_rate))
    assert all(bill.gst_rate == expected for bill in BILLS)


def test_the_set_spans_two_orders_of_magnitude():
    consumption = [bill.consumption_kwh for bill in BILLS]
    assert min(consumption) < 3_000
    assert max(consumption) > 100_000


# --- arithmetic, recomputed without the validator ---------------------------------------


def test_arithmetic_closes_on_every_bill_recomputed_independently():
    """A validator cannot be its own witness: recompute from the key's raw inputs."""
    for bill in BILLS:
        energy = round2(Decimal(bill.consumption_kwh) * bill.tariff_cents_per_kwh / Decimal(100))
        subtotal = energy + bill.fixed_charge_sgd
        gst = round2(subtotal * bill.gst_rate)
        assert energy == bill.energy_charge_sgd
        assert subtotal == bill.subtotal_sgd
        assert gst == bill.gst_amount_sgd
        assert subtotal + gst == bill.total_sgd


def test_worked_example_a_low_tension():
    """Lion City Laundry, May 2024, injected noise 0.973. Literals from the scratch script."""
    bill = build_bill(
        profile("lion_city_laundry"),
        2024,
        5,
        412_823,
        "elec_001",
        DEFAULT_SEED,
        customer_address="x",
        meter_number="SP00000000",
        noise=0.973,
    )
    assert bill.consumption_kwh == 9_859
    assert bill.energy_charge_sgd == Decimal("2943.90")
    assert bill.fixed_charge_sgd == Decimal("12.00")
    assert bill.subtotal_sgd == Decimal("2955.90")
    assert bill.gst_amount_sgd == Decimal("266.03")
    assert bill.total_sgd == Decimal("3221.93")


def test_worked_example_b_high_tension():
    """Kranji Cold Store, Dec 2024, injected noise 1.000, on the HT plan."""
    bill = build_bill(
        profile("kranji_cold_store"),
        2024,
        12,
        10_000_000,
        "elec_009",
        DEFAULT_SEED,
        customer_address="x",
        meter_number="SP00000000",
        noise=1.000,
    )
    assert bill.consumption_kwh == 154_940
    assert bill.energy_charge_sgd == Decimal("42840.91")
    assert bill.fixed_charge_sgd == Decimal("32.00")
    assert bill.subtotal_sgd == Decimal("42872.91")
    assert bill.gst_amount_sgd == Decimal("3858.56")
    assert bill.total_sgd == Decimal("46731.47")


# --- the consumption model ---------------------------------------------------------------


def test_day_scaling_a_shorter_month_bills_less():
    """Inject noise AND seasonal so the day count is the only thing left that can differ."""
    february = monthly_consumption_kwh(9_200, 2024, 2, 0.08, noise=1.0, seasonal=1.0)
    march = monthly_consumption_kwh(9_200, 2024, 3, 0.08, noise=1.0, seasonal=1.0)
    assert days_in_month(2024, 2) == 29
    assert days_in_month(2024, 3) == 31
    assert february == 8_776
    assert march == 9_382
    assert february < march


def test_seasonal_amplitude_reaches_the_curve():
    """Direct test of the amplitude, rather than inferring it from noisy generated bills."""
    assert seasonal_multiplier(PEAK_MONTH, 0.18) == 1.18
    assert seasonal_multiplier(PEAK_MONTH, 0.05) == 1.05
    # Feb and Aug are the cosine's zero crossings at every amplitude.
    assert seasonal_multiplier(2, 0.18) == 1.0
    assert seasonal_multiplier(8, 0.05) == 1.0


def test_build_bill_passes_each_profiles_own_amplitude_through():
    """build_bill must use the profile's OWN amplitude, not a global one.

    Asserted as exact integer equality rather than as a ratio: consumption is rounded to
    whole kWh, and at base_kwh = 2,400 that rounding is ~2e-4 of the value, which no
    sensible ratio tolerance can distinguish from a real error.

    Each case also checks that a *different* amplitude would have produced a different
    number, so the test genuinely discriminates instead of passing by coincidence.
    """
    for key, month, wrong_amplitude in (
        ("kranji_cold_store", 12, 0.05),
        ("serangoon_dental", 5, 0.18),
    ):
        prof = profile(key)
        bill = build_bill(
            prof,
            2024,
            month,
            1_000_000,
            "elec_001",
            DEFAULT_SEED,
            customer_address="x",
            meter_number="SP00000000",
            noise=1.0,
        )
        assert bill.consumption_kwh == _expected_kwh(
            prof.base_kwh, month, prof.seasonal_amplitude
        ), key
        assert bill.consumption_kwh != _expected_kwh(prof.base_kwh, month, wrong_amplitude), key


def test_noise_stays_within_its_declared_bound():
    for prof in CUSTOMER_PROFILES:
        for bill in (b for b in BILLS if b.account_number == prof.account_number):
            month = bill.period_start.month
            expected = (
                prof.base_kwh
                * seasonal_multiplier(month, prof.seasonal_amplitude)
                * days_in_month(bill.period_start.year, month)
                / AVG_DAYS_PER_MONTH
            )
            assert abs(bill.consumption_kwh / expected - 1) <= NOISE + 1e-6


# --- meter readings ----------------------------------------------------------------------


def test_opening_read_scales_with_the_business():
    """The invariant the old flat 100k-900k range violated for a 180,000 kWh/month meter."""
    low, high = MONTHS_IN_SERVICE_RANGE
    for prof in CUSTOMER_PROFILES:
        months = opening_reading_kwh(prof, DEFAULT_SEED) / prof.base_kwh
        assert low <= months <= high, (prof.key, months)


def test_opening_reads_are_not_round_multiples_of_base_kwh():
    """A continuous month draw, not an integer one: no meter starts on a suspiciously round
    number such as 9200 x 45 = 414,000."""
    exact = [
        prof.key
        for prof in CUSTOMER_PROFILES
        if opening_reading_kwh(prof, DEFAULT_SEED) % prof.base_kwh == 0
    ]
    assert exact == []


# --- determinism -------------------------------------------------------------------------


def _fingerprint(bills) -> str:
    return "\n".join(b.model_dump_json() for b in bills)


def test_the_same_seed_reproduces_the_same_answer_keys():
    assert _fingerprint(build_bill_series(DEFAULT_SEED)) == _fingerprint(
        build_bill_series(DEFAULT_SEED)
    )


def test_the_same_seed_reproduces_the_same_html():
    first = [render_html(b) for b in build_bill_series(DEFAULT_SEED)]
    second = [render_html(b) for b in build_bill_series(DEFAULT_SEED)]
    assert first == second


def test_a_different_seed_produces_different_output():
    """Guards against a seed that is accepted and then silently ignored."""
    assert _fingerprint(build_bill_series(DEFAULT_SEED)) != _fingerprint(
        build_bill_series(DEFAULT_SEED + 1)
    )


def test_identities_are_stable_for_a_seed():
    for prof in CUSTOMER_PROFILES:
        assert incidental_details(prof, DEFAULT_SEED) == incidental_details(prof, DEFAULT_SEED)


# --- plan consistency --------------------------------------------------------------------


def test_the_rate_belongs_to_the_plan_not_the_customer():
    """Every bill on a plan code carries that plan's rate and fixed charge."""
    for bill in BILLS:
        plan = TARIFF_PLANS[bill.tariff_plan_code]
        assert bill.tariff_cents_per_kwh == plan.cents_per_kwh
        assert bill.fixed_charge_sgd == plan.fixed_charge_sgd
        assert bill.tension == plan.tension
        assert bill.tariff_plan_name == plan.name

    shared = [b for b in BILLS if b.tariff_plan_code == "SPR-LT-FIX-25"]
    assert len({b.account_number for b in shared}) > 1  # genuinely shared between customers
    assert len({b.tariff_cents_per_kwh for b in shared}) == 1


# --- the doc_009 date trap ----------------------------------------------------------------


def test_elec_009_bills_a_december_period_with_a_january_document_date():
    """The one document where the billing period and bill_date disagree on the year.

    Both readings return the 2024 factor, which is exactly why it is a trap: the number
    looks right while a bill_date-driven citation would wrongly announce a substitution.
    """
    bill = next(b for b in BILLS if b.doc_id == "elec_009")
    assert bill.customer_name == "Kranji Cold Store Pte Ltd"
    assert bill.period_start.isoformat() == "2024-12-01"
    assert bill.period_end.isoformat() == "2024-12-31"
    assert bill.document_date.year == 2025
    assert bill.factor_year == 2024


# --- the rendered page --------------------------------------------------------------------


def test_every_page_declares_itself_synthetic_and_prints_its_own_figures():
    for bill in BILLS:
        html = render_html(bill)
        assert "SYNTHETIC DOCUMENT" in html
        assert "Straits Power Retail Pte Ltd" in html
        assert f"{bill.consumption_kwh:,}" in html
        assert f"{bill.total_sgd:,.2f}" in html
        assert f"{bill.previous_reading_kwh:,}" in html


# --- docs/synthgen/SYNTHGEN_SPEC.md Part 8: tests every document kind must have ---------


def test_spec_common_fields_present_on_every_document():
    """Spec 3.3/1.8: the fields the eval harness relies on across document kinds."""
    for bill in BILLS:
        assert bill.doc_kind == DOC_KIND
        assert bill.layout_id == "elec_a"
        assert bill.difficulty_tier == "clean"
        assert bill.degradation == {}
        assert bill.quirks == []


def test_adding_a_profile_does_not_change_any_existing_document():
    """Spec 1.4 / Part 8.5. The reason RNG labels and doc numbers are owned by the profile
    rather than taken from loop position: a recorded accuracy figure must keep referring to
    the same documents after the set grows."""
    newcomer = CustomerProfile(
        key="newcomer",
        name="Paya Lebar Bakery Pte Ltd",
        account_number="8820-0001-09",
        plan_code="SPR-LT-FIX-25",
        base_kwh=4_400,
        seasonal_amplitude=0.09,
        start_year=2025,
        start_month=3,
        first_doc_number=19,
        street="Jalan Roti Manis",
        postal_sector="53",
        ground_floor_only=True,
    )
    before = build_bill_series(DEFAULT_SEED)
    # Prepended, not appended: position must not matter at all.
    after = build_bill_series(DEFAULT_SEED, (newcomer, *CUSTOMER_PROFILES))

    unchanged = [b for b in after if b.doc_id in {x.doc_id for x in before}]
    assert len(after) == len(before) + 3
    assert [b.model_dump_json() for b in unchanged] == [b.model_dump_json() for b in before]


def test_date_sanity_and_a_document_whose_period_year_differs_from_its_date_year():
    """Spec Part 8.11."""
    for bill in BILLS:
        assert bill.period_start <= bill.period_end <= bill.document_date
        assert (bill.document_date - bill.period_end).days in range(
            BILL_LAG_DAYS_RANGE[0], BILL_LAG_DAYS_RANGE[1] + 1
        )
    straddling = [b for b in BILLS if b.document_date.year != b.period_start.year]
    assert straddling, "the set must contain a document where period year != document year"


def test_document_date_lag_is_not_a_constant():
    """Backlog G3: a fixed 4-day lag is learnable; the draw must actually vary."""
    lags = {(b.document_date - b.period_end).days for b in BILLS}
    assert len(lags) > 1


def test_exactly_one_layout_is_held_out_once_there_is_more_than_one():
    """Spec 6.1 / Part 8.12.

    v0 ships a single layout, so there is nothing to hold out - a holdout is only
    meaningful once there are layouts to tune against. This test records that state and
    will start enforcing the rule the moment Tier 2 adds a second layout.
    """
    layouts = {b.layout_id for b in BILLS}
    holdouts = {b.layout_id for b in BILLS if b.is_holdout_layout}
    if len(layouts) == 1:
        assert holdouts == set(), "a single-layout set cannot hold one out"
    else:
        assert len(holdouts) == 1


def test_profiles_are_internally_coherent():
    """Lesson 13 / backlog G1: a car workshop does not sit on the seventh floor, and a
    Kranji street does not carry a Toa Payoh postal code."""
    for prof in CUSTOMER_PROFILES:
        assert prof.postal_sector in POSTAL_SECTORS, prof.key
        address, meter = incidental_details(prof, DEFAULT_SEED)
        postal = address.rsplit(" ", 1)[-1]
        assert len(postal) == 6 and postal.startswith(prof.postal_sector), address
        if prof.ground_floor_only:
            assert "#01-" in address, address
        assert meter.startswith("SP")


def test_identifiers_cannot_be_mistaken_for_real_ones():
    """Spec 1.7. The vendor UEN must be self-evidently synthetic: the first draft used
    202411234K, which is shaped exactly like a real ACRA local-company UEN."""
    from carbon_r.synthgen.profiles import VENDOR_SUPPORT, VENDOR_UEN

    assert VENDOR_UEN.startswith("SYN-")
    assert not VENDOR_UEN[:4].isdigit()
    assert VENDOR_SUPPORT.endswith(".example")  # reserved TLD, never routable


def test_printed_values_round_trip_through_the_pdf_text_layer():
    """Spec Part 8.9: once per layout, check the actual PDF - not just the HTML.

    render_html guarantees the page can only print values from the key, but that guarantee
    is worth testing end to end at least once: a CSS or font problem could drop a column
    from the rendered output while the HTML still contained it.

    Skipped when WeasyPrint's native libraries are unavailable, so a bare `pytest` still
    passes on a machine without pango. `make check` exports DYLD_FALLBACK_LIBRARY_PATH and
    therefore runs it.
    """
    import shutil
    import subprocess
    import tempfile

    # One import, both failures: `importorskip` catches ImportError only, and a missing pango
    # raises OSError from inside WeasyPrint's import - which failed this test, not skipped it.
    try:
        from weasyprint import HTML  # noqa: F401
    except (ImportError, OSError) as exc:
        pytest.skip(
            "WeasyPrint cannot load its native libraries (pango); on macOS export "
            f"DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib, as `make check` does ({exc})"
        )
    if not shutil.which("pdftotext"):
        pytest.skip("pdftotext not installed")

    from carbon_r.synthgen.render import write_pdf

    checked_layouts = set()
    for bill in BILLS:
        if bill.layout_id in checked_layouts:
            continue
        checked_layouts.add(bill.layout_id)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / f"{bill.doc_id}.pdf"
            write_pdf(render_html(bill), path)
            text = subprocess.run(
                ["pdftotext", "-layout", str(path), "-"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout

        for expected in (
            bill.doc_id,
            bill.customer_name,
            bill.account_number,
            bill.meter_number,
            bill.tariff_plan_code,
            f"{bill.previous_reading_kwh:,}",
            f"{bill.current_reading_kwh:,}",
            f"{bill.consumption_kwh:,}",
            f"{bill.energy_charge_sgd:,.2f}",
            f"{bill.fixed_charge_sgd:,.2f}",
            f"{bill.subtotal_sgd:,.2f}",
            f"{bill.gst_amount_sgd:,.2f}",
            f"{bill.total_sgd:,.2f}",
            bill.document_date.strftime("%d %b %Y"),
            bill.period_start.strftime("%d %b %Y"),
            bill.period_end.strftime("%d %b %Y"),
        ):
            assert expected in text, (bill.doc_id, expected)

        assert "SYNTHETIC DOCUMENT" in text
        assert "Not a real document" in text

    assert checked_layouts == {b.layout_id for b in BILLS}
