# Tier 1 Block 3 — extraction, factor lookup, calculation

Dated 17 Oct 2026. This is the block that closed the loop between the two halves built
earlier: a verified factor table and 18 synthetic bills that each ship with a
generator-produced answer key. Before it, nothing read a document and there was no eval,
so the project had no measured claim at all.

```
synthgen   :  answer   ──►  document      (Block 2)
extraction :  document ──►  answer        (this block)
eval       :  do the two answers match?   (this block — the first number)
```

Outcome: three tools returning `{status, data, message}`, and an eval that scores both
per-field extraction and end-to-end kgCO₂e.

---

## The measurement

On the 18-document `clean` tier, 24 scored fields per document:

| | Result |
|---|---|
| Every field correct | **18 of 18**, in all three post-fix `--no-cache` runs |
| Pipeline kgCO₂e exact | **18 of 18**, in every one of the 12 recorded runs |
| Factor-year substituted | **9 of 18** (2025 activity periods; newest EMA factor is 2024) |
| Tests | **139 passing** (`make check`), 91 of them new in this block |

Across all runs: 12 records, 200 document-scorings, 144 model calls.

### What this number does not say

- **One layout, one vendor, crisp synthetic PDFs, no quirks.** It says the pipeline reads
  *these modelled formats*. It says nothing yet about scans, phone photographs, MWh units,
  cross-month periods or credit adjustments.
- **Three fresh runs is a small sample.** Both defects found below appeared in exactly one
  run out of three, so a defect at that frequency is about as likely to be missed as
  caught. A clean three-run range is weak evidence of determinism, not proof of it.
- **No holdout layout exists.** v0 ships a single layout, so there is no honest
  unseen-layout figure to quote. Tier 2 Block 2 adds 3–4 layouts and holds one out.
- **The pipeline figure never moved**, in any run — including the two runs with a field
  miss, because both misses were identity fields (`vendor_uen`, `tariff_plan_*`) that no
  calculation consumes. The *figures* — consumption, period, rate, totals — were stable
  throughout. That is the honest reading of the stability, and also its limit: it is not
  evidence that text fields are reliable.

---

## Architecture

```
document (PDF)
   │  pdftoppm -r 150 -png                     page image, cached
   ▼
extract_from_document ──► ExtractedElectricityBill   printed strings, verbatim
   │                      (no Decimal, no date, no validator, no totals requested)
   │  normalise()         plain Python: separators, dates, MWh→kWh, Decimal
   ▼
NormalisedBill + [FieldProblem] + [ConsistencyFlag]
   │
   │  lookup_emission_factor(category, unit, activity_year)   exact SQL
   ▼
FactorCitation ──► calculate_emissions ──► kgCO₂e + every input used + disclosure flags
```

### The central design point

`ElectricityBill` is an **answer key** — frozen, `extra="forbid"`, with a validator that
re-derives every arithmetic relation. The checklist said extraction should return one. It
must not:

1. Handed a bill with one misread figure the validator **raises**, turning a
   23-of-24-fields result into a total failure and destroying the per-field accuracy that
   makes an eval diagnostic rather than merely discouraging.
2. It carries `generator_seed`, `layout_id`, `difficulty_tier` and `degradation`, none of
   which are on the page.

So extraction has its own model: every printed field as `str | None`, no validator, and no
field that could only be produced by multiplying. This is the "LLM never does arithmetic"
rule expressed as types rather than as a prompt instruction — there is no total to ask for.
It also mirrors the spec's `printed` vs `truth` split: a bad *read* and a bad
*interpretation* fail separately and are reported separately.

One unparseable field yields one `FieldProblem`, never an exception. One miss costs one
field, not the document.

---

## Files added

