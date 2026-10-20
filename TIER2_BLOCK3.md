# Tier 2 Block 3 — the eval harness

Written for someone who was not there: the design decisions and their reasons, the defects
found and what caught them, and the evidence behind every number. Environment details and
career notes live in the local build log, not here.

**Status:** done on 20 Oct 2026. Ten commits, `fe370e8` to `eb4b86a`. Every measurement
below names the commit it ran at. The holdout was scored once, at `eb4b86a`, as the last
measurement of the block; nothing was tuned after it.

## What this block had to do

The Block 2 baseline (TIER2_BLOCK2.md) had every emissions figure right (166 of 166) and
125 field failures, 96 of them in Python normalisation, plus one defect in the eval itself.
This block:

1. fixed the measurement before anything it measures;
2. made the eval report per tier and per quirk, with counts;
3. fixed the normaliser one change at a time, each re-scored from the reply cache;
4. added one retry, with the error and never the answer;
5. added company-year scenarios (Makan Mates and 20 variations) with a roll-up, an
   allocation and honesty checks, scored at calculation level and, for three of them, end
   to end through extraction;
6. scored the held-out layout once, last.

## The order, and why it was that order

**Measurement first.** A fix scored by a broken eval is not a measured fix. The credit
column was therefore corrected, and re-scored against the same replies, before any
pipeline code changed.

**One change per commit, each with a prediction.** Each normaliser fix was predicted
from the baseline's traced failures before it was made, then re-scored from the reply
cache. The cache key covers the classifier, the instructions and the schemas, and nothing
in Python, so a normaliser change costs no model calls. A fix whose count differed from
its prediction would have been investigated before the next one. None did.

**Holdout last.** Layout `d` was never opened while tuning, and was scored after
everything else was committed.

## The measurement fix (A, `fe370e8`)

### The credit field

The eval read the key's credit under the reading's field name
(`credit_adjustment_sgd`), but the key's field is `credit_amount_sgd`. The defect was
quiet for two reasons:

- the lookup found nothing and returned `None`;
- the rule that scores an absent credit as zero then turned that `None` into 0.

Two changes:

- **The key's field name is now declared.** `KindSpec.key_fields` maps a reading field to
  the key field it is compared against.
- **The zero rule applies to the reading side only.** An expected field missing from the
  key now raises `KeyError`.

A test asserts that every scored field exists on its key model.

The re-score from cache reproduced the offline Block 2 figure exactly: credit 165 of 166,
125 field failures, pipeline 166 of 166.

### Per tier, per quirk, and cost

`summarise` produces two count tables: tier × kind and quirk × kind. Each cell gives
every-field-correct, activity and pipeline counts. Cells under 15 documents are marked,
because several v1 cells are thin. The same tables go into the run JSON as `summary`, so
the README quotes a file and not a console.

Fresh calls now record the provider's token counts, in the cache entry and the run.

### A defect introduced while fixing the speed, and caught the same hour

A re-score made several hundred factor lookups for a handful of distinct keys, each over a
new connection, so lookups are now memoised per run.

The first version memoised every result, **errors included**. A single dropped database
connection was replayed onto all ten bills sharing its key. Those ten scored as pipeline
misses, and electricity's pipeline count showed 72 of 82 for code that had not touched it.

**The fix:**
- only `ok` and `no_results` are memoised;
- an `error` is retried up to three times and recorded in the run as `factor_lookup_retries`;
- if it still fails, the run aborts.

A run that contains a database error is not a measurement, and must not be scored as one.

## The normaliser fixes (B, 0 model calls each)

| Commit | Change | Predicted | Field failures after |
|---|---|---|---|
| `70fe9f1` B3 | Meter readings converted in the bill's unit, as consumption already was | −48 | 125 → **77** |
| `84bef71` B4 | An LPG charge line whose quantity is a word ("2 visits", "1 trip(s)") keeps its amount | −25 | 77 → **52** |
| `be60ddc` B5 | Layout b's "Cylinder hire" and layout c's "CYL RENTAL" recognised as rental | −20 | 52 → **32** |
| `67bec06` B6 | The inline "UEN" label dropped on LPG and fuel, not only electricity | −3 | 32 → **29** |

