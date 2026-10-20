# Tier 2 Block 2 — layouts, quirks, difficulty tiers

Written for someone who was not there: the design decisions and their reasons, the defects
found and what caught them, and the evidence behind every number. Environment details and
career notes live in the local build log, not here.

**Status:** built at `a77f601`; documented at `719b840` (`QUIRKS.md`); **baseline measured
at `719b840`**, a clean tree, on 20 Oct 2026. The baseline is recorded below exactly as run,
with nothing fixed in between, together with one defect the run exposed in the eval itself.

## What this block had to change

Block 1 left 36 documents: one layout per kind, all `clean`, no injected irregularities.
Its eval (36 of 36) says the pipeline reads *those* pages and nothing more. This block
builds the set that Block 3's eval measures on:

- four layouts per kind, one of them held out;
- the quirk catalogue;
- degraded `moderate` and `hard` tiers;
- 198 documents;
- pro-rating across a year boundary, so a bill spanning 31 December takes each year's
  factor.

## The decisions, and why

### 1. Pro-rating is enforced by the calculation, not suggested to the model

`prorate.split_by_year` divides a bill's kWh between calendar years by days. Every share
but the last is rounded half up to a whole kWh, and the last share takes the remainder, so
the parts sum exactly to the whole. Two things are built on it:

- **Each activity now carries the year it belongs to** (`Activity.factor_year`). A bill
  across 31 December is two activities, each recording its share of the period and the
  division (`"9,000 kWh x 16 of 31 days in 2023 = 4,645 kWh"`).
- **`calculate_emissions` refuses a factor looked up for another year.** Asking for 2025
  and receiving 2024 is a disclosed substitution and is allowed. Asking for the wrong year
  in the first place is an error, and the whole call returns no figure. Without this
  check, applying one year's factor to both halves of a split bill would produce a figure
  that is plausible, cited and wrong.

The agent's hint now gives a year beside each activity handle, and its prompt gained one
sentence: one factor lookup per distinct year. Before this block, the agent was given a
single "activity year" taken from `period_start`, which would have sent both halves of a
split bill to one factor.

**Deviation from spec 5.2:** the split is by **year**, not by month. Factors are annual,
so a monthly split would add lines and change no figure.

**The witness problem, and how it is handled.** Synthgen writes the split into the key and
the pipeline splits what it reads, and both call `split_by_year`. A shared function cannot
be its own witness (spec 1.3). So its tests assert literals produced by a separate,
integer-only scratch script that never calls it. For example, 4,296 kWh over 15 Dec 2025 –
14 Jan 2026 splits as `{2025: 2356, 2026: 1940}`. The half-up case checks that 1 kWh over
two days goes to the first year.

### 2. Structural and injected quirks are different things

The catalogue lives in `models/quirks.py`, and every answer key validates its `quirks`
against it in both directions: a quirk named without its fields, or fields present without
the quirk, is an unconstructible key. There are two classes:

- **Structural quirks** describe what a document *is*. An LPG invoice that prints only
  cylinder counts is `lpg_cylinders`; a statement for a fleet burning two fuels is
  `mixed_fuel`. Both are recorded whenever true, **in every tier**.
- **Injected quirks** are placed by the generator, and only they set a document's tier:
  clean 0, moderate 1–2, hard 3 or more.

The spec said "clean: no quirks". Taken literally, that would forbid a clean
two-product fuel statement, so the deviation is recorded in the spec.

### 3. What "hard" means is different per kind — read Block 3's tiers this way

For **LPG and fuel, every injected quirk is a money decoy**: a credit for an earlier
period, a balance brought forward, or GST shown inclusive. The traps that change those
kinds' *activity* — cylinder arithmetic, two fuels needing two factors — are structural
and appear in every tier.

**A hard LPG or fuel document is therefore harder to read (a degraded image, extra money
lines), not harder to interpret.** Only electricity's hard tier adds interpretation traps:
a year boundary, MWh, an estimated reading, a mid-month cycle.