| Path | Lines | Purpose |
|---|---|---|
| `src/carbon_r/tools/base.py` | 57 | `ToolResult` + `ok`/`no_results`/`error`; one definition for every tool |
| `src/carbon_r/models/factors.py` | 51 | `FactorCitation` — a factor that cannot be constructed without provenance |
| `src/carbon_r/models/extraction.py` | 518 | Printed-strings model, typed counterpart, `normalise`, `check_consistency` |
| `src/carbon_r/tools/extract.py` | 269 | Rasterise → vision model → validated reading; the request hash |
| `src/carbon_r/tools/factors.py` | 198 | Exact SQL, the fallback-year rule, full citation |
| `src/carbon_r/tools/calculate.py` | 145 | The only arithmetic in the application |
| `scripts/eval_extraction.py` | 358 | The eval: per-field + end-to-end, with a reply cache |

Tests, all of which pass with no `.env`, no database and no API key:

| Path | Tests |
|---|---|
| `tests/test_extraction_normalise.py` | 57 — includes a renderer round-trip over all 18 documents |
| `tests/test_calculate_emissions.py` | 15 — against hand-computed literals |
| `tests/test_factor_lookup.py` | 12 — the whole year rule offline, plus one live-table test |
| `tests/test_extract_cache_key.py` | 7 — the key must move when the request moves |

`make eval` added to the `Makefile`.

---

## Decisions

### Documents reach the model as page images, never as PDFs

`pdftoppm -r 150 -png`. A PDF handed to the provider may be read through its text layer,
which these synthetic documents have perfectly and real scans do not — the accuracy figure
would flatter itself and would not transfer. Tier 2's `moderate`/`hard` tiers are PNGs and
use this same path unchanged.

### `MAX_FACTOR_YEAR_GAP = 2`, sized to the publication lag

The first reasoning for a cap of 1 was wrong *in kind*: it argued that the EMA series is
contiguous 2005–2024 so no gap needs a 2-year reach — but that is about holes **inside**
the series. The binding gap is at the **end**. EMA publishes in arrears (SES 2025 carries
the 2024 factor), so the newest available year always trails the current activity year.

Measured against the live table (`max(year) = 2024`):

| Activity year | Gap | Result |
|---|---|---|
| 2024 | 0 | `ok`, `year_used=2024`, `substituted=false` |
| 2025 | 1 | `ok`, `year_used=2024`, `substituted=true` |
| 2026 | 2 | `ok`, `year_used=2024`, `substituted=true` |
| 2027 | 3 | `no_results` — gap exceeds cap |
| 2004 | — | `no_results` — nothing published at or before (a distinct message) |

A cap of 1 would have returned `no_results` for every current-year bill, which Tier 2
Block 3's company-year scenario needs. A fallback **always** sets `substituted=true` with a
note naming both years; anything past the cap is `no_results`, never a quiet substitution.

### Disclosures are structured flags, not prose

`calculate_emissions` returns `estimated_reading`, `factor_year_substituted`,
`non_singapore_factor`, `factor_unverified`, `not_assured`. Block 4's system prompt renders
them into sentences, so the wording lives in one place and the tool stays testable by
equality rather than by string matching.

### The reply cache is keyed on the instruction *and* the JSON schema

`responses.parse` sends the schema with every request, so editing one field's
`description` changes what the model is asked for. A cache keyed on the prompt alone would
replay stale replies after a schema edit — the fix would appear to do nothing — and a
partially populated cache would blend two requests into one score.

```python
REQUEST_HASH = sha256(EXTRACTION_INSTRUCTION + json.dumps(schema, sort_keys=True))[:8]
# cache key: {doc_id}-{model}-{dpi}dpi-{image_detail}-{REQUEST_HASH}.json
```

- A cache **hit skips only the model call**. `normalise`, `check_consistency`,
  `lookup_emission_factor`, `calculate_emissions` and `compare_fields` always re-run
  against current code, so a code fix is re-measurable against the same replies for free.
- A **changed request misses every key**, so the model is called for every document even
  without `--no-cache`. There is no stale-reply path.
- `--no-cache` forces fresh calls with an *unchanged* request, to measure run-to-run
  variation.
- The eval prints the cache split and says `MIXED RUN: n cached, m fresh — not a clean
  measurement` when a run blends the two.

