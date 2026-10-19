"""END-TO-END FLOW CHECK. Not an accuracy measurement, at any document count.

Read that line again before quoting anything this prints. Documents from one `clean`
layout per kind, asked one question, cannot measure accuracy however many of them there
are; `scripts/eval_extraction.py` is the measured number, and this script exists to answer
a different, cruder question: does the whole thing work when a person uploads a document
and asks?

The count is deliberately not in the filename. It was `agent_smoke_5bills.py` in Block 4,
and the rename happened the moment the same script was run over ten - a filename that
states a sample size becomes a lie the first time the sample changes, and the honesty of
the banner is the whole point of this script.

    upload -> chat -> tool loop -> cited answer

Five checks per document, each a count, never a percentage:

    answered            the loop finished rather than running out of turns
    figure matches      the agent's kgCO2e equals the answer key's, to the last place
    cited               the reply names the factor, its publisher and the year used
    disclosed           "not assured" is stated, and a factor-year substitution is
                        stated in words whenever one was made
    no stray numbers    the figure audit found nothing the tools did not produce

Run it with a live OPENAI_API_KEY and DATABASE_URL:

    make agent-smoke          # the first five, as Block 4 ran it
    make flow-10              # ten documents spanning both factor years
    make flow-scope1          # one document of each kind, including a refusal
    uv run python scripts/agent_flow_check.py --docs elec_001,lpg_004,fuel_001

Since Tier 2 Block 1 it handles all three document kinds. Two checks changed with them:

- `figure_matches` compares the TOTAL, which for a two-product fuel-card statement is a
  sum of two lines - the number the model is never allowed to compute;
- a document for which no factor is published (`lpg_001`, whose supply period predates the
  loaded DESNZ series) is expected to produce NO figure. `refused_honestly` scores that
  case, and `figure_matches` is not applied to it. A script that counted an honest refusal
  as a failure would be rewarding a guess.

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
from eval_extraction import KINDS, documents_dir, emissions_for, kind_of

GENERATED = PROJECT_ROOT / "data" / "generated"
RUNS_DIR = GENERATED / "flow_checks_TIER2_BLOCK1"

QUESTION = (
    "What are the greenhouse-gas emissions from this document? "
    "Show the factor you used and where it comes from."
)

CHECKS = (
    "answered",
    "figure_matches",
    "cited",
    "disclosed",
    "no_stray_numbers",
    "refused_honestly",
)


def expected_figure(key) -> dict:
    """The answer key's own figure, by the same route the agent must take.

    Returns `{kg_co2e, refused}`. `refused` is True when no factor is published for the
    activity year, which is a correct outcome rather than a missing one.
    """
    spec = KINDS[key.doc_kind]
    outcome = emissions_for(spec.key_activities(key), key.factor_year)
    return {"kg_co2e": outcome["kg_co2e"], "refused": outcome["refused"]}


def check_document(client: TestClient, doc_id: str) -> dict:
    """Upload one document, ask the question, and score the checks."""
    kind = kind_of(doc_id)
    directory = documents_dir(kind)
    pdf = directory / f"{doc_id}.pdf"
    key = KINDS[kind].key_model.model_validate_json((directory / f"{doc_id}.json").read_text())

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
    expected = expected_figure(key)
    wanted = expected["kg_co2e"]
    reply = answer["reply"]
    lowered = reply.lower()

    lines = (calculated or {}).get("lines", [])
    factors = [line["factor"] for line in lines]
    substituted = any(f["substituted"] for f in factors)
    non_singapore = any(f["jurisdiction"] != "SG" for f in factors)
    converted = any(line["conversion"] for line in lines)

    # Cited: every line's factor value, publisher and year used appear in the reply. With
    # two lines that means two factors and two publishers, which is the point - a reply
    # that cited one factor for a two-product statement is not cited.
    cited = bool(factors) and all(
        token.lower() in lowered
        for factor in factors
        for token in (str(factor["factor"]), factor["publisher"][:4], str(factor["year_used"]))
    )
    disclosed = bool(factors) and (
        "not assured" in lowered
        and (not substituted or all(str(f["year_requested"]) in reply for f in factors))
        # A UK factor must be labelled as not published for Singapore wherever it appears.
        and (not non_singapore or "singapore" in lowered)
    )

    return {
        "doc_id": doc_id,
        "document_kind": kind,
        "stopped_reason": answer["stopped_reason"],
        "turns_used": answer["turns_used"],
        "tool_calls": [f"{c['tool']}:{c['status']}" for c in answer["tool_calls"]],
        "expected_kg_co2e": wanted,
        "expected_refusal": expected["refused"],
        "actual_kg_co2e": (calculated or {}).get("kg_co2e"),
        "line_count": len(lines),
        "factor_substituted": substituted,
        "non_singapore_factor": non_singapore,
        "unit_converted": converted,
        "unverified_figures": answer["unverified_figures"],
        "reply": reply,
        "checks": {
            "answered": answer["stopped_reason"] == "completed" and bool(reply.strip()),
            # Not applied to a document for which no factor is published: there is no
            # figure to match, and `refused_honestly` scores that case instead.
            "figure_matches": (
                True
                if expected["refused"]
                else (
                    wanted is not None
                    and calculated is not None
                    and Decimal(calculated["kg_co2e"]) == Decimal(wanted)
                )
            ),
            "cited": True if expected["refused"] else cited,
            "disclosed": True if expected["refused"] else disclosed,
            "no_stray_numbers": answer["unverified_figures"] == [],
            # The honesty check. When no factor is published the agent must produce no
            # figure and say so; stating one would mean it had guessed.
            "refused_honestly": (
                (calculated is None and "no" in lowered) if expected["refused"] else True
            ),
        },
    }


def report(run: dict) -> int:
    rows = run["documents"]
    total = len(rows)

    print(f"\nFLOW CHECK ({total} documents, NOT an accuracy measurement)")
    print(f"  model {run['model']}  question {QUESTION!r}\n")

    for check in CHECKS:
        passed = sum(row["checks"][check] for row in rows)
        marker = "" if passed == total else "   <-"
        print(f"  {check:18} {passed:2} of {total}{marker}")

    substituted = sum(row["factor_substituted"] for row in rows)
    turns = sum(row["turns_used"] for row in rows)
    by_kind: dict[str, int] = {}
    for row in rows:
        by_kind[row["document_kind"]] = by_kind.get(row["document_kind"], 0) + 1
    print(f"\n  documents by kind       {by_kind}")
    print(f"  factor-year substituted {substituted} of {total}")
    print(f"  non-Singapore factor    {sum(r['non_singapore_factor'] for r in rows)} of {total}")
    print(f"  unit converted          {sum(r['unit_converted'] for r in rows)} of {total}")
    print(f"  multi-line figures      {sum(r['line_count'] > 1 for r in rows)} of {total}")
    print(f"  expected refusals       {sum(r['expected_refusal'] for r in rows)} of {total}")
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
    path = RUNS_DIR / f"{stamp}-flow-{len(rows)}.json"
    path.write_text(json.dumps(run, indent=2) + "\n", encoding="utf-8")
    print(f"\nRun written to {path.relative_to(PROJECT_ROOT)}")

    return 0 if all(all(row["checks"].values()) for row in rows) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docs", help="comma-separated doc ids; default the first --count")
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
        directory = documents_dir("electricity_bill")
        doc_ids = sorted(p.stem for p in directory.glob("elec_*.json"))[: args.count]
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
            "kind": "flow_check",
            "note": (
                f"End-to-end flow check on {len(rows)} documents, one layout per kind, "
                "one question. Not an accuracy measurement."
            ),
            "model": settings.openai_model,
            "question": QUESTION,
            "document_count": len(rows),
            "documents": rows,
        }
    )


if __name__ == "__main__":
    raise SystemExit(main())
