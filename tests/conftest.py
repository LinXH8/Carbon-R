"""Shared fixtures for the agent tests.

Everything here is offline. No `.env`, no database, no API key, no network: the tools are
substituted with functions that return realistic `ToolResult`s, and the provider client
is a fake that replays a scripted sequence. That is a property of the whole suite worth
keeping - a fresh clone runs `make check` before it has credentials.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from carbon_r.agent.session import Session, StoredDocument, reset_sessions
from carbon_r.agent.tools import Toolkit
from carbon_r.tools.base import ToolResult, ok

# A real citation, in the shape `lookup_emission_factor` returns: EMA's 2024 grid factor.
FACTOR_2024 = {
    "category": "electricity_grid_sg",
    "factor": "0.402",
    "unit": "kg CO2 / kWh",
    "year_requested": 2025,
    "year_used": 2024,
    "substituted": True,
    "substitution_note": (
        "No 2025 factor is published for this category; used the 2024 factor instead "
        "(a 1-year substitution). Energy Market Authority (EMA) publishes in arrears."
    ),
    "publisher": "Energy Market Authority (EMA)",
    "source_document": "Singapore Energy Statistics 2025, Chapter 2",
    "source_url": "https://www.ema.gov.sg/singapore-energy-statistics",
    "source_page": "Table 2.4",
    "retrieved_at": "2026-10-16",
    "jurisdiction": "SG",
    "verified": True,
    "notes": None,
    "scope": 2,
    "method": "location-based",
    "gas_basis": "CO2",
}

# DESNZ's 2026 LPG factor, as `lookup_emission_factor` returns it. Published per TONNE,
# which is why a kg activity has to be converted before it can be applied - the one
# conversion in the project, and the reason this fixture exists.
FACTOR_LPG_2026 = {
    "category": "fuel_lpg",
    "factor": "2939.36095",
    "unit": "kg CO2e / tonnes",
    "year_requested": 2026,
    "year_used": 2026,
    "substituted": False,
    "substitution_note": None,
    "publisher": "Department for Energy Security and Net Zero",
    "source_document": (
        "UK Government GHG Conversion Factors for Company Reporting 2026, full set, sheet 'Fuels'"
    ),
    "source_url": (
        "https://www.gov.uk/government/publications/"
        "greenhouse-gas-reporting-conversion-factors-2026"
    ),
    "source_page": "sheet 'Fuels', row 35, column D (LPG, tonnes)",
    "retrieved_at": "2026-10-18",
    "jurisdiction": "UK",
    "verified": True,
    "notes": "LPG combustion, all greenhouse gases as CO2e.",
    "scope": 1,
    "method": "combustion",
    "gas_basis": "CO2e",
}

# DESNZ's 2026 diesel and petrol factors, per litre. Two rows because a fuel-card
# statement needs both at once, which is the case the pairs-based calculation exists for.
FACTOR_DIESEL_2026 = dict(
    FACTOR_LPG_2026,
    category="fuel_diesel",
    factor="2.66155",
    unit="kg CO2e / litres",
    source_page="sheet 'Fuels', row 76, column D (Diesel (100% mineral diesel), litres)",
    notes="Diesel combustion, all greenhouse gases as CO2e.",
)
FACTOR_PETROL_2026 = dict(
    FACTOR_LPG_2026,
    category="fuel_petrol",
    factor="2.35372",
    unit="kg CO2e / litres",
    source_page="sheet 'Fuels', row 100, column D (Petrol (100% mineral petrol), litres)",
    notes="Petrol combustion, all greenhouse gases as CO2e.",
)

# Keyed by the category the agent asks for, so a fake lookup can serve any of them.
FACTORS_BY_CATEGORY = {
    "electricity_grid_sg": FACTOR_2024,
    "fuel_lpg": FACTOR_LPG_2026,
    "fuel_diesel": FACTOR_DIESEL_2026,
    "fuel_petrol": FACTOR_PETROL_2026,
}

# A reading in the shape `extract_from_document` returns, trimmed as the toolkit trims it.
NORMALISED = {
    "vendor_name": "Syntheon Power Pte Ltd",
    "customer_name": "Makan Mates Pte Ltd",
    "document_date": "2025-12-04",
    "period_start": "2025-11-01",
    "period_end": "2025-11-30",
    "consumption_kwh": 139600,
    "is_estimated_reading": False,
    "tariff_cents_per_kwh": "27.65",
    "energy_charge_sgd": "38599.40",
    "fixed_charge_sgd": "180.00",
    "subtotal_sgd": "38779.40",
    "gst_rate": "0.09",
    "gst_amount_sgd": "3490.15",
    "total_sgd": "42269.55",
}


# The activity an electricity bill carries, in the shape `extract_from_document` returns.
ELECTRICITY_ACTIVITY = {
    "kind": "electricity",
    "amount": "139600",
    "unit": "kWh",
    "source_field": "consumption",
    "derivation": None,
    "is_estimated": False,
    "factor_year": 2025,
    "period_start": "2025-11-01",
    "period_end": "2025-11-30",
}

# The two activities a mixed fuel-card statement carries. Litres, not money, and one
# entry per product because they take different factors.
FUEL_ACTIVITIES = [
    {
        "kind": "diesel",
        "amount": "156.79",
        "unit": "litres",
        "source_field": "transactions.litres",
        "derivation": "3 diesel transaction(s): 52.30 + 48.21 + 56.28 = 156.79 litres",
        "is_estimated": False,
        "factor_year": 2026,
        "period_start": "2026-02-01",
        "period_end": "2026-02-28",
    },
    {
        "kind": "petrol",
        "amount": "116.79",
        "unit": "litres",
        "source_field": "transactions.litres",
        "derivation": "3 petrol transaction(s): 38.11 + 41.02 + 37.66 = 116.79 litres",
        "is_estimated": False,
        "factor_year": 2026,
        "period_start": "2026-02-01",
        "period_end": "2026-02-28",
    },
]

# The LPG activity, which the page never prints: it comes from the cylinder arithmetic.
LPG_ACTIVITY = {
    "kind": "lpg",
    "amount": "250",
    "unit": "kg",
    "source_field": "cylinder_deliveries",
    "derivation": "5 cylinders x 50 kg = 250 kg",
    "is_estimated": False,
}


def extract_payload(
    document_id: str = "doc_test",
    filename: str = "elec_001.pdf",
    *,
    document_kind: str = "electricity_bill",
    normalised: dict | None = None,
    activities: list[dict] | None = None,
) -> dict:
    """What the real tool returns, before the toolkit trims it."""
    return {
        "document": filename,
        "document_kind": document_kind,
        "classification": {
            "document_kind": document_kind,
            "confidence": 0.98,
            "why": "scripted fixture",
        },
        "printed": {"consumption": "139,600", "consumption_unit": "kWh"},
        "normalised": dict(NORMALISED if normalised is None else normalised),
        "activities": (
            [dict(ELECTRICITY_ACTIVITY)] if activities is None else [dict(a) for a in activities]
        ),
        "field_problems": [],
        "consistency_flags": [],
        "request": {"model": "test-model", "dpi": 150, "image_detail": "high"},
        "usage": {},
    }


@pytest.fixture(autouse=True)
def _clean_sessions():
    reset_sessions()
    yield
    reset_sessions()


@pytest.fixture
def session(tmp_path: Path) -> Session:
    """A session with one uploaded document. The file exists but is never read."""
    document_path = tmp_path / "doc_test.pdf"
    document_path.write_bytes(b"%PDF-1.4 synthetic")
    session = Session(session_id="sess_test")
    session.add_document(
        StoredDocument(
            document_id="doc_test",
            filename="elec_001.pdf",
            content_type="application/pdf",
            size_bytes=document_path.stat().st_size,
            path=document_path,
        )
    )
    return session


@pytest.fixture
def toolkit(session: Session) -> Toolkit:
    """A toolkit whose extraction and SQL are replaced; the arithmetic is the real one."""

    def fake_extract(path, **_: Any) -> ToolResult:
        return ok(extract_payload(), f"Read {Path(path).name}.")

    def fake_lookup(category: str, unit: str, year: int) -> ToolResult:
        factor = dict(FACTORS_BY_CATEGORY.get(category, FACTOR_2024), year_requested=year)
        return ok(
            {"factor": factor, "is_non_singapore": factor["jurisdiction"] != "SG"},
            f"{factor['factor']} {factor['unit']} ({factor['publisher']}).",
        )

    return Toolkit(session, extract=fake_extract, lookup=fake_lookup)


@pytest.fixture
def fuel_toolkit(session: Session) -> Toolkit:
    """A toolkit whose document is a two-product fuel-card statement.

    The case the pairs-based calculation exists for: two activities, two factors, one
    total that no model computed.
    """

    def fake_extract(path, **_: Any) -> ToolResult:
        return ok(
            extract_payload(
                filename="fuel_001.pdf",
                document_kind="fuel_card_statement",
                normalised={"period_start": "2026-02-01", "period_end": "2026-02-28"},
                activities=FUEL_ACTIVITIES,
            ),
            "Read fuel_001.pdf as a fuel card statement.",
        )

    def fake_lookup(category: str, unit: str, year: int) -> ToolResult:
        factor = dict(FACTORS_BY_CATEGORY[category], year_requested=year)
        return ok(
            {"factor": factor, "is_non_singapore": True},
            f"{factor['factor']} {factor['unit']} (DESNZ {year}).",
        )

    return Toolkit(session, extract=fake_extract, lookup=fake_lookup)


def pair(activity_ref: str, factor_ref: str) -> dict:
    """One `calculate_emissions` pair, as the model would send it."""
    return {"activity_ref": activity_ref, "factor_ref": factor_ref}


# --- a fake provider client -------------------------------------------------------------


@dataclass
class FakeFunctionCall:
    """One scripted tool call, shaped like the provider's `function_call` output item."""

    name: str
    arguments: str
    call_id: str = "call_1"
    type: str = "function_call"

    def model_dump(self, **_: Any) -> dict:
        return {
            "type": self.type,
            "name": self.name,
            "arguments": self.arguments,
            "call_id": self.call_id,
        }


