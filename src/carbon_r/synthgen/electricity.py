"""Consumption model and money arithmetic for synthetic electricity bills.

This is the only module in synthgen that computes a quantity, and it is plain Python
throughout (CLAUDE.md rule 1). It imports no model provider and touches no database.

The direction of information is the whole design:

    synthgen : answer   -> document      (here, plus render.py)
    Block 3  : document -> answer        (extraction)
    eval     : do the two answers match?

This module is the *answer* half. Every choice below protects that comparison: varying
usage so a constant guess cannot score; meter readings so there are decoy kWh figures;
a fixed charge so "total" is not "energy charge"; exact decimals so a calculator held to
the printed page agrees with the key.
"""

import calendar
import math
import random
from datetime import date, timedelta
from decimal import Decimal

from carbon_r.config import get_settings
from carbon_r.models.electricity_bill import ElectricityBill
from carbon_r.money import round2
from carbon_r.prorate import split_by_year
from carbon_r.synthgen.profiles import (
    CUSTOMER_PROFILES,
    VENDOR_NAME,
    VENDOR_UEN,
    CustomerProfile,
    incidental_details,
)
from carbon_r.synthgen.rng import account_rng, bill_rng, draw

DOC_KIND = "electricity_bill"
DOC_ID_PREFIX = "elec"
LAYOUT_ID = "elec_a"
# v0 ships a single layout, so none can be held out: a holdout only means something once
# there are layouts to tune on. Tier 2 adds 3-4 layouts and marks exactly one holdout
# (spec 6.1).
IS_HOLDOUT_LAYOUT = False
DIFFICULTY_TIER = "clean"

DEFAULT_SEED = 20261016
# 3 since Tier 2 Block 2: plan by period year, by-year split, standalone periods, quirks.
GENERATOR_VERSION = "synthgen-electricity-3"

PEAK_MONTH = 5  # May: hottest month in Singapore, peak cooling load. Global - weather is.
NOISE = 0.10  # +/-10% month-to-month wobble, as the checklist asks
AVG_DAYS_PER_MONTH = 30.4  # the reference month length base_kwh is quoted against
# A bill arrives a few days after the period it covers. Drawn per document rather than
# fixed at 4, so document_date is not a constant offset from period_end (backlog G3).
BILL_LAG_DAYS_RANGE = (2, 9)
MONTHS_PER_ACCOUNT = 3
MONTHS_IN_SERVICE_RANGE = (24.0, 150.0)  # meter age when the first bill is issued


def seasonal_multiplier(month: int, amplitude: float) -> float:
    """A multiplier near 1 saying how far above or below a typical month this one runs.

    A cosine because it is the simplest smooth curve that peaks once a year and returns to
    where it started, which is what weather does. `amplitude` scales the cosine's natural
    +/-1 down to each business's own swing. Feb and Aug are the zero crossings, so they
    return exactly 1.0 at any amplitude.
    """
    return 1 + amplitude * math.cos(2 * math.pi * (month - PEAK_MONTH) / 12)


def days_in_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def monthly_consumption_kwh(
    base_kwh: int,
    year: int,
    month: int,
    amplitude: float,
    rng: random.Random | None = None,
    *,
    noise: float | None = None,
    seasonal: float | None = None,
) -> int:
    """Units of electricity used in one month. The most important field on the bill.

    `noise` and `seasonal` are injectable so tests assert against arithmetic rather than
    against whatever the RNG happened to draw. A test proving the day term works injects
    both and varies only the month.
    """
    if noise is None and rng is None:
        raise ValueError("monthly_consumption_kwh needs either rng or noise")
    noise = draw(noise, rng, 1 - NOISE, 1 + NOISE)
    if seasonal is None:
        seasonal = seasonal_multiplier(month, amplitude)
    days = days_in_month(year, month)
    # The only float arithmetic in this module. It becomes an int here and never
    # reaches money: meters count whole units.
    return round(base_kwh * seasonal * noise * days / AVG_DAYS_PER_MONTH)


