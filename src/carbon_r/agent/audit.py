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
scope labels are removed before scanning for the same reason, including a list of them
("Scope 1, 2, and 3", "Scopes 1–3"). A list continues only with single digits 1-3, so
"Scope 1, 2,500 t" still audits 2,500. Known limit: a small number (1-3) written straight
after a scope list and a separator is read as part of the list and is not audited - "Scope 2
and 3 tonnes", "Scope 1 and 2 vans".

One tool's text is not a source of numbers: `search_peer_disclosures` (Part B). Its passages
are PDF text layers in which footnote markers fuse onto figures (a total of 240,576
with note 3 attached reads "240,5763") and table columns lose their years, so a number
found only in a peer passage, a judge's quote from one, or the judge's reason about one is
not a number any report states.
Those three fields are dropped before numbers are collected (`_figure_view`); the passage's
structured fields - page, report year, period - still count. A reply that states a peer
figure in its own words is therefore flagged.

What is NOT flagged is a number inside a verified quote of at least five words: a quoted
span that `audit_quotes` would accept is the source's sentence, and its numbers are
reported, not computed (`_without_verified_quotes`). That holds for every tool. The peer
tool withholds the quotes most likely to carry misread table numbers (`is_table_like`), and
a table-like span gets no exemption here even when verbatim - so what can be quoted with its
numbers is prose that happens to contain one.

Quotes get the same treatment (`audit_quotes`, Tier 2 Block 4). A span in double quotes,
straight or curly, presents words as someone else's - in practice a GHG Protocol passage -
so it must appear verbatim (whitespace-normalised) in some string a tool returned, or it is
flagged in `unverified_quotes`. No attempt is made to decide which quotes are "attributed to
methodology": any quoted span is a claim that those words exist somewhere, and the check is
the same for all of them. An ellipsis inside a quote marks an omission, so each piece either
side of it is checked separately. Sentence punctuation just inside the closing mark
(`.,;:!?`) is not part of the quoted words and is ignored, but a match may not stop partway
through a word or a number in the source: "were 2." is not a quote of "were 2.5 t".

Two things are not quotations of a source, and since Interlude D they are not flagged. The
first is the user's own words quoted back (a refusal that names the question it cannot answer): the
user's messages in the session are quote haystacks. The second is a span that is only a scope
label ("Scope 1, 2, and 3"). Neither loosens the figure check: user text is a quote source
only, never a source of numbers, so a quote of the user exempts none of its figures. Only
what the user typed counts, not the document list the server appends to it.

Known limit: the check is that the words exist, not who said them. A quote that matches the
user's own text passes even when the reply attributes it to a source ("the GHG Protocol
says \"…\"" where the words are the user's).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation

# A standalone number: not joined to letters, so `CO2e`, `PM2.5` and `elec_001` contribute
# nothing - and, since Part B, not joined to letters by a hyphen either (`R-32`, a
# refrigerant, or `GHG-2`), which is an identifier, not a figure. Thousands separators are
# part of the token and stripped after.
_NUMBER = re.compile(
    r"(?<![A-Za-z0-9_.])(?<![A-Za-z]-)[-+]?\d[\d,]*(?:\.\d+)?(?![A-Za-z0-9_])(?!-[A-Za-z])"
)

# Not quantities. `Scope 2` is a classification, and so is a tier. A list of scopes is one
# label too - "Scope 1, 2, and 3", "Scopes 1 and 2", "Scopes 1–3", "Scope 1/2" - so after the
# first number the pattern takes further single digits 1-3 joined by a comma, "and", "or",
# "to", "/", "&" or a dash. A digit followed by more digits ("Scope 1, 2,500 t") is a figure
# and ends the list. Added in Interlude D, when "Scope 1, 2, and 3" was flagged as 2 and 3.
_SCOPE_LABEL = (
    r"scopes?\s*[-–]?\s*\d+"
    r"(?:\s*(?:,\s*(?:and|or)?|/|&|and|or|to|-|–)\s*[1-3](?![.,]?\d))*"
)
_NON_QUANTITIES = re.compile(rf"\b(?:{_SCOPE_LABEL}|tier\s*[-–]?\s*\d+)\b", re.IGNORECASE)
_SCOPE_LABELS = re.compile(rf"\b{_SCOPE_LABEL}\b", re.IGNORECASE)
_CONJUNCTIONS_AND_PUNCTUATION = re.compile(r"\b(?:and|or)\b|[\W_]", re.IGNORECASE)

