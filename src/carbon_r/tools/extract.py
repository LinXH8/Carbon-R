"""`extract_from_document` - a vision model reads a page and transcribes it.

The model's job here is narrow on purpose: transcribe what is printed. It is not asked for
a total, a conversion or a derived figure, because CLAUDE.md rule 1 says the model never
does arithmetic and the cheapest way to enforce that is to never request a number it would
have to compute. Everything typed or derived happens in
`carbon_r.models.extraction.normalise`, in plain Python.

Documents reach the model as PAGE IMAGES, never as a PDF the provider might read a text
layer out of. Three reasons:

1. A text-layer read would flatter the Block 3 accuracy figure - the synthetic PDFs have
   perfect text layers, which real scans do not.
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

from openai import OpenAI, OpenAIError
from pydantic import BaseModel

from carbon_r.config import PROJECT_ROOT, get_settings
from carbon_r.models.extraction import (
    ExtractedElectricityBill,
    check_consistency,
    normalise,
)
from carbon_r.tools.base import ToolResult, error, no_results, ok

EXTRACTION_INSTRUCTION = """You are transcribing a Singapore commercial electricity bill.

Report ONLY what is printed on the page, exactly as printed:
- Keep digits as shown, including thousands separators ("139,600" stays "139,600").
- Keep dates in the page's own format ("04 Dec 2025" stays "04 Dec 2025"). Do not reformat.
- Keep units as shown ("kWh", "MWh"). Do not convert.

Never compute, infer, complete or correct a figure. If a value is not printed on the
page, return null for it. Do not derive a value from other values, even when the
arithmetic is obvious. A null is a correct answer; an invented number is not.

Use `notes` for anything ambiguous: an illegible figure, two candidates for the same
field, or a layout that does not match these labels."""

# Sent with every request alongside the instruction, so it is part of the request.
IMAGE_DETAIL = "high"
DEFAULT_DPI = 150
MAX_PAGES = 4  # a bill is one page; the cap stops a stray document costing a fortune

PAGE_CACHE = PROJECT_ROOT / "data" / "generated" / ".cache" / "pages"


def request_hash(instruction: str, schema_model: type[BaseModel]) -> str:
    """Fingerprint of everything in the request that is not the image.

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


REQUEST_HASH = request_hash(EXTRACTION_INSTRUCTION, ExtractedElectricityBill)


def cache_key(
    doc_id: str,
    model: str,
    dpi: int = DEFAULT_DPI,
    *,
    image_detail: str = IMAGE_DETAIL,
    request_fingerprint: str = REQUEST_HASH,
) -> str:
    """The cache filename for one reply.

    Every input that can change the model's reply appears here: which document, which
    model, how finely it was rasterised, how much detail the provider was asked to use,
    and the fingerprint of the instruction and schema. An input that does not reach this
    key is a defect, not an optimisation - a changed request must miss the cache.
    """
    return f"{doc_id}-{model}-{dpi}dpi-{image_detail}-{request_fingerprint}.json"


def rasterise(path: Path, dpi: int = DEFAULT_DPI, *, cache_dir: Path | None = None) -> list[Path]:
    """PDF -> one PNG per page, cached. A PNG input is returned as-is.

    Uses poppler's `pdftoppm`, which is already a project dependency in practice (the
    build log records it as present and `pdftotext` is used elsewhere).
    """
    if path.suffix.lower() in (".png", ".jpg", ".jpeg"):
        return [path]

    cache_dir = cache_dir or PAGE_CACHE
    cache_dir.mkdir(parents=True, exist_ok=True)
    prefix = cache_dir / f"{path.stem}-{dpi}dpi"

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


def _image_content(page: Path) -> dict:
    encoded = base64.b64encode(page.read_bytes()).decode()
    return {
        "type": "input_image",
        "image_url": f"data:image/png;base64,{encoded}",
        "detail": IMAGE_DETAIL,
    }


def read_page(
    path: Path, *, dpi: int = DEFAULT_DPI, model: str | None = None
) -> ExtractedElectricityBill:
    """One model call. Raises on provider failure; callers wrap it in a ToolResult."""
    settings = get_settings()
    model = model or settings.openai_model
    pages = rasterise(path, dpi)

    client = OpenAI(api_key=settings.openai_api_key)
    response = client.responses.parse(
        model=model,
        input=[
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": EXTRACTION_INSTRUCTION},
                    *(_image_content(page) for page in pages),
                ],
            }
        ],
        text_format=ExtractedElectricityBill,
    )
    parsed = response.output_parsed
    if parsed is None:
        raise RuntimeError(f"Model returned no parsed output (status {response.status!r})")
    return parsed


def extract_from_document(
    path: str | Path, *, dpi: int = DEFAULT_DPI, model: str | None = None
) -> ToolResult:
    """Read one document and return its printed fields, typed values and flags.

    `status` is `ok` even when some fields failed to parse: a partial reading is a result,
    and the caller is told exactly which fields are missing. `no_results` means the page
    yielded nothing at all - the honest answer when a document is unreadable or is not an
    electricity bill (rule 7).
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
        extracted = read_page(path, dpi=dpi, model=model)
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

    return build_result(extracted, path, dpi=dpi, model=model or settings.openai_model)


def build_result(
    extracted: ExtractedElectricityBill,
    path: Path,
    *,
    dpi: int,
    model: str,
    usage: dict | None = None,
) -> ToolResult:
    """Normalise a reading and package it. Pure - no API call, so the eval reuses it.

    This is what lets a cached reply be re-scored against fixed code for free: the cache
    stores the raw reading, and every step after it runs again here.
    """
    normalised, problems = normalise(extracted)
    if normalised is None:
        return no_results(
            f"Nothing could be read from {path.name}. It may not be an electricity bill, "
            "or the page may be illegible.",
            {"printed": extracted.model_dump(), "document": path.name},
        )

    flags = check_consistency(normalised)
    data = {
        "document": path.name,
        "printed": extracted.model_dump(),
        "normalised": normalised.model_dump(mode="json"),
        "field_problems": [p.model_dump() for p in problems],
        "consistency_flags": [f.model_dump(mode="json") for f in flags],
        "request": {
            "model": model,
            "dpi": dpi,
            "image_detail": IMAGE_DETAIL,
            "request_hash": REQUEST_HASH,
        },
        "usage": usage or {},
    }

    parts = [f"Read {path.name}"]
    if problems:
        parts.append(f"{len(problems)} field(s) could not be parsed")
    if flags:
        parts.append(f"{len(flags)} printed figure(s) do not agree with the others")
    if extracted.notes:
        parts.append(f"model noted: {extracted.notes}")
    return ok(data, ". ".join(parts) + ".")


def _demo(path: str) -> None:  # pragma: no cover - manual verification aid
    result = extract_from_document(path)
    print(f"status: {result.status}\nmessage: {result.message}\n")
    if result.data:
        print("request:", json.dumps(result.data["request"]))
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
