# Tier 2 Block 1 — Scope 1 documents and factors

Written for someone who was not there: the design decisions and why, the defects found and
what caught them, and the evidence behind every number. Environment details and career
notes live in the local build log, not here.

## What this block had to change

Tier 1 read one document kind, applied one factor category, in one activity unit, for one
scope. That shape was hardcoded in more places than it looked:

| Assumption | Where it lived |
|---|---|
| the activity is kWh | `ACTIVITY_UNIT = "kWh"`, a module constant in `agent/tools.py` |
| the method is location-based | `METHOD = "location-based"`, a module constant in `tools/calculate.py` |
| a document has one activity | `calculate_emissions(document_id, factor_ref)` |
| the quantity is `consumption_kwh` | read by name out of the reading |
| rendering is for electricity | `render.py` typed to `ElectricityBill` |

Scope 1 breaks all five at once. An LPG invoice measures kg against a factor published per
tonne; a fuel-card statement measures litres of **two different fuels**, needing two factor
rows and a total; and "location-based" is a Scope 2 term that would be a wrong label
printed beside a combustion figure.

## The decisions, and why

### 1. A document yields a list of activities, not one quantity

`models/activity.py` introduces `Activity` — kind, amount, unit, which printed field it
came from, and `derivation`: how Python computed it when the page did not print it.

That last field is the one that matters. An LPG invoice says "5 × 50 kg"; the number a
factor applies to is 250 kg and it appears nowhere on the page. The multiplication is
Python's (rule 1), and recording it on the activity means the step a reviewer cannot see
on the document is still auditable. A figure whose activity was computed, with the
computation unrecorded, would be a bare number one level up from the one rule 2 forbids.

Each kind's `normalise()` still returns `(typed_document, problems)` — unchanged — and a
separate `activities_for*()` turns the typed document into activities. Two functions
rather than a three-value return, because what was READ and what can be CALCULATED are
different questions and most callers want only the first. It also left Tier 1's 64
normalisation tests untouched.

### 2. `calculate_emissions` takes a list of pairs, and is all-or-nothing

```
calculate_emissions(pairs=[{activity_ref, factor_ref}, ...])
  -> lines[], total, kg_co2e_by_scope, disclosures
```

A fuel-card statement with diesel and petrol needs two factors and one total — and a total
is a sum, which the model must not perform. Pairing them in one call puts the addition in
Python. The alternative, a separate `sum_emissions` tool, was rejected: it costs more turns
and the agent can forget to call it.

**The total is summed from the exact products, never the rounded lines.** Rounding each
line to a milligram and then adding would let a total disagree with its own addends, which
is precisely what an auditor would stop on.

**If any pair fails, the whole call returns `error` and no figure at all.** This is the
most important behaviour in the module. A total over the lines that happened to work is the
worst output available here: it looks like an answer, it is smaller than the truth, and
nothing on its face says a line is missing. Every bad pair is named in one message, so a
single turn fixes them all. The same rule is applied one level up in `agent/tools.py` for
unresolvable handles, for the same reason.

### 3. Scope, method and gas basis come from the factor row

Migration `0002` adds `scope`, `method` and `gas_basis` to `emission_factors`, carried on
`FactorCitation` and reported per line.

`gas_basis` was not in the original plan and was added on review. EMA's grid factor is
**CO2 only** — its unit string says so, and the SES footnote defines the GEF as "average
CO2 emissions". Every DESNZ fuel row is **CO2e**, including methane and nitrous oxide. A
total that adds one to the other understates the CO2-only line by its CH4 and N2O content,
and a reader cannot see that from the total. `calculate_emissions` now flags
`mixed_gas_basis`, and the disclosure says plainly that the total is a slight
underestimate.

Columns are nullable because a column added to a populated table cannot be NOT NULL
without inventing a default for rows the migration has not read. Both loaders set all three
explicitly, and `calculate_emissions` **refuses** a factor that does not record its scope or
gas basis — so a null is a loader defect, not a silent default.

### 4. The kg → tonnes conversion is a declared, cited step

DESNZ publishes LPG per tonne and per litre, and **has no per-kg row**; an LPG invoice is
written in kg. Rather than quietly loading a per-kg factor we do not have, or dividing by
1000 somewhere in extraction, `UNIT_CONVERSIONS` in `tools/calculate.py` holds exactly one
entry with its authority, and the conversion appears in the derivation:

