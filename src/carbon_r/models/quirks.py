"""The quirk catalogue, and the answer-key fields every quirk-bearing document shares.

A quirk is an irregularity a real document has and a clean synthetic one does not. Each is
**controlled** (the generator decides where it goes) and **recorded** (named in the key's
`quirks`, validated against this catalogue), so the eval can say not only "37 of 50" but
which kinds of document defeat the pipeline (SYNTHGEN_SPEC.md Part 5).

Two classes, and the distinction matters for what a difficulty tier means:

- **structural** quirks describe what a document IS. An LPG invoice that prices whole
  cylinders and prints no kg total is `lpg_cylinders`; a statement for a fleet that burns
  two fuels is `mixed_fuel`. They are recorded whenever true, in every tier, including
  the clean one, because removing them would mean generating a different document.
- **injected** quirks are put there by the generator, and only they count toward a tier:
  clean has none, moderate 1-2, hard 3 or more.

This deviates from the spec's "clean: no quirks" and is recorded there. A consequence
worth stating plainly: for LPG and fuel, every injected quirk is a money decoy (a credit,
a balance brought forward, GST shown inclusive), while the traps that change the activity
are structural. A *hard* LPG or fuel document is harder to READ, not harder to INTERPRET.

The fields live in `QuirkFields`, a base every answer-key model inherits, so the three
kinds cannot drift apart on what a credit or a balance forward is. Each model's own
validator calls `check_quirk_fields` and folds `credit_amount_sgd` into its subtotal.
"""

from dataclasses import dataclass
from decimal import Decimal

from pydantic import BaseModel, Field


@dataclass(frozen=True)
class QuirkSpec:
    kinds: frozenset[str]
    structural: bool
    description: str


ELEC = "electricity_bill"
LPG = "lpg_invoice"
FUEL = "fuel_card_statement"

QUIRKS: dict[str, QuirkSpec] = {
    "estimated_reading": QuirkSpec(
        frozenset({ELEC}), False, "the reading basis is printed as Estimated"
    ),
    "cross_month_period": QuirkSpec(
        frozenset({ELEC}),
        False,
        "a meter-read cycle (e.g. 15th to 14th) inside one calendar year",
    ),
    "cross_year_period": QuirkSpec(
        frozenset({ELEC}),
        False,
        "the period straddles 31 December, so two factor years apply, split by days",
    ),
    "unit_mwh": QuirkSpec(
        frozenset({ELEC}), False, "readings and consumption printed in MWh, tariff per kWh"
    ),
    "lpg_cylinders": QuirkSpec(
        frozenset({LPG}), True, "only cylinder counts and sizes are printed; no kg total"
    ),
    "mixed_fuel": QuirkSpec(
        frozenset({FUEL}), True, "more than one fuel product, each needing its own factor"
    ),
    "credit_adjustment": QuirkSpec(
        frozenset({ELEC, LPG, FUEL}),
        False,
        "a credit for a PRIOR period; this period's activity is unchanged by it",
    ),
    "balance_forward": QuirkSpec(
        frozenset({ELEC, LPG, FUEL}),
        False,
        "previous balance and payment printed, so 'amount due' differs from this period",
    ),
    "gst_confusion": QuirkSpec(
        frozenset({ELEC, LPG, FUEL}),
        False,
        "the GST-inclusive figure is the headline; the pre-GST subtotal is not labelled so",
    ),
}

INJECTED_QUIRKS = tuple(name for name, spec in QUIRKS.items() if not spec.structural)


def injected_for(kind: str) -> tuple[str, ...]:
    """The injected quirks that can apply to one document kind, in catalogue order."""
    return tuple(name for name in INJECTED_QUIRKS if kind in QUIRKS[name].kinds)


