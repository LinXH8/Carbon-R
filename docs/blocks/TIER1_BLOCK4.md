# Tier 1 Block 4 — agent loop and API

Dated 18 Oct 2026. Block 3 left three working tools and a measured number, but nothing
drove those tools except an eval script that chained them in a fixed order. There was no
way for a person to upload a bill and ask a question.

```
Block 3   document ──► tools ──► figure        (a script chained them)
Block 4   person   ──► /upload ──► /chat ──► agent chooses the tools ──► cited answer
```

Outcome: a bounded tool-calling loop, a FastAPI surface (`/upload`, `/chat`), and — the
part that matters — two structural guarantees that the model cannot produce a number.

---

## The measurement

Initial end-to-end smoke check, `make agent-smoke`. **Five documents is not an accuracy
measurement**; `scripts/eval_extraction.py` remains the measured number (18 of 18).

| Check | First five (`elec_001`–`005`) | Five substituting bills (`elec_010, 013, 016, 017, 018`) |
|---|---|---|
| answered | **5 of 5** | **5 of 5** |
| figure matches the answer key | **5 of 5** | **5 of 5** |
| cited (factor, publisher, year used) | **5 of 5** | **5 of 5** |
| disclosed (not assured; both years when substituted) | **5 of 5** | **5 of 5** |
| no stray numbers (figure audit empty) | **5 of 5** | **5 of 5** |
| factor-year substituted | 0 of 5 | **5 of 5** |
| turns used | 20 across 5 | 20 across 5 |

Four turns per document every time: extract, look up, calculate, answer. The second run
exists because the first five bills all fall in 2024 and so never exercise the
substitution disclosure — a set that passes every check can still be the wrong set.

Tests: **207 passing** (`make check`), 68 of them new in this block, all offline — no
`.env`, no database, no API key, no network.

### What this does not say

- Ten runs of one question on one layout. It says the wiring works; it says nothing about
  how the agent behaves on a document type it has not seen, or on a question that needs
  two documents.
- The honesty paths (`no_results`, max turns, provider failure) are covered by offline
  tests with a scripted fake, not by live misbehaviour.
- One live probe, below, is the only evidence about a question the tools cannot answer.

---

## Architecture

```
POST /upload   bytes ──► data/uploads/doc_<uuid>.pdf        generated name, allowlisted type
                           │
                           ▼  document_id
POST /chat   ──► run_agent ──┬─► responses.create(tools=…)  ◄─┐  max 8 turns
                             │        │ function_call          │
                             │        ▼                        │
                             │   Toolkit.dispatch ──► {status, data, message}
                             │        │                        │
                             │        └── stored in the session workspace ──┘
                             │             readings[doc_id], factor_N, calc_N
                             ▼
                        audit_figures(reply, tool_payloads) ──► unverified_figures
```

### Two structural guarantees, not two prompt instructions

**1. The model never carries a number between tools.** `calculate_emissions` as the agent
sees it takes `document_id` and `factor_ref` — handles into the session workspace — and
nothing else. The consumption comes from the stored `NormalisedBill`; the factor comes
from the stored `FactorCitation`. The model chooses *which* inputs; Python moves the
values.

```python
calculate_emissions(document_id="doc_a81f", factor_ref="factor_1")
# not: calculate_emissions(activity_amount=139600, factor={"factor": 0.402, ...})
```

A model that cannot write a digit into a calculation cannot mistype `0.402` as `0.42`.
No prompt wording achieves that, and `test_calculate_takes_handles_not_values` is the
test that should fail if a convenience argument is ever added.

`lookup_emission_factor` keeps literal `category`/`unit`/`year` arguments, which is not an
exception: those are selectors against the table's UNIQUE key, not quantities, and rule 3
wants them exact. A wrong year is visible in the citation's `year_requested`.

**2. The answer is audited.** After the loop finishes, every numeric token in the reply is
matched against the numbers the tools returned in that session. Anything untraceable comes
back on the response as `unverified_figures`. Three equivalences are allowed, deliberately:
thousands separators (`56,119.2` = `56119.2`), a stated rounding to the precision
`calculate_emissions` returns, and a fraction written as a percentage (`9%` = `0.09`). A
number inside a word (`CO2e`) is not scanned, and the literal phrases `Scope 1/2/3` are
removed first.

This is what turns "the LLM never does arithmetic" from a claim into a measurement. The
smoke check asserts the list is empty; if it is ever not, the API response says so.

### The guards

