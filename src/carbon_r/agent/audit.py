"""Does every number in the answer trace back to a tool?

CLAUDE.md rule 1 says the model never does arithmetic. The tool layer makes that true of
the *calculation* - a handle cannot be mistyped. This module makes it checkable of the
*answer*: after the loop finishes, every numeric token in the reply is matched against
the numbers the tools actually returned in this session, and anything untraceable comes
back as `unverified_figures`.

That turns the project's central claim from a prompt instruction into a measurement. The
5-bill end-to-end run asserts the list is empty; if it is ever not, the response says so
rather than the figure passing quietly.

Three equivalences are allowed, and they are allowed deliberately rather than by accident:

- **Thousands separators.** `56,119.2` is `56119.2`.
- **A stated rounding.** `56,119.2` matches a tool's `56119.200`, because a reply that
  rounds a figure it was given is reporting, not computing. Matching is to 0-6 decimal
  places, the precision `calculate_emissions` itself returns.
- **A fraction written as a percentage.** `9%` matches a stored `0.09`. The GST rate is
  stored as a fraction and printed as a percentage, and both are the same number.

Everything else - a sum, a difference, a per-month average, an invented total - is
flagged. A number buried in a word (`CO2e`, `Scope2`) is not a figure and is not scanned;
the literal phrases `Scope 1/2/3` are removed before scanning for the same reason.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

# A standalone number: not preceded or followed by a letter, so `CO2e` and `elec_001`
# contribute nothing. Thousands separators are part of the token and stripped after.
_NUMBER = re.compile(r"(?<![A-Za-z0-9_.])[-+]?\d[\d,]*(?:\.\d+)?(?![A-Za-z0-9_])")

# Not quantities. `Scope 2` is a classification, and so is a tier.
_NON_QUANTITIES = re.compile(r"\b(?:scope|tier)\s*[-–]?\s*\d+\b", re.IGNORECASE)

_MAX_PLACES = 6


def _to_decimal(token: str) -> Decimal | None:
    try:
        return Decimal(token.replace(",", "").lstrip("+"))
    except InvalidOperation:
        return None


def numbers_in(value: object) -> set[Decimal]:
    """Every number reachable in a tool payload, however deeply nested."""
    found: set[Decimal] = set()
    if isinstance(value, dict):
        for item in value.values():
            found |= numbers_in(item)
    elif isinstance(value, list | tuple):
        for item in value:
            found |= numbers_in(item)
    elif isinstance(value, bool):
        pass  # True is not 1 here
    elif isinstance(value, int | float | Decimal):
        found.add(Decimal(str(value)))
    elif isinstance(value, str):
        for token in _NUMBER.findall(value):
            number = _to_decimal(token)
            if number is not None:
                found.add(number)
    return found


def _variants(number: Decimal) -> set[Decimal]:
    """A tool's number, plus the forms a faithful reply may state it in."""
    forms = {number}
    for places in range(_MAX_PLACES + 1):
        forms.add(round(number, places))
    if abs(number) < 1:
        forms.add(number * 100)  # a fraction stated as a percentage
    return forms


def allowed_numbers(tool_payloads: list[dict]) -> set[Decimal]:
    """Every number a reply may state, given what the tools returned."""
    allowed: set[Decimal] = set()
    for payload in tool_payloads:
        for number in numbers_in(payload):
            allowed |= _variants(number)
    return allowed


def audit_figures(reply: str, tool_payloads: list[dict]) -> list[str]:
    """Numeric tokens in `reply` that no tool produced, in the order they appear.

    Returns the tokens as written, so a flag can be read against the sentence it came
    from without hunting for which `0.42` was meant.
    """
    allowed = allowed_numbers(tool_payloads)
    text = _NON_QUANTITIES.sub(" ", reply)

    unverified: list[str] = []
    for token in _NUMBER.findall(text):
        number = _to_decimal(token)
        if number is None or number in allowed:
            continue
        unverified.append(token)
    return unverified
