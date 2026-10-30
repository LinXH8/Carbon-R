# Tier 1 Block 1 — foundations

Dated 15–16 Oct 2026. Nothing existed before this block. Its job was to set up the parts
every later number depends on: a factor table that cannot hold an anonymous value, a
factor series read from the publisher's own document, and the project rules written down
before any code could break them.

```
Block 1   rules ──► schema ──► verified factor series ──► /health
Block 2   answer keys and documents   (synthgen v0)
Block 3   document ──► figure         (the first measured number)
```

Outcome: a FastAPI app with a `/health` that reports its own state, the `emission_factors`
table, an idempotent loader holding all 20 years of the EMA grid emission factor (each one
machine-checked against the workbook), and `CLAUDE.md`.

Commits: `2c9821b` (the block), `47dd0e4` (GST and carbon tax verified from primary, same
day), `b49a9cc` (GHG Protocol skim guide). These hashes are from after the 17 Oct history
rewrite (`845e4c4`). Hashes quoted anywhere before that commit no longer exist.

---

## The measurement

This block has no accuracy figure because nothing reads a document yet. What it does
measure is the factor data:

| | Result |
|---|---|
| EMA grid factor years loaded | **20** (2005–2024), 2024 = **0.402 kg CO₂/kWh** |
| Literals checked against the workbook | **20 of 20 match**, 0 mismatches |
| Loader run twice | 1st: 20 inserted · 2nd: **0 inserted, 20 unchanged** |
| Rows missing any provenance field | **0** |
| Duplicate `(category, unit, year)` groups | **0** |
| Tests | **3 passing**, with no database and no API key |

### What this does not say

- **One factor series, one jurisdiction.** Scope 1 factors (DESNZ) came later, in
  [Tier 2 Block 1](TIER2_BLOCK1.md).
- **The fallback-year query was run by hand, not by a tool.** It showed that the data
  supports the rule. The rule itself, and its cap, were built and tested in
  [Block 3](TIER1_BLOCK3.md).

---

## Files added

| Path | Purpose |
|---|---|
| `CLAUDE.md` | The seven rules that never change, written as directives an agent can act on |
| `SOURCES.md` (now [docs/SOURCES.md](../SOURCES.md)) | Provenance registry with a verification status per source |
| `pyproject.toml`, `.python-version`, `Makefile` | uv project on Python 3.13; recipes export the named virtualenv |
| `.gitignore`, `data/raw/.gitkeep`, `data/generated/.gitkeep` | All source documents excluded; directory layout kept |
| `.env.example` | Configuration template. The real `.env` is ignored |
| `src/carbon_r/config.py` | `pydantic-settings`, **tolerant of missing config** so a fresh clone boots |
| `src/carbon_r/db.py` | `connect()` context manager and a `check_connection()` that never raises |
| `src/carbon_r/main.py` | FastAPI app, `GET /health` |
| `db/migrations/0001_emission_factors.sql` | The factor table. Provenance columns are `NOT NULL` |
| `scripts/seed_factors.py` | Idempotent loader for the EMA series |
| `scripts/list_models.py` | Lists the model ids actually available to the configured key |
| `tests/test_health.py` | 3 tests |
| `docker-compose.yml` | `pgvector/pgvector:pg17`. Committed here, first run in [Tier 2 Block 5](TIER2_BLOCK5.md) |

---

## Decisions

### Rule 2 is enforced by the schema, not by callers

`publisher`, `source_document`, `source_url` and `retrieved_at` are `NOT NULL`, so a factor
cannot be inserted without them. `(category, unit, year)` is `UNIQUE`: the same category
published in a different unit is a different row, so unit conversion has to happen in
code and can never happen silently inside a lookup. `source_page` can be null only
because spreadsheet sources have no page number.

### `jurisdiction` and `verified` were added before anything needed them

Tier 2 needs both: the non-Singapore disclosure on DESNZ rows, and rule 5's VERIFY status
at row level. Adding two columns to an empty table cost nothing. Migrating them in later
would have cost a migration.

### All 20 years loaded, not one

The checklist said to seed one row (2024). A single row cannot test Block 3's
fallback-year rule ("latest published year at or before the activity year"), and every
year came from the same primary sheet at no extra cost. The rule was then queried directly
against the loaded data:

| Activity year asked | Factor year returned | Factor |
|---|---|---|
| 2024 | 2024 | 0.402 |
| 2023 | 2023 | 0.412 |
| 2022 | 2022 | 0.417 |
| 2021 | 2021 | 0.409 |
| **2026** | **2024** | **0.402**. The substitution is correct, since nothing newer is published |

### Not loaded: Build Margin and the fugitive-methane factor

Both are in the same SES table (T2.4). Neither is a location-based Scope 2 electricity
factor. Keeping them out of `emission_factors` means a lookup can never return the wrong
one.

