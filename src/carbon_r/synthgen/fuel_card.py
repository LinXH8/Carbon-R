"""Transaction model and money arithmetic for synthetic fuel-card statements.

Plain Python throughout (CLAUDE.md rule 1). It imports no model provider and touches no
database.

Unlike the other two kinds, the activity here is not one number shaped by a seasonal
curve. It is the sum of individual fills, each of which is its own small decision:

    fleet  ->  which vehicles      (per customer, declared in profiles.py)
    month  ->  how many fills      (per vehicle, with a +/-1 wobble)
    fill   ->  how many litres     (uniform within the tank's usable range)
    litres ->  amount              (litres x that month's pump price)

That gives the variation the LPG invoice cannot have: no two statements carry the same
litres, so nothing can be scored by memorising a figure.

Two properties are deliberate and both are traps the extraction has to survive:

**Litres are per product, never pooled.** A mixed fleet produces diesel litres and petrol
litres, which take different emission factors. `litres_by_product` is the truth and there
is no total-litres field, because the sum of the two is a number with no legitimate use.

**The discount is money only.** A per-litre rebate reduces what is payable and leaves the
litres untouched. A reading that let it reduce the activity would understate the emissions
by the discount's share - plausibly, and in the right direction to look like a correction.

A tank is never filled from empty: `FILL_FRACTION_RANGE` keeps a fill between about half
and nine tenths of tank capacity, which is what a driver topping up actually buys.
"""

import random
from datetime import date, timedelta
from decimal import Decimal

from carbon_r.config import get_settings
from carbon_r.models.fuel_card_statement import FuelCardStatement, FuelTransaction
from carbon_r.money import round2
from carbon_r.synthgen.electricity import days_in_month
from carbon_r.synthgen.profiles import (
    FUEL_CUSTOMERS,
    FUEL_VENDOR_NAME,
    FUEL_VENDOR_UEN,
    FuelCustomer,
    Vehicle,
    customer_by_key,
    fuel_price_list,
    incidental_details,
)
from carbon_r.synthgen.rng import bill_rng

DOC_KIND = "fuel_card_statement"
DOC_ID_PREFIX = "fuel"
LAYOUT_ID = "fuel_a"
IS_HOLDOUT_LAYOUT = False
DIFFICULTY_TIER = "clean"

DEFAULT_SEED = 20261016  # the same master seed as the other kinds
# 2 since Tier 2 Block 2: price list by year, rebate per contract, quirks.
GENERATOR_VERSION = "synthgen-fuel-card-2"

# A statement follows the period it covers by a few days, drawn per document.
STATEMENT_LAG_DAYS_RANGE = (2, 7)
MONTHS_PER_ACCOUNT = 3

# How full a tank gets on one visit, as a fraction of capacity. Nobody arrives empty.
FILL_FRACTION_RANGE = (0.52, 0.90)
# A vehicle's monthly fill count wobbles by one either way around the fleet's nominal rate.
FILLS_WOBBLE = 1
# Fills land in working hours.
FILL_HOUR_RANGE = (6, 20)

STATIONS = (
    "Jurong Pier Service Point",
    "Pandan Loop Station",
    "Kranji Way Station",
    "Toh Guan Service Point",
    "Sungei Kadut Station",
)


def fills_for_vehicle(nominal: int, rng: random.Random, *, count: int | None = None) -> int:
    """How many times one vehicle filled up in a month. At least one.

    `count` is injectable so a test can fix the transaction count and assert exact litres
    rather than asserting on whatever the RNG drew (spec 1.5).
    """
    if count is not None:
        return count
    return max(1, nominal + rng.randint(-FILLS_WOBBLE, FILLS_WOBBLE))


def litres_for_fill(
    vehicle: Vehicle, rng: random.Random, *, fraction: float | None = None
) -> Decimal:
    """Litres taken in one fill, to 2 dp as a pump prints them.

    Quantised to 2 dp here rather than later: the printed figure is the activity, and a
    key holding more precision than the page shows could never be recovered by reading it.
    """
    if fraction is None:
        fraction = rng.uniform(*FILL_FRACTION_RANGE)
    litres = Decimal(str(round(vehicle.tank_litres * fraction, 2)))
    return litres


def statement_period(
    year: int, month: int, rng: random.Random | None = None, *, lag_days: int | None = None
) -> tuple[date, date, date]:
    """(period_start, period_end, document_date) for one whole calendar month."""
    period_start = date(year, month, 1)
    period_end = date(year, month, days_in_month(year, month))
    lag = lag_days if lag_days is not None else rng.randint(*STATEMENT_LAG_DAYS_RANGE)
    return period_start, period_end, period_end + timedelta(days=lag)