A second, related limit: LPG and fuel have exactly three injected quirks, and their hard
tier takes three, so **every hard LPG or fuel document carries the same three**. A
hard-tier failure there cannot be attributed to one quirk, because the per-quirk results
are confounded. `test_hard_lpg_and_fuel_documents_carry_all_three_injected_quirks` keeps
this visible in the suite.

### 4. Credits: a decoy quantity, never a change to this period's activity

Every credit names a **prior** period. About half print a quantity — kWh for an
over-estimated reading, kg for an unused cylinder, litres for a reversed duplicate fuel
transaction — and that quantity is a decoy. Subtracting it from this period double-counts;
this period's activity is unchanged.

**Restating the prior period is not modelled.** That would need a negative activity
attributed to an earlier year, and this block does not attempt it (see known limits).

### 5. The set is planned before it is built, and the plan is checked before rendering

`synthgen/plan.py` declares every document's account and period as literal windows, then
assigns its dress by fixed rules:

- layout cycles a→d and tier cycles clean→moderate→hard over each kind's series, in
  document-number order;
- injected quirks are filled least-used first, counting non-holdout documents only.

`make docs-plan` prints the per-quirk × tier table and writes nothing. `make docs` runs
the same checks before it renders anything:

- every injected quirk reaches **15 non-holdout documents per kind**;
- every activity year resolves against the factor years the table holds;
- nothing is dated after September 2026.

**Appending a profile changes nothing that exists.** Assignment visits documents in number
order, and least-used-first looks only at earlier documents. A profile appended at the
end, numbered after everything else, therefore cannot move any existing document's
layout, tier or quirks. A test appends a dummy profile to each kind and proves it.

Standalone bills carry the two period quirks:

- **15 cross-year bills**, over the 2022/23, 2023/24, 2024/25 and 2025/26 crossings
  (4/4/4/3) and all seven accounts;
- **15 mid-month cycles**, in 2022–2023.

They are never on the holdout layout and never clean, and `cross_month_period` and
`cross_year_period` never share a document (the validator makes them mutually exclusive).

### 6. Realism fixes from the Block 1 spot check

These deliberately change documents. The Block 1 set on disk was **not** regenerated:
synthgen v1 writes to `data/generated/synthgen_v1/`, so Block 1's recorded eval stays
reproducible from its own files.

| Finding | Change |
|---|---|
| A dental clinic, on an upper floor, buying LPG | Replaced as an LPG customer by a new ground-floor **Tampines Catering Kitchen**. It takes the 12.7 kg cylinder on a side station beside 50 kg mains, so the fractional-kg trap survives. Test: every LPG customer is ground floor |
| `IGS-COM-26` printed on a December 2024 invoice; prices identical 2024–2026 | One LPG price list per year (`IGS-COM-24/25/26`) with its own prices, chosen by the supply period's year. Fuel's monthly pump table is split into `SFC-PUMP-25/26` with the prices unchanged |
| **Electricity had the same flaw** — found while fixing the LPG one, not in the spot check | Tariff plans by year (`SPR-LT-FIX-22` … `-26`, `SPR-HT-FIX-22` … `-26`), chosen by the period's year, so a 2022 bill never prints "Business Fixed 24". Every Block 1 bill was already on its own year's plan, so the three existing plans kept their rates and `elec_001…018` are unchanged |
| Only 6 fuel-card statements | 54, with three new fleets appended (two of them mixed) and a per-contract rebate (0.05, 0.08, 0.04). The two Block 1 fleets are **pinned at the old 0.06**, so `fuel_001…006` keep every amount |
| One cylinder count every month per account | Per-delivery invoices (print works), an occasional top-up visit (Kopi Corner, Lion City Laundry), and mixed sizes (catering). Test: no LPG account has one kg figure on every invoice |

### 7. Degradation is Pillow only

- **Moderate:** half the documents stay crisp PDFs; the other half are "scans" (slight
  rotation, blur, uneven light, JPEG).
- **Hard:** "phone photos" (keystone, a shadow gradient, rotation, blur, downscale, JPEG).

