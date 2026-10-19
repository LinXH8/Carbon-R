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
    add_months,
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
)
from carbon_r.synthgen.rng import bill_rng, draw

DOC_KIND = "lpg_invoice"
DOC_ID_PREFIX = "lpg"
LAYOUT_ID = "lpg_a"
# One layout in this block, so none can be held out: a holdout only means something once
# there are layouts to tune on. Tier 2 Block 2 adds 3-4 layouts and marks exactly one.
IS_HOLDOUT_LAYOUT = False
DIFFICULTY_TIER = "clean"

DEFAULT_SEED = 20261016  # the same master seed as the electricity set
GENERATOR_VERSION = "synthgen-lpg-1"

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

    Whole months in this block; per-delivery invoicing and a period straddling two months
    are Block 2 quirks. `monthrange` supplies the real day count, so February gets 28 or 29
    and no date names a day its month does not have.
    """
    period_start = date(year, month, 1)
    period_end = date(year, month, days_in_month(year, month))
    lag = lag_days if lag_days is not None else rng.randint(*INVOICE_LAG_DAYS_RANGE)
    return period_start, period_end, period_end + timedelta(days=lag)


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
) -> LPGInvoice:
    """Assemble and validate one invoice.

    Returns a validated LPGInvoice, so an arithmetic mistake in this module fails here -
    before anything is written to disk and long before a PDF exists.
    """
    profile = customer_by_key(customer.key)
    prices = customer.price_list
    rng = bill_rng(seed, customer.account_number, year, month, DOC_KIND)

    kg_wanted = monthly_lpg_kg(
        customer.base_kg, year, month, customer.seasonal_amplitude, rng=rng, noise=noise
    )
    count = cylinders_for(kg_wanted, customer.cylinder_size_kg)
    period_start, period_end, document_date = supply_period(year, month, rng, lag_days=lag_days)

    price = prices.price_per_cylinder_sgd[customer.cylinder_size_kg]
    line = CylinderLine(
        count=count, size_kg=customer.cylinder_size_kg, price_per_cylinder_sgd=price
    )

    # Money, in the exact order LPGInvoice's validator re-checks it.
    gst_rate = Decimal(str(get_settings().gst_rate))  # str(): never Decimal(float)
    cylinder_charge = line.amount_sgd
    rental_charge = round2(Decimal(count) * prices.rental_per_cylinder_sgd)
    subtotal = cylinder_charge + prices.delivery_charge_sgd + rental_charge
    gst_amount = round2(subtotal * gst_rate)

    offset = returned_offset if returned_offset is not None else rng.randint(*RETURNED_OFFSET_RANGE)
    returned = max(0, previous_cylinders + offset)

    return LPGInvoice(
        doc_id=doc_id,
        generator_seed=seed,
        generator_version=GENERATOR_VERSION,
        layout_id=LAYOUT_ID,
        is_holdout_layout=IS_HOLDOUT_LAYOUT,
        difficulty_tier=DIFFICULTY_TIER,
        degradation={},
        vendor_name=LPG_VENDOR_NAME,
        vendor_uen=LPG_VENDOR_UEN,
        customer_name=profile.name,
        customer_address=customer_address,
        account_number=customer.account_number,
        invoice_number=f"IGS-{year}{month:02d}-{customer.account_number[-4:]}",
        document_date=document_date,
        period_start=period_start,
        period_end=period_end,
        price_list_code=prices.code,
        cylinders=(line,),
        lpg_kg=line.kg,
        # The base layout does NOT print a kg total: the activity has to be computed from
        # the cylinder line. That is the `lpg_cylinders` case, and it is the default here
        # rather than an injected quirk because pricing per cylinder is how the invoice
        # works.
        prints_total_kg=False,
        returned_cylinders=returned,
        cylinder_charge_sgd=cylinder_charge,
        delivery_charge_sgd=prices.delivery_charge_sgd,
        rental_per_cylinder_sgd=prices.rental_per_cylinder_sgd,
        rental_charge_sgd=rental_charge,
        subtotal_sgd=subtotal,
        gst_rate=gst_rate,
        gst_amount_sgd=gst_amount,
        total_sgd=subtotal + gst_amount,
        quirks=[],  # this block ships no injected quirks; Block 2 fills this
    )


def build_invoice_series(
    seed: int = DEFAULT_SEED, customers: tuple[LPGCustomer, ...] = LPG_CUSTOMERS
) -> list[LPGInvoice]:
    """Every LPG invoice: four accounts, three consecutive months each.

    Document numbers come from each customer's `first_doc_number`, not from iteration
    order, so adding a fifth customer cannot renumber the first four (spec 1.4). The
    cylinder count carries forward within an account, which is what makes the returned-
    empties count follow the previous delivery rather than being drawn from nowhere.
    """
    invoices: list[LPGInvoice] = []

    for customer in customers:
        profile = customer_by_key(customer.key)
        address, _ = incidental_details(profile, seed)
        previous_cylinders = 0

        for offset in range(MONTHS_PER_ACCOUNT):
            year, month = add_months(customer.start_year, customer.start_month, offset)
            invoice = build_invoice(
                customer,
                year,
                month,
                f"{DOC_ID_PREFIX}_{customer.first_doc_number + offset:03d}",
                seed,
                customer_address=address,
                previous_cylinders=previous_cylinders,
            )
            previous_cylinders = invoice.cylinder_count
            invoices.append(invoice)

    return invoices