def build_statement(
    customer: FuelCustomer,
    year: int,
    month: int,
    doc_id: str,
    seed: int,
    *,
    customer_address: str,
    lag_days: int | None = None,
    layout_id: str = LAYOUT_ID,
    is_holdout_layout: bool = IS_HOLDOUT_LAYOUT,
    difficulty_tier: str = DIFFICULTY_TIER,
) -> FuelCardStatement:
    """Assemble and validate one clean statement. Quirks are applied afterwards.

    Returns a validated FuelCardStatement, so an arithmetic mistake in this module fails
    here - before anything is written to disk and long before a PDF exists.
    """
    profile = customer_by_key(customer.key)
    prices = fuel_price_list(year)
    rng = bill_rng(seed, customer.account_number, year, month, DOC_KIND)

    month_prices = prices.prices.get((year, month))
    if month_prices is None:
        raise ValueError(
            f"{prices.code} has no prices for {year}-{month:02d}; extend the price list "
            "in profiles.py rather than falling back to another month's price"
        )

    period_start, period_end, document_date = statement_period(year, month, rng, lag_days=lag_days)
    last_day = days_in_month(year, month)

    transactions: list[FuelTransaction] = []
    for vehicle in customer.fleet.vehicles:
        unit_price = month_prices.get(vehicle.product)
        if unit_price is None:
            raise ValueError(
                f"{prices.code} has no {vehicle.product!r} price for {year}-{month:02d}"
            )
        for _ in range(fills_for_vehicle(customer.fleet.fills_per_vehicle_per_month, rng)):
            litres = litres_for_fill(vehicle, rng)
            transactions.append(
                FuelTransaction(
                    date=date(year, month, rng.randint(1, last_day)),
                    time=f"{rng.randint(*FILL_HOUR_RANGE):02d}:{rng.randint(0, 59):02d}",
                    vehicle=vehicle.plate,
                    station=STATIONS[rng.randrange(len(STATIONS))],
                    product=vehicle.product,
                    litres=litres,
                    unit_price_sgd=unit_price,
                    amount_sgd=round2(litres * unit_price),
                )
            )

    # Chronological, as a statement prints them. Sorted by (date, time, vehicle) so the
    # order is total: two fills on the same day at the same minute would otherwise order
    # differently between runs, and the PDF would not be reproducible from the seed.
    transactions.sort(key=lambda t: (t.date, t.time, t.vehicle))

    litres_by_product: dict[str, Decimal] = {}
    for transaction in transactions:
        litres_by_product[transaction.product] = (
            litres_by_product.get(transaction.product, Decimal(0)) + transaction.litres
        )

    # Money, in the exact order FuelCardStatement's validator re-checks it.
    gst_rate = Decimal(str(get_settings().gst_rate))  # str(): never Decimal(float)
    gross = sum((t.amount_sgd for t in transactions), Decimal(0))
    total_litres = sum(litres_by_product.values(), Decimal(0))
    discount = round2(total_litres * customer.discount_per_litre_sgd)
    subtotal = gross - discount
    gst_amount = round2(subtotal * gst_rate)

    return FuelCardStatement(
        doc_id=doc_id,
        generator_seed=seed,
        generator_version=GENERATOR_VERSION,
        layout_id=layout_id,
        is_holdout_layout=is_holdout_layout,
        difficulty_tier=difficulty_tier,
        degradation={},
        vendor_name=FUEL_VENDOR_NAME,
        vendor_uen=FUEL_VENDOR_UEN,
        customer_name=profile.name,
        customer_address=customer_address,
        account_number=customer.account_number,
        card_number=customer.fleet.card_number,
        statement_number=f"SFC-{year}{month:02d}-{customer.account_number[-4:]}",
        document_date=document_date,
        period_start=period_start,
        period_end=period_end,
        price_list_code=prices.code,
        transactions=tuple(transactions),
        litres_by_product=litres_by_product,
        prints_product_totals=True,
        gross_amount_sgd=gross,
        discount_per_litre_sgd=customer.discount_per_litre_sgd,
        discount_amount_sgd=discount,
        subtotal_sgd=subtotal,
        gst_rate=gst_rate,
        gst_amount_sgd=gst_amount,
        total_sgd=subtotal + gst_amount,
        # Structural: a statement for a fleet burning two fuels is what it is, in every tier.
        quirks=["mixed_fuel"] if len(litres_by_product) > 1 else [],
    )


def build_statement_series(
    seed: int = DEFAULT_SEED, customers: tuple[FuelCustomer, ...] = FUEL_CUSTOMERS
) -> list[FuelCardStatement]:
    """The Block 1 statements: the original two accounts, three consecutive months each.

    Document numbers come from each customer's `first_doc_number`, not from iteration
    order; a customer with 0 has no Block 1 run (spec 1.4).
    """
    from carbon_r.synthgen.plan import FUEL, plan_kind

    return build_v1(seed, plan_kind(FUEL, seed, series=(), accounts=customers), customers)


def build_v1(
    seed: int = DEFAULT_SEED, plans=None, customers: tuple[FuelCustomer, ...] = FUEL_CUSTOMERS
) -> list[FuelCardStatement]:
    """Every synthgen v1 statement, from the plan, clean and then dressed."""
    from carbon_r.synthgen.layouts import layout
    from carbon_r.synthgen.plan import FUEL, plan_kind
    from carbon_r.synthgen.quirks import dress

    plans = plans if plans is not None else plan_kind(FUEL, seed)
    by_key = {customer.key: customer for customer in customers}
    built = {}
    for plan in plans:
        customer = by_key[plan.account]
        address, _ = incidental_details(customer_by_key(customer.key), seed)
        statement = build_statement(
            customer,
            plan.year,
            plan.month,
            plan.doc_id,
            seed,
            customer_address=address,
            layout_id=plan.layout_id,
            is_holdout_layout=layout(plan.layout_id).is_holdout,
            difficulty_tier=plan.tier,
        )
        built[plan.number] = dress(statement, plan, seed)
    return [built[n] for n in sorted(built)]
