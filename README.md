# Carbon R

Upload a utility bill or fuel invoice, get a greenhouse-gas figure that carries its own
receipts: the emission factor used, its unit, publisher, year, and the document it came from.

**Status:** Tier 2, Block 1 complete — usable end to end in the browser. Upload an
electricity bill, an LPG invoice or a vehicle fuel-card statement and get a cited Scope 1
or Scope 2 figure, with every line of the derivation shown.

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
results, [TIER1_BLOCK4.md](TIER1_BLOCK4.md) for the agent loop and the API, and
[TIER2_BLOCK1.md](TIER2_BLOCK1.md) for Scope 1 — the two new document kinds, the DESNZ
factors, and the four defects the first eval run found.

## What works today, and what it measures

Three tools, each returning `{status, data, message}`:

| Tool | What it does |
|---|---|
| `extract_from_document` | Establishes what kind of document a page is, then has a vision model transcribe what is printed and returns the measurable activities it carries. It is never asked for a derived figure. A kind it cannot establish is refused, not guessed. |
| `lookup_emission_factor` | Exact SQL (`category`, `unit`, `year`), never similarity search. Returns the factor with publisher, source document, page, scope, method, gas basis and retrieval date. |
| `calculate_emissions` | Plain Python. Takes a list of (activity, factor) pairs and returns each line and the total, with every input used, the derivation written out, and structured disclosure flags. |

### Three document kinds, two scopes

| Document | Activity | Factor | Scope |
|---|---|---|---|
| Electricity bill | kWh | EMA grid emission factor | 2, location-based |
| LPG invoice | kg of gas, computed from the cylinder lines | DESNZ, per tonne | 1, combustion |
| Fuel-card statement | litres **per product** | DESNZ, per litre, diesel and petrol separately | 1, combustion |

Measured by `make eval` on **36 synthetic documents** in the `clean` tier — 18 bills, 12
LPG invoices, 6 fuel-card statements — in two independent fresh runs with identical
results:

| | Count |
|---|---|
| Document kind identified correctly | **36 of 36** |
| Activities recovered, in the right units | **36 of 36** |
| Every scored field correct — electricity bills, 24 fields | **18 of 18** |
| Every scored field correct — LPG invoices, 18 fields | **12 of 12** |
| Every scored field correct — fuel-card statements, 17 fields | **6 of 6** |
| End-to-end kgCO₂e matching the answer key | **36 of 36** |

Two of those counts are worth reading twice. **9 of 36** documents exercise the
factor-year substitution, which is stated on the result rather than applied silently. And
**1 of 36** is an *expected refusal*: an LPG invoice whose supply period is 2024, for which
no DESNZ factor is published at or before that year. Producing no figure is the correct
outcome there, and the eval scores it as a pass — a harness that counted it as a miss would
be rewarding a guess.

