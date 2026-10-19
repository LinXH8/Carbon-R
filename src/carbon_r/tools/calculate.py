"""`calculate_emissions` - the one place a greenhouse-gas figure is computed.

This module is CLAUDE.md rule 1 made concrete. It imports no model provider and no
database: it is plain Python over `Decimal`, it can be read in one sitting, and every
number it returns can be re-derived by hand from the inputs it hands back alongside.

Five properties are load-bearing:

1. **It returns its inputs.** Rule 2: there is no code path here that returns a bare
   float. Every line carries its activity amount, its factor with full provenance, and its
   derivation written out, so a reviewer can check the arithmetic without re-running
   anything.
2. **It refuses incompatible units.** A factor in `kg CO2 / kWh` applied to litres is an
   error, never a silent conversion. Where a conversion IS allowed it is declared in
   `UNIT_CONVERSIONS`, carries a source, and appears in the derivation - see below.
3. **Its disclosures are flags, not prose.** The agent's system prompt turns
   `factor_year_substituted: true` into a sentence. Keeping the wording in one place means
   it cannot drift between tools, and it leaves this function testable by equality rather
   than by string matching.
4. **It is all-or-nothing.** See `Why a bad line fails the whole call`.
5. **It refuses a factor for the wrong year.** An activity carries the year it belongs
   to; a factor looked up for any other year is an error. A bill split across 31
   December is two activities in two years, and this is what stops one year's factor
   being applied to both halves (Tier 2 Block 2).

Many lines, one call
--------------------
Tier 1 computed one figure from one document. A fuel-card statement listing diesel and
petrol has two activities needing two different factor rows, and the answer a user wants
is the total - which is a sum, which the model must not perform. So the tool takes a list
of (activity, factor) pairs and returns both the per-line figures and the total, summed
here in Python.

The total is summed from the **exact products**, never from the rounded lines. Rounding
each line to a milligram and then adding would let the total disagree with its own
addends, which is precisely the kind of discrepancy an auditor would (correctly) stop on.

Why a bad line fails the whole call
-----------------------------------
If any pair is unusable - an unknown handle, a unit that cannot be reconciled, a factor
without provenance - the whole call returns `error` and no figure at all. The alternative,
computing the lines that work and totalling those, produces the single worst output this
module could emit: a number that looks like an answer, is smaller than the truth, and
carries nothing on its face to say a line is missing. Every bad pair is named in the one
error message, so a single turn is enough to correct them all.

Scope, method and gas basis come from the factor row
----------------------------------------------------
They used to be module constants here. `METHOD = "location-based"` is a Scope 2 term, and
it was only ever right because every row in the table was Singapore grid electricity.
Printed beside an LPG figure it is a wrong label on a correct number, so the factor now
carries its own classification and this module reports it.

A result whose lines disagree on gas basis is flagged: adding a CO2e line to a CO2-only
line understates the CO2-only line by its methane and nitrous oxide content, and a reader
cannot see that from the total.

Rounding is not `money.round2`: that helper means "round to cents, as a vendor would", and
an emission figure is not money. The quantisation below is for *presentation only* - the
exact product is always returned too, and `tonnes` is derived from the exact product
rather than from the rounded kilograms, so the two can never disagree by a rounding step.
"""

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from pydantic import ValidationError

from carbon_r.models.activity import Activity
from carbon_r.models.factors import FactorCitation
from carbon_r.tools.base import ToolResult, error, ok

# What a factor's unit implies about the activity it can be applied to. Exact string match
# against the publisher's own unit string - EMA's "kg CO2 / kWh", spaces and all, and
# DESNZ's plural denominators. A factor whose unit is not a key here cannot be used by this
# function at all, which is the intended behaviour for an unrecognised unit: refuse, do not
# improvise.
UNIT_COMPATIBILITY: dict[str, str] = {
    "kg CO2 / kWh": "kWh",
    "kg CO2e / kWh": "kWh",
    "kg CO2e / litres": "litres",
    "kg CO2e / tonnes": "tonnes",
}

