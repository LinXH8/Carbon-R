"""`FuelCardStatement` - the ground-truth model for a synthetic fuel-card statement.

This is an **answer key**, not an extraction result. Per CLAUDE.md rule 4, instances are
produced by `synthgen` alongside the document, never by reading a document with a model.

What makes this document kind different from the other two:

    It carries ONE ACTIVITY PER PRODUCT, not one per document.

A statement listing diesel and petrol has two activities, and they need two different
emission factors. Summing the litres first would be wrong by the difference between the
two factors, so `litres_by_product` is the truth and a single total litre figure is
deliberately NOT a field on this model: there is no such quantity, and offering one would
invite exactly the mistake the document exists to test.

The money decoys here are different from the LPG invoice's:

    per-litre discount   reduces the amount payable and NOT the litres
    gross vs net         both are money; neither is a quantity of fuel

A reading that used the amount column instead of the litres column would be wrong by the
pump price, and a reading that let the discount reduce the litres would understate the
emissions. The validator holds the discount to the money side.

GST convention, which is a VERIFY item (docs/SOURCES.md): amounts are GST-EXCLUSIVE, with GST
added as its own line, matching the electricity bill and the LPG invoice. Whether
Singapore pump prices are quoted inclusive of GST, and how fuel-card statements present
it, has not been verified. Litres are unaffected by the choice either way, so it cannot
move an emissions figure.

Factor-year rule, as for every kind:

    The emission-factor year comes from the STATEMENT PERIOD, never from `document_date`.
"""

import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from carbon_r.models.quirks import QuirkFields, check_quirk_fields, require_quirk
from carbon_r.money import round2


class FuelTransaction(BaseModel):
    """One fill. `litres x unit_price` is the amount; the litres are the activity.

    `datetime.date` is written out in full because the field is called `date`, mirroring
    the statement's own column heading, and a bare `date` import would be shadowed by the
    field name while the class body is being evaluated.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    date: datetime.date
    time: str = Field(min_length=1, description="HH:MM as printed; not a key field")
    vehicle: str = Field(min_length=1)
    station: str = Field(min_length=1)
    product: str = Field(min_length=1)
    litres: Decimal = Field(gt=0)
    unit_price_sgd: Decimal = Field(gt=0)
    amount_sgd: Decimal = Field(gt=0)

    @model_validator(mode="after")
    def _amount_closes(self) -> "FuelTransaction":
        expected = round2(self.litres * self.unit_price_sgd)
        if expected != self.amount_sgd:
            raise ValueError(
                f"amount_sgd {self.amount_sgd} != "
                f"round2({self.litres} x {self.unit_price_sgd}) = {expected}"
            )
        return self


class FuelCardStatement(QuirkFields):
    """One monthly fuel-card statement, with its own derivation attached."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # --- identity (spec 3.3: common to every document kind) -------------------------
    # "2" since Tier 2 Block 2: quirk fields.
    schema_version: Literal["2"] = "2"
    doc_id: str = Field(pattern=r"^fuel_\d{3}$")
    doc_kind: Literal["fuel_card_statement"] = "fuel_card_statement"
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
    card_number: str = Field(min_length=1)
    statement_number: str = Field(min_length=1)

    # --- dates --------------------------------------------------------------------
    document_date: datetime.date
    period_start: datetime.date
    period_end: datetime.date

    # --- the fuel -----------------------------------------------------------------
    price_list_code: str = Field(min_length=1)
    transactions: tuple[FuelTransaction, ...] = Field(min_length=1)
    # Litres per PRINTED PRODUCT NAME, derived from the transactions. The activity truth.
    #
    # There is deliberately no `total_litres`: litres of diesel and litres of petrol are
    # not the same quantity, they take different factors, and a field holding their sum
    # would be a number with no legitimate use.
    litres_by_product: dict[str, Decimal]
    # True when the layout prints the per-product summary rows. The base layout does, so
    # extraction has something to be checked against; a layout that omits them is a
    # harder case for Block 2.
    prints_product_totals: bool = True

    # --- money --------------------------------------------------------------------
    currency: Literal["SGD"] = "SGD"
    gross_amount_sgd: Decimal = Field(ge=0)
    discount_per_litre_sgd: Decimal = Field(ge=0)
    # Money only. It never reduces the litres, which is the trap on this document.
    discount_amount_sgd: Decimal = Field(ge=0)
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
    def products(self) -> tuple[str, ...]:
        return tuple(sorted(self.litres_by_product))

    @property
    def total_litres_all_products(self) -> Decimal:
        """Every litre on the statement, for a count in a manifest - never for a factor.

        Named at length on purpose. This is a document statistic, not an activity, and
        applying one emission factor to it would be wrong by the difference between the
        products' factors.
        """
        return sum(self.litres_by_product.values(), Decimal(0))

    @model_validator(mode="after")
    def _arithmetic_closes(self) -> "FuelCardStatement":
        if self.period_start > self.period_end:
            raise ValueError(
                f"period_start {self.period_start} is after period_end {self.period_end}"
            )
        if self.document_date < self.period_end:
            raise ValueError(
                f"document_date {self.document_date} precedes period_end {self.period_end}: "
                "a statement cannot be issued before the period it covers has ended"
            )

        outside = [
            t.date
            for t in self.transactions
            if not (self.period_start <= t.date <= self.period_end)
        ]
        if outside:
            raise ValueError(
                f"{len(outside)} transaction(s) fall outside the statement period "
                f"{self.period_start}..{self.period_end}: {outside[:3]}"
            )

        # The activity relation: litres per product, from the transactions, per product.
        derived: dict[str, Decimal] = {}
        for transaction in self.transactions:
            derived[transaction.product] = (
                derived.get(transaction.product, Decimal(0)) + transaction.litres
            )
        if derived != dict(self.litres_by_product):
            raise ValueError(
                f"litres_by_product {dict(self.litres_by_product)} != "
                f"sum of transaction litres per product {derived}"
            )

        gross = sum((t.amount_sgd for t in self.transactions), Decimal(0))
        if gross != self.gross_amount_sgd:
            raise ValueError(
                f"gross_amount_sgd {self.gross_amount_sgd} != sum(transaction amounts) = {gross}"
            )

        # The discount is per litre across every product - and it touches money only.
        discount = round2(self.total_litres_all_products * self.discount_per_litre_sgd)
        if discount != self.discount_amount_sgd:
            raise ValueError(
                f"discount_amount_sgd {self.discount_amount_sgd} != "
                f"round2({self.total_litres_all_products} x "
                f"{self.discount_per_litre_sgd}) = {discount}"
            )

        # A reversed prior-statement transaction is a credit: money only, and never this
        # statement's litres.
        subtotal = self.gross_amount_sgd - self.discount_amount_sgd - self.credit_amount_sgd
        if subtotal != self.subtotal_sgd:
            raise ValueError(
                f"subtotal_sgd {self.subtotal_sgd} != {self.gross_amount_sgd} - "
                f"{self.discount_amount_sgd} - credit {self.credit_amount_sgd} = {subtotal}"
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
            self.quirks, "mixed_fuel", len(self.litres_by_product) > 1, "more than one product"
        )
        return self


__all__ = ["FuelCardStatement", "FuelTransaction"]
