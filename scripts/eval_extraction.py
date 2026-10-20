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
import dataclasses
import functools
import hashlib
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
from carbon_r.synthgen.plan import TIERS
from carbon_r.tools.calculate import calculate_emissions
from carbon_r.tools.extract import (
    DEFAULT_DPI,
    EXTRACTORS,
    IMAGE_DETAIL,
    REQUEST_HASH,
    RETRY_HASH,
    build_result,
    cache_key,
    classify_page,
    needs_retry,
    read_page,
    reread_page,
    retry_message,
    with_retry,
)
from carbon_r.tools.factors import lookup_emission_factor

GENERATED = PROJECT_ROOT / "data" / "generated"
V1_ROOT = GENERATED / "synthgen_v1"
# Run records are kept per DOCUMENT SET, not in one pooled directory - an accuracy figure
# is only meaningful against the set it was measured on, and pooling two sets would
# silently mix them. The Block 1 set (one layout, clean) keeps its own directory.
RUNS_DIRS = {
    V1_ROOT: GENERATED / "eval_runs_TIER2_BLOCK2",
    # The held-out layout, scored once at the end of Block 3 and never tuned on.
    (V1_ROOT, "holdout"): GENERATED / "eval_runs_TIER2_BLOCK3_HOLDOUT",
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
    # Reading field -> answer-key field, where the two models name one printed value
    # differently. Without this the key side was looked up by the READING's name, found
    # nothing, and - through ZERO_WHEN_ABSENT - scored as 0 (the Block 2 credit defect).
    key_fields: dict[str, str] = dataclasses.field(default_factory=dict)

    def key_field(self, name: str) -> str:
        return self.key_fields.get(name, name)


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
        key_fields={"credit_adjustment_sgd": "credit_amount_sgd"},
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
        key_fields={"credit_adjustment_sgd": "credit_amount_sgd"},
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
        key_fields={"credit_adjustment_sgd": "credit_amount_sgd"},
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


# A READ field that may be absent where the key holds zero: a page with no credit prints
# no credit line, and a reading that returns null for it has read the page right. Applied
# to the reading only - an absent EXPECTED value raises instead (see `compare_fields`).
ZERO_WHEN_ABSENT = frozenset({"credit_adjustment_sgd"})


def _comparable(value, field: str, spec: KindSpec, *, read_side: bool = False):
    if read_side and field in ZERO_WHEN_ABSENT and value is None:
        return Decimal(0)
    if value is None:
        return None
    if field in spec.text_fields:
        return " ".join(str(value).split()).casefold()
    if isinstance(value, Decimal):
        return value
    return value


def compare_fields(key, reading, spec: KindSpec) -> dict[str, dict]:
    """Field-by-field verdict. Pure: no API, no database - unit-testable on its own.

    The expected side must exist on the key. A comparison that defaults a missing expected
    value can never fail loudly, and that is how the credit column scored nothing in
    Block 2.
    """
    verdicts: dict[str, dict] = {}
    for field in spec.scored_fields:
        key_name = spec.key_field(field)
        if key_name not in type(key).model_fields:
            raise KeyError(f"{type(key).__name__} has no field {key_name!r} (scored as {field!r})")
        expected = getattr(key, key_name)
        got = getattr(reading, field, None) if reading is not None else None
        verdicts[field] = {
            "correct": _comparable(expected, field, spec)
            == _comparable(got, field, spec, read_side=True),
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


_FACTORS: dict[tuple[str, str, int], Any] = {}
# Lookup attempts that came back `error` and were retried, for the run record.
LOOKUP_RETRIES: list[str] = []
LOOKUP_ATTEMPTS = 3


class LookupFailed(RuntimeError):
    """The database failed, repeatedly. The run stops: it would not be a measurement."""


def _factor(category: str, unit: str, year: int):
    """One exact-SQL lookup per (category, unit, year) per run. Each lookup opens its own
    connection to the database, and a 166-document re-score made several hundred of them
    for a handful of distinct keys. The factor table does not change during a run, so an
    answer - `ok` or `no_results` - cannot either.

    An `error` is never memoised and never scored. It is a fact about the connection, not
    about the factor: caching one replayed a single dropped connection onto ten bills, and
    scoring one turned a key-side failure into a "figures differ" pipeline miss (both in
    Block 3, A2). It is retried, recorded, and after LOOKUP_ATTEMPTS the run aborts.
    """
    key = (category, unit, year)
    if key in _FACTORS:
        return _FACTORS[key]
    for _attempt in range(LOOKUP_ATTEMPTS):
        result = lookup_emission_factor(category, unit, year)
        if result.status != "error":
            _FACTORS[key] = result
            return result
        LOOKUP_RETRIES.append(f"{category} {unit} {year}: {result.message[:120]}")
    raise LookupFailed(
        f"factor lookup for {key} failed {LOOKUP_ATTEMPTS} times; last: {result.message}"
    )


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
        factor = _factor(category, unit, factor_year)
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
        # A hit costs nothing now; the usage it cost when fresh is kept with the entry
        # (absent on entries written before Block 3).
        return stored["classification"], kind, extracted, True, stored.get("usage")

    usage: list[dict] = []
    classification = classify_page(pdf, dpi=dpi, model=model, usage=usage)
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
                    "usage": usage,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return classification.model_dump(), kind, None, False, usage

    extracted = read_page(pdf, kind=kind, dpi=dpi, model=model, usage=usage)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "classification": classification.model_dump(),
                "document_kind": kind,
                "printed": extracted.model_dump(),
                "usage": usage,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return classification.model_dump(), kind, extracted, False, usage


def _score(result, *, key, spec, read_kind, kind_correct, expected_activities, from_key) -> dict:
    """Score one reading against the key. Called for the first reading and, when a retry
    ran, for the reading that stands - so the two are scored by the same code."""
    reading = (
        KINDS[read_kind].normalised_model.model_validate(result.data["normalised"])
        if result.status == "ok"
        else None
    )
    verdicts = compare_fields(key, reading, spec) if kind_correct else {}
    found = result.data.get("activities", []) if result.data else []
    from_reading = emissions_for(read_activities(found))
    expected_kg = from_key["kg_co2e"]
    actual_kg = from_reading["kg_co2e"]
    return {
        "reading": reading,
        "verdicts": verdicts,
        "activity": compare_activities(expected_activities, found),
        "actual_kg": actual_kg,
        "pipeline_exact": (
            (expected_kg is None and actual_kg is None and from_key["refused"])
            or (
                expected_kg is not None
                and actual_kg is not None
                and Decimal(expected_kg) == Decimal(actual_kg)
            )
        ),
        "note": from_reading["note"],
        "substituted": from_reading["substituted"],
        "found": found,
    }


BUCKETS = {
    (False, True): "wrong_to_correct",
    (True, False): "correct_to_wrong",
    (False, False): "wrong_to_wrong",
    (True, True): "correct_to_correct",
}


def retry_buckets(before: dict, after: dict, changed: list[str]) -> dict[str, list[str]]:
    """Each field the retry changed, by whether it was right before and after, against
    the key. `correct_to_wrong` is a true value the retry destroyed - the cost the retry
    rule is most at risk of, and reported first. A changed field the eval does not score
    (a transaction list, a charge line) is listed as `unscored`, never dropped."""
    out: dict[str, list[str]] = {name: [] for name in BUCKETS.values()} | {"unscored": []}
    for name in changed:
        if name not in before or name not in after:
            out["unscored"].append(name)
            continue
        out[BUCKETS[(before[name]["correct"], after[name]["correct"])]].append(name)
    return out


def load_retry(
    pdf: Path,
    doc_id: str,
    first,
    *,
    kind: str,
    model: str,
    dpi: int,
    use_cache: bool,
    cache_dir: Path,
):
    """The retry's reading, from cache when the same retry was asked before.

    Keyed by the first reading's key, the retry wording's hash, and a digest of the exact
    message - which depends on what the CURRENT normaliser found wrong. A normaliser fix
    that changes the failures therefore asks a different retry, and misses."""
    message = retry_message(first)
    digest = hashlib.sha256(message.encode()).hexdigest()[:8]
    stem = cache_key(doc_id, model, dpi).removesuffix(".json")
    path = cache_dir / f"{stem}-retry-{RETRY_HASH}-{digest}.json"
    if use_cache and path.exists():
        stored = json.loads(path.read_text())
        return EXTRACTORS[kind]["schema"].model_validate(stored["printed"]), True, stored["usage"]
    usage: list[dict] = []
    second = reread_page(pdf, kind=kind, message=message, dpi=dpi, model=model, usage=usage)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"message": message, "printed": second.model_dump(), "usage": usage}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    return second, False, usage


def evaluate(
    refs: list[DocRef],
    *,
    model: str,
    dpi: int,
    use_cache: bool,
    root: Path = V1_ROOT,
    retry_enabled: bool = True,
) -> dict:
    rows = []
    for ref in refs:
        doc_id = ref.doc_id
        true_kind = kind_of(doc_id)
        spec = KINDS[true_kind]
        pdf = ref.page
        key = spec.key_model.model_validate_json(ref.key.read_text())

        classification, read_kind, extracted, cached, usage = load_reading(
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
                    "usage": usage,
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

        first = build_result(extracted, pdf, kind=read_kind, dpi=dpi, model=model)
        # Fields are only comparable when the kind was right: a reading parsed with the
        # wrong schema has no fields in common with the key.
        kind_correct = read_kind == true_kind
        score = functools.partial(
            _score,
            key=key,
            spec=spec,
            read_kind=read_kind,
            kind_correct=kind_correct,
            expected_activities=expected_activities,
            from_key=from_key,
        )
        before = score(first)
        result, after, retry = first, before, None
        if retry_enabled and needs_retry(first):
            second, retry_cached, retry_usage = load_retry(
                pdf,
                doc_id,
                first,
                kind=read_kind,
                model=model,
                dpi=dpi,
                use_cache=use_cache,
                cache_dir=reply_cache(root),
            )
            result = with_retry(first, second, pdf, kind=read_kind, dpi=dpi, model=model)
            after = score(result)
            retry = {
                "cached": retry_cached,
                "usage": retry_usage,
                "adopted": result.data["retry"]["adopted"],
                "changed_on_retry": result.data["changed_on_retry"],
                "buckets": retry_buckets(
                    before["verdicts"], after["verdicts"], result.data["changed_on_retry"]
                ),
                "activity_before": before["activity"]["correct"],
                "activity_after": after["activity"]["correct"],
                "pipeline_before": before["pipeline_exact"],
                "pipeline_after": after["pipeline_exact"],
            }

        rows.append(
            {
                "doc_id": doc_id,
                **dress,
                "cached": cached,
                "usage": usage,
                "status": result.status,
                "true_kind": true_kind,
                "read_kind": read_kind,
                "kind_correct": kind_correct,
                "classification": classification,
                "fields": after["verdicts"],
                "fields_correct": sum(v["correct"] for v in after["verdicts"].values()),
                "fields_scored": len(after["verdicts"]),
                "activity": after["activity"],
                "field_problems": result.data.get("field_problems", []) if result.data else [],
                "consistency_flags": (
                    result.data.get("consistency_flags", []) if result.data else []
                ),
                "inconsistent": bool(result.data and result.data.get("inconsistent")),
                "expected_kg_co2e": from_key["kg_co2e"],
                "actual_kg_co2e": after["actual_kg"],
                # When the key itself cannot be costed - no factor published for the
                # activity year - the honest result is no figure, and matching that is a
                # pass rather than a miss.
                "expected_refusal": from_key["refused"],
                "pipeline_exact": after["pipeline_exact"],
                "pipeline_note": after["note"],
                "factor_substituted": after["substituted"],
                "factor_year_expected": key.factor_year,
                "factor_year_read": after["reading"].factor_year if after["reading"] else None,
                "model_notes": getattr(extracted, "notes", None),
                # The same document scored on the first reading alone, so "with retry" and
                # "without retry" come from one run and differ only by the retry.
                "without_retry": {
                    "fields": before["verdicts"],
                    "fields_correct": sum(v["correct"] for v in before["verdicts"].values()),
                    "fields_scored": len(before["verdicts"]),
                    "activity_correct": before["activity"]["correct"],
                    "pipeline_exact": before["pipeline_exact"],
                },
                "retry": retry,
                # What was READ, for a caller that computes from the reading rather than
                # scoring it - the company-year end-to-end run (eval_scenarios.py).
                "read": {
                    "activities": after["found"],
                    "period_start": (result.data or {}).get("normalised", {}).get("period_start"),
                    "period_end": (result.data or {}).get("normalised", {}).get("period_end"),
                    "is_estimated": bool(
                        (result.data or {}).get("normalised", {}).get("is_estimated_reading")
                    ),
                    "currency": (result.data or {}).get("normalised", {}).get("currency"),
                },
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
            "retry_hash": RETRY_HASH if retry_enabled else None,
            "cache_enabled": use_cache,
        },
        "document_count": len(rows),
        "documents": rows,
        "summary": summarise(rows),
        # Database errors retried during the run. Non-empty means the connection dropped
        # and was retried successfully; it never means a row was scored on an error.
        "factor_lookup_retries": list(LOOKUP_RETRIES),
    }


# Below this many documents a cell is reported, but marked: the quirk x kind cells of v1
# sit at or near the floor of 15 (TIER2_BLOCK2.md, "Known limits"), and several moderate
# cells hold 4 to 7. A count that small is a description of those documents, not a rate.
SMALL_N = 15

MEASURES = ("every_field", "activity", "pipeline")


def _cell(rows: list[dict]) -> dict:
    return {
        "documents": len(rows),
        "every_field": sum(
            r["fields_scored"] > 0 and r["fields_correct"] == r["fields_scored"] for r in rows
        ),
        "activity": sum(r["activity"]["correct"] for r in rows),
        "pipeline": sum(r["pipeline_exact"] for r in rows),
        "small_n": len(rows) < SMALL_N,
    }


def summarise(rows: list[dict]) -> dict:
    """Counts per (tier, kind) and per (quirk, kind). Pure, so it is unit-testable.

    A document with three quirks counts once in each of its three quirk rows, so the quirk
    table's rows do not sum to the document count - and are not meant to.
    """
    by_tier: dict[str, dict[str, dict]] = {}
    by_quirk: dict[str, dict[str, dict]] = {}
    for kind in KINDS:
        in_kind = [r for r in rows if r["true_kind"] == kind]
        present = {r["difficulty_tier"] for r in in_kind}
        for tier in [t for t in TIERS if t in present] + sorted(present - set(TIERS)):
            by_tier.setdefault(tier, {})[kind] = _cell(
                [r for r in in_kind if r["difficulty_tier"] == tier]
            )
        for quirk in sorted({q for r in in_kind for q in r["quirks"]}):
            by_quirk.setdefault(quirk, {})[kind] = _cell(
                [r for r in in_kind if quirk in r["quirks"]]
            )
    return {
        "small_n_below": SMALL_N,
        "by_tier": by_tier,
        "by_quirk": by_quirk,
        "tokens_this_run": tokens(rows),
        "without_retry": {
            kind: _without_retry_cell([r for r in rows if r["true_kind"] == kind])
            for kind in KINDS
            if any(r["true_kind"] == kind for r in rows)
        },
        "retry": retry_summary(rows),
    }


def _without_retry_cell(rows: list[dict]) -> dict:
    """The kind's headline counts from the first readings alone."""
    first = [r.get("without_retry") or {} for r in rows]
    return {
        "documents": len(rows),
        "every_field": sum(
            f.get("fields_scored", 0) > 0 and f["fields_correct"] == f["fields_scored"]
            for f in first
        ),
        "activity": sum(bool(f.get("activity_correct")) for f in first),
        "pipeline": sum(bool(f.get("pipeline_exact")) for f in first),
    }


def retry_summary(rows: list[dict]) -> dict:
    retried = [r for r in rows if r.get("retry")]
    totals = {name: 0 for name in [*BUCKETS.values(), "unscored"]}
    for row in retried:
        for name, fields in row["retry"]["buckets"].items():
            totals[name] += len(fields)
    return {
        "triggered": len(retried),
        "adopted": sum(r["retry"]["adopted"] for r in retried),
        "changed_a_value": sum(bool(r["retry"]["changed_on_retry"]) for r in retried),
        "field_buckets": totals,
        "activity_correct_to_wrong": sum(
            r["retry"]["activity_before"] and not r["retry"]["activity_after"] for r in retried
        ),
        "activity_wrong_to_correct": sum(
            not r["retry"]["activity_before"] and r["retry"]["activity_after"] for r in retried
        ),
        "pipeline_correct_to_wrong": sum(
            r["retry"]["pipeline_before"] and not r["retry"]["pipeline_after"] for r in retried
        ),
        "still_inconsistent": sum(r.get("inconsistent", False) for r in retried),
        "documents": {r["doc_id"]: r["retry"]["buckets"] for r in retried},
    }


def tokens(rows) -> dict:
    """What this run cost: the recorded calls of fresh first readings and fresh retries.
    A cached reading or retry costs nothing now, so it is not counted."""
    calls = [u for r in rows if not r["cached"] for u in (r.get("usage") or [])]
    calls += [
        u
        for r in rows
        if r.get("retry") and not r["retry"]["cached"]
        for u in (r["retry"]["usage"] or [])
    ]
    return {
        "calls": len(calls),
        "input_tokens": sum(u.get("input_tokens", 0) for u in calls),
        "output_tokens": sum(u.get("output_tokens", 0) for u in calls),
    }


def _print_table(title: str, table: dict[str, dict[str, dict]]) -> None:
    print(f"\n{title}  (every field / activity / pipeline, as counts; * = fewer than {SMALL_N})")
    kinds = [k for k in KINDS if any(k in row for row in table.values())]
    print(f"  {'':22}" + "".join(f"{k:>30}" for k in kinds))
    for name, cells in table.items():
        line = f"  {name:22}"
        for kind in kinds:
            cell = cells.get(kind)
            if cell is None:
                line += f"{'-':>30}"
                continue
            n = cell["documents"]
            text = " / ".join(f"{cell[m]}" for m in MEASURES) + f" of {n}"
            line += f"{text + ('*' if cell['small_n'] else ' '):>30}"
        print(line)


def _print_retry(summary: dict, rows: list[dict]) -> None:
    retry = summary.get("retry")
    if not retry or not retry["triggered"]:
        print("\nRetry: none triggered")
        return
    b = retry["field_buckets"]
    print(
        f"\nRetry: {retry['triggered']} triggered, {retry['adopted']} adopted, "
        f"{retry['changed_a_value']} changed a value, "
        f"{retry['still_inconsistent']} still inconsistent"
    )
    # correct->wrong first: a true value the retry destroyed is the cost that matters.
    marker = "   <- TRUE VALUES OVERWRITTEN" if b["correct_to_wrong"] else ""
    print(f"  fields correct -> wrong   {b['correct_to_wrong']:3}{marker}")
    print(f"  fields wrong -> correct   {b['wrong_to_correct']:3}")
    print(f"  fields wrong -> wrong     {b['wrong_to_wrong']:3}")
    print(f"  changed, not scored       {b['unscored']:3}  (transaction rows, charge lines)")
    print(
        f"  activity correct -> wrong {retry['activity_correct_to_wrong']:3}   "
        f"wrong -> correct {retry['activity_wrong_to_correct']}   "
        f"pipeline correct -> wrong {retry['pipeline_correct_to_wrong']}"
    )
    for doc_id, buckets in retry["documents"].items():
        named = "; ".join(f"{k} {', '.join(v)}" for k, v in buckets.items() if v)
        print(f"  {doc_id}: {named or 'no change adopted'}")
    print("\nWithout retry vs with retry (every field / activity / pipeline)")
    for kind, cell in summary["without_retry"].items():
        in_kind = [r for r in rows if r["true_kind"] == kind]
        now = _cell(in_kind)
        print(
            f"  {kind:22} {cell['every_field']} / {cell['activity']} / {cell['pipeline']}  ->  "
            f"{now['every_field']} / {now['activity']} / {now['pipeline']}  of {cell['documents']}"
        )


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
    retried = [r for r in rows if r.get("retry")]
    if retried:
        fresh_retries = sum(not r["retry"]["cached"] for r in retried)
        print(
            f"  retries {run['request'].get('retry_hash')}: "
            f"{len(retried) - fresh_retries} cached, {fresh_retries} fresh"
        )

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

    summary = run.get("summary") or summarise(rows)
    _print_table("Per tier", summary["by_tier"])
    _print_table("Per quirk", summary["by_quirk"])
    _print_retry(summary, rows)

    if run.get("factor_lookup_retries"):
        print(
            f"\nFactor lookups retried after a database error: {len(run['factor_lookup_retries'])}"
        )
    spent = summary["tokens_this_run"]
    print(
        f"\nTokens this run: {spent['calls']} calls, {spent['input_tokens']} in, "
        f"{spent['output_tokens']} out (cached documents cost nothing)"
    )

    if worst:
        print(f"\nWeakest fields: {', '.join(worst)}")

    set_root = PROJECT_ROOT / run["document_set"]
    if run.get("holdout_only"):
        runs_dir = RUNS_DIRS.get((set_root, "holdout"), GENERATED / "eval_runs_other")
    else:
        runs_dir = RUNS_DIRS.get(set_root, GENERATED / "eval_runs_other")
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
        "--holdout-only",
        action="store_true",
        help="score ONLY the held-out layout, into its own runs directory (once, last)",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="call the model again for every document, to measure run-to-run variation",
    )
    parser.add_argument(
        "--no-retry",
        action="store_true",
        help="score the first reading only (the 'without retry' figures are in every run)",
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
    refs = select(refs, docs=args.docs, include_holdout=args.include_holdout or args.holdout_only)
    if args.holdout_only:
        refs = [ref for ref in refs if ref.is_holdout]
    if args.limit:
        refs = refs[: args.limit]
    if not refs:
        print("No documents selected.", file=sys.stderr)
        return 2

    run = evaluate(
        refs,
        model=model,
        dpi=args.dpi,
        use_cache=not args.no_cache,
        root=root,
        retry_enabled=not args.no_retry,
    )
    run["holdout_only"] = args.holdout_only
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
