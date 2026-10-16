# Carbon R — working rules

Carbon R turns uploaded documents into greenhouse-gas figures that can be audited line by
line. The rules below are what make that claim true. They are not style preferences; a
change that breaks one of them is a defect even if the tests pass.

## The rules that never change

### 1. The language model never does arithmetic

Every multiplication, sum, pro-rating and unit conversion happens in `calculate_emissions`
in plain Python. The model extracts fields and selects tools; it never computes a quantity
that reaches the user.

If you catch yourself writing a prompt that asks for a total, or accepting a number the
model produced rather than one the code computed, stop. The number must be recomputed in
Python from the extracted inputs.

### 2. Every number carries its source

A factor or emission figure is incomplete unless it travels with: **factor, unit,
publisher, year, source document, and the page or table it came from**. There is no code
path that returns a bare float. `calculate_emissions` returns its result *together with
the inputs it used*, so any figure can be re-derived by hand.

### 3. Emission factors come from exact SQL, never from vector search

Factor lookup is `where category = ... and unit = ... and year = ...`. Similarity search
is for *methodology prose* (`search_methodology`) and for *matching a spend description to
a factor category* (`match_factor_category`, where an LLM picks from retrieved candidates
and then SQL fetches the exact value). A factor value itself is never retrieved by
embedding distance.

Corollary: never add a convenience fallback that fetches factors from a public API at
request time. If a factor is missing, that is a `no_results`, not a thing to paper over.

### 4. Answer keys come from the generator or a primary source

Ground truth is produced by `synthgen` alongside the document, or hand-labelled from a
primary document. **Never label from model output**, and never "fix" an answer key to make
an eval pass. If an eval disagrees with a key, the key is checked against the document
first — and if the key was right, the failure is real and stays recorded.

### 5. Anything marked VERIFY is checked against the primary source before it is used

No value with regulatory or numeric significance — emission factors, GST, carbon tax
rates, reporting timelines — enters code, `README.md` or a resume until it has been read
from the publisher's own document. Secondary sources (blogs, aggregators, another model's
recollection) are leads, not citations.

`SOURCES.md` records, per dataset, whether it is `verified from primary` or
`secondary only`. Values still `secondary only` must not be presented as established.

### 6. Never cut: the answer keys, the accuracy check, the deterministic calculation

Under time pressure, cut scope elsewhere — fewer layouts, fewer documents, plainer UI. These
three are the project. A demo without them is a chatbot with a spreadsheet attached.

### 7. Honesty over coverage

When extraction fails, a factor is missing, or a retrieved passage does not answer the
question, the agent **says so and asks**. It never guesses, never interpolates a factor,
and never silently substitutes a neighbouring year without stating the substitution.

Every result states its method (location-based, activity-based), flags estimated readings,
labels non-Singapore factors as such, marks spend-based Scope 3 as indicative with high
uncertainty, and says that estimates are not assured.

## Conventions

- **Python 3.13**, managed with `uv`. The virtualenv is `.venv-carbon-r`, not `.venv` —
  recipes in the `Makefile` export `UV_PROJECT_ENVIRONMENT` for you.
- **Pydantic everywhere** at boundaries: extraction targets, ground truth, tool arguments,
  API requests and responses. If data crosses a boundary untyped, that is a bug.
- **Tools return `{status, data, message}`.** `status` is one of `ok`, `no_results`,
  `error`. Callers branch on `status`; they never infer success from a non-empty `data`.
- **Lint and format with ruff** (`make fmt`, `make check`).
- **Migrations** live in `db/migrations/`, applied in filename order, and are idempotent
  (`create table if not exists`, `on conflict ... do update`). Running a loader twice must
  never duplicate a row.
- **No third-party source document is ever committed.** `data/raw/` is git-ignored.
  Provenance goes in `SOURCES.md`; the bytes stay local. Likewise no API keys, no real
  customer bills, no third-party logos.
- **Claims are measured.** Any accuracy or cost figure in `README.md` comes with a count
  ("37 of 50"), from a run that actually happened.

## Current state

The build log and the dated plan are **local working notes, deliberately not committed**
(see `.gitignore`). They hold the current tier/block, the environment facts worth not
rediscovering, verification evidence, and the traps already hit. If they are present in
the working directory, read the log before debugging anything environmental.

**Keep the log current.** At the end of a working session, add an entry using the template
at the bottom of it, and update its "Current state at a glance" table. Record actual
command output as evidence, counts rather than bare percentages, and any problem that cost
more than ten minutes — including corrections to earlier conclusions.

What belongs in the repo instead: decisions that explain the *code* go in docstrings and
in [SYNTHGEN_V0.md](SYNTHGEN_V0.md); provenance and verification status go in
[SOURCES.md](SOURCES.md).