@dataclass
class FakeMessage:
    """A final prose answer."""

    text: str
    type: str = "message"

    def model_dump(self, **_: Any) -> dict:
        return {"type": self.type, "content": [{"type": "output_text", "text": self.text}]}


@dataclass
class FakeResponse:
    output: list
    output_text: str = ""


@dataclass
class FakeResponsesAPI:
    """Replays a scripted list of turns and records what it was asked."""

    turns: list
    requests: list[dict] = field(default_factory=list)

    def create(self, **kwargs) -> FakeResponse:
        self.requests.append(kwargs)
        if not self.turns:
            raise AssertionError("The fake client ran out of scripted turns")
        turn = self.turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        if isinstance(turn, str):
            return FakeResponse(output=[FakeMessage(turn)], output_text=turn)
        return FakeResponse(output=list(turn))


@dataclass
class FakeClient:
    responses: FakeResponsesAPI


def fake_client(*turns) -> FakeClient:
    """`fake_client(call(...), "the answer")` - tool-call turns then a prose turn."""
    return FakeClient(responses=FakeResponsesAPI(turns=list(turns)))


def call(name: str, call_id: str = "call_1", **arguments) -> list:
    """One turn consisting of a single tool call."""
    return [FakeFunctionCall(name=name, arguments=json.dumps(arguments), call_id=call_id)]
