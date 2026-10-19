"""The extraction eval: does the pipeline recover the answer key, per document kind?

Four numbers, because they fail for different reasons (SYNTHGEN_SPEC.md 4.1, 6.1):

    classification  which kind did it decide this was?     - did it ROUTE the page?
    extraction      per field, vs the generator's key      - did it READ the page?
    activity        the quantities, in the right units     - did it find what to CALCULATE?
    pipeline        kgCO2e from the key vs from the reading - did it INTERPRET it right?

`classification` and `activity` are new in Tier 2 Block 1. They exist because the two new
failures of this block are not field failures: a page sent to the wrong schema reads as an
illegible document, and an LPG invoice whose cylinder arithmetic went wrong has every
field right and the wrong answer.

Everything runs through the same `calculate_emissions`, so the pipeline comparison isolates
the reading: identical code, identical factor lookup, different inputs.

    uv run python scripts/eval_extraction.py                      (or: make eval)
    uv run python scripts/eval_extraction.py --kind lpg_invoice
    uv run python scripts/eval_extraction.py --no-cache --docs elec_001,lpg_001

Per-kind registry
-----------------
One `KindSpec` per document kind holds its answer-key model, the fields worth scoring, how
to compare them, and how to get the activities and the factor selectors. Adding a kind is
a row; nothing branches on `doc_kind` outside the registry.

Expected refusals are scored as EXPECTED, not as misses. `lpg_001` has a 2024 supply period
and the DESNZ table starts at 2025, so no figure can honestly be produced for it - and a
run that counted that as a failure would be measuring the data, not the pipeline.

The reply cache
---------------
A cache HIT skips only the model calls. Normalisation, consistency checks, factor lookup,
calculation and field comparison always re-run against the current code, so a code fix can
be re-measured against the same replies for free.

The cache key carries everything that can change a reply - document, model, dpi, image
detail, and a hash of the classifier AND all three extraction instructions and schemas
(see `tools/extract.py`). A changed request therefore misses every key and calls the model
for every document, even without `--no-cache`. `--no-cache` forces fresh calls with an
UNCHANGED request; its purpose is to measure run-to-run variation.

Counts, never bare percentages (CLAUDE.md): "17 of 18" is a claim that can be checked.
"""

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from carbon_r.config import PROJECT_ROOT, get_settings
from carbon_r.models.electricity_bill import ElectricityBill
from carbon_r.models.extraction import NormalisedBill
from carbon_r.models.extraction_fuel import NormalisedFuelCardStatement
from carbon_r.models.extraction_lpg import NormalisedLPGInvoice
from carbon_r.models.fuel_card_statement import FuelCardStatement
from carbon_r.models.lpg_invoice import LPGInvoice
from carbon_r.tools.calculate import calculate_emissions
from carbon_r.tools.extract import (
    DEFAULT_DPI,
    EXTRACTORS,
    IMAGE_DETAIL,
    REQUEST_HASH,
    build_result,
    cache_key,
    classify_page,
    read_page,
)
from carbon_r.tools.factors import lookup_emission_factor

GENERATED = PROJECT_ROOT / "data" / "generated"
REPLY_CACHE = GENERATED / ".eval-cache"
# Run records are kept per DOCUMENT SET, not in one pooled directory. These results
# describe the Tier 2 Block 1 set: three kinds, one layout each, the clean tier, no quirks.
# When Block 2 adds layouts, quirks and degraded tiers, its runs belong in their own
# directory - an accuracy figure is only meaningful against the set it was measured on,
# and pooling the two would silently mix them.
RUNS_DIR = GENERATED / "eval_runs_TIER2_BLOCK1"

# Activity kind -> the (category, unit) selectors its factor is looked up by. The unit
# strings are the publishers' own, verbatim.
FACTOR_SELECTORS: dict[str, tuple[str, str]] = {
    "electricity": ("electricity_grid_sg", "kg CO2 / kWh"),
    "lpg": ("fuel_lpg", "kg CO2e / tonnes"),
    "diesel": ("fuel_diesel", "kg CO2e / litres"),
    "petrol": ("fuel_petrol", "kg CO2e / litres"),
}