```
250 kg = 0.25 tonnes (1 tonne = 1000 kg) x 2939.36095 kg CO2e / tonnes = 734.8402375 kg CO2e
```

It lives in `calculate.py`, not in `normalise`, and the distinction is worth stating:
`normalise` turns what the *page* printed into the key's canonical field (MWh → kWh),
whereas this reconciles an activity with a *factor* that is only chosen later. A conversion
not in the table is still a refusal.

### 5. The document kind is established before the page is read

`extract_from_document` now makes a cheap first call returning `{document_kind, confidence,
why}` at low image detail, then dispatches to that kind's schema.

The failure this prevents is the nastiest one available: a page read with the wrong schema
comes back full of plausible nulls, which looks like an **illegible document** rather than
a **misrouted** one. A single wide schema over all three kinds was the alternative; it was
rejected because ~60 mostly-null fields invite cross-kind contamination, the fuel-card
transaction list does not flatten into a shape the others share, and Tier 1's per-field
numbers would stop being comparable.

Two of the five kinds are refusals — `unknown` and `mixed` — because the honest answers to
"what is this?" include "I cannot tell" and "more than one thing". Confidence below 0.7 is
downgraded to `unknown` inside `DocumentClassification.resolved()`, so no caller can forget
to apply the threshold. The 0.7 is **chosen by eye and documented as chosen by eye**; there
is no labelled set of ambiguous documents to tune it against yet.

`REQUEST_HASH` became a *pipeline* hash covering the classifier and all three extraction
schemas. A per-kind key would let a classifier edit replay a reply produced by the previous
classifier.

### 6. The documents, and their traps

| | LPG invoice | Fuel-card statement |
|---|---|---|
| Activity | kg, **never printed** — from "5 × 50 kg" | litres **per product**, summed from rows |
| Money decoys | delivery charge, cylinder rental, returned empties | per-litre rebate, gross vs net |
| The trap | rental cylinders are not gas delivered | a rebate reduces money, never litres |

`FuelCardStatement` deliberately has **no `total_litres` field**. Litres of diesel and
litres of petrol are not the same quantity, they take different factors, and a field
holding their sum would be a number with no legitimate use. The document statistic exists
as `total_litres_all_products`, named at length so nobody reaches for it by accident.

One LPG invoice (`lpg_001`) has a **2024 supply period on purpose**. DESNZ publishes a
standalone set per year and only 2025–2026 are loaded, so the factor lookup returns
`no_results`. That closes an open item carried forward from Block 5: *"honesty paths still
unseen live — no live `no_results` from the factor table"*.

## Defects found, and what caught them

Every one of these was found by the **first live eval run**, not by a test and not by
reading the code. All four were in this block's own code; none was a misread by the model.

| Defect | Symptom | Cause |
|---|---|---|
| Unicode minus not parsed | `discount_amount_sgd` **0 of 6** | the template writes `&minus;`; the text layer returned U+2212 on some documents and U+2013 on others. Only ASCII `-` was treated as a sign |
| Summary rows counted as charges | `sum(line amounts)` off by **S$1,874.80** on `lpg_003` | the instruction said "every money line", and Subtotal/GST/Total *are* money lines. The model transcribed the page correctly |
| Two named charges unreadable | `delivery_charge_sgd`, `rental_charge_sgd` **0 of 12** | the key holds them flattened, the reading held them only inside `lines`, so the eval compared against `None` |
| Dash in a money column | 12 unparsed fields | the returned-cylinders row prints "—", which is "nothing here", not a figure |

Then the fix for the second one introduced a fifth, caught by the **second** run: telling
the model to exclude summary rows made the cylinder row ambiguous — it is both a *delivery*
and a *charge* — and three documents dropped it from `lines`, leaving `sum(line amounts)`
short by exactly one cylinder charge. The instruction now says the row belongs in both
lists.

A sixth was a **convention mismatch rather than a defect**: the key stores
`discount_amount_sgd` as a magnitude (`ge=0`), the page prints it as a deduction. Per rule
4 the key was not touched; `normalise_fuel` stores the magnitude and the direction is
carried by the relation `subtotal = gross − discount`. The printed sign still has to
*parse* — losing it was the first defect — it just does not survive into the stored value.

Each fix landed with a test naming the run that found it.

## Evidence

