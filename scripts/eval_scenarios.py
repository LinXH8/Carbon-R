"""The company-year calculation eval: Makan Mates and its variations, at calculation level.

No model call anywhere. Each scenario's documents come from the generator (in memory, so
the eval needs no generated files and runs in CI); their TRUTH activities go through the
real path

    lookup_emission_factor -> calculate_emissions -> roll_up -> allocate

and the result is compared with figures this script computes a second, independent way:
exact rationals (`fractions.Fraction`) of truth amount x the factor value the table
holds, scoped by document kind rather than by the factor row, summed and rounded half up
once. The factor VALUE is an input to both routes - the table is the authority for it,
and Rule 3 forbids getting it any other way - so what is checked is everything done with
it: unit conversion, the year split, the year filter, the scope grouping, the sums, the
rounding and the allocation.

The honesty properties are checked against the scenario's own definition, not against
the roll-up's output:

    gaps             exactly the withheld monthly documents' months, and an LPG run only
                     when it exceeds LPG_MAX_GAP_DAYS - nothing more, nothing less
    estimated        exactly the documents the generator marked estimated
    excluded         exactly the documents whose factor is not published, named
    substitutions    disclosed whenever a factor is from an earlier year than its activity
    outside year     a meter-cycle bill's days in the neighbouring years listed, not counted
    currency         every USD invoice listed as such; no figure depends on currency
    absent scope     a scope with no document has no figure - None, never 0

Expected emissions are NOT the 30.1 / 39.4 / 21 t of the project context: those used
approximate factors and are illustrative only (SYNTHGEN_SPEC 4.5).

    uv run python scripts/eval_scenarios.py              (or: make eval-scenarios)
    uv run python scripts/eval_scenarios.py --scenario makan_mates_2025

End to end (`--end-to-end`, or: make eval-scenarios-e2e)
--------------------------------------------------------
Three scenarios - the base year, the missing month and the estimated readings - are also
run through EXTRACTION: each rendered page is classified and read by the model (cached per
scenario), and the year is then rolled up from what was READ, not from the key. That is
where the honesty checks meet model behaviour: a gap is only flagged if the periods were
read right, and an estimate only if "Estimated" was read. Reported separately from the
calculation-level result, which it is compared against: the same figures, the same gaps,
the same estimates. Needs `make scenarios` first and an OPENAI_API_KEY.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from fractions import Fraction

from carbon_r.config import PROJECT_ROOT
from carbon_r.models.activity import KIND_UNITS
from carbon_r.synthgen.scenario import (
    GENERATOR_VERSION,
    SCENARIO_SEED,
    SCENARIOS,
    Scenario,
    build_scenario,
    scenario_by_id,
)
from carbon_r.tools.calculate import calculate_emissions
from carbon_r.tools.company_year import (
    LPG_MAX_GAP_DAYS,
    MONTHLY_KINDS,
    SCOPE_OF_KIND,
    DocumentFigure,
    allocate,
    roll_up,
)
from eval_extraction import KINDS, LOOKUP_RETRIES, _factor, git_state

RUNS_DIR = PROJECT_ROOT / "data" / "generated" / "eval_runs_TIER2_BLOCK3_SCENARIOS"
E2E_RUNS_DIR = PROJECT_ROOT / "data" / "generated" / "eval_runs_TIER2_BLOCK3_SCENARIOS_E2E"
SCENARIOS_ROOT = PROJECT_ROOT / "data" / "generated" / "scenarios"

FACTOR_SELECTORS: dict[str, tuple[str, str]] = {
    "electricity": ("electricity_grid_sg", "kg CO2 / kWh"),
    "lpg": ("fuel_lpg", "kg CO2e / tonnes"),
    "diesel": ("fuel_diesel", "kg CO2e / litres"),
    "petrol": ("fuel_petrol", "kg CO2e / litres"),
}
# Activity unit -> the factor's denominator, as a rational multiplier. Written out here
# rather than imported from calculate.py, so a wrong conversion there cannot agree with
# itself here.
PER_FACTOR_UNIT = {("kg", "kg CO2e / tonnes"): Fraction(1, 1000)}

PROPERTIES = (
    "scope_1_exact",
    "scope_2_exact",
    "allocation_exact",
    "gaps_exact",
    "estimated_exact",
    "excluded_named",
    "substitution_disclosed",
    "outside_year_listed",
    "currency_listed",
    "absent_scope_not_zero",
)


def round_half_up(value: Fraction, places: int = 3) -> Decimal:
    scaled = value * 10**places
    whole = (scaled.numerator * 2 + scaled.denominator) // (2 * scaled.denominator)
    return Decimal(whole).scaleb(-places)


def truth_activities(document) -> list[dict]:
    """The document's activities from its answer key, in the shape a reading returns."""
    spec = KINDS[document.doc_kind]
    estimated = bool(getattr(document, "is_estimated_reading", False))
    return [
        {
            "kind": kind,
            "amount": str(amount),
            "unit": KIND_UNITS[kind],
            "source_field": "scenario truth",
            "factor_year": year,
            "is_estimated": estimated,
        }
        for (kind, year), amount in sorted(spec.key_activities(document).items())
    ]