@dataclass(frozen=True)
class KindSpec:
    """Everything the eval needs to know about one document kind."""

    key_model: type
    normalised_model: type
    doc_glob: str
    # Fields that are BOTH printed on the page and present in the answer key. Fields the
    # generator knows but the page does not show (seed, layout, degradation) are not
    # extractable and are not scored.
    scored_fields: tuple[str, ...]
    # Compared on whitespace and case only - never on content. Declared rather than
    # inferred, so the strictness of the score is visible: money and dates are exact.
    text_fields: frozenset[str]
    # How to read the truth's activities, as {kind: amount}, for the activity score.
    key_activities: Any


def _electricity_activities(key: ElectricityBill) -> dict[str, Decimal]:
    return {"electricity": Decimal(key.consumption_kwh)}


def _lpg_activities(key: LPGInvoice) -> dict[str, Decimal]:
    return {"lpg": key.lpg_kg}


def _fuel_activities(key: FuelCardStatement) -> dict[str, Decimal]:
    """Litres per ACTIVITY KIND, so two octane grades collapse as the pipeline collapses
    them. Keyed by kind rather than by printed product name for exactly that reason."""
    from carbon_r.models.extraction_fuel import kind_for_product

    totals: dict[str, Decimal] = {}
    for product, litres in key.litres_by_product.items():
        kind = kind_for_product(product)
        totals[kind] = totals.get(kind, Decimal(0)) + litres
    return totals


KINDS: dict[str, KindSpec] = {
    "electricity_bill": KindSpec(
        key_model=ElectricityBill,
        normalised_model=NormalisedBill,
        doc_glob="elec_*.json",
        scored_fields=(
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
        ),
        text_fields=frozenset(
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
        ),
        key_activities=_electricity_activities,
    ),
    "lpg_invoice": KindSpec(
        key_model=LPGInvoice,
        normalised_model=NormalisedLPGInvoice,
        doc_glob="lpg_*.json",
        scored_fields=(
            "vendor_name",
            "vendor_uen",
            "customer_name",
            "customer_address",
            "account_number",
            "invoice_number",
            "document_date",
            "period_start",
            "period_end",
            # The field the page never prints: it is the cylinder arithmetic.
            "lpg_kg",
            "returned_cylinders",
            "currency",
            "delivery_charge_sgd",
            "rental_charge_sgd",
            "subtotal_sgd",
            "gst_rate",
            "gst_amount_sgd",
            "total_sgd",
        ),
        text_fields=frozenset(
            {
                "vendor_name",
                "vendor_uen",
                "customer_name",
                "customer_address",
                "account_number",
                "invoice_number",
                "currency",
            }
        ),
        key_activities=_lpg_activities,
    ),
    "fuel_card_statement": KindSpec(
        key_model=FuelCardStatement,
        normalised_model=NormalisedFuelCardStatement,
        doc_glob="fuel_*.json",
        scored_fields=(
            "vendor_name",
            "vendor_uen",
            "customer_name",
            "customer_address",
            "account_number",
            "card_number",
            "statement_number",
            "document_date",
            "period_start",
            "period_end",
            "currency",
            "gross_amount_sgd",
            "discount_amount_sgd",
            "subtotal_sgd",
            "gst_rate",
            "gst_amount_sgd",
            "total_sgd",
        ),
        text_fields=frozenset(
            {
                "vendor_name",
                "vendor_uen",
                "customer_name",
                "customer_address",
                "account_number",
                "card_number",
                "statement_number",
                "currency",
            }
        ),
        key_activities=_fuel_activities,
    ),
}

# Prefix -> kind, so a doc id alone says which kind it belongs to (spec 3.2).
PREFIXES = {"elec": "electricity_bill", "lpg": "lpg_invoice", "fuel": "fuel_card_statement"}


def kind_of(doc_id: str) -> str:
    prefix = doc_id.split("_", 1)[0]
    if prefix not in PREFIXES:
        raise ValueError(f"{doc_id!r} has no known kind prefix; have {sorted(PREFIXES)}")
    return PREFIXES[prefix]


def documents_dir(kind: str) -> Path:
    return GENERATED / kind / "clean"


