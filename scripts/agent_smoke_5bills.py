"""INITIAL END-TO-END SMOKE CHECK over 5 bills. Not an accuracy measurement.

Read that line again before quoting anything this prints. Five documents from the single
`clean` layout cannot measure accuracy; `scripts/eval_extraction.py` is the measured
number (18 of 18 on 24 fields), and this script exists to answer a different, cruder
question: does the whole thing work when a person uploads a bill and asks?

    upload -> chat -> tool loop -> cited answer

Five checks per document, each a count, never a percentage:

    answered            the loop finished rather than running out of turns
    figure matches      the agent's kgCO2e equals the answer key's, to the last place
    cited               the reply names the factor, its publisher and the year used
    disclosed           "not assured" is stated, and a factor-year substitution is
                        stated in words whenever one was made
    no stray numbers    the figure audit found nothing the tools did not produce

Run it with a live OPENAI_API_KEY and DATABASE_URL:

    make agent-smoke
    uv run python scripts/agent_smoke_5bills.py --docs elec_001,elec_002

The API is exercised in-process through FastAPI's TestClient rather than over a socket.
It is the same routing, validation and response model a curl would hit, with one fewer
moving part to get wrong - and it lets the script read the session workspace afterwards
to compare the agent's figure against the key without re-deriving it from prose.
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient

from carbon_r.agent.session import get_session
from carbon_r.config import PROJECT_ROOT, get_settings
from carbon_r.main import app
from carbon_r.models.electricity_bill import ElectricityBill
from carbon_r.tools.calculate import calculate_emissions
from carbon_r.tools.factors import lookup_emission_factor

GENERATED = PROJECT_ROOT / "data" / "generated" / "electricity_bill" / "clean"
RUNS_DIR = PROJECT_ROOT / "data" / "generated" / "agent_smoke_SYNTHGEN_V0"

FACTOR_CATEGORY = "electricity_grid_sg"
FACTOR_UNIT = "kg CO2 / kWh"

QUESTION = (
    "What are the Scope 2 emissions for this electricity bill? "
    "Show the factor you used and where it comes from."
)

CHECKS = ("answered", "figure_matches", "cited", "disclosed", "no_stray_numbers")


def expected_kg(key: ElectricityBill) -> str | None:
    """The answer key's own figure, by the same route the agent must take."""
    if key.consumption_kwh is None or key.factor_year is None:
        return None
    factor = lookup_emission_factor(FACTOR_CATEGORY, FACTOR_UNIT, key.factor_year)
    if factor.status != "ok":
        return None
    result = calculate_emissions(
        activity_amount=Decimal(key.consumption_kwh),
        activity_unit="kWh",
        factor=factor.data["factor"],
    )
    return result.data["kg_co2e"] if result.status == "ok" else None


def check_document(client: TestClient, doc_id: str) -> dict:
    """Upload one bill, ask the question, and score the five checks."""
    pdf = GENERATED / f"{doc_id}.pdf"
    key = ElectricityBill.model_validate_json((GENERATED / f"{doc_id}.json").read_text())

    with pdf.open("rb") as handle:
        uploaded = client.post(
            "/upload", files={"file": (pdf.name, handle, "application/pdf")}
        ).json()

    answer = client.post(
        "/chat",
        json={"message": QUESTION, "session_id": uploaded["session_id"]},
    ).json()

    session = get_session(uploaded["session_id"])
    calculations = list(session.calculations.values()) if session else []
    calculated = calculations[-1] if calculations else None
    wanted = expected_kg(key)
    reply = answer["reply"]
    lowered = reply.lower()

    factor = (calculated or {}).get("inputs", {}).get("factor", {})
    substituted = bool(factor.get("substituted"))

    cited = bool(factor) and all(
        token.lower() in lowered
        for token in (str(factor["factor"]), "ema", str(factor["year_used"]))
    )
    disclosed = "not assured" in lowered and (
        not substituted or str(factor["year_requested"]) in reply
    )

    return {
        "doc_id": doc_id,
        "stopped_reason": answer["stopped_reason"],
        "turns_used": answer["turns_used"],
        "tool_calls": [f"{c['tool']}:{c['status']}" for c in answer["tool_calls"]],
        "expected_kg_co2e": wanted,
        "actual_kg_co2e": (calculated or {}).get("kg_co2e"),
        "factor_substituted": substituted,
        "unverified_figures": answer["unverified_figures"],
        "reply": reply,
        "checks": {
            "answered": answer["stopped_reason"] == "completed" and bool(reply.strip()),
            "figure_matches": (
                wanted is not None
                and calculated is not None
                and Decimal(calculated["kg_co2e"]) == Decimal(wanted)
            ),
            "cited": cited,
            "disclosed": disclosed,
            "no_stray_numbers": answer["unverified_figures"] == [],
        },
    }


