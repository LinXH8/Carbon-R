"""`extract_from_document` - a vision model classifies a page, then transcribes it.

The model's job here is narrow on purpose: say what kind of document this is, then
transcribe what is printed. It is not asked for a total, a conversion or a derived figure,
because CLAUDE.md rule 1 says the model never does arithmetic and the cheapest way to
enforce that is to never request a number it would have to compute. Everything typed or
derived happens in `carbon_r.models.extraction*`, in plain Python.

Two calls, not one
------------------
Tier 1 had one document kind and sent every page to one schema. With three kinds there is
a routing decision, and it is made first, by its own small call:

    1. classify  -> {document_kind, confidence, why}     low detail, tiny schema
    2. transcribe -> the schema for that kind            high detail

A single wide schema covering all three kinds was the alternative. It was rejected: about
sixty mostly-null fields invites cross-kind contamination, the fuel-card transaction list
does not flatten into a shape the other two share, and the per-field accuracy figures from
Block 3 would stop being comparable.

The cost is one extra call per document. The benefit is that `unknown` and `mixed` are
expressible: a page that is not one of the three kinds, or is more than one of them, is
refused and asked about instead of being read under a guessed schema. That failure is the
nastiest one available here - the wrong schema returns a page full of plausible nulls,
which reads like an illegible document rather than a misrouted one.

Documents reach the model as PAGE IMAGES, never as a PDF the provider might read a text
layer out of. Three reasons:

1. A text-layer read would flatter the accuracy figures - the synthetic PDFs have perfect
   text layers, which real scans do not.
2. Tier 2's `moderate` and `hard` tiers are PNGs. Rasterising now means they use this code
   path unchanged.
3. It is the honest test of the claim being made, which is that the pipeline reads
   documents.

On reproducibility: the pinned model snapshot keeps the model VERSION fixed, but it does
not make replies identical across calls. The eval's reply cache is what makes re-reporting
reproducible; a `--no-cache` run may differ slightly from a cached one, and measuring that
spread is what the three fresh runs in the acceptance criteria are for.
"""

import base64
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from openai import OpenAI, OpenAIError
from pydantic import BaseModel

from carbon_r.config import PROJECT_ROOT, get_settings
from carbon_r.models.doc_kind import (
    READABLE_KINDS,
    REFUSAL_KINDS,
    DocumentClassification,
    refusal_message,
)
from carbon_r.models.extraction import (
    ExtractedElectricityBill,
    activities_for,
    check_consistency,
    normalise,
)
from carbon_r.models.extraction_fuel import (
    ExtractedFuelCardStatement,
    activities_for_fuel,
    check_consistency_fuel,
    normalise_fuel,
)
from carbon_r.models.extraction_lpg import (
    ExtractedLPGInvoice,
    activities_for_lpg,
    check_consistency_lpg,
    normalise_lpg,
)
from carbon_r.tools.base import ToolResult, error, no_results, ok

CLASSIFY_INSTRUCTION = """Say what kind of document this page is. Do not transcribe it.

- electricity_bill: a bill for mains electricity, with a meter reading and kWh consumed.
- lpg_invoice: an invoice for delivered LPG / cooking gas cylinders, priced per cylinder
  or per kg.
- fuel_card_statement: a statement of vehicle fuel purchases, listing transactions in
  litres of diesel or petrol.
- unknown: none of those three, or not a business document at all.
- mixed: this file contains more than one of those kinds of document.

Answer `unknown` rather than picking the closest match. A wrong kind is worse than no
kind, because the page will then be read with the wrong set of fields. Give the evidence
that decided it - a heading, a column, a unit - in one short sentence."""

# One instruction per readable kind. Each keeps the same three rules: report only what is
# printed, keep it exactly as printed, never compute anything.
_SHARED_RULES = """
Report ONLY what is printed on the page, exactly as printed:
- Keep digits as shown, including thousands separators ("139,600" stays "139,600").
- Keep dates in the page's own format ("04 Dec 2025" stays "04 Dec 2025"). Do not reformat.
- Keep units as shown. Do not convert.

Never compute, infer, complete or correct a figure. If a value is not printed on the
page, return null for it. Do not derive a value from other values, even when the
arithmetic is obvious. A null is a correct answer; an invented number is not.

Use `notes` for anything ambiguous: an illegible figure, two candidates for the same
field, or a layout that does not match these labels."""

EXTRACTION_INSTRUCTION = (
    "You are transcribing a Singapore commercial electricity bill.\n" + _SHARED_RULES
)