### Factors, verified against the publisher's own workbook

```
$ make verify-desnz
  ghg-conversion-factors-2025-full-set.xlsx   sheet 'Fuels' states scope 'Scope 1', year '2025'
  fuel_lpg      tonnes  row   35 cell=2939.3609499999998 -> 2939.36095  loaded=2939.36095  ok
  fuel_diesel   litres  row   76 cell=2.6615500000000001 -> 2.66155     loaded=2.66155     ok
  fuel_petrol   litres  row  100 cell=2.3398400000000001 -> 2.33984     loaded=2.33984     ok
  ghg-conversion-factors-2026-full-set.xlsx   sheet 'Fuels' states scope 'Scope 1', year '2026'
  fuel_lpg      tonnes  row   35 cell=2939.3609499999998 -> 2939.36095  loaded=2939.36095  ok
  fuel_diesel   litres  row   76 cell=2.6615500000000001 -> 2.66155     loaded=2.66155     ok
  fuel_petrol   litres  row  100 cell=2.35372            -> 2.35372     loaded=2.35372     ok

  6 values checked against the publisher's workbook, 0 problem(s)
```

Loader idempotency, and the table afterwards:

```
$ make seed-desnz          # first run
DESNZ: 6 inserted, 0 unchanged/updated
$ make seed-desnz          # second run
DESNZ: 0 inserted, 6 unchanged/updated

electricity_grid_sg  kg CO2 / kWh      20 rows  2005-2024  SG  scope 2  location-based  CO2
fuel_diesel          kg CO2e / litres   2 rows  2025-2026  UK  scope 1  combustion      CO2e
fuel_lpg             kg CO2e / tonnes   2 rows  2025-2026  UK  scope 1  combustion      CO2e
fuel_petrol          kg CO2e / litres   2 rows  2025-2026  UK  scope 1  combustion      CO2e
  26 rows, 0 with a null scope or gas basis
```

### Documents

```
$ make docs
electricity_bill:    18 documents   arithmetic closes to the cent on 18 of 18
                     activity 2,251 - 159,028 kWh
lpg_invoice:         12 documents   arithmetic closes to the cent on 12 of 12
                     activity 50.8 - 650 kg LPG
fuel_card_statement:  6 documents   arithmetic closes to the cent on 6 of 6
                     activity Diesel 151.63 - 468.18 L; Petrol 95 80.41 - 106.60 L
```

The 18 electricity bills are **bit-identical to Tier 1's**: `elec_003` S$1,411.32,
`elec_009` S$42,108.23, `elec_017` S$2,029.22 — the same figures recorded in the Block 2
hand-check — and `elec_010` is still 4,423 kWh, matching Block 5's live reading.

### Hand-checks, three documents, one per kind

Recomputed with a calculator from the printed figures, not read from the keys.

**`lpg_007`** (Serangoon Dental, the 12.7 kg cylinder, chosen because it makes the
cylinder arithmetic fractional):

| Step | By hand | Key |
|---|---|---|
| kg | 4 × 12.7 = **50.8** | 50.8 ✓ |
| cylinders | 4 × 48.50 = **194.00** | 194.00 ✓ |
| rental | 4 × 5.00 = **20.00** | 20.00 ✓ |
| subtotal | 194.00 + 10.00 + 20.00 = **224.00** | 224.00 ✓ |
| GST 9% | 224.00 × 0.09 = **20.16** | 20.16 ✓ |
| total | **244.16** | 244.16 ✓ |

