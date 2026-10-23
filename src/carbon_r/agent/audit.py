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

Quotes get the same treatment (`audit_quotes`, Tier 2 Block 4). A span in double quotes,
straight or curly, presents words as someone else's - in practice a GHG Protocol passage -
so it must appear verbatim (whitespace-normalised) in some string a tool returned, or it is
flagged in `unverified_quotes`. No attempt is made to decide which quotes are "attributed to
methodology": any quoted span is a claim that those words exist somewhere, and the check is
the same for all of them. An ellipsis inside a quote marks an omission, so each piece either
side of it is checked separately.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

# A standalone number: not preceded or followed by a letter, so `CO2e` and `elec_001`
# contribute nothing. Thousands separators are part of the token and stripped after.
_NUMBER = re.compile(r"(?<![A-Za-z0-9_.])[-+]?\d[\d,]*(?:\.\d+)?(?![A-Za-z0-9_])")

# Not quantities. `Scope 2` is a classification, and so is a tier.
_NON_QUANTITIES = re.compile(r"\b(?:scope|tier)\s*[-–]?\s*\d+\b", re.IGNORECASE)

# A numbered-list marker: one or two digits at the start of a line, then "." or ")" and a
# space ("1. Download…", "2) Ingest…"). Not a quantity. Only the marker is removed, so a
# figure later on the same line is still checked. Capped at two digits so a line that starts
# with a year ("2024. The factor…") is still audited. Added after Tier 2 Block 5, when a
# reply's "1." was flagged as an untraced figure.
_LIST_MARKER = re.compile(r"^([ \t]*)\d{1,2}[.)](?=[ \t])", re.MULTILINE)

_MAX_PLACES = 6

# A quoted span: straight double quotes or curly ones, on one line.
_QUOTED = re.compile(r'"([^"\n]+)"|\u201c([^\u201d\n]+)\u201d')
_ELLIPSIS = re.compile(r"\.\.\.|\u2026")


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
    text = _NON_QUANTITIES.sub(" ", _LIST_MARKER.sub(r"\1", reply))

    unverified: list[str] = []
    for token in _NUMBER.findall(text):
        number = _to_decimal(token)
        if number is None or number in allowed:
            continue
        unverified.append(token)
    return unverified


def _normalise(text: str) -> str:
    return " ".join(text.split())


def strings_in(value: object) -> list[str]:
    """Every string reachable in a tool payload, however deeply nested."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for item in value.values() for s in strings_in(item)]
    if isinstance(value, list | tuple):
        return [s for item in value for s in strings_in(item)]
    return []


def audit_quotes(reply: str, tool_payloads: list[dict]) -> list[str]:
    """Quoted spans in `reply` that appear in no tool payload, as written, in order."""
    haystacks = [_normalise(s) for payload in tool_payloads for s in strings_in(payload)]
    unverified: list[str] = []
    for match in _QUOTED.finditer(reply):
        span = match.group(1) or match.group(2)
        pieces = [_normalise(p) for p in _ELLIPSIS.split(span)]
        pieces = [p for p in pieces if p]
        if pieces and not all(any(p in h for h in haystacks) for p in pieces):
            unverified.append(span)
    return unverified