LPG_INSTRUCTION = (
    """You are transcribing a Singapore LPG (cooking gas) delivery invoice.

Two things on this document need care:

- `cylinder_deliveries` is for GAS DELIVERED only. Cylinder rental, cylinder deposits,
  returned empty cylinders and delivery charges are NOT gas delivered, even though they
  appear as quantities and amounts on the same invoice. Put those in `lines` only.
- `lines` is for CHARGE rows only - the things being billed. Do NOT include the subtotal,
  GST or total rows: those have their own fields, and listing them as charges would
  double-count them.
- The cylinder delivery row belongs in BOTH lists: in `cylinder_deliveries` because it is
  gas delivered, and in `lines` because it is also a charge. Putting it in only one of
  them leaves the other incomplete.
- `total_kg_printed` is only for a total weight the page actually prints. Most invoices
  do not print one. Return null rather than adding the cylinder lines together."""
    + _SHARED_RULES
)

FUEL_INSTRUCTION = (
    """You are transcribing a Singapore vehicle fuel-card statement.

Three things on this document need care:

- `transactions` is the fuel purchase rows only. Do not include summary rows, discount
  rows or total rows.
- Record each row's product exactly as printed ("Diesel", "Petrol 95"). Different products
  have different emission factors, so they must not be merged.
- `litres` is the quantity. The amount in dollars is not a quantity of fuel, and a
  discount or rebate reduces the money without changing the litres."""
    + _SHARED_RULES
)

# doc_kind -> everything needed to read that kind. One table rather than a chain of ifs, so
# adding a kind is a row here and a row in `DocumentKind`, and `tests/test_doc_kind.py`
# asserts the two agree.
EXTRACTORS: dict[str, dict[str, Any]] = {
    "electricity_bill": {
        "schema": ExtractedElectricityBill,
        "instruction": EXTRACTION_INSTRUCTION,
        "normalise": normalise,
        "activities": activities_for,
        "consistency": check_consistency,
    },
    "lpg_invoice": {
        "schema": ExtractedLPGInvoice,
        "instruction": LPG_INSTRUCTION,
        "normalise": normalise_lpg,
        "activities": activities_for_lpg,
        "consistency": check_consistency_lpg,
    },
    "fuel_card_statement": {
        "schema": ExtractedFuelCardStatement,
        "instruction": FUEL_INSTRUCTION,
        "normalise": normalise_fuel,
        "activities": activities_for_fuel,
        "consistency": check_consistency_fuel,
    },
}

# Sent with every request alongside the instruction, so it is part of the request.
IMAGE_DETAIL = "high"
CLASSIFY_DETAIL = "low"  # the kind is legible from the headings; full detail is wasted here
DEFAULT_DPI = 150
MAX_PAGES = 4  # a bill is one page; the cap stops a stray document costing a fortune

PAGE_CACHE = PROJECT_ROOT / "data" / "generated" / ".cache" / "pages"


def request_hash(instruction: str, schema_model: type[BaseModel]) -> str:
    """Fingerprint of everything in one request that is not the image.

    BOTH the instruction and the JSON schema, because `responses.parse` sends the schema
    with every call - so editing one field's `description` changes what the model is
    asked for, and a cache keyed on the prompt alone would replay stale replies.

    `sort_keys=True` so dict ordering cannot drift the digest without the request
    changing. Note that the schema includes the model's class docstring, so a prose-only
    docstring edit does change the hash: correct, since that prose is sent to the model,
    but worth knowing before rewriting a comment and re-spending an eval run.

    sha256 of the encoded string, never `hash()` - PYTHONHASHSEED randomises
    `str.__hash__` per process, the trap `synthgen/rng.py` documents for seeds.
    """
    schema = json.dumps(schema_model.model_json_schema(), sort_keys=True)
    return hashlib.sha256((instruction + schema).encode()).hexdigest()[:8]


def pipeline_hash() -> str:
    """Fingerprint of the WHOLE request pipeline: the classifier and all three schemas.

    One hash rather than one per kind, because a document's cache entry must miss when
    anything that could change its reply changes - and that now includes the classifier,
    which runs before the kind is even known. Keying per-kind would let a classifier edit
    replay a reply produced by the previous classifier.
    """
    parts = [request_hash(CLASSIFY_INSTRUCTION, DocumentClassification)]
    parts += [
        request_hash(spec["instruction"], spec["schema"])
        for kind, spec in sorted(EXTRACTORS.items())
    ]
    return hashlib.sha256("".join(parts).encode()).hexdigest()[:8]