Every parameter is drawn from the document's own labelled stream and written into the
key's `degradation` before rendering. The same parameters on the same pixels give the same
output pixels (tested).

### 8. The eval reads the new set without being tuned on it

- **Discovery** is through each manifest's `file` (a PNG for a degraded document).
- **The holdout is excluded by default**, even when a held-out document is named.
- **Activities are compared by (kind, year)**, so a cross-year bill read as one year is a
  miss even with the right kWh.
- **Run record:** every run records the git commit and a dirty flag, the seed and
  generator versions, the model and the request hash.
- **Flow check:** `agent_flow_check.py` refuses a held-out document by name.

Extraction gained one nullable printed field, `credit_adjustment`, so that a credited page
does not raise a false "subtotal doesn't close" flag. **No other prompt or instruction was
changed before the baseline**, apart from the agent's one-factor-per-year sentence, which
the pro-rating requires.

## Defects found, and what caught them

| Defect | Caught by | Fix |
|---|---|---|
| **The page cache was keyed by file name only.** A regenerated `lpg_007.pdf` — now a different customer's invoice — would have been read from Block 1's cached page image | Reading `rasterise` before the baseline | Cache keyed by a hash of the file's bytes as well. Regression test with a fake `pdftoppm`. No recorded run was affected |
| On the wide label/value layout `b`, a 2–3° tilt put each right-hand value a **whole row** below its label ("Units used" beside the closing reading), so the page had no fair answer (spec 6.2) | Looking at `elec_089` | Rotation ranges narrowed twice: photo 1–3° → 0.3–1.0°, scan 0.5–2° → 0.2–0.8°. Ranges tuned, never a key |
| On LPG layout `a`, adding the delivery date squeezed the columns, so "12" cylinders and "50 kg" printed as **"1250 kg"**, a false quantity | Looking at `lpg_029` during the hand-check | Date moved to its own line; number cells padded and `nowrap` |
| Fuel layout `a` printed "Fleet fuel purchases" twice (present since Block 1) | Editing the template | Duplicate line removed |
| `uv run` without the Makefile's environment created a stray `.venv` | Noticed immediately | Removed; commands run through `make` or with `UV_PROJECT_ENVIRONMENT` set |

**Found, deliberately not fixed:** statement and invoice numbers take the last four
characters of the account number, giving `SFC-202607-7-01` and `IGS-202505-1-07`. This is
harmless text, but fixing it would change the Block 1 fuel documents' printed numbers and
break the "unchanged except the price-list code" claim. It is logged here instead.

