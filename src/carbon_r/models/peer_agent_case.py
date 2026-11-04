"""Eval 5b's agent cases: peer questions put to the whole agent, with checks fixed in advance.

`data/evals/peer_agent_questions.json` holds each case's question, the plain-English pass
rule the author set, and the mechanical checks that stand for it. Each check is one kind
from the closed vocabulary below, so the file cannot name a check the scorer does not
implement, and the scorer cannot quietly reinterpret one (`scripts/eval_peer_agent.py`).

The cases have no dev/test split, so they are test only (the author's rule, 2b): they are
run once the prompt is final, each case `runs` times, every check reported as n of runs,
the replies recorded as given, and the prompt not changed in response. A failure is logged
as a known limit.

How the text checks read a reply, fixed here so they are not tuned after a run:

- `no_ranking`: a ranking word (`RANKING_WORDS`) anywhere in a sentence fails, unless the
  same sentence carries a negation (`NEGATIONS`): "I can't say which is lower" restates
  the question and refuses; "DBS has the lower Scope 2" ranks.
- `no_offer_to_compare`: a sentence with an offer ("I can", "if you want", "would you
  like", ...) and "compar" or "rank" fails, unless it carries a negation, as for ranking.
  The run-3 (b) reply ended in an offer. The negation rule was added after the 2b baseline
  run, where "I can’t rank them" was scored as an offer three times; a sentence that both
  refuses and offers now passes, a known limit of the check.
- `method_named`: "location-based", "market-based" or "method not stated" appears.
- `period_named`: a period written as the tools write it, "1 Jan 2025 to 31 Dec 2025".
- `disclosure_stated`: "self-reported" and "not assured" (or "not independently assured").
- `names_difference`: a boundary, sector or period difference is named (`DIFFERENCE_TERMS`).
- `no_figure`: the audit's own `figures_in`, less four-digit years, finds nothing once
  dates and financial-year labels ("1 April 2025", "FY2025/26") are removed.
- `states_no_verified_figure`: "no verified figure", or with up to three words between
  ("no verified Scope 1 figure"); widened after v2.
- `quotes_exclude`: no quoted span in the reply contains any of the given terms.
- `units_stated`: every given unit appears as printed, with a subscript "₂" read as "2" and
  case and spacing ignored. Units are taken longest first and every occurrence of one is
  removed before the next is looked for, so ["thousand tCO2e", "tCO2e"] needs a bare
  "tCO2e" outside every "thousand tCO2e". Which company each unit sits beside is not
  checked; that is read by hand.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from carbon_r.agent.audit import figures_in
from carbon_r.config import PROJECT_ROOT
from carbon_r.sources import PEERS
from carbon_r.tools.peer import PEER_CAVEAT

AGENT_CASES_PATH = PROJECT_ROOT / "data" / "evals" / "peer_agent_questions.json"

RANKING_WORDS = (
    "lower", "lowest", "higher", "highest", "better", "best", "worse", "worst", "smaller",
    "smallest", "larger", "largest", "bigger", "biggest", "greener", "cleaner", "outperform",
    "outperforms", "ahead", "behind", "leads", "lags",
)  # fmt: skip
NEGATIONS = ("not", "n't", "cannot", "no", "never", "without")
OFFERS = ("i can", "i could", "if you want", "if you'd like", "if you would like",
          "would you like", "happy to", "let me know")  # fmt: skip
DIFFERENCE_TERMS = (
    "boundary", "boundaries", "consolidation", "equity share", "operational control",
    "financial control", "sector", "reporting period", "financial year", "not comparable",
)  # fmt: skip


class _Check(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ToolCalled(_Check):
    """At least one call to `tool`, with `company` among its arguments when given, and the
    given status when given."""

    kind: Literal["tool_called"]
    tool: str
    company: str | None = None
    status: Literal["ok", "no_results", "error"] | None = None


class Plain(_Check):
    kind: Literal[
        "no_unverified_figures",
        "no_unverified_quotes",
        "no_ranking",
        "no_offer_to_compare",
        "method_named",
        "period_named",
        "disclosure_stated",
        "names_difference",
        "no_figure",
        "states_no_verified_figure",
    ]


class QuotesExclude(_Check):
    kind: Literal["quotes_exclude"]
    terms: list[str] = Field(min_length=1)


class UnitsStated(_Check):
    kind: Literal["units_stated"]
    units: list[str] = Field(min_length=1)


Check = Annotated[ToolCalled | Plain | QuotesExclude | UnitsStated, Field(discriminator="kind")]


class AgentCase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9_]+$")
    question: str = Field(min_length=5)
    # The earlier failure or limit this case measures, as recorded in TIER2_BLOCK4.md.
    measures: str
    # The author's pass rule, in words. The checks stand for it; a reply is read against it.
    pass_rule: str
    checks: list[Check] = Field(min_length=1)

    @model_validator(mode="after")
    def _companies_are_peers(self) -> AgentCase:
        companies = {p.company for p in PEERS.values()}
        for check in self.checks:
            if isinstance(check, ToolCalled) and check.company not in (None, *companies):
                raise ValueError(f"{self.id}: {check.company!r} is not a peer company")
        return self


class AgentCaseSet(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int
    drafted: str
    review_status: Literal["draft", "reviewed"]
    runs: int = Field(ge=1)
    cases: list[AgentCase]

    @model_validator(mode="after")
    def _unique(self) -> AgentCaseSet:
        duplicates = [i for i, n in Counter(c.id for c in self.cases).items() if n > 1]
        if duplicates:
            raise ValueError(f"duplicate case ids: {duplicates}")
        return self


def load_agent_cases(path: Path = AGENT_CASES_PATH) -> AgentCaseSet:
    return AgentCaseSet.model_validate(json.loads(path.read_text()))


# --- scoring one reply ---------------------------------------------------------------------

_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
_METHOD = re.compile(r"\b(?:location|market)[- ]based\b|\bmethod not stated\b", re.I)
_PERIOD = re.compile(r"\b\d{1,2} [A-Z][a-z]{2,8} \d{4} to \d{1,2} [A-Z][a-z]{2,8} \d{4}\b")
_YEAR = re.compile(r"(?:19|20)\d\d")
# "no verified figure", and with up to three words between: "no verified Scope 1 figure",
# "no verified market-based figures". Widened after v2, where "no verified Scope 1 figure"
# failed the exact phrase.
_NO_VERIFIED_FIGURE = re.compile(r"\bno verified (?:[\w-]+ ){0,3}?figures?\b", re.I)
_QUOTED = re.compile(r'"([^"\n]+)"|\u201c([^\u201d\n]+)\u201d')
_MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*"
# Dates and financial-year labels name a period, not a figure: "1 April 2025", "1 Apr",
# "FY2025/26". Removed before `no_figure` reads the reply.
_DATE_OR_FY = re.compile(
    rf"\b\d{{1,2}} {_MONTH}(?: \d{{4}})?\b|\bFY\s?'?\d{{2,4}}(?:\s?[/\-\u2013]\s?\d{{2,4}})?\b"
)


def _sentences(reply: str) -> list[str]:
    return [s.strip().lower() for s in _SENTENCE.split(reply) if s.strip()]


def _has_word(text: str, words: tuple[str, ...]) -> bool:
    return any(re.search(rf"\b{re.escape(w)}\b", text) for w in words)


def _negated(sentence: str) -> bool:
    # Replies often use a curly apostrophe ("can’t"), so both spellings count.
    contracted = "n't" in sentence or "n’t" in sentence
    return contracted or _has_word(sentence, tuple(w for w in NEGATIONS if w != "n't"))


def _tool_called(check: ToolCalled, calls: list[dict]) -> bool:
    for call in calls:
        if call["tool"] != check.tool:
            continue
        if check.status is not None and call["status"] != check.status:
            continue
        if check.company is not None:
            try:
                arguments = json.loads(call["arguments"] or "{}")
            except json.JSONDecodeError:
                continue
            if arguments.get("company") != check.company:
                continue
        return True
    return False


def _unit_form(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\u2082", "2")).casefold()


def _units_stated(units: list[str], reply: str) -> bool:
    remaining = _unit_form(reply)
    for unit in sorted((_unit_form(u) for u in units), key=len, reverse=True):
        pattern = rf"(?<![\w']){re.escape(unit)}(?!\w)"
        if re.search(pattern, remaining) is None:
            return False
        remaining = re.sub(pattern, " ", remaining)
    return True


def check_label(check: Check) -> str:
    if isinstance(check, ToolCalled):
        parts = [check.tool, check.company, check.status]
        return "tool_called:" + ":".join(p for p in parts if p)
    if isinstance(check, QuotesExclude):
        return "quotes_exclude:" + ",".join(check.terms)
    if isinstance(check, UnitsStated):
        return "units_stated:" + ",".join(check.units)
    return check.kind


def score_reply(case: AgentCase, response: dict) -> dict[str, bool]:
    """Every check of `case` against one `/chat` response (its JSON). Pure.

    Since 2b the loop attaches PEER_CAVEAT to any reply that used a peer tool. The checks
    read the MODEL's words, with that caveat removed; only `disclosure_stated` reads the
    whole reply, and so measures the code wherever a peer tool was used."""
    full = response["reply"]
    reply = full.removesuffix(f"\n\n{PEER_CAVEAT}")
    lowered = reply.lower()
    sentences = _sentences(reply)
    plain = {
        "no_unverified_figures": response["unverified_figures"] == [],
        "no_unverified_quotes": response.get("unverified_quotes", []) == [],
        "no_ranking": not any(_has_word(s, RANKING_WORDS) and not _negated(s) for s in sentences),
        "no_offer_to_compare": not any(
            any(o in s for o in OFFERS) and ("compar" in s or "rank" in s) and not _negated(s)
            for s in sentences
        ),
        "method_named": bool(_METHOD.search(reply)),
        "period_named": bool(_PERIOD.search(reply)),
        "disclosure_stated": ("self-reported" in full.lower() or "self reported" in full.lower())
        and ("not assured" in full.lower() or "not independently assured" in full.lower()),
        "names_difference": any(term in lowered for term in DIFFERENCE_TERMS),
        "no_figure": not [
            t for t in figures_in(_DATE_OR_FY.sub(" ", reply)) if not _YEAR.fullmatch(t)
        ],
        "states_no_verified_figure": bool(_NO_VERIFIED_FIGURE.search(reply)),
    }
    results: dict[str, bool] = {}
    for check in case.checks:
        if isinstance(check, ToolCalled):
            passed = _tool_called(check, response["tool_calls"])
        elif isinstance(check, QuotesExclude):
            spans = [a or b for a, b in _QUOTED.findall(reply)]
            passed = not any(t.lower() in span.lower() for span in spans for t in check.terms)
        elif isinstance(check, UnitsStated):
            passed = _units_stated(check.units, reply)
        else:
            passed = plain[check.kind]
        results[check_label(check)] = passed
    return results


__all__ = [
    "AGENT_CASES_PATH",
    "DIFFERENCE_TERMS",
    "NEGATIONS",
    "OFFERS",
    "RANKING_WORDS",
    "AgentCase",
    "AgentCaseSet",
    "Check",
    "Plain",
    "QuotesExclude",
    "ToolCalled",
    "UnitsStated",
    "check_label",
    "load_agent_cases",
    "score_reply",
]
