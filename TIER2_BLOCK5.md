# Tier 2 Block 5 — engineering and release

Written for someone who was not there: the design decisions and their reasons, the defects
found and what caught them, and the evidence behind every number. Environment details and
career notes live in the local build log, not here.

**Status:** done 23 Oct 2026. Tagged **`v0.2-tier2`** at `b71061c`. CI green on that commit.
Langfuse tracing is deferred (see the end).

## What this block had to do

Tier 2's done check: the repo runs from `git clone` + `docker compose up`, CI runs on every
push, and the README states measured results. Before this block there was no Dockerfile, no
CI, and a `docker-compose.yml` with a database service that had never been run.

## What was built

| Piece | What it does, and why it is shaped that way |
|---|---|
| `Dockerfile` | Python 3.13 slim; `poppler-utils` (pdftoppm, which rasterises pages for the vision model) and WeasyPrint's pango/harfbuzz libraries plus one font family; dependencies in a layer before the source; non-root user `carbon`, which owns `/app/data/uploads` so a named volume mounted there inherits it; a health check written in Python, because the slim image has no curl |
| `.dockerignore` | No `.env`, no `data/raw`, no eval anchors, no notes: nothing secret or third-party can reach the image |
| `scripts/docker_entrypoint.sh` | Waits for Postgres, runs both factor loaders (idempotent, from literals, so no workbook is needed), then serves |
| `docker-compose.yml` | Its own Postgres with pgvector (port bound to `127.0.0.1` only), an `app` service whose `DATABASE_URL` always points at it, and the two model ids pinned but overridable — so a fresh clone needs only `OPENAI_API_KEY` |
| `search_methodology` | Counts the corpus, under the search's own filter, before embedding anything; an empty corpus is `no_results` with `refusal_level: "not_ingested"` and no API call |
| `run_id` | On every `/chat` response and on one log line per tool call |
| `.github/workflows/ci.yml` | Lint, the offline suite and an image build. No secrets: the evals stay local, as the README says |
| `DATA.md`, `docs/architecture.md`, ADR 0001, ADR 0002 | Datasets with licences and refresh procedures, the data dictionary and ER diagram; the architecture; exact SQL for factors; Postgres + pgvector with no approximate index yet |

### Decisions and why

- **Compose pins the model ids, `config.py` does not.** The no-default rule (a model id must
  be one the billed account has) stays in code; compose supplies the project's measured
  snapshot. Without that, a clone with only a key would have had empty model ids.
- **Seeding on every start.** Both loaders apply all migrations and upsert; the second start
  inserted 0 rows. `docker compose up` is therefore the whole setup, every time.
- **The corpus is never in the image.** It cannot be redistributed (TIER2_BLOCK4.md). Compose
  mounts `data/raw` read-only for a user who has downloaded it; the README says how.

## Verification evidence

**Clean-clone test suite** (a fresh clone, no `.env`, no data): **644 passed, 20 skipped**.
The 20 are the tests needing a database, the DESNZ workbooks, the corpus or the peer reports.
With all of those present locally: 663 passed, 1 skipped. CI reproduced the clean-clone
count exactly on both pushed commits (`0e77cab`, `b71061c`): 644 passed, 20 skipped; the
image job found pdftoppm 22.12.0 and WeasyPrint 70.0.

**Fresh-clone test**, from GitHub (`0e77cab`), into an empty directory, with a one-line
`.env` holding only `OPENAI_API_KEY`:

| Check | Result |
|---|---|
| `docker compose up -d --build` | app healthy; `/health` db connected, model from compose |
| Seeded | 26 emission factors; 0 sources, 0 chunks |
| Synthetic bill rendered in the container | 33 of 33 close to the cent; `elec_001`, Jan 2024, 4,016 kWh |
| `/upload` as the non-root user | written to the volume, owned by `carbon` |
| `/chat` "What are the Scope 2 emissions for this bill?" | extract → lookup → calculate, all ok; **1,614.432** — equal to 4,016 × 0.402 computed independently; EMA 2024 factor cited |
| Audits | `unverified_figures []`, `unverified_quotes []` |
| Methodology question, no corpus | `no_results`, "not loaded in this database … see README 'Methodology corpus'" |
| Teardown | `down -v`: no containers or volumes left |

**After the prompt fix** (`b71061c`), on a local stack with no corpus: the same methodology
question three times — `unverified_quotes` empty in **3 of 3**, each reply saying the corpus
is not loaded and pointing to the README. Regression checks against the hosted database:

| Flow check (NOT accuracy measurements) | Documents | All six checks |
|---|---|---|
| `flow-block2` (cross-year, MWh, LPG, hard fuel card) | 4 | 4 of 4 |
| v1 stand-in for the Block 1 targets: 5 electricity bills, `lpg_004`, `fuel_001`, and `lpg_001` (Dec 2024, no DESNZ factor: must refuse) | 8 | 8 of 8, including the refusal |

## Defects found, and what caught them

| Defect | Caught by |
|---|---|
| **The no-corpus reply quoted a field value** — it wrote `the methodology corpus is "not ingested"` (the tool's `refusal_level` is `not_ingested`) and described a "nearest result" that did not exist | **The quote audit**, on the fresh-clone run: `unverified_quotes: ['not ingested']`. The prompt now branches on `refusal_level`: on `not_ingested`, say the corpus is not loaded and how to load it, quote nothing, describe no result |
| `test_xlsx` passed its own `.py` file as a workbook when the DESNZ workbook was absent, raising a zip error instead of `SheetNotFoundError`: `make check` failed on **every fresh clone since Tier 2 Block 1** | The clean-clone test run before the first push. The test now builds a tiny workbook |
| `make agent-smoke`, `flow-10` and `flow-scope1` cannot run: their Block 1 document set has `schema_version 1` answer keys, and the models now require 2 | Re-running them in this block. Covered by a v1 stand-in (above); the targets themselves are not yet repointed |
| The figure audit flags a numbered-list marker: one no-corpus reply wrote "1. Download…" and got `unverified_figures: ['1']` | The three-run re-check. Recorded, not fixed: excluding list markers relaxes the audit, which is a decision, not a cleanup |
| `logging.basicConfig` at INFO in `main.py` also raised the HTTP client's logs, so every provider request writes a log line (URLs only, no keys) | Reading the flow-check output. Not yet scoped to Carbon R's own loggers |
| The first push was rejected: the GitHub token lacked the `workflow` scope needed to add a workflow file | The push itself; fixed by refreshing the token's scopes |

## Known limits

- The quote audit proves a quoted span exists in a tool payload, not that it answers the
  question (TIER2_BLOCK4.md); the figure audit can flag list numbering.
- The image renders bills with one font family, so a document rendered in the container
  looks different from one rendered on macOS. The synthgen digest hashes HTML, not PDFs, so
  this cannot move an answer key.
- A CO₂-only electricity figure is still labelled "kg CO2e" (README limitations).
- `run_id` is a log key only. Langfuse tracing is **deferred**: it needs an account, and is
  planned as an optional extra that does nothing unless its keys are set.