REQUEST_HASH = pipeline_hash()


def cache_key(
    doc_id: str,
    model: str,
    dpi: int = DEFAULT_DPI,
    *,
    image_detail: str = IMAGE_DETAIL,
    request_fingerprint: str | None = None,
) -> str:
    """The cache filename for one document's reading.

    Every input that can change the model's reply appears here: which document, which
    model, how finely it was rasterised, how much detail the provider was asked to use,
    and the fingerprint of the classifier plus every extraction instruction and schema. An
    input that does not reach this key is a defect, not an optimisation - a changed request
    must miss the cache.
    """
    fingerprint = request_fingerprint or REQUEST_HASH
    return f"{doc_id}-{model}-{dpi}dpi-{image_detail}-{fingerprint}.json"


def rasterise(path: Path, dpi: int = DEFAULT_DPI, *, cache_dir: Path | None = None) -> list[Path]:
    """PDF -> one PNG per page, cached. A PNG input is returned as-is.

    Uses poppler's `pdftoppm`, which is already a project dependency in practice (the
    build log records it as present and `pdftotext` is used elsewhere).

    The cache is keyed by the file's CONTENT as well as its name. Until Tier 2 Block 2 it
    was keyed by name alone, so a regenerated `lpg_007.pdf` - a different document under
    the same name - would have been read from the old page image. Found by reading this
    code before the Block 2 baseline, not by a failure; no recorded run was affected,
    because no Block 1 document was regenerated with a change after its pages were cached.
    """
    if path.suffix.lower() in (".png", ".jpg", ".jpeg"):
        return [path]

    cache_dir = cache_dir or PAGE_CACHE
    cache_dir.mkdir(parents=True, exist_ok=True)
    content = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    prefix = cache_dir / f"{path.stem}-{content}-{dpi}dpi"

    existing = sorted(cache_dir.glob(f"{prefix.name}-*.png"))
    if existing:
        return existing[:MAX_PAGES]

    subprocess.run(
        ["pdftoppm", "-r", str(dpi), "-png", str(path), str(prefix)],
        check=True,
        capture_output=True,
    )
    pages = sorted(cache_dir.glob(f"{prefix.name}-*.png"))
    if not pages:
        raise RuntimeError(f"pdftoppm produced no pages for {path}")
    return pages[:MAX_PAGES]


def rasterise_page(
    path: Path, pdf_page: int, dpi: int = DEFAULT_DPI, *, cache_dir: Path | None = None
) -> Path:
    """One page of a PDF -> one PNG, cached by content, page and dpi.

    For long documents (the peer reports run to 190 pages) where only a few pages are read:
    `rasterise` renders every page and keeps the first `MAX_PAGES`, which is right for a bill
    and wrong here. Same cache rule: the file's content is in the key, not just its name.
    """
    cache_dir = cache_dir or PAGE_CACHE
    cache_dir.mkdir(parents=True, exist_ok=True)
    content = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    target = cache_dir / f"{path.stem}-{content}-p{pdf_page}-{dpi}dpi.png"
    if target.exists():
        return target
    # -singlefile writes exactly <prefix>.png, with no page-number suffix to guess at.
    subprocess.run(
        [
            "pdftoppm",
            "-f",
            str(pdf_page),
            "-l",
            str(pdf_page),
            "-r",
            str(dpi),
            "-png",
            "-singlefile",
            str(path),
            str(target.with_suffix("")),
        ],
        check=True,
        capture_output=True,
    )
    if not target.exists():
        raise RuntimeError(f"pdftoppm produced no image for {path.name} page {pdf_page}")
    return target


def _image_content(page: Path, detail: str) -> dict:
    encoded = base64.b64encode(page.read_bytes()).decode()
    return {
        "type": "input_image",
        "image_url": f"data:image/png;base64,{encoded}",
        "detail": detail,
    }


def _parse(
    client: OpenAI,
    model: str,
    pages: list[Path],
    instruction: str,
    schema: type[BaseModel],
    detail: str,
    usage: list[dict] | None = None,
) -> BaseModel:
    """One model call returning a validated instance of `schema`.

    `usage`, when given, receives the provider's token counts for this call - the eval
    records them so a run's cost is measured rather than estimated.
    """
    response = client.responses.parse(
        model=model,
        input=[
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": instruction},
                    *(_image_content(page, detail) for page in pages),
                ],
            }
        ],
        text_format=schema,
    )
    if usage is not None and getattr(response, "usage", None) is not None:
        usage.append(response.usage.model_dump())
    parsed = response.output_parsed
    if parsed is None:
        raise RuntimeError(f"Model returned no parsed output (status {response.status!r})")
    return parsed


