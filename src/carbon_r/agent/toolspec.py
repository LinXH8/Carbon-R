"""What the model is allowed to call, and the arguments it may pass.

One Pydantic model per tool, used for BOTH the JSON schema sent to the provider and the
validation of what comes back. A second, hand-written schema would be a place for the two
to disagree, and the disagreement would show up as a confusing provider error rather than
as a validation message.

The argument shapes are the load-bearing part of this module. Note what is NOT here:

    calculate_emissions does not take an amount, and does not take a factor.

It takes a `document_id` and a `factor_ref` - handles into the session workspace - and the
values are read from what the earlier tools stored. The model selects the inputs; Python
moves them. This is CLAUDE.md rule 1 again, one level up from `tools/calculate.py`: a
model that cannot write a digit into a calculation cannot mistype 0.402 as 0.42, and no
amount of prompt wording achieves that.

`lookup_emission_factor` keeps literal category/unit/year arguments, which is not an
exception to the above. Those are SELECTORS against the table's UNIQUE key, not
quantities, and rule 3 requires them exact. A wrong year is visible in the citation the
tool returns (`year_requested`), so it cannot pass silently.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


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


class CalculateArgs(BaseModel):
    """Arguments for `calculate_emissions`.

    No amount and no factor value: both are read from the session workspace. See the
    module docstring.
    """

    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(
        description="Document whose extracted consumption should be used as the activity "
        "amount. It must already have been read with extract_from_document."
    )
    factor_ref: str = Field(
        description="Handle of a factor returned by lookup_emission_factor, e.g. 'factor_1'."
    )


TOOL_DESCRIPTIONS: dict[str, str] = {
    "extract_from_document": (
        "Read an uploaded document with a vision model and return what is printed on it, "
        "together with the typed values, any field that could not be parsed, and any way "
        "in which the page disagrees with itself. Transcription only: it returns no "
        "derived or computed figure."
    ),
    "lookup_emission_factor": (
        "Fetch one emission factor by exact match on category, unit and year, with its "
        "full provenance (publisher, source document, page, URL). If no factor is "
        "published for the requested year, the latest published year at or before it is "
        "used and the substitution is stated; past a 2-year gap this returns no_results "
        "rather than a figure. Returns a factor_ref handle for calculate_emissions."
    ),
    "calculate_emissions": (
        "Multiply the consumption read from a document by a looked-up emission factor, "
        "in Python. This is the only tool that produces an emissions figure. It returns "
        "the figure, the derivation written out, every input it used, and the "
        "disclosures that must be stated with the result."
    ),
}

ARG_MODELS: dict[str, type[BaseModel]] = {
    "extract_from_document": ExtractArgs,
    "lookup_emission_factor": LookupFactorArgs,
    "calculate_emissions": CalculateArgs,
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
    "ExtractArgs",
    "LookupFactorArgs",
    "schema_for",
    "tool_schemas",
]