def report(run: dict) -> int:
    rows = run["documents"]
    total = len(rows)

    print(f"\nSMOKE ({total} documents, NOT an accuracy measurement)")
    print(f"  model {run['model']}  question {QUESTION!r}\n")

    for check in CHECKS:
        passed = sum(row["checks"][check] for row in rows)
        marker = "" if passed == total else "   <-"
        print(f"  {check:18} {passed:2} of {total}{marker}")

    substituted = sum(row["factor_substituted"] for row in rows)
    turns = sum(row["turns_used"] for row in rows)
    print(f"\n  factor-year substituted {substituted} of {total}")
    print(f"  turns used              {turns} across {total} documents")

    for row in rows:
        failed = [name for name, passed in row["checks"].items() if not passed]
        if failed:
            print(f"\n  {row['doc_id']} FAILED {', '.join(failed)}")
            print(f"    tools: {' -> '.join(row['tool_calls']) or 'none'}")
            print(f"    expected {row['expected_kg_co2e']} kg, got {row['actual_kg_co2e']} kg")
            if row["unverified_figures"]:
                print(f"    numbers no tool produced: {row['unverified_figures']}")
            print(f"    reply: {row['reply'][:400]}")

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = run["run_at"].replace(":", "").replace("-", "")
    path = RUNS_DIR / f"{stamp}-smoke.json"
    path.write_text(json.dumps(run, indent=2) + "\n", encoding="utf-8")
    print(f"\nRun written to {path.relative_to(PROJECT_ROOT)}")

    return 0 if all(all(row["checks"].values()) for row in rows) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docs", help="comma-separated doc ids; default the first 5")
    parser.add_argument("--count", type=int, default=5, help="how many documents (default 5)")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.llm_configured or not settings.openai_model:
        print("OPENAI_API_KEY and OPENAI_MODEL must be set; see .env.example", file=sys.stderr)
        return 2
    if not settings.database_configured:
        print("DATABASE_URL must be set: the factor lookup is real SQL.", file=sys.stderr)
        return 2
    if not GENERATED.exists():
        print(f"No generated documents at {GENERATED}. Run `make docs` first.", file=sys.stderr)
        return 2

    if args.docs:
        doc_ids = [d.strip() for d in args.docs.split(",") if d.strip()]
    else:
        doc_ids = sorted(p.stem for p in GENERATED.glob("elec_*.json"))[: args.count]
    if not doc_ids:
        print("No documents selected.", file=sys.stderr)
        return 2

    client = TestClient(app)
    rows = []
    for doc_id in doc_ids:
        print(f"  {doc_id} ...", flush=True)
        rows.append(check_document(client, doc_id))

    return report(
        {
            "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "kind": "smoke",
            "note": "Initial end-to-end check on 5 documents. Not an accuracy measurement.",
            "model": settings.openai_model,
            "question": QUESTION,
            "document_count": len(rows),
            "documents": rows,
        }
    )


if __name__ == "__main__":
    raise SystemExit(main())
