"""Consumption model and money arithmetic for synthetic LPG invoices.

Plain Python throughout (CLAUDE.md rule 1). It imports no model provider and touches no
database. The direction of information is the same as every other generator:

    synthgen : answer   -> document
    extraction: document -> answer
    eval      : do the two answers match?

What is specific to this kind is the step between gas wanted and gas delivered:

    kg wanted  ->  whole cylinders  ->  kg actually delivered

Gas arrives in sealed cylinders, so a kitchen that needs 190 kg in a month takes four
50 kg cylinders and receives 200. The key records the 200, because that is what was
bought and what the emission factor applies to. Rounding UP rather than to nearest is
what a supplier does: a business does not run out on purpose.

The consequence worth knowing: `lpg_kg` is always a multiple of the cylinder size, so the
answer is quantised. With the 12.7 kg cylinder it is quantised to a fractional figure,
which is why one customer in `profiles.py` uses that size - an extraction that rounded the
cylinder arithmetic to whole kg would pass on every 50 kg document and fail on that one.
"""

import math
import random
from datetime import date, timedelta
from decimal import Decimal

from carbon_r.config import get_settings
from carbon_r.models.lpg_invoice import CylinderLine, LPGInvoice
from carbon_r.money import round2
from carbon_r.synthgen.electricity import (
    AVG_DAYS_PER_MONTH,
    days_in_month,
    seasonal_multiplier,
)
from carbon_r.synthgen.profiles import (
    LPG_CUSTOMERS,
    LPG_VENDOR_NAME,
    LPG_VENDOR_UEN,
    LPGCustomer,
    customer_by_key,
    incidental_details,
    lpg_price_list,
)
from carbon_r.synthgen.rng import bill_rng, doc_rng, draw

DOC_KIND = "lpg_invoice"
DOC_ID_PREFIX = "lpg"
LAYOUT_ID = "lpg_a"
# One layout in this block, so none can be held out: a holdout only means something once
# there are layouts to tune on. Tier 2 Block 2 adds 3-4 layouts and marks exactly one.
IS_HOLDOUT_LAYOUT = False
DIFFICULTY_TIER = "clean"

DEFAULT_SEED = 20261016  # the same master seed as the electricity set
# 2 since Tier 2 Block 2: price list by year, dated visits, top-ups, mixed sizes,
# per-delivery billing, quirks.
GENERATOR_VERSION = "synthgen-lpg-2"

NOISE = 0.10  # +/-10% month-to-month wobble, as for electricity
# A delivery invoice follows the supply period by a few days, drawn per document so the
# offset cannot be learned as a constant.
INVOICE_LAG_DAYS_RANGE = (1, 6)
MONTHS_PER_ACCOUNT = 3
# Empties collected at the delivery visit. Usually the cylinders left last time, but not
# always - a site keeps a spare, or hands back two at once.
RETURNED_OFFSET_RANGE = (-1, 1)


def monthly_lpg_kg(
    base_kg: int,
    year: int,
    month: int,
    amplitude: float,
    rng: random.Random | None = None,
    *,
    noise: float | None = None,
    seasonal: float | None = None,
) -> Decimal:
    """Gas a business wants in one month, before it is rounded to whole cylinders.

    `noise` and `seasonal` are injectable so tests assert against arithmetic rather than
    against whatever the RNG happened to draw (spec 1.5).

    Returned as a Decimal because it feeds a cylinder count and then money; the float
    arithmetic is confined to this expression and never reaches a printed figure.
    """
    if noise is None and rng is None:
        raise ValueError("monthly_lpg_kg needs either rng or noise")
    noise = draw(noise, rng, 1 - NOISE, 1 + NOISE)
    if seasonal is None:
        seasonal = seasonal_multiplier(month, amplitude)
    days = days_in_month(year, month)
    wanted = base_kg * seasonal * noise * days / AVG_DAYS_PER_MONTH
    return Decimal(str(round(wanted, 2)))


def cylinders_for(kg_wanted: Decimal, size_kg: Decimal) -> int:
    """How many whole cylinders cover the gas wanted. At least one.

    Rounds UP: a supplier delivers enough, and a part cylinder is not a thing that can be
    delivered. `math.ceil` on the Decimal quotient rather than on a float division, so a
    quantity that lands exactly on a cylinder boundary does not pick up a spurious extra
    cylinder from binary rounding.
    """
    if size_kg <= 0:
        raise ValueError(f"cylinder size must be positive, got {size_kg}")
    return max(1, math.ceil(kg_wanted / size_kg))


def supply_period(
    year: int, month: int, rng: random.Random | None = None, *, lag_days: int | None = None
) -> tuple[date, date, date]:
    """(period_start, period_end, document_date) for one whole calendar month.

    `monthrange` supplies the real day count, so February gets 28 or 29 and no date names
    a day its month does not have.
    """
    period_start = date(year, month, 1)
    period_end = date(year, month, days_in_month(year, month))
    lag = lag_days if lag_days is not None else rng.randint(*INVOICE_LAG_DAYS_RANGE)
    return period_start, period_end, period_end + timedelta(days=lag)


