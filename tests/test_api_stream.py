"""`POST /chat/stream` - the same turn as /chat, with the tool results pushed as they land.

What matters here is the contract the page depends on: the events arrive in the order the
work happened, and **every run ends with exactly one `final`** - including a run the
provider broke. One terminal shape means the client has one parse path and no branch in
which it waits forever for an event that is never coming.

Offline: `run_agent` is replaced with a recorder that emits the events a real run would.
"""

import io
import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from carbon_r import main
from carbon_r.agent.loop import AgentRun, ToolCallRecord
from carbon_r.agent.session import reset_sessions
from carbon_r.api import stream as stream_module

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


def scripted(monkeypatch, *, events, run: AgentRun):
    """Replace the agent with one that emits `events` and then returns `run`."""

    def fake_run_agent(session, message, *, on_event=None, **kwargs):
        for event in events:
            if on_event:
                on_event(event)
        # The session id is the server's, not the fixture's.
        return replace(run, session_id=session.session_id)

    monkeypatch.setattr(stream_module, "run_agent", fake_run_agent)


def events_from(response) -> list[dict]:
    """Parse the SSE body into the event dicts a client would see."""
    assert response.headers["content-type"].startswith("text/event-stream")
    return [
        json.loads(line[len("data:") :].strip())
        for line in response.text.splitlines()
        if line.startswith("data:")
    ]


TOOL_EVENTS = [
    {"type": "turn", "n": 1, "of": 8},
    {"type": "tool", "tool": "extract_from_document", "status": "ok", "message": "Read it."},
    {"type": "turn", "n": 2, "of": 8},
    {"type": "tool", "tool": "lookup_emission_factor", "status": "ok", "message": "0.402."},
]

ANSWERED = AgentRun(
    session_id="replaced",
    reply="56,119.2 kg CO2e (EMA 2024). Not assured.",
    tool_calls=[ToolCallRecord("extract_from_document", "{}", "ok", "Read it.")],
    turns_used=3,
)


def ask(session_id: str | None = None):
    body = {"message": "Scope 2 for this bill?"}
    if session_id:
        body["session_id"] = session_id
    return client.post("/chat/stream", json=body)


def test_tool_events_arrive_before_the_final_event(configured, monkeypatch):
    scripted(monkeypatch, events=TOOL_EVENTS, run=ANSWERED)
    events = events_from(ask())

    kinds = [event["type"] for event in events]
    assert kinds[-1] == "final"
    assert kinds.count("final") == 1
    assert kinds[:-1] == ["turn", "tool", "turn", "tool"]


def test_tool_events_carry_what_the_step_rows_show(configured, monkeypatch):
    scripted(monkeypatch, events=TOOL_EVENTS, run=ANSWERED)
    tools = [e for e in events_from(ask()) if e["type"] == "tool"]

    assert [t["tool"] for t in tools] == ["extract_from_document", "lookup_emission_factor"]
    assert all(t["status"] == "ok" and t["message"] for t in tools)


def test_the_final_event_is_the_chat_response(configured, monkeypatch):
    """Same fields as /chat, so one renderer serves both routes."""
    scripted(monkeypatch, events=TOOL_EVENTS, run=ANSWERED)
    final = events_from(ask())[-1]

    assert set(final) == {
        "type",
        "session_id",
        "reply",
        "tool_calls",
        "turns_used",
        "stopped_reason",
        "unverified_figures",
        "figures",
    }
    assert final["reply"] == ANSWERED.reply
    assert final["stopped_reason"] == "completed"
    assert final["unverified_figures"] == []


def test_a_provider_failure_still_ends_in_one_final_event(configured, monkeypatch):
    broken = AgentRun(
        session_id="replaced",
        reply="I could not reach the model provider, so I have no answer for you.",
        turns_used=1,
        stopped_reason="provider_error",
    )
    scripted(monkeypatch, events=[{"type": "turn", "n": 1, "of": 8}], run=broken)
    events = events_from(ask())

    assert [e["type"] for e in events] == ["turn", "final"]
    assert events[-1]["stopped_reason"] == "provider_error"
    assert events[-1]["figures"] == []


def test_a_run_that_calls_no_tool_still_finals(configured, monkeypatch):
    """The rule-1 refusal path: no tool calls, one turn, an honest answer."""
    refusal = AgentRun(
        session_id="replaced",
        reply="I can't do that arithmetic outside the calculation tool.",
        turns_used=1,
    )
    scripted(monkeypatch, events=[{"type": "turn", "n": 1, "of": 8}], run=refusal)

    final = events_from(ask())[-1]
    assert final["figures"] == []
    assert final["tool_calls"] == []


def test_the_stream_refuses_what_chat_refuses(monkeypatch):
    """Shared `_prepare_turn`: an unconfigured provider is a 503 on both routes."""
    settings = main.get_settings()
    monkeypatch.setattr(settings, "openai_api_key", None, raising=False)
    monkeypatch.setattr(settings, "openai_model", None, raising=False)

    assert ask().status_code == 503
    assert client.post("/chat", json={"message": "hi"}).status_code == 503


def test_an_unknown_document_is_a_404_on_the_stream_too(configured, monkeypatch):
    scripted(monkeypatch, events=[], run=ANSWERED)
    uploaded = client.post(
        "/upload",
        files={"file": ("elec_001.pdf", io.BytesIO(b"%PDF-1.4 x"), "application/pdf")},
    ).json()

    response = client.post(
        "/chat/stream",
        json={
            "message": "Scope 2?",
            "session_id": uploaded["session_id"],
            "document_ids": ["doc_nope"],
        },
    )
    assert response.status_code == 404
    assert "doc_nope" in response.json()["detail"]


def test_the_stream_is_not_buffered_by_a_proxy(configured, monkeypatch):
    scripted(monkeypatch, events=TOOL_EVENTS, run=ANSWERED)
    response = ask()

    assert response.headers["x-accel-buffering"] == "no"
    assert response.headers["cache-control"] == "no-cache"
