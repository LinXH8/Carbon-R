# Carbon R

Upload a utility bill or fuel invoice, get a greenhouse-gas figure that carries its own
receipts: the emission factor used, its unit, publisher, year, and the document it came from.

**Status:** Tier 1, Block 1 (foundations). Not yet usable end to end.

## The one design rule worth stating up front

**The language model never does arithmetic.** It reads documents and chooses tools. Every
multiplication happens in `calculate_emissions`, in plain Python, against a factor fetched
by exact SQL. A model that is asked to multiply will sometimes produce a plausible wrong
number with no way to audit it; a model that is asked only to extract and to choose can be
measured, and its mistakes show up in an eval instead of in a disclosure.

See [CLAUDE.md](CLAUDE.md) for the full set of rules this repo holds itself to.

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
uv run python scripts/seed_factors.py
```

## Data provenance

No third-party source document is committed to this repo. Emission factors are transcribed
from primary sources with their citation recorded alongside, and every dataset used is
listed in [SOURCES.md](SOURCES.md) with its publisher, URL and retrieval date.

## Limitations

Stated plainly, because they bound what any number here means:

- All bills and invoices processed so far are **synthetic**. None are real customer documents.
- Emission estimates produced here are **not assured** by any third party.
- Figures are **location-based** (average grid intensity), not market-based.

## Layout

```
src/carbon_r/
  config.py    settings from the environment
  db.py        connection helper
  main.py      FastAPI app
  models/      Pydantic domain models
  tools/       the tools the agent is allowed to call
db/migrations/ schema, applied in filename order
scripts/       operational scripts (factor loading)
data/raw/      source documents — git-ignored, never committed
```
