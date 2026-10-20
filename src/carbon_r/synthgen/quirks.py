"""Quirk injectors: take a validated clean document, return a validated quirky one.

The catalogue itself is `carbon_r.models.quirks` (the answer keys validate against it).
This module only APPLIES quirks, in plain Python, and every injector ends by
re-validating the whole document - so a quirk that broke the arithmetic fails here, before
anything is rendered (spec 5.3).

The two period quirks (`cross_month_period`, `cross_year_period`) are not here: a period
changes the consumption itself, so the electricity builder builds those bills with their
period from the start. Everything else is applied after the clean build, in a fixed order:

    credit_adjustment  - changes subtotal, GST and total
    balance_forward    - depends on the total, so comes after the credit
    gst_confusion, estimated_reading, unit_mwh - presentation and flags only

Each injector draws from its own labelled stream, `doc_rng(seed, doc_id, "<quirk>")`, so
adding a quirk type never moves another quirk's draw.

What a credit is, in every kind: a credit for something on a PRIOR document. Half of them
print a quantity - kWh, kg or litres - and that quantity is a decoy: this period's activity
is unchanged, and subtracting it double-counts. Restating the prior period is not modelled
(a known limit, TIER2_BLOCK2.md).
"""

import calendar
from decimal import Decimal

from carbon_r.money import round2
from carbon_r.synthgen.degrade import params_for
from carbon_r.synthgen.rng import doc_rng

# Share of credits that print a decoy quantity rather than money alone.
QUANTITY_CREDIT_SHARE = 0.5
# A prior over-estimate, as a share of this period's consumption.
CREDIT_KWH_SHARE = (0.03, 0.12)
MONEY_CREDIT_SGD = (18.0, 95.0)
# The previous bill brought forward, relative to this one; and how much of it was paid.
BALANCE_RELATIVE = (0.85, 1.15)
PARTIAL_PAYMENT_SHARE = 0.4
PARTIAL_PAYMENT = (0.4, 0.8)


def _money(value: float) -> Decimal:
    return Decimal(str(round(value, 2)))


def _previous_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def _month_name(year: int, month: int) -> str:
    return f"{calendar.month_name[month]} {year}"


def _revalidate(document, **changes):
    """A new, fully validated document with `changes` applied."""
    return type(document).model_validate({**document.model_dump(), **changes})


def _charges(document) -> Decimal:
    """This period's charges before any credit: what the subtotal would be without one."""
    kind = document.doc_kind
    if kind == "electricity_bill":
        return document.energy_charge_sgd + document.fixed_charge_sgd
    if kind == "lpg_invoice":
        return (
            document.cylinder_charge_sgd + document.delivery_charge_sgd + document.rental_charge_sgd
        )
    return document.gross_amount_sgd - document.discount_amount_sgd


def _with_quirk(document, name: str) -> list[str]:
    from carbon_r.models.quirks import QUIRKS

    names = set(document.quirks) | {name}
    return [q for q in QUIRKS if q in names]


# --- credit_adjustment ---------------------------------------------------------------


def _credit_terms(document, rng) -> dict:
    """(amount, reference, quantity, unit) for this kind's credit."""
    year, month = _previous_month(document.period_start.year, document.period_start.month)
    prior = _month_name(year, month)
    with_quantity = rng.random() < QUANTITY_CREDIT_SHARE
    kind = document.doc_kind

    if kind == "electricity_bill":
        if with_quantity:
            kwh = max(1, round(document.consumption_kwh * rng.uniform(*CREDIT_KWH_SHARE)))
            return {
                "credit_amount_sgd": round2(
                    Decimal(kwh) * document.tariff_cents_per_kwh / Decimal(100)
                ),
                "credit_reference": f"Over-estimated reading on the {prior} bill",
                "credit_quantity": Decimal(kwh),
                "credit_quantity_unit": "kWh",
            }
        return {
            "credit_amount_sgd": _money(rng.uniform(*MONEY_CREDIT_SGD)),
            "credit_reference": f"Billing correction, {prior}",
        }

    if kind == "lpg_invoice":
        from carbon_r.synthgen.profiles import lpg_price_list

        prices = lpg_price_list(document.period_start.year)
        if with_quantity:
            size = document.cylinders[0].size_kg
            return {
                "credit_amount_sgd": prices.price_per_cylinder_sgd[size],
                "credit_reference": f"Unused cylinder returned from the {prior} delivery",
                "credit_quantity": size,
                "credit_quantity_unit": "kg",
            }
        return {
            "credit_amount_sgd": prices.delivery_charge_sgd,
            "credit_reference": f"Delivery charge refunded, {prior} (late delivery)",
        }

    from carbon_r.synthgen.fuel_card import litres_for_fill
    from carbon_r.synthgen.profiles import fuel_price_list

    if with_quantity:
        transaction = document.transactions[0]
        vehicle = _vehicle(document, transaction.vehicle)
        litres = litres_for_fill(vehicle, rng)
        price = fuel_price_list(year).prices[(year, month)][transaction.product]
        return {
            "credit_amount_sgd": round2(litres * price),
            "credit_reference": (
                f"Reversal of a duplicated {transaction.product} charge, {prior} statement"
            ),
            "credit_quantity": litres,
            "credit_quantity_unit": "litres",
        }
    return {
        "credit_amount_sgd": Decimal("5.00"),
        "credit_reference": f"Card fee waived, {prior}",
    }


