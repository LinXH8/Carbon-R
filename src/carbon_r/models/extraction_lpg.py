"""What a vision model read off an LPG invoice, and the kg it implies.

The same two-stage shape as the electricity bill - printed strings in, typed values and a
list of problems out - with one difference that is the whole point of this document kind:

    **The activity may not be printed anywhere on the page.**

An LPG invoice says "4 x 50 kg cylinders". The number a factor applies to is 200 kg, and
it appears nowhere. Multiplying is Python's job (CLAUDE.md rule 1), so the model is asked
only to transcribe the delivery lines and `normalise_lpg` does the arithmetic, recording it
on the `Activity` so the step a reviewer cannot see on the page is still auditable.

Two decoys are deliberate, and both are money that is not fuel:

    delivery charge        a service, not a gas
    cylinder rental        hire of the steel, not what is in it
    cylinder deposit       refundable, and not a purchase at all

A reading that summed the invoice's quantities would count rental cylinders as delivered
gas. So the fuel quantity comes from `cylinder_deliveries` alone, which is a separate
transcription field, and the money lines are kept apart from it. `returned_cylinders` is
a third decoy: a count of empties going back, which is not a delivery.

Deciding which printed lines are gas deliveries is classification, not arithmetic, and so
is work the model may do - the same category as choosing a factor category. What it must
never do is total them.
"""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from carbon_r.models.activity import Activity
from carbon_r.models.parsing import (
    ConsistencyFlag,
    FieldProblem,
    comparer,
    is_blank_dash,
    parse_date,
    parse_decimal,
    parse_int,
    taker,
    text_of,
)
from carbon_r.money import round2


class PrintedCylinderDelivery(BaseModel):
    """One "4 x 50 kg" line, exactly as printed. Two strings, no product."""

    model_config = ConfigDict(extra="forbid")

    count: str | None = Field(description="How many cylinders, as printed, e.g. '4'")
    size_kg: str | None = Field(
        description="The size of ONE cylinder in kg, as printed, e.g. '50' or '12.7 kg'"
    )
    description: str | None = Field(description="The line's text as printed, for the record")


class PrintedInvoiceLine(BaseModel):
    """One money line on the invoice, whatever it is for."""

    model_config = ConfigDict(extra="forbid")

    description: str | None = Field(description="The line description as printed")
    quantity: str | None = Field(description="Quantity as printed, or null if none is shown")
    unit_price: str | None = Field(description="Unit price as printed, or null")
    amount: str | None = Field(description="The line amount as printed")


class ExtractedLPGInvoice(BaseModel):
    """What a vision model read off one LPG invoice. Printed strings only."""

    model_config = ConfigDict(extra="forbid")

    vendor_name: str | None = Field(description="Gas supplier name as printed")
    vendor_uen: str | None = Field(description="Vendor UEN / registration number as printed")
    customer_name: str | None = Field(description="Customer name as printed")
    customer_address: str | None = Field(description="Delivery address as printed, one line")
    account_number: str | None = Field(description="Customer account number as printed")
    invoice_number: str | None = Field(description="Invoice number as printed")

    document_date: str | None = Field(description="Invoice date, in the page's format")
    period_start: str | None = Field(description="Start of the supply period, page's format")
    period_end: str | None = Field(description="End of the supply period, page's format")

    cylinder_deliveries: list[PrintedCylinderDelivery] = Field(
        description=(
            "One entry per line of GAS CYLINDERS DELIVERED. Do NOT include cylinder "
            "rental, deposits, returned empties or delivery charges - those are not gas "
            "delivered. If the page shows no cylinder delivery, return an empty list."
        )
    )
    total_kg_printed: str | None = Field(
        description=(
            "A total weight of gas in kg IF the page prints one. Return null if it does "
            "not. Never add the cylinder lines up yourself."
        )
    )
    returned_cylinders: str | None = Field(
        description="Count of empty cylinders returned, as printed, or null"
    )

    lines: list[PrintedInvoiceLine] = Field(
        description=(
            "The CHARGE rows on the invoice, in the order printed. Do not include the "
            "subtotal, GST or total rows - those have their own fields."
        )
    )

    credit_adjustment: str | None = Field(
        description=(
            "A credit or adjustment line for an earlier delivery, as printed (its money "
            "amount only), or null if the page has none. A credit row is a charge row too: "
            "keep it in `lines` with its minus sign"
        )
    )

    currency: str | None = Field(description="Currency code or symbol as printed")
    subtotal: str | None = Field(description="Subtotal before GST as printed")
    gst_percent: str | None = Field(description="GST percentage as printed, e.g. '9'")
    gst_amount: str | None = Field(description="GST amount as printed")
    total: str | None = Field(description="Total payable as printed")

    notes: str | None = Field(description="Anything ambiguous or unreadable; null if nothing")

    def is_empty(self) -> bool:
        """True when the page yielded nothing usable - `notes` alone is not a reading."""
        scalars = all(
            value is None or not str(value).strip()
            for name, value in self.model_dump().items()
            if name not in ("notes", "cylinder_deliveries", "lines")
        )
        return scalars and not self.cylinder_deliveries and not self.lines


