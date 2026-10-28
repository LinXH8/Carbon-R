"""What the model is allowed to call, and the arguments it may pass.

One Pydantic model per tool, used for BOTH the JSON schema sent to the provider and the
validation of what comes back. A second, hand-written schema would be a place for the two
to disagree, and the disagreement would show up as a confusing provider error rather than
as a validation message.

The argument shapes are the load-bearing part of this module. Note what is NOT here:

    calculate_emissions does not take an amount, and does not take a factor.

It takes a list of `(activity_ref, factor_ref)` handles into the session workspace, and
the values are read from what the earlier tools stored. The model selects the inputs;
Python moves them. This is CLAUDE.md rule 1 again, one level up from
`tools/calculate.py`: a model that cannot write a digit into a calculation cannot mistype
0.402 as 0.42, and no amount of prompt wording achieves that.

The argument is a LIST because a fuel-card statement carries diesel and petrol, which
need two different factors and one total - and that total is a sum, which the model must
not perform. Pairing them in one call means Python does the addition. It also means a
partial result is impossible to request: the call either calculates every line or none.

`lookup_emission_factor` keeps literal category/unit/year arguments, which is not an
exception to the above. Those are SELECTORS against the table's UNIQUE key, not
quantities, and rule 3 requires them exact. A wrong year is visible in the citation the
tool returns (`year_requested`), so it cannot pass silently.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from carbon_r.peer_sources import load_peer_sources


class ExtractArgs(BaseModel):
    """Arguments for `extract_from_document`."""

    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(
        description="Id of a document uploaded in this session, as returned by /upload"
    )


class LookupFactorArgs(BaseModel):
    """Arguments for `lookup_emission_factor`."""

    model_config = ConfigDict(extra="forbid")

    category: str = Field(
        description="Factor category, e.g. 'electricity_grid_sg'. Matched exactly."
    )
    unit: str = Field(
        description="The publisher's own unit string, verbatim, e.g. 'kg CO2 / kWh'. "
        "Matched exactly, spaces included."
    )
    year: int = Field(
        description="The ACTIVITY year - the year the billing period starts in, taken "
        "from the extracted reading. Never the document date."
    )


class CalculatePair(BaseModel):
    """One line of a calculation: which activity, and which factor to apply to it."""

    model_config = ConfigDict(extra="forbid")

    activity_ref: str = Field(
        description="Handle of an activity returned by extract_from_document, e.g. 'act_1'."
    )
    factor_ref: str = Field(
        description="Handle of a factor returned by lookup_emission_factor, e.g. 'factor_1'."
    )


class CalculateArgs(BaseModel):
    """Arguments for `calculate_emissions`.

    No amount and no factor value: both are read from the session workspace. See the
    module docstring.
    """

    model_config = ConfigDict(extra="forbid")

    pairs: list[CalculatePair] = Field(
        description="One entry per activity to calculate. Pass EVERY activity from the "
        "document in a single call, each with the factor that applies to it, and the "
        "total is computed for you. If any pair is wrong, no figure is returned at all."
    )


class SearchMethodologyArgs(BaseModel):
    """Arguments for `search_methodology`."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(
        description="The methodology question, in plain words. Ask one thing at a time."
    )
    source_id: Literal["ghgp_corporate", "ghgp_scope2", "ghgp_scope3"] | None = Field(
        default=None,
        description="Restrict to one standard: ghgp_corporate (Corporate Standard), "
        "ghgp_scope2 (Scope 2 Guidance) or ghgp_scope3 (Scope 3 Standard). Null searches all.",
    )


# The companies whose reports are ingested, from the committed data/peer_sources.json. A
# Literal, so the model can only filter on a company that exists.
PEER_COMPANIES: tuple[str, ...] = tuple(sorted({p.company for p in load_peer_sources().sources}))


