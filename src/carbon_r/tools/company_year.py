"""A company's year: Scope 1 and 2 totals over many documents, and one buyer's share of them.

Tier 2 Block 3 pulls this forward from Tier 3 as Python only - no agent tool yet - because
the calculation eval needs it: "Makan Mates, allocated" is a figure over a year of
documents, not over one.

Two functions, both deterministic and both `{status, data, message}`:

    roll_up(documents, reporting_year=..., active_period=...)
    allocate(roll_up_data, contract_revenue_sgd=..., total_revenue_sgd=..., share_source=...)

Neither computes an emission. `roll_up` sums lines `calculate_emissions` already produced
(their exact products, rounded once at the end, as `calculate_emissions` does), and
everything it was given is in what it returns, so a total can be re-derived by hand (rule 2).

What `roll_up` must never do quietly (rule 7):

- **count a day outside the reporting year.** A bill from 15 Dec 2024 to 14 Jan 2025 is
  already split by days into two activities; only the 2025 one counts toward 2025, and
  the 2024 one is listed as outside the year rather than dropped.
- **drop a document it could not cost.** A document whose factor is not published is
  named under `excluded`, its scope is marked partial, and the total says so.
- **treat a missing month as a quiet month.** Electricity and fuel cards bill every month,
  so each uncovered day in the active period is a gap and its months are listed. LPG is
  bought when cylinders run out, so one month without an invoice is ordinary; only a run
  longer than LPG_MAX_GAP_DAYS is flagged (see there).
- **report "no documents" as zero.** A scope with no document behind it has no figure.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from carbon_r.tools.base import ToolResult, error, no_results, ok
from carbon_r.tools.calculate import KG_PLACES, TONNE_PLACES

# Which scope a document kind's activity falls in, for a document that produced NO line
# (no factor, so no factor row to read the scope from). GHG Protocol Corporate Standard:
# purchased electricity is Scope 2; fuel burned in owned equipment and vehicles, Scope 1.
# A document that did produce lines is scoped by its factor rows, never by this table.
SCOPE_OF_KIND = {"electricity_bill": 2, "lpg_invoice": 1, "fuel_card_statement": 1}

# Kinds billed every month: an uncovered day is a missing document.
MONTHLY_KINDS = frozenset({"electricity_bill", "fuel_card_statement"})

# LPG is bought when cylinders run out, not on a calendar. The scenario profiles show the
# cadence: a monthly-billed kitchen's invoices cover 28 to 31 days each, and a per-delivery
# customer's visits fall 9 to 16 days apart (lpg.DELIVERY_INTERVAL_DAYS). A month with no
# invoice is ordinary for a kitchen with full cylinders on site, so it is not flagged.
# 45 days is two weeks longer than the longest month and nearly three times the longest
# delivery interval: it flags two consecutive months without an invoice, or three missed
# deliveries, and not one quiet month. A judgement, stated so it can be argued with.
LPG_MAX_GAP_DAYS = 45

ASSURANCE = (
    "These figures are estimates produced from the uploaded documents. They are not "
    "assured, audited or verified by a third party."
)
ALLOCATION_ASSUMPTION = (
    "Allocating by revenue share is an assumption, not a measurement: it presumes this "
    "buyer's work caused emissions in proportion to what it paid, whatever that work was."
)


class DocumentFigure(BaseModel):
    """One document's contribution: its period, and the lines `calculate_emissions`
    returned for it - or why there are none."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    document_id: str
    document_kind: str
    period_start: date
    period_end: date
    status: Literal["ok", "no_results", "error"]
    message: str = ""
    is_estimated: bool = False
    currency: str = "SGD"
    lines: list[dict] = Field(default_factory=list)


def _q(value: Decimal, places: Decimal) -> str:
    return str(value.quantize(places, rounding=ROUND_HALF_UP))


def _days(start: date, end: date) -> set[date]:
    return {start + timedelta(days=n) for n in range((end - start).days + 1)}


def _runs(days: list[date]) -> list[tuple[date, date]]:
    """Consecutive days as (first, last) runs."""
    runs: list[tuple[date, date]] = []
    for day in sorted(days):
        if runs and day == runs[-1][1] + timedelta(days=1):
            runs[-1] = (runs[-1][0], day)
        else:
            runs.append((day, day))
    return runs


