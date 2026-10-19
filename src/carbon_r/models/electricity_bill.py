"""`ElectricityBill` — the ground-truth model for a synthetic electricity bill.

This is an **answer key**, not an extraction result. Per CLAUDE.md rule 4, instances are
produced by `synthgen` alongside the document, never by reading a document with a model.

Every arithmetic relation on the bill is re-derived by the model validator, so an
internally inconsistent key cannot be constructed. That matters because the key is the
arbiter of truth for every eval downstream: a wrong key sends you debugging a prompt that
was fine.

Factor-year rule, for Block 3's `lookup_emission_factor`:

    The emission-factor year comes from the BILLING PERIOD, never from `bill_date`.

`doc_009` in the v0 set is the document that distinguishes them — its period is
2024-12-01..2024-12-31 but its `bill_date` is 2025-01-04. Both readings happen to return
0.402 kg CO2/kWh, which is exactly why it is a trap: the number looks right while the
stated citation ("substituted 2024 for 2025") is wrong.
"""

import calendar
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from carbon_r.models.quirks import QuirkFields, check_quirk_fields, require_quirk
from carbon_r.money import round2
from carbon_r.prorate import split_by_year


class ElectricityBill(QuirkFields):
    """One monthly commercial electricity bill, with its own derivation attached."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # --- identity (spec 3.3: common to every document kind) -------------------------
    # "2" since Tier 2 Block 2: the quirk fields, the printed unit and the by-year split.
    schema_version: Literal["2"] = "2"
    doc_id: str = Field(pattern=r"^elec_\d{3}$")
    doc_kind: Literal["electricity_bill"] = "electricity_bill"
    generator_seed: int
    generator_version: str

    # --- provenance of the rendering (spec 3.3, 1.8) --------------------------------
    layout_id: str = Field(min_length=1)
    is_holdout_layout: bool = False
    difficulty_tier: Literal["clean", "moderate", "hard"] = "clean"
    # Applied degradation effects and their parameters; {} for a clean render.
    degradation: dict = Field(default_factory=dict)

    # --- parties ------------------------------------------------------------------
    vendor_name: str = Field(min_length=1)
    vendor_uen: str = Field(min_length=1)
    customer_name: str = Field(min_length=1)
    customer_address: str = Field(min_length=1)
    account_number: str = Field(min_length=1)

    # --- dates --------------------------------------------------------------------
    # The factor year comes from period_start/period_end, NEVER from document_date.
    # See the module docstring; `elec_009` is the document where the two disagree.
    document_date: date
    period_start: date
    period_end: date

    # --- meter --------------------------------------------------------------------
    meter_number: str = Field(min_length=1)
    previous_reading_kwh: int = Field(ge=0)
    current_reading_kwh: int = Field(ge=0)
    consumption_kwh: int = Field(gt=0)
    is_estimated_reading: bool = False
    # The unit the page prints readings and consumption in (`unit_mwh`). The truth above
    # is always kWh; MWh is printed to 3 dp, so any whole kWh figure is exact on the page.
    consumption_unit_printed: Literal["kWh", "MWh"] = "kWh"
    # consumption_kwh divided between calendar years by days (`prorate.split_by_year`).
    # One entry for a period inside one year; two for `cross_year_period`, where the two
    # years take different grid factors. None only on a hand-built key.
    split_kwh_by_year: dict[int, int] | None = None

    # --- tariff plan --------------------------------------------------------------
    # Flattened rather than nested: this is how they appear on the page, and how
    # extraction will have to recover them.
    tariff_plan_code: str = Field(min_length=1)
    tariff_plan_name: str = Field(min_length=1)
    tension: str = Field(min_length=1)

    # --- money --------------------------------------------------------------------
    currency: Literal["SGD"] = "SGD"
    tariff_cents_per_kwh: Decimal = Field(gt=0)
    energy_charge_sgd: Decimal = Field(ge=0)
    fixed_charge_sgd: Decimal = Field(ge=0)
    subtotal_sgd: Decimal = Field(ge=0)
    gst_rate: Decimal = Field(ge=0)
    gst_amount_sgd: Decimal = Field(ge=0)
    total_sgd: Decimal = Field(ge=0)

    # `quirks` and the shared credit / balance / GST-display fields come from QuirkFields.

    @property
    def days_in_period(self) -> int:
        return (self.period_end - self.period_start).days + 1

    @property
    def is_calendar_month(self) -> bool:
        """True when the period is exactly one calendar month, 1st to last day."""
        last = calendar.monthrange(self.period_start.year, self.period_start.month)[1]
        return self.period_start.day == 1 and self.period_end == self.period_start.replace(day=last)

    @property
    def factor_year(self) -> int:
        """Year to look an emission factor up for: from the period, never document_date."""
        return self.period_start.year

    @model_validator(mode="after")
    def _arithmetic_closes(self) -> "ElectricityBill":
        if self.period_start > self.period_end:
            raise ValueError(
                f"period_start {self.period_start} is after period_end {self.period_end}"
            )
        if self.document_date < self.period_end:
            raise ValueError(
                f"document_date {self.document_date} precedes period_end {self.period_end}: "
                "a bill cannot be issued before the period it bills for has ended"
            )

        metered = self.current_reading_kwh - self.previous_reading_kwh
        if metered != self.consumption_kwh:
            raise ValueError(
                f"consumption_kwh {self.consumption_kwh} != "
                f"{self.current_reading_kwh} - {self.previous_reading_kwh} = {metered}"
            )

        # Same order, same helper, as synthgen.electricity computes them.
        energy = round2(Decimal(self.consumption_kwh) * self.tariff_cents_per_kwh / Decimal(100))
        if energy != self.energy_charge_sgd:
            raise ValueError(
                f"energy_charge_sgd {self.energy_charge_sgd} != "
                f"round2({self.consumption_kwh} x {self.tariff_cents_per_kwh} / 100) = {energy}"
            )

        # A credit for a prior period reduces this period's money, never its kWh.
        subtotal = self.energy_charge_sgd + self.fixed_charge_sgd - self.credit_amount_sgd
        if subtotal != self.subtotal_sgd:
            raise ValueError(
                f"subtotal_sgd {self.subtotal_sgd} != {self.energy_charge_sgd} + "
                f"{self.fixed_charge_sgd} - credit {self.credit_amount_sgd} = {subtotal}"
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

        self._quirks_agree()
        return self

    def _quirks_agree(self) -> None:
        """Every electricity quirk against the fields it changes, in both directions."""
        check_quirk_fields(self, self.doc_kind, self.total_sgd)
        names = self.quirks

        if self.split_kwh_by_year is not None:
            expected = split_by_year(self.consumption_kwh, self.period_start, self.period_end)
            if self.split_kwh_by_year != expected:
                raise ValueError(
                    f"split_kwh_by_year {self.split_kwh_by_year} != split by days {expected}"
                )
        crosses_year = self.period_start.year != self.period_end.year
        if crosses_year and self.split_kwh_by_year is None:
            raise ValueError("a period across 31 December needs split_kwh_by_year")

        require_quirk(names, "cross_year_period", crosses_year, "the period crossing a year")
        require_quirk(
            names,
            "cross_month_period",
            not crosses_year and not self.is_calendar_month,
            "a period that is not one calendar month",
        )
        require_quirk(names, "estimated_reading", self.is_estimated_reading, "is_estimated")
        require_quirk(names, "unit_mwh", self.consumption_unit_printed == "MWh", "printing in MWh")
