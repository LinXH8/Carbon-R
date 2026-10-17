"""The first eval: does extraction recover the answer key, and does the pipeline agree?

Two numbers, because they fail for different reasons (SYNTHGEN_SPEC.md 4.1, 6.1):

    extraction  per field, vs the generator's answer key   - did it READ the page?
    pipeline    kgCO2e from the key  vs  from the reading  - did it INTERPRET it right?

Both run through the same `calculate_emissions`, so the pipeline comparison isolates the
reading: identical code, identical factor lookup, different inputs.

    uv run python scripts/eval_extraction.py            (or: make eval)
    uv run python scripts/eval_extraction.py --no-cache --docs elec_001,elec_009

The reply cache
---------------
A cache HIT skips only the model call. `normalise`, `check_consistency`,
`lookup_emission_factor`, `calculate_emissions` and `compare_fields` always re-run against
the current code, so a code fix can be re-measured against the same replies for free.

The cache key carries everything that can change a reply - document, model, dpi, image
detail, and a hash of the instruction AND the JSON schema (see `tools/extract.py`). A
changed request therefore misses every key and calls the model for every document, even
without `--no-cache`. `--no-cache` forces fresh calls with an UNCHANGED request; its
purpose is to measure run-to-run variation.

Counts, never bare percentages (CLAUDE.md): "17 of 18" is a claim that can be checked.
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from carbon_r.config import PROJECT_ROOT, get_settings
from carbon_r.models.electricity_bill import ElectricityBill
from carbon_r.models.extraction import ExtractedElectricityBill, NormalisedBill
from carbon_r.tools.calculate import calculate_emissions
from carbon_r.tools.extract import (
    DEFAULT_DPI,
    IMAGE_DETAIL,
    REQUEST_HASH,
    build_result,
    cache_key,
    read_page,
)
from carbon_r.tools.factors import lookup_emission_factor

GENERATED = PROJECT_ROOT / "data" / "generated" / "electricity_bill" / "clean"
REPLY_CACHE = PROJECT_ROOT / "data" / "generated" / ".eval-cache"
# Run records are kept per DOCUMENT SET, not in one pooled directory. These results
# describe bills produced by the synthgen v0 generator (SYNTHGEN_V0.md): one layout, one
# vendor, the clean tier, no quirks. When Tier 2 Block 2 adds layouts, quirks and degraded
# tiers, its runs belong in their own directory - an accuracy figure is only meaningful
# against the set it was measured on, and pooling the two would silently mix them.
RUNS_DIR = PROJECT_ROOT / "data" / "generated" / "eval_runs_SYNTHGEN_V0"

FACTOR_CATEGORY = "electricity_grid_sg"
FACTOR_UNIT = "kg CO2 / kWh"

# Every field that is BOTH printed on the page and present in the answer key. Fields the
# generator knows but the page does not show (seed, layout, degradation) are not
# extractable and are not scored.
SCORED_FIELDS = (
    "vendor_name",
    "vendor_uen",
    "customer_name",
    "customer_address",
    "account_number",
    "document_date",
    "period_start",
    "period_end",
    "meter_number",
    "previous_reading_kwh",
    "current_reading_kwh",
    "consumption_kwh",
    "is_estimated_reading",
    "tariff_plan_code",
    "tariff_plan_name",
    "tension",
    "currency",
    "tariff_cents_per_kwh",
    "energy_charge_sgd",
    "fixed_charge_sgd",
    "subtotal_sgd",
    "gst_rate",
    "gst_amount_sgd",
    "total_sgd",
)

# How each type is compared. Declared rather than inferred, so the strictness of the score
# is visible: money and dates are exact, and only free text is forgiving - and only about
# whitespace and case, never about content.
TEXT_FIELDS = frozenset(
    {
        "vendor_name",
        "vendor_uen",
        "customer_name",
        "customer_address",
        "account_number",
        "meter_number",
        "tariff_plan_code",
        "tariff_plan_name",
        "tension",
        "currency",
    }
)


def _comparable(value, field: str):
    if value is None:
        return None
    if field in TEXT_FIELDS:
        return " ".join(str(value).split()).casefold()
    if isinstance(value, Decimal):
        return value
    return value


def compare_fields(key: ElectricityBill, reading: NormalisedBill | None) -> dict[str, dict]:
    """Field-by-field verdict. Pure: no API, no database - unit-testable on its own."""
    verdicts: dict[str, dict] = {}
    for field in SCORED_FIELDS:
        expected = getattr(key, field)
        got = getattr(reading, field) if reading is not None else None
        verdicts[field] = {
            "correct": _comparable(expected, field) == _comparable(got, field),
            "expected": str(expected),
            "got": None if got is None else str(got),
        }
    return verdicts


def emissions_for(consumption_kwh: int | None, factor_year: int | None) -> dict:
    """kgCO2e for an activity, or None with the reason. Same path for key and reading.

    Returns `{kg_co2e, substituted, note}`. `note` is empty on success and says what went
    wrong otherwise, so a pipeline miss reports a cause rather than just a blank.
    """
    if consumption_kwh is None or factor_year is None:
        return {
            "kg_co2e": None,
            "substituted": None,
            "note": "consumption or period year was not read",
        }

    factor = lookup_emission_factor(FACTOR_CATEGORY, FACTOR_UNIT, factor_year)
    if factor.status != "ok":
        return {
            "kg_co2e": None,
            "substituted": None,
            "note": f"factor lookup {factor.status}: {factor.message}",
        }

    result = calculate_emissions(
        activity_amount=Decimal(consumption_kwh),
        activity_unit="kWh",
        factor=factor.data["factor"],
    )
    if result.status != "ok":
        return {
            "kg_co2e": None,
            "substituted": factor.data["factor"]["substituted"],
            "note": f"calculation {result.status}: {result.message}",
        }
    return {
        "kg_co2e": result.data["kg_co2e"],
        "substituted": factor.data["factor"]["substituted"],
        "note": "",
    }


def load_reading(
    pdf: Path, doc_id: str, *, model: str, dpi: int, use_cache: bool
) -> tuple[ExtractedElectricityBill, bool]:
    """The model's raw reading, from cache when the request is unchanged."""
    path = REPLY_CACHE / cache_key(doc_id, model, dpi)
    if use_cache and path.exists():
        return ExtractedElectricityBill.model_validate_json(path.read_text()), True

    extracted = read_page(pdf, dpi=dpi, model=model)
    REPLY_CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(extracted.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return extracted, False


def evaluate(doc_ids: list[str], *, model: str, dpi: int, use_cache: bool) -> dict:
    rows = []
    for doc_id in doc_ids:
        pdf = GENERATED / f"{doc_id}.pdf"
        key = ElectricityBill.model_validate_json((GENERATED / f"{doc_id}.json").read_text())

        extracted, cached = load_reading(pdf, doc_id, model=model, dpi=dpi, use_cache=use_cache)
        result = build_result(extracted, pdf, dpi=dpi, model=model)
        reading = (
            NormalisedBill.model_validate(result.data["normalised"])
            if result.status == "ok"
            else None
        )

        verdicts = compare_fields(key, reading)
        # The key's own figure, and the reading's. Same factor table, same calculation;
        # the reading supplies its OWN period year, so a misread period that changes the
        # factor year shows up here as a pipeline miss rather than hiding.
        from_key = emissions_for(key.consumption_kwh, key.factor_year)
        from_reading = emissions_for(
            reading.consumption_kwh if reading else None,
            reading.factor_year if reading else None,
        )
        expected_kg = from_key["kg_co2e"]
        actual_kg = from_reading["kg_co2e"]

        rows.append(
            {
                "doc_id": doc_id,
                "cached": cached,
                "status": result.status,
                "fields": verdicts,
                "fields_correct": sum(v["correct"] for v in verdicts.values()),
                "field_problems": result.data.get("field_problems", []) if result.data else [],
                "consistency_flags": (
                    result.data.get("consistency_flags", []) if result.data else []
                ),
                "expected_kg_co2e": expected_kg,
                "actual_kg_co2e": actual_kg,
                "pipeline_exact": (
                    expected_kg is not None
                    and actual_kg is not None
                    and Decimal(expected_kg) == Decimal(actual_kg)
                ),
                "pipeline_note": from_reading["note"],
                "factor_substituted": from_reading["substituted"],
                "factor_year_expected": key.factor_year,
                "factor_year_read": reading.factor_year if reading else None,
                "model_notes": extracted.notes,
            }
        )
    return {
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "request": {
            "model": model,
            "dpi": dpi,
            "image_detail": IMAGE_DETAIL,
            "request_hash": REQUEST_HASH,
            "cache_enabled": use_cache,
        },
        "document_count": len(rows),
        "documents": rows,
    }


def report(run: dict) -> int:
    rows = run["documents"]
    total = len(rows)
    cached = sum(r["cached"] for r in rows)
    fresh = total - cached

    print(f"\nCarbon R extraction eval - {total} documents")
    print(
        f"  model {run['request']['model']}  dpi {run['request']['dpi']}  "
        f"detail {run['request']['image_detail']}  request {run['request']['request_hash']}"
    )

    if cached and fresh:
        print(f"  MIXED RUN: {cached} cached, {fresh} fresh - not a clean measurement")
    else:
        print(f"  {cached} cached, {fresh} fresh")

    print("\nPer-field extraction accuracy")
    worst = []
    for field in SCORED_FIELDS:
        correct = sum(r["fields"][field]["correct"] for r in rows)
        marker = "" if correct == total else "   <-"
        print(f"  {field:24} {correct:3} of {total}{marker}")
        if correct != total:
            worst.append(field)

    all_correct = sum(r["fields_correct"] == len(SCORED_FIELDS) for r in rows)
    pipeline_exact = sum(r["pipeline_exact"] for r in rows)
    substituted = sum(1 for r in rows if r["factor_substituted"])

    print(f"\n  every field correct     {all_correct:3} of {total}")
    print(f"  pipeline kgCO2e exact   {pipeline_exact:3} of {total}")
    print(f"  factor-year substituted {substituted:3} of {total} (newest factor is 2024)")

    failures = [
        (r["doc_id"], field, v["expected"], v["got"])
        for r in rows
        for field, v in r["fields"].items()
        if not v["correct"]
    ]
    if failures:
        print(f"\nField failures ({len(failures)})")
        for doc_id, field, expected, got in failures:
            print(f"  {doc_id} · {field} · expected {expected!r} · got {got!r}")

    for row in rows:
        if not row["pipeline_exact"]:
            print(
                f"\n  pipeline miss {row['doc_id']}: expected {row['expected_kg_co2e']} kg, "
                f"got {row['actual_kg_co2e']} kg ({row['pipeline_note'] or 'figures differ'})"
            )
        for flag in row["consistency_flags"]:
            print(f"  consistency {row['doc_id']}: {flag['relation']} off by {flag['difference']}")
        for problem in row["field_problems"]:
            print(f"  unparsed {row['doc_id']}: {problem['field']} <- {problem['printed']!r}")

    if worst:
        print(f"\nWeakest fields: {', '.join(worst)}")

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = run["run_at"].replace(":", "").replace("-", "")
    path = RUNS_DIR / f"{stamp}-{run['request']['request_hash']}.json"
    path.write_text(json.dumps(run, indent=2) + "\n", encoding="utf-8")
    print(f"\nRun written to {path.relative_to(PROJECT_ROOT)}")

    return 0 if pipeline_exact == total else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docs", help="comma-separated doc ids; default every document")
    parser.add_argument("--limit", type=int, help="score only the first N documents")
    parser.add_argument("--dpi", type=int, default=DEFAULT_DPI)
    parser.add_argument("--model", default=None)
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="call the model again for every document, to measure run-to-run variation",
    )
    args = parser.parse_args()

    settings = get_settings()
    model = args.model or settings.openai_model
    if not settings.llm_configured or not model:
        print("OPENAI_API_KEY and OPENAI_MODEL must be set; see .env.example", file=sys.stderr)
        return 2
    if not GENERATED.exists():
        print(f"No generated documents at {GENERATED}. Run `make docs` first.", file=sys.stderr)
        return 2

    if args.docs:
        doc_ids = [d.strip() for d in args.docs.split(",") if d.strip()]
    else:
        doc_ids = sorted(p.stem for p in GENERATED.glob("elec_*.json"))
    if args.limit:
        doc_ids = doc_ids[: args.limit]
    if not doc_ids:
        print("No documents selected.", file=sys.stderr)
        return 2

    run = evaluate(doc_ids, model=model, dpi=args.dpi, use_cache=not args.no_cache)
    return report(run)


if __name__ == "__main__":
    raise SystemExit(main())