def _coverage(kind: str, documents: list[DocumentFigure], window: tuple[date, date]) -> dict:
    covered: set[date] = set()
    for document in documents:
        start = max(document.period_start, window[0])
        end = min(document.period_end, window[1])
        if start <= end:
            covered |= _days(start, end)
    uncovered = _runs(sorted(_days(*window) - covered))
    if kind in MONTHLY_KINDS:
        gaps = uncovered
    else:
        gaps = [run for run in uncovered if (run[1] - run[0]).days + 1 > LPG_MAX_GAP_DAYS]
    months = sorted({f"{d.year}-{d.month:02d}" for first, last in gaps for d in _days(first, last)})
    return {
        "documents": len(documents),
        "rule": (
            "every day of the active period"
            if kind in MONTHLY_KINDS
            else f"no run longer than {LPG_MAX_GAP_DAYS} days"
        ),
        "gaps": [
            {"from": a.isoformat(), "to": b.isoformat(), "days": (b - a).days + 1} for a, b in gaps
        ],
        "gap_months": months,
        "uncovered_not_flagged": [
            {"from": a.isoformat(), "to": b.isoformat(), "days": (b - a).days + 1}
            for a, b in uncovered
            if (a, b) not in gaps
        ],
    }


def roll_up(
    documents: list[DocumentFigure | dict],
    *,
    reporting_year: int,
    active_period: tuple[date, date] | None = None,
) -> ToolResult:
    """Scope 1 and Scope 2 for one reporting year, with everything that qualifies them."""
    docs = [d if isinstance(d, DocumentFigure) else DocumentFigure(**d) for d in documents]
    if not docs:
        return no_results("No documents were given, so there is no year to total.")
    year_window = (date(reporting_year, 1, 1), date(reporting_year, 12, 31))
    window = active_period or year_window
    window = (max(window[0], year_window[0]), min(window[1], year_window[1]))

    exact = {1: Decimal(0), 2: Decimal(0)}
    counted: dict[int, int] = {1: 0, 2: 0}
    included, outside, excluded = [], [], []
    substitutions, non_sg, methods, bases = [], [], {1: set(), 2: set()}, set()
    for doc in docs:
        if doc.status != "ok":
            excluded.append(
                {
                    "document_id": doc.document_id,
                    "document_kind": doc.document_kind,
                    "scope": SCOPE_OF_KIND.get(doc.document_kind),
                    "status": doc.status,
                    "reason": doc.message,
                }
            )
            continue
        for line in doc.lines:
            year = line["activity"].get("factor_year")
            entry = {
                "document_id": doc.document_id,
                "kind": line["kind"],
                "activity_year": year,
                "scope": line["scope"],
                "kg_co2e_exact": line["kg_co2e_exact"],
                "derivation": line["derivation"],
            }
            if year != reporting_year:
                outside.append(entry)
                continue
            scope = int(line["scope"])
            exact[scope] += Decimal(line["kg_co2e_exact"])
            counted[scope] += 1
            included.append(entry)
            methods[scope].add(line.get("method") or "")
            bases.add(line.get("gas_basis"))
            factor = line["factor"]
            if factor.get("substituted"):
                substitutions.append(
                    {
                        "document_id": doc.document_id,
                        "activity_year": year,
                        "factor_year": factor["year_used"],
                        "publisher": factor["publisher"],
                    }
                )
            if factor.get("jurisdiction") != "SG":
                non_sg.append(
                    {
                        "document_id": doc.document_id,
                        "jurisdiction": factor.get("jurisdiction"),
                        "publisher": factor["publisher"],
                    }
                )

    in_year = [d for d in docs if d.period_end >= window[0] and d.period_start <= window[1]]
    coverage = {
        kind: _coverage(kind, [d for d in in_year if d.document_kind == kind], window)
        for kind in sorted({d.document_kind for d in docs})
    }
    excluded_scopes = {e["scope"] for e in excluded}

    def scope_block(scope: int) -> dict:
        has_docs = counted[scope] > 0 or scope in excluded_scopes
        if not has_docs:
            # No document of this scope: no figure, and explicitly not zero.
            return {
                "kg_co2e": None,
                "tonnes_co2e": None,
                "lines": 0,
                "partial": False,
                "note": f"No Scope {scope} document was supplied; no figure, not zero.",
            }
        return {
            "kg_co2e": _q(exact[scope], KG_PLACES) if counted[scope] else None,
            "tonnes_co2e": _q(exact[scope] / 1000, TONNE_PLACES) if counted[scope] else None,
            "kg_co2e_exact": str(exact[scope]),
            "lines": counted[scope],
            "methods": sorted(m for m in methods[scope] if m),
            "partial": scope in excluded_scopes,
            "note": (
                f"Partial: {sum(e['scope'] == scope for e in excluded)} Scope {scope} "
                "document(s) could not be costed and are not in this figure."
                if scope in excluded_scopes
                else ""
            ),
        }

    scopes = {"1": scope_block(1), "2": scope_block(2)}
    if not included:
        return no_results(
            f"No document produced a figure for {reporting_year}. "
            f"{len(excluded)} could not be costed; {len(outside)} line(s) fall outside the year.",
            {"excluded": excluded, "outside_reporting_year": outside, "coverage": coverage},
        )

    total_exact = exact[1] + exact[2]
    estimated = sorted(
        {d.document_id for d in docs if d.is_estimated}
        | {
            d.document_id
            for d in docs
            for line in d.lines
            if line["activity"].get("is_estimated")
            and line["activity"].get("factor_year") == reporting_year
        }
    )
    non_sgd = [
        {"document_id": d.document_id, "currency": d.currency} for d in docs if d.currency != "SGD"
    ]
    gaps = {kind: c["gap_months"] for kind, c in coverage.items() if c["gaps"]}
    disclosures = {
        "coverage_gap": bool(gaps),
        "partial_scope": bool(excluded),
        "estimated_reading": bool(estimated),
        "factor_year_substituted": bool(substitutions),
        "non_singapore_factor": bool(non_sg),
        "mixed_gas_basis": len(bases) > 1,
        "outside_reporting_year": bool(outside),
        "non_sgd_currency": bool(non_sgd),
        "not_assured": True,
    }
    data = {
        "reporting_year": reporting_year,
        "active_period": [window[0].isoformat(), window[1].isoformat()],
        "kg_co2e_scope_1_and_2": _q(total_exact, KG_PLACES),
        "tonnes_co2e_scope_1_and_2": _q(total_exact / 1000, TONNE_PLACES),
        "kg_co2e_exact": str(total_exact),
        "scopes": scopes,
        "coverage": coverage,
        "gaps": gaps,
        "estimated_documents": estimated,
        "substitutions": substitutions,
        "non_singapore_factors": non_sg,
        "non_sgd_documents": non_sgd,
        "excluded": excluded,
        "outside_reporting_year": outside,
        "included_lines": included,
        "disclosures": disclosures,
        "assurance": ASSURANCE,
    }
    parts = [f"{data['tonnes_co2e_scope_1_and_2']} t CO2e, Scope 1 + 2, {reporting_year}"]
    if gaps:
        parts.append("gaps: " + "; ".join(f"{k} {', '.join(v)}" for k, v in gaps.items()))
    if excluded:
        parts.append(f"{len(excluded)} document(s) could not be costed")
    if estimated:
        parts.append(f"{len(estimated)} estimated reading(s)")
    return ok(data, ". ".join(parts) + ".")