Pipeline kgCO2e stayed 166 of 166 throughout.

**B3 also closed a latent hazard the baseline could not show.** `clean` strips "MWh" as
noise, so a whole-number "285 MWh" with no separate unit would have parsed as **285 kWh**:
1000 times low, with no problem raised. The baseline's MWh readings all had decimals, so
they failed loudly instead. `energy_kwh` now reads a unit printed with the figure before
anything strips it. Two printed units that disagree are a problem, not a choice.

**B4 fixed the structure, not just the words.** The count words joined the inline-unit
allowlist, and a quantity or unit price that will not parse is now a problem against that
cell. The line's amount is kept, because the amount is the figure and the other cells
only describe it.

**Not fixed, on purpose:**
- **Two fields under one label** (`fuel_b`'s "Account / card", `fuel_c`'s "CARDHOLDER
  name, address"): 8 fields. This is a property of the layout, and a fair trap; it is not a
  normaliser defect.
- **The model "correcting" `SFC-…-N-NN` to `SFC-…-NN`**: 3 fields. The identifier oddity
  stays, because changing it would change v1.

### What is left: 29 failures, none in normalisation

- 18 genuine misreads, 15 of them on hard-tier phone photos;
- 8 from two fields printed under one label;
- 3 from the identifier "correction".

| Tier | Electricity, every field | LPG | Fuel card |
|---|---|---|---|
| clean | 29 of 29 | 22 of 22 | 14 of 18 |
| moderate | 26 of 26 | 9 of 10 | 11 of 12 |
| hard | 25 of 27 | 9 of 10 | 3 of 12 |
| **all** | **80 of 82** (was 57) | **40 of 42** (was 0) | **28 of 42** (was 27) |

Run at `eb4b86a`, from cache, holdout excluded. That is the same commit as the holdout,
re-run afterwards only to have a clean-tree record. It reproduced the pre-holdout figures
exactly, and no code changed between them.

**Per quirk, every field correct:**

| Quirk | Electricity | LPG | Fuel |
|---|---|---|---|
| `estimated_reading` | 24 of 24 | | |
| `unit_mwh` | 23 of 24 | | |
| `cross_month_period` | 15 of 15 | | |
| `cross_year_period` | 14 of 15 | | |
| `credit_adjustment` | 22 of 24 | 14 of 16 | 10 of 19 |
| `balance_forward` | 22 of 24 | 14 of 15 | 9 of 19 |
| `gst_confusion` | 21 of 23 | 13 of 15 | 9 of 18 |
| `lpg_cylinders` | | 30 of 32 | |
| `mixed_fuel` | | | 14 of 24 |

Activity and pipeline are 100% in every cell. The fuel quirk rows are confounded with the
hard tier (TIER2_BLOCK2.md §3): every hard fuel document carries the same three money
quirks, so a low fuel cell above is mostly a hard-photo cell.

## The retry (C)

### The rule

When a reading has a field that will not parse, or a page-arithmetic check that fails,
extraction reads the page **once** more.

**The retry message:**
- names each field that would not parse, quoting the model's own transcription;
- names each failed check by its relation, e.g. "gross_amount = sum(transaction
  amounts)";
- **never names a figure the page should hold**: no expected value, no difference, no
  value printed elsewhere;
- tells the model not to change a value to make arithmetic agree.

The retry wording is kept out of the first request's fingerprint, so every cached first
reading still hits.

**What happens to the second reading:**
- It is adopted only if it leaves fewer problems and failed checks.
- Every field it changed is marked `changed_on_retry`, and a disclosure sentence says
  those fields are less certain.
- If checks still fail on the reading that stands, it is `inconsistent`: accepted,
  disclosed, and never retried again.
- Both disclosures travel with every figure computed from that reading, so the page shows
  them beside the figure.

**What the eval records:** every document is scored with and without the retry in one run.
Each changed field goes into one of three buckets, judged against the key:
- wrong → correct;
- correct → wrong, printed first;
- wrong → wrong.

### The first version made things worse, and the counts caught it (`fcc1dbf`)

Taking the whole second reading, the first live run (4 fresh retries, all hard fuel
photos) gave:

