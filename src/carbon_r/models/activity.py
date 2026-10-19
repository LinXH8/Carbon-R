"""`Activity` - one quantity of one thing, with how it was arrived at.

Tier 1 had no need for this type. A document was an electricity bill, the activity was its
`consumption_kwh`, and the unit was a module constant (`ACTIVITY_UNIT = "kWh"`). Three
facts about Scope 1 documents break all three assumptions at once:

- an LPG invoice measures kg, a fuel-card statement measures litres;
- a fuel-card statement listing diesel and petrol has **two** activities, needing two
  different factors, and they must not be added together before the factors are applied;
- the number may not be printed anywhere on the page. "4 x 50 kg" is the activity; 200 kg
  is what the factor applies to, and the multiplication is Python's job (rule 1).

So an extracted document yields a *list* of these, each naming its own unit, and
`calculate_emissions` pairs each one with a factor.

`derivation` is what makes the third case auditable. When the amount was printed, it is
None - there is nothing to explain. When Python computed it, it holds the arithmetic in
full ("4 cylinders x 50 kg = 200 kg"), so a reviewer can check the step that the page
itself does not show. A figure whose activity was computed and whose computation is not
recorded would be a bare number one level up from the one rule 2 forbids.
"""

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# What is being consumed. Not the same as a factor category: `diesel` and `petrol` are
# both read off one fuel-card statement and each needs its own factor row, while the
# `kind` is what the document says was bought.
ActivityKind = Literal["electricity", "lpg", "diesel", "petrol"]

# The unit each kind is measured in on the documents this project reads. Checked at
# construction, so an activity cannot claim to be litres of electricity.
KIND_UNITS: dict[str, str] = {
    "electricity": "kWh",
    "lpg": "kg",
    "diesel": "litres",
    "petrol": "litres",
}


class Activity(BaseModel):
    """One measured quantity from one document, ready to be paired with a factor."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: ActivityKind
    amount: Decimal = Field(ge=0)
    unit: str = Field(min_length=1)

    # Which printed field, or fields, this came from. Names the page, not the model: an
    # eval reporting "the litres were wrong" is more useful when it can say which column.
    source_field: str = Field(min_length=1)

    # Filled only when Python derived the amount rather than reading it. See the module
    # docstring.
    derivation: str | None = None

    # True when the document itself marks the reading as an estimate rather than a meter
    # read. Travels with the activity rather than the document because a document can
    # carry several activities and they need not share a basis.
    is_estimated: bool = False

    # The calendar year this quantity belongs to, and so the year its factor must be
    # looked up for. Set from the activity period, never from the document date. A bill
    # whose period straddles 31 December yields one activity per year (`prorate.py`), and
    # `calculate_emissions` refuses a factor looked up for any other year - so "the right
    # year's factor" is enforced by the code rather than by the model following a hint.
    # None only for an activity built by hand in a test that does not care.
    factor_year: int | None = None

    # The days this quantity covers. On a split, each part carries its own share of the
    # original period, so the year boundary is visible on the line itself.
    period_start: date | None = None
    period_end: date | None = None

    @property
    def label(self) -> str:
        """`'4,423 kWh of electricity'` - for a tool message, never for a calculation."""
        return f"{self.amount:,} {self.unit} of {self.kind}"


def unit_for(kind: str) -> str:
    """The unit a kind of activity is measured in. Raises on an unknown kind."""
    try:
        return KIND_UNITS[kind]
    except KeyError:
        raise ValueError(
            f"No activity unit known for {kind!r}; known kinds: {', '.join(sorted(KIND_UNITS))}"
        ) from None


__all__ = ["KIND_UNITS", "Activity", "ActivityKind", "unit_for"]
