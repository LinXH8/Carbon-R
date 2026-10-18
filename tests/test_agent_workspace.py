"""The toolkit: handles in, values out of the session, and no value through the model.

These are the tests that hold CLAUDE.md rule 1 in place one level above
`calculate_emissions`. If a future convenience argument lets the model pass an amount or
a factor value, `test_calculate_uses_the_stored_reading_not_the_model` and the schema
test in `test_agent_toolspec.py` are the ones that should fail.
"""

import json
from decimal import Decimal

from carbon_r.agent.tools import Toolkit
from carbon_r.tools.base import ToolResult, error, no_results, ok
from conftest import FACTOR_2024, extract_payload


def dispatch(toolkit: Toolkit, name: str, **arguments) -> ToolResult:
    return toolkit.dispatch(name, json.dumps(arguments))


# --- dispatch ---------------------------------------------------------------------


def test_unknown_tool_is_an_error_not_an_exception(toolkit):
    result = toolkit.dispatch("delete_everything", "{}")
    assert result.status == "error"
    assert "No tool named" in result.message
    assert "extract_from_document" in result.message  # tells the model what it may call


def test_malformed_json_arguments_are_an_error(toolkit):
    result = toolkit.dispatch("extract_from_document", "{document_id: doc_test")
    assert result.status == "error"
    assert "not valid JSON" in result.message


def test_arguments_of_the_wrong_shape_are_an_error(toolkit):
    assert toolkit.dispatch("extract_from_document", '["doc_test"]').status == "error"


def test_invalid_arguments_name_the_field(toolkit):
    result = dispatch(toolkit, "lookup_emission_factor", category="c", unit="u", year="soon")
    assert result.status == "error"
    assert "year" in result.message


def test_every_dispatch_is_recorded_for_the_audit(toolkit, session):
    dispatch(toolkit, "extract_from_document", document_id="doc_test")
    toolkit.dispatch("no_such_tool", "{}")
    assert [p["tool"] for p in session.tool_payloads] == ["extract_from_document", "no_such_tool"]


# --- extraction -------------------------------------------------------------------


def test_extract_addresses_documents_by_id(toolkit):
    result = dispatch(toolkit, "extract_from_document", document_id="doc_test")
    assert result.status == "ok"
    assert result.data["document_id"] == "doc_test"
    assert result.data["normalised"]["consumption_kwh"] == 139600


def test_extract_refuses_an_id_not_in_the_session(toolkit):
    result = dispatch(toolkit, "extract_from_document", document_id="../../.env")
    assert result.status == "error"
    assert "No document" in result.message


def test_extract_result_drops_the_raw_printed_dump(toolkit):
    """The model gets the typed reading; it does not need the transcription twice."""
    result = dispatch(toolkit, "extract_from_document", document_id="doc_test")
    assert "printed" not in result.data
    assert "request" not in result.data


def test_extract_states_the_activity_year(toolkit):
    result = dispatch(toolkit, "extract_from_document", document_id="doc_test")
    assert "2025" in result.message  # period_start, not document_date


def test_extract_is_cached_per_document(session):
    calls = []

    def counting_extract(path, **_):
        calls.append(path)
        return ok(extract_payload(), "Read it.")

    toolkit = Toolkit(session, extract=counting_extract)
    dispatch(toolkit, "extract_from_document", document_id="doc_test")
    second = dispatch(toolkit, "extract_from_document", document_id="doc_test")
    assert len(calls) == 1
    assert second.status == "ok"
    assert "already been read" in second.message


def test_a_failed_extraction_is_passed_through_unchanged(session):
    toolkit = Toolkit(session, extract=lambda path, **_: error("pdftoppm was not found."))
    result = dispatch(toolkit, "extract_from_document", document_id="doc_test")
    assert result.status == "error"
    assert "doc_test" not in session.readings


# --- factor lookup ----------------------------------------------------------------


def test_lookup_returns_a_handle_and_stores_the_citation(toolkit, session):
    result = dispatch(
        toolkit,
        "lookup_emission_factor",
        category="electricity_grid_sg",
        unit="kg CO2 / kWh",
        year=2025,
    )
    assert result.data["factor_ref"] == "factor_1"
    assert session.factors["factor_1"]["factor"] == "0.402"
    assert "factor_1" in result.message