```
fields correct -> wrong   3   <- TRUE VALUES OVERWRITTEN
fields wrong -> correct   0
fuel every field          28 of 42 without retry -> 27 of 42 with it
```

On `fuel_027` and `fuel_048` the retry fixed exactly the value it was asked about. But
re-reading the whole page, it also got three fields nobody had asked about wrong: an
account number, a card number and an address. Because the second reading had fewer failed
checks, it was adopted whole, collateral damage included.

### Scoped retry (`8e38882`)

A retry now replaces only the **printed** fields that a failed check reads, or that would
not parse:
- each consistency relation names its fields (`ConsistencyFlag.fields`);
- each parse problem names its printed source (`FieldProblem.source`).

Every other field keeps its first reading. The merged reading is normalised again from
scratch, so derived totals (litres per fuel, kg) are recomputed by the code rather than
spliced in.

The scoped version was re-scored from the cached retries at 0 model calls:

| | Whole-reading retry | Scoped retry |
|---|---|---|
| fields correct → wrong | **3** | **0** |
| fields wrong → correct | 0 | 0 |
| changed, unscored | 2 | 2 |
| field failures | 32 | 29 (equal to no retry) |

**The two adopted changes were checked against the key by hand**, because the eval does
not score them:
- `fuel_027` transaction 5: amount 78.62 → **78.52**, which is the key's value;
- `fuel_048` printed petrol total: "216,76" → **"216.76"**. The first reading used a comma
  as the decimal point, which parsed as 21,676 litres.

`fuel_036` and `fuel_039` were re-read with the same wrong gross amount. Neither was
adopted; both stay `inconsistent` and are disclosed.

**On the holdout**, one retry (`fuel_026`) moved a *scored* field, the gross amount,
wrong → correct.

**What the retry does not do:** it fixed no scored field on the tuned set. Its measured
value so far is two unscored corrections and one scored correction on the holdout, at no
measured cost. That is a small sample, stated as one.

`make flow-block2` after the retry: 4 of 4 on all six checks, with the same four figures to
the gram as the Block 2 baseline.

## Company-year scenarios (D, `887cdd0`, `5ae22e3`)

### What a scenario is

A scenario is one fictional business's documents for one reporting year, built from the
v1 builders, templates and quirk injectors, plus a `scenario.json` (QUIRKS.md has a
section on what the pages show). There are 21 scenarios and 676 documents, seed `20261020`:

- **Makan Mates 2025, the base:** 12 electricity bills, 12 LPG invoices and 12 diesel
  fuel-card statements.
- **15 variations of the base:**
  - one missing month; two missing months;
  - estimated readings;
  - bills printed in MWh;
  - a 15th-to-14th meter cycle, so the year's first and last bills straddle its edges;
  - credits and balances forward;
  - three LPG invoices in USD;
  - reporting year 2024;
  - a mixed diesel and petrol fleet;
  - trading only from July;
  - contract share 0%; contract share 100%;
  - GST-inclusive headlines;
  - one quiet LPG month; a three-month LPG gap.
- **Five other businesses:** a laundry, a cold store, a print works on per-delivery LPG, a
  dental clinic with electricity only, and a vehicle workshop with a mixed fleet.

### Decisions

**Why the reporting year is 2025.**
- Both factor paths are exercised in every scenario. Electricity takes EMA 2024 (no 2025
  figure is published), as a disclosed substitution. LPG and diesel take DESNZ 2025
  exactly.
- No document lands in a year with no factor by accident. The one scenario where that
  happens, reporting year 2024, is deliberate.
- 2026 is not over, so a 2026 year would show October to December as gaps.

**`LPG_MAX_GAP_DAYS = 45`.**
- *The cadence in the profiles:* a monthly-billed kitchen's invoices cover 28 to 31 days
  each, and a per-delivery customer's visits fall 9 to 16 days apart.
- *Why one quiet month is not flagged:* a kitchen with full cylinders on site can run a
  month without buying gas, so a month with no LPG invoice is ordinary.
- *Why 45:* it is two weeks longer than the longest month and nearly three times the
  longest delivery interval. It flags two consecutive months without an invoice, or three
  missed deliveries, and not one quiet month.
