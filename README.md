# Carbon R

Upload a utility bill or fuel invoice, get a greenhouse-gas figure that carries its own
receipts: the emission factor used, its unit, publisher, year, and the document it came from.

**Status:** Tier 1, Block 4 complete — usable end to end over HTTP: upload a bill, ask a
question, get a cited Scope 2 figure. No UI yet; that is Block 5.

## The one design rule worth stating up front

**The language model never does arithmetic.** It reads documents and chooses tools. Every
multiplication happens in `calculate_emissions`, in plain Python, against a factor fetched
by exact SQL. A model that is asked to multiply will sometimes produce a plausible wrong
number with no way to audit it; a model that is asked only to extract and to choose can be
measured, and its mistakes show up in an eval instead of in a disclosure.

See [CLAUDE.md](CLAUDE.md) for the full set of rules this repo holds itself to,
[SOURCES.md](SOURCES.md) for every dataset's provenance and verification status,
[SYNTHGEN_V0.md](SYNTHGEN_V0.md) for how the synthetic test documents are generated,
[TIER1_BLOCK3.md](TIER1_BLOCK3.md) for the extraction/calculation design and its measured
results, and [TIER1_BLOCK4.md](TIER1_BLOCK4.md) for the agent loop and the API.

## What works today, and what it measures

Three tools, each returning `{status, data, message}`:

| Tool | What it does |
|---|---|
| `extract_from_document` | Rasterises a page and has a vision model transcribe what is printed. It is never asked for a derived figure. |
| `lookup_emission_factor` | Exact SQL (`category`, `unit`, `year`), never similarity search. Returns the factor with publisher, source document, page and retrieval date. |
| `calculate_emissions` | Plain Python. Returns the figure together with every input used, the derivation written out, and structured disclosure flags. |

Measured by `make eval` on the 18 synthetic bills in the `clean` tier, 24 scored fields
per document:

- Every field recovered correctly on **18 of 18** documents, in each of three fresh runs.
- End-to-end kgCO₂e matching the answer key on **18 of 18**, in every run recorded.
- **9 of 18** documents exercise the factor-year substitution: their activity periods fall
  in 2025 and the newest published EMA factor is 2024, so the substitution is stated on the
  result rather than applied silently.