**Predicted before the baseline, not fixed, to be confirmed or refuted by it:**
`rental_charge_sgd` should miss on LPG layouts `b` ("Cylinder hire") and `c` ("CYL
RENTAL"). The normaliser finds the rental row by the label prefixes "cylinder rental" and
"rental", and neither layout's wording starts with either.

## Test assertions changed, and why

Each failing Block 1 assertion was rewritten to state the v1 fact. None was deleted or
loosened.

**`tests/test_synthgen_scope1.py`** (the six that failed):

| Test | Was | Now | Reason |
|---|---|---|---|
| `test_adding_a_customer_does_not_change_any_existing_document` | The newcomer was built with `price_list_code="IGS-COM-26"` | Built with no price list; additionally asserts the extended set is exactly 3 longer | A customer no longer owns a price list — the list is chosen by the supply period's year |
| `test_the_rendered_lpg_page_shows_the_cylinders_but_not_the_kg_total` | The dental clinic's single 12.7 kg line; asserted "12.7 kg" printed and 50.8 not printed | `lpg_007` is Tampines Catering Kitchen with 50 kg and 12.7 kg lines; asserts both lines' size and count cells are printed, the 188.1 kg total is not, `quirks == ["lpg_cylinders"]`, and template `a` | The clinic was replaced; two lines to add is the harder reading |
| `test_prices_belong_to_the_vendor_so_two_customers_pay_the_same` | `FUEL_CUSTOMERS[0].price_list is FUEL_CUSTOMERS[1].price_list` | Every v1 statement names its period year's list (`SFC-PUMP-YY`) and pays that list's price for each transaction; every list holds only its own year's months; petrol above diesel still holds. A companion test checks LPG the same way | Price lists are per year, not per customer |
| `test_a_month_with_no_published_price_is_refused_rather_than_substituted` | Error contains "no prices for 2099-01" | Error contains "No fuel price list SFC-PUMP-99"; a new companion test asserts a missing month inside an existing year is still refused ("no prices for 2024-01") | The year's list is looked up first, so a year with no list is refused by name before the month is |
| `test_identities_are_shared_with_the_electricity_profiles` | Checked over the Block 1 set | Checked over the whole v1 set | The three new fleets have no Block 1 statements, so the Block 1 set would leave them untested |
| `test_no_document_carries_a_quirk_in_this_block` → `test_block1_documents_carry_only_their_structural_quirks` | No document carries any quirk | Every Block 1 LPG invoice is exactly `["lpg_cylinders"]`; a fuel statement is `["mixed_fuel"]` exactly when it has two products (and one does); all are clean | Structural quirks are now recorded in every tier; Block 1 documents still carry no injected quirk |

**Elsewhere**, changed for the same kind of reason:

- `test_extraction_fuel.py::test_the_discount_relation_is_checked`: the relation name
  became `subtotal = gross_amount - discount - credit_adjustment`. A companion test checks
  that a printed credit closes the subtotal and is stored as a magnitude.
- `test_electricity_bill_model.py::test_unknown_fields_are_rejected`: it used
  `amount_due_sgd` as its example of an unknown field, which is now a real field. It now
  uses `monthly_split_kwh`.
- `conftest.py`: the fixture activities gained `factor_year` and their period, matching
  what extraction now returns.

## Evidence

### The set

```
$ make docs-plan        (writes nothing)
electricity_bill   93 documents (82 non-holdout)  block1 18, series 45, cross_year 15, cross_month 15
lpg_invoice        51 documents (42 non-holdout)  block1 12, series 39
fuel_card_statement 54 documents (42 non-holdout)  block1 6, series 48
total             198 documents

Injected quirks, NON-HOLDOUT documents (floor 15 per kind):
  kind                 quirk                moderate  hard  total
  electricity_bill     estimated_reading           7    17     24
  electricity_bill     cross_month_period          7     8     15
  electricity_bill     cross_year_period           8     7     15
  electricity_bill     unit_mwh                    6    18     24
  electricity_bill     credit_adjustment           5    19     24
  electricity_bill     balance_forward             4    20     24
  electricity_bill     gst_confusion               4    19     23
  lpg_invoice          credit_adjustment           6    10     16
  lpg_invoice          balance_forward             5    10     15
  lpg_invoice          gst_confusion               5    10     15
  fuel_card_statement  credit_adjustment           7    12     19
  fuel_card_statement  balance_forward             7    12     19
  fuel_card_statement  gst_confusion               6    12     18
Structural: lpg_cylinders 32 (19 clean, 6 moderate, 7 hard); mixed_fuel 24 (11/6/7)
Cross-year bills by crossing: 2022/23 4, 2023/24 4, 2024/25 4, 2025/26 3
Expected refusals (no factor by design): lpg_001

$ make docs
arithmetic closes to the cent on 93 of 93 / 51 of 51 / 54 of 54
```

### Factor years confirmed before generation

The factor table was queried before any cross-year bill was designed: `electricity_grid_sg`
2005–2024 (2022 = 0.417, 2023 = 0.412, 2024 = 0.402), and `fuel_*` 2025–2026. A
database-backed test asserts that the planner's declared years match the table; it passes
with `DATABASE_URL` set and skips without it.

### Stability against the untouched Block 1 tree

```
electricity_bill:    18 keys -> fields differing: none
fuel_card_statement:  6 keys -> price_list_code (2, the 2025 statements), quirks (3, mixed_fuel)
lpg_invoice:         12 keys -> changed by design (catering, per-year prices, dated visits)
```

`fuel_002` still reads gross 773.22, rebate 15.49 and total 825.93, the figures
hand-checked in Block 1. A test now pins them.

### Hand-checks, none on the held-out layout

Each was recomputed with a calculator from the figures printed on the page, then compared
with the key.

| Document | What it exercises | By hand | Key |
|---|---|---|---|
| `elec_064` (layout `a`, moderate) | MWh, across 31 Dec 2022 | 289,637 − 285,727 = 3,910 kWh; 3,910 × 31.42/100 = 1,228.52; + 11.00 = 1,239.52; GST 111.56; total **1,351.08**. Split 15 + 16 of 31 days: **1,892 (2022) / 2,018 (2023)** | identical |
| `lpg_029` (layout `a`, moderate) | balance forward, a 50 kg credit | 12 × 158.50 = 1,902.00; + 9.00 + 60.00 − 158.50 = 1,812.50; GST 163.13; total **1,975.63**; due 2,045.17 − 2,045.17 + 1,975.63 = **1,975.63**; gas **600 kg** (the 50 kg credit is not subtracted) | identical |
| `fuel_012` (layout `b`, hard — read off the photo) | degraded image, credit, balance | four fills 97.33 / 153.29 / 153.05 / 111.39; diesel **101.10 L**, petrol **65.02 L**; gross 515.06; rebate 166.12 × 0.06 = 9.97; credit 5.00; subtotal 500.09; GST 45.01; total **545.10** | identical |

### Readability

The following were inspected by eye:
- every hard bill on electricity layout `b`;
- hard LPG layout `c` and hard fuel layout `b`;
- the two hand-check pages.

The checks found two defects in this block's own rendering, both listed above, and both
fixed before this commit.

### Suite

```
$ make check
ruff check .           All checks passed!
ruff format --check .  89 files already formatted
pytest -q              521 passed, 1 skipped   (437 at the end of Block 1)
```

The skip is the database-backed factor-years test, which passes with `DATABASE_URL` set.

## The baseline

One fresh run, holdout excluded, nothing changed before or after it.

```
$ make eval
Carbon R extraction eval - 166 documents from data/generated/synthgen_v1
  commit 719b840  seed [20261016]  holdout excluded            (git tree clean)
  model gpt-5.4-mini-2026-03-17  dpi 150  detail high  request 1788ce3e
  0 cached, 166 fresh

  document kind correct   166 of 166
  activity recovered      166 of 166
  pipeline kgCO2e exact   166 of 166
  factor-year substituted  40 of 166
  expected refusals         1 of 166   (lpg_001: no LPG factor for 2024, correctly no figure)

  every field correct, as printed:  electricity 47 of 82 · LPG 0 of 42 · fuel 18 of 42
  field failures, as printed:       182
Run: data/generated/eval_runs_TIER2_BLOCK2/20261020T082120+0000-1788ce3e.json
```

### The headline: every emissions figure is right, on every tier and every quirk

**166 of 166 documents produced the answer key's kgCO2e to the last gram**: clean 69 of 69,
moderate 48 of 48, hard 49 of 49 (49 phone photos and 28 scans among them). That holds
on every quirk:

- all 15 cross-year bills were split into the right two years by days;
- all 24 MWh bills were converted to the right kWh;
- no credit's decoy quantity was ever subtracted from an activity;
- no "amount due" was taken for a period total;
- no GST-inclusive headline reached a calculation.

**What it means.** The traps that change an emissions figure — reading the activity, its
unit, its year, and which lines count — were all read and interpreted correctly on this set.

**What it does not mean.**
- It does not mean every *field* was read right; the next section is about the fields.
- It is one run: run-to-run variation is not measured.
- The documents are synthetic.
- The holdout layout has not been scored.

### A defect in the eval, found by the run: the credit column scored nothing

The `credit_adjustment_sgd` column (electricity 58 of 82, LPG 26 of 42, fuel 24 of 42 as
printed) is **invalid**. The eval looked the field up on the answer key as
`credit_adjustment_sgd`; the key calls it `credit_amount_sgd`. "Expected" was therefore
always empty, so every document that carried a credit failed and every one without a credit
passed, whatever was read.

The rule that scores an absent credit as zero is what kept this quiet: it turned the
missing key field into 0 rather than raising an error. It was introduced in this block, with
the eval plumbing, and no test compared the eval's field names against the key's.

**Re-scored without new model calls.** The run file holds every reading. Comparing each
recorded reading against the key's `credit_amount_sgd` gives **credit read correctly on 165
of 166**. The one miss is `fuel_039`, whose whole money summary was misread; see the
misreads below.

The harness is **not** fixed in this commit. Fixing it is Block 3's first change, and it is
re-scorable from the reply cache at no API cost.

With the credit column re-scored, **125 field failures remain**:

| Kind | clean | moderate | hard |
|---|---|---|---|
| Electricity, every field correct | 29 of 29 | 20 of 26 | 8 of 27 |
| LPG, every field correct | 0 of 22 | 0 of 10 | 0 of 10 |
| Fuel card, every field correct | 14 of 18 | 11 of 12 | 2 of 12 |

### Where the 125 failures come from

Each failure was traced to the recorded reply. **96 of the 125 are in Python normalisation,
not in what the model read.**

| Cause | Field failures | Documents | Evidence |
|---|---|---|---|
| **Meter readings on MWh bills are not converted.** The model transcribes "285.727 MWh"; `normalise` converts the *consumption* by its unit but parses the *readings* as whole kWh, fails, and reports them unparsed | 48 | 24 MWh bills: readings **0 of 24**, against 58 of 58 on kWh bills; consumption on the same bills 24 of 24 | `unparsed elec_064: previous_reading_kwh <- '285.727 MWh'` |
| **A charge line is dropped when its quantity is a word.** LPG layout `a` now prints "1 visit" / "2 visits" in the count column; the model transcribes it exactly, the quantity fails to parse, and the whole row is discarded, taking the delivery charge with it | 25 | all **22 of 22** layout-`a` LPG invoices, plus 3 layout-`b` ("1 trip(s)") | `lpg_001 lines[3]: quantity '2 visits', amount '16.00'` dropped; subtotal check short by exactly 16.00 |
| **The rental row's label is not recognised** — the prediction made before the run | 20 | **all 20** layout-`b` ("Cylinder hire") and layout-`c` ("CYL RENTAL") invoices, and no others | **Prediction confirmed exactly** |
| The inline label "UEN" is stripped from electricity readings but not from LPG or fuel | 3 | `fuel_009`, `fuel_051`, `lpg_039` | got `'UEN SYN-0000-0003'` |
| **Two fields under one label**, by layout design: `fuel_b` prints "Account / card" on one row, `fuel_c` "CARDHOLDER name, address" | 8 | `fuel_028`, `fuel_032`, `fuel_048` (account and card); `fuel_033` (name and address) | the reading holds both values in one field |
| **The model "corrected" an odd identifier**: `SFC-202607-7-01` read as `SFC-202607-01` | 3 | `fuel_012`, `fuel_021`, `fuel_040` | the cosmetic identifier oddity logged in Block 2 is now a measured cost |
| **Genuine misreads** | 18 | 12 documents: 9 hard, 1 moderate, 2 clean | below |

**The genuine misreads, by tier:**
- **hard, 15 fields in 9 documents:**
  - two addresses (`Tempua` → `Tempeua`; `523169` → `52369`);
  - three card numbers (`6677` → `667?`, a dropped `*`);
  - a UEN (`SYN-0000-0002` → `SYN-000-0002`);
  - three fuel money summaries: `fuel_024` gross unread, `fuel_036` gross 2,047.49 for 1,937.60, and `fuel_039` read wrong throughout its summary, with total 4,787.19 for 3,023.14.
- **moderate, 1:** `lpg_035`'s address truncated.
- **clean, 2:** `fuel_025` `Tempua` → `Tempa`; `fuel_052` `523169` → `52369`.

The postal code `523169` was misread three times across three kinds and tiers.

**Per tier:** genuine misreads touch 9 of 49 hard documents, 1 of 48 moderate, and 2 of 69
clean. They concentrate on fuel statements, the densest pages: 6 of the 9 hard
documents with a misread are fuel cards. Every scored misread is in a money or identity
field.

**None touched a quantity an activity is computed from**, which is why the pipeline figure
survived them. One unscored printed figure was a quantity: `fuel_048`'s per-product petrol
total, which the consistency check flagged ("litres[petrol] = sum(transaction litres) off by
21459.24"). The activity is summed from the transaction rows, which were read correctly, so
that check did its job by catching the disagreement.

### End to end

```
$ make flow-block2          # elec_068, elec_056, lpg_029, fuel_012 - none on the holdout
  answered / figure_matches / cited / disclosed / no_stray_numbers / refused_honestly   4 of 4 each
  16 turns · 1 substitution · 2 non-Singapore · 1 unit conversion · 2 multi-line figures
  elec_068  elec_b moderate  factor years [2023, 2024]  3553.628 kg
  elec_056  elec_b moderate  factor years [2024]        2839.728 kg
  lpg_029   lpg_a  moderate  factor years [2025]        1763.617 kg
  fuel_012  fuel_b hard      factor years [2026]         422.122 kg
```

Each figure was re-derived by an independent integer route and matches to the last gram:

```
elec_068  5060 x 0.412 + 3654 x 0.402         = 2084.720 + 1468.908 = 3553.628
elec_056  7064 x 0.402  (2026 -> 2024, disclosed)                   = 2839.728
lpg_029   600 kg = 0.6 t x 2939.36095                               = 1763.61657
fuel_012  101.10 x 2.66155 + 65.02 x 2.35372  = 269.0827 + 153.0389 = 422.1215794
```

`elec_068` is the demonstration this block was built for. The agent made two factor
lookups, and its reply reads: "5,060 kWh × 0.412 … 3,654 kWh × 0.402 … The period crosses a
year boundary, so the document was split into 2023 and 2024 activities". It cited both EMA
years. `lpg_029` carried a 50 kg credit and a balance forward, and its figure is on the
600 kg delivered.

**One labelling flaw seen in the replies, present since Tier 1:** a figure computed only
from EMA's CO2-only factor is still labelled "kg CO2e", because `calculate_emissions` names
its unit "kg CO2e" whatever the gas basis. The mixed-basis disclosure exists; a single-basis
CO2 label does not. It is recorded, not fixed.

### What the baseline says Block 3 should do first

Fix the measurement before anything it measures, then one normaliser change at a time, each
re-scored from the reply cache so that each change's effect is isolated:

1. the eval's credit field name (a measurement fix, so no pipeline change);
2. MWh meter readings, the largest single cause at 48 failures;
3. a charge line kept when its quantity is a word;
4. the rental label;
5. the inline "UEN" label on LPG and fuel.

The holdout is scored once, separately, only after these.

## Known limits of this set

- **Hard LPG and fuel are harder to read, not to interpret**, and their per-quirk results
  are confounded (section 3).
- **Thin cells.** Four quirk × kind cells sit exactly at the floor of 15, and several
  moderate cells hold only 4–7 documents. Block 3's per-quirk-per-tier figures will be
  small counts and must be reported as such.
- **`balance_forward` bites only on partial payment.** When the previous balance was paid
  in full (about 60% of cases), "amount due" equals this period's total.
- **Prior-period credits are not restated.**
- **Meter readings are not continuous** between an account's separate windows (standalone
  bills take a reading wound back from the account's opening read).
- **Pillow-only degradation:** no paper texture or ink bleed.
- **LPG quantisation is softened, not removed:** Kopi Corner's monthly invoices still land
  mostly on 250 kg.
- **Identifier oddity** (`SFC-…-7-01`) left in place, as above.
- **Everything is synthetic.** These documents describe modelled formats, not real
  Singapore bills.
