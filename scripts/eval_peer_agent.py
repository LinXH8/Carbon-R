"""Eval 5b, agent cases: peer questions put to the whole agent, each run several times.

    uv run python scripts/eval_peer_agent.py                    the v1 cases (the 2b baseline)
    uv run python scripts/eval_peer_agent.py --cases data/evals/peer_agent_questions_v2.json

The cases and their checks are in data/evals/peer_agent_questions.json, frozen before the
first run (its sha256 is recorded). They are TEST ONLY - there is no dev split - so, by the
author's rule: run them once the prompt is final; run each case `runs` times; report every
check as n of runs; record the replies as given; do not change the prompt in response. A
failure is logged as a known limit. The checks are scored by `peer_agent_case.score_reply`.

Each run is a fresh session through FastAPI's TestClient, the same routing a curl would hit
(as `agent_flow_check.py` does).

What is written where:

- data/evals/runs/peer_agent_<stamp>.json (committed): case ids, tool names and statuses,
  each check's result, counts. NO reply text: a reply states verified peer figures, and
  those may be committed in one file only (CLAUDE.md rule 4).
- data/generated/peer_agent_replies/<stamp>.json (git-ignored): every reply in full, with its
  tool calls and audit lists, for reading against each case's pass rule.

Local only: needs a database with the peer corpus and verified figures, and an API key.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from carbon_r.config import PROJECT_ROOT
from carbon_r.main import app
from carbon_r.models.peer_agent_case import AGENT_CASES_PATH, load_agent_cases, score_reply
from carbon_r.rag.scoring import file_sha256, git_state, n_of

RUNS_DIR = PROJECT_ROOT / "data" / "evals" / "runs"
REPLIES_DIR = PROJECT_ROOT / "data" / "generated" / "peer_agent_replies"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--case", action="append", help="run only these case ids")
    parser.add_argument("--cases", type=Path, default=AGENT_CASES_PATH, help="the cases file")
    args = parser.parse_args(argv)

    cases_path = args.cases.resolve()
    cases = load_agent_cases(cases_path)
    if cases.review_status != "reviewed":
        print("The agent cases are still a draft. They are reviewed before the first run.")
        return 1
    selected = [c for c in cases.cases if not args.case or c.id in args.case]

    git = git_state()
    key_sha = file_sha256(cases_path)
    dirty = " (DIRTY TREE - not a clean measurement)" if git["dirty"] else ""
    print(
        f"peer agent cases: {len(selected)} case(s) x {cases.runs} runs; commit "
        f"{git['commit'][:7]}{dirty}; cases v{cases.version}, sha256 {key_sha[:16]}\n"
    )

    client = TestClient(app)
    record_cases, replies = [], []
    for case in selected:
        runs = []
        for n in range(1, cases.runs + 1):
            response = client.post("/chat", json={"message": case.question}).json()
            checks = score_reply(case, response)
            runs.append(
                {
                    "run": n,
                    "run_id": response["run_id"],
                    "stopped_reason": response["stopped_reason"],
                    "tool_calls": [f"{c['tool']}:{c['status']}" for c in response["tool_calls"]],
                    "unverified_figures": len(response["unverified_figures"]),
                    "unverified_quotes": len(response.get("unverified_quotes", [])),
                    "checks": checks,
                }
            )
            replies.append({"case": case.id, "run": n, "question": case.question} | response)
        counts = {label: sum(r["checks"][label] for r in runs) for label in runs[0]["checks"]}
        all_pass = sum(all(r["checks"].values()) for r in runs)
        record_cases.append(
            {
                "id": case.id,
                "pass_rule": case.pass_rule,
                "counts": counts,
                "all_checks_passed": all_pass,
                "runs": runs,
            }  # fmt: skip
        )
        print(f"  {case.id}: all checks passed in {n_of(all_pass, cases.runs)}")
        for label, k in counts.items():
            print(f"    {label:58} {n_of(k, cases.runs)}")

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    REPLIES_DIR.mkdir(parents=True, exist_ok=True)
    out = RUNS_DIR / f"peer_agent_{stamp}.json"
    out.write_text(
        json.dumps(
            {
                "git": git,
                "cases_file": {
                    "path": str(cases_path.relative_to(PROJECT_ROOT)),
                    "version": cases.version,
                    "sha256": key_sha,
                },
                "runs_per_case": cases.runs,
                "cases": record_cases,
            },
            indent=2,
        )
        + "\n"
    )
    local = REPLIES_DIR / f"{stamp}.json"
    local.write_text(json.dumps(replies, indent=2, ensure_ascii=False) + "\n")
    print(f"\n  run written to {out.relative_to(PROJECT_ROOT)}")
    print(f"  replies (local only) in {local.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