def _comparable(value, field: str, spec: KindSpec):
    if value is None:
        return None
    if field in spec.text_fields:
        return " ".join(str(value).split()).casefold()
    if isinstance(value, Decimal):
        return value
    return value


def compare_fields(key, reading, spec: KindSpec) -> dict[str, dict]:
    """Field-by-field verdict. Pure: no API, no database - unit-testable on its own."""
    verdicts: dict[str, dict] = {}
    for field in spec.scored_fields:
        expected = getattr(key, field, None)
        got = getattr(reading, field, None) if reading is not None else None
        verdicts[field] = {
            "correct": _comparable(expected, field, spec) == _comparable(got, field, spec),
            "expected": str(expected),
            "got": None if got is None else str(got),
        }
    return verdicts


def compare_activities(expected: dict[str, Decimal], found: list[dict]) -> dict:
    """Did the reading recover the same quantities, in the same units, as the key?

    Compared as a mapping of kind to amount rather than positionally: the order activities
    come back in is not a property worth scoring, but the set of them is.
    """
    got = {a["kind"]: Decimal(a["amount"]) for a in found}
    units = {a["kind"]: a["unit"] for a in found}
    from carbon_r.models.activity import KIND_UNITS

    units_right = all(units.get(kind) == KIND_UNITS.get(kind) for kind in got)
    return {
        "correct": got == expected and units_right,
        "expected": {k: str(v) for k, v in sorted(expected.items())},
        "got": {k: str(v) for k, v in sorted(got.items())},
        "units_right": units_right,
    }


def emissions_for(activities: dict[str, Decimal], factor_year: int | None) -> dict:
    """kgCO2e for a set of activities, or None with the reason. Same path for key and read.

    Returns `{kg_co2e, substituted, note, refused}`. `refused` marks an honest refusal -
    a factor that is not published - as distinct from a failure, so the report can score
    an expected refusal as expected.
    """
    if not activities or factor_year is None:
        return {
            "kg_co2e": None,
            "substituted": None,
            "note": "no activity or no period year was read",
            "refused": False,
        }

    pairs = []
    substituted = False
    for kind, amount in sorted(activities.items()):
        category, unit = FACTOR_SELECTORS[kind]
        factor = lookup_emission_factor(category, unit, factor_year)
        if factor.status != "ok":
            return {
                "kg_co2e": None,
                "substituted": None,
                "note": f"factor lookup {factor.status}: {factor.message}",
                # no_results is the honest answer when nothing is published at or before
                # the activity year; an error is not.
                "refused": factor.status == "no_results",
            }
        substituted = substituted or factor.data["factor"]["substituted"]
        from carbon_r.models.activity import KIND_UNITS

        pairs.append(
            {
                "activity": {
                    "kind": kind,
                    "amount": amount,
                    "unit": KIND_UNITS[kind],
                    "source_field": "eval",
                },
                "factor": factor.data["factor"],
            }
        )

    result = calculate_emissions(pairs=pairs)
    if result.status != "ok":
        return {
            "kg_co2e": None,
            "substituted": substituted,
            "note": f"calculation {result.status}: {result.message}",
            "refused": False,
        }
    return {
        "kg_co2e": result.data["kg_co2e"],
        "substituted": substituted,
        "note": "",
        "refused": False,
    }


def load_reading(pdf: Path, doc_id: str, *, model: str, dpi: int, use_cache: bool) -> tuple:
    """The model's raw classification and reading, from cache when unchanged.

    Both calls are cached together: a cached entry that held only the transcription would
    have to re-classify to know which schema to parse it with, which is the expensive half
    of what the cache exists to avoid.
    """
    path = REPLY_CACHE / cache_key(doc_id, model, dpi)
    if use_cache and path.exists():
        stored = json.loads(path.read_text())
        kind = stored["document_kind"]
        extracted = EXTRACTORS[kind]["schema"].model_validate(stored["printed"])
        return stored["classification"], kind, extracted, True

    classification = classify_page(pdf, dpi=dpi, model=model)
    kind, _ = classification.resolved()
    if kind not in EXTRACTORS:
        # A refusal is cached too: re-paying for a page the classifier declined would make
        # a re-run of the eval cost money for no new information.
        REPLY_CACHE.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "classification": classification.model_dump(),
                    "document_kind": kind,
                    "printed": None,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return classification.model_dump(), kind, None, False

    extracted = read_page(pdf, kind=kind, dpi=dpi, model=model)
    REPLY_CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "classification": classification.model_dump(),
                "document_kind": kind,
                "printed": extracted.model_dump(),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return classification.model_dump(), kind, extracted, False


