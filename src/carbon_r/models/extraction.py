"""What a vision model read off a document, and the Python that makes sense of it.

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

The model transcribes; Python converts. A field the model could only produce by
multiplying is never asked for, so there is no prompt that invites it to do arithmetic.
This also mirrors the spec's `printed` vs `truth` split (SYNTHGEN_SPEC.md 4.1, 6.1): a bad
read and a bad interpretation fail separately and are reported separately.

One unparseable field yields one `FieldProblem`, never an exception. One miss must cost
one field, not the document.
"""

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, ConfigDict, Field

from carbon_r.money import round2

# Date formats the layouts use, in the order they are tried. Spec 6.1 lists
# `04 Dec 2025`, `04/12/2025` and `2025-12-04` as the formats layouts vary between.
#
# `%d/%m/%Y` before `%m/%d/%Y`, and the American form is not tried at all: these are
# Singapore documents, where 04/12/2025 is 4 December. For days 1-12 the two readings are
# both syntactically valid and no amount of parsing can tell them apart, so the convention
# is fixed here, documented, and recorded as an assumption rather than guessed per
# document. A layout wanting unambiguous dates should use `%d %b %Y`.
DATE_FORMATS = (
    "%d %b %Y",  # 04 Dec 2025
    "%d %B %Y",  # 04 December 2025
    "%Y-%m-%d",  # 2025-12-04
    "%d/%m/%Y",  # 04/12/2025  (day first: Singapore convention)
    "%d-%m-%Y",  # 04-12-2025
    "%d %b %y",  # 04 Dec 25
)

# Multipliers onto kWh. Extraction never converts; this is the conversion, in Python,
# with the factor written down.
CONSUMPTION_UNITS = {
    "kwh": 1,
    "mwh": 1000,
    "gwh": 1_000_000,
}

_CURRENCY_NOISE = ("s$", "sgd", "$", "usd", "us$")

# Unit tokens the layouts print INLINE with the figure - "139,600 kWh", "27.65 c/kWh".
# Stripped from an allowlist rather than by grabbing the leading number with a regex: a
# regex would also silently swallow "1,200 (estimated)" or "4,016 kWh credit", turning a
# misread or a quirk into a clean-looking value. An unrecognised suffix still raises, and
# so still surfaces as a FieldProblem.
_UNIT_SUFFIXES = (
    "cents/kwh",
    "c/kwh",
    "\u00a2/kwh",
    "kwh",
    "mwh",
    "gwh",
    "units",
    "unit",
    "sgd",
    "kg",
    "%",
)


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
    subtotal_sgd: Decimal | None = None
    gst_rate: Decimal | None = None
    gst_amount_sgd: Decimal | None = None
    total_sgd: Decimal | None = None

    @property
    def factor_year(self) -> int | None:
        """The year to look a factor up for: the activity period, never document_date."""
        return self.period_start.year if self.period_start else None


class FieldProblem(BaseModel):
    """One field that could not be turned into a value. Not fatal; reported."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    field: str
    printed: str | None
    reason: str


class ConsistencyFlag(BaseModel):
    """A printed figure that does not agree with the other printed figures.

    This says the PAGE disagrees with itself as read - which usually means a misread
    field, occasionally a genuinely odd document. Never an error: the tool reports it and
    the agent raises it with the user (rule 7).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    relation: str
    expected: Decimal
    printed: Decimal
    difference: Decimal


# --- parsing helpers: the only place a printed string becomes a value -------------------


def _clean(printed: str) -> str:
    """Strip separators, a currency prefix and any inline unit, leaving the number."""
    text = printed.strip().replace(",", "").replace("\u00a0", " ")

    lowered = text.lower()
    for token in _CURRENCY_NOISE:
        if lowered.startswith(token):
            text = text[len(token) :].strip()
            lowered = text.lower()

    # Longest suffix first, so "c/kwh" is not shortened to "kwh" and left with a "c".
    for suffix in sorted(_UNIT_SUFFIXES, key=len, reverse=True):
        if lowered.endswith(suffix):
            text = text[: -len(suffix)].strip()
            break

    return text.strip()


def _parse_int(printed: str) -> int:
    value = Decimal(_clean(printed))  # via Decimal so "139600.00" is accepted
    if value != value.to_integral_value():
        raise ValueError(f"{printed!r} is not a whole number of units")
    return int(value)


def _parse_decimal(printed: str) -> Decimal:
    return Decimal(_clean(printed))


def _parse_date(printed: str) -> date:
    text = printed.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"{printed!r} matches none of {len(DATE_FORMATS)} known date formats")


def _parse_reading_basis(printed: str) -> bool:
    text = printed.strip().lower()
    if text.startswith("est"):
        return True
    if text.startswith("act"):
        return False
    raise ValueError(f"{printed!r} is neither 'Actual' nor 'Estimated'")


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