def _visits(
    count: int, period_start: date, period_end: date, chance: float, rng: random.Random
) -> list[tuple[date, int]]:
    """The visits a month's cylinders arrived in: usually one, sometimes a top-up.

    Drawn from the document's own "deliveries" stream. A top-up needs at least two
    cylinders to split, and lands in the second half of the month.
    """
    last = period_end.day
    first_day = rng.randint(2, min(12, last))
    if count >= 2 and rng.random() < chance:
        first_share = max(1, min(count - 1, round(count * rng.uniform(0.5, 0.7))))
        second_day = rng.randint(16, last - 1)
        return [
            (period_start.replace(day=first_day), first_share),
            (period_start.replace(day=second_day), count - first_share),
        ]
    return [(period_start.replace(day=first_day), count)]


def build_invoice(
    customer: LPGCustomer,
    year: int,
    month: int,
    doc_id: str,
    seed: int,
    *,
    customer_address: str,
    previous_cylinders: int,
    noise: float | None = None,
    lag_days: int | None = None,
    returned_offset: int | None = None,
    period: tuple[date, date] | None = None,
    layout_id: str = LAYOUT_ID,
    is_holdout_layout: bool = IS_HOLDOUT_LAYOUT,
    difficulty_tier: str = DIFFICULTY_TIER,
    prints_total_kg: bool = False,
) -> LPGInvoice:
    """Assemble and validate one clean invoice. Quirks are applied afterwards (quirks.py).

    Monthly (`period` None): the month's gas, rounded up to whole cylinders, delivered in
    one visit or - at the customer's `extra_delivery_chance` - two; plus a second cylinder
    size where the customer takes one. Per delivery (`period` given): the gas used since
    the previous visit, delivered on the period's last day.

    The price list is the one for the period's year. Returns a validated LPGInvoice, so an
    arithmetic mistake fails here - before anything is written to disk.
    """
    profile = customer_by_key(customer.key)
    if period is None:
        rng = bill_rng(seed, customer.account_number, year, month, DOC_KIND)
        kg_wanted = monthly_lpg_kg(
            customer.base_kg, year, month, customer.seasonal_amplitude, rng=rng, noise=noise
        )
        period_start, period_end, document_date = supply_period(year, month, rng, lag_days=lag_days)
    else:
        period_start, period_end = period
        if period_start.year != period_end.year:
            raise ValueError(
                f"{doc_id}: an LPG period may not cross a year boundary ({period_start} to "
                f"{period_end}); LPG is not pro-rated"
            )
        rng = doc_rng(seed, customer.account_number, period_end.isoformat(), DOC_KIND)
        days = (period_end - period_start).days + 1
        noise_value = draw(noise, rng, 1 - NOISE, 1 + NOISE)
        wanted = customer.base_kg * noise_value * days / AVG_DAYS_PER_MONTH
        kg_wanted = Decimal(str(round(wanted, 2)))
        lag = lag_days if lag_days is not None else rng.randint(*INVOICE_LAG_DAYS_RANGE)
        document_date = period_end + timedelta(days=lag)

    prices = lpg_price_list(period_start.year)
    deliveries = doc_rng(seed, doc_id, "deliveries")
    count = cylinders_for(kg_wanted, customer.cylinder_size_kg)
    size = customer.cylinder_size_kg
    if period is None:
        visits = _visits(
            count, period_start, period_end, customer.extra_delivery_chance, deliveries
        )
    else:
        visits = [(period_end, count)]

    lines = [
        CylinderLine(
            delivery_date=day,
            count=n,
            size_kg=size,
            price_per_cylinder_sgd=prices.price_per_cylinder_sgd[size],
        )
        for day, n in visits
    ]
    if customer.second_size_kg is not None:
        second_wanted = Decimal(
            str(round(customer.second_base_kg * deliveries.uniform(1 - NOISE, 1 + NOISE), 2))
        )
        lines.append(
            CylinderLine(
                delivery_date=visits[0][0],
                count=cylinders_for(second_wanted, customer.second_size_kg),
                size_kg=customer.second_size_kg,
                price_per_cylinder_sgd=prices.price_per_cylinder_sgd[customer.second_size_kg],
            )
        )

    # Money, in the exact order LPGInvoice's validator re-checks it.
    gst_rate = Decimal(str(get_settings().gst_rate))  # str(): never Decimal(float)
    cylinder_charge = sum((line.amount_sgd for line in lines), Decimal(0))
    cylinder_count = sum(line.count for line in lines)
    rental_charge = round2(Decimal(cylinder_count) * prices.rental_per_cylinder_sgd)
    visit_count = len({line.delivery_date for line in lines})
    delivery_charge = round2(Decimal(visit_count) * prices.delivery_charge_sgd)
    subtotal = cylinder_charge + delivery_charge + rental_charge
    gst_amount = round2(subtotal * gst_rate)

    offset = returned_offset if returned_offset is not None else rng.randint(*RETURNED_OFFSET_RANGE)
    returned = max(0, previous_cylinders + offset)

    return LPGInvoice(
        doc_id=doc_id,
        generator_seed=seed,
        generator_version=GENERATOR_VERSION,
        layout_id=layout_id,
        is_holdout_layout=is_holdout_layout,
        difficulty_tier=difficulty_tier,
        degradation={},
        vendor_name=LPG_VENDOR_NAME,
        vendor_uen=LPG_VENDOR_UEN,
        customer_name=profile.name,
        customer_address=customer_address,
        account_number=customer.account_number,
        invoice_number=(
            f"IGS-{period_end:%Y%m%d}-{customer.account_number[-4:]}"
            if period is not None
            else f"IGS-{year}{month:02d}-{customer.account_number[-4:]}"
        ),
        document_date=document_date,
        period_start=period_start,
        period_end=period_end,
        billing_basis="per_delivery" if period is not None else "monthly",
        price_list_code=prices.code,
        cylinders=tuple(lines),
        lpg_kg=sum((line.kg for line in lines), Decimal(0)),
        # A layout that prints no kg total is the `lpg_cylinders` case: the activity has to
        # be computed from the cylinder lines. Structural, so recorded in every tier.
        prints_total_kg=prints_total_kg,
        returned_cylinders=returned,
        cylinder_charge_sgd=cylinder_charge,
        delivery_fee_sgd=prices.delivery_charge_sgd,
        delivery_charge_sgd=delivery_charge,
        rental_per_cylinder_sgd=prices.rental_per_cylinder_sgd,
        rental_charge_sgd=rental_charge,
        subtotal_sgd=subtotal,
        gst_rate=gst_rate,
        gst_amount_sgd=gst_amount,
        total_sgd=subtotal + gst_amount,
        quirks=[] if prints_total_kg else ["lpg_cylinders"],
    )