Note: `model_json_schema()` includes the model class's docstring, so a prose-only docstring
edit changes the hash and costs a full re-run. Correct, since that prose is sent to the
provider — but worth knowing before rewording a comment.

### On reproducibility

The pinned model snapshot keeps the model *version* fixed; it does **not** make replies
identical across calls. The cache is what makes re-reporting reproducible, and the three
fresh runs are what measure the spread.

---

## Defects found, and by what

Both extraction findings came from the eval rather than from inspection, and both are
layouts conflating things the answer key holds apart. In each case the answer key was
checked first and left alone — the key is right; the page conflates.

**1. Two key fields on one line.** `electricity_bill_a.html.j2` line 73 renders
`{{ tariff_plan_name }} ({{ tariff_plan_code }})` under a single "Tariff plan" label, so a
faithful transcription reads `Industrial Fixed 24 (SPR-HT-FIX-24)` and matches neither key
field. Split in Python. The first version of that split only triggered when the model had
*also* filled `tariff_plan_code` — and the next fresh run broke it by leaving that field
null, so **both** fields scored wrong from one correctly-read string. Now split from the
line alone, with the parenthesised token matched against a narrow plan-code pattern so an
ordinary `(promotional)` is never mistaken for a code.

**2. A field label run together with its value.** Line 47 prints
`UEN {{ vendor_uen }} · {{ vendor_address }}` as one text run, so `vendor_uen` came through
as `UEN SYN-0000-0001`. A known leading label is now stripped from identifier fields.

**3. (mine) The label strip was too greedy.** It did not require a separator after the
label, so `UENITED-1234` became `ITED-1234` — a mangled value delivered with confidence.
Caught by a test written alongside the function, before any eval run saw it. A label must
now be followed by whitespace, a colon or a middot; hyphen is deliberately *not* a
separator, because `UEN-123456` is more plausibly a value than a labelled one.

**4. (mine) Inline units would have failed every money field.** The layout prints
`139,600 kWh` and `27.65 c/kWh`, so `Decimal("139,600 kWh")` raises. `_clean` now strips a
trailing unit from an allowlist, longest-suffix-first so `c/kwh` is not shortened to `kwh`.
Deliberately an allowlist rather than a leading-number regex: a regex would also swallow
`4,016 kWh credit`, turning a Tier 2 quirk into a clean-looking value.

**5. (mine) A test claimed to prove something unprovable.** I wrote
`test_tonnes_derive_from_the_exact_product_not_from_rounded_kilograms`, then found that at
3 decimal places for kg and 6 for tonnes, `round6(x/1000)` *is* `round3(x)/1000` — no input
distinguishes the two routes. Rewritten to assert what is true, with the limitation in the
docstring. A test whose name over-claims is worse than no test.

---

## Verification evidence

Factor lookup against the live table (`uv run python -m carbon_r.tools.factors`):

```text
2024: ok          year_used=2024 substituted=False
     0.402 kg CO2 / kWh (EMA 2024, Singapore Energy Statistics 2025, Table 2.4).
2025: ok          year_used=2024 substituted=True
     ... used the 2024 factor instead (a 1-year substitution). EMA publishes in arrears.
2026: ok          year_used=2024 substituted=True
     ... used the 2024 factor instead (a 2-year substitution). EMA publishes in arrears.
2027: no_results  The newest electricity_grid_sg factor is for 2024, which is 3 years
     before the requested 2027. That exceeds the 2-year limit...
2004: no_results  No electricity_grid_sg factor in 'kg CO2 / kWh' is published for 2004
     or any earlier year. The table holds 2005-2024.
```

Arithmetic, checked by an independent integer route *before* the tests were written
(0.402 kg/kWh is 402 thousandths, so the product is exact in integers):

```text
elec_001: 4016 x 402 = 1614432 /1000 = 1614.432 kg = 1.614432 t
elec_009: 139600 x 402 = 56119200 /1000 = 56119.2 kg = 56.1192 t
```

`calculate_emissions` returns exactly those, with the derivation string
`139,600 kWh x 0.402 kg CO2 / kWh = 56,119.2 kg CO2e`.