def period_consumption_kwh(
    base_kwh: int,
    start: date,
    end: date,
    amplitude: float,
    rng: random.Random | None = None,
    *,
    noise: float | None = None,
) -> int:
    """Units used over a period that is not a calendar month (a meter-read cycle).

    The same model as `monthly_consumption_kwh`, with the period's own day count and the
    seasonal multiplier of the month the period mostly falls in (its midpoint).
    """
    if noise is None and rng is None:
        raise ValueError("period_consumption_kwh needs either rng or noise")
    noise = draw(noise, rng, 1 - NOISE, 1 + NOISE)
    days = (end - start).days + 1
    midpoint = start + timedelta(days=days // 2)
    seasonal = seasonal_multiplier(midpoint.month, amplitude)
    return round(base_kwh * seasonal * noise * days / AVG_DAYS_PER_MONTH)


def standalone_opening_reading_kwh(profile: CustomerProfile, seed: int, start: date) -> int:
    """A meter read for a bill that is not part of the account's monthly run.

    Wound back from the account's opening read by its base use per month, so a 2022 bill
    reads lower than the 2024 run on the same meter. Readings are NOT continuous between
    an account's separate windows - recorded as a known limit in docs/blocks/TIER2_BLOCK2.md.
    """
    opening = opening_reading_kwh(profile, seed)
    months_before = (profile.start_year - start.year) * 12 + (profile.start_month - start.month)
    return max(profile.base_kwh * 6, opening - profile.base_kwh * months_before)


def opening_reading_kwh(profile: CustomerProfile, seed: int) -> int:
    """A plausible lifetime meter read, sized to the business.

    uniform(), not randrange(): an integer month count would make every opening read an
    exact multiple of base_kwh, so meters would start on suspiciously round numbers
    (9200 x 45 = 414,000). Sized to the business rather than drawn from a fixed absolute
    range, because a flat 100k-900k would put Kranji's 180,000 kWh/month meter at under
    five months of service.
    """
    rng = account_rng(seed, profile.account_number, DOC_KIND)
    return round(profile.base_kwh * rng.uniform(*MONTHS_IN_SERVICE_RANGE))


def billing_period(
    year: int, month: int, rng: random.Random | None = None, *, lag_days: int | None = None
) -> tuple[date, date, date]:
    """(period_start, period_end, document_date) for one whole calendar month.

    Whole months in v0; a period straddling two months is the Tier 2 `cross_month_period`
    quirk. monthrange supplies the real day count, so February 2024 gets 29 days and no
    date names a day its month does not have.

    document_date falls a few days after period_end, so the two are always different values
    - which lets the eval detect a model that picked the wrong one. The lag is drawn per
    document, not fixed, so it cannot be learned as a constant offset. Note this means a
    December period carries a January document_date; the emission-factor year comes from
    the period. See ElectricityBill's module docstring.
    """
    period_start = date(year, month, 1)
    period_end = date(year, month, days_in_month(year, month))
    lag = lag_days if lag_days is not None else rng.randint(*BILL_LAG_DAYS_RANGE)
    return period_start, period_end, period_end + timedelta(days=lag)


def add_months(year: int, month: int, offset: int) -> tuple[int, int]:
    index = (year * 12) + (month - 1) + offset
    return divmod(index, 12)[0], divmod(index, 12)[1] + 1


def build_bill(
    profile: CustomerProfile,
    year: int,
    month: int,
    previous_reading_kwh: int,
    doc_id: str,
    seed: int,
    *,
    customer_address: str,
    meter_number: str,
    noise: float | None = None,
    lag_days: int | None = None,
    period: tuple[date, date] | None = None,
    layout_id: str = LAYOUT_ID,
    is_holdout_layout: bool = IS_HOLDOUT_LAYOUT,
    difficulty_tier: str = DIFFICULTY_TIER,
) -> ElectricityBill:
    """Assemble and validate one clean bill. Quirks are applied afterwards (quirks.py).

    `period` replaces the calendar month with a meter-read cycle - the standalone
    `cross_month_period` and `cross_year_period` bills. Without it the bill is exactly the
    Block 1 bill: same random draws in the same order, so Block 1 is bit-identical.

    Returns a validated ElectricityBill, so an arithmetic mistake in this module fails
    here - before anything is written to disk and long before a PDF exists.
    """
    rng = bill_rng(seed, profile.account_number, year, month, DOC_KIND)
    if period is None:
        consumption = monthly_consumption_kwh(
            profile.base_kwh,
            year,
            month,
            profile.seasonal_amplitude,
            rng=rng,
            noise=noise,
        )
        period_start, period_end, document_date = billing_period(
            year, month, rng, lag_days=lag_days
        )
    else:
        period_start, period_end = period
        consumption = period_consumption_kwh(
            profile.base_kwh,
            period_start,
            period_end,
            profile.seasonal_amplitude,
            rng=rng,
            noise=noise,
        )
        lag = lag_days if lag_days is not None else rng.randint(*BILL_LAG_DAYS_RANGE)
        document_date = period_end + timedelta(days=lag)

    # The plan for the PERIOD's year: a plan code carries its year, and a page whose plan
    # year disagrees with its period contradicts itself.
    plan = profile.plan_for(period_start.year)

    # Money, in the exact order ElectricityBill's validator re-checks it.
    gst_rate = Decimal(str(get_settings().gst_rate))  # str(): never Decimal(float)
    energy_charge = round2(Decimal(consumption) * plan.cents_per_kwh / Decimal(100))
    subtotal = energy_charge + plan.fixed_charge_sgd
    gst_amount = round2(subtotal * gst_rate)

    quirks = []
    if period_start.year != period_end.year:
        quirks.append("cross_year_period")
    elif period is not None:
        quirks.append("cross_month_period")

    return ElectricityBill(
        doc_id=doc_id,
        generator_seed=seed,
        generator_version=GENERATOR_VERSION,
        layout_id=layout_id,
        is_holdout_layout=is_holdout_layout,
        difficulty_tier=difficulty_tier,
        degradation={},
        vendor_name=VENDOR_NAME,
        vendor_uen=VENDOR_UEN,
        customer_name=profile.name,
        customer_address=customer_address,
        account_number=profile.account_number,
        document_date=document_date,
        period_start=period_start,
        period_end=period_end,
        meter_number=meter_number,
        previous_reading_kwh=previous_reading_kwh,
        current_reading_kwh=previous_reading_kwh + consumption,
        consumption_kwh=consumption,
        is_estimated_reading=False,
        split_kwh_by_year=split_by_year(consumption, period_start, period_end),
        tariff_plan_code=plan.code,
        tariff_plan_name=plan.name,
        tension=plan.tension,
        tariff_cents_per_kwh=plan.cents_per_kwh,
        energy_charge_sgd=energy_charge,
        fixed_charge_sgd=plan.fixed_charge_sgd,
        subtotal_sgd=subtotal,
        gst_rate=gst_rate,
        gst_amount_sgd=gst_amount,
        total_sgd=subtotal + gst_amount,
        quirks=quirks,
    )


def build_bill_series(
    seed: int = DEFAULT_SEED, profiles: tuple[CustomerProfile, ...] = CUSTOMER_PROFILES
) -> list[ElectricityBill]:
    """All 18 bills: six accounts, three consecutive months each.

    Document numbers come from each profile's `first_doc_number`, not from iteration
    order, so adding a seventh company cannot renumber the first six (spec 1.4).
    Meter readings carry forward within an account, which is what makes
    `current - previous == consumption` true by construction rather than by assertion.
    """
    bills: list[ElectricityBill] = []

    for profile in profiles:
        if profile.first_doc_number <= 0:
            continue  # added after Block 1: it has no Block 1 run
        address, meter_number = incidental_details(profile, seed)
        reading = opening_reading_kwh(profile, seed)

        for offset in range(MONTHS_PER_ACCOUNT):
            year, month = add_months(profile.start_year, profile.start_month, offset)
            bill = build_bill(
                profile,
                year,
                month,
                reading,
                f"{DOC_ID_PREFIX}_{profile.first_doc_number + offset:03d}",
                seed,
                customer_address=address,
                meter_number=meter_number,
            )
            reading = bill.current_reading_kwh
            bills.append(bill)

    return bills


def build_v1(seed: int = DEFAULT_SEED, plans=None) -> list[ElectricityBill]:
    """Every synthgen v1 bill, from the plan (synthgen/plan.py), clean and then dressed.

    Each account's monthly documents are built in date order so the meter reading carries
    from one bill to the next, Block 1 straight into its Block 2 series. Standalone bills
    (the two period quirks) take a reading wound back from the account's opening read.
    """
    from carbon_r.synthgen.layouts import layout
    from carbon_r.synthgen.plan import ELEC, plan_kind
    from carbon_r.synthgen.profiles import customer_by_key
    from carbon_r.synthgen.quirks import dress

    plans = plans if plans is not None else plan_kind(ELEC, seed)
    by_number = {}
    readings: dict[str, int] = {}

    def key(plan):
        return (plan.account, plan.group not in ("block1", "series"), plan.year, plan.month)

    for plan in sorted(plans, key=key):
        profile = customer_by_key(plan.account)
        address, meter_number = incidental_details(profile, seed)
        if plan.period is None:
            reading = readings.get(plan.account)
            if reading is None:
                reading = opening_reading_kwh(profile, seed)
        else:
            reading = standalone_opening_reading_kwh(profile, seed, plan.period[0])
        bill = build_bill(
            profile,
            plan.year,
            plan.month,
            reading,
            plan.doc_id,
            seed,
            customer_address=address,
            meter_number=meter_number,
            period=plan.period,
            layout_id=plan.layout_id,
            is_holdout_layout=layout(plan.layout_id).is_holdout,
            difficulty_tier=plan.tier,
        )
        if plan.period is None:
            readings[plan.account] = bill.current_reading_kwh
        by_number[plan.number] = dress(bill, plan, seed)

    return [by_number[n] for n in sorted(by_number)]