def test_handles_do_not_collide(toolkit):
    first = dispatch(toolkit, "lookup_emission_factor", category="c", unit="u", year=2024)
    second = dispatch(toolkit, "lookup_emission_factor", category="c", unit="u", year=2023)
    assert first.data["factor_ref"] != second.data["factor_ref"]


def test_no_results_from_the_table_is_passed_through(session):
    toolkit = Toolkit(
        session,
        extract=lambda path, **_: ok(extract_payload(), "Read it."),
        lookup=lambda *_: no_results("The newest factor is for 2024, 3 years before 2027."),
    )
    result = dispatch(toolkit, "lookup_emission_factor", category="c", unit="u", year=2027)
    assert result.status == "no_results"
    assert session.factors == {}  # nothing stored, so nothing to calculate with


# --- calculation ------------------------------------------------------------------


def _prepare(toolkit) -> None:
    dispatch(toolkit, "extract_from_document", document_id="doc_test")
    dispatch(
        toolkit,
        "lookup_emission_factor",
        category="electricity_grid_sg",
        unit="kg CO2 / kWh",
        year=2025,
    )


def test_calculate_uses_the_stored_reading_not_the_model(toolkit):
    """139600 kWh x 0.402 = 56119.2 kg. Neither number came through the model."""
    _prepare(toolkit)
    result = dispatch(toolkit, "calculate_emissions", document_id="doc_test", factor_ref="factor_1")
    assert result.status == "ok"
    assert Decimal(result.data["kg_co2e"]) == Decimal("56119.200")
    assert result.data["inputs"]["activity_amount"] == "139600"
    assert result.data["inputs"]["factor"]["factor"] == "0.402"


def test_calculate_returns_the_disclosure_sentences(toolkit):
    _prepare(toolkit)
    result = dispatch(toolkit, "calculate_emissions", document_id="doc_test", factor_ref="factor_1")
    sentences = " ".join(result.data["required_disclosures"])
    assert "not assured" in sentences
    assert "not from the activity year" in sentences  # FACTOR_2024 is a substitution
    assert "marked estimated" not in sentences  # this reading is actual


def test_calculate_refuses_an_unknown_handle(toolkit):
    _prepare(toolkit)
    result = dispatch(toolkit, "calculate_emissions", document_id="doc_test", factor_ref="factor_9")
    assert result.status == "error"
    assert "lookup_emission_factor" in result.message


def test_calculate_refuses_a_document_that_was_never_read(toolkit):
    dispatch(
        toolkit,
        "lookup_emission_factor",
        category="electricity_grid_sg",
        unit="kg CO2 / kWh",
        year=2025,
    )
    result = dispatch(toolkit, "calculate_emissions", document_id="doc_test", factor_ref="factor_1")
    assert result.status == "error"
    assert "extract_from_document" in result.message


def test_calculate_says_so_when_the_consumption_was_not_read(session):
    payload = extract_payload()
    payload["normalised"]["consumption_kwh"] = None
    toolkit = Toolkit(
        session,
        extract=lambda path, **_: ok(payload, "Read it, mostly."),
        lookup=lambda *_: ok({"factor": dict(FACTOR_2024), "is_non_singapore": False}, "0.402."),
    )
    _prepare(toolkit)
    result = dispatch(toolkit, "calculate_emissions", document_id="doc_test", factor_ref="factor_1")
    assert result.status == "no_results"
    assert "ask the user" in result.message


def test_an_estimated_reading_reaches_the_disclosures(session):
    payload = extract_payload()
    payload["normalised"]["is_estimated_reading"] = True
    toolkit = Toolkit(
        session,
        extract=lambda path, **_: ok(payload, "Read it."),
        lookup=lambda *_: ok({"factor": dict(FACTOR_2024), "is_non_singapore": False}, "0.402."),
    )
    _prepare(toolkit)
    result = dispatch(toolkit, "calculate_emissions", document_id="doc_test", factor_ref="factor_1")
    assert result.data["disclosures"]["estimated_reading"] is True
    assert any("marked estimated" in s for s in result.data["required_disclosures"])