# Conversions from an activity's own unit to the unit a factor is published in. Declared,
# not inferred: this table is the complete set of conversions the project performs, each
# with the authority it rests on.
#
# There is exactly one entry, and it exists because DESNZ publishes LPG per tonne and per
# litre but not per kg, while an LPG invoice is written in kg. Rather than quietly loading
# a per-kg factor we do not have, or dividing by 1000 somewhere in the extraction layer,
# the step is named here and printed in the derivation.
#
# It lives in this module rather than in `models.extraction.normalise` because it is a
# property of the FACTOR, not of the page: `normalise` turns what was printed into the
# key's canonical field (MWh -> kWh), whereas this reconciles an activity with a factor
# that is only chosen later.
UNIT_CONVERSIONS: dict[tuple[str, str], dict[str, Any]] = {
    ("kg", "tonnes"): {
        "multiplier": Decimal(1) / Decimal(1000),
        "statement": "1 tonne = 1000 kg",
        "source": (
            "SI definition of the kilogram prefix; reproduced in the DESNZ conversion "
            "factors workbook, sheet 'Conversions', abbreviation table (Kilo = 1000)."
        ),
    },
}

KG_PLACES = Decimal("0.001")  # grams: finer than any reporting threshold cares about
TONNE_PLACES = Decimal("0.000001")


def _trim(value: Decimal) -> Decimal:
    """Drop trailing zeros for display without ever producing exponent notation.

    `Decimal("56119.200").normalize()` is `56119.2`, which is what we want - but
    `Decimal("139600").normalize()` is `1.396E+5`, which is not. Integers are quantised
    back to a plain integer instead.
    """
    if value == value.to_integral_value():
        return value.quantize(Decimal(1))
    return value.normalize()


def _as_activity(value: Activity | dict, index: int) -> tuple[Activity | None, str | None]:
    """Validate one activity. Returns `(activity, None)` or `(None, problem)`."""
    if isinstance(value, Activity):
        return value, None
    try:
        return Activity.model_validate(value), None
    except ValidationError as exc:
        fields = ", ".join(".".join(str(p) for p in e["loc"]) for e in exc.errors())
        return None, f"line {index}: the activity is not usable ({fields})"


def _as_factor(
    value: FactorCitation | dict, index: int
) -> tuple[FactorCitation | None, str | None]:
    """Validate one factor. A factor without provenance is a problem, not a number."""
    if isinstance(value, FactorCitation):
        factor = value
    else:
        try:
            factor = FactorCitation.model_validate(value)
        except ValidationError as exc:
            fields = ", ".join(".".join(str(p) for p in e["loc"]) for e in exc.errors())
            return None, (
                f"line {index}: the emission factor is missing the provenance every figure "
                f"must carry ({fields}). A factor must travel with its publisher, source "
                "document, unit and year before it can be used in a calculation"
            )
    if factor.scope is None or factor.gas_basis is None:
        return None, (
            f"line {index}: the factor for {factor.category} does not record its scope or "
            "gas basis, so the figure could not state what it is a figure OF. Reload the "
            "factor table"
        )
    return factor, None


def _year_problem(activity: Activity, factor: FactorCitation, index: int) -> str | None:
    """A factor looked up for a different year than the activity's is refused.

    `year_requested` is what the lookup was asked for; a substitution (asking for 2025 and
    being given 2024) is legitimate and is disclosed separately. What is refused here is
    asking for the wrong year in the first place - most importantly, applying one year's
    factor to both halves of a bill split across 31 December. Without this check that
    mistake would produce a plausible, cited, wrong figure.
    """
    if activity.factor_year is None or factor.year_requested == activity.factor_year:
        return None
    return (
        f"line {index}: the activity ({activity.label}) belongs to {activity.factor_year}, "
        f"but the factor was looked up for {factor.year_requested}. Look the factor up for "
        f"{activity.factor_year}"
    )