class CylinderDelivery(BaseModel):
    """One delivery line, typed. `count x size_kg` is the gas it represents."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    count: int = Field(gt=0)
    size_kg: Decimal = Field(gt=0)

    @property
    def kg(self) -> Decimal:
        return Decimal(self.count) * self.size_kg


class InvoiceLine(BaseModel):
    """One money line, typed. Quantity and unit price are optional; the amount is not."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    description: str
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    amount: Decimal


class NormalisedLPGInvoice(BaseModel):
    """The typed counterpart of an LPG invoice reading. No arithmetic validator, by design.

    Field names match `LPGInvoice`'s, the answer key, so the eval compares like with like.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    vendor_name: str | None = None
    vendor_uen: str | None = None
    customer_name: str | None = None
    customer_address: str | None = None
    account_number: str | None = None
    invoice_number: str | None = None

    document_date: date | None = None
    period_start: date | None = None
    period_end: date | None = None

    cylinders: list[CylinderDelivery] = Field(default_factory=list)
    # Derived from `cylinders` in Python. None when no delivery line was read, which is
    # different from 0 kg: one means "not read", the other means "nothing delivered".
    lpg_kg: Decimal | None = None
    # What the page printed, when it printed anything. Kept beside the derived figure
    # rather than replacing it, so `check_consistency` can compare the two.
    total_kg_printed: Decimal | None = None
    returned_cylinders: int | None = None

    lines: list[InvoiceLine] = Field(default_factory=list)
    # The two named charges the answer key holds flattened. The page shows them as rows,
    # so the reading holds them as rows too - and these pick them back out by their
    # printed label, so the eval compares like with like rather than against a None.
    #
    # Label matching, not position: the row order is a layout's choice. An unmatched
    # label leaves the field None, which scores as a miss rather than silently taking
    # whichever row happened to be in the right place.
    delivery_charge_sgd: Decimal | None = None
    rental_charge_sgd: Decimal | None = None
    # A credit for an earlier delivery, as a magnitude. The row itself stays in `lines`
    # with its sign, so `subtotal = sum(line amounts)` still holds.
    credit_adjustment_sgd: Decimal | None = None

    currency: str | None = None
    subtotal_sgd: Decimal | None = None
    gst_rate: Decimal | None = None
    gst_amount_sgd: Decimal | None = None
    total_sgd: Decimal | None = None

    @property
    def factor_year(self) -> int | None:
        """The year to look a factor up for: the supply period, never document_date."""
        return self.period_start.year if self.period_start else None


def _take_cylinders(
    extracted: ExtractedLPGInvoice,
    values: dict[str, object],
    problems: list[FieldProblem],
) -> None:
    """Type each delivery line, then total the kg in Python.

    A line that cannot be parsed is one `FieldProblem` and is left OUT of the total, and
    the total is still reported - with the problem beside it, so the agent can say "I read
    three of the four delivery lines" rather than either failing the document or quietly
    understating it.
    """
    deliveries: list[CylinderDelivery] = []
    for index, printed in enumerate(extracted.cylinder_deliveries, start=1):
        if printed.count is None or printed.size_kg is None:
            problems.append(
                FieldProblem(
                    field=f"cylinders[{index}]",
                    printed=printed.description,
                    reason="a delivery line needs both a cylinder count and a cylinder size",
                )
            )
            continue
        try:
            deliveries.append(
                CylinderDelivery(
                    count=parse_int(printed.count), size_kg=parse_decimal(printed.size_kg)
                )
            )
        except (ValueError, ArithmeticError) as exc:
            problems.append(
                FieldProblem(
                    field=f"cylinders[{index}]",
                    printed=f"{printed.count} x {printed.size_kg}",
                    reason=str(exc),
                )
            )

    values["cylinders"] = deliveries
    if deliveries:
        # The arithmetic the model is not allowed to do, and the reason this field exists.
        values["lpg_kg"] = sum((d.kg for d in deliveries), Decimal(0))


# Printed labels for the two charges the answer key names. Lower-cased and matched as a
# prefix, so "Cylinder rental (4 x 5.00)" still matches. Deliberately short lists: a
# label this does not know leaves the field None, which an eval reports.
_CHARGE_LABELS = {
    "delivery_charge_sgd": ("delivery charge", "delivery"),
    "rental_charge_sgd": ("cylinder rental", "rental"),
}

# Rows that are summary lines rather than charges. They have their own fields, and
# including them in `lines` would double-count them in `sum(line amounts)` - which is
# exactly what the first eval run found, inflating one subtotal check by S$1,874.80.
_SUMMARY_LABELS = ("subtotal", "gst", "total", "amount due", "balance")


def _is_summary(description: str) -> bool:
    lowered = description.strip().lower()
    return any(lowered.startswith(label) for label in _SUMMARY_LABELS)


def _optional_decimal(
    printed: str | None, field: str, problems: list[FieldProblem]
) -> Decimal | None:
    if printed is None or not printed.strip():
        return None
    try:
        return parse_decimal(printed)
    except (ValueError, ArithmeticError) as exc:
        problems.append(FieldProblem(field=field, printed=printed, reason=str(exc)))
        return None


def _take_lines(
    extracted: ExtractedLPGInvoice,
    values: dict[str, object],
    problems: list[FieldProblem],
) -> None:
    """Type the charge lines, and pick out the two the answer key names.

    Three rows are NOT charges and are dropped rather than reported as problems:
    a summary row (subtotal, GST, total), a row whose amount is a dash standing for
    "nothing here", and a row with no amount cell at all. Each is a faithful reading of
    the page; none is a figure.
    """
    lines: list[InvoiceLine] = []
    for index, printed in enumerate(extracted.lines, start=1):
        description = (printed.description or "").strip()
        if _is_summary(description):
            continue
        # "Empty cylinders collected ... —": a row that carries no money.
        if is_blank_dash(printed.amount):
            continue
        if printed.amount is None or not str(printed.amount).strip():
            problems.append(
                FieldProblem(
                    field=f"lines[{index}]",
                    printed=printed.description,
                    reason="the line has no readable amount",
                )
            )
            continue
        try:
            amount = parse_decimal(printed.amount)
        except (ValueError, ArithmeticError) as exc:
            problems.append(
                FieldProblem(field=f"lines[{index}]", printed=printed.amount, reason=str(exc))
            )
            continue
        # The amount is the figure; quantity and unit price only describe it. A quantity
        # that will not parse is a problem against THAT cell, never a reason to drop the
        # charge - before Block 3 B4 a "2 visits" cell discarded its row's S$16.00.
        lines.append(
            InvoiceLine(
                description=description,
                quantity=_optional_decimal(printed.quantity, f"lines[{index}].quantity", problems),
                unit_price=_optional_decimal(
                    printed.unit_price, f"lines[{index}].unit_price", problems
                ),
                amount=amount,
            )
        )
    values["lines"] = lines

    for field, labels in _CHARGE_LABELS.items():
        for line in lines:
            lowered = line.description.lower()
            if any(lowered.startswith(label) for label in labels):
                values[field] = line.amount
                break


def normalise_lpg(
    extracted: ExtractedLPGInvoice,
) -> tuple[NormalisedLPGInvoice | None, list[FieldProblem]]:
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
        "invoice_number",
        "currency",
    ):
        take(field, field, text_of)

    for field in ("document_date", "period_start", "period_end"):
        take(field, field, parse_date)

    take("total_kg_printed", "total_kg_printed", parse_decimal)
    take("returned_cylinders", "returned_cylinders", parse_int)
    take("credit_adjustment_sgd", "credit_adjustment", lambda printed: abs(parse_decimal(printed)))

    for target, source in (
        ("subtotal_sgd", "subtotal"),
        ("gst_amount_sgd", "gst_amount"),
        ("total_sgd", "total"),
    ):
        take(target, source, parse_decimal)
    take("gst_rate", "gst_percent", lambda printed: parse_decimal(printed) / Decimal(100))

    _take_cylinders(extracted, values, problems)
    _take_lines(extracted, values, problems)

    return NormalisedLPGInvoice(**values), problems


def activities_for_lpg(invoice: NormalisedLPGInvoice) -> list[Activity]:
    """The LPG delivered, in kg, with the cylinder arithmetic written out.

    One activity, not one per delivery line: a factor applied to the total is the same
    number as the sum of the factor applied to each line, and the invoice is one purchase.
    The per-line detail is in `derivation` so the step is still checkable.
    """
    if invoice.lpg_kg is None:
        return []

    if len(invoice.cylinders) == 1:
        only = invoice.cylinders[0]
        derivation = f"{only.count} cylinders x {only.size_kg} kg = {invoice.lpg_kg} kg"
    else:
        parts = " + ".join(f"{d.count} x {d.size_kg} kg" for d in invoice.cylinders)
        derivation = f"{parts} = {invoice.lpg_kg} kg"

    return [
        Activity(
            kind="lpg",
            amount=invoice.lpg_kg,
            unit="kg",
            source_field="cylinder_deliveries",
            derivation=derivation,
            factor_year=invoice.factor_year,
            period_start=invoice.period_start,
            period_end=invoice.period_end,
        )
    ]


def check_consistency_lpg(invoice: NormalisedLPGInvoice) -> list[ConsistencyFlag]:
    """Re-derive the invoice's own arithmetic from its own printed figures."""
    flags: list[ConsistencyFlag] = []
    compare = comparer(flags)

    # The page's own kg total, where it prints one, against the cylinder arithmetic. This
    # is the check that catches a misread cylinder count: the two disagree.
    if invoice.lpg_kg is not None:
        compare(
            "total_kg = sum(cylinder count x cylinder size)",
            invoice.lpg_kg,
            invoice.total_kg_printed,
        )

    for index, line in enumerate(invoice.lines, start=1):
        if line.quantity is not None and line.unit_price is not None:
            compare(
                f"lines[{index}] amount = round2(quantity x unit_price)",
                round2(line.quantity * line.unit_price),
                line.amount,
            )

    if invoice.lines:
        compare(
            "subtotal = sum(line amounts)",
            sum((line.amount for line in invoice.lines), Decimal(0)),
            invoice.subtotal_sgd,
        )

    if invoice.subtotal_sgd is not None and invoice.gst_rate is not None:
        compare(
            "gst_amount = round2(subtotal x gst_rate)",
            round2(invoice.subtotal_sgd * invoice.gst_rate),
            invoice.gst_amount_sgd,
        )

    if invoice.subtotal_sgd is not None and invoice.gst_amount_sgd is not None:
        compare(
            "total = subtotal + gst_amount",
            invoice.subtotal_sgd + invoice.gst_amount_sgd,
            invoice.total_sgd,
        )

    return flags


__all__ = [
    "CylinderDelivery",
    "ExtractedLPGInvoice",
    "InvoiceLine",
    "NormalisedLPGInvoice",
    "PrintedCylinderDelivery",
    "PrintedInvoiceLine",
    "activities_for_lpg",
    "check_consistency_lpg",
    "normalise_lpg",
]
