"""Retry with the error (Tier 2 Block 3, C7). No model, no network.

The rules under test:
- one retry, only when a field would not parse or a page-arithmetic check failed;
- the retry message names what failed and NEVER a value the page should hold;
- a second reading is adopted only when it leaves fewer problems, and every field it
  changed is marked `changed_on_retry`;
- a reading whose checks still fail is accepted, marked `inconsistent`, and disclosed.
"""

import json
from pathlib import Path

import pytest

from carbon_r.agent.prompt import DISCLOSURE_SENTENCES
from carbon_r.agent.tools import Toolkit
from carbon_r.models.extraction_fuel import ExtractedFuelCardStatement
from carbon_r.tools import extract
from carbon_r.tools.base import ok
from carbon_r.tools.extract import (
    REQUEST_HASH,
    RETRY_HASH,
    build_result,
    needs_retry,
    retry_message,
    with_retry,
)
from conftest import FACTOR_2024, extract_payload
from test_extraction_fuel import reading

PAGE = Path("fuel_036.png")
KIND = "fuel_card_statement"

# The fuel_036 shape from the Block 2 baseline: gross misread, transactions right.
MISREAD = {"gross_amount": "426.72", "subtotal": "420.51"}


def _result(statement: ExtractedFuelCardStatement):
    return build_result(statement, PAGE, kind=KIND, dpi=150, model="test-model")


def test_a_clean_reading_is_not_retried():
    assert not needs_retry(_result(reading()))


def test_a_failed_check_triggers_a_retry():
    first = _result(reading(**MISREAD))
    assert needs_retry(first)
    assert first.data["inconsistent"]


def test_the_retry_names_the_relation_and_never_a_figure():
    first = _result(reading(**MISREAD))
    message = retry_message(first)
    assert "gross_amount = sum(transaction amounts)" in message
    assert "Do not change a value to make arithmetic agree" in message
    for flag in first.data["consistency_flags"]:
        for figure in (flag["expected"], flag["printed"], flag["difference"]):
            assert str(figure) not in message
    # The page's true gross is the one figure the model must read for itself.
    assert reading().gross_amount not in message


def test_an_unparsed_field_is_quoted_back_as_the_model_transcribed_it():
    first = _result(reading(gross_amount="3l6.83"))
    message = retry_message(first)
    assert "gross_amount_sgd: you transcribed '3l6.83'" in message


def test_the_retry_is_outside_the_first_requests_fingerprint():
    # Changing the retry wording must not miss every cached first reading.
    assert RETRY_HASH != REQUEST_HASH
    assert extract.RETRY_INSTRUCTION not in json.dumps(
        [spec["instruction"] for spec in extract.EXTRACTORS.values()]
    )


def test_a_better_second_reading_is_adopted_and_its_changes_named():
    first = _result(reading(**MISREAD))
    final = with_retry(first, reading(), PAGE, kind=KIND, dpi=150, model="test-model")
    assert final.data["retry"]["adopted"]
    assert set(final.data["changed_on_retry"]) == {"gross_amount_sgd", "subtotal_sgd"}
    assert not final.data["inconsistent"]
    # Both readings are kept, so the eval can score what the retry changed.
    assert final.data["retry"]["first_normalised"]["gross_amount_sgd"] == "426.72"


def test_a_second_reading_that_is_no_better_is_not_adopted():
    first = _result(reading(**MISREAD))
    final = with_retry(first, reading(**MISREAD), PAGE, kind=KIND, dpi=150, model="test-model")
    assert not final.data["retry"]["adopted"]
    assert final.data["changed_on_retry"] == []
    assert final.data["inconsistent"]
    assert "marked inconsistent" in final.message


def test_extraction_retries_once_and_never_twice(monkeypatch, tmp_path):
    page = tmp_path / "fuel_036.png"
    page.write_bytes(b"not read")
    calls = {"read": 0, "reread": 0}

    class Classified:
        def resolved(self):
            return KIND, "scripted"

        def model_dump(self):
            return {"document_kind": KIND}

    def read_page(*_, **__):
        calls["read"] += 1
        return reading(**MISREAD)

    def reread_page(*_, **__):
        calls["reread"] += 1
        return reading(**MISREAD)  # still wrong: accepted, flagged, not retried again

    monkeypatch.setattr(extract, "classify_page", lambda *_, **__: Classified())
    monkeypatch.setattr(extract, "read_page", read_page)
    monkeypatch.setattr(extract, "reread_page", reread_page)
    settings = extract.get_settings()
    monkeypatch.setattr(settings, "openai_api_key", "sk-test", raising=False)
    monkeypatch.setattr(settings, "openai_model", "test-model", raising=False)
    if not settings.llm_configured:
        pytest.skip("settings cannot be configured in this environment")

    result = extract.extract_from_document(page)
    assert calls == {"read": 1, "reread": 1}
    assert result.status == "ok"
    assert result.data["inconsistent"]


def test_a_reading_changed_on_retry_is_disclosed_on_the_figure(session):
    payload = extract_payload() | {"changed_on_retry": ["consumption_kwh"], "inconsistent": False}

    def fake_lookup(category, unit, year):
        return ok({"factor": dict(FACTOR_2024, year_requested=year), "is_non_singapore": False}, "")

    toolkit = Toolkit(session, extract=lambda *_, **__: ok(payload, "Read."), lookup=fake_lookup)
    read = toolkit.dispatch("extract_from_document", json.dumps({"document_id": "doc_test"}))
    assert DISCLOSURE_SENTENCES["read_on_retry"] in read.data["required_disclosures"]

    toolkit.dispatch(
        "lookup_emission_factor",
        json.dumps({"category": "electricity_grid_sg", "unit": "kg CO2 / kWh", "year": 2025}),
    )
    figure = toolkit.dispatch(
        "calculate_emissions",
        json.dumps({"pairs": [{"activity_ref": "act_1", "factor_ref": "factor_1"}]}),
    )
    assert figure.status == "ok", figure.message
    assert figure.data["disclosures"]["read_on_retry"]
    assert DISCLOSURE_SENTENCES["read_on_retry"] in figure.data["required_disclosures"]
    assert DISCLOSURE_SENTENCES["inconsistent_reading"] not in figure.data["required_disclosures"]