def classify_page(
    path: Path,
    *,
    dpi: int = DEFAULT_DPI,
    model: str | None = None,
    usage: list[dict] | None = None,
) -> DocumentClassification:
    """One small model call: what kind of document is this? Raises on provider failure."""
    settings = get_settings()
    model = model or settings.openai_model
    client = OpenAI(api_key=settings.openai_api_key)
    result = _parse(
        client,
        model,
        rasterise(path, dpi),
        CLASSIFY_INSTRUCTION,
        DocumentClassification,
        CLASSIFY_DETAIL,
        usage,
    )
    return result  # type: ignore[return-value]


def read_page(
    path: Path,
    *,
    kind: str,
    dpi: int = DEFAULT_DPI,
    model: str | None = None,
    usage: list[dict] | None = None,
) -> BaseModel:
    """One model call transcribing a page with the schema for `kind`.

    `kind` is required: there is no default document kind any more, and a caller that does
    not know the kind should call `classify_page` first rather than assume electricity.
    """
    if kind not in EXTRACTORS:
        raise ValueError(f"No extractor for document kind {kind!r}; have {sorted(EXTRACTORS)}")
    spec = EXTRACTORS[kind]
    settings = get_settings()
    model = model or settings.openai_model
    client = OpenAI(api_key=settings.openai_api_key)
    return _parse(
        client,
        model,
        rasterise(path, dpi),
        spec["instruction"],
        spec["schema"],
        IMAGE_DETAIL,
        usage,
    )


def extract_from_document(
    path: str | Path, *, dpi: int = DEFAULT_DPI, model: str | None = None
) -> ToolResult:
    """Classify one document, read it, and return its printed fields, values and flags.

    `status` is `ok` even when some fields failed to parse: a partial reading is a result,
    and the caller is told exactly which fields are missing. `no_results` means either the
    page yielded nothing at all, or its kind could not be established - both honest
    answers, and both of which the agent reports and asks about (rule 7).
    """
    path = Path(path)
    settings = get_settings()
    if not settings.llm_configured:
        return error("OPENAI_API_KEY is not set; see .env.example.")
    if not (model or settings.openai_model):
        return error("No model id configured. Run `make models` and set OPENAI_MODEL.")
    if not path.exists():
        return error(f"No such document: {path}")

    try:
        classification = classify_page(path, dpi=dpi, model=model)
        kind, why = classification.resolved()
        if kind in REFUSAL_KINDS:
            return no_results(
                refusal_message(kind, why),
                {
                    "document": path.name,
                    "classification": classification.model_dump(),
                    "readable_kinds": list(READABLE_KINDS),
                },
            )
        extracted = read_page(path, kind=kind, dpi=dpi, model=model)
    except FileNotFoundError:
        return error(
            "pdftoppm was not found. Install poppler (`brew install poppler`) to "
            "rasterise PDFs for the vision model."
        )
    except subprocess.CalledProcessError as exc:
        return error(f"pdftoppm failed on {path.name}: {exc.stderr.decode()[:200]}")
    except OpenAIError as exc:
        return error(f"Model provider error reading {path.name}: {exc}")
    except RuntimeError as exc:
        return error(str(exc))

    first = build_result(
        extracted,
        path,
        kind=kind,
        dpi=dpi,
        model=model or settings.openai_model,
        classification=classification,
    )
    if not needs_retry(first):
        return first
    try:
        second = reread_page(path, kind=kind, message=retry_message(first), dpi=dpi, model=model)
    except (OpenAIError, RuntimeError) as exc:
        # The retry is a second opinion, not a precondition: if it fails, the first reading
        # stands with its problems and flags reported, exactly as it would have before.
        return ok(
            first.data,
            f"{first.message} A second reading was attempted and failed ({exc}).",
        )
    return with_retry(first, second, path, kind=kind, dpi=dpi, model=model or settings.openai_model)


