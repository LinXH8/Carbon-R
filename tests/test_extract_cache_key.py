"""The cache key must change whenever the request changes.

This is not a test about caching performance. It protects a measurement: step 6 of the
block is "change the request and re-measure", and a key that missed part of the request
would replay stale replies - the fix would appear to do nothing - or blend replies from
two different requests into one score.

The schema half matters as much as the instruction half, because `responses.parse` sends
the JSON schema with every call. Editing one field's `description` changes what the model
is asked for.

Offline: the key function is pure, so none of this calls a provider.
"""

from pydantic import Field

from carbon_r.models.extraction import ExtractedElectricityBill
from carbon_r.tools.extract import (
    DEFAULT_DPI,
    EXTRACTION_INSTRUCTION,
    IMAGE_DETAIL,
    REQUEST_HASH,
    cache_key,
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
    assert request_hash(EXTRACTION_INSTRUCTION, ExtractedElectricityBill) == REQUEST_HASH


def test_editing_the_instruction_changes_the_key():
    edited = request_hash(
        EXTRACTION_INSTRUCTION + "\nPrefer the figure in the summary box.",
        ExtractedElectricityBill,
    )
    assert edited != REQUEST_HASH
    assert cache_key("elec_009", MODEL, request_fingerprint=edited) != cache_key("elec_009", MODEL)


def test_editing_one_field_description_changes_the_key():
    """The schema travels with the request, so a description is part of the request."""
    edited = request_hash(EXTRACTION_INSTRUCTION, DescriptionEdited)
    assert edited != REQUEST_HASH


def test_adding_a_field_changes_the_key():
    class FieldAdded(ExtractedElectricityBill):
        late_payment_charge: str | None = Field(description="Late payment charge as printed")

    assert request_hash(EXTRACTION_INSTRUCTION, FieldAdded) != REQUEST_HASH


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
