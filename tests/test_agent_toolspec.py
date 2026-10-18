"""The tool schemas sent to the provider, and the validation of what comes back.

Both come from the same Pydantic models, and these tests are what keep that true: a tool
added to the dispatch table without a schema (or the reverse) is caught here rather than
as a provider error at request time.
"""

import json

import pytest

from carbon_r.agent.toolspec import (
    ARG_MODELS,
    TOOL_DESCRIPTIONS,
    CalculateArgs,
    ExtractArgs,
    LookupFactorArgs,
    schema_for,
    tool_schemas,
)


def test_every_tool_has_a_description_and_a_schema():
    assert set(ARG_MODELS) == set(TOOL_DESCRIPTIONS)
    assert {schema["name"] for schema in tool_schemas()} == set(ARG_MODELS)


def test_schemas_are_strict():
    for schema in tool_schemas():
        assert schema["strict"] is True
        assert schema["type"] == "function"
        params = schema["parameters"]
        assert params["additionalProperties"] is False
        # Strict mode requires EVERY property to be required, including any with a default.
        assert set(params["required"]) == set(params["properties"])


def test_schemas_carry_no_keyword_strict_mode_rejects():
    serialised = json.dumps(tool_schemas())
    assert '"title"' not in serialised
    assert '"default"' not in serialised


def test_every_argument_is_described():
    for schema in tool_schemas():
        for name, prop in schema["parameters"]["properties"].items():
            assert prop.get("description"), f"{schema['name']}.{name} has no description"


def test_calculate_takes_handles_not_values():
    """The central rule: the model cannot put a quantity into a calculation."""
    properties = schema_for(CalculateArgs)["properties"]
    assert set(properties) == {"document_id", "factor_ref"}
    assert not any("amount" in name or "factor_value" in name for name in properties)


def test_argument_models_reject_unknown_fields():
    with pytest.raises(ValueError):
        ExtractArgs.model_validate({"document_id": "doc_1", "path": "/etc/passwd"})


def test_argument_models_require_their_fields():
    with pytest.raises(ValueError):
        LookupFactorArgs.model_validate({"category": "electricity_grid_sg", "unit": "kg CO2 / kWh"})


def test_year_is_an_integer():
    assert (
        LookupFactorArgs.model_validate({"category": "c", "unit": "u", "year": "2025"}).year == 2025
    )