class SearchPeerDisclosuresArgs(BaseModel):
    """Arguments for `search_peer_disclosures`."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(
        description="The question about how companies report, in plain words. One thing at "
        "a time; for two companies, search each one separately."
    )
    company: Literal[PEER_COMPANIES] | None = Field(  # type: ignore[valid-type]
        default=None, description="Restrict to one company's report(s). Null searches all."
    )
    sector: Literal["bank", "reit", "industrial", "energy"] | None = Field(
        default=None, description="Restrict to one sector. Null searches all."
    )
    report_year: int | None = Field(
        default=None,
        description="The calendar year in which the report's period ENDS (a 1 April 2025 to "
        "31 March 2026 report is 2026). Null searches every year.",
    )


TOOL_DESCRIPTIONS: dict[str, str] = {
    "extract_from_document": (
        "Establish what kind of document an uploaded file is, then read it with a vision "
        "model and return what is printed on it, together with the typed values, the "
        "measurable activities it carries as act_N handles, any field that could not be "
        "parsed, and any way in which the page disagrees with itself. Handles electricity "
        "bills, LPG invoices and vehicle fuel-card statements; a file whose kind cannot be "
        "established returns no_results rather than being read as a guess. Transcription "
        "only: it returns no derived or computed emissions figure."
    ),
    "lookup_emission_factor": (
        "Fetch one emission factor by exact match on category, unit and year, with its "
        "full provenance (publisher, source document, page, URL, scope, method and "
        "whether it is CO2 or CO2e). If no factor is published for the requested year, "
        "the latest published year at or before it is used and the substitution is "
        "stated; past a 2-year gap, or with nothing published at or before the year, this "
        "returns no_results rather than a figure. Returns a factor_ref handle for "
        "calculate_emissions. One call per activity: different fuels need different rows."
    ),
    "calculate_emissions": (
        "Multiply each activity read from a document by the emission factor that applies "
        "to it, in Python, and total the lines. This is the only tool that produces an "
        "emissions figure. It returns the total, every line with its own derivation "
        "written out, every input it used, and the disclosures that must be stated with "
        "the result. Pass all of a document's activities in ONE call: that is what "
        "produces the total. If any pair cannot be calculated, the whole call fails and "
        "returns no figure, rather than a total that is quietly short."
    ),
    "search_methodology": (
        "Search the GHG Protocol Corporate Standard, Scope 2 Guidance and Scope 3 Standard "
        "for passages that answer a methodology question, and return verified verbatim "
        "quotes with their document and page. Returns no_results when nothing in those "
        "documents is close to the question, or when the closest passages do not actually "
        "answer it. Never returns an emission factor and never produces a figure."
    ),
    "search_peer_disclosures": (
        "Search the sustainability reports of 12 Singapore-listed companies (banks, REITs, "
        "industrials, energy) for passages on how they report emissions - boundaries, "
        "targets, methods, financed emissions - and return verified verbatim quotes with "
        "company, report and page. Filters by company, sector and report year. Returns "
        "passages, never figures: numbers in report text are not reliable, so cite the page "
        "instead of stating one. Never an input to a calculation."
    ),
}

ARG_MODELS: dict[str, type[BaseModel]] = {
    "extract_from_document": ExtractArgs,
    "lookup_emission_factor": LookupFactorArgs,
    "calculate_emissions": CalculateArgs,
    "search_methodology": SearchMethodologyArgs,
    "search_peer_disclosures": SearchPeerDisclosuresArgs,
}

# Schema keywords the provider's strict mode does not accept. Stripped rather than
# avoided in the models, because `title` is emitted by Pydantic for every field and
# `default` is emitted for every field that has one.
_STRIP_KEYS = ("title", "default")


def _strict(node: Any) -> Any:
    """Make one schema node satisfy strict function-calling rules, recursively.

    Strict mode requires every object to forbid extra properties and to list every
    property as required. Pydantic marks a field with a default as optional, which strict
    mode rejects - so required is rebuilt from the full property list rather than trusted.
    """
    if isinstance(node, list):
        return [_strict(item) for item in node]
    if not isinstance(node, dict):
        return node

    out = {key: _strict(value) for key, value in node.items() if key not in _STRIP_KEYS}
    if out.get("type") == "object":
        out["additionalProperties"] = False
        out["required"] = list(out.get("properties", {}))
    return out


def schema_for(model: type[BaseModel]) -> dict:
    """The strict JSON schema for one argument model."""
    return _strict(model.model_json_schema())


def tool_schemas() -> list[dict]:
    """The `tools` payload for the Responses API. Order is stable across calls."""
    return [
        {
            "type": "function",
            "name": name,
            "description": TOOL_DESCRIPTIONS[name],
            "parameters": schema_for(ARG_MODELS[name]),
            "strict": True,
        }
        for name in ARG_MODELS
    ]


TOOL_NAMES = tuple(ARG_MODELS)

__all__ = [
    "ARG_MODELS",
    "TOOL_DESCRIPTIONS",
    "TOOL_NAMES",
    "CalculateArgs",
    "CalculatePair",
    "ExtractArgs",
    "LookupFactorArgs",
    "SearchMethodologyArgs",
    "SearchPeerDisclosuresArgs",
    "schema_for",
    "tool_schemas",
]