def _vehicle(statement, plate: str):
    from carbon_r.synthgen.profiles import FUEL_CUSTOMERS, SCENARIO_FUEL_CUSTOMERS

    # v1's cards first, so a v1 plate resolves exactly as it always did.
    for customer in FUEL_CUSTOMERS + SCENARIO_FUEL_CUSTOMERS:
        for vehicle in customer.fleet.vehicles:
            if vehicle.plate == plate:
                return vehicle
    raise KeyError(f"No vehicle {plate!r} on any fuel card")


def inject_credit(document, seed: int):
    rng = doc_rng(seed, document.doc_id, "credit_adjustment")
    terms = _credit_terms(document, rng)
    charges = _charges(document)
    if terms["credit_amount_sgd"] >= charges:
        raise ValueError(
            f"{document.doc_id}: a credit of {terms['credit_amount_sgd']} would exceed this "
            f"period's charges {charges}"
        )
    subtotal = charges - terms["credit_amount_sgd"]
    gst = round2(subtotal * document.gst_rate)
    return _revalidate(
        document,
        **terms,
        subtotal_sgd=subtotal,
        gst_amount_sgd=gst,
        total_sgd=subtotal + gst,
        quirks=_with_quirk(document, "credit_adjustment"),
    )


# --- balance_forward -----------------------------------------------------------------


def inject_balance_forward(document, seed: int):
    rng = doc_rng(seed, document.doc_id, "balance_forward")
    balance = round2(document.total_sgd * Decimal(str(round(rng.uniform(*BALANCE_RELATIVE), 4))))
    if rng.random() < PARTIAL_PAYMENT_SHARE:
        payment = round2(balance * Decimal(str(round(rng.uniform(*PARTIAL_PAYMENT), 4))))
    else:
        payment = balance
    return _revalidate(
        document,
        balance_forward_sgd=balance,
        payment_received_sgd=payment,
        amount_due_sgd=balance - payment + document.total_sgd,
        quirks=_with_quirk(document, "balance_forward"),
    )


# --- presentation and flags ----------------------------------------------------------


def inject_gst_confusion(document, seed: int):
    return _revalidate(
        document, gst_inclusive_display=True, quirks=_with_quirk(document, "gst_confusion")
    )


def inject_estimated_reading(document, seed: int):
    return _revalidate(
        document, is_estimated_reading=True, quirks=_with_quirk(document, "estimated_reading")
    )


def inject_unit_mwh(document, seed: int):
    return _revalidate(
        document, consumption_unit_printed="MWh", quirks=_with_quirk(document, "unit_mwh")
    )


# Applied in this order; see the module docstring.
INJECTORS = {
    "credit_adjustment": inject_credit,
    "balance_forward": inject_balance_forward,
    "gst_confusion": inject_gst_confusion,
    "estimated_reading": inject_estimated_reading,
    "unit_mwh": inject_unit_mwh,
}
BUILT_IN = ("cross_month_period", "cross_year_period")


def dress(document, plan, seed: int):
    """Apply a plan's injected quirks and degradation parameters to a clean document."""
    built = {q for q in document.quirks if q in BUILT_IN}
    wanted_built = {q for q in plan.quirks if q in BUILT_IN}
    if built != wanted_built:
        raise ValueError(
            f"{document.doc_id}: the builder made period quirks {sorted(built)}, the plan "
            f"asked for {sorted(wanted_built)}"
        )
    for name, injector in INJECTORS.items():
        if name in plan.quirks:
            document = injector(document, seed)
    unknown = set(plan.quirks) - set(INJECTORS) - set(BUILT_IN)
    if unknown:
        raise ValueError(f"{document.doc_id}: no injector for {sorted(unknown)}")
    return _revalidate(document, degradation=params_for(seed, document.doc_id, plan.tier))


__all__ = ["BUILT_IN", "INJECTORS", "dress"]
