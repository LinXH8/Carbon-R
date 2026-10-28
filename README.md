# Carbon R

[![ci](https://github.com/LinXH8/Carbon-R/actions/workflows/ci.yml/badge.svg)](https://github.com/LinXH8/Carbon-R/actions/workflows/ci.yml)

Upload a utility bill or fuel invoice, get a greenhouse-gas figure that carries its own
receipts: the emission factor used, its unit, publisher, year, and the document it came from.

**Status:** Tier 2 — usable end to end in the browser, and from a fresh clone with
`docker compose up`. Upload an electricity bill, an LPG invoice or a vehicle fuel-card
statement and get a cited Scope 1 or Scope 2 figure, with every line of the derivation
shown; ask a methodology question and get verbatim GHG Protocol quotes with their pages, or
an honest "not answered". The test set is 198 synthetic documents over four layouts and
three difficulty tiers, one layout held out; results are under [What the evals
measure](#what-the-evals-measure).

## Quick start (Docker)

```bash
git clone https://github.com/LinXH8/Carbon-R.git && cd Carbon-R
echo "OPENAI_API_KEY=sk-..." > .env      # the only thing you need to supply
docker compose up --build
open http://localhost:8000
```

Compose runs its own Postgres (with pgvector), applies the migrations and seeds the
emission factors on every start (idempotent), and pins the model ids the README's numbers
were measured with (`OPENAI_MODEL`, `OPENAI_EMBEDDING_MODEL`; override either in `.env`).
Without a key the stack still starts, and `/health` says what is missing. To try it without
a bill of your own, render a synthetic one inside the container and upload it on the page:

```bash
docker compose exec app python scripts/generate_docs.py --kind electricity_bill --tiers clean --out /tmp/docs
docker compose cp app:/tmp/docs/electricity_bill/clean/elec_001.pdf .
```

Methodology questions need the GHG Protocol standards, which cannot be redistributed; until
they are ingested, `search_methodology` says so instead of answering. See [Methodology
corpus](#methodology-corpus). The same holds for the peer companies' reports and
`search_peer_disclosures`: see [Peer corpus](#peer-corpus).

More: [architecture](docs/architecture.md) · [data and its licences](docs/DATA.md) ·
[ADR 0001: exact SQL for factors](docs/adr/0001-exact-sql-for-factors.md) ·
[ADR 0002: Postgres + pgvector, no ANN index yet](docs/adr/0002-postgres-pgvector.md)

## The one design rule worth stating up front

**The language model never does arithmetic.** It reads documents and chooses tools. Every
multiplication happens in `calculate_emissions`, in plain Python, against a factor fetched
by exact SQL. A model that is asked to multiply will sometimes produce a plausible wrong
number with no way to audit it; a model that is asked only to extract and to choose can be
measured, and its mistakes show up in an eval instead of in a disclosure.

[CLAUDE.md](CLAUDE.md) holds the full set of rules this repo holds itself to, and
[SOURCES.md](docs/SOURCES.md) every dataset's provenance and verification status.

The per-block records are written for someone who was not there — the decisions and their
reasons, the defects and what caught them, and the evidence behind every number:

| | |
|---|---|
| [TIER1_BLOCK3.md](docs/blocks/TIER1_BLOCK3.md) | extraction, factor lookup, the deterministic calculation |
| [TIER1_BLOCK4.md](docs/blocks/TIER1_BLOCK4.md) | the agent loop and the API |
| [TIER1_BLOCK5.md](docs/blocks/TIER1_BLOCK5.md) | the page, streamed tool calls, Tier 1 sign-off |
| [TIER2_BLOCK1.md](docs/blocks/TIER2_BLOCK1.md) | Scope 1: two new document kinds, the DESNZ factors, and the four defects the first eval run found |
| [TIER2_BLOCK2.md](docs/blocks/TIER2_BLOCK2.md) | four layouts per kind, the quirk catalogue, difficulty tiers, and the first baseline on 166 documents |
| [TIER2_BLOCK3.md](docs/blocks/TIER2_BLOCK3.md) | the eval harness: per-tier and per-quirk tables, four normaliser fixes, the scoped retry, the company-year scenarios, and the holdout scored last |
| [QUIRKS.md](docs/synthgen/QUIRKS.md) | what each quirk looks like on the page and what it tests, per generator version |
| [SYNTHGEN_SPEC.md](docs/synthgen/SYNTHGEN_SPEC.md) | the generator's specification and its open VERIFY items |

[SYNTHGEN_V0.md](docs/synthgen/SYNTHGEN_V0.md) describes the **superseded v0** generator — 18 bills, one
layout — and is kept for the record rather than as a current description.

## What works today, and what it measures

Five tools, each returning `{status, data, message}`:

| Tool | What it does |
|---|---|
| `extract_from_document` | Establishes what kind of document a page is, then has a vision model transcribe what is printed and returns the measurable activities it carries. It is never asked for a derived figure. A kind it cannot establish is refused, not guessed. When a printed figure fails a consistency check, it retries **once**, with the error and without the answer, and takes back only the fields that check reads. |
| `lookup_emission_factor` | Exact SQL (`category`, `unit`, `year`), never similarity search. Returns the factor with publisher, source document, page, scope, method, gas basis and retrieval date. |
| `calculate_emissions` | Plain Python. Takes a list of (activity, factor) pairs and returns each line and the total, with every input used, the derivation written out, and structured disclosure flags. |
| `search_methodology` | Similarity search over GHG Protocol methodology prose, never a factor. Returns verbatim quotes with document and page, or `no_results` — either because nothing is close (retrieval level) or because the closest passages do not answer the question (generation level, decided by a judging model whose quotes must be found verbatim in the passages). Nothing it returns can enter a calculation. |
| `search_peer_disclosures` | Hybrid search (vector and keyword rankings fused, filtered by company, sector and report year) over 12 Singapore-listed companies' sustainability reports: how they draw boundaries, set targets, treat financed emissions. Returns verified verbatim quotes with company, report and page, or `no_results` (not ingested; a filter that matches nothing, with what does exist; or passages that do not answer). **Never a figure**: the reports' PDF text layers fuse footnote markers onto table numbers, so table-like quotes are withheld and cited by page, and the figure audit flags any peer number stated outside a verified quote. Peer disclosures are self-reported under differing boundaries and periods, and every result says so. |

Rolling a year of documents up into a company total, and allocating a share of it to one
customer, are **plain Python in `tools/company_year.py`** (`roll_up`, `allocate`). They are
not agent tools yet — exposing them is a Tier 3 item — but the company-year scenarios are
scored through them, so the arithmetic they do is measured.

### Three document kinds, two scopes

| Document | Activity | Factor | Scope |
|---|---|---|---|
| Electricity bill | kWh | EMA grid emission factor | 2, location-based |
| LPG invoice | kg of gas, computed from the cylinder lines | DESNZ, per tonne | 1, combustion |
| Fuel-card statement | litres **per product** | DESNZ, per litre, diesel and petrol separately | 1, combustion |

Every measured result lives in one place — [What the evals
measure](#what-the-evals-measure) — so there is no second set of counts to quote by
accident. In short: on the 166 non-holdout documents of the v1 set, **every emissions
figure is exact on every tier and every quirk (166 of 166)**, and the field-level numbers
are lower and mostly limited by Python normalisation rather than by the model. The held-out
layout was scored once, last.

Two behaviours those counts depend on are worth stating here, because both are cases where
the honest output is *no* figure:

- **A factor that is not published is a refusal, not a substitution.** Where no factor
  exists at or before a document's activity year, the tool returns `no_results` and the
  agent says which years the table does hold. An eval that counted that as a miss would be
  rewarding a guess, so it is scored as a pass.
- **A kind that cannot be established is a refusal too.** A file that is not one of the
  three kinds, or holds two at once, is reported and asked about rather than read under a
  guessed schema.

What these figures do **not** cover is set out under [Limitations](#limitations).

### The agent that drives them

An agent chooses which tool to call; it does not compute. Two properties make that
structural rather than a matter of prompt wording:

- **It passes handles, not values.** The calculation tool takes `activity_ref` and
  `factor_ref` handles into the session's stored tool results — never an amount, never a
  factor. A model that cannot write a digit into a calculation cannot mistype `0.402` as
  `0.42`. That extends to totals: a fuel-card statement with diesel and petrol is one call
  with two pairs, and the sum is Python's.
- **Its answer is audited.** Every number in a reply is matched against the numbers the
  tools returned; anything untraceable comes back on the response as `unverified_figures`.
  Every quoted span is matched against the strings the tools returned in the session;
  anything not found verbatim comes back as `unverified_quotes`.

The loop is bounded at 8 turns. On `no_results`, on an error, or when the turns run out,
it reports what is missing and asks — it never assembles a figure to look finished. Three
refusal paths are exercised live and recorded in
[TIER2_BLOCK1.md](docs/blocks/TIER2_BLOCK1.md): a document whose kind cannot be established, a file
holding two kinds at once, and an activity year for which no factor is published.

`calculate_emissions` is also all-or-nothing: if any line of a multi-line calculation
cannot be computed, it returns no figure at all. A total over the lines that happened to
work would look like an answer, be smaller than the truth, and carry nothing on its face to
say a line was missing.

The same property holds all the way to the screen: `/chat` returns the structured output of
each `calculate_emissions` alongside the prose, and the page renders the figure from that.
A number a user reads has come from the Python that computed it, not from the model's
transcription of it.

## What the evals measure

### Reproducing the test set

The documents are not committed; the generator and its seed are, so the set can be rebuilt
bit-for-bit at the answer-key level:

- **seed** `20261016`, recorded in each kind's `manifest.json`;
- **generators** `synthgen-electricity-3`, `synthgen-lpg-2`, `synthgen-fuel-card-2`;
- **output** `data/generated/synthgen_v1/<kind>/<clean|moderate|hard>/`, each document a
  page (`.pdf`, or `.png` for a scanned or photographed one) beside its JSON answer key.

synthgen v1 holds **198 documents**: 93 electricity bills, 51 LPG invoices and 54 fuel-card
statements, over four layouts per kind and three difficulty tiers. Layout `d` of each kind
is **held out**: never looked at while tuning, and excluded from `make eval` by default.
Every injected quirk appears on at least 15 non-holdout documents per kind; `make
docs-plan` prints the table. Arithmetic closes to the cent on all 198. Determinism is
asserted on the JSON keys and the HTML; PDF bytes carry a WeasyPrint timestamp and are not
compared.

What each quirk looks like on the page, and what it tests, is described per version in
[QUIRKS.md](docs/synthgen/QUIRKS.md).

### The synthgen v1 baseline

One fresh run on the 166 non-holdout documents, at commit `719b840` (clean tree), model
`gpt-5.4-mini-2026-03-17`, request `1788ce3e`, 20 Oct 2026:

| Measure | Result |
|---|---|
| Document kind correct | 166 of 166 |
| Activity recovered (kind, amount, unit **and year**) | 166 of 166 |
| **Pipeline kgCO2e exact** | **166 of 166**: clean 69 of 69 · moderate 48 of 48 · hard 49 of 49 |
| Every field correct, electricity | 57 of 82 (29 of 29 clean · 20 of 26 moderate · 8 of 27 hard) |
| Every field correct, fuel card | 27 of 42 (14 of 18 · 11 of 12 · 2 of 12) |
| Every field correct, LPG | 0 of 42 (two normaliser defects; see below) |

Every emissions figure was right on every tier and every quirk, including 15 bills split
across 31 December, 24 printed in MWh, and every credit, balance forward and GST-inclusive
headline. **The field-level numbers are lower, and mostly not because of the model.**
Of the 125 failing fields:
- 96 trace to Python normalisation: MWh meter readings not converted; an LPG charge row
  dropped when its quantity is a word; the rental row's label not recognised; an inline
  "UEN" label not stripped;
- 8 trace to two layouts that print two fields under one label;
- 3 are the model "correcting" an odd-looking identifier;
- 18 are genuine misreads, 15 of them on phone-photo pages.

The credit column needs a caveat. The eval itself had a defect: it compared against a field
name the answer key does not use. The figures above re-score that column from the same
recorded replies against the right field (165 of 166), with no new model calls; the run as
printed, defect included, is kept in [TIER2_BLOCK2.md](docs/blocks/TIER2_BLOCK2.md).

**What this did not measure.**
- It is one run, so run-to-run variation is unmeasured.
- The held-out layout was not part of it. It was scored once, after Block 3's fixes;
  see below.
- The documents are synthetic.

### After Tier 2 Block 3: the eval fixed, the normaliser fixed, the holdout scored

Details, commits and evidence: [TIER2_BLOCK3.md](docs/blocks/TIER2_BLOCK3.md). Every figure below is a
count from a recorded run.

**The same 166 replies, re-scored after each fix** (0 model calls; commit `eb4b86a`):

| Measure | Baseline | Now |
|---|---|---|
| Pipeline kgCO2e exact | 166 of 166 | 166 of 166 |
| Every field correct, electricity | 57 of 82 | **80 of 82** |
| Every field correct, LPG | 0 of 42 | **40 of 42** |
| Every field correct, fuel card | 27 of 42 | **28 of 42** |
| Field failures | 125 | **29** |

The eval's credit-column defect was fixed first. Then four normaliser changes, one per
commit, removed exactly the number of failures each was predicted to: 48, 25, 20 and 3.
The 29 that remain:
- 18 genuine misreads, 15 of them on phone photos;
- 8 where a layout prints two fields under one label;
- 3 where the model "corrects" an odd identifier.

**One retry, with the error and never the answer.** When a field will not parse or the
page's arithmetic does not close, the page is read once more. The model is told which
check failed, never which value is expected.

The first version took the whole second reading, and overwrote **3 correct fields while
fixing 0**. It now takes only the fields the failed check reads: **0 correct → wrong**.
Counts so far:
- on the tuned set, 2 unscored values corrected, both checked against the key by hand;
- on the holdout, 1 scored field corrected (wrong → correct).

That is five triggers in all: a small sample.

**The held-out layout, scored once, last** (32 documents, fresh):
- document kind, activity and **pipeline kgCO2e: 32 of 32**;
- every field: electricity 0 of 11, LPG 7 of 9, fuel 5 of 12.

24 of the 26 field failures come from two labels that layout `d` prints and the normaliser,
tuned on layouts a–c, did not know: "Registration" for the UEN, and "Low Tension supply".
That is overfitting, which is what a holdout is there to show. It is recorded, not fixed.

**Company-year scenarios.** 21 scenarios run at calculation level:
- one fictional business's year, Makan Mates, with 15 variations;
- five other businesses.

Each scenario's truth activities go through factor lookup, calculation, a year roll-up and
a revenue-share allocation. The results are checked against an independent exact-rational
route and against the honesty facts each scenario defines. **21 of 21 pass all ten
checks:**
- Scope 1, Scope 2 and the allocation are exact;
- gaps are listed exactly;
- estimated readings are listed;
- documents with no published factor are named;
- factor-year substitutions are disclosed;
- days outside the year are listed, not counted;
- USD invoices are listed and not converted;
- a scope with no document has no figure rather than 0.

Tests show this check fails a roll-up that hides gaps, drops exclusions or counts a
neighbouring year.

| Makan Mates 2025 | kg CO2e |
|---|---|
| Scope 1 (LPG 7.7 t, diesel 3,775.19 L; DESNZ 2025, non-Singapore) | 32,680.936 |
| Scope 2 (99,740 kWh; EMA 2024 factor, substituted and disclosed; location-based) | 40,095.480 |
| Allocated to one buyer (Scope 1 + 2 × 720,000 / 2,400,000 revenue; an assumption) | 21,832.925 |

These are computed from the activities and the verified factors in the database. They are
not the 30.1 / 39.4 / 21 t of the project's illustrative context.

**End to end, three scenarios through extraction** (107 documents):
- activity and pipeline: 107 of 107;
- the year's figures equal the calculation level in all three;
- the March gap and the two estimated readings were flagged **from what was read**.

The scenarios are synthetic, and each was run once.

`make flow-block2` passed 4 of 4 on all six checks. That includes a bill across 31 Dec 2023
that the agent split into two lines with two EMA factors (0.412 and 0.402), each cited.

The flow checks are flow checks, not measurements: a handful of documents asked one
question cannot measure accuracy, and the script says so on every line it prints. They
check that the loop finishes, that its figure equals the answer key's, that every factor is
cited, that the disclosures are stated, that no number appears which no tool produced, and
that a document with no published factor is refused rather than guessed.

`make flow-scope1`, on the Block 1 set, is **4 of 4 on all six checks** across the three
kinds, 15 turns, with one unit conversion, one two-line total and one honest refusal.
`make eval` over the 166 non-holdout v1 documents remains the measured number; the flow
checks only show the whole path runs.

`make eval` caches each model reply, keyed on the document, model, DPI and a hash of the
prompt *and* the response schema. A cache hit skips only the model call — every step after
it re-runs against current code, so a fix can be re-measured against the same replies at no
API cost. Changing the prompt or the schema misses the cache by construction.

### Methodology retrieval (Tier 2 Block 4)

`search_methodology` is scored against a key of 48 questions drafted by reading the three
GHG Protocol standards (Corporate Standard, Scope 2 Guidance, Scope 3 Standard), never from
retrieval output: 36 answerable — 12 in each of three phrasings (`textbook`, in the
standards' own words; `user`, as an SME finance or operations person would ask;
`singlish`, as a Singaporean user might type it) — and 12 that the standards do not answer.
Each label carries an anchor sentence that a test finds on the labelled page of the PDF.
The key was checked against the documents once, after the first dev run, under one rule (a
page counts only if it states the answer itself); every added and rejected page is listed
in [TIER2_BLOCK4.md](docs/blocks/TIER2_BLOCK4.md). A hit is the right **page** in the top 5 passages.

Final setup, commit `b2fc2e4`; the test split was scored **once**, after everything was
frozen:

| | Dev hit@1 | Dev hit@5 | Test hit@1 | Test hit@5 |
|---|---|---|---|---|
| All answerable | 4 of 18 | 12 of 18 | 8 of 18 | 14 of 18 |
| textbook | 2 of 6 | 4 of 6 | 4 of 6 | 6 of 6 |
| user | 2 of 6 | 3 of 6 | 1 of 6 | 4 of 6 |
| singlish | 0 of 6 | 5 of 6 | 3 of 6 | 4 of 6 |
| Corporate Standard | 1 of 6 | 4 of 6 | 3 of 6 | 5 of 6 |
| Scope 2 Guidance | 0 of 6 | 3 of 6 | 3 of 6 | 5 of 6 |
| Scope 3 Standard | 3 of 6 | 5 of 6 | 2 of 6 | 4 of 6 |

| Refusals | Dev | Test |
|---|---|---|
| Unanswerable questions refused | 6 of 6 | 6 of 6 |
| Answerable questions wrongly refused | 2 of 18 | 1 of 18 |
| Answered, citing a labelled page | 11 of 16 | 12 of 17 |

What these numbers do and do not show:

- **Every refusal came from the generation-level check.** The retrieval-level floor (cosine
  similarity 0.30) refused nothing on either split: similarity scores for answerable and
  unanswerable questions overlap, so no threshold separated them without refusing
  answerable questions. It is kept as a guard, and recorded as doing no measurable work.
- **Cells are six questions.** Test scoring above dev is within what two draws of six can
  produce, and is not the result of tuning.
- **User-style questions retrieve worst at the top** (hit@1 2 of 6 and 1 of 6): phrasing a
  question away from the standards' vocabulary costs the most.
- **The judge is not deterministic.** Two dev runs on identical retrieval (top 5 equal on
  24 of 24 questions) differed by one question on each judge-level count.
- **One retrieval change was tried on dev and reverted** — embedding each chunk with its
  document's title. hit@5 fell from 12 to 10 of 18 and answers citing a labelled page from
  12 of 15 to 9 of 16, because the title pulled in passages from the wrong standard.
- **A verified quote is real, not necessarily relevant.** In that experiment one answer
  quoted, verbatim, a passage on transmission losses for a question about purchased
  electricity. The verbatim check cannot catch that; only the labelled-page metrics can.

The corpus is local only. The standards' terms prohibit public reproduction and extraction
by data mining, so no GHG Protocol text is committed: the anchors are stored as hashes,
the run files hold ids, pages and scores, and the PDFs and their chunks stay on the
machine that ingested them.

### What CI runs, and what it does not

CI runs lint and the offline tests — the chunker on hand-written text fixtures, the refusal
decisions, the quote and figure audits, the key's schema and balance — none of which need a
database, an API key or the corpus. The extraction and retrieval evals need all three (the
retrieval eval also needs the git-ignored GHG Protocol PDFs), so they run locally and their
run files are committed with the commit SHA they measured. "Evals on every push" means
those committed runs, not a CI job.

## Running it

Requires [uv](https://docs.astral.sh/uv/) and Python 3.13 (uv will fetch it).

```bash
# This project uses a named virtualenv, .venv-carbon-r, rather than a bare .venv.
export UV_PROJECT_ENVIRONMENT=.venv-carbon-r   # or just use `make`, which sets it
uv sync

cp .env.example .env     # then fill in OPENAI_API_KEY and DATABASE_URL
uv run uvicorn carbon_r.main:app --reload   # or: make run
open http://localhost:8000
```

`/health` reports whether the database and the model provider are configured, so a partly
configured checkout tells you what is missing instead of crashing.

Every `/chat` response carries a `run_id`; the server logs one `run_id=… tool=… status=…`
line per tool call, so a reply on screen can be matched to the calls behind it.

### Methodology corpus

`search_methodology` searches the GHG Protocol Corporate Standard, Scope 2 Guidance and
Scope 3 Standard. Their terms do not allow redistribution, so a clone does not include them
and the database starts with none of their text. To enable it:

1. Download the three PDFs from ghgprotocol.org (the [Corporate
   Standard](https://ghgprotocol.org/corporate-standard), the [Scope 2
   Guidance](https://ghgprotocol.org/scope-2-guidance) and the [Scope 3
   Standard](https://ghgprotocol.org/corporate-value-chain-scope-3-standard)) and save them
   as `data/raw/ghgp/ghg-protocol-revised.pdf`, `data/raw/ghgp/Scope 2 Guidance.pdf` and
   `data/raw/ghgp/Scope 3 Standard.pdf`.
2. Ingest them (about 220,000 embedding tokens, once):

   ```bash
   docker compose run --rm app python scripts/ingest_methodology.py   # Docker
   make ingest-methodology                                            # local
   ```

The ingest checks each file's sha256 against the registry in `src/carbon_r/sources.py` and
refuses one that differs — a re-published edition is a different document, and the eval's
page labels would not apply to it.

### Peer corpus

`search_peer_disclosures` searches 14 report files from 12 companies (banks, REITs,
industrials, energy), listed with URLs, periods and sha256 in
[`data/peer_sources.json`](data/peer_sources.json). They are the issuers' copyright, so a
clone includes only that list. To enable the search, save each file under `data/raw/peer/`
with the `local_filename` the list gives, then:

```bash
uv run python scripts/peer_intake.py check     # every file present, with the listed bytes
uv run python scripts/ingest_peer.py --preview # page offsets and sample chunks; no API calls
make ingest-peer                               # about 950,000 embedding tokens, once
```

A report's printed page numbers are used only where one offset matches at least 80% of its
pages' footers; otherwise its citations give the PDF page and say so. `report_year` is the
calendar year in which a report's period ends, so the April-to-March trusts (MPACT, MLT)
are 2026.

### The page

`http://localhost:8000` is a single page with no build step: drop a bill on it, ask a
question, and watch the tool calls arrive as they happen — the steps on screen are the
real tool transcript being built, not a progress bar on a timer.

The answer panel shows the figure in kg and tonnes, the arithmetic written out, and the
factor with its publisher, both years, source document and page or table, followed by the
disclosures the result must carry. **Those numbers are read from the structured output of
`calculate_emissions`, never parsed out of the agent's prose** — the agent's wording is
shown separately and nothing is extracted from it. Pull up the audit sheet to see every
tool call with its arguments and its result.

If the figure audit ever finds a number in the reply that no tool produced, the page says
so above the figure rather than below it.

### Asking a question about a bill from the shell

```bash
curl -F file=@data/generated/electricity_bill/clean/elec_010.pdf localhost:8000/upload
# {"session_id":"sess_…","document_id":"doc_…", …}

curl -s localhost:8000/chat -H 'content-type: application/json' -d '{
  "message": "What are the Scope 2 emissions for this bill?",
  "session_id": "sess_…"
}'
```

The reply comes back with `figures` — the structured output of each
`calculate_emissions`, with the factor and its provenance — alongside the prose, the tool
transcript that produced it, the turn count, and `unverified_figures`, which must be empty.
`POST /chat/stream` is the same turn as server-sent events, emitting each tool result as it
lands and then exactly one `final` event carrying the whole response.

Sessions are in-memory, so they do not survive a restart; persistence is a Tier 3 concern.

### Database

Any Postgres 15+ will do. Either point `DATABASE_URL` at a hosted instance, or:

```bash
docker compose up -d db
```

Then load the emission factors (safe to re-run — it upserts):

```bash
make seed          # EMA grid emission factors (Scope 2), 20 years
make seed-desnz    # UK DESNZ fuel combustion factors (Scope 1)
```

Both are idempotent: re-running changes nothing. `make verify-desnz` re-reads the DESNZ
workbooks and diffs them against the values the loader carries, so the transcription is
checkable rather than trusted once — it needs the git-ignored spreadsheets present.

### Generating documents and scoring them

```bash
make docs-plan    # print the synthgen v1 quirk x tier table and pre-render checks; writes nothing
make docs         # the synthgen v1 set: 198 documents + answer keys (git-ignored)
make eval         # score routing, extraction and the pipeline on v1 (holdout excluded),
                  # per tier and per quirk, with and without the one retry
make eval-holdout # the held-out layout only - scored once, at the end of Block 3
make eval-scenarios      # 21 company-year scenarios at calculation level (0 model calls)
make scenarios           # render those scenarios' 676 documents (git-ignored)
make eval-scenarios-e2e  # three of them end to end, through extraction (model calls)
make eval-block1  # the Block 1 set (36 clean documents), as Block 1 measured it
make agent-smoke  # end-to-end flow check of /upload + /chat on 5 Block 1 bills
make flow-10      # the same flow on 10 Block 1 bills spanning both factor years
make flow-scope1  # one Block 1 document of each kind, including a refusal
make flow-block2  # v1: a bill across 31 Dec 2023, an MWh bill, a balance forward, a photo
make ingest-methodology  # chunk + embed the local GHG Protocol PDFs (idempotent; local only)
make eval-retrieval      # score search_methodology on the dev split (model calls)
make ingest-peer         # chunk + embed the local peer reports (idempotent; local only)
make check        # ruff + the full test suite (760 pass, 1 skipped, with everything local)
```

## Data provenance

No third-party source document is committed to this repo. Emission factors are transcribed
from primary sources with their citation recorded alongside, and every dataset used is
listed in [SOURCES.md](docs/SOURCES.md) with its publisher, URL and retrieval date.

| Factor | Publisher | Licence | Scope | Basis |
|---|---|---|---|---|
| Singapore grid electricity, 2005–2024 | EMA, *Singapore Energy Statistics 2025*, Table 2.4 | — | 2 | CO₂ only |
| LPG, diesel, petrol, 2025–2026 | DESNZ, *GHG Conversion Factors*, sheet `Fuels` | Open Government Licence v3.0 | 1 | CO₂e |

Methodology passages come from the GHG Protocol Corporate Standard (2004), Scope 2 Guidance
(2015) and Scope 3 Standard (2011), WRI / WBCSD. They are used locally under
ghgprotocol.org's Terms of Use, which prohibit public reproduction; their text is never
committed or published. Licences and hashes are in [SOURCES.md](docs/SOURCES.md) and the
`sources` table.

Peer passages come from 12 companies' own sustainability reports (copyright of each
issuer), used locally; only their provenance is committed (`data/peer_sources.json`). A
public deployment would cite them, not display them.

The DESNZ factors are **published for the UK, not Singapore**, and every result that uses
one says so. They are also CO₂e where EMA's is CO₂ only, so a figure combining the two
carries a disclosure that the carbon-dioxide-only line excludes methane and nitrous oxide.
The reasoning behind choosing the 100%-mineral diesel and petrol rows — and the fact that
it rests on an **unverified assumption** about Singapore pump fuel — is recorded in
[SOURCES.md](docs/SOURCES.md) §9.

## Limitations

Stated plainly, because they bound what any number here means:

- All documents processed so far are **synthetic**. None are real customer documents.
- Emission estimates produced here are **not assured** by any third party.
- **Peer search returns prose, not figures.** No peer emissions figure has been verified
  yet; the reports' text layers misread tables. Its judge can accept quotes that are real
  but do not answer (seen once in the demo questions), and there is no peer retrieval eval
  yet — the checks so far are 8 demo questions and 3 adversarial agent runs, recorded in
  [TIER2_BLOCK4.md](docs/blocks/TIER2_BLOCK4.md).
- Electricity figures are **location-based** (average grid intensity), not market-based.
- **The fuel factors are not Singapore factors.** LPG, diesel and petrol use UK DESNZ
  values, flagged as non-Singapore on every affected result. That they are the closest
  available is a judgement, not a measurement.
- **Diesel and petrol use the 100%-mineral rows on an unverified assumption** about the
  biofuel content of Singapore pump fuel. NEA's own fuel specification has not been read.
- **Every figure here is one run.** The synthgen v1 numbers cover four layouts, three
  tiers, scans, photos and quirks — but each measure was run once, so run-to-run variation
  is unmeasured. A clean run is weak evidence of correctness, not proof of it.
- **A CO2-only electricity figure is labelled "kg CO2e".** `calculate_emissions` names its
  unit "kg CO2e" whatever the factor's gas basis. Mixing CO2 and CO2e lines is disclosed;
  a figure that is CO2 only is not labelled as such.
- **In synthgen v1, a hard LPG or fuel document is harder to read, not harder to
  interpret**: their injected quirks are all money decoys, and every hard one carries the
  same three, so per-quirk results on them are confounded. See docs/blocks/TIER2_BLOCK2.md.
- **The evals have found defects that the tests did not.** In Block 1 four, in Block 2 a
  defect in the eval itself, in Block 3 a memoised database error and a retry that
  overwrote correct fields. Each is recorded in its block's write-up. A green suite is not
  evidence that a measurement is sound.
- **The LPG documents carry less variation than their count suggests.** Gas is delivered in
  whole cylinders. synthgen v1 adds per-delivery invoices, top-up visits and mixed sizes,
  but a steady account's monthly invoices still often share a kg figure.
- **The classifier's confidence threshold (0.7) was chosen by eye**, with no labelled set of
  ambiguous documents behind it.
- **The interface has no automated interaction coverage.** The page's figure panel and
  streamed steps are tested against the shape of the response; the drag behaviour of the
  audit sheet has been reviewed and used by hand, not instrumented.
- **Sessions and uploads accumulate** with no eviction, and a restart loses every session.
- **The holdout showed the normaliser overfits its layouts.** On layout `d`, every emissions
  figure was right, but 24 field failures came from two printed labels that layouts a–c do
  not use. They are recorded and not fixed, and the holdout can no longer measure a fix
  for them.
- **Company-year figures are Python functions, not yet agent tools.** The roll-up and the
  allocation are measured by the scenario eval; asking the chat for a year's total or an
  allocated figure arrives in Tier 3. Allocation by revenue share is an assumption, and
  is labelled as one.
- **Methodology retrieval is measured on 48 questions**, 6 per cell, written by one person
  from the documents. Its refusals depend on a judging model that is not deterministic, and
  its quote check proves a quote exists in a source, not that it answers the question. A
  public deployment would show citations rather than GHG Protocol passages.
- Where no factor is published for a document's activity year, the latest factor at or
  before it is used, up to two years back, and the substitution is stated on the result.
  Beyond two years, or where nothing is published at or before that year at all, the tool
  returns `no_results` rather than a stale figure. DESNZ publishes no back series, so an
  activity year before 2025 reaches that path for the fuel factors — one document in the
  set does so on purpose.

## Layout

```
src/carbon_r/
  config.py    settings from the environment
  db.py        connection helper and the migration runner
  sources.py   the source registry: every document read, with licence and sha256
  peer_sources.py  the peer reports' committed list, and their page offsets
  rag/         retrieval: chunking, embedding, ingestion, the methodology eval key
  main.py      FastAPI app: the page, /health, /upload, /chat, /chat/stream
  money.py     one definition of "round to cents", shared by generator and extraction
  models/      Pydantic domain models: answer keys, extraction targets, activities,
               factor citations, and the document-kind classification
  xlsx.py      a read-only xlsx reader, for transcribing publishers' workbooks
  synthgen/    the synthetic-document generator (answer first, then the document):
               layouts, quirks, degradation, and the company-year scenarios
  tools/       what the agent may call (including methodology.py, peer.py), plus company_year.py —
               a roll-up and an allocation that are Python only, not yet exposed as tools
  agent/       the tool-calling loop, the session workspace, the figure and quote audits
  api/         request and response models, upload storage, the SSE stream
  static/      the page: one HTML file, its stylesheet, its script, and a spring
db/migrations/ schema, applied in filename order
scripts/       factor loading, document and scenario generation, the extraction,
               scenario and retrieval evals, methodology and peer ingest, peer intake, the flow
               checks, the container entrypoint
tests/         in a clean copy (no .env, data or database; what CI runs): 740 pass, 21 skip - the ones needing a
               database, the workbooks, the corpus or the peer reports. With all of
               those present locally: 760 pass, 1 skip
docs/          architecture diagram, ADRs, the GHG Protocol skim guide
data/evals/    the retrieval key (hashes, no GHG Protocol text) and committed eval runs
data/peer_sources.json  provenance of the peer reports (the reports are local)
data/uploads/  documents uploaded at runtime — git-ignored, never committed
data/raw/      source documents and publishers' workbooks — git-ignored, never committed
data/generated/ synthetic documents, answer keys, eval runs — git-ignored
Dockerfile, docker-compose.yml   the image and the two-container stack
.github/workflows/ci.yml         lint, offline tests, image build
```
