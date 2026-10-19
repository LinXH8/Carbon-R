"""What a vision model read off a fuel-card statement, and the litres per product.

This is the document kind that forced the whole activity abstraction. The other two carry
one activity each; a fuel-card statement carries one **per product**, because diesel and
petrol have different emission factors and adding their litres together before applying a
factor would be wrong by the difference between the two.

So the traps here are about aggregation rather than about reading:

    summing litres across products   wrong: two factors, two lines
    using the money column           an amount is not a quantity of fuel
    counting a discount line         a rebate reduces money, never litres
    reading the gross/net totals     those are money too

`normalise_fuel` therefore sums litres **per product, in Python**, from the transaction
rows, and compares its own totals against the per-product totals the page prints
(`check_consistency_fuel`). A misread litre figure shows up as the two disagreeing rather
than as a quietly wrong answer.

Product names are mapped to activity kinds through a closed allowlist. "Petrol 95" and
"Petrol 98" are both petrol as far as DESNZ's factor table is concerned - there is one
petrol row, not one per octane - whereas an unrecognised product is a `FieldProblem`, not
a guess. Mapping an unknown fuel onto diesel because it looked closest is exactly the kind
of plausible wrong answer rule 7 exists to prevent.
"""

import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from carbon_r.models.activity import Activity
from carbon_r.models.parsing import (
    ConsistencyFlag,
    FieldProblem,
    comparer,
    parse_date,
    parse_decimal,
    taker,
    text_of,
)
from carbon_r.money import round2

# Printed product name (lower-cased, stripped) -> activity kind.
#
# A closed set. Octane grades collapse onto `petrol` because DESNZ publishes one petrol
# factor, not one per grade; "gas oil" is NOT included even though it is chemically close
# to diesel, because DESNZ publishes it as a separate row with a different value and
# treating them as the same fuel would silently pick the wrong factor.
PRODUCT_KINDS: dict[str, str] = {
    "diesel": "diesel",
    "diesel b7": "diesel",
    "euro v diesel": "diesel",
    "euro vi diesel": "diesel",
    "petrol": "petrol",
    "petrol 92": "petrol",
    "petrol 95": "petrol",
    "petrol 98": "petrol",
    "unleaded 95": "petrol",
    "unleaded 98": "petrol",
}


def kind_for_product(printed: str) -> str:
    """The activity kind a printed product name refers to. Raises if it is not known."""
    key = " ".join(printed.strip().lower().split())
    if key in PRODUCT_KINDS:
        return PRODUCT_KINDS[key]
    raise ValueError(
        f"{printed!r} is not a fuel this project has a factor for; known products: "
        f"{', '.join(sorted(PRODUCT_KINDS))}"
    )


class PrintedTransaction(BaseModel):
    """One fuel purchase row, exactly as printed."""

    model_config = ConfigDict(extra="forbid")

    date: str | None = Field(description="Transaction date as printed; include the time if shown")
    vehicle: str | None = Field(description="Vehicle registration as printed")
    station: str | None = Field(description="Station name as printed")
    product: str | None = Field(description="Fuel product as printed, e.g. 'Diesel', 'Petrol 95'")
    litres: str | None = Field(description="Litres as printed, with decimals, unconverted")
    unit_price: str | None = Field(description="Price per litre as printed")
    amount: str | None = Field(description="Line amount as printed")


class PrintedProductTotal(BaseModel):
    """A per-product summary row, where the statement prints one."""

    model_config = ConfigDict(extra="forbid")

    product: str | None = Field(description="Product name as printed")
    litres: str | None = Field(description="Total litres for this product as printed")
    amount: str | None = Field(description="Total amount for this product as printed")