# A per-delivery customer's visits fall this many days apart (inclusive).
DELIVERY_INTERVAL_DAYS = (9, 16)


def delivery_period(seed: int, doc_id: str, previous_end: date) -> tuple[date, date]:
    """The days a per-delivery invoice covers: from the day after the last visit."""
    interval = doc_rng(seed, doc_id, "interval").randint(*DELIVERY_INTERVAL_DAYS)
    start = previous_end + timedelta(days=1)
    return start, start + timedelta(days=interval - 1)


def build_invoice_series(
    seed: int = DEFAULT_SEED, customers: tuple[LPGCustomer, ...] = LPG_CUSTOMERS
) -> list[LPGInvoice]:
    """The Block 1 LPG invoices: four accounts, three documents each, on layout `a`.

    Document numbers come from each customer's `first_doc_number`, not from iteration
    order, so adding a customer cannot renumber the others (spec 1.4).
    """
    from carbon_r.synthgen.plan import LPG, plan_kind

    plans = [p for p in plan_kind(LPG, seed, series=(), accounts=customers)]
    return build_v1(seed, plans, customers=customers)


def build_v1(seed: int = DEFAULT_SEED, plans=None, *, customers=LPG_CUSTOMERS) -> list[LPGInvoice]:
    """Every synthgen v1 invoice, from the plan, clean and then dressed.

    Each account's documents are built in number order (which is date order), carrying the
    cylinder count forward for the returned-empties line and, for a per-delivery customer,
    the date of the last visit.
    """
    from carbon_r.synthgen.layouts import layout
    from carbon_r.synthgen.plan import LPG, plan_kind
    from carbon_r.synthgen.quirks import dress

    plans = plans if plans is not None else plan_kind(LPG, seed)
    by_key = {customer.key: customer for customer in customers}
    previous_cylinders: dict[str, int] = {}
    last_visit: dict[str, date] = {}
    built = {}

    for plan in sorted(plans, key=lambda p: (p.account, p.number)):
        customer = by_key[plan.account]
        address, _ = incidental_details(customer_by_key(customer.key), seed)
        period = None
        if customer.billing_basis == "per_delivery":
            previous_end = last_visit.get(
                plan.account, date(plan.year, plan.month, 1) - timedelta(days=1)
            )
            period = delivery_period(seed, plan.doc_id, previous_end)
        shape = layout(plan.layout_id)
        invoice = build_invoice(
            customer,
            plan.year,
            plan.month,
            plan.doc_id,
            seed,
            customer_address=address,
            previous_cylinders=previous_cylinders.get(plan.account, 0),
            period=period,
            layout_id=plan.layout_id,
            is_holdout_layout=shape.is_holdout,
            difficulty_tier=plan.tier,
            prints_total_kg=shape.prints_total_kg,
        )
        previous_cylinders[plan.account] = invoice.cylinder_count
        last_visit[plan.account] = invoice.period_end
        built[plan.number] = dress(invoice, plan, seed)

    return [built[n] for n in sorted(built)]
