"""The cache key must change whenever the request changes.

This is not a test about caching performance. It protects a measurement: step 6 of the
block is "change the request and re-measure", and a key that missed part of the request
would replay stale replies - the fix would appear to do nothing - or blend replies from
two different requests into one score.

The schema half matters as much as the instruction half, because `responses.parse` sends
the JSON schema with every call. Editing one field's `description` changes what the model
is asked for.

Since Tier 2 Block 1 the key carries a PIPELINE hash rather than one request's hash: the
classifier runs before the kind is known, and all three extraction schemas are part of
what could change a document's reading. A per-kind key would let a classifier edit replay
a reply produced by the previous classifier.

Offline: the key function is pure, so none of this calls a provider.
"""

from pathlib import Path

from pydantic import Field

from carbon_r.models.doc_kind import DocumentClassification
from carbon_r.models.extraction import ExtractedElectricityBill
from carbon_r.tools.extract import (
    CLASSIFY_INSTRUCTION,
    DEFAULT_DPI,
    EXTRACTION_INSTRUCTION,
    IMAGE_DETAIL,
    REQUEST_HASH,
    cache_key,
    pipeline_hash,
    request_hash,
)

MODEL = "gpt-5.4-mini-2026-03-17"


class DescriptionEdited(ExtractedElectricityBill):
    """What a vision model read off one electricity bill. Printed strings only.

    Every field is `str | None` with no default: required-and-nullable, which is what
    OpenAI strict structured outputs accepts, and which lets `null` mean "not printed on
    this page" rather than "the model forgot".
    """

    # Identical docstring to the parent, so ONLY the field description differs.
    consumption: str | None = Field(description="Units billed (kWh), with separators")


def test_identical_inputs_give_an_identical_key():
    assert cache_key("elec_009", MODEL) == cache_key("elec_009", MODEL)
    assert pipeline_hash() == REQUEST_HASH


def test_the_electricity_request_is_one_component_of_the_pipeline_hash():
    """The per-request hash still exists; it is no longer the whole key on its own."""
    assert request_hash(EXTRACTION_INSTRUCTION, ExtractedElectricityBill) != REQUEST_HASH


def test_editing_the_instruction_changes_the_key():
    edited = request_hash(
        EXTRACTION_INSTRUCTION + "\nPrefer the figure in the summary box.",
        ExtractedElectricityBill,
    )
    assert edited != REQUEST_HASH
    assert cache_key("elec_009", MODEL, request_fingerprint=edited) != cache_key("elec_009", MODEL)


def test_editing_the_classifier_changes_the_key():
    """A document's reading depends on the classifier, so the classifier is in the key."""
    edited = request_hash(
        CLASSIFY_INSTRUCTION + "\nWhen unsure, guess the most likely kind.",
        DocumentClassification,
    )
    assert edited != request_hash(CLASSIFY_INSTRUCTION, DocumentClassification)
    assert cache_key("lpg_001", MODEL, request_fingerprint=edited) != cache_key("lpg_001", MODEL)


def test_editing_one_field_description_changes_the_key():
    """The schema travels with the request, so a description is part of the request."""
    edited = request_hash(EXTRACTION_INSTRUCTION, DescriptionEdited)
    assert edited != request_hash(EXTRACTION_INSTRUCTION, ExtractedElectricityBill)


def test_adding_a_field_changes_the_key():
    class FieldAdded(ExtractedElectricityBill):
        late_payment_charge: str | None = Field(description="Late payment charge as printed")

    assert request_hash(EXTRACTION_INSTRUCTION, FieldAdded) != request_hash(
        EXTRACTION_INSTRUCTION, ExtractedElectricityBill
    )


def test_document_model_dpi_and_detail_each_change_the_key_independently():
    base = cache_key("elec_009", MODEL)
    assert cache_key("elec_010", MODEL) != base
    assert cache_key("elec_009", "some-other-model") != base
    assert cache_key("elec_009", MODEL, DEFAULT_DPI + 150) != base
    assert cache_key("elec_009", MODEL, image_detail="low") != base


def test_the_key_names_its_components_so_a_cache_directory_is_readable():
    key = cache_key("elec_009", MODEL)
    for component in ("elec_009", MODEL, f"{DEFAULT_DPI}dpi", IMAGE_DETAIL, REQUEST_HASH):
        assert component in key
    assert key.endswith(".json")


def test_the_hash_is_stable_across_processes():
    """sha256, never hash(): PYTHONHASHSEED would randomise str.__hash__ per process."""
    import subprocess
    import sys

    code = "from carbon_r.tools.extract import REQUEST_HASH; print(REQUEST_HASH)"
    runs = {
        subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env={"PYTHONHASHSEED": seed, "PATH": "/usr/bin:/bin"},
            check=True,
        ).stdout.strip()
        for seed in ("0", "1", "12345")
    }
    assert runs == {REQUEST_HASH}


def test_the_page_cache_is_keyed_by_content_not_only_by_name(tmp_path, monkeypatch):
    """A regenerated document under the same name must not be read from the old page.

    Tier 2 Block 2 regenerates lpg_007 as a different customer's invoice; a name-keyed
    cache would have handed the model Block 1's page. pdftoppm is replaced by a fake that
    records each call, so the test needs no poppler.
    """
    from carbon_r.tools import extract

    calls = []

    def fake_run(command, **_):
        calls.append(command)
        Path(command[-1] + "-1.png").write_bytes(b"page")

    monkeypatch.setattr(extract.subprocess, "run", fake_run)
    pdf = tmp_path / "lpg_007.pdf"
    cache = tmp_path / "pages"

    pdf.write_bytes(b"%PDF block 1 dental clinic")
    first = extract.rasterise(pdf, cache_dir=cache)
    assert extract.rasterise(pdf, cache_dir=cache) == first
    assert len(calls) == 1  # unchanged content: served from the cache

    pdf.write_bytes(b"%PDF v1 catering kitchen")
    second = extract.rasterise(pdf, cache_dir=cache)
    assert len(calls) == 2  # changed content: rasterised again
    assert second != first