# A label must be followed by one of these before it counts as a label. Hyphen is NOT
# included: "UEN-123456" is more plausibly a value than a labelled one, and treating the
# hyphen as a separator would silently truncate it.
_LABEL_SEPARATORS = " :\u00b7\t\u2013\u2014"


def _without_inline_label(printed: str, field: str) -> str:
    """Drop a leading field label the layout did not separate from its value.

    The label must be followed by a separator, or it is not a label: without that check
    "UENITED-1234" becomes "ITED-1234" - a mangled value presented with confidence. That
    was caught by a test written alongside this function, which is the only reason the
    narrower rule is here.
    """
    text = printed.strip()
    for label in _INLINE_LABELS.get(field, ()):
        lowered = text.lower()
        if not lowered.startswith(label):
            continue
        rest = text[len(label) :]
        if not rest or rest[0] not in _LABEL_SEPARATORS:
            continue
        remainder = rest.lstrip(_LABEL_SEPARATORS)
        # Only when something is actually left: "UEN" alone is not a UEN, and returning
        # "" would turn a bad read into a confidently empty field.
        if remainder:
            return remainder
    return text


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

    def take(target: str, source: str, parser) -> None:
        printed = getattr(extracted, source)
        if printed is None or not str(printed).strip():
            return
        try:
            values[target] = parser(printed)
        except (ValueError, ArithmeticError, InvalidOperation) as exc:
            problems.append(FieldProblem(field=target, printed=printed, reason=str(exc)))

    text = lambda printed: str(printed).strip()  # noqa: E731 - a parser, like the others

    for field in ("vendor_name", "customer_name", "customer_address", "tension", "currency"):
        take(field, field, text)

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
        take(field, field, _parse_date)

    take("previous_reading_kwh", "previous_reading", _parse_int)
    take("current_reading_kwh", "current_reading", _parse_int)
    take("is_estimated_reading", "reading_basis", _parse_reading_basis)

    for target, source in (
        ("tariff_cents_per_kwh", "tariff_cents_per_kwh"),
        ("energy_charge_sgd", "energy_charge"),
        ("fixed_charge_sgd", "fixed_charge"),
        ("subtotal_sgd", "subtotal"),
        ("gst_amount_sgd", "gst_amount"),
        ("total_sgd", "total"),
    ):
        take(target, source, _parse_decimal)

    # GST is printed as a percentage and stored as a fraction, matching the answer key.
    take("gst_rate", "gst_percent", lambda printed: _parse_decimal(printed) / Decimal(100))

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

    unit = (extracted.consumption_unit or "kWh").strip().lower()
    multiplier = CONSUMPTION_UNITS.get(unit)
    if multiplier is None:
        problems.append(
            FieldProblem(
                field="consumption_kwh",
                printed=f"{printed} {extracted.consumption_unit}",
                reason=(
                    f"Unrecognised consumption unit {extracted.consumption_unit!r}; "
                    f"known units: {', '.join(sorted(CONSUMPTION_UNITS))}"
                ),
            )
        )
        return

    try:
        amount = _parse_decimal(printed)
    except (ValueError, ArithmeticError, InvalidOperation) as exc:
        problems.append(FieldProblem(field="consumption_kwh", printed=printed, reason=str(exc)))
        return

    kwh = amount * multiplier
    if kwh != kwh.to_integral_value():
        problems.append(
            FieldProblem(
                field="consumption_kwh",
                printed=f"{printed} {unit}",
                reason=f"{kwh} kWh is not a whole number of units",
            )
        )
        return
    values["consumption_kwh"] = int(kwh)


def check_consistency(bill: NormalisedBill) -> list[ConsistencyFlag]:
    """Re-derive the page's own arithmetic from the page's own figures.

    Each relation is checked from PRINTED inputs rather than from previously derived ones,
    so one misread field raises one flag instead of cascading through all four. A relation
    whose inputs were not read is skipped - absence is reported as a `FieldProblem`, not
    as a disagreement.

    Uses the shared `money.round2`; this module defines no rounding of its own (spec 1.2).
    """
    flags: list[ConsistencyFlag] = []

    def compare(relation: str, expected: Decimal, printed: Decimal | None) -> None:
        if printed is None:
            return
        if expected != printed:
            flags.append(
                ConsistencyFlag(
                    relation=relation,
                    expected=expected,
                    printed=printed,
                    difference=printed - expected,
                )
            )

    if bill.consumption_kwh is not None and bill.tariff_cents_per_kwh is not None:
        compare(
            "energy_charge = round2(consumption_kwh x tariff_cents_per_kwh / 100)",
            round2(Decimal(bill.consumption_kwh) * bill.tariff_cents_per_kwh / Decimal(100)),
            bill.energy_charge_sgd,
        )

    if bill.energy_charge_sgd is not None and bill.fixed_charge_sgd is not None:
        compare(
            "subtotal = energy_charge + fixed_charge",
            bill.energy_charge_sgd + bill.fixed_charge_sgd,
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