What these figures do **not** cover is set out under [Limitations](#limitations) — one
layout per kind, crisp synthetic PDFs, and a small sample.

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

The loop is bounded at 8 turns. On `no_results`, on an error, or when the turns run out,
it reports what is missing and asks — it never assembles a figure to look finished. Three
refusal paths are exercised live and recorded in
[TIER2_BLOCK1.md](TIER2_BLOCK1.md): a document whose kind cannot be established, a file
holding two kinds at once, and an activity year for which no factor is published.

`calculate_emissions` is also all-or-nothing: if any line of a multi-line calculation
cannot be computed, it returns no figure at all. A total over the lines that happened to
work would look like an answer, be smaller than the truth, and carry nothing on its face to
say a line was missing.

The same property holds all the way to the screen: `/chat` returns the structured output of
each `calculate_emissions` alongside the prose, and the page renders the figure from that.
A number a user reads has come from the Python that computed it, not from the model's
transcription of it.

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
make check        # ruff + the full test suite (585 tests)
```

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
[QUIRKS.md](QUIRKS.md).

#### The synthgen v1 baseline

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
printed, defect included, is kept in [TIER2_BLOCK2.md](TIER2_BLOCK2.md).

**What this does not measure.**
- It is one run, so run-to-run variation is unmeasured.
- The held-out layout has not been scored.
- The documents are synthetic.

#### After Tier 2 Block 3: the eval fixed, the normaliser fixed, the holdout scored

Details, commits and evidence: [TIER2_BLOCK3.md](TIER2_BLOCK3.md). Every figure below is a
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

The flow checks are flow checks, not measurements: documents of one layout asked one
question cannot measure accuracy, and the script says so on every line it prints. They
check that the loop finishes, that its figure equals the answer key's, that every factor is
cited, that the disclosures are stated, that no number appears which no tool produced, and
that a document with no published factor is refused rather than guessed.

`make flow-scope1` is **4 of 4 on all six checks** across the three kinds, 15 turns, with
one unit conversion, one two-line total and one honest refusal. `make eval` (36 of 36)
remains the measured number.

`make eval` caches each model reply, keyed on the document, model, DPI and a hash of the
prompt *and* the response schema. A cache hit skips only the model call — every step after
it re-runs against current code, so a fix can be re-measured against the same replies at no
API cost. Changing the prompt or the schema misses the cache by construction.

## Data provenance

No third-party source document is committed to this repo. Emission factors are transcribed
from primary sources with their citation recorded alongside, and every dataset used is
listed in [SOURCES.md](SOURCES.md) with its publisher, URL and retrieval date.

| Factor | Publisher | Licence | Scope | Basis |
|---|---|---|---|---|
| Singapore grid electricity, 2005–2024 | EMA, *Singapore Energy Statistics 2025*, Table 2.4 | — | 2 | CO₂ only |
| LPG, diesel, petrol, 2025–2026 | DESNZ, *GHG Conversion Factors*, sheet `Fuels` | Open Government Licence v3.0 | 1 | CO₂e |

The DESNZ factors are **published for the UK, not Singapore**, and every result that uses
one says so. They are also CO₂e where EMA's is CO₂ only, so a figure combining the two
carries a disclosure that the carbon-dioxide-only line excludes methane and nitrous oxide.
The reasoning behind choosing the 100%-mineral diesel and petrol rows — and the fact that
it rests on an **unverified assumption** about Singapore pump fuel — is recorded in
[SOURCES.md](SOURCES.md) §9.

## Limitations

Stated plainly, because they bound what any number here means:

- All documents processed so far are **synthetic**. None are real customer documents.
- Emission estimates produced here are **not assured** by any third party.
- Electricity figures are **location-based** (average grid intensity), not market-based.
- **The fuel factors are not Singapore factors.** LPG, diesel and petrol use UK DESNZ
  values, flagged as non-Singapore on every affected result. That they are the closest
  available is a judgement, not a measurement.
- **Diesel and petrol use the 100%-mineral rows on an unverified assumption** about the
  biofuel content of Singapore pump fuel. NEA's own fuel specification has not been read.
- The Block 1 figures (36 of 36) describe **one layout per kind, crisp PDFs, no quirks**. The
  synthgen v1 figures cover scans, photos and quirks, but each is **one run**, on synthetic
  documents.
- **A CO2-only electricity figure is labelled "kg CO2e".** `calculate_emissions` names its
  unit "kg CO2e" whatever the factor's gas basis. Mixing CO2 and CO2e lines is disclosed;
  a figure that is CO2 only is not labelled as such.
- **In synthgen v1, a hard LPG or fuel document is harder to read, not harder to
  interpret**: their injected quirks are all money decoys, and every hard one carries the
  same three, so per-quirk results on them are confounded. See TIER2_BLOCK2.md.
- **Two runs is a small sample.** Every defect found in this block appeared in the first
  live run and none in a test, which is a reminder that a clean run is weak evidence of
  correctness, not proof of it.
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
  db.py        connection helper
  main.py      FastAPI app: the page, /health, /upload, /chat, /chat/stream
  money.py     one definition of "round to cents", shared by generator and extraction
  models/      Pydantic domain models: answer keys, extraction targets, activities,
               factor citations, and the document-kind classification
  xlsx.py      a read-only xlsx reader, for transcribing publishers' workbooks
  synthgen/    the synthetic-document generator (answer first, then the document)
  tools/       the tools the agent is allowed to call
  agent/       the tool-calling loop, the session workspace, the figure audit
  api/         request and response models, upload storage, the SSE stream
  static/      the page: one HTML file, its stylesheet, its script, and a spring
db/migrations/ schema, applied in filename order
scripts/       operational scripts: factor loading, document generation, the eval, the flow check
tests/         437 tests, every one of them offline: no .env, no database, no API key
data/uploads/  documents uploaded at runtime — git-ignored, never committed
data/raw/      source documents and publishers' workbooks — git-ignored, never committed
data/generated/ synthetic documents, answer keys, eval runs — git-ignored
```