def figure_for(document) -> tuple[DocumentFigure, list[tuple[dict, dict | None]]]:
    """The real path for one document, plus (activity, factor) pairs for the second route."""
    activities = truth_activities(document)
    pairs, looked_up = [], []
    refusal = None
    for activity in activities:
        category, unit = FACTOR_SELECTORS[activity["kind"]]
        result = _factor(category, unit, activity["factor_year"])
        if result.status != "ok":
            looked_up.append((activity, None))
            refusal = refusal or result.message
            continue
        factor = result.data["factor"]
        looked_up.append((activity, factor))
        pairs.append({"activity": activity, "factor": factor})

    common = {
        "document_id": document.doc_id,
        "document_kind": document.doc_kind,
        "period_start": document.period_start,
        "period_end": document.period_end,
        "is_estimated": bool(getattr(document, "is_estimated_reading", False)),
        "currency": document.currency,
    }
    if refusal is not None:
        return DocumentFigure(**common, status="no_results", message=refusal), looked_up
    result = calculate_emissions(pairs=pairs)
    if result.status != "ok":
        return DocumentFigure(**common, status="error", message=result.message), looked_up
    return DocumentFigure(**common, status="ok", lines=result.data["lines"]), looked_up


def expected_for(scenario: Scenario, documents, looked_up: dict[str, list]) -> dict:
    """The second route, and the honesty facts the scenario's definition implies."""
    year = scenario.reporting_year
    by_scope = {1: Fraction(0), 2: Fraction(0)}
    excluded, substituted, outside = set(), set(), set()
    for document in documents:
        pairs = looked_up[document.doc_id]
        if any(factor is None for _, factor in pairs):
            excluded.add(document.doc_id)
            continue
        for activity, factor in pairs:
            if activity["factor_year"] != year:
                outside.add(document.doc_id)
                continue
            conversion = PER_FACTOR_UNIT.get((activity["unit"], factor["unit"]), Fraction(1))
            kg = Fraction(activity["amount"]) * Fraction(str(factor["factor"])) * conversion
            by_scope[SCOPE_OF_KIND[document.doc_kind]] += kg
            if factor["year_used"] != activity["factor_year"]:
                substituted.add(document.doc_id)

    scopes_with_documents = {SCOPE_OF_KIND[d.doc_kind] for d in documents}
    total = by_scope[1] + by_scope[2]
    revenue = Fraction(scenario.business.revenue_sgd)
    contract = Fraction(scenario.contract_revenue)

    start, _ = scenario.active_period
    gaps: dict[str, set[str]] = {}
    for kind, month in scenario.omit:
        if kind in MONTHLY_KINDS and date(year, month, 1) >= start:
            gaps.setdefault(kind, set()).add(f"{year}-{month:02d}")
    lpg_withheld = sorted(m for k, m in scenario.omit if k == "lpg_invoice")
    if lpg_withheld:
        # The withheld months are consecutive in every scenario that withholds LPG; a run
        # is a gap only when its days exceed the threshold.
        first = date(year, lpg_withheld[0], 1)
        after = date(year + lpg_withheld[-1] // 12, lpg_withheld[-1] % 12 + 1, 1)
        days = after - first
        if days.days > LPG_MAX_GAP_DAYS:
            gaps["lpg_invoice"] = {f"{year}-{m:02d}" for m in lpg_withheld}

    return {
        "scope_1_exact": by_scope[1],
        "scope_2_exact": by_scope[2],
        "scopes_with_documents": scopes_with_documents,
        "allocated": round_half_up(total * contract / revenue),
        "gaps": {k: sorted(v) for k, v in gaps.items()},
        "estimated": sorted(
            d.doc_id for d in documents if getattr(d, "is_estimated_reading", False)
        ),
        "excluded": sorted(excluded),
        "substituted": sorted(substituted),
        "outside": sorted(outside),
        "usd": sorted(d.doc_id for d in documents if d.currency != "SGD"),
    }


def evaluate_scenario(scenario: Scenario, seed: int = SCENARIO_SEED) -> dict:
    documents = build_scenario(scenario, seed)
    figures, looked_up = [], {}
    for document in documents:
        figure, pairs = figure_for(document)
        figures.append(figure)
        looked_up[document.doc_id] = pairs

    roll = roll_up(
        figures, reporting_year=scenario.reporting_year, active_period=scenario.active_period
    )
    expected = expected_for(scenario, documents, looked_up)
    data = roll.data or {}
    alloc = (
        allocate(
            data,
            contract_revenue_sgd=scenario.contract_revenue,
            total_revenue_sgd=scenario.business.revenue_sgd,
            share_source=(
                f"scenario {scenario.scenario_id}: contract revenue / total revenue "
                "(fictional figures)"
            ),
        )
        if roll.status == "ok"
        else None
    )

    def scope_kg(scope: str):
        block = (data.get("scopes") or {}).get(scope) or {}
        return None if block.get("kg_co2e") is None else Decimal(block["kg_co2e"])

    def exact_ok(scope: int) -> bool:
        got = scope_kg(str(scope))
        want = expected[f"scope_{scope}_exact"]
        if want == 0 and got is None:
            return True  # nothing in scope this year: no figure, and none expected
        return got is not None and got == round_half_up(want)

    gaps_got = {k: v for k, v in (data.get("gaps") or {}).items()}
    excluded_got = sorted(e["document_id"] for e in data.get("excluded", []))
    if roll.status != "ok":
        excluded_got = sorted(e["document_id"] for e in (roll.data or {}).get("excluded", []))
    disclosures = data.get("disclosures", {})
    absent = [s for s in (1, 2) if s not in expected["scopes_with_documents"]]
    checks = {
        "scope_1_exact": exact_ok(1),
        "scope_2_exact": exact_ok(2),
        "allocation_exact": alloc is not None
        and alloc.status == "ok"
        and Decimal(alloc.data["allocated_kg_co2e"]) == expected["allocated"]
        and alloc.data["allocates"] == "Scope 1 + Scope 2"
        and bool(alloc.data["assumption"]),
        "gaps_exact": gaps_got == expected["gaps"]
        and disclosures.get("coverage_gap", False) == bool(expected["gaps"]),
        "estimated_exact": data.get("estimated_documents", []) == expected["estimated"]
        and disclosures.get("estimated_reading", False) == bool(expected["estimated"]),
        "excluded_named": excluded_got == expected["excluded"]
        and all(e.get("reason") for e in data.get("excluded", []))
        and all(
            (data["scopes"][str(SCOPE_OF_KIND[e["document_kind"]])]["partial"])
            for e in data.get("excluded", [])
        ),
        "substitution_disclosed": sorted({s["document_id"] for s in data.get("substitutions", [])})
        == expected["substituted"]
        and disclosures.get("factor_year_substituted", False) == bool(expected["substituted"]),
        "outside_year_listed": sorted(
            {o["document_id"] for o in data.get("outside_reporting_year", [])}
        )
        == expected["outside"],
        "currency_listed": sorted(d["document_id"] for d in data.get("non_sgd_documents", []))
        == expected["usd"],
        "absent_scope_not_zero": all(
            data.get("scopes", {}).get(str(s), {}).get("kg_co2e") is None for s in absent
        ),
    }
    return {
        "scenario_id": scenario.scenario_id,
        "title": scenario.title,
        "company": scenario.company,
        "reporting_year": scenario.reporting_year,
        "documents": len(documents),
        "status": roll.status,
        "checks": checks,
        "passed": all(checks.values()),
        "got": {
            "scope_1_kg": None if scope_kg("1") is None else str(scope_kg("1")),
            "scope_2_kg": None if scope_kg("2") is None else str(scope_kg("2")),
            "allocated_kg": alloc.data["allocated_kg_co2e"] if alloc and alloc.data else None,
            "share": alloc.data["share"] if alloc and alloc.data else None,
            "gaps": gaps_got,
            "estimated": data.get("estimated_documents", []),
            "excluded": excluded_got,
        },
        "expected": {
            "scope_1_kg": str(round_half_up(expected["scope_1_exact"])),
            "scope_2_kg": str(round_half_up(expected["scope_2_exact"])),
            "allocated_kg": str(expected["allocated"]),
            "gaps": expected["gaps"],
            "estimated": expected["estimated"],
            "excluded": expected["excluded"],
            "substituted": expected["substituted"],
            "outside": expected["outside"],
            "usd": expected["usd"],
        },
        "roll_up": roll.model_dump(mode="json"),
        "allocation": alloc.model_dump(mode="json") if alloc else None,
    }


def report(run: dict) -> int:
    rows = run["scenarios"]
    total = len(rows)
    git = run["git"]
    dirty = " (DIRTY TREE - not a clean measurement)" if git["dirty"] else ""
    print(f"\nCarbon R scenario eval - {total} company-year scenarios, calculation level")
    print(f"  commit {git['commit'][:7]}{dirty}  seed {run['seed']}  {run['generator_version']}")
    print(
        "  0 model calls: truth activities -> exact SQL factor -> calculate -> roll-up -> allocate"
    )
    print()
    for name in PROPERTIES:
        passed = sum(r["checks"][name] for r in rows)
        marker = "" if passed == total else "   <-"
        print(f"  {name:24} {passed:3} of {total}{marker}")
    print(f"  {'every check':24} {sum(r['passed'] for r in rows):3} of {total}")
    print()
    for r in rows:
        mark = "ok  " if r["passed"] else "FAIL"
        g = r["got"]
        print(
            f"  {mark} {r['scenario_id']:30} S1 {g['scope_1_kg'] or '-':>12}  S2 "
            f"{g['scope_2_kg'] or '-':>12}  allocated {g['allocated_kg'] or '-':>12} "
            f"(share {g['share'] or '-'})"
        )
        failed = [name for name, passed in r["checks"].items() if not passed]
        if failed:
            print(f"         failed: {', '.join(failed)}")
            print(f"         got      {json.dumps(r['got'], default=str)}")
            print(f"         expected {json.dumps(r['expected'], default=str)}")
    if run["factor_lookup_retries"]:
        retried = len(run["factor_lookup_retries"])
        print(f"\n  factor lookups retried after a database error: {retried}")
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = run["run_at"].replace(":", "").replace("-", "")
    path = RUNS_DIR / f"{stamp}.json"
    path.write_text(json.dumps(run, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"\nRun written to {path.relative_to(PROJECT_ROOT)}")
    return 0 if all(r["passed"] for r in rows) else 1


def run_all(scenarios=SCENARIOS) -> dict:
    return {
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "git": git_state(),
        "seed": SCENARIO_SEED,
        "generator_version": GENERATOR_VERSION,
        "scenarios": [evaluate_scenario(s) for s in scenarios],
        "factor_lookup_retries": list(LOOKUP_RETRIES),
    }


# --- end to end -----------------------------------------------------------------------------

E2E_CHECKS = (
    "scope_1_matches",
    "scope_2_matches",
    "allocation_matches",
    "gaps_match",
    "estimated_match",
    "excluded_match",
)


def _read_currency(printed: str | None) -> str:
    """The reading's currency as a code. Only the scenario set's two currencies exist."""
    return "USD" if printed and "US" in printed.upper() else "SGD"


def read_figure(row: dict) -> DocumentFigure:
    """One document's figure computed from what extraction READ - the same path as the
    calculation level, fed the reading instead of the key."""
    read = row.get("read") or {}
    common = {
        "document_id": row["doc_id"],
        "document_kind": row["read_kind"],
        "is_estimated": bool(read.get("is_estimated")),
        "currency": _read_currency(read.get("currency")),
    }
    if not read.get("period_start") or not read.get("period_end"):
        return DocumentFigure(
            **common,
            period_start=date(1900, 1, 1),
            period_end=date(1900, 1, 1),
            status="error",
            message="the period was not read, so the document cannot be placed in the year",
        )
    common |= {
        "period_start": date.fromisoformat(read["period_start"]),
        "period_end": date.fromisoformat(read["period_end"]),
    }
    pairs, refusal = [], None
    for activity in read["activities"]:
        category, unit = FACTOR_SELECTORS[activity["kind"]]
        result = _factor(category, unit, activity["factor_year"])
        if result.status != "ok":
            refusal = refusal or result.message
            continue
        pairs.append({"activity": activity, "factor": result.data["factor"]})
    if refusal is not None:
        return DocumentFigure(**common, status="no_results", message=refusal)
    if not pairs:
        return DocumentFigure(**common, status="error", message="no activity was read")
    result = calculate_emissions(pairs=pairs)
    if result.status != "ok":
        return DocumentFigure(**common, status="error", message=result.message)
    return DocumentFigure(**common, status="ok", lines=result.data["lines"])


def evaluate_end_to_end(scenario: Scenario, *, model: str, use_cache: bool) -> dict:
    from carbon_r.tools.extract import DEFAULT_DPI
    from eval_extraction import discover, evaluate, summarise

    root = SCENARIOS_ROOT / scenario.scenario_id
    refs = [ref for kind in KINDS for ref in discover(root, kind)]
    if not refs:
        raise SystemExit(f"No documents under {root}; run `make scenarios` first.")
    extraction = evaluate(refs, model=model, dpi=DEFAULT_DPI, use_cache=use_cache, root=root)
    rows = extraction["documents"]
    figures = [read_figure(row) for row in rows]
    roll = roll_up(
        figures, reporting_year=scenario.reporting_year, active_period=scenario.active_period
    )
    data = roll.data or {}
    alloc = (
        allocate(
            data,
            contract_revenue_sgd=scenario.contract_revenue,
            total_revenue_sgd=scenario.business.revenue_sgd,
            share_source=f"scenario {scenario.scenario_id} (fictional figures)",
        )
        if roll.status == "ok"
        else None
    )
    truth = evaluate_scenario(scenario)

    def scope(block: dict, n: str):
        return ((block.get("scopes") or {}).get(n) or {}).get("kg_co2e")

    truth_roll = truth["roll_up"]["data"] or {}
    checks = {
        "scope_1_matches": scope(data, "1") == scope(truth_roll, "1"),
        "scope_2_matches": scope(data, "2") == scope(truth_roll, "2"),
        "allocation_matches": (alloc.data["allocated_kg_co2e"] if alloc else None)
        == truth["got"]["allocated_kg"],
        "gaps_match": data.get("gaps", {}) == truth["expected"]["gaps"],
        "estimated_match": data.get("estimated_documents", []) == truth["expected"]["estimated"],
        "excluded_match": sorted(e["document_id"] for e in data.get("excluded", []))
        == truth["expected"]["excluded"],
    }
    summary = summarise(rows)
    return {
        "scenario_id": scenario.scenario_id,
        "documents": len(rows),
        "extraction": {
            "kind_correct": sum(r["kind_correct"] for r in rows),
            "activity_correct": sum(r["activity"]["correct"] for r in rows),
            "pipeline_exact": sum(r["pipeline_exact"] for r in rows),
            "every_field": sum(
                r["fields_scored"] > 0 and r["fields_correct"] == r["fields_scored"] for r in rows
            ),
            "cached": sum(r["cached"] for r in rows),
            "retry": summary["retry"],
            "tokens_this_run": summary["tokens_this_run"],
            "misses": [
                {
                    "doc_id": r["doc_id"],
                    "activity": r["activity"],
                    "pipeline_note": r["pipeline_note"],
                    "fields": {f: v for f, v in r["fields"].items() if not v["correct"]},
                }
                for r in rows
                if not (r["activity"]["correct"] and r["pipeline_exact"])
                or r["fields_correct"] != r["fields_scored"]
            ],
        },
        "checks": checks,
        "passed": all(checks.values()),
        "read": {
            "scope_1_kg": scope(data, "1"),
            "scope_2_kg": scope(data, "2"),
            "allocated_kg": alloc.data["allocated_kg_co2e"] if alloc else None,
            "gaps": data.get("gaps", {}),
            "estimated": data.get("estimated_documents", []),
            "excluded": [e["document_id"] for e in data.get("excluded", [])],
        },
        "truth": {
            "scope_1_kg": scope(truth_roll, "1"),
            "scope_2_kg": scope(truth_roll, "2"),
            "allocated_kg": truth["got"]["allocated_kg"],
            "gaps": truth["expected"]["gaps"],
            "estimated": truth["expected"]["estimated"],
        },
        "request": extraction["request"],
        "roll_up": roll.model_dump(mode="json"),
    }


def report_end_to_end(run: dict) -> int:
    rows = run["scenarios"]
    git = run["git"]
    dirty = " (DIRTY TREE - not a clean measurement)" if git["dirty"] else ""
    print(f"\nCarbon R scenario eval - END TO END, {len(rows)} scenarios, through extraction")
    print(f"  commit {git['commit'][:7]}{dirty}  model {run['model']}")
    for r in rows:
        x = r["extraction"]
        n = r["documents"]
        print(f"\n  {r['scenario_id']}  ({n} documents, {x['cached']} cached)")
        print(
            f"    extraction: kind {x['kind_correct']} of {n} · activity {x['activity_correct']} "
            f"of {n} · pipeline {x['pipeline_exact']} of {n} · "
            f"every field {x['every_field']} of {n}"
        )
        rt = x["retry"]
        print(
            f"    retry: {rt['triggered']} triggered, {rt['adopted']} adopted, fields "
            f"correct->wrong {rt['field_buckets']['correct_to_wrong']}, "
            f"wrong->correct {rt['field_buckets']['wrong_to_correct']}"
        )
        for name in E2E_CHECKS:
            print(f"    {name:20} {'yes' if r['checks'][name] else 'NO  <-'}")
        print(
            f"    read:  S1 {r['read']['scope_1_kg']}  S2 {r['read']['scope_2_kg']}  "
            f"allocated {r['read']['allocated_kg']}  gaps {r['read']['gaps']}  "
            f"estimated {r['read']['estimated']}"
        )
        print(
            f"    truth: S1 {r['truth']['scope_1_kg']}  S2 {r['truth']['scope_2_kg']}  "
            f"allocated {r['truth']['allocated_kg']}  gaps {r['truth']['gaps']}  "
            f"estimated {r['truth']['estimated']}"
        )
        for miss in x["misses"]:
            print(f"    miss {miss['doc_id']}: {json.dumps(miss, default=str)[:300]}")
    for name in E2E_CHECKS:
        print(f"  {name:20} {sum(r['checks'][name] for r in rows)} of {len(rows)}")
    tokens = [r["extraction"]["tokens_this_run"] for r in rows]
    print(
        f"  tokens this run: {sum(t['calls'] for t in tokens)} calls, "
        f"{sum(t['input_tokens'] for t in tokens)} in, "
        f"{sum(t['output_tokens'] for t in tokens)} out"
    )
    E2E_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = run["run_at"].replace(":", "").replace("-", "")
    path = E2E_RUNS_DIR / f"{stamp}.json"
    path.write_text(json.dumps(run, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"\nRun written to {path.relative_to(PROJECT_ROOT)}")
    return 0 if all(r["passed"] for r in rows) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", help="one scenario id; default every scenario")
    parser.add_argument(
        "--end-to-end", action="store_true", help="the three e2e scenarios, through extraction"
    )
    parser.add_argument("--no-cache", action="store_true", help="fresh model calls")
    args = parser.parse_args()
    scenarios = [scenario_by_id(args.scenario)] if args.scenario else list(SCENARIOS)
    if not args.end_to_end:
        return report(run_all(scenarios))

    from carbon_r.config import get_settings

    settings = get_settings()
    if not settings.llm_configured or not settings.openai_model:
        print("OPENAI_API_KEY and OPENAI_MODEL must be set; see .env.example", file=sys.stderr)
        return 2
    chosen = [s for s in scenarios if s.end_to_end]
    run = {
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "git": git_state(),
        "model": settings.openai_model,
        "scenarios": [
            evaluate_end_to_end(s, model=settings.openai_model, use_cache=not args.no_cache)
            for s in chosen
        ],
        "factor_lookup_retries": list(LOOKUP_RETRIES),
    }
    return report_end_to_end(run)


if __name__ == "__main__":
    sys.exit(main())
