"""`LPGInvoice` - the ground-truth model for a synthetic LPG delivery invoice.

This is an **answer key**, not an extraction result. Per CLAUDE.md rule 4, instances are
produced by `synthgen` alongside the document, never by reading a document with a model.

Every arithmetic relation on the invoice is re-derived by the model validator, so an
internally inconsistent key cannot be constructed. That matters because the key is the
arbiter of truth for every eval downstream: a wrong key sends you debugging a prompt that
was fine.

What makes this document kind different from an electricity bill:

    The activity - kg of LPG - is NOT PRINTED on the page.

The page says "4 x 50 kg". The key holds `lpg_kg = 200`, derived from the cylinder lines,
and the extraction pipeline has to perform the same multiplication in Python. That is the
`lpg_cylinders` trap from SYNTHGEN_SPEC.md 5.1, and it is here in the base document rather
than as an injected quirk because pricing per cylinder is simply how this invoice works.

Three money decoys are deliberate and are **not fuel**:

    delivery charge      a service
    cylinder rental      hire of the steel, charged per cylinder on site
    returned cylinders   a count of empties going back, no money at all

A reading that totalled the invoice's quantity column would count rental cylinders as
delivered gas. The validator asserts that `lpg_kg` depends on the delivery lines alone.

Factor-year rule, as for every kind:

    The emission-factor year comes from the SUPPLY PERIOD, never from `document_date`.
"""

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from carbon_r.models.quirks import QuirkFields, check_quirk_fields, require_quirk
from carbon_r.money import round2


