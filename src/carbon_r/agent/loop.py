"""The tool-calling loop.

Ask the model, run whatever tools it asked for, hand the results back, repeat until it
answers in prose. Three properties matter more than the mechanics:

**It is bounded.** `MAX_TURNS` model calls, then it stops. A loop that can spin is a loop
that can cost money without producing an answer, and an agent that has run out of steps
must say so - it must not assemble a figure out of the transcript to look finished.

**Nothing escapes as an exception.** A tool that fails, a tool that does not exist,
arguments that are not valid JSON: all become `{status: error, ...}` fed back to the
model, which gets the remaining turns to correct itself. The only exception the loop
catches at the top level is the provider's own.

**The answer is audited.** Before the reply is returned, every number in it is checked
against the numbers the tools produced (`audit.py`). The result travels with the reply
rather than being logged away, so a caller can see it.

The provider client is injected, so the whole loop is testable offline with a fake that
scripts a sequence of tool calls - see `tests/test_agent_loop.py`.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from openai import OpenAI, OpenAIError

from carbon_r.agent.audit import audit_figures, audit_quotes
from carbon_r.agent.prompt import MAX_TURNS, SYSTEM_PROMPT
from carbon_r.agent.session import Session
from carbon_r.agent.tools import Toolkit
from carbon_r.agent.toolspec import tool_schemas
from carbon_r.config import get_settings

# One line per tool call, keyed by the run, so a reply on screen can be matched to the calls
# that produced it in the server log. The same id is the trace id if tracing is enabled.
log = logging.getLogger("carbon_r.agent")


class SupportsResponses(Protocol):  # pragma: no cover - a shape, not behaviour
    """The slice of the OpenAI client this loop uses."""

    @property
    def responses(self) -> Any: ...


@dataclass(frozen=True)
class ToolCallRecord:
    """One tool call, as it will be shown to the user. The transcript is the audit trail."""

    tool: str
    arguments: str
    status: str
    message: str


@dataclass
class AgentRun:
    """What one `/chat` turn produced."""

    session_id: str
    reply: str
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    turns_used: int = 0
    stopped_reason: str = "completed"  # completed | max_turns | provider_error
    unverified_figures: list[str] = field(default_factory=list)
    unverified_quotes: list[str] = field(default_factory=list)

    # The `calculate_emissions` payloads produced by THIS run, in order. Collected here
    # rather than read from `session.calculations` because that dict accumulates across
    # turns: a follow-up question would otherwise re-display the previous turn's figure as
    # though the agent had just computed it.
    figures: list[dict] = field(default_factory=list)


OUT_OF_STEPS = (
    "I ran out of steps before I could finish this answer ({used} of {allowed} turns "
    "used), so I am not going to state a figure. What I established so far:"
)

NOTHING_ESTABLISHED = (
    "I ran out of steps before I could finish this answer ({used} of {allowed} turns "
    "used), and nothing was established. Please ask again, or narrow the question to one "
    "document."
)


def _as_input_item(item: Any) -> dict:
    """A provider output item as a plain dict, so history stays serialisable."""
    if isinstance(item, dict):
        return item
    return item.model_dump(exclude_none=True)


def _unfinished_reply(session: Session, used: int, allowed: int) -> str:
    """An honest stop. Only tool messages - never a figure the model asserted."""
    established = [p["message"] for p in session.tool_payloads if p["status"] == "ok"]
    if not established:
        return NOTHING_ESTABLISHED.format(used=used, allowed=allowed)
    lines = "\n".join(f"- {message}" for message in established)
    return OUT_OF_STEPS.format(used=used, allowed=allowed) + "\n" + lines


def run_agent(
    session: Session,
    message: str,
    *,
    context: str = "",
    client: SupportsResponses | None = None,
    model: str | None = None,
    toolkit: Toolkit | None = None,
    max_turns: int = MAX_TURNS,
    on_event: Callable[[dict], None] | None = None,
) -> AgentRun:
    """One user message in, one answer out, with every tool call it took on the way.

    `on_event` is called as the run progresses - `{"type": "turn"}` before each provider
    call and `{"type": "tool", ...}` after each tool result - so a streaming caller can
    show the audit trail being built instead of inventing progress while it waits. It is
    optional and defaults to doing nothing, which is what every non-streaming caller wants.

    `context` is the server's note on which documents exist. The model reads it appended to
    `message`, but only `message` is recorded as the user's words for the quote audit.
    """
    emit = on_event or (lambda event: None)
    run_id = uuid.uuid4().hex
    log.info("run_id=%s session=%s start", run_id, session.session_id)
    settings = get_settings()
    model = model or settings.openai_model
    if client is None:
        client = OpenAI(api_key=settings.openai_api_key)
    toolkit = toolkit or Toolkit(session)

    session.user_messages.append(message)
    conversation: list[dict] = [*session.history, {"role": "user", "content": message + context}]
    calls: list[ToolCallRecord] = []
    schemas = tool_schemas()
    figures: list[dict] = []
    turns = 0
    reply = ""
    stopped = "completed"

    while turns < max_turns:
        turns += 1
        emit({"type": "turn", "n": turns, "of": max_turns})
        try:
            response = client.responses.create(
                model=model,
                instructions=SYSTEM_PROMPT,
                input=conversation,
                tools=schemas,
            )
        except OpenAIError as exc:
            stopped = "provider_error"
            reply = (
                "I could not reach the model provider, so I have no answer for you. "
                f"The error was: {exc}"
            )
            break

        output = list(response.output)
        conversation.extend(_as_input_item(item) for item in output)

        requested = [item for item in output if getattr(item, "type", None) == "function_call"]
        if not requested:
            reply = (response.output_text or "").strip()
            break

        for call in requested:
            result = toolkit.dispatch(call.name, call.arguments)
            log.info("run_id=%s tool=%s status=%s", run_id, call.name, result.status)
            calls.append(
                ToolCallRecord(
                    tool=call.name,
                    arguments=call.arguments,
                    status=result.status,
                    message=result.message,
                )
            )
            if call.name == "calculate_emissions" and result.status == "ok" and result.data:
                figures.append(result.data)
            emit(
                {
                    "type": "tool",
                    "tool": call.name,
                    "status": result.status,
                    "message": result.message,
                }
            )
            conversation.append(
                {
                    "type": "function_call_output",
                    "call_id": call.call_id,
                    "output": result.model_dump_json(),
                }
            )
    else:
        # The loop ended because the budget ran out, with tools still being called.
        stopped = "max_turns"
        reply = _unfinished_reply(session, turns, max_turns)

    session.history = conversation
    log.info("run_id=%s stopped=%s turns=%d", run_id, stopped, turns)
    return AgentRun(
        session_id=session.session_id,
        reply=reply,
        run_id=run_id,
        tool_calls=calls,
        turns_used=turns,
        stopped_reason=stopped,
        unverified_figures=audit_figures(reply, session.tool_payloads),
        unverified_quotes=audit_quotes(reply, session.tool_payloads, session.user_messages),
        figures=figures,
    )
