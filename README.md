# Carbon R

Upload a utility bill or fuel invoice, get a greenhouse-gas figure that carries its own
receipts: the emission factor used, its unit, publisher, year, and the document it came from.

**Status:** Tier 1, Block 3 complete — a document can be read, a factor looked up and an
emission figure computed, all measured against answer keys. Not yet usable end to end:
there is no agent loop and no UI until Blocks 4 and 5.

## The one design rule worth stating up front

**The language model never does arithmetic.** It reads documents and chooses tools. Every
multiplication happens in `calculate_emissions`, in plain Python, against a factor fetched
by exact SQL. A model that is asked to multiply will sometimes produce a plausible wrong
number with no way to audit it; a model that is asked only to extract and to choose can be
measured, and its mistakes show up in an eval instead of in a disclosure.

See [CLAUDE.md](CLAUDE.md) for the full set of rules this repo holds itself to,
[SOURCES.md](SOURCES.md) for every dataset's provenance and verification status,
[SYNTHGEN_V0.md](SYNTHGEN_V0.md) for how the synthetic test documents are generated, and
[TIER1_BLOCK3.md](TIER1_BLOCK3.md) for the extraction/calculation design and its measured
results.

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
make docs     # 18 synthetic bills + answer keys into data/generated/ (git-ignored)
make eval     # score extraction and the pipeline against those answer keys
make check    # ruff + the full test suite (139 tests)
```

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
  main.py      FastAPI app
  money.py     one definition of "round to cents", shared by generator and extraction
  models/      Pydantic domain models: answer keys, extraction targets, factor citations
  synthgen/    the synthetic-document generator (answer first, then the document)
  tools/       the tools the agent is allowed to call
db/migrations/ schema, applied in filename order
scripts/       operational scripts: factor loading, document generation, the eval
tests/         139 tests; the new ones need no .env, no database and no API key
data/raw/      source documents — git-ignored, never committed
data/generated/ synthetic documents, answer keys, eval runs — git-ignored
```