class QuirkFields(BaseModel):
    """Fields every answer key carries for the shared quirks. Empty on a clean document.

    Not a model on its own: the three kinds inherit it, and their `model_config` applies.
    """

    quirks: list[str] = Field(default_factory=list)

    # --- credit_adjustment --------------------------------------------------------------
    # A credit for something on a PRIOR document (an over-estimated reading, an unused
    # cylinder, a duplicated fuel transaction). It reduces the money on this document and
    # never this period's activity: subtracting it from this period double-counts.
    # `credit_quantity` is the printed decoy quantity, when the credit names one.
    credit_amount_sgd: Decimal = Field(default=Decimal("0.00"), ge=0)
    credit_reference: str | None = None
    credit_quantity: Decimal | None = Field(default=None, gt=0)
    credit_quantity_unit: str | None = None

    # --- balance_forward ----------------------------------------------------------------
    # The account summary: what was owed, what was paid, and what is now due. `total_sgd`
    # stays THIS period's charges; `amount_due_sgd` is a different number on purpose.
    balance_forward_sgd: Decimal | None = Field(default=None, ge=0)
    payment_received_sgd: Decimal | None = Field(default=None, ge=0)
    amount_due_sgd: Decimal | None = None

    # --- gst_confusion ------------------------------------------------------------------
    gst_inclusive_display: bool = False


def check_quirk_fields(document: QuirkFields, kind: str, total_sgd: Decimal) -> None:
    """Raise unless `quirks` and the shared quirk fields agree, in both directions.

    A quirk named without its fields, or fields present without the quirk named, is an
    answer key that misdescribes its own document - and the eval would then score a
    pipeline against the wrong expectation.
    """
    names = document.quirks
    if len(set(names)) != len(names):
        raise ValueError(f"quirks lists a quirk twice: {names}")
    for name in names:
        spec = QUIRKS.get(name)
        if spec is None:
            raise ValueError(f"unknown quirk {name!r}; the catalogue has {sorted(QUIRKS)}")
        if kind not in spec.kinds:
            raise ValueError(f"quirk {name!r} does not apply to a {kind}")

    credited = document.credit_amount_sgd > 0
    if credited != ("credit_adjustment" in names):
        raise ValueError(
            f"credit_amount_sgd {document.credit_amount_sgd} disagrees with quirks {names}: "
            "a credit is present exactly when credit_adjustment is named"
        )
    if credited and not document.credit_reference:
        raise ValueError("a credit must name the prior document it corrects")
    if not credited and (document.credit_reference or document.credit_quantity is not None):
        raise ValueError("credit details are present without a credit")
    if (document.credit_quantity is None) != (document.credit_quantity_unit is None):
        raise ValueError("credit_quantity and credit_quantity_unit come together or not at all")

    summary = (
        document.balance_forward_sgd,
        document.payment_received_sgd,
        document.amount_due_sgd,
    )
    if ("balance_forward" in names) != (document.amount_due_sgd is not None):
        raise ValueError(
            f"amount_due_sgd {document.amount_due_sgd} disagrees with quirks {names}: "
            "an account summary is present exactly when balance_forward is named"
        )
    if document.amount_due_sgd is not None:
        if None in summary:
            raise ValueError("an account summary needs balance, payment and amount due")
        due = document.balance_forward_sgd - document.payment_received_sgd + total_sgd
        if due != document.amount_due_sgd:
            raise ValueError(
                f"amount_due_sgd {document.amount_due_sgd} != balance "
                f"{document.balance_forward_sgd} - payment {document.payment_received_sgd} "
                f"+ this period's total {total_sgd} = {due}"
            )
    elif any(value is not None for value in summary):
        raise ValueError("account-summary fields are present without balance_forward")

    if document.gst_inclusive_display != ("gst_confusion" in names):
        raise ValueError(
            f"gst_inclusive_display {document.gst_inclusive_display} disagrees with quirks {names}"
        )


def require_quirk(names: list[str], name: str, present: bool, why: str) -> None:
    """Raise unless `name` is in `names` exactly when `present` is true."""
    if present != (name in names):
        state = "true" if present else "false"
        raise ValueError(f"{why} is {state} but quirks is {names}: they must agree on {name}")


__all__ = [
    "INJECTED_QUIRKS",
    "QUIRKS",
    "QuirkFields",
    "QuirkSpec",
    "check_quirk_fields",
    "injected_for",
    "require_quirk",
]