def build_result(
    extracted: BaseModel,
    path: Path,
    *,
    kind: str,
    dpi: int,
    model: str,
    classification: DocumentClassification | None = None,
    usage: dict | None = None,
) -> ToolResult:
    """Normalise a reading and package it. Pure - no API call, so the eval reuses it.

    This is what lets a cached reply be re-scored against fixed code for free: the cache
    stores the raw reading, and every step after it runs again here.
    """
    spec = EXTRACTORS[kind]
    normalised, problems = spec["normalise"](extracted)
    if normalised is None:
        return no_results(
            f"Nothing could be read from {path.name}. It may not be a "
            f"{kind.replace('_', ' ')}, or the page may be illegible.",
            {"printed": extracted.model_dump(), "document": path.name, "document_kind": kind},
        )

    flags = spec["consistency"](normalised)
    activities = spec["activities"](normalised)
    data = {
        "document": path.name,
        "document_kind": kind,
        "classification": classification.model_dump() if classification else None,
        "printed": extracted.model_dump(),
        "normalised": normalised.model_dump(mode="json"),
        "activities": [activity.model_dump(mode="json") for activity in activities],
        "field_problems": [p.model_dump() for p in problems],
        "consistency_flags": [f.model_dump(mode="json") for f in flags],
        "request": {
            "model": model,
            "dpi": dpi,
            "image_detail": IMAGE_DETAIL,
            "request_hash": REQUEST_HASH,
        },
        "usage": usage or {},
        # Set by `with_retry` when a second reading was taken; see there.
        "retry": None,
        "changed_on_retry": [],
        "inconsistent": bool(flags),
    }

    parts = [f"Read {path.name} as a {kind.replace('_', ' ')}"]
    if activities:
        parts.append("found " + ", ".join(activity.label for activity in activities))
    else:
        parts.append("found no measurable activity on the page")
    if problems:
        parts.append(f"{len(problems)} field(s) could not be parsed")
    if flags:
        parts.append(f"{len(flags)} printed figure(s) do not agree with the others")
    notes = getattr(extracted, "notes", None)
    if notes:
        parts.append(f"model noted: {notes}")
    return ok(data, ". ".join(parts) + ".")


# --- retry with the error (Tier 2 Block 3, C7) ---------------------------------------------
#
# ONE further reading when the first could not be parsed or its arithmetic does not close.
# The message names what failed - a field and what was transcribed for it, or a relation by
# name - and NEVER a value the page should hold: the expected figure is exactly what the
# model must read off the page, and a retry that hands it over measures nothing and invites
# the model to copy it. For the same reason the message forbids adjusting a value to make
# arithmetic agree; whether that holds is measured (correct->wrong) rather than assumed.
#
# Kept OUT of REQUEST_HASH: the first reading's request is unchanged, so every cached first
# reading stays a hit. The retry is cached under its own key (see the eval).

RETRY_INSTRUCTION = """You have already read this page once. Part of that reading could not be used:

{items}

Read the page again and transcribe every field, in the same schema. Re-read these exactly
as printed. Do not change a value to make arithmetic agree; if the page's own figures
disagree, transcribe them as printed."""

RETRY_HASH = hashlib.sha256(RETRY_INSTRUCTION.encode()).hexdigest()[:8]


def needs_retry(result: ToolResult) -> bool:
    """A reading that parsed, but with fields that would not parse or sums that do not add."""
    return (
        result.status == "ok"
        and result.data is not None
        and bool(result.data["field_problems"] or result.data["consistency_flags"])
    )


def retry_message(result: ToolResult) -> str:
    """What failed, by name. The model's own transcription may be quoted back to it; no
    expected, printed-elsewhere or difference figure ever is."""
    items = [
        f"- {problem['field']}: you transcribed {problem['printed']!r}, which could not be "
        "read as a value"
        for problem in result.data["field_problems"]
    ]
    items += [
        f"- on your reading, {flag['relation']} does not hold"
        for flag in result.data["consistency_flags"]
    ]
    return RETRY_INSTRUCTION.format(items="\n".join(items))


def reread_page(
    path: Path,
    *,
    kind: str,
    message: str,
    dpi: int = DEFAULT_DPI,
    model: str | None = None,
    usage: list[dict] | None = None,
) -> BaseModel:
    """The one retry: the same page and schema, the kind's instruction plus `message`."""
    spec = EXTRACTORS[kind]
    settings = get_settings()
    model = model or settings.openai_model
    client = OpenAI(api_key=settings.openai_api_key)
    return _parse(
        client,
        model,
        rasterise(path, dpi),
        spec["instruction"] + "\n\n" + message,
        spec["schema"],
        IMAGE_DETAIL,
        usage,
    )