# A numbered-list marker: one or two digits at the start of a line, then "." or ")" and a
# space ("1. Download…", "2) Ingest…"). Not a quantity. Only the marker is removed, so a
# figure later on the same line is still checked. Capped at two digits so a line that starts
# with a year ("2024. The factor…") is still audited. Added after Tier 2 Block 5, when a
# reply's "1." was flagged as an untraced figure.
_LIST_MARKER = re.compile(r"^([ \t]*)\d{1,2}[.)](?=[ \t])", re.MULTILINE)

_MAX_PLACES = 6

# A quoted span this long or longer, found verbatim in a tool payload, is the source's own
# sentence: its numbers are the source's, reported, not the model's. Shorter spans get no
# exemption - a bare "2,5113" in quotation marks is a figure dressed as a quote. The same
# floor `search_methodology` applies to a judge's quote (MIN_QUOTE_WORDS).
_QUOTE_WORDS_FOR_FIGURES = 5

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


# Tools whose free text is not evidence for a number, and the keys holding that text.
_TEXT_NOT_FIGURES = {"search_peer_disclosures": frozenset({"text", "quote", "judge_reason"})}


def _without_keys(value: object, keys: frozenset[str]) -> object:
    if isinstance(value, dict):
        return {k: _without_keys(v, keys) for k, v in value.items() if k not in keys}
    if isinstance(value, list | tuple):
        return [_without_keys(item, keys) for item in value]
    return value


def _figure_view(payload: dict) -> object:
    """The part of a tool payload whose numbers a reply may state."""
    keys = _TEXT_NOT_FIGURES.get(payload.get("tool", ""))
    return _without_keys(payload, keys) if keys else payload


def allowed_numbers(tool_payloads: list[dict]) -> set[Decimal]:
    """Every number a reply may state, given what the tools returned."""
    allowed: set[Decimal] = set()
    for payload in tool_payloads:
        for number in numbers_in(_figure_view(payload)):
            allowed |= _variants(number)
    return allowed


def audit_figures(reply: str, tool_payloads: list[dict]) -> list[str]:
    """Numeric tokens in `reply` that no tool produced, in the order they appear.

    Returns the tokens as written, so a flag can be read against the sentence it came
    from without hunting for which `0.42` was meant.
    """
    allowed = allowed_numbers(tool_payloads)
    prose = _without_verified_quotes(reply, _haystacks(tool_payloads))
    return [token for token in figures_in(prose) if _to_decimal(token) not in allowed]


# A quote is table-like - withheld, cited by page only - when it shows a fused footnote
# marker (below), or when it holds more than
# TABLE_MAX_NUMBERS figures, or figures make up more than TABLE_MAX_SHARE of its
# whitespace-separated tokens. Figures are counted as the audit counts them (`figures_in`):
# scope labels, list markers and identifiers such as R-32 or CO2e are not figures. Set by
# the author in Part B and recorded, not tuned: a table row read out of a text layer is
# exactly where a fused footnote marker or a lost column turns into a wrong number. The peer
# tool withholds such quotes, and a table-like span in a reply earns no exemption below
# even when it is verbatim in a payload (a passage's text still holds the row).
TABLE_MAX_NUMBERS = 3
TABLE_MAX_SHARE = 0.25


# Two signs that a footnote marker has fused onto a figure, added by the author after the
# first pass: a thousands group that is not exactly three digits ("240,5763" would be 240,576
# plus note 3), and a scope that does not exist ("Total scope 17", Scope 1 plus note 7). Both
# only between digits / after the word scope, so "in 2024, the bank" and "Scope 3 category
# 15" are untouched. What they cannot see: a fusion with no comma ("500" plus note 1 reads
# "5001") looks like any other number.
_MALFORMED_THOUSANDS = re.compile(r"\d,(?:\d{1,2}|\d{4,})(?!\d)")
_SCOPE_NUMBER = re.compile(r"\bscope\s*[-\u2013]?\s*(\d+)\b", re.IGNORECASE)


