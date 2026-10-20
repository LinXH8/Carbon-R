"""Turning printed strings into values: the primitives every document kind shares.

Extracted from `extraction.py` when Tier 2 Block 1 added a second and third document kind.
Nothing here knows what an electricity bill or an LPG invoice is; it knows how a Singapore
commercial document writes a date, a number, a currency amount and an inline unit, which is
the same across all three.

The discipline these exist to enforce is the one in `extraction.py`'s docstring: the model
transcribes, Python converts. A field the model could only produce by multiplying is never
asked for, and every conversion is here where it can be unit-tested without a model call.

One unparseable field yields one `FieldProblem`, never an exception. One miss must cost one
field, not the document.
"""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, ConfigDict

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

_CURRENCY_NOISE = ("s$", "sgd", "$", "usd", "us$")

# Characters a layout may use for a minus sign. HTML's `&minus;` renders as U+2212, and a
# PDF text layer may hand back U+2013 for the same glyph - the eval found a discount read
# as "−15.46" in one document and "–15.76" in another, from one template. Treating only
# ASCII "-" as a sign lost every discount on every fuel-card statement.
_MINUS_SIGNS = ("-", "−", "–")  # hyphen-minus, minus sign, en dash

# A dash standing alone in a money column means "nothing here", not a number. Layouts use
# it for a row that carries no amount - the returned-cylinders line on an LPG invoice. It
# is NOT a parse failure, so `is_blank_dash` lets callers skip such a row rather than
# recording a problem against it.
_BLANK_DASHES = {"-", "–", "—", "−", "--", "---"}

# Unit tokens the layouts print INLINE with the figure - "139,600 kWh", "27.65 c/kWh",
# "62.41 L". Stripped from an allowlist rather than by grabbing the leading number with a
# regex: a regex would also silently swallow "1,200 (estimated)" or "4,016 kWh credit",
# turning a misread or a quirk into a clean-looking value. An unrecognised suffix still
# raises, and so still surfaces as a FieldProblem.
_UNIT_SUFFIXES = (
    "cents/kwh",
    "c/kwh",
    "¢/kwh",
    "kwh",
    "mwh",
    "gwh",
    "units",
    "unit",
    "sgd",
    "litres",
    "litre",
    "ltr",
    "l",
    "cylinders",
    "cylinder",
    "tonnes",
    "tonne",
    "kg",
    "%",
    # Counts of deliveries on an LPG invoice's charge lines: "1 visit", "2 trip(s)".
    # Block 2's layouts print them; Block 3 B4 found every such row dropped.
    "trip(s)",
    "trips",
    "trip",
    "visits",
    "visit",
)


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


def is_blank_dash(printed: str | None) -> bool:
    """True when a cell holds a dash standing for "no amount" rather than a figure."""
    return printed is not None and printed.strip() in _BLANK_DASHES


def clean(printed: str) -> str:
    """Strip separators, a currency prefix and any inline unit, leaving the number."""
    text = printed.strip().replace(",", "").replace(" ", " ")

    lowered = text.lower()
    for token in _CURRENCY_NOISE:
        if lowered.startswith(token):
            text = text[len(token) :].strip()
            lowered = text.lower()

    # A leading minus may sit on either side of the currency symbol: "-S$12.63" and
    # "S$-12.63" are both written on real statements, and a discount line is the one place
    # a negative number legitimately appears. Any of the three minus glyphs counts.
    negative = text[:1] in _MINUS_SIGNS
    if negative:
        text = text[1:].strip()
        lowered = text.lower()
        for token in _CURRENCY_NOISE:
            if lowered.startswith(token):
                text = text[len(token) :].strip()
                lowered = text.lower()

    # Longest suffix first, so "c/kwh" is not shortened to "kwh" and left with a "c", and
    # "litres" is not shortened to "l".
    for suffix in sorted(_UNIT_SUFFIXES, key=len, reverse=True):
        if lowered.endswith(suffix):
            text = text[: -len(suffix)].strip()
            break

    text = text.strip()
    return f"-{text}" if negative and text else text