class ExtractedFuelCardStatement(BaseModel):
    """What a vision model read off one fuel-card statement. Printed strings only."""

    model_config = ConfigDict(extra="forbid")

    vendor_name: str | None = Field(description="Fuel card issuer name as printed")
    vendor_uen: str | None = Field(description="Vendor UEN / registration number as printed")
    customer_name: str | None = Field(description="Account holder name as printed")
    customer_address: str | None = Field(description="Account address as printed, one line")
    account_number: str | None = Field(description="Account number as printed")
    card_number: str | None = Field(description="Card number as printed, masked or not")
    statement_number: str | None = Field(description="Statement number as printed")

    document_date: str | None = Field(description="Statement date, in the page's format")
    period_start: str | None = Field(description="Start of the statement period, page's format")
    period_end: str | None = Field(description="End of the statement period, page's format")

    transactions: list[PrintedTransaction] = Field(
        description=(
            "Every fuel purchase row in the transaction table, in the order printed. Do "
            "not include summary, discount or total rows here."
        )
    )
    product_totals: list[PrintedProductTotal] = Field(
        description=(
            "The per-product summary rows IF the statement prints them. Return an empty "
            "list if it does not. Never add the transactions up yourself."
        )
    )

    currency: str | None = Field(description="Currency code or symbol as printed")
    gross_amount: str | None = Field(description="Gross amount before discount, as printed")
    discount_amount: str | None = Field(
        description="Total discount or rebate as printed, positive or with its minus sign"
    )
    subtotal: str | None = Field(description="Amount after discount and before GST, as printed")
    gst_percent: str | None = Field(description="GST percentage as printed, e.g. '9'")
    gst_amount: str | None = Field(description="GST amount as printed")
    total: str | None = Field(description="Net payable as printed")

    notes: str | None = Field(description="Anything ambiguous or unreadable; null if nothing")

    def is_empty(self) -> bool:
        """True when the page yielded nothing usable - `notes` alone is not a reading."""
        scalars = all(
            value is None or not str(value).strip()
            for name, value in self.model_dump().items()
            if name not in ("notes", "transactions", "product_totals")
        )
        return scalars and not self.transactions and not self.product_totals


class Transaction(BaseModel):
    """One purchase, typed. `kind` is the activity kind, resolved from the product name.

    `datetime.date` is written out in full here rather than imported bare: the field is
    called `date`, mirroring the statement's own column heading, and a bare `date` import
    would be shadowed by the field name while the class body is being evaluated.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    date: datetime.date | None = None
    vehicle: str | None = None
    station: str | None = None
    product: str
    kind: str
    litres: Decimal = Field(gt=0)
    unit_price: Decimal | None = None
    amount: Decimal | None = None


class NormalisedFuelCardStatement(BaseModel):
    """The typed counterpart of a statement reading. No arithmetic validator, by design."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    vendor_name: str | None = None
    vendor_uen: str | None = None
    customer_name: str | None = None
    customer_address: str | None = None
    account_number: str | None = None
    card_number: str | None = None
    statement_number: str | None = None

    document_date: datetime.date | None = None
    period_start: datetime.date | None = None
    period_end: datetime.date | None = None

    transactions: list[Transaction] = Field(default_factory=list)
    # Litres per ACTIVITY KIND, summed in Python from the transactions. Keyed by kind
    # rather than by printed product name, so "Petrol 95" and "Petrol 98" on one statement
    # are one petrol line with one factor rather than two.
    litres_by_kind: dict[str, Decimal] = Field(default_factory=dict)
    # What the page printed per product, where it printed anything. Kept beside the derived
    # figures so the two can be compared rather than one replacing the other.
    litres_printed_by_kind: dict[str, Decimal] = Field(default_factory=dict)

    currency: str | None = None
    gross_amount_sgd: Decimal | None = None
    discount_amount_sgd: Decimal | None = None
    subtotal_sgd: Decimal | None = None
    gst_rate: Decimal | None = None
    gst_amount_sgd: Decimal | None = None
    total_sgd: Decimal | None = None

    @property
    def factor_year(self) -> int | None:
        """The year to look a factor up for: the statement period, never document_date."""
        return self.period_start.year if self.period_start else None


