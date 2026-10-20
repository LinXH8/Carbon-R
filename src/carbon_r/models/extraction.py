"""What a vision model read off an electricity bill, and the Python that makes sense of it.

`ElectricityBill` is an ANSWER KEY: frozen, `extra="forbid"`, and guarded by a validator
that re-derives every arithmetic relation. It is deliberately not reused as the extraction
target, for two reasons:

1. Its validator would reject a bill where the model misread one figure, collapsing a
   25-fields-right result into a total failure - and destroying the per-field accuracy
   that makes an eval diagnostic rather than merely discouraging.
2. `generator_seed`, `layout_id`, `difficulty_tier` and `degradation` are not on the page
   and cannot be read off it.

So extraction has its own model, and the shape of it is CLAUDE.md rule 1 expressed as
types:

    ExtractedElectricityBill   every printed field as `str | None`, verbatim
           |                   (no Decimal, no date, no validator, no totals requested)
           |  normalise()      <- plain Python: separators, dates, MWh->kWh, Decimal
           v
    NormalisedBill  +  list[FieldProblem]
           |
           |  activities_for() <- what there is to apply a factor to
           v
    list[Activity]

The model transcribes; Python converts. A field the model could only produce by
multiplying is never asked for, so there is no prompt that invites it to do arithmetic.
This also mirrors the spec's `printed` vs `truth` split (SYNTHGEN_SPEC.md 4.1, 6.1): a bad
read and a bad interpretation fail separately and are reported separately.

The parsing primitives live in `parsing.py`, shared with the LPG and fuel-card kinds.
`normalise` keeps its two-value return - `(bill, problems)` - and the activity list is a
separate step, because what was READ and what can be CALCULATED are different questions
and a caller often wants only the first.
"""

import re
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from carbon_r.models.activity import Activity
from carbon_r.models.parsing import (
    DATE_FORMATS,
    ConsistencyFlag,
    FieldProblem,
    comparer,
    parse_date,
    parse_decimal,
    parse_reading_basis,
    taker,
    text_of,
    without_inline_label,
)
from carbon_r.money import round2
from carbon_r.prorate import days_by_year, split_by_year

# Multipliers onto kWh. Extraction never converts; this is the conversion, in Python,
# with the factor written down.
CONSUMPTION_UNITS = {
    "kwh": 1,
    "mwh": 1000,
    "gwh": 1_000_000,
}


class ExtractedElectricityBill(BaseModel):
    """What a vision model read off one electricity bill. Printed strings only.

    Every field is `str | None` with no default: required-and-nullable, which is what
    OpenAI strict structured outputs accepts, and which lets `null` mean "not printed on
    this page" rather than "the model forgot".
    """

    model_config = ConfigDict(extra="forbid")

    vendor_name: str | None = Field(description="Retailer name as printed")
    vendor_uen: str | None = Field(description="Vendor UEN / registration number as printed")
    customer_name: str | None = Field(description="Account holder name as printed")
    customer_address: str | None = Field(description="Supply address as printed, one line")
    account_number: str | None = Field(description="Account number as printed")

    document_date: str | None = Field(description="Bill/invoice date, in the page's format")
    period_start: str | None = Field(description="Start of the billing period, page's format")
    period_end: str | None = Field(description="End of the billing period, page's format")

    meter_number: str | None = Field(description="Meter serial as printed")
    previous_reading: str | None = Field(description="Previous meter reading, with separators")
    current_reading: str | None = Field(description="Current meter reading, with separators")
    consumption: str | None = Field(description="Units consumed, with separators, unconverted")
    consumption_unit: str | None = Field(description="Unit beside consumption, e.g. kWh or MWh")
    reading_basis: str | None = Field(description="'Actual' or 'Estimated' as printed, else null")

    tariff_plan_code: str | None = Field(description="Plan code as printed")
    tariff_plan_name: str | None = Field(description="Plan name as printed")
    tension: str | None = Field(description="Tension / voltage category as printed")

    currency: str | None = Field(description="Currency code or symbol as printed")
    tariff_cents_per_kwh: str | None = Field(description="Energy rate as printed, no conversion")
    energy_charge: str | None = Field(description="Energy charge line amount as printed")
    fixed_charge: str | None = Field(description="Fixed/service charge line amount as printed")
    credit_adjustment: str | None = Field(
        description=(
            "A credit or adjustment line for an earlier period, as printed (its money "
            "amount only), or null if the page has none"
        )
    )
    subtotal: str | None = Field(description="Subtotal before GST as printed")
    gst_percent: str | None = Field(description="GST percentage as printed, e.g. '9'")
    gst_amount: str | None = Field(description="GST amount as printed")
    total: str | None = Field(description="Total payable as printed")

    notes: str | None = Field(description="Anything ambiguous or unreadable; null if nothing")

    def is_empty(self) -> bool:
        """True when the page yielded nothing usable - `notes` alone is not a reading."""
        return all(
            value is None or not str(value).strip()
            for name, value in self.model_dump().items()
            if name != "notes"
        )


