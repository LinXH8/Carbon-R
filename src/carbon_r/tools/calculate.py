"""`calculate_emissions` - the one place a greenhouse-gas figure is computed.

This module is CLAUDE.md rule 1 made concrete. It imports no model provider and no
database: it is plain Python over `Decimal`, it can be read in one sitting, and every
number it returns can be re-derived by hand from the inputs it hands back alongside.

Three properties are load-bearing:

1. **It returns its inputs.** Rule 2: there is no code path here that returns a bare
   float. The result carries the activity amount, the factor with full provenance, and the
   derivation written out as a string, so a reviewer can check the arithmetic without
   re-running anything.
2. **It refuses incompatible units.** A factor in `kg CO2 / kWh` applied to litres is an
   `error`, never a silent conversion. Unit conversion, when it arrives in Tier 2, will be
   an explicit step with its own citation - not a lookup table of fudge factors here.
3. **Its disclosures are flags, not prose.** The agent's system prompt turns
   `factor_year_substituted: true` into a sentence. Keeping the wording in one place means
   it cannot drift between tools, and it leaves this function testable by equality rather
   than by string matching.

Rounding is not `money.round2`: that helper means "round to cents, as a vendor would", and
an emission figure is not money. The quantisation below is for *presentation only* - the
exact product is always returned too, and `tonnes` is derived from the exact product
rather than from the rounded kilograms, so the two can never disagree by a rounding step.
"""

from decimal import ROUND_HALF_UP, Decimal

from pydantic import ValidationError

from carbon_r.models.factors import FactorCitation
from carbon_r.tools.base import ToolResult, error, ok

# What a factor's unit implies about the activity it can be applied to. Exact string
# match against the publisher's own unit string - EMA's "kg CO2 / kWh", spaces and all.
# A factor whose unit is not a key here cannot be used by this function at all, which is
# the intended behaviour for an unrecognised unit: refuse, do not improvise.
UNIT_COMPATIBILITY: dict[str, str] = {
    "kg CO2 / kWh": "kWh",
    "kg CO2e / kWh": "kWh",
}

KG_PLACES = Decimal("0.001")  # grams: finer than any reporting threshold cares about
TONNE_PLACES = Decimal("0.000001")

METHOD = "location-based"  # GHG Protocol Scope 2 Guidance; the only method Tier 1 supports
BASIS = "activity-based"


def _trim(value: Decimal) -> Decimal:
    """Drop trailing zeros for display without ever producing exponent notation.

    `Decimal("56119.200").normalize()` is `56119.2`, which is what we want - but
    `Decimal("139600").normalize()` is `1.396E+5`, which is not. Integers are quantised
    back to a plain integer instead.
    """
    if value == value.to_integral_value():
        return value.quantize(Decimal(1))
    return value.normalize()


def calculate_emissions(
    *,
    activity_amount: Decimal,
    activity_unit: str,
    factor: FactorCitation | dict,
    is_estimated_reading: bool = False,
) -> ToolResult:
    """Multiply an activity amount by an emission factor. The only arithmetic in the app.

    `factor` accepts the `data["factor"]` dict from `lookup_emission_factor` as well as a
    `FactorCitation`, so the agent loop can pass a tool result straight through. Either
    way it is validated: a factor arriving without provenance is an `error`, because a
    figure that cannot be cited is worse than no figure (rule 2).
    """
    if isinstance(factor, dict):
        try:
            factor = FactorCitation.model_validate(factor)
        except ValidationError as exc:
            missing = ", ".join(".".join(str(p) for p in e["loc"]) for e in exc.errors())
            return error(
                "The emission factor is missing the provenance every figure must carry "
                f"({missing}). A factor must travel with its publisher, source document, "
                "unit and year before it can be used in a calculation."
            )

    if not isinstance(activity_amount, Decimal):
        return error(
            f"activity_amount must be a Decimal, got {type(activity_amount).__name__}. "
            "Floats are not used anywhere a figure reaches the user."
        )
    if activity_amount < 0:
        return error(f"activity_amount is negative ({activity_amount}).")

    expected_unit = UNIT_COMPATIBILITY.get(factor.unit)
    if expected_unit is None:
        return error(
            f"No known activity unit for a factor measured in '{factor.unit}'. "
            f"Recognised factor units: {', '.join(sorted(UNIT_COMPATIBILITY))}."
        )
    if activity_unit != expected_unit:
        return error(
            f"Unit mismatch: the factor is per '{expected_unit}' "
            f"('{factor.unit}') but the activity is in '{activity_unit}'. "
            "No conversion is applied automatically; convert explicitly, with a source, "
            "before calculating."
        )

    exact_kg = activity_amount * factor.factor
    kg_co2e = exact_kg.quantize(KG_PLACES, rounding=ROUND_HALF_UP)
    # From the exact product, not from kg_co2e: deriving tonnes from an already-rounded
    # figure would let the two disagree in the last place.
    tonnes_co2e = (exact_kg / Decimal(1000)).quantize(TONNE_PLACES, rounding=ROUND_HALF_UP)

    derivation = (
        f"{_trim(activity_amount):,} {activity_unit} "
        f"x {_trim(factor.factor)} {factor.unit} "
        f"= {_trim(exact_kg):,} kg CO2e"
    )

    data = {
        # The answer...
        "kg_co2e": str(kg_co2e),
        "tonnes_co2e": str(tonnes_co2e),
        "kg_co2e_exact": str(exact_kg),
        "derivation": derivation,
        "method": METHOD,
        "basis": BASIS,
        # ...and everything it was computed from (rule 2).
        "inputs": {
            "activity_amount": str(activity_amount),
            "activity_unit": activity_unit,
            "factor": factor.model_dump(mode="json"),
        },
        # Structured, not prose: the agent's prompt renders these.
        "disclosures": {
            "estimated_reading": is_estimated_reading,
            "factor_year_substituted": factor.substituted,
            "non_singapore_factor": factor.is_non_singapore,
            "factor_unverified": not factor.verified,
            "not_assured": True,
        },
    }

    return ok(data, f"{derivation} ({METHOD}, {factor.publisher} {factor.year_used}).")