def allocate(
    roll: dict,
    *,
    contract_revenue_sgd: Decimal,
    total_revenue_sgd: Decimal,
    share_source: str,
) -> ToolResult:
    """One buyer's share of a year's Scope 1 + 2, by revenue share. Inputs travel with it.

    Multiplied before it is divided, and rounded once, so the share's own rounding never
    reaches the figure. Everything the roll-up had to disclose, the allocation inherits.
    """
    contract = Decimal(contract_revenue_sgd)
    revenue = Decimal(total_revenue_sgd)
    if revenue <= 0:
        return error(f"Total revenue must be positive to allocate by share; got {revenue}.")
    if not 0 <= contract <= revenue:
        return error(
            f"Contract revenue {contract} must be between 0 and total revenue {revenue}; "
            "a share outside 0-100% is not an allocation."
        )
    total_exact = Decimal(roll["kg_co2e_exact"])
    allocated = total_exact * contract / revenue
    inherited = [name for name, on in roll["disclosures"].items() if on and name != "not_assured"]
    data = {
        "allocates": "Scope 1 + Scope 2",
        "reporting_year": roll["reporting_year"],
        "total_kg_co2e": roll["kg_co2e_scope_1_and_2"],
        "total_kg_co2e_exact": str(total_exact),
        "contract_revenue_sgd": str(contract),
        "total_revenue_sgd": str(revenue),
        "share": str((contract / revenue).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)),
        "share_source": share_source,
        "allocated_kg_co2e": _q(allocated, KG_PLACES),
        "allocated_tonnes_co2e": _q(allocated / 1000, TONNE_PLACES),
        "derivation": (
            f"{roll['kg_co2e_scope_1_and_2']} kg CO2e x {contract:,} / {revenue:,} SGD "
            f"= {_q(allocated, KG_PLACES)} kg CO2e"
        ),
        "assumption": ALLOCATION_ASSUMPTION,
        "inherits": inherited,
        "assurance": ASSURANCE,
    }
    return ok(
        data,
        f"{data['allocated_tonnes_co2e']} t CO2e allocated (Scope 1 + 2 x {data['share']}).",
    )


__all__ = [
    "ALLOCATION_ASSUMPTION",
    "LPG_MAX_GAP_DAYS",
    "SCOPE_OF_KIND",
    "DocumentFigure",
    "allocate",
    "roll_up",
]
