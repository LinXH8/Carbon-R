"""Render the company-year scenarios to disk, with their answer keys and scenario.json.

    data/generated/scenarios/<scenario_id>/scenario.json
    data/generated/scenarios/<scenario_id>/<doc_kind>/manifest.json
    data/generated/scenarios/<scenario_id>/<doc_kind>/<tier>/doc_XXX.{json,pdf,png}

The per-kind layout is synthgen v1's, so the extraction eval reads a scenario directory
exactly as it reads v1 (`discover`, `evaluate`). The calculation-level eval does not need
these files - it builds the same documents in memory - so this exists for the end-to-end
run and for anyone who wants to open a scenario's pages.

    uv run python scripts/generate_scenarios.py                    (or: make scenarios)
    uv run python scripts/generate_scenarios.py --scenario makan_mates_2025
    uv run python scripts/generate_scenarios.py --end-to-end      the three e2e scenarios only
"""

import argparse
import json
import sys
from pathlib import Path

from carbon_r.config import PROJECT_ROOT
from carbon_r.synthgen.degrade import degrade_pdf, is_image
from carbon_r.synthgen.render import render_html, write_pdf
from carbon_r.synthgen.scenario import (
    GENERATOR_VERSION,
    SCENARIO_SEED,
    SCENARIOS,
    Scenario,
    build_scenario,
    scenario_by_id,
)
from generate_docs import activity_summary, activity_years, document_file

SCENARIOS_ROOT = PROJECT_ROOT / "data" / "generated" / "scenarios"

NOTE = (
    "Synthetic documents for one fictional business's reporting year. Answer keys are "
    "produced by the generator, never by reading a document with a model. Revenue and "
    "contract revenue are fictional and exist only to give an allocation a stated basis. "
    "The generator records activity data only; it never looks up an emission factor or "
    "computes emissions."
)


def write_scenario(scenario: Scenario, root: Path = SCENARIOS_ROOT) -> int:
    out = root / scenario.scenario_id
    documents = build_scenario(scenario)
    by_kind: dict[str, list] = {}
    for document in documents:
        tier_dir = out / document.doc_kind / document.difficulty_tier
        tier_dir.mkdir(parents=True, exist_ok=True)
        html = render_html(document)
        if is_image(document.degradation):
            clean_pdf = tier_dir / f"{document.doc_id}.clean.pdf"
            write_pdf(html, clean_pdf)
            degrade_pdf(clean_pdf, tier_dir / f"{document.doc_id}.png", document.degradation)
        else:
            write_pdf(html, tier_dir / f"{document.doc_id}.pdf")
        (tier_dir / f"{document.doc_id}.json").write_text(
            document.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        by_kind.setdefault(document.doc_kind, []).append(document)

    for kind, docs in by_kind.items():
        manifest = {
            "doc_kind": kind,
            "generator_version": GENERATOR_VERSION,
            "seed": SCENARIO_SEED,
            "document_count": len(docs),
            "scenario_id": scenario.scenario_id,
            "note": NOTE,
            "documents": [
                {
                    "doc_id": d.doc_id,
                    "file": f"{d.difficulty_tier}/{document_file(d)}",
                    "layout_id": d.layout_id,
                    "is_holdout_layout": d.is_holdout_layout,
                    "difficulty_tier": d.difficulty_tier,
                    "period_start": d.period_start.isoformat(),
                    "period_end": d.period_end.isoformat(),
                    "factor_years": activity_years(d),
                    "activity": activity_summary(d),
                    "quirks": d.quirks,
                }
                for d in docs
            ],
        }
        (out / kind / "manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )

    start, end = scenario.active_period
    record = {
        "scenario_id": scenario.scenario_id,
        "title": scenario.title,
        "note": scenario.note,
        "generator_version": GENERATOR_VERSION,
        "seed": SCENARIO_SEED,
        "company": {
            "key": scenario.company,
            "name": documents[0].customer_name,
            "industry": scenario.business.industry,
            "revenue_sgd": str(scenario.business.revenue_sgd),
            "contract_revenue_sgd": str(scenario.contract_revenue),
        },
        "reporting_year": scenario.reporting_year,
        "active_period": [start.isoformat(), end.isoformat()],
        "intended": {
            "withheld": [list(item) for item in scenario.omit],
            "quirks": [list(item) for item in scenario.quirks],
            "elec_cycle_day": scenario.elec_cycle_day,
            "lpg_usd_months": list(scenario.lpg_usd_months),
            "without": list(scenario.without),
        },
        "end_to_end": scenario.end_to_end,
        "documents": [
            {
                "doc_id": d.doc_id,
                "doc_kind": d.doc_kind,
                "file": f"{d.doc_kind}/{d.difficulty_tier}/{document_file(d)}",
                "period_start": d.period_start.isoformat(),
                "period_end": d.period_end.isoformat(),
                "currency": d.currency,
                "is_estimated_reading": bool(getattr(d, "is_estimated_reading", False)),
                "quirks": d.quirks,
                # The truth activities, for reading; the eval recomputes them from the key.
                "activity": activity_summary(d),
            }
            for d in documents
        ],
        "note_generator": NOTE,
    }
    (out / "scenario.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return len(documents)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", help="one scenario id; default every scenario")
    parser.add_argument("--end-to-end", action="store_true", help="only the end-to-end three")
    args = parser.parse_args()
    if args.scenario:
        chosen = [scenario_by_id(args.scenario)]
    else:
        chosen = [s for s in SCENARIOS if s.end_to_end or not args.end_to_end]
    total = 0
    for scenario in chosen:
        count = write_scenario(scenario)
        total += count
        print(f"  {scenario.scenario_id:32} {count:3} documents")
    print(
        f"{total} documents in {len(chosen)} scenarios under "
        f"{SCENARIOS_ROOT.relative_to(PROJECT_ROOT)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