def _take_transactions(
    extracted: ExtractedFuelCardStatement,
    values: dict[str, object],
    problems: list[FieldProblem],
) -> None:
    """Type every purchase row, then total litres per kind in Python.

    A row that cannot be parsed is one `FieldProblem` and is left out of the totals, which
    `check_consistency_fuel` will then surface as a disagreement with the printed totals -
    two independent signals that the reading is short, rather than a silent undercount.
    """
    transactions: list[Transaction] = []
    for index, printed in enumerate(extracted.transactions, start=1):
        label = f"transactions[{index}]"
        if printed.litres is None or not str(printed.litres).strip():
            problems.append(
                FieldProblem(
                    field=label,
                    printed=printed.product,
                    reason="the row has no readable litres figure, so it cannot be an activity",
                )
            )
            continue
        if printed.product is None or not str(printed.product).strip():
            problems.append(
                FieldProblem(
                    field=label,
                    printed=printed.litres,
                    reason="the row names no product, so no emission factor applies to it",
                )
            )
            continue
        try:
            transactions.append(
                Transaction(
                    date=parse_date(printed.date) if printed.date else None,
                    vehicle=printed.vehicle.strip() if printed.vehicle else None,
                    station=printed.station.strip() if printed.station else None,
                    product=printed.product.strip(),
                    kind=kind_for_product(printed.product),
                    litres=parse_decimal(printed.litres),
                    unit_price=parse_decimal(printed.unit_price) if printed.unit_price else None,
                    amount=parse_decimal(printed.amount) if printed.amount else None,
                )
            )
        except (ValueError, ArithmeticError) as exc:
            problems.append(
                FieldProblem(
                    field=label,
                    printed=f"{printed.product} {printed.litres}",
                    reason=str(exc),
                )
            )

    values["transactions"] = transactions

    totals: dict[str, Decimal] = {}
    for transaction in transactions:
        totals[transaction.kind] = totals.get(transaction.kind, Decimal(0)) + transaction.litres
    values["litres_by_kind"] = totals


def _take_printed_totals(
    extracted: ExtractedFuelCardStatement,
    values: dict[str, object],
    problems: list[FieldProblem],
) -> None:
    """Type the statement's own per-product totals, keyed by activity kind."""
    printed_totals: dict[str, Decimal] = {}
    for index, row in enumerate(extracted.product_totals, start=1):
        if not row.product or not row.litres:
            continue
        try:
            kind = kind_for_product(row.product)
            printed_totals[kind] = printed_totals.get(kind, Decimal(0)) + parse_decimal(row.litres)
        except (ValueError, ArithmeticError) as exc:
            problems.append(
                FieldProblem(
                    field=f"product_totals[{index}]",
                    printed=f"{row.product} {row.litres}",
                    reason=str(exc),
                )
            )
    values["litres_printed_by_kind"] = printed_totals


def normalise_fuel(
    extracted: ExtractedFuelCardStatement,
) -> tuple[NormalisedFuelCardStatement | None, list[FieldProblem]]:
    """Turn printed strings into typed values. Plain Python; no model involved."""
    if extracted.is_empty():
        return None, [
            FieldProblem(field="(document)", printed=None, reason="No field was read from the page")
        ]

    problems: list[FieldProblem] = []
    values: dict[str, object] = {}
    take = taker(extracted, values, problems)

    for field in (
        "vendor_name",
        "vendor_uen",
        "customer_name",
        "customer_address",
        "account_number",
        "card_number",
        "statement_number",
        "currency",
    ):
        take(field, field, text_of)

    for field in ("document_date", "period_start", "period_end"):
        take(field, field, parse_date)

    for target, source in (
        ("gross_amount_sgd", "gross_amount"),
        ("subtotal_sgd", "subtotal"),
        ("gst_amount_sgd", "gst_amount"),
        ("total_sgd", "total"),
    ):
        take(target, source, parse_decimal)

    # The discount is stored as a MAGNITUDE, matching the answer key, while the page
    # prints it as a deduction ("−15.49"). Both describe the same thing; the direction is
    # fixed by the relation `subtotal = gross - discount` rather than by the sign of this
    # field, and `FuelCardStatement.discount_amount_sgd` is declared `ge=0` for that
    # reason.
    #
    # Taking the magnitude here rather than in the comparison keeps the field names
    # meaning the same thing on both sides, which is the whole reason they match. The
    # printed sign still has to PARSE - losing it was a defect the first eval run found -
    # it just does not survive into the stored value.
    take("discount_amount_sgd", "discount_amount", lambda printed: abs(parse_decimal(printed)))
    take("gst_rate", "gst_percent", lambda printed: parse_decimal(printed) / Decimal(100))

    _take_transactions(extracted, values, problems)
    _take_printed_totals(extracted, values, problems)

    return NormalisedFuelCardStatement(**values), problems


