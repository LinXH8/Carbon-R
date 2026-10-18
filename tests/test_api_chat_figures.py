"""`ChatResponse.figures` - the structured figure the UI renders instead of the prose.

The page must not parse numbers out of `reply`: a figure scraped from model prose is a
model-authored figure, which is CLAUDE.md rule 1 with the arithmetic moved into the
presentation layer. That makes this field the thing standing between the rule and the
screen, so the payloads here are built by the real `Toolkit` and the real
`calculate_emissions` rather than hand-written - a hand-written dict would stop noticing
if the shape ever drifted.

Offline, like the rest of the suite.
"""

import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from carbon_r import main
from carbon_r.agent.loop import AgentRun, run_agent
from carbon_r.agent.session import reset_sessions
from carbon_r.api.models import ChatResponse, FactorOut, FigureOut
from conftest import call, fake_client

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def _clean():
    reset_sessions()
    yield
    reset_sessions()


@pytest.fixture
def configured(monkeypatch):
    settings = main.get_settings()
    monkeypatch.setattr(settings, "openai_api_key", "sk-test", raising=False)
    monkeypatch.setattr(settings, "openai_model", "test-model", raising=False)
    return settings


@pytest.fixture
def figure_payload(toolkit):
    """A real `calculate_emissions` payload, produced the way the agent produces one."""
    toolkit.dispatch("extract_from_document", json.dumps({"document_id": "doc_test"}))
    toolkit.dispatch(
        "lookup_emission_factor",
        json.dumps({"category": "electricity_grid_sg", "unit": "kg CO2 / kWh", "year": 2025}),
    )
    result = toolkit.dispatch(
        "calculate_emissions",
        json.dumps({"document_id": "doc_test", "factor_ref": "factor_1"}),
    )
    assert result.status == "ok"
    return result.data


def _chat(monkeypatch, run: AgentRun) -> dict:
    monkeypatch.setattr(main, "run_agent", lambda session, message, **kw: run)
    response = client.post("/chat", json={"message": "Scope 2 for this bill?"})
    assert response.status_code == 200
    return response.json()


def test_figure_reaches_the_client_with_every_number_as_a_string(
    configured, monkeypatch, figure_payload
):
    """A float round-trip is the corruption the Decimal discipline exists to prevent."""
    body = _chat(
        monkeypatch,
        AgentRun(session_id="sess_x", reply="56,119.2 kg CO2e.", figures=[figure_payload]),
    )

    figure = body["figures"][0]
    assert figure["kg_co2e"] == figure_payload["kg_co2e"]
    assert figure["tonnes_co2e"] == figure_payload["tonnes_co2e"]
    for key in ("kg_co2e", "tonnes_co2e", "activity_amount"):
        assert isinstance(figure[key], str)
    assert isinstance(figure["factor"]["factor"], str)


def test_figure_carries_the_full_citation(configured, monkeypatch, figure_payload):
    """Rule 2 at the HTTP boundary: factor, unit, publisher, year, document, page."""
    body = _chat(monkeypatch, AgentRun(session_id="sess_x", reply="x", figures=[figure_payload]))
    factor = body["figures"][0]["factor"]

    for field in (
        "factor",
        "unit",
        "publisher",
        "year_used",
        "year_requested",
        "source_document",
        "source_url",
        "source_page",
    ):
        assert factor[field], f"{field} is missing from the figure's citation"


def test_both_years_travel_so_a_substitution_cannot_hide(configured, monkeypatch, figure_payload):
    body = _chat(monkeypatch, AgentRun(session_id="sess_x", reply="x", figures=[figure_payload]))
    factor = body["figures"][0]["factor"]

    assert factor["substituted"] is True
    assert factor["year_requested"] == 2025
    assert factor["year_used"] == 2024
    assert factor["substitution_note"]


def test_figure_carries_the_derivation_and_the_disclosure_sentences(
    configured, monkeypatch, figure_payload
):
    """The sentences are rendered server-side, so the UI composes no wording of its own."""
    body = _chat(monkeypatch, AgentRun(session_id="sess_x", reply="x", figures=[figure_payload]))
    figure = body["figures"][0]

    assert "x 0.402 kg CO2 / kWh =" in figure["derivation"]
    assert figure["method"] == "location-based"
    assert figure["basis"] == "activity-based"
    assert figure["required_disclosures"]
    assert any("not assured" in sentence.lower() for sentence in figure["required_disclosures"])


def test_a_factor_without_provenance_cannot_be_serialised():
    """There is deliberately no leaner variant of FactorOut to reach for."""
    with pytest.raises(ValidationError):
        FactorOut.model_validate({"category": "electricity_grid_sg", "factor": "0.402"})


def test_a_figure_without_its_inputs_cannot_be_serialised():
    with pytest.raises(KeyError):
        FigureOut.from_payload({"kg_co2e": "1", "tonnes_co2e": "0.001"})


def test_no_calculation_means_no_figure(configured, monkeypatch):
    """A refusal or a no_results answers with prose and nothing to display as a number."""
    body = _chat(
        monkeypatch,
        AgentRun(session_id="sess_x", reply="I cannot do that arithmetic.", figures=[]),
    )
    assert body["figures"] == []


def test_a_follow_up_turn_does_not_re_report_the_previous_turns_figure(session, toolkit):
    """`session.calculations` accumulates; a run reports only what it produced itself.

    Displaying a stale figure under a new question would show a number as though the agent
    had just computed it for that question.
    """
    first = run_agent(
        session,
        "Scope 2 for doc_test?",
        client=fake_client(
            call("extract_from_document", document_id="doc_test"),
            call(
                "lookup_emission_factor",
                category="electricity_grid_sg",
                unit="kg CO2 / kWh",
                year=2025,
            ),
            call("calculate_emissions", document_id="doc_test", factor_ref="factor_1"),
            "56,119.2 kg CO2e (EMA 2024). Not assured.",
        ),
        model="test-model",
        toolkit=toolkit,
    )
    assert len(first.figures) == 1

    second = run_agent(
        session,
        "What does location-based mean?",
        client=fake_client("It means the grid average factor for the market you draw from."),
        model="test-model",
        toolkit=toolkit,
    )

    assert second.figures == []
    assert session.calculations, "the earlier figure is still in the workspace"


def test_from_run_maps_every_figure_in_order(figure_payload):
    run = AgentRun(
        session_id="sess_x", reply="two figures", figures=[figure_payload, figure_payload]
    )
    assert len(ChatResponse.from_run(run).figures) == 2
