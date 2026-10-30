"""The loop: bounded, exception-free, and honest when it cannot finish.

The provider is a fake that replays scripted turns, so the whole loop - tool dispatch,
the workspace, the turn budget, the audit - runs with no API key and no network.
"""

from openai import APIConnectionError

from carbon_r.agent.loop import run_agent
from carbon_r.agent.prompt import SYSTEM_PROMPT
from conftest import call, fake_client

ANSWER = (
    "Scope 2 emissions are 56,119.2 kg CO2e (56.1192 tonnes): 139,600 kWh x 0.402 "
    "kg CO2 / kWh, location-based, activity-based. Factor: EMA, Singapore Energy "
    "Statistics 2025, Table 2.4, 2024 - no 2025 factor is published yet, so the 2024 "
    "one was used. These estimates are not assured."
)

FULL_SEQUENCE = (
    call("extract_from_document", document_id="doc_test"),
    call(
        "lookup_emission_factor",
        category="electricity_grid_sg",
        unit="kg CO2 / kWh",
        year=2025,
    ),
    call("calculate_emissions", pairs=[{"activity_ref": "act_1", "factor_ref": "factor_1"}]),
    ANSWER,
)


def test_the_normal_sequence_produces_a_cited_answer(session, toolkit):
    run = run_agent(
        session,
        "What are the Scope 2 emissions?",
        client=fake_client(*FULL_SEQUENCE),
        model="m",
        toolkit=toolkit,
    )

    assert run.stopped_reason == "completed"
    assert run.turns_used == 4
    assert [c.tool for c in run.tool_calls] == [
        "extract_from_document",
        "lookup_emission_factor",
        "calculate_emissions",
    ]
    assert all(c.status == "ok" for c in run.tool_calls)
    assert run.unverified_figures == []
    assert "56,119.2" in run.reply


def test_the_system_prompt_is_sent_every_turn(session, toolkit):
    client = fake_client(*FULL_SEQUENCE)
    run_agent(session, "question", client=client, model="m", toolkit=toolkit)
    assert len(client.responses.requests) == 4
    for request in client.responses.requests:
        assert request["instructions"] == SYSTEM_PROMPT
        assert {t["name"] for t in request["tools"]} == {
            "extract_from_document",
            "lookup_emission_factor",
            "calculate_emissions",
            "search_methodology",
            "search_peer_disclosures",
        }


def test_tool_output_is_fed_back_against_the_call_id(session, toolkit):
    client = fake_client(*FULL_SEQUENCE)
    run_agent(session, "question", client=client, model="m", toolkit=toolkit)
    last_input = client.responses.requests[-1]["input"]
    outputs = [i for i in last_input if i.get("type") == "function_call_output"]
    assert len(outputs) == 3
    assert all(o["call_id"] == "call_1" for o in outputs)
    assert '"status":"ok"' in outputs[-1]["output"]


def test_a_second_turn_keeps_the_conversation(session, toolkit):
    run_agent(session, "first", client=fake_client(*FULL_SEQUENCE), model="m", toolkit=toolkit)
    before = len(session.history)
    run_agent(session, "and in tonnes?", client=fake_client(ANSWER), model="m", toolkit=toolkit)
    assert len(session.history) > before
    assert session.history[0]["content"] == "first"


def test_quoting_the_users_question_back_is_not_flagged(session, toolkit):
    """The user's messages in this session, earlier turns included, are a quote source."""
    asked = "Where is the best Hainanese chicken rice?"
    run_agent(session, asked, client=fake_client("I cannot say."), model="m", toolkit=toolkit)
    reply = 'I cannot rank "best Hainanese chicken rice" or say "which stall is cheapest".'
    run = run_agent(session, "Why not?", client=fake_client(reply), model="m", toolkit=toolkit)
    assert run.unverified_quotes == ["which stall is cheapest"]


def test_the_servers_document_context_is_not_the_users_words(session, toolkit):
    """The model reads the context; a reply quoting it gets no pass as the user's text."""
    context = "\n\n[No documents have been uploaded in this session.]"
    reply = 'You said "No documents have been uploaded in this session."'
    client = fake_client(reply)
    run = run_agent(session, "Scope 2?", context=context, client=client, model="m", toolkit=toolkit)
    assert session.history[0]["content"] == "Scope 2?" + context  # what the model was sent
    assert session.user_messages == ["Scope 2?"]
    assert run.unverified_quotes == ["No documents have been uploaded in this session."]


# --- the guards -------------------------------------------------------------------


def test_the_turn_budget_is_enforced(session, toolkit):
    """A model that keeps calling tools is stopped, and says so without a figure."""
    forever = [call("extract_from_document", document_id="doc_test") for _ in range(20)]
    run = run_agent(
        session, "question", client=fake_client(*forever), model="m", toolkit=toolkit, max_turns=4
    )

    assert run.stopped_reason == "max_turns"
    assert run.turns_used == 4
    assert "ran out of steps" in run.reply
    assert "4 of 4" in run.reply
    assert run.unverified_figures == []


def test_a_run_that_establishes_nothing_says_nothing(session, toolkit):
    run = run_agent(
        session,
        "question",
        client=fake_client(*[call("no_such_tool") for _ in range(5)]),
        model="m",
        toolkit=toolkit,
        max_turns=2,
    )
    assert run.stopped_reason == "max_turns"
    assert "nothing was established" in run.reply


def test_an_unknown_tool_costs_a_turn_not_the_request(session, toolkit):
    run = run_agent(
        session,
        "question",
        client=fake_client(call("lookup_the_answer"), ANSWER),
        model="m",
        toolkit=toolkit,
    )
    assert run.stopped_reason == "completed"
    assert run.tool_calls[0].status == "error"


def test_a_provider_failure_is_reported_not_raised(session, toolkit):
    failure = APIConnectionError(request=None)  # type: ignore[arg-type]
    run = run_agent(session, "question", client=fake_client(failure), model="m", toolkit=toolkit)
    assert run.stopped_reason == "provider_error"
    assert "could not reach the model provider" in run.reply
    assert run.tool_calls == []


def test_a_no_results_tool_result_reaches_the_model(session, toolkit):
    """The loop does not decide what a gap means; it hands it back and lets the agent speak."""
    client = fake_client(
        call("calculate_emissions", pairs=[{"activity_ref": "act_1", "factor_ref": "factor_1"}]),
        "I could not calculate that: the document has not been read yet.",
    )
    run = run_agent(session, "question", client=client, model="m", toolkit=toolkit)
    assert run.tool_calls[0].status == "error"
    assert "could not calculate" in run.reply


# --- the audit, wired in ----------------------------------------------------------


def test_an_invented_figure_is_flagged(session, toolkit):
    invented = "Your annual emissions will be about 673,430 kg CO2e."
    run = run_agent(
        session,
        "question",
        client=fake_client(*FULL_SEQUENCE[:3], invented),
        model="m",
        toolkit=toolkit,
    )
    assert run.unverified_figures == ["673,430"]


def test_every_run_has_its_own_run_id(session, toolkit):
    first = run_agent(session, "q", client=fake_client(*FULL_SEQUENCE), model="m", toolkit=toolkit)
    second = run_agent(session, "q", client=fake_client(*FULL_SEQUENCE), model="m", toolkit=toolkit)
    assert len(first.run_id) == 32 and first.run_id != second.run_id