def activities_for_fuel(statement: NormalisedFuelCardStatement) -> list[Activity]:
    """One activity per fuel kind, in litres, with the sum written out.

    Ordered by kind so the lines of a calculation are stable across runs - a figure whose
    line order moved between two runs of the same document would be hard to diff.
    """
    activities: list[Activity] = []
    for kind in sorted(statement.litres_by_kind):
        litres = statement.litres_by_kind[kind]
        rows = [t for t in statement.transactions if t.kind == kind]
        derivation = (
            f"{len(rows)} {kind} transaction(s): "
            + " + ".join(str(t.litres) for t in rows)
            + f" = {litres} litres"
        )
        activities.append(
            Activity(
                kind=kind,  # type: ignore[arg-type]  - keys come from PRODUCT_KINDS
                amount=litres,
                unit="litres",
                source_field="transactions.litres",
                derivation=derivation,
            )
        )
    return activities


def check_consistency_fuel(
    statement: NormalisedFuelCardStatement,
) -> list[ConsistencyFlag]:
    """Re-derive the statement's own arithmetic from its own printed figures.

    The per-kind litre comparison is the important one: it is the only check that can
    catch a misread litre figure, since nothing else on the page depends on litres except
    the money, which has its own rounding.
    """
    flags: list[ConsistencyFlag] = []
    compare = comparer(flags)

    for kind, derived in sorted(statement.litres_by_kind.items()):
        compare(
            f"litres[{kind}] = sum(transaction litres)",
            derived,
            statement.litres_printed_by_kind.get(kind),
        )

    # A product the page totals but no transaction row produced: the transaction table was
    # read short. Reported in the same shape, with 0 as what was derived.
    for kind, printed in sorted(statement.litres_printed_by_kind.items()):
        if kind not in statement.litres_by_kind:
            compare(f"litres[{kind}] = sum(transaction litres)", Decimal(0), printed)

    for index, transaction in enumerate(statement.transactions, start=1):
        if transaction.unit_price is not None and transaction.amount is not None:
            compare(
                f"transactions[{index}] amount = round2(litres x unit_price)",
                round2(transaction.litres * transaction.unit_price),
                transaction.amount,
            )

    amounts = [t.amount for t in statement.transactions if t.amount is not None]
    if amounts and len(amounts) == len(statement.transactions):
        compare(
            "gross_amount = sum(transaction amounts)",
            sum(amounts, Decimal(0)),
            statement.gross_amount_sgd,
        )

    if statement.gross_amount_sgd is not None and statement.discount_amount_sgd is not None:
        # `discount_amount_sgd` is a magnitude however the page printed it, so the
        # relation is a plain subtraction. See `normalise_fuel`.
        compare(
            "subtotal = gross_amount - discount",
            statement.gross_amount_sgd - statement.discount_amount_sgd,
            statement.subtotal_sgd,
        )

    if statement.subtotal_sgd is not None and statement.gst_rate is not None:
        compare(
            "gst_amount = round2(subtotal x gst_rate)",
            round2(statement.subtotal_sgd * statement.gst_rate),
            statement.gst_amount_sgd,
        )

    if statement.subtotal_sgd is not None and statement.gst_amount_sgd is not None:
        compare(
            "total = subtotal + gst_amount",
            statement.subtotal_sgd + statement.gst_amount_sgd,
            statement.total_sgd,
        )

    return flags


__all__ = [
    "PRODUCT_KINDS",
    "ExtractedFuelCardStatement",
    "NormalisedFuelCardStatement",
    "PrintedProductTotal",
    "PrintedTransaction",
    "Transaction",
    "activities_for_fuel",
    "check_consistency_fuel",
    "kind_for_product",
    "normalise_fuel",
]