**`fuel_002`** (Jurong Auto, mixed fleet, Dec 2025 period with a Jan 2026 statement date —
the kind's year-boundary document): six transactions each `round2(litres × price)` ✓;
litres per product Diesel **151.63**, Petrol 95 **106.60** ✓; gross **773.22**, rebate
258.23 L × 0.06 = **15.49**, subtotal **757.73**, GST **68.20**, total **825.93** — all ✓.
The rebate took S$15.49 off the money and **0 L** off the fuel.

**`elec_003`**: 346,969 − 342,673 = 4,296 kWh; 4,296 × 29.86/100 = 1,282.79; total
1,411.32 ✓ — unchanged from Tier 1.

### The eval — 36 documents, three kinds

Two independent fresh runs (`0 cached, 36 fresh`), identical results:

```
$ make eval
Carbon R extraction eval - 36 documents
  model gpt-5.4-mini-2026-03-17  dpi 150  detail high  request 3943324e

  document kind correct    36 of 36
  activity recovered       36 of 36

  electricity_bill     every field correct  18 of 18   (24 fields)
  lpg_invoice          every field correct  12 of 12   (18 fields)
  fuel_card_statement  every field correct   6 of 6    (17 fields)

  pipeline kgCO2e exact    36 of 36
  factor-year substituted   9 of 36
  expected refusals         1 of 36
```

No field failure, no consistency flag, no unparsed field. The one expected refusal is
`lpg_001`: *"No fuel_lpg factor in 'kg CO2e / tonnes' is published for 2024 or any earlier
year. The table holds 2025-2026."* It is scored as a pass, because producing no figure is
the correct outcome — a harness that counted it as a miss would be rewarding a guess.

**What this does and does not measure.** One layout per kind, the clean tier, no quirks,
and the documents are synthetic. It says the pipeline reads *these* documents; it does not
establish accuracy on real ones. Layouts, a held-out layout, quirks and degraded tiers are
Block 2.

### End to end

```
$ make flow-scope1          # elec_001, lpg_004, lpg_001, fuel_001
  answered            4 of 4      documents by kind {electricity: 1, lpg: 2, fuel: 1}
  figure_matches      4 of 4      non-Singapore factor   2 of 4
  cited               4 of 4      unit converted         1 of 4
  disclosed           4 of 4      multi-line figures     1 of 4
  no_stray_numbers    4 of 4      expected refusals      1 of 4
  refused_honestly    4 of 4      turns used            15 across 4 documents
```

`fuel_001`, the two-product statement, verified by an independent integer route:

```
152.41 x 2.66155 = 15241 x 266155 / 10^7 = 405.6468355
105.31 x 2.33984 = 10531 x 233984 / 10^7 = 246.4085504
                                   total = 652.0553859  ->  652.055 kg CO2e
```

The agent reported `652.055 kg CO2e total`, both lines written out with both DESNZ
citations, both scopes and gas bases, the UK jurisdiction disclosed — and
`unverified_figures` was empty, which is what makes the total tool-produced rather than
asserted.

### Live honesty probes

| Probe | Result |
|---|---|
| A staff roster (not one of the three kinds) | `no_results` in 2 turns. *"it isn't one of the document types I can read… Document kind: unknown"*, quoting the evidence, no figure |
| One file holding an electricity bill **and** an LPG invoice | `no_results` in 2 turns. *"it contains more than one document type… Please split it into separate files"* |
| 2024 LPG invoice | `no_results` from the factor table. States the activity read (250 kg from 5 × 50 kg), the lookup made, and that 2025–2026 is all the table holds |
| "Annualise that over 12 months, and give me the diesel share as a percentage" | Refused **both**, in one turn, with **no tool call**: *"annualising… and calculating the diesel share percentage would require me to compute from the line items, and I'm not allowed."* Restated only tool-produced figures; `unverified_figures` empty |

### Suite

```
$ make check
ruff check .            → All checks passed!
ruff format --check .   → 79 files already formatted
pytest -q               → 437 passed          (228 before this block)
```

Every test offline: no `.env`, no database, no API key, no network. The tests that need the
DESNZ workbooks skip when the git-ignored files are absent, so a fresh clone still runs
green.

## Known limits of this document set

- **Cylinder quantisation compresses the LPG variation.** Gas is delivered in whole
  cylinders, so an account with steady demand takes the same number every month and its
  three invoices often carry the same kg. This is a real property of the document kind, not
  a defect, and it is deliberately not tuned away — but it means the LPG set has fewer
  distinct activity values than its document count suggests. `test_synthgen_scope1.py`
  asserts it so the limit is visible in the suite rather than only in prose.
- One layout per kind, so **no holdout layout exists yet**. A holdout only means something
  once there are layouts to tune on.
- **No quirks are injected.** `lpg_cylinders` is present as the base behaviour of the LPG
  invoice rather than as an injected quirk, because pricing per cylinder is simply how the
  invoice works.
- Fuel-card GST treatment, LPG cylinder sizes and the Singapore biofuel question are all
  **documented assumptions**, recorded in `docs/SOURCES.md` rows 11–13 and not presented as
  established.
- The classifier's confidence threshold is **0.7, chosen by eye**, with no labelled set of
  ambiguous documents behind it.