def evaluate(doc_ids: list[str], *, model: str, dpi: int, use_cache: bool) -> dict:
    rows = []
    for doc_id in doc_ids:
        true_kind = kind_of(doc_id)
        spec = KINDS[true_kind]
        directory = documents_dir(true_kind)
        pdf = directory / f"{doc_id}.pdf"
        key = spec.key_model.model_validate_json((directory / f"{doc_id}.json").read_text())

        classification, read_kind, extracted, cached = load_reading(
            pdf, doc_id, model=model, dpi=dpi, use_cache=use_cache
        )

        expected_activities = spec.key_activities(key)
        from_key = emissions_for(expected_activities, key.factor_year)

        if extracted is None:
            # The classifier refused. Nothing was read, so there is nothing to score but
            # the routing itself.
            rows.append(
                {
                    "doc_id": doc_id,
                    "cached": cached,
                    "status": "no_results",
                    "true_kind": true_kind,
                    "read_kind": read_kind,
                    "kind_correct": False,
                    "classification": classification,
                    "fields": {},
                    "fields_correct": 0,
                    "fields_scored": 0,
                    "activity": {"correct": False, "expected": {}, "got": {}},
                    "field_problems": [],
                    "consistency_flags": [],
                    "expected_kg_co2e": from_key["kg_co2e"],
                    "actual_kg_co2e": None,
                    "expected_refusal": from_key["refused"],
                    "pipeline_exact": False,
                    "pipeline_note": f"classifier returned {read_kind}",
                    "factor_substituted": None,
                    "factor_year_expected": key.factor_year,
                    "factor_year_read": None,
                    "model_notes": None,
                }
            )
            continue

        result = build_result(extracted, pdf, kind=read_kind, dpi=dpi, model=model)
        reading = (
            KINDS[read_kind].normalised_model.model_validate(result.data["normalised"])
            if result.status == "ok"
            else None
        )

        # Fields are only comparable when the kind was right: a reading parsed with the
        # wrong schema has no fields in common with the key.
        kind_correct = read_kind == true_kind
        verdicts = compare_fields(key, reading, spec) if kind_correct else {}
        found = result.data.get("activities", []) if result.data else []
        activity = compare_activities(expected_activities, found)

        read_activities = {a["kind"]: Decimal(a["amount"]) for a in found}
        from_reading = emissions_for(read_activities, reading.factor_year if reading else None)
        expected_kg = from_key["kg_co2e"]
        actual_kg = from_reading["kg_co2e"]

        rows.append(
            {
                "doc_id": doc_id,
                "cached": cached,
                "status": result.status,
                "true_kind": true_kind,
                "read_kind": read_kind,
                "kind_correct": kind_correct,
                "classification": classification,
                "fields": verdicts,
                "fields_correct": sum(v["correct"] for v in verdicts.values()),
                "fields_scored": len(verdicts),
                "activity": activity,
                "field_problems": result.data.get("field_problems", []) if result.data else [],
                "consistency_flags": (
                    result.data.get("consistency_flags", []) if result.data else []
                ),
                "expected_kg_co2e": expected_kg,
                "actual_kg_co2e": actual_kg,
                # When the key itself cannot be costed - no factor published for the
                # activity year - the honest result is no figure, and matching that is a
                # pass rather than a miss.
                "expected_refusal": from_key["refused"],
                "pipeline_exact": (
                    (expected_kg is None and actual_kg is None and from_key["refused"])
                    or (
                        expected_kg is not None
                        and actual_kg is not None
                        and Decimal(expected_kg) == Decimal(actual_kg)
                    )
                ),
                "pipeline_note": from_reading["note"],
                "factor_substituted": from_reading["substituted"],
                "factor_year_expected": key.factor_year,
                "factor_year_read": reading.factor_year if reading else None,
                "model_notes": getattr(extracted, "notes", None),
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

    kind_correct = sum(r["kind_correct"] for r in rows)
    activity_correct = sum(r["activity"]["correct"] for r in rows)
    pipeline_exact = sum(r["pipeline_exact"] for r in rows)
    refusals = sum(r["expected_refusal"] for r in rows)

    print("\nRouting and activity")
    print(f"  document kind correct   {kind_correct:3} of {total}")
    print(f"  activity recovered      {activity_correct:3} of {total}")

    worst: list[str] = []
    for kind, spec in KINDS.items():
        in_kind = [r for r in rows if r["true_kind"] == kind]
        if not in_kind:
            continue
        print(f"\n{kind} - {len(in_kind)} documents")
        for field in spec.scored_fields:
            scored = [r for r in in_kind if field in r["fields"]]
            correct = sum(r["fields"][field]["correct"] for r in scored)
            marker = "" if correct == len(in_kind) else "   <-"
            print(f"  {field:24} {correct:3} of {len(in_kind)}{marker}")
            if correct != len(in_kind):
                worst.append(f"{kind}.{field}")
        every = sum(
            r["fields_scored"] > 0 and r["fields_correct"] == r["fields_scored"] for r in in_kind
        )
        print(f"  {'every field correct':24} {every:3} of {len(in_kind)}")
        print(
            f"  {'activity recovered':24} "
            f"{sum(r['activity']['correct'] for r in in_kind):3} of {len(in_kind)}"
        )
        print(
            f"  {'pipeline kgCO2e exact':24} "
            f"{sum(r['pipeline_exact'] for r in in_kind):3} of {len(in_kind)}"
        )

    substituted = sum(1 for r in rows if r["factor_substituted"])
    print(f"\n  pipeline kgCO2e exact   {pipeline_exact:3} of {total}")
    print(f"  factor-year substituted {substituted:3} of {total}")
    print(
        f"  expected refusals       {refusals:3} of {total} "
        "(no factor published at or before the activity year)"
    )

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
        if not row["kind_correct"]:
            print(
                f"\n  MISROUTED {row['doc_id']}: read as {row['read_kind']}, "
                f"is {row['true_kind']} ({row['classification'].get('why')})"
            )
        if not row["activity"]["correct"]:
            print(
                f"\n  activity miss {row['doc_id']}: expected {row['activity']['expected']}, "
                f"got {row['activity']['got']}"
            )
        if not row["pipeline_exact"]:
            print(
                f"\n  pipeline miss {row['doc_id']}: expected {row['expected_kg_co2e']} kg, "
                f"got {row['actual_kg_co2e']} kg ({row['pipeline_note'] or 'figures differ'})"
            )
        elif row["expected_refusal"]:
            print(
                f"  expected refusal {row['doc_id']}: no figure, correctly - {row['pipeline_note']}"
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

    return 0 if (pipeline_exact == total and kind_correct == total) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docs", help="comma-separated doc ids; default every document")
    parser.add_argument(
        "--kind", choices=[*sorted(KINDS), "all"], default="all", help="limit to one kind"
    )
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

    if args.docs:
        doc_ids = [d.strip() for d in args.docs.split(",") if d.strip()]
    else:
        kinds = sorted(KINDS) if args.kind == "all" else [args.kind]
        doc_ids = []
        for kind in kinds:
            directory = documents_dir(kind)
            if not directory.exists():
                print(f"No generated documents at {directory}. Run `make docs`.", file=sys.stderr)
                return 2
            doc_ids += sorted(p.stem for p in directory.glob(KINDS[kind].doc_glob))
    if args.limit:
        doc_ids = doc_ids[: args.limit]
    if not doc_ids:
        print("No documents selected.", file=sys.stderr)
        return 2

    run = evaluate(doc_ids, model=model, dpi=args.dpi, use_cache=not args.no_cache)
    return report(run)


if __name__ == "__main__":
    raise SystemExit(main())
