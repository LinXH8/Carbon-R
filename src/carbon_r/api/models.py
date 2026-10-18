"""Request and response models for /upload and /chat.

Pydantic at the boundary (CLAUDE.md): a client gets a 422 with the field named, rather
than a 500 three layers in. The response models matter as much as the requests - the tool
transcript and the figure audit are part of the contract, not debug output, because an
answer whose derivation cannot be inspected is the thing this project exists not to ship.
"""

from pydantic import BaseModel, ConfigDict, Field

from carbon_r.agent.loop import AgentRun


class UploadResponse(BaseModel):
    """What the caller needs to talk about an uploaded document: its id."""

    model_config = ConfigDict(frozen=True)

    session_id: str = Field(description="Pass this back to /chat to use the document")
    document_id: str = Field(description="How the agent addresses this document")
    filename: str = Field(description="The client's filename, kept for display only")
    content_type: str
    size_bytes: int


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, description="The user's question")
    session_id: str | None = Field(
        default=None, description="Continue an existing conversation; omit to start one"
    )
    document_ids: list[str] = Field(
        default_factory=list,
        description="Documents this question is about. Defaults to every document "
        "uploaded in the session.",
    )


class ToolCallOut(BaseModel):
    """One tool call in the order it happened. The user-visible audit trail."""

    model_config = ConfigDict(frozen=True)

    tool: str
    arguments: str
    status: str = Field(description="ok | no_results | error")
    message: str


class ChatResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    session_id: str
    reply: str
    tool_calls: list[ToolCallOut]
    turns_used: int
    stopped_reason: str = Field(description="completed | max_turns | provider_error")
    unverified_figures: list[str] = Field(
        description="Numbers in the reply that no tool produced. Must be empty; a "
        "non-empty list means the model stated a figure it was not given."
    )

    @classmethod
    def from_run(cls, run: AgentRun) -> "ChatResponse":
        return cls(
            session_id=run.session_id,
            reply=run.reply,
            tool_calls=[ToolCallOut(**vars(call)) for call in run.tool_calls],
            turns_used=run.turns_used,
            stopped_reason=run.stopped_reason,
            unverified_figures=run.unverified_figures,
        )
