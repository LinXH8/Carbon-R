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

Document sets (Tier 2 Block 2)
------------------------------
The default set is synthgen v1 at `data/generated/synthgen_v1/`: three tiers, four layouts
per kind, quirks. Documents are found through each kind's `manifest.json`, whose `file`
names the page to read - a PNG for a scanned or photographed document. `--root
data/generated` reads the Block 1 set instead; its runs stay in their own directory.

**The holdout layout is excluded by default.** Layout `d` of each kind is never looked at
while tuning, and is scored separately in Block 3 with `--include-holdout`.

Activities are compared by (kind, factor year), not by kind alone: a bill across 31
December is two activities, and getting the split wrong is a different failure from
reading the wrong kWh.

Every run records the git commit (and whether the tree was dirty), the seed and generator
versions from the manifests, the model and the request hash - so a figure can be traced
to exactly the code and documents that produced it.

Counts, never bare percentages (CLAUDE.md): "17 of 18" is a claim that can be checked.
"""

import argparse
import json
import subprocess
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
V1_ROOT = GENERATED / "synthgen_v1"
# Run records are kept per DOCUMENT SET, not in one pooled directory - an accuracy figure
# is only meaningful against the set it was measured on, and pooling two sets would
# silently mix them. The Block 1 set (one layout, clean) keeps its own directory.
RUNS_DIRS = {
    V1_ROOT: GENERATED / "eval_runs_TIER2_BLOCK2",
    GENERATED: GENERATED / "eval_runs_TIER2_BLOCK1",
}


def reply_cache(root: Path) -> Path:
    """Cached model replies, per document set: v1 reuses Block 1's document ids for
    different documents, so one shared cache could replay a Block 1 reply against a v1 key."""
    return root / ".eval-cache"


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
    # Fields that are BOTH printed on the page and present in the answer key. Fields the
    # generator knows but the page does not show (seed, layout, degradation) are not
    # extractable and are not scored.
    scored_fields: tuple[str, ...]
    # Compared on whitespace and case only - never on content. Declared rather than
    # inferred, so the strictness of the score is visible: money and dates are exact.
    text_fields: frozenset[str]
    # How to read the truth's activities, as {(kind, factor year): amount}.
    key_activities: Any


ActivityKey = tuple[str, int]


def _electricity_activities(key: ElectricityBill) -> dict[ActivityKey, Decimal]:
    """One entry per calendar year: a bill across 31 December is two activities."""
    split = key.split_kwh_by_year or {key.factor_year: key.consumption_kwh}
    return {("electricity", year): Decimal(kwh) for year, kwh in split.items()}


def _lpg_activities(key: LPGInvoice) -> dict[ActivityKey, Decimal]:
    return {("lpg", key.factor_year): key.lpg_kg}


def _fuel_activities(key: FuelCardStatement) -> dict[ActivityKey, Decimal]:
    """Litres per ACTIVITY KIND, so two octane grades collapse as the pipeline collapses
    them. Keyed by kind rather than by printed product name for exactly that reason."""
    from carbon_r.models.extraction_fuel import kind_for_product

    totals: dict[ActivityKey, Decimal] = {}
    for product, litres in key.litres_by_product.items():
        slot = (kind_for_product(product), key.factor_year)
        totals[slot] = totals.get(slot, Decimal(0)) + litres
    return totals


KINDS: dict[str, KindSpec] = {
    "electricity_bill": KindSpec(
        key_model=ElectricityBill,
        normalised_model=NormalisedBill,
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
            "credit_adjustment_sgd",
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
            "credit_adjustment_sgd",
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
            "credit_adjustment_sgd",
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


@dataclass(frozen=True)
class DocRef:
    """One document to score: where its page and its key are, and whether it is held out."""

    doc_id: str
    page: Path
    key: Path
    is_holdout: bool = False


def discover(root: Path, kind: str) -> list[DocRef]:
    """Every document of one kind in a set, through its manifest's `file` entries.

    The Block 1 manifest predates `file`; its documents are clean PDFs beside their keys.
    """
    manifest_path = root / kind / "manifest.json"
    if not manifest_path.exists():
        return []
    refs = []
    for entry in json.loads(manifest_path.read_text())["documents"]:
        page = root / kind / entry.get("file", f"clean/{entry['doc_id']}.pdf")
        refs.append(
            DocRef(
                doc_id=entry["doc_id"],
                page=page,
                key=page.parent / f"{entry['doc_id']}.json",
                is_holdout=entry.get("is_holdout_layout", False),
            )
        )
    return refs


def set_provenance(root: Path) -> dict:
    """Seed and generator version per kind, from the manifests - for the run record."""
    out = {}
    for kind in KINDS:
        path = root / kind / "manifest.json"
        if path.exists():
            manifest = json.loads(path.read_text())
            out[kind] = {
                "seed": manifest["seed"],
                "generator_version": manifest["generator_version"],
                "document_count": manifest["document_count"],
            }
    return out


def git_state() -> dict:
    """The commit the run measured, and whether uncommitted changes could have touched it."""

    def run(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=PROJECT_ROOT, capture_output=True, text=True, check=False
        ).stdout.strip()

    return {"commit": run("rev-parse", "HEAD"), "dirty": bool(run("status", "--porcelain"))}


# A field that is absent on a document where the key holds zero: a page with no credit
# prints no credit line, and a reading that returns null for it has read the page right.
ZERO_WHEN_ABSENT = frozenset({"credit_adjustment_sgd"})


def _comparable(value, field: str, spec: KindSpec):
    if field in ZERO_WHEN_ABSENT and (value is None or value == 0):
        return Decimal(0)
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


def read_activities(found: list[dict]) -> dict[ActivityKey, Decimal]:
    """The reading's activities as {(kind, factor year): amount}. Two lines of one kind in
    one year (two octane grades) are summed, as for the key."""
    got: dict[ActivityKey, Decimal] = {}
    for activity in found:
        slot = (activity["kind"], activity.get("factor_year"))
        got[slot] = got.get(slot, Decimal(0)) + Decimal(activity["amount"])
    return got


def _label(slot: ActivityKey) -> str:
    return f"{slot[0]} {slot[1]}"


def compare_activities(expected: dict[ActivityKey, Decimal], found: list[dict]) -> dict:
    """Did the reading recover the same quantities, in the same units and years, as the key?

    Compared as a mapping of (kind, year) to amount rather than positionally: the order
    activities come back in is not a property worth scoring, but the set of them is - and
    since Tier 2 Block 2 the year is part of it, because a cross-year bill split wrongly
    is a wrong activity even when its kWh total is right.
    """
    got = read_activities(found)
    from carbon_r.models.activity import KIND_UNITS

    units_right = all(a["unit"] == KIND_UNITS.get(a["kind"]) for a in found)
    return {
        "correct": got == expected and units_right,
        "expected": {_label(k): str(v) for k, v in sorted(expected.items(), key=str)},
        "got": {_label(k): str(v) for k, v in sorted(got.items(), key=str)},
        "units_right": units_right,
    }


def emissions_for(activities: dict[ActivityKey, Decimal]) -> dict:
    """kgCO2e for a set of activities, or None with the reason. Same path for key and read.

    Returns `{kg_co2e, substituted, note, refused}`. `refused` marks an honest refusal -
    a factor that is not published - as distinct from a failure, so the report can score
    an expected refusal as expected.
    """
    if not activities or any(year is None for _, year in activities):
        return {
            "kg_co2e": None,
            "substituted": None,
            "note": "no activity or no period year was read",
            "refused": False,
        }

    pairs = []
    substituted = False
    for (kind, factor_year), amount in sorted(activities.items()):
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
                    "factor_year": factor_year,
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


def load_reading(
    pdf: Path, doc_id: str, *, model: str, dpi: int, use_cache: bool, cache_dir: Path
) -> tuple:
    """The model's raw classification and reading, from cache when unchanged.

    Both calls are cached together: a cached entry that held only the transcription would
    have to re-classify to know which schema to parse it with, which is the expensive half
    of what the cache exists to avoid.
    """
    path = cache_dir / cache_key(doc_id, model, dpi)
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
        cache_dir.mkdir(parents=True, exist_ok=True)
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
    cache_dir.mkdir(parents=True, exist_ok=True)
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


def evaluate(
    refs: list[DocRef], *, model: str, dpi: int, use_cache: bool, root: Path = V1_ROOT
) -> dict:
    rows = []
    for ref in refs:
        doc_id = ref.doc_id
        true_kind = kind_of(doc_id)
        spec = KINDS[true_kind]
        pdf = ref.page
        key = spec.key_model.model_validate_json(ref.key.read_text())

        classification, read_kind, extracted, cached = load_reading(
            pdf, doc_id, model=model, dpi=dpi, use_cache=use_cache, cache_dir=reply_cache(root)
        )

        expected_activities = spec.key_activities(key)
        from_key = emissions_for(expected_activities)
        dress = {
            "file": str(pdf.relative_to(root)) if pdf.is_relative_to(root) else str(pdf),
            "layout_id": key.layout_id,
            "is_holdout_layout": key.is_holdout_layout,
            "difficulty_tier": key.difficulty_tier,
            "quirks": list(key.quirks),
        }

        if extracted is None:
            # The classifier refused. Nothing was read, so there is nothing to score but
            # the routing itself.
            rows.append(
                {
                    "doc_id": doc_id,
                    **dress,
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

        from_reading = emissions_for(read_activities(found))
        expected_kg = from_key["kg_co2e"]
        actual_kg = from_reading["kg_co2e"]

        rows.append(
            {
                "doc_id": doc_id,
                **dress,
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
        "document_set": str(root.relative_to(PROJECT_ROOT)),
        "set_provenance": set_provenance(root),
        "git": git_state(),
        "holdout_included": any(ref.is_holdout for ref in refs),
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

    print(f"\nCarbon R extraction eval - {total} documents from {run['document_set']}")
    git = run["git"]
    dirty = " (DIRTY TREE - not a clean measurement)" if git["dirty"] else ""
    print(
        f"  commit {git['commit'][:7]}{dirty}"
        f"  seed {sorted({p['seed'] for p in run['set_provenance'].values()})}"
        f"  holdout {'INCLUDED' if run['holdout_included'] else 'excluded'}"
    )
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

    runs_dir = RUNS_DIRS.get(PROJECT_ROOT / run["document_set"], GENERATED / "eval_runs_other")
    runs_dir.mkdir(parents=True, exist_ok=True)
    stamp = run["run_at"].replace(":", "").replace("-", "")
    path = runs_dir / f"{stamp}-{run['request']['request_hash']}.json"
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
        "--root",
        type=Path,
        default=V1_ROOT,
        help="document set to score (default synthgen v1; data/generated for Block 1)",
    )
    parser.add_argument(
        "--include-holdout",
        action="store_true",
        help="also score the held-out layout (Block 3; never while tuning)",
    )
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

    root = args.root if args.root.is_absolute() else PROJECT_ROOT / args.root
    kinds = sorted(KINDS) if args.kind == "all" else [args.kind]
    refs = [ref for kind in kinds for ref in discover(root, kind)]
    if not refs:
        print(f"No generated documents under {root}. Run `make docs`.", file=sys.stderr)
        return 2
    refs = select(refs, docs=args.docs, include_holdout=args.include_holdout)
    if args.limit:
        refs = refs[: args.limit]
    if not refs:
        print("No documents selected.", file=sys.stderr)
        return 2

    run = evaluate(refs, model=model, dpi=args.dpi, use_cache=not args.no_cache, root=root)
    return report(run)


def select(refs: list[DocRef], *, docs: str | None, include_holdout: bool) -> list[DocRef]:
    """Narrow to named documents, and drop the holdout layout unless asked for.

    A named holdout document is still dropped without `--include-holdout`: naming it is
    exactly how a holdout leaks into tuning.
    """
    if docs:
        wanted = {d.strip() for d in docs.split(",") if d.strip()}
        refs = [ref for ref in refs if ref.doc_id in wanted]
    if not include_holdout:
        refs = [ref for ref in refs if not ref.is_holdout]
    return refs


if __name__ == "__main__":
    raise SystemExit(main())
