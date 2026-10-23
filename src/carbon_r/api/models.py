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


class FactorOut(BaseModel):
    """An emission factor as the client receives it: never without its provenance.

    This is CLAUDE.md rule 2 at the HTTP boundary. `FactorCitation` enforces it as a type
    inside the process; without a response model that carries the same fields, a figure
    could still reach a screen as a bare number. There is deliberately no leaner variant
    of this model to reach for - every field below is part of being able to re-derive the
    figure by hand.

    Numbers stay strings, exactly as `calculate_emissions` returned them. A float
    round-trip through JSON is the corruption the `Decimal` discipline exists to prevent.
    """

    model_config = ConfigDict(frozen=True)

    category: str
    factor: str = Field(description="The factor value, as a decimal string")
    unit: str = Field(description="The publisher's own unit string, verbatim")

    # Two years, never one: a result that reported only `year_used` would hide the
    # substitution it made.
    year_requested: int
    year_used: int
    substituted: bool
    substitution_note: str | None = None

    publisher: str
    source_document: str
    source_url: str
    source_page: str | None = None
    retrieved_at: str
    jurisdiction: str
    verified: bool = Field(description="The value has been read from the publisher's own document")
    notes: str | None = None

    # What the factor classifies as. Nullable to match the column, which could not be made
    # NOT NULL on a populated table; a null here means the factor table needs reloading,
    # and `calculate_emissions` refuses such a factor before it ever reaches this model.
    scope: int | None = None
    method: str | None = None
    gas_basis: str | None = Field(
        default=None, description="CO2 = carbon dioxide only; CO2e = all gases as equivalent"
    )


class ConversionOut(BaseModel):
    """A unit conversion applied before a factor, with the authority for it.

    Present only when the activity's unit and the factor's unit differed - LPG in kg
    against a factor published per tonne. A conversion that happened without appearing
    here would be a step in the arithmetic the client cannot see.
    """

    model_config = ConfigDict(frozen=True)

    from_unit: str
    to_unit: str
    from_amount: str
    to_amount: str
    statement: str = Field(description="The conversion in words, e.g. '1 tonne = 1000 kg'")
    source: str


class FigureLineOut(BaseModel):
    """One line of a figure: one activity, one factor, one product.

    A single-activity document produces exactly one of these, so the client renders one
    shape whether it is reading an electricity bill or a two-product fuel-card statement.
    """

    model_config = ConfigDict(frozen=True)

    kind: str = Field(description="What was consumed: electricity, lpg, diesel, petrol")
    kg_co2e: str
    tonnes_co2e: str
    derivation: str = Field(description="This line's arithmetic, written out")

    # From the factor, not composed by the client: a page that captioned a figure itself
    # would be asserting a classification it was never given.
    scope: int | None = None
    method: str | None = None
    gas_basis: str | None = None

    activity_amount: str
    activity_unit: str
    activity_derivation: str | None = Field(
        default=None,
        description="How the quantity was computed when the page did not print it, "
        "e.g. the cylinder arithmetic behind an LPG figure",
    )
    conversion: ConversionOut | None = None
    factor: FactorOut

    @classmethod
    def from_line(cls, line: dict) -> "FigureLineOut":
        activity = line["activity"]
        return cls(
            kind=line["kind"],
            kg_co2e=line["kg_co2e"],
            tonnes_co2e=line["tonnes_co2e"],
            derivation=line["derivation"],
            scope=line["scope"],
            method=line["method"],
            gas_basis=line["gas_basis"],
            activity_amount=activity["amount"],
            activity_unit=activity["unit"],
            activity_derivation=activity.get("derivation"),
            conversion=(
                ConversionOut.model_validate(line["conversion"]) if line["conversion"] else None
            ),
            factor=FactorOut.model_validate(line["factor"]),
        )


class FigureOut(BaseModel):
    """One emissions result: a total, and every line it was computed from.

    The UI renders numbers from this and from nothing else. Scraping them out of `reply`
    would put a model-authored figure on screen, which is the same defect as rule 1 with
    the arithmetic moved into the presentation layer.

    The total and the lines both cross the boundary because a total alone cannot be
    checked against the document, and a mixed-scope total cannot honestly be reported as
    belonging to any one scope - which is what `kg_co2e_by_scope` is for.
    """

    model_config = ConfigDict(frozen=True)

    kg_co2e: str = Field(description="The total across every line")
    tonnes_co2e: str
    derivation: str = Field(description="The arithmetic written out, checkable by hand")

    lines: list[FigureLineOut]
    scopes: list[int] = Field(description="Every GHG Protocol scope this total covers")
    methods: list[str]
    gas_bases: list[str]
    kg_co2e_by_scope: dict[str, str] = Field(
        description="Total per scope, so a mixed result is never only one number"
    )

    disclosures: dict[str, bool] = Field(description="The flags; see required_disclosures")
    required_disclosures: list[str] = Field(
        description="The sentences this figure must be presented with, rendered server-side "
        "so the wording cannot drift between the API and the UI."
    )

    document_ids: list[str]
    calculation_ref: str = Field(description="The session handle this figure is stored under")

    @classmethod
    def from_payload(cls, data: dict) -> "FigureOut":
        """Reshape a `calculate_emissions` payload for the wire."""
        return cls(
            kg_co2e=data["kg_co2e"],
            tonnes_co2e=data["tonnes_co2e"],
            derivation=data["derivation"],
            lines=[FigureLineOut.from_line(line) for line in data["lines"]],
            scopes=data["scopes"],
            methods=data["methods"],
            gas_bases=data["gas_bases"],
            kg_co2e_by_scope=data["kg_co2e_by_scope"],
            disclosures=data["disclosures"],
            required_disclosures=data["required_disclosures"],
            document_ids=data["document_ids"],
            calculation_ref=data["calculation_ref"],
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
    run_id: str = Field(description="Identifies this turn in the server log (and trace, if on)")
    reply: str
    tool_calls: list[ToolCallOut]
    turns_used: int
    stopped_reason: str = Field(description="completed | max_turns | provider_error")
    unverified_figures: list[str] = Field(
        description="Numbers in the reply that no tool produced. Must be empty; a "
        "non-empty list means the model stated a figure it was not given."
    )
    unverified_quotes: list[str] = Field(
        default_factory=list,
        description="Quoted spans in the reply that no tool returned verbatim. Must be "
        "empty; a non-empty list means the model put words in a source's mouth.",
    )
    figures: list[FigureOut] = Field(
        default_factory=list,
        description="Every figure `calculate_emissions` produced in this turn, with the "
        "inputs it used. Clients display these; they do not parse `reply`.",
    )

    @classmethod
    def from_run(cls, run: AgentRun) -> "ChatResponse":
        return cls(
            session_id=run.session_id,
            run_id=run.run_id,
            reply=run.reply,
            tool_calls=[ToolCallOut(**vars(call)) for call in run.tool_calls],
            turns_used=run.turns_used,
            stopped_reason=run.stopped_reason,
            unverified_figures=run.unverified_figures,
            unverified_quotes=run.unverified_quotes,
            figures=[FigureOut.from_payload(payload) for payload in run.figures],
        )