class CylinderLine(BaseModel):
    """One delivery line: `count` cylinders of `size_kg` each, at `price_per_cylinder`.

    `delivery_date` says which visit the line belongs to. Two lines on one date are two
    cylinder sizes from one visit; two dates are two visits (an extra delivery in the
    month), each paying the delivery fee.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    delivery_date: date | None = None
    count: int = Field(gt=0)
    size_kg: Decimal = Field(gt=0)
    price_per_cylinder_sgd: Decimal = Field(gt=0)

    @property
    def kg(self) -> Decimal:
        return Decimal(self.count) * self.size_kg

    @property
    def amount_sgd(self) -> Decimal:
        return round2(Decimal(self.count) * self.price_per_cylinder_sgd)


class LPGInvoice(QuirkFields):
    """One LPG delivery invoice, with its own derivation attached."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # --- identity (spec 3.3: common to every document kind) -------------------------
    # "2" since Tier 2 Block 2: quirk fields, dated delivery lines, billing basis.
    schema_version: Literal["2"] = "2"
    doc_id: str = Field(pattern=r"^lpg_\d{3}$")
    doc_kind: Literal["lpg_invoice"] = "lpg_invoice"
    generator_seed: int
    generator_version: str

    # --- provenance of the rendering (spec 3.3, 1.8) --------------------------------
    layout_id: str = Field(min_length=1)
    is_holdout_layout: bool = False
    difficulty_tier: Literal["clean", "moderate", "hard"] = "clean"
    degradation: dict = Field(default_factory=dict)

    # --- parties ------------------------------------------------------------------
    vendor_name: str = Field(min_length=1)
    vendor_uen: str = Field(min_length=1)
    customer_name: str = Field(min_length=1)
    customer_address: str = Field(min_length=1)
    account_number: str = Field(min_length=1)
    invoice_number: str = Field(min_length=1)

    # --- dates --------------------------------------------------------------------
    # The factor year comes from period_start/period_end, NEVER from document_date.
    document_date: date
    period_start: date
    period_end: date

    # --- the gas ------------------------------------------------------------------
    # `monthly`: one invoice per calendar month, possibly with an extra visit.
    # `per_delivery`: one invoice per visit; the period is the days since the last one.
    billing_basis: Literal["monthly", "per_delivery"] = "monthly"
    price_list_code: str = Field(min_length=1)
    cylinders: tuple[CylinderLine, ...] = Field(min_length=1)
    # Derived = sum(count x size_kg). The activity, and the thing the page never prints.
    lpg_kg: Decimal = Field(gt=0)
    # True when the layout prints a kg total. False on the base layout, which is the
    # `lpg_cylinders` case: the figure must be computed, not read.
    prints_total_kg: bool = False
    # A decoy count: empties collected at the same visit. Never gas delivered.
    returned_cylinders: int = Field(ge=0)

    # --- money --------------------------------------------------------------------
    currency: Literal["SGD"] = "SGD"
    cylinder_charge_sgd: Decimal = Field(ge=0)
    # Per visit, from the price list. None only on a hand-built key, where the delivery
    # charge is then taken as printed.
    delivery_fee_sgd: Decimal | None = Field(default=None, ge=0)
    delivery_charge_sgd: Decimal = Field(ge=0)
    # Charged per cylinder delivered, and not a purchase of gas.
    rental_per_cylinder_sgd: Decimal = Field(ge=0)
    rental_charge_sgd: Decimal = Field(ge=0)
    subtotal_sgd: Decimal = Field(ge=0)
    gst_rate: Decimal = Field(ge=0)
    gst_amount_sgd: Decimal = Field(ge=0)
    total_sgd: Decimal = Field(ge=0)

    # `quirks` and the shared credit / balance / GST-display fields come from QuirkFields.

    @property
    def days_in_period(self) -> int:
        return (self.period_end - self.period_start).days + 1

    @property
    def factor_year(self) -> int:
        """Year to look an emission factor up for: from the period, never document_date."""
        return self.period_start.year

    @property
    def cylinder_count(self) -> int:
        return sum(line.count for line in self.cylinders)

    @property
    def deliveries(self) -> int:
        """Visits on this invoice: distinct delivery dates, or one when lines are undated."""
        dates = {line.delivery_date for line in self.cylinders if line.delivery_date}
        return max(1, len(dates))

    @model_validator(mode="after")
    def _arithmetic_closes(self) -> "LPGInvoice":
        if self.period_start > self.period_end:
            raise ValueError(
                f"period_start {self.period_start} is after period_end {self.period_end}"
            )
        if self.document_date < self.period_end:
            raise ValueError(
                f"document_date {self.document_date} precedes period_end {self.period_end}: "
                "an invoice cannot be issued before the period it bills for has ended"
            )

        # The relation that matters most on this document: the activity comes from the
        # delivery lines and from nothing else. Rental and delivery charges cannot reach it.
        gas = sum((line.kg for line in self.cylinders), Decimal(0))
        if gas != self.lpg_kg:
            raise ValueError(
                f"lpg_kg {self.lpg_kg} != sum(count x size_kg) = {gas}. The activity is the "
                "gas delivered; it is not affected by rental, delivery or returned cylinders"
            )

        cylinder_charge = sum((line.amount_sgd for line in self.cylinders), Decimal(0))
        if cylinder_charge != self.cylinder_charge_sgd:
            raise ValueError(
                f"cylinder_charge_sgd {self.cylinder_charge_sgd} != "
                f"sum(round2(count x price_per_cylinder)) = {cylinder_charge}"
            )

        rental = round2(Decimal(self.cylinder_count) * self.rental_per_cylinder_sgd)
        if rental != self.rental_charge_sgd:
            raise ValueError(
                f"rental_charge_sgd {self.rental_charge_sgd} != "
                f"round2({self.cylinder_count} x {self.rental_per_cylinder_sgd}) = {rental}"
            )

        if self.delivery_fee_sgd is not None:
            delivery = round2(Decimal(self.deliveries) * self.delivery_fee_sgd)
            if delivery != self.delivery_charge_sgd:
                raise ValueError(
                    f"delivery_charge_sgd {self.delivery_charge_sgd} != round2("
                    f"{self.deliveries} visit(s) x {self.delivery_fee_sgd}) = {delivery}"
                )

        outside = [
            line.delivery_date
            for line in self.cylinders
            if line.delivery_date
            and not (self.period_start <= line.delivery_date <= self.period_end)
        ]
        if outside:
            raise ValueError(f"delivery date(s) {outside} fall outside the supply period")

        # A credit for a prior delivery reduces the money, never this invoice's kg.
        subtotal = (
            self.cylinder_charge_sgd
            + self.delivery_charge_sgd
            + self.rental_charge_sgd
            - self.credit_amount_sgd
        )
        if subtotal != self.subtotal_sgd:
            raise ValueError(
                f"subtotal_sgd {self.subtotal_sgd} != cylinder {self.cylinder_charge_sgd} + "
                f"delivery {self.delivery_charge_sgd} + rental {self.rental_charge_sgd} "
                f"- credit {self.credit_amount_sgd} = {subtotal}"
            )

        gst = round2(self.subtotal_sgd * self.gst_rate)
        if gst != self.gst_amount_sgd:
            raise ValueError(
                f"gst_amount_sgd {self.gst_amount_sgd} != "
                f"round2({self.subtotal_sgd} x {self.gst_rate}) = {gst}"
            )

        total = self.subtotal_sgd + self.gst_amount_sgd
        if total != self.total_sgd:
            raise ValueError(
                f"total_sgd {self.total_sgd} != "
                f"{self.subtotal_sgd} + {self.gst_amount_sgd} = {total}"
            )

        check_quirk_fields(self, self.doc_kind, self.total_sgd)
        require_quirk(
            self.quirks, "lpg_cylinders", not self.prints_total_kg, "the kg total being unprinted"
        )
        return self


__all__ = ["CylinderLine", "LPGInvoice"]