def has_malformed_thousands(text: str) -> bool:
    return _MALFORMED_THOUSANDS.search(text) is not None


def has_impossible_scope(text: str) -> bool:
    return any(int(n) not in (1, 2, 3) for n in _SCOPE_NUMBER.findall(text))


def is_table_like(quote: str) -> bool:
    if has_malformed_thousands(quote) or has_impossible_scope(quote):
        return True
    numbers = len(figures_in(quote))
    tokens = len(quote.split())
    return numbers > TABLE_MAX_NUMBERS or (tokens > 0 and numbers / tokens > TABLE_MAX_SHARE)


def _without_verified_quotes(reply: str, haystacks: list[str]) -> str:
    """The reply with every verified, not table-like quote of 5+ words blanked."""

    def blank(match: re.Match) -> str:
        span = match.group(1) or match.group(2)
        long_enough = len(span.split()) >= _QUOTE_WORDS_FOR_FIGURES
        exempt = long_enough and not is_table_like(span) and _is_verified(span, haystacks)
        return " " if exempt else match.group(0)

    return _QUOTED.sub(blank, reply)


def figures_in(text: str) -> list[str]:
    """The numeric tokens the audit treats as figures: not scope or tier labels, not list
    markers, not numbers inside words. In order, as written."""
    cleaned = _NON_QUANTITIES.sub(" ", _LIST_MARKER.sub(r"\1", text))
    return [token for token in _NUMBER.findall(cleaned) if _to_decimal(token) is not None]


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


def _haystacks(tool_payloads: list[dict]) -> list[str]:
    return [_normalise(s) for payload in tool_payloads for s in strings_in(payload)]


# Sentence punctuation a reply puts inside the closing quotation mark ("…chicken rice."
# for a question that ended "…chicken rice?"). Not part of the quoted words, so it is
# dropped before matching. Added after the Interlude D live check, where it kept the
# user-echo false positive alive.
_TRAILING_PUNCTUATION = re.compile(r"[.,;:!?]+$")


def _found(piece: str, haystacks: list[str]) -> bool:
    """`piece`, less trailing punctuation, appears in a haystack and does not stop partway
    through a word or a number there: "emissions were 2." is not a quote of "were 2.5 t"."""
    words = _TRAILING_PUNCTUATION.sub("", piece).rstrip() or piece
    pattern = re.compile(re.escape(words) + r"(?![A-Za-z0-9_]|[.,]\d)")
    return any(pattern.search(h) for h in haystacks)


def _is_verified(span: str, haystacks: list[str]) -> bool:
    """Every piece of the span (split at an ellipsis) appears verbatim in some payload."""
    pieces = [_normalise(p) for p in _ELLIPSIS.split(span)]
    pieces = [p for p in pieces if p]
    return bool(pieces) and all(_found(p, haystacks) for p in pieces)


def _is_label(span: str) -> bool:
    """A quoted span that is only a scope label ("Scope 2", "Scope 1, 2, and 3") names a
    classification; it presents nobody's words."""
    if not _SCOPE_LABELS.search(span):
        return False
    return not _CONJUNCTIONS_AND_PUNCTUATION.sub("", _SCOPE_LABELS.sub("", span))


def audit_quotes(
    reply: str, tool_payloads: list[dict], user_messages: Sequence[str] = ()
) -> list[str]:
    """Quoted spans in `reply` found in no tool payload and no user message, as written, in
    order. A span that is only a scope label is not a quotation and is skipped."""
    haystacks = _haystacks(tool_payloads) + [_normalise(m) for m in user_messages]
    unverified: list[str] = []
    for match in _QUOTED.finditer(reply):
        span = match.group(1) or match.group(2)
        if not _is_label(span) and not _is_verified(span, haystacks):
            unverified.append(span)
    return unverified