# A problem's field root that is not itself the printed field's name.
_PRINTED_ROOT = {"cylinders": "cylinder_deliveries"}


def retry_scope(result: ToolResult) -> set[str]:
    """The PRINTED fields a retry may replace: those a failed check reads, and those that
    would not parse. Nothing else is taken from the second reading (Block 3, C7b)."""
    scope: set[str] = set()
    for flag in result.data["consistency_flags"]:
        scope.update(flag.get("fields") or ())
    for problem in result.data["field_problems"]:
        root = problem.get("source") or problem["field"].split("[", 1)[0].split(".", 1)[0]
        scope.add(_PRINTED_ROOT.get(root, root))
    return scope


def _burden(result: ToolResult) -> float:
    if result.status != "ok" or result.data is None:
        return float("inf")
    return len(result.data["field_problems"]) + len(result.data["consistency_flags"])


def with_retry(
    first: ToolResult, second: BaseModel, path: Path, *, kind: str, dpi: int, model: str
) -> ToolResult:
    """Choose between two readings of one page, and record what the retry changed. Pure.

    SCOPED (Block 3, C7b). The second reading is not taken whole: only the printed fields
    the failed checks read, or that would not parse, come from it, and every other field
    keeps its first reading. The merged reading is then normalised from scratch, so every
    derived figure is recomputed by the code. The first version adopted the whole second
    reading, and the eval showed why that is wrong on hard photos: the retry fixed the field
    it was asked about and, re-reading everything, got three fields nobody asked about
    wrong (3 correct->wrong, 0 wrong->correct).

    The merged reading is adopted only if it leaves FEWER problems and failed checks than
    the first; otherwise the first stands and nothing is marked changed. An adopted reading
    names every normalised field whose value differs as `changed_on_retry`, and the agent
    and the page disclose them as less certain. If checks still fail on the reading that
    stands, it is `inconsistent` - accepted, disclosed, and never retried again.
    """
    schema = EXTRACTORS[kind]["schema"]
    first_printed = schema.model_validate(first.data["printed"])
    scope = retry_scope(first) & set(schema.model_fields)
    merged = first_printed.model_copy(update={name: getattr(second, name) for name in scope})
    second_result = build_result(merged, path, kind=kind, dpi=dpi, model=model)
    adopted = _burden(second_result) < _burden(first)
    final = second_result if adopted else first
    changed = (
        [
            name
            for name, value in second_result.data["normalised"].items()
            if first.data["normalised"].get(name) != value
        ]
        if adopted
        else []
    )
    data = final.data | {
        "classification": first.data["classification"],
        "changed_on_retry": changed,
        "inconsistent": bool(final.data["consistency_flags"]),
        "retry": {
            "retry_hash": RETRY_HASH,
            "triggered_by": {
                "field_problems": [p["field"] for p in first.data["field_problems"]],
                "consistency_flags": [f["relation"] for f in first.data["consistency_flags"]],
            },
            "adopted": adopted,
            "scope": sorted(scope),
            "second_printed": second.model_dump(),
            "first_normalised": first.data["normalised"],
            "first_activities": first.data["activities"],
            "first_field_problems": first.data["field_problems"],
            "first_consistency_flags": first.data["consistency_flags"],
        },
    }
    note = (
        f"Re-read once after a failed check; the second reading was used and changed "
        f"{', '.join(changed) or 'nothing'}"
        if adopted
        else "Re-read once after a failed check; the second reading was no better, so the "
        "first was kept"
    )
    if data["inconsistent"]:
        note += ". The figures still do not all agree, so the reading is marked inconsistent"
    return ok(data, f"{final.message} {note}.")


def _demo(path: str) -> None:  # pragma: no cover - manual verification aid
    result = extract_from_document(path)
    print(f"status: {result.status}\nmessage: {result.message}\n")
    if result.data:
        print("kind:", result.data.get("document_kind"))
        print("classification:", json.dumps(result.data.get("classification")))
        print("\nactivities:")
        for activity in result.data.get("activities", []):
            print(f"  {activity}")
        print("\nnormalised:")
        for key, value in result.data["normalised"].items():
            print(f"  {key:24} {value}")
        if result.data["field_problems"]:
            print("\nfield problems:", json.dumps(result.data["field_problems"], indent=2))
        if result.data["consistency_flags"]:
            print("\nconsistency:", json.dumps(result.data["consistency_flags"], indent=2))


if __name__ == "__main__":  # pragma: no cover
    import sys

    _demo(sys.argv[1])