def _reconcile(
    activity: Activity, factor: FactorCitation, index: int
) -> tuple[dict | None, str | None]:
    """Get the activity into the factor's unit, or say why it cannot be done.

    Returns `({amount, conversion}, None)` or `(None, problem)`. `conversion` is None when
    the units already agree, so the common case carries no conversion noise.
    """
    expected = UNIT_COMPATIBILITY.get(factor.unit)
    if expected is None:
        return None, (
            f"line {index}: no known activity unit for a factor measured in "
            f"'{factor.unit}'. Recognised factor units: {', '.join(sorted(UNIT_COMPATIBILITY))}"
        )

    if activity.unit == expected:
        return {"amount": activity.amount, "conversion": None}, None

    conversion = UNIT_CONVERSIONS.get((activity.unit, expected))
    if conversion is None:
        return None, (
            f"line {index}: the factor is per '{expected}' ('{factor.unit}') but the "
            f"activity is in '{activity.unit}', and no conversion between them is declared. "
            "No conversion is applied automatically"
        )

    converted = activity.amount * conversion["multiplier"]
    return {
        "amount": converted,
        "conversion": {
            "from_unit": activity.unit,
            "to_unit": expected,
            "from_amount": str(activity.amount),
            "to_amount": str(_trim(converted)),
            "statement": conversion["statement"],
            "source": conversion["source"],
        },
    }, None


def _line(activity: Activity, factor: FactorCitation, reconciled: dict) -> dict:
    """One finished line: the arithmetic, and everything it was computed from."""
    amount = reconciled["amount"]
    conversion = reconciled["conversion"]
    exact_kg = amount * factor.factor

    unit_for_factor = UNIT_COMPATIBILITY[factor.unit]
    if conversion is None:
        derivation = (
            f"{_trim(amount):,} {unit_for_factor} "
            f"x {_trim(factor.factor)} {factor.unit} "
            f"= {_trim(exact_kg):,} kg CO2e"
        )
    else:
        derivation = (
            f"{_trim(activity.amount):,} {activity.unit} "
            f"= {_trim(amount):,} {unit_for_factor} ({conversion['statement']}) "
            f"x {_trim(factor.factor)} {factor.unit} "
            f"= {_trim(exact_kg):,} kg CO2e"
        )

    return {
        "kind": activity.kind,
        "kg_co2e": str(exact_kg.quantize(KG_PLACES, rounding=ROUND_HALF_UP)),
        # From the exact product, not from the rounded kilograms: deriving tonnes from an
        # already-rounded figure would let the two disagree in the last place.
        "tonnes_co2e": str(
            (exact_kg / Decimal(1000)).quantize(TONNE_PLACES, rounding=ROUND_HALF_UP)
        ),
        "kg_co2e_exact": str(exact_kg),
        "derivation": derivation,
        "scope": factor.scope,
        "method": factor.method,
        "gas_basis": factor.gas_basis,
        "activity": activity.model_dump(mode="json"),
        "conversion": conversion,
        "factor": factor.model_dump(mode="json"),
    }