def parse_int(printed: str) -> int:
    value = Decimal(clean(printed))  # via Decimal so "139600.00" is accepted
    if value != value.to_integral_value():
        raise ValueError(f"{printed!r} is not a whole number of units")
    return int(value)


def parse_decimal(printed: str) -> Decimal:
    return Decimal(clean(printed))


def parse_date(printed: str) -> date:
    """A date in any of the layouts' formats.

    A trailing time of day is dropped: a fuel-card transaction is stamped
    "03 Mar 2026 07:42", and the time is not a field any answer key holds. Dropping it
    here rather than asking the model to omit it keeps the transcription faithful to the
    page.
    """
    text = printed.strip()
    for candidate in (text, text.rsplit(" ", 1)[0]):
        for fmt in DATE_FORMATS:
            try:
                return datetime.strptime(candidate, fmt).date()
            except ValueError:
                continue
    raise ValueError(f"{printed!r} matches none of {len(DATE_FORMATS)} known date formats")


def parse_reading_basis(printed: str) -> bool:
    text = printed.strip().lower()
    if text.startswith("est"):
        return True
    if text.startswith("act"):
        return False
    raise ValueError(f"{printed!r} is neither 'Actual' nor 'Estimated'")


def text_of(printed: object) -> str:
    return str(printed).strip()


# A label must be followed by one of these before it counts as a label. Hyphen is NOT
# included: "UEN-123456" is more plausibly a value than a labelled one, and treating the
# hyphen as a separator would silently truncate it.
_LABEL_SEPARATORS = " :·\t–—"


def without_inline_label(printed: str, labels: tuple[str, ...]) -> str:
    """Drop a leading field label the layout did not separate from its value.

    Layouts print "UEN SYN-0000-0001 · <address>" as one text run, so a faithful
    transcription begins with the label. Stripping it is reading the page correctly, not
    correcting the model. Shared by all three kinds since Block 3 B6; it lived in the
    electricity module, and LPG and fuel readings kept the "UEN " prefix.

    The label must be followed by a separator, or it is not a label: without that check
    "UENITED-1234" becomes "ITED-1234" - a mangled value presented with confidence. That
    was caught by a test written alongside this function, which is the only reason the
    narrower rule is here.
    """
    text = printed.strip()
    for label in labels:
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


def taker(source: object, values: dict, problems: list[FieldProblem]):
    """Build the `take(target, field, parser)` helper each kind's `normalise` uses.

    A closure rather than a method on a base class: the three kinds share no fields and
    would get nothing else from a shared superclass, while this is the one piece of
    machinery they do share - read a printed field, parse it, and record a problem instead
    of raising if it cannot be parsed.
    """

    def take(target: str, field: str, parser) -> None:
        printed = getattr(source, field, None)
        if printed is None or not str(printed).strip():
            return
        try:
            values[target] = parser(printed)
        except (ValueError, ArithmeticError, InvalidOperation) as exc:
            problems.append(FieldProblem(field=target, printed=str(printed), reason=str(exc)))

    return take


def comparer(flags: list[ConsistencyFlag]):
    """Build the `compare(relation, expected, printed)` helper used by consistency checks.

    Each relation is checked from PRINTED inputs rather than from previously derived ones,
    so one misread field raises one flag instead of cascading. A relation whose inputs were
    not read is skipped - absence is reported as a `FieldProblem`, not as a disagreement.
    """

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

    return compare


__all__ = [
    "DATE_FORMATS",
    "ConsistencyFlag",
    "FieldProblem",
    "clean",
    "comparer",
    "is_blank_dash",
    "parse_date",
    "parse_decimal",
    "parse_int",
    "parse_reading_basis",
    "taker",
    "text_of",
]