- *Electricity and fuel cards* bill every month, so for them every uncovered day of the
  active period is a gap.

It is a judgement, written next to the constant so it can be argued with.

**USD without FX.** The `usd_invoice` quirk prices an LPG invoice from its own fictional
USD list. That list is not a conversion of the SGD list at any rate: an FX rate is a
VERIFY item, and none is claimed. The kg, and so the figure, do not depend on the currency.
The roll-up lists the document as non-SGD and converts nothing. The invoice charges 9% GST
in USD. Whether a Singapore tax invoice in a foreign currency must also show the GST in
SGD has not been checked against IRAS; the page makes no claim either way.

**The 2024 scenario has no fuel statements.** The fictional pump-price series does not
cover early 2024. Extending it to make this scenario possible would be inventing prices
for a test's convenience. The twelve LPG invoices carry the `no_results` path on their own.

**v1 does not move.** Scenario profiles live outside the tuples the v1 planner walks, and
`usd_invoice` is a structural quirk, which the planner never picks.
`tests/test_synthgen_v1_stability.py` compares a digest of all 198 v1 answer keys (JSON)
and rendered pages (HTML) with one taken at `8e38882`. That digest came from a separate
worktree, verified to have imported the old code. Result: 198 of 198 unchanged. PDF bytes
are not compared, because WeasyPrint stamps them (spec 1.4).

### The roll-up and the allocation (`tools/company_year.py`)

Both are pure Python, return `{status, data, message}` and are not agent tools yet; the
agent tools are Tier 3.

**`roll_up`:**
- Sums the exact products `calculate_emissions` already returned, by scope, and rounds
  once.