def calculate_emissions(
    *,
    pairs: list[dict],
) -> ToolResult:
    """Multiply activities by emission factors. The only arithmetic in the app.

    Each item of `pairs` is `{"activity": Activity | dict, "factor": FactorCitation |
    dict}`. Dicts are accepted so the agent layer can pass stored tool payloads straight
    through; either way both are validated, because a figure that cannot be cited is worse
    than no figure (rule 2).

    Returns one `ok` carrying every line and the total, or one `error` naming every
    unusable line and carrying no figure at all.
    """
    if not pairs:
        return error(
            "No activity was given to calculate. Read a document first, then pass one "
            "activity and one factor per line."
        )

    problems: list[str] = []
    prepared: list[tuple[Activity, FactorCitation, dict]] = []

    for index, pair in enumerate(pairs, start=1):
        if not isinstance(pair, dict) or "activity" not in pair or "factor" not in pair:
            problems.append(f"line {index}: each pair needs both an activity and a factor")
            continue

        activity, problem = _as_activity(pair["activity"], index)
        if problem:
            problems.append(problem)
        factor, factor_problem = _as_factor(pair["factor"], index)
        if factor_problem:
            problems.append(factor_problem)
        if activity is None or factor is None:
            continue

        year_problem = _year_problem(activity, factor, index)
        if year_problem:
            problems.append(year_problem)
            continue

        reconciled, unit_problem = _reconcile(activity, factor, index)
        if unit_problem or reconciled is None:
            problems.append(unit_problem or f"line {index}: units could not be reconciled")
            continue
        prepared.append((activity, factor, reconciled))

    # All-or-nothing. A partial total is the worst output this module could return: it
    # looks like an answer and is quietly short. See the module docstring.
    if problems:
        return error(
            f"No figure was produced: {len(problems)} of {len(pairs)} line(s) could not be "
            "calculated, and a total over the remainder would be short without saying so. "
            + " | ".join(problems)
            + "."
        )

    lines = [_line(activity, factor, reconciled) for activity, factor, reconciled in prepared]

    exact_total = sum((Decimal(line["kg_co2e_exact"]) for line in lines), Decimal(0))
    total_kg = exact_total.quantize(KG_PLACES, rounding=ROUND_HALF_UP)
    total_tonnes = (exact_total / Decimal(1000)).quantize(TONNE_PLACES, rounding=ROUND_HALF_UP)

    scopes = sorted({line["scope"] for line in lines})
    bases = sorted({line["gas_basis"] for line in lines})
    methods = sorted({line["method"] for line in lines if line["method"]})

    # Per scope, so a mixed result is never only a single number. Summed from the exact
    # products for the same reason the grand total is.
    by_scope = {
        str(scope): str(
            sum(
                (Decimal(line["kg_co2e_exact"]) for line in lines if line["scope"] == scope),
                Decimal(0),
            ).quantize(KG_PLACES, rounding=ROUND_HALF_UP)
        )
        for scope in scopes
    }

    if len(lines) == 1:
        derivation = lines[0]["derivation"]
    else:
        written = "\n".join(f"  {line['derivation']}" for line in lines)
        derivation = f"{written}\n  = {_trim(total_kg):,} kg CO2e in total"

    data = {
        # The answer...
        "kg_co2e": str(total_kg),
        "tonnes_co2e": str(total_tonnes),
        "kg_co2e_exact": str(exact_total),
        "derivation": derivation,
        "scopes": scopes,
        "methods": methods,
        "gas_bases": bases,
        "kg_co2e_by_scope": by_scope,
        # ...and every line it was computed from (rule 2).
        "lines": lines,
        # Structured, not prose: the agent's prompt renders these.
        "disclosures": {
            "estimated_reading": any(line["activity"]["is_estimated"] for line in lines),
            "factor_year_substituted": any(line["factor"]["substituted"] for line in lines),
            "non_singapore_factor": any(line["factor"]["jurisdiction"] != "SG" for line in lines),
            "factor_unverified": any(not line["factor"]["verified"] for line in lines),
            "unit_converted": any(line["conversion"] for line in lines),
            "mixed_scopes": len(scopes) > 1,
            "mixed_gas_basis": len(bases) > 1,
            "not_assured": True,
        },
    }

    publishers = sorted({line["factor"]["publisher"] for line in lines})
    scope_text = ", ".join(f"Scope {scope}" for scope in scopes)
    message = (
        f"{_trim(total_kg):,} kg CO2e across {len(lines)} line(s) "
        f"({scope_text}; {', '.join(publishers)})."
    )
    return ok(data, message)