| Guard | Behaviour |
|---|---|
| `MAX_TURNS = 8` | Stops, sets `stopped_reason="max_turns"`, and replies with the tool messages established so far and no figure. It never assembles an answer out of the transcript to look finished. |
| Unknown tool, bad JSON, invalid arguments | An `error` `ToolResult` fed back to the model, which has the remaining turns to correct itself. No exception leaves the loop. |
| `no_results` | Passed through verbatim. The loop does not decide what a gap means; the agent reports it and asks (rule 7). |
| Provider failure | `stopped_reason="provider_error"` and a reply that says so. |
| Document addressing | Tools take a `document_id` that must already be in the session. There is no tool argument that can name a path, so a prompt injected into a document cannot ask for `/etc/passwd`. |
| Upload | Generated filename (never the client's), extension allowlist, 10 MB cap. |

---

## Files added

| Path | Purpose |
|---|---|
| `src/carbon_r/agent/prompt.py` | System prompt; `render_disclosures` — the one place a flag becomes a sentence |
| `src/carbon_r/agent/toolspec.py` | One Pydantic model per tool, used for both the strict schema and validation |
| `src/carbon_r/agent/session.py` | Documents, the workspace of handles, the transcript. In-memory: Tier 1 only |
| `src/carbon_r/agent/tools.py` | The three tools as the agent sees them; handles in, `{status, data, message}` out |
| `src/carbon_r/agent/loop.py` | The bounded loop; `AgentRun` with the tool transcript and the audit |
| `src/carbon_r/agent/audit.py` | Which numbers a reply is allowed to contain |
| `src/carbon_r/api/models.py` | `UploadResponse`, `ChatRequest`, `ChatResponse`, `ToolCallOut` |
| `src/carbon_r/api/uploads.py` | Where an upload goes and what is refused at the door |
| `scripts/agent_smoke_5bills.py` | The initial end-to-end check (`make agent-smoke`). Renamed to `scripts/agent_flow_check.py` in Block 5, when the same script was run over ten documents and a sample size in a filename stopped being true |

`src/carbon_r/main.py` gains `/upload` and `/chat`. New dependency: `python-multipart`.

Tests, all offline:

| Path | Tests |
|---|---|
| `tests/conftest.py` | Shared fixtures: a session, a fake toolkit, a scripted fake provider client |
| `tests/test_agent_toolspec.py` | 8 — strict-schema shape; the handles-not-values rule |
| `tests/test_agent_workspace.py` | 20 — dispatch failures, caching, handles, the honesty paths |
| `tests/test_agent_loop.py` | 10 — the normal sequence, the turn budget, provider failure, the audit wired in |
| `tests/test_figure_audit.py` | 15 — what must pass and what must be caught |
| `tests/test_api_upload_chat.py` | 15 — upload refusals, the 404s, the 503, the document context |

---

## Decisions

### The end-to-end check is a script, and its name says what it is

`scripts/agent_smoke_5bills.py`, `make agent-smoke`, and every line of output prefixed
`SMOKE (n documents, NOT an accuracy measurement)`. Five documents from one layout cannot
measure anything, and a number that is easy to quote will eventually be quoted. The
alternative — a pytest test skipped without credentials — would have put two classes of
test in one suite and made a silent skip able to hide a regression. Every pytest test
stays offline, which is a property the suite has had since Block 1.

The script drives the API in-process through FastAPI's `TestClient`: the same routing,
validation and response models a `curl` hits, with one fewer moving part, and it can read
the session workspace afterwards to compare the agent's figure against the key rather than
parsing prose.

### Disclosures are sentences for the reader, not instructions to the agent

Found by the first live run, not by inspection. The disclosure strings were originally
phrased as directives — `"...used instead. State both years."` — and the model printed the
directive verbatim under a "Disclosures:" heading, telling the reader to state a year. The
sentences are now true and printable as they stand; anything the agent must *do* with a
disclosure lives in the system prompt. `test_disclosure_sentences_are_printable_not_directives`
keeps it that way.

### Sessions are in-memory, and that is a scope cut with a seam

A restart loses every session and two workers would not share one. Sessions that survive a
restart need a table and a retention policy, which is a Tier 3 deployment concern.
`get_session` / `create_session` is where that gets swapped.

### The document list is appended to the user's message, not to the system prompt

The set of uploaded documents changes with every upload; the system prompt is constant. A
constant prompt is what keeps a provider-side cache useful, and it keeps the instructions
the agent follows identical across every request.

---

## Verification evidence

```
$ make check
ruff check .            → All checks passed!
ruff format --check .   → 54 files already formatted
pytest -q               → 207 passed

$ make agent-smoke
SMOKE (5 documents, NOT an accuracy measurement)
  answered            5 of 5
  figure_matches      5 of 5
  cited               5 of 5
  disclosed           5 of 5
  no_stray_numbers    5 of 5
  factor-year substituted 0 of 5
  turns used              20 across 5 documents

$ uv run python scripts/agent_smoke_5bills.py --docs elec_010,elec_013,elec_016,elec_017,elec_018
  ... same five checks, 5 of 5 each
  factor-year substituted 5 of 5
```

Hand-checked against `elec_010.json` (`consumption_kwh` 4423, period starting 2025-02-01;
4423 × 0.402 = 1778.046):

```
Scope 2 emissions: 1,778.046 kg CO2e (1.778046 tCO2e)
Derivation: 4,423 kWh × 0.402 kg CO2 / kWh = 1,778.046 kg CO2e
Factor: 0.402 kg CO2 / kWh · EMA · year used 2024 · year requested 2025
Source: Singapore Energy Statistics 2025, Table 2.4
Method: location-based   Basis: activity-based
Disclosures: factor not from the activity year …; estimates, not assured …
```

**Rule-1 probe**, asked as a second turn in the same session:

```
Q: Annualise that over 12 months and tell me the yearly tonnes.
stopped: completed | turns: 1 | unverified: []
"I can't do that arithmetic outside the calculation tool, and I also can't annualise by
 hand. … I can't state a yearly tonnes figure without a tool-produced result."
```

It refused, in one turn, with no tool call — and the audit independently confirmed the
reply contains no number the tools did not produce.

---

## Open items carried forward

- No UI yet (Block 5), and no run over 10 bills with failures recorded (Block 5).
- The honesty paths are proven offline but have never been seen live: no live run has yet
  produced a `no_results` from the factor table, because every synthetic bill falls inside
  the 2-year substitution cap.
- The figure audit has been exercised on twelve real replies. Its tolerances (rounding,
  percentage) are reasoned, not yet stress-tested against a chatty answer.
- Sessions and uploads accumulate on disk and in memory with no eviction.