- Lists every day outside the reporting year (a meter-cycle bill's December 2024 days)
  rather than counting it.
- Names every document it could not cost and marks that scope partial.
- Reports a scope with no document as `None`, never 0.
- Lists gaps, estimated readings, substitutions, non-Singapore factors and non-SGD
  documents.

**`allocate`:**
- States that it allocates Scope 1 + Scope 2.
- Multiplies before it divides, and rounds once.
- Carries the contract revenue, the total revenue and the share's source.
- Says that revenue-share allocation is an assumption, not a measurement.
- Inherits every disclosure the roll-up raised.

### Calculation level: 21 of 21 (`make eval-scenarios`, 0 model calls)

Each scenario's **truth** activities go through the real path:

```
lookup_emission_factor -> calculate_emissions -> roll_up -> allocate
```

The result is compared with a second route, computed separately:

- **Emissions:** exact rationals of truth amount × the factor value the table holds,
  scoped by document kind, not by factor row.
- **Honesty facts:** taken from each scenario's own definition, not from the roll-up's
  output.

```
commit eb4b86a · seed 20261020 · synthgen-scenario-1 · 0 model calls
  scope_1_exact 21 of 21 · scope_2_exact 21 of 21 · allocation_exact 21 of 21
  gaps_exact 21 of 21 · estimated_exact 21 of 21 · excluded_named 21 of 21
  substitution_disclosed 21 of 21 · outside_year_listed 21 of 21
  currency_listed 21 of 21 · absent_scope_not_zero 21 of 21
```

**A check that passes everything on its first run has to be shown able to fail.** Three
tests run the eval against a stand-in factor table with a deliberately broken roll-up. The
eval fails each one:
- a roll-up that hides gaps;
- a roll-up that drops excluded documents;
- a roll-up that counts the neighbouring year's days.

**Makan Mates 2025**, re-derived by hand:

```
Scope 2   99,740 kWh x 0.402 (EMA 2024, substituted, disclosed)       = 40,095.480 kg
Scope 1   7.7 t LPG x 2,939.36095 = 22,633.079 kg
          3,775.19 L diesel x 2.66155 = 10,047.857 kg                 = 32,680.936 kg
Total                                                                   72,776.416 kg
Allocated 72,776.416 x 720,000 / 2,400,000                            = 21,832.925 kg
```

The missing-month variation, by the same route: 90,363 kWh × 0.402 = 36,325.926 kg, and
20,702.059 kg allocated. Share 0 gives 0.000.

**These are not the project context's 30.1 / 39.4 / 21 t.** Those figures used
approximate factors and were illustrative (SYNTHGEN_SPEC 4.5). The ones above are
computed from activities and the verified factors in the database.

### End to end: 3 of 3 (`make eval-scenarios-e2e`, `5ae22e3`)

Three scenarios went through classification and extraction, and the year was rolled up
from what was **read**:
- the base year;
- the missing month;
- the estimated readings.

```
commit 5ae22e3 · gpt-5.4-mini-2026-03-17 · 107 documents, 0 cached · 214 calls
                                  base      missing month   estimated
  kind / activity / pipeline      36/36/36  35/35/35        36/36/36
  every field                     28 of 36  27 of 35        29 of 36
  Scope 1, Scope 2, allocation    = calculation level, all three
  gaps                            {}        elec 2025-03    {}
  estimated documents             []        []              elec_006, elec_007
```

This is where the honesty checks met model behaviour:
- The March gap was flagged because each period was read right.
- The two estimates were listed because "Estimated" was read off the page.

Field misses fall into three groups:
- Almost all are the generator's `…-0-11` identifier, which the model "corrects".
- The `fuel_b` layout's account and card share one label.
- One gross amount (`fuel_008`, estimated scenario) is a genuine misread.

Cost: 741,252 input tokens and 45,786 output tokens.

## The holdout, once, last (E, `eb4b86a`)

```
make eval-holdout   commit eb4b86a (clean) · 32 documents (layout d) · 0 cached, 32 fresh
  document kind 32 of 32 · activity 32 of 32 · pipeline kgCO2e 32 of 32
  every field: electricity 0 of 11 · LPG 7 of 9 · fuel 5 of 12 · 26 field failures
  retry 1 triggered, 1 adopted: fuel_026 gross amount wrong -> correct; 0 correct -> wrong
  65 calls, 206,444 in, 15,417 out
```

**Every emissions figure on the unseen layout was right.** Of the 26 field failures:

| Cause | Fields |
|---|---|
| Layout `d` labels the UEN "**Registration**"; the label list (B6) knew only "UEN" | 13 (electricity 5, fuel 6, LPG 2) |
| Layout `d` prints "Low Tension **supply**"; the tension field keeps the suffix | 11 (all electricity) |
| Genuine misreads, hard photos (`fuel_042` card, `fuel_050` gross) | 2 |

24 of the 26 are two label gaps: normaliser rules tuned on layouts a–c meeting a layout
they had never seen. **This is the overfitting the holdout exists to show.** Per the rule,
it is recorded and not fixed in this block. Fixing it now would be tuning on the holdout,
which would then measure nothing.

## Defects found, and what caught them

| Defect | Caught by |
|---|---|
| Eval compared the credit column against a non-existent key field | The Block 2 live run; fixed in A, with a test that every scored field exists on its key |
| A whole-number MWh figure with no unit column would parse 1000× low | Reading `clean` while fixing B3; now a test |
| The memo cached a database error and replayed it onto ten bills | The re-score's pipeline count dropping on code that had not changed |
| A key-side lookup error scored as "figures differ" | The same re-score; lookup errors now retry, then abort the run |
| The whole-reading retry overwrote three correct fields | The correct → wrong bucket, on its first live run |
| Layout d's "Registration" and "… supply" labels | The holdout |

## Known limits

- **Synthetic documents only.** The scenarios' revenues and contract shares are fictional.
- **One run each.** Run-to-run variation is still unmeasured.
- **The retry's value is a small sample:** 5 triggers across the tuned set and the
  holdout.
- **The scenario set covers one industry in depth.** The other five businesses are one
  year each, with no variations.
- **Allocation by revenue share is one method among several** and is labelled as an
  assumption.
- **The calculation-level route and the real path share the factor value**, by design:
  Rule 3 makes the table the only source of it. What is independently checked is
  everything done with it.
- **The holdout's two label gaps are open.** They are the first thing the next normaliser
  change should address, and the holdout cannot be used to measure that change.

## Suite

`make check`: ruff clean; **585 passed, 1 skipped** (was 521 at the start of the block).