class NormalisedBill(BaseModel):
    """The typed counterpart of a reading. No arithmetic validator, by design.

    Field names deliberately match `ElectricityBill`'s, so the eval compares like with
    like without a translation table in the middle.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    vendor_name: str | None = None
    vendor_uen: str | None = None
    customer_name: str | None = None
    customer_address: str | None = None
    account_number: str | None = None

    document_date: date | None = None
    period_start: date | None = None
    period_end: date | None = None

    meter_number: str | None = None
    previous_reading_kwh: int | None = None
    current_reading_kwh: int | None = None
    consumption_kwh: int | None = None
    is_estimated_reading: bool | None = None

    tariff_plan_code: str | None = None
    tariff_plan_name: str | None = None
    tension: str | None = None

    currency: str | None = None
    tariff_cents_per_kwh: Decimal | None = None
    energy_charge_sgd: Decimal | None = None
    fixed_charge_sgd: Decimal | None = None
    # A credit for an earlier period, stored as a magnitude (the page prints a deduction).
    credit_adjustment_sgd: Decimal | None = None
    subtotal_sgd: Decimal | None = None
    gst_rate: Decimal | None = None
    gst_amount_sgd: Decimal | None = None
    total_sgd: Decimal | None = None

    @property
    def factor_year(self) -> int | None:
        """The year to look a factor up for: the activity period, never document_date."""
        return self.period_start.year if self.period_start else None


# --- parsing specific to this kind ------------------------------------------------------

# A tariff plan code as the layouts print them: upper-case alphanumerics in hyphenated
# groups, e.g. SPR-HT-FIX-24. Deliberately narrow, so an ordinary parenthetical such as
# "(promotional)" or "(see overleaf)" is never mistaken for a code.
_PLAN_CODE = re.compile(r"^[A-Z0-9]+(?:-[A-Z0-9]+)+$")


# Field labels that layouts run together with the value in a single text node, rather
# than separating them structurally. Layout `elec_a` prints "UEN SYN-0000-0001 · <address>"
# on one line, so a faithful transcription of it begins with the label. Stripping the label
# is reading the page correctly, not correcting the model.
_INLINE_LABELS = {
    "vendor_uen": ("uen",),
    "account_number": ("account no", "account number", "account"),
    "meter_number": ("meter no", "meter number", "meter"),
}


def _without_inline_label(printed: str, field: str) -> str:
    """Drop a leading field label the layout did not separate from its value."""
    return without_inline_label(printed, _INLINE_LABELS.get(field, ()))


def _split_plan_line(printed: str) -> tuple[str, str | None]:
    """Separate "Industrial Fixed 24 (SPR-HT-FIX-24)" into its name and its code.

    Layout `elec_a` prints ONE "Tariff plan" line as `{name} ({code})`, so a faithful
    transcription of that label contains both answer-key fields. The page conflates them;
    the key is right to hold them apart (rule 4 - the key is not touched). So Python
    separates them, here, deterministically.

    This does not depend on the model having also filled `tariff_plan_code`. It initially
    did, and the eval caught the consequence: on a run where the model put the whole line
    in the name and left the code null, the name kept its "(...)" and BOTH fields scored
    wrong - from one printed string that was read correctly. Recovering both from the line
    itself is the honest reading of what the page prints.

    Returns `(name, code_or_None)`. The code is only ever offered as a FALLBACK; a code
    the model read directly from its own field wins.
    """
    text = printed.strip()
    if not (text.endswith(")") and "(" in text):
        return text, None

    head, _, tail = text.rpartition("(")
    candidate = tail[:-1].strip()
    if _PLAN_CODE.match(candidate):
        return head.strip(), candidate
    return text, None


def normalise(
    extracted: ExtractedElectricityBill,
) -> tuple[NormalisedBill | None, list[FieldProblem]]:
    """Turn printed strings into typed values. Plain Python; no model involved.

    Returns the normalised bill and every field that could not be parsed. A problem is
    data, not an exception: the eval needs to know that 24 of 25 fields were read, and
    which one was not.

    Returns `(None, problems)` only when the reading was empty to begin with.
    """
    if extracted.is_empty():
        return None, [
            FieldProblem(
                field="(document)",
                printed=None,
                reason="No field was read from the page",
            )
        ]

    problems: list[FieldProblem] = []
    values: dict[str, object] = {}
    take = taker(extracted, values, problems)

    for field in ("vendor_name", "customer_name", "customer_address", "tension", "currency"):
        take(field, field, text_of)

    # Identifier fields, where the layout may print the label in the same text run.
    for field in ("vendor_uen", "account_number", "meter_number", "tariff_plan_code"):
        take(field, field, lambda printed, f=field: _without_inline_label(printed, f))

    # Not in the loop above: the layout prints the plan name with its code appended, so
    # one printed line carries two key fields. See _split_plan_line.
    if extracted.tariff_plan_name and extracted.tariff_plan_name.strip():
        name, code_from_name = _split_plan_line(extracted.tariff_plan_name)
        values["tariff_plan_name"] = name
        if code_from_name and "tariff_plan_code" not in values:
            values["tariff_plan_code"] = code_from_name

    for field in ("document_date", "period_start", "period_end"):
        take(field, field, parse_date)

    # Readings are in the bill's unit: an MWh bill prints "285.727 MWh" on its register
    # lines too. They go through the same conversion as consumption (Block 3, B3: before
    # this, readings 0 of 24 on MWh bills against 58 of 58 on kWh bills).
    bill_unit = extracted.consumption_unit
    for target, source in (
        ("previous_reading_kwh", "previous_reading"),
        ("current_reading_kwh", "current_reading"),
    ):
        take(target, source, lambda printed: energy_kwh(printed, bill_unit))
    take("is_estimated_reading", "reading_basis", parse_reading_basis)

    for target, source in (
        ("tariff_cents_per_kwh", "tariff_cents_per_kwh"),
        ("energy_charge_sgd", "energy_charge"),
        ("fixed_charge_sgd", "fixed_charge"),
        ("subtotal_sgd", "subtotal"),
        ("gst_amount_sgd", "gst_amount"),
        ("total_sgd", "total"),
    ):
        take(target, source, parse_decimal)

    # A credit is printed as a deduction; the key holds its magnitude, and the direction
    # lives in `subtotal = energy + fixed - credit` (the convention normalise_fuel set).
    take("credit_adjustment_sgd", "credit_adjustment", lambda printed: abs(parse_decimal(printed)))

    # GST is printed as a percentage and stored as a fraction, matching the answer key.
    take("gst_rate", "gst_percent", lambda printed: parse_decimal(printed) / Decimal(100))

    # Consumption last: it is the only field whose value depends on another field.
    _take_consumption(extracted, values, problems)

    return NormalisedBill(**values), problems


def _take_consumption(
    extracted: ExtractedElectricityBill,
    values: dict[str, object],
    problems: list[FieldProblem],
) -> None:
    """kWh from the printed amount and its printed unit. The conversion lives here.

    An unrecognised unit is a problem rather than an assumption: silently treating "MWh"
    as kWh would understate emissions by a factor of 1000 and the figure would still look
    plausible.
    """
    printed = extracted.consumption
    if printed is None or not str(printed).strip():
        return
    try:
        values["consumption_kwh"] = energy_kwh(printed, extracted.consumption_unit)
    except (ValueError, ArithmeticError) as exc:
        shown = (
            printed if not extracted.consumption_unit else f"{printed} {extracted.consumption_unit}"
        )
        problems.append(FieldProblem(field="consumption_kwh", printed=shown, reason=str(exc)))


# An energy unit printed at the END of the figure itself. `clean` strips these silently, so
# the unit is read here first: "285 MWh" must never reach a parser that sees only "285".
_INLINE_ENERGY_UNIT = re.compile(r"(kwh|mwh|gwh)\s*$", re.IGNORECASE)


def energy_kwh(printed: str, unit_printed: str | None) -> int:
    """Whole kWh from a printed energy figure. The conversion lives here, and only here.

    The unit is the one printed WITH the figure if there is one, else the bill's printed
    unit, else kWh. Two printed units that disagree are a problem, not a choice: picking
    either would be a guess about a factor of 1000.

    An unrecognised unit is a problem rather than an assumption: silently treating "MWh"
    as kWh would understate emissions by a factor of 1000 and the figure would still look
    plausible. That was possible before Block 3 - `clean` strips "MWh" as noise, so a
    whole-number "285 MWh" with no separate unit read parsed as 285 kWh.
    """
    inline = _INLINE_ENERGY_UNIT.search(printed.strip())
    stated = (unit_printed or "").strip().lower() or None
    if inline and stated and inline.group(1).lower() != stated:
        raise ValueError(
            f"{printed!r} is printed in {inline.group(1)}, but the bill's unit is {unit_printed!r}"
        )
    unit = inline.group(1).lower() if inline else (stated or "kwh")
    multiplier = CONSUMPTION_UNITS.get(unit)
    if multiplier is None:
        raise ValueError(
            f"Unrecognised energy unit {unit_printed!r}; known units: "
            f"{', '.join(sorted(CONSUMPTION_UNITS))}"
        )
    kwh = parse_decimal(printed) * multiplier
    if kwh != kwh.to_integral_value():
        raise ValueError(f"{printed!r} is {kwh} kWh, not a whole number of units")
    return int(kwh)


def activities_for(bill: NormalisedBill) -> list[Activity]:
    """The electricity on a bill: one activity per calendar year its period touches.

    Returns a list for the same reason every kind does: `calculate_emissions` takes lines,
    and a single-activity document is one line rather than a separate shape. An empty list
    is the honest answer when the consumption could not be read - the caller reports a gap
    rather than calculating from a guess.

    A period straddling 31 December (`cross_year_period`) is split by days with
    `prorate.split_by_year`, because the two years take different grid factors. Each part
    carries its own year and its own slice of the period, and the division is written into
    `derivation` - the page never prints the split, so the step has to be checkable here.
    Without a readable period there is nothing to split by, so the bill stays one activity
    with no year, and the factor lookup has to say so.
    """
    if bill.consumption_kwh is None:
        return []

    basis = {
        "kind": "electricity",
        "unit": "kWh",
        "source_field": "consumption",
        "is_estimated": bool(bill.is_estimated_reading),
    }
    start, end = bill.period_start, bill.period_end
    if start is None or end is None or start > end or start.year == end.year:
        return [
            Activity(
                amount=Decimal(bill.consumption_kwh),
                factor_year=bill.factor_year,
                period_start=start,
                period_end=end,
                **basis,
            )
        ]

    days = days_by_year(start, end)
    total_days = sum(days.values())
    shares = split_by_year(bill.consumption_kwh, start, end)
    last_year = max(shares)
    activities = []
    for year, share in sorted(shares.items()):
        rounding = (
            "the remainder, so the parts sum to the whole"
            if year == last_year
            else "rounded half up to a whole kWh"
        )
        activities.append(
            Activity(
                amount=Decimal(share),
                factor_year=year,
                period_start=max(start, date(year, 1, 1)),
                period_end=min(end, date(year, 12, 31)),
                derivation=(
                    f"{bill.consumption_kwh:,} kWh x {days[year]} of {total_days} days "
                    f"in {year} = {share:,} kWh ({rounding})"
                ),
                **basis,
            )
        )
    return activities


def check_consistency(bill: NormalisedBill) -> list[ConsistencyFlag]:
    """Re-derive the page's own arithmetic from the page's own figures.

    Uses the shared `money.round2`; this module defines no rounding of its own (spec 1.2).
    """
    flags: list[ConsistencyFlag] = []
    compare = comparer(flags)

    if bill.consumption_kwh is not None and bill.tariff_cents_per_kwh is not None:
        compare(
            "energy_charge = round2(consumption_kwh x tariff_cents_per_kwh / 100)",
            round2(Decimal(bill.consumption_kwh) * bill.tariff_cents_per_kwh / Decimal(100)),
            bill.energy_charge_sgd,
        )

    if bill.energy_charge_sgd is not None and bill.fixed_charge_sgd is not None:
        credit = bill.credit_adjustment_sgd or Decimal(0)
        compare(
            "subtotal = energy_charge + fixed_charge - credit_adjustment",
            bill.energy_charge_sgd + bill.fixed_charge_sgd - credit,
            bill.subtotal_sgd,
        )

    if bill.subtotal_sgd is not None and bill.gst_rate is not None:
        compare(
            "gst_amount = round2(subtotal x gst_rate)",
            round2(bill.subtotal_sgd * bill.gst_rate),
            bill.gst_amount_sgd,
        )

    if bill.subtotal_sgd is not None and bill.gst_amount_sgd is not None:
        compare(
            "total = subtotal + gst_amount",
            bill.subtotal_sgd + bill.gst_amount_sgd,
            bill.total_sgd,
        )

    if bill.previous_reading_kwh is not None and bill.current_reading_kwh is not None:
        compare(
            "consumption_kwh = current_reading - previous_reading",
            Decimal(bill.current_reading_kwh - bill.previous_reading_kwh),
            Decimal(bill.consumption_kwh) if bill.consumption_kwh is not None else None,
        )

    return flags


__all__ = [
    "CONSUMPTION_UNITS",
    "DATE_FORMATS",
    "ConsistencyFlag",
    "ExtractedElectricityBill",
    "FieldProblem",
    "NormalisedBill",
    "activities_for",
    "check_consistency",
    "normalise",
]
