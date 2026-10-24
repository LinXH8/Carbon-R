"""Write the sample turns the UI preview pages render: src/carbon_r/static/preview/sample.json.

The previews need figures to show, and a figure on screen must be one the code computed.
So nothing here is typed in by hand: each sample runs the real `Toolkit`, whose
`calculate_emissions` does the arithmetic, and the real figure audit decides
`unverified_figures`. Only extraction and the SQL lookup are substituted, with the offline
fixtures the test suite uses (a synthetic bill; the factor rows are the published EMA and
DESNZ values recorded in tests/conftest.py).

Run with `uv run python scripts/build_ui_sample.py`. No database, key or network needed.
"""

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

import conftest as fixtures  # noqa: E402
from carbon_r.agent.audit import audit_figures  # noqa: E402
from carbon_r.agent.session import Session, StoredDocument  # noqa: E402
from carbon_r.agent.tools import Toolkit  # noqa: E402
from carbon_r.api.models import FigureOut  # noqa: E402
from carbon_r.tools.base import ok  # noqa: E402

OUT = ROOT / "src" / "carbon_r" / "static" / "preview" / "sample.json"
DOCUMENT_ID = "doc_test"


def _lookup(category: str, unit: str, year: int):
    factor = dict(fixtures.FACTORS_BY_CATEGORY[category], year_requested=year)
    return ok(
        {"factor": factor, "is_non_singapore": factor["jurisdiction"] != "SG"},
        f"{factor['factor']} {factor['unit']} ({factor['publisher']}).",
    )


def _turn(tmp: Path, filename: str, extract_payload: dict, steps: list, reply: str) -> dict:
    path = tmp / filename
    path.write_bytes(b"%PDF-1.4 synthetic")
    session = Session(session_id="sess_preview")
    session.add_document(
        StoredDocument(
            document_id=DOCUMENT_ID,
            filename=filename,
            content_type="application/pdf",
            size_bytes=path.stat().st_size,
            path=path,
        )
    )
    toolkit = Toolkit(
        session,
        extract=lambda _path, **_: ok(extract_payload, f"Read {filename}."),
        lookup=_lookup,
    )

    calls, payloads, figures = [], [], []
    for name, arguments in steps:
        result = toolkit.dispatch(name, json.dumps(arguments))
        assert result.status == "ok", result.message
        calls.append(
            {
                "tool": name,
                "arguments": json.dumps(arguments),
                "status": result.status,
                "message": result.message,
            }
        )
        payloads.append(result.data)
        if name == "calculate_emissions":
            figures.append(FigureOut.from_payload(result.data).model_dump(mode="json"))

    return {
        "filename": filename,
        "document_id": DOCUMENT_ID,
        "final": {
            "type": "final",
            "session_id": "sess_preview",
            "run_id": "run_preview",
            "reply": reply,
            "tool_calls": calls,
            "turns_used": len(calls) + 1,
            "stopped_reason": "completed",
            "unverified_figures": audit_figures(reply, payloads),
            "unverified_quotes": [],
            "figures": figures,
        },
    }


ELECTRICITY_STEPS = [
    ("extract_from_document", {"document_id": DOCUMENT_ID}),
    (
        "lookup_emission_factor",
        {"category": "electricity_grid_sg", "unit": "kg CO2 / kWh", "year": 2025},
    ),
    ("calculate_emissions", {"pairs": [fixtures.pair("act_1", "factor_1")]}),
]

FUEL_STEPS = [
    ("extract_from_document", {"document_id": DOCUMENT_ID}),
    (
        "lookup_emission_factor",
        {"category": "fuel_diesel", "unit": "kg CO2e / litres", "year": 2026},
    ),
    (
        "lookup_emission_factor",
        {"category": "fuel_petrol", "unit": "kg CO2e / litres", "year": 2026},
    ),
    (
        "calculate_emissions",
        {"pairs": [fixtures.pair("act_1", "factor_1"), fixtures.pair("act_2", "factor_2")]},
    ),
]


def main() -> None:
    tmp = Path(tempfile.mkdtemp())
    fuel_extract = fixtures.extract_payload(
        filename="fuel_001.pdf",
        document_kind="fuel_card_statement",
        normalised={"period_start": "2026-02-01", "period_end": "2026-02-28"},
        activities=fixtures.FUEL_ACTIVITIES,
    )
    samples = {
        "electricity": _turn(
            tmp,
            "elec_001.pdf",
            fixtures.extract_payload(),
            ELECTRICITY_STEPS,
            "I read the bill, looked up the Singapore grid emission factor and calculated "
            "the emissions. The factor is from an earlier year than the bill; the note "
            "under the figure says why.",
        ),
        "fuel": _turn(
            tmp,
            "fuel_001.pdf",
            fuel_extract,
            FUEL_STEPS,
            "The statement has diesel and petrol on it, so each product was calculated with "
            "its own factor. Both factors are UK DESNZ values, not Singapore ones.",
        ),
        # The failure state the figure audit exists for: a reply stating a number that no
        # tool returned. The audit, not this script, decides which token is flagged.
        "flagged": _turn(
            tmp,
            "elec_001.pdf",
            fixtures.extract_payload(),
            ELECTRICITY_STEPS,
            "The bill comes to roughly 56,000 kg CO2e.",
        ),
    }
    OUT.write_text(json.dumps(samples, indent=1) + "\n")
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