### Factor values are Python literals, not parsed from the workbook at runtime

The workbook is third-party and git-ignored, so a fresh clone must still be able to seed.
The literals were then re-read from the workbook by script and compared, 20 of 20. They
were not checked by eye.

### data.gov.sg is not a fallback, and the reason was corrected during the block

The first conclusion, that data.gov.sg's "Average Operating Margin" was a different
measure from the SES Grid Emission Factor, **was wrong**. Over the 16 overlapping years,
with half-up rounding to 3 dp, **31 of 32 values match**: `avg_opt_margin` is the SES GEF,
and `build_margin` is the SES BM. The decision (use SES) still stands, for a different
reason. data.gov.sg stops at 2020, and for 2020 it still carries **0.408** where EMA has
since revised the value to **0.407**. It is an out-of-date copy, not an independent source.
So there is no runtime fallback to that API (rule 3's corollary), and
[SOURCES.md §1](../SOURCES.md) records this so the question is not reopened.

### Real Postgres through `psycopg`, not a REST layer

PostgREST cannot run DDL and cannot serve the exact-SQL lookups rule 3 requires. It also
could not have reached pgvector in Tier 2.

### The model id is a dated snapshot

`gpt-5.4-mini-2026-03-17` was chosen from the account's live model list, not from
documentation, and pinned to a dated snapshot rather than a floating alias. An alias
could change underneath the eval figures in the README and make them meaningless. Whether
to move up a model was left for Block 3's per-field eval to decide. It never had to.

---

## VERIFY items

| Item | Status at end of block | Evidence |
|---|---|---|
| EMA GEF 2005–2024 | **verified from primary** | SES 2025 Tabular Data, sheet `T2.4`. Unit printed as `kg CO2 / kWh`. Definition quoted verbatim in SOURCES.md |
| GST 9% | **verified from primary** (`47dd0e4`) | IRAS e-Tax guide *"2024 GST Rate Change: A Guide for GST-registered Businesses"*: "from 8% to 9% with effect from 1 Jan 2024" |
| Carbon tax S$45/tCO₂e, 2026–2027 | **verified from primary** (`47dd0e4`) | NCCS carbon tax page: "raised to S$45/tCO2e with effect from 1 Jan 2026 for 2026 and 2027" |
| Carbon tax offset percentage | **unconfirmed** | Not stated on the page checked. Needed only for the Tier 3 insight line |

Both publishers' HTML pages build their content in the browser, so a plain fetch gets the
page without the figures (the same is true of ema.gov.sg). Verification used each
publisher's static document instead, such as IRAS's e-Tax guide PDF. A secondary source
would not have counted.

---

## Defects found, and by what

1. **`.gitignore` had to exist before the first `git add`.** `data/raw/` already held
   third-party EMA and GHG Protocol files, and the remote is public. Caught before the
   first commit. A later check of the full history found no `.pdf` or `.xlsx` and no key.
2. **Ignoring `data/raw/` as a directory silently dropped `.gitkeep`.** Git does not look
   inside an ignored directory, so `!data/raw/.gitkeep` never applied. The fix was to
   ignore the directory's *contents* (`data/raw/*` with `!data/raw/.gitkeep`). Caught by
   checking that a fresh clone has the directory.
3. **A database URI with a literal `@` in the password.** Python's `urlsplit` accepts it
   because it splits on the last `@`, but libpq does not. A parse check in Python
   therefore passes while the real connection fails. The value has to be percent-encoded,
   and the only reliable test is a real `psycopg.connect`.

---

## Verification evidence

```
$ make check
ruff check .            → All checks passed!
ruff format --check .   → 13 files already formatted
pytest -q               → 3 passed

$ curl localhost:8000/health
{"status":"ok","version":"0.1.0.dev0","db_connected":true,
 "llm_configured":true,"model":"gpt-5.4-mini-2026-03-17"}

$ make seed   (1st)  → electricity_grid_sg: 20 inserted, 0 unchanged
$ make seed   (2nd)  → electricity_grid_sg:  0 inserted, 20 unchanged
```

A 2024 lookup returns the full citation: factor, unit, publisher, source document, page,
URL, jurisdiction, verified and retrieved_at.

---

## Carried forward

- **One checklist line is still open: skimming the GHG Protocol contents pages.** It is a
  reading task, not a numeric claim. [ghg-protocol-skim-guide.md](../ghg-protocol-skim-guide.md)
  maps both tables of contents to the relevant chapters. Still open at the time of writing
  (SOURCES.md §2–3a).
- **Carbon tax offset percentage**: unconfirmed until Tier 3.
- `docker-compose.yml` was committed but not run. It was exercised first in Tier 2 Block 5.