Request fingerprint, on the implemented instruction and 26-field schema:

```text
REQUEST_HASH (real instruction + real schema): 0a2390d9
stable on repeat: True
instruction edited       -> f8aa6b64  differs: True
field description edited -> 2bb19e50  differs: True
```

Eval runs in order (`gpt-5.4-mini-2026-03-17`, dpi 150, detail high, request `0a2390d9`):

| # | Run (UTC) | n | cache | every field | pipeline | note |
|---|---|---|---|---|---|---|
| 1 | 08:34 | 2 | 0 cached, 2 fresh | 2 of 2 | 2 of 2 | harness check before spending 18 calls |
| 2 | 08:35 | 18 | 2 cached, 16 fresh | 18 of 18 | 18 of 18 | MIXED warning fired correctly |
| 3 | 08:37 | 18 | 0 cached, 18 fresh | **17 of 18** | 18 of 18 | `elec_009` tariff plan — finding 1 |
| 4 | 08:39 | 18 | 18 cached, 0 fresh | 18 of 18 | 18 of 18 | fix re-measured, **zero API calls** |
| 5 | 08:40 | 18 | 0 cached, 18 fresh | 18 of 18 | 18 of 18 | fresh 1 of 3 |
| 6 | 08:42 | 18 | 0 cached, 18 fresh | 18 of 18 | 18 of 18 | fresh 2 of 3 |
| 7 | 08:43 | 18 | 0 cached, 18 fresh | **17 of 18** | 18 of 18 | `elec_010` vendor_uen — finding 2 |
| 8 | 08:45 | 18 | 18 cached, 0 fresh | 18 of 18 | 18 of 18 | fix re-measured, zero API calls |
| 9 | 08:46 | 18 | 0 cached, 18 fresh | 18 of 18 | 18 of 18 | post-fix fresh 1 of 3 |
| 10 | 08:48 | 18 | 0 cached, 18 fresh | 18 of 18 | 18 of 18 | post-fix fresh 2 of 3 |
| 11 | 08:49 | 18 | 0 cached, 18 fresh | 18 of 18 | 18 of 18 | post-fix fresh 3 of 3 |
| 12 | 09:33 | 18 | 18 cached, 0 fresh | 18 of 18 | 18 of 18 | verifying the directory rename |

Rows 4 and 8 are the cache design paying for itself: each fix was measured against the
identical replies that exposed it, for no API spend.

Run records are kept per document set, in `data/generated/eval_runs_SYNTHGEN_V0/`, because
an accuracy figure is only meaningful against the set it was measured on. Each record
carries the request block and, per document, all 24 field verdicts with expected *and*
received values, both kgCO₂e figures, the requested vs used factor year, unparsed fields,
consistency flags, and whether the reply was cached — so both failures above are still on
disk as evidence, not merely described here.

---

## Carried forward

- **Tier 1 Block 4** is next: tool schemas for the three tools, a tool-calling loop with a
  6–8 turn guard, the system prompt that renders the disclosure flags into sentences,
  `/upload` and `/chat`, end-to-end on 5 bills.
- `check_consistency` flags are computed and reported but **nothing yet acts on them**.
  Block 4 should surface a flagged document to the user rather than answering silently.
- The eval's `--limit`/`--docs` paths have no pytest coverage; `compare_fields` is pure and
  should get a unit test when Block 4 touches the harness.
- Run records live under `data/generated/`, which is git-ignored, so the eval history is
  **local only**. Tier 2 Block 3 wants evals in CI; the records will need a committed home
  outside `data/generated/` for measured accuracy to survive a fresh clone.
- Run records do not carry `generator_version`. Provenance currently lives in the directory
  name alone, so a record copied elsewhere loses it — and the generator's own version
  string (`synthgen-electricity-2`) does not match the directory name, so the two can drift
  without anything complaining. Recording it inside each record would make the file
  self-describing.
- Before any accuracy figure enters `README.md`: three `--no-cache` runs and the recorded
  range, with the tier and layout count stated alongside. Done for this block; redo it
  whenever the request changes.
