"""The shape every tool returns.

One definition, shared by all tools, because the agent loop in Block 4 branches on
`status` and must never have to ask "what does this particular tool do on a miss?".

`{status, data, message}` with `status` in `ok | no_results | error` is a convention from
CLAUDE.md, and the reason for it is rule 7: a tool that signalled failure by returning
empty data would let a caller mistake "nothing found" for "nothing to report". The three
statuses mean different things to the agent:

    ok          the tool did its job; `data` is present
    no_results  the tool worked, and the answer is that there is nothing - the agent says
                so and asks, it does not guess or interpolate
    error       the tool could not run (bad input, provider or database failure)

`message` is always populated, including on `ok`, so a transcript of tool calls reads as
an explanation rather than as a dump of dictionaries.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Status = Literal["ok", "no_results", "error"]


class ToolResult(BaseModel):
    """What a tool hands back. Callers branch on `status`, never on `data` being truthy."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Status
    data: dict | None = Field(
        default=None, description="Present on ok; may be present on no_results for context"
    )
    message: str = Field(description="Human-readable account of what happened; never empty")

    @property
    def succeeded(self) -> bool:
        return self.status == "ok"


def ok(data: dict, message: str) -> ToolResult:
    return ToolResult(status="ok", data=data, message=message)


def no_results(message: str, data: dict | None = None) -> ToolResult:
    """Not an error: the tool ran and the honest answer is that there is nothing.

    `data` is optional context for the agent's explanation - e.g. which years *are*
    available when the requested one is not.
    """
    return ToolResult(status="no_results", data=data, message=message)


def error(message: str) -> ToolResult:
    return ToolResult(status="error", data=None, message=message)