What that figure does **not** cover is set out under [Limitations](#limitations) — it is
one layout on crisp synthetic PDFs, and three runs is a small sample.

### The agent that drives them

An agent chooses which tool to call; it does not compute. Two properties make that
structural rather than a matter of prompt wording:

- **It passes handles, not values.** The calculation tool takes a `document_id` and a
  `factor_ref` into the session's stored tool results — never an amount, never a factor.
  A model that cannot write a digit into a calculation cannot mistype `0.402` as `0.42`.
- **Its answer is audited.** Every number in a reply is matched against the numbers the
  tools returned; anything untraceable comes back on the response as `unverified_figures`.

The loop is bounded at 8 turns. On `no_results`, on an error, or when the turns run out,
it reports what is missing and asks — it never assembles a figure to look finished.

## Running it

Requires [uv](https://docs.astral.sh/uv/) and Python 3.13 (uv will fetch it).

```bash
# This project uses a named virtualenv, .venv-carbon-r, rather than a bare .venv.
export UV_PROJECT_ENVIRONMENT=.venv-carbon-r   # or just use `make`, which sets it
uv sync

cp .env.example .env     # then fill in OPENAI_API_KEY and DATABASE_URL
uv run uvicorn carbon_r.main:app --reload
curl localhost:8000/health
```

`/health` reports whether the database and the model provider are configured, so a partly
configured checkout tells you what is missing instead of crashing.

### Asking a question about a bill

```bash
curl -F file=@data/generated/electricity_bill/clean/elec_010.pdf localhost:8000/upload
# {"session_id":"sess_…","document_id":"doc_…", …}

curl -s localhost:8000/chat -H 'content-type: application/json' -d '{
  "message": "What are the Scope 2 emissions for this bill?",
  "session_id": "sess_…"
}'
```

The reply comes back with the tool transcript that produced it, the turn count, and
`unverified_figures` — which must be empty. Sessions are in-memory, so they do not
survive a restart; persistence is a Tier 3 concern.

### Database

Any Postgres 15+ will do. Either point `DATABASE_URL` at a hosted instance, or:

```bash
docker compose up -d db
```

Then load the emission factors (safe to re-run — it upserts):

```bash
uv run python scripts/seed_factors.py        # or: make seed
```

### Generating documents and scoring them

```bash
make docs         # 18 synthetic bills + answer keys into data/generated/ (git-ignored)
make eval         # score extraction and the pipeline against those answer keys
make agent-smoke  # initial end-to-end check of /upload + /chat on 5 bills
make check        # ruff + the full test suite (207 tests)
```

`make agent-smoke` is a smoke check, not a measurement: five documents of one layout
cannot measure accuracy, and its output says so on every line. It checks that the loop
finishes, that its figure equals the answer key's, that the factor is cited, that the
disclosures are stated, and that no number appears which no tool produced. It is 5 of 5
on all five checks, run both over the first five bills and over five whose activity year
forces a factor-year substitution.

`make eval` caches each model reply, keyed on the document, model, DPI and a hash of the
prompt *and* the response schema. A cache hit skips only the model call — every step after
it re-runs against current code, so a fix can be re-measured against the same replies at no
API cost. Changing the prompt or the schema misses the cache by construction.

## Data provenance

No third-party source document is committed to this repo. Emission factors are transcribed
from primary sources with their citation recorded alongside, and every dataset used is
listed in [SOURCES.md](SOURCES.md) with its publisher, URL and retrieval date.

## Limitations

Stated plainly, because they bound what any number here means:

- All bills and invoices processed so far are **synthetic**. None are real customer documents.
- Emission estimates produced here are **not assured** by any third party.
- Figures are **location-based** (average grid intensity), not market-based.
- The accuracy figures above describe **one layout, one vendor, crisp PDFs, no quirks** —
  the `clean` tier. They say nothing yet about scans, phone photographs, MWh units or
  billing periods that straddle a month. Tier 2 adds those tiers and reports per-quirk.
- **Three runs is a small sample.** Both defects found during Block 3 appeared in exactly
  one run of three, so a defect at that frequency is roughly as likely to be missed as
  found. A clean three-run range is weak evidence of determinism, not proof of it.
- **The agent has been exercised on one question over twelve live replies.** The
  honesty paths — a missing factor, a failed read, an exhausted turn budget — are covered
  by offline tests with a scripted provider, not yet by live misbehaviour.
- **No holdout layout exists yet.** With a single layout there is no honest unseen-layout
  number to quote; Tier 2 holds one layout out and reports it separately.
- Where no factor is published for a bill's activity year, the latest factor at or before
  it is used, up to two years back, and the substitution is stated on the result. Beyond
  two years the tool returns `no_results` rather than a stale figure.

## Layout

```
src/carbon_r/
  config.py    settings from the environment
  db.py        connection helper
  main.py      FastAPI app: /health, /upload, /chat
  money.py     one definition of "round to cents", shared by generator and extraction
  models/      Pydantic domain models: answer keys, extraction targets, factor citations
  synthgen/    the synthetic-document generator (answer first, then the document)
  tools/       the tools the agent is allowed to call
  agent/       the tool-calling loop, the session workspace, the figure audit
  api/         request and response models, upload storage
db/migrations/ schema, applied in filename order
scripts/       operational scripts: factor loading, document generation, the eval, the smoke check
tests/         207 tests, every one of them offline: no .env, no database, no API key
data/uploads/  documents uploaded at runtime — git-ignored, never committed
data/raw/      source documents — git-ignored, never committed
data/generated/ synthetic documents, answer keys, eval runs — git-ignored
```
