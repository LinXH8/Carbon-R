# Tier 1 Block 2 — synthgen v0

Dated 16 Oct 2026, committed 17 Oct as `2ad442a`. Block 1 left a verified factor table and
nothing to read. This block created the material that makes an accuracy figure possible:
documents that each come with a correct answer, where the answer exists *before* the
document does.

```
synthgen   :  answer   ──►  document      (this block)
extraction :  document ──►  answer        (Block 3)
eval       :  do the two answers match?   (Block 3)
```

Outcome: 18 synthetic Singapore commercial electricity bills, each written as
`elec_XXX.pdf` + `elec_XXX.json`. They are generated deterministically from a seed, every
key validates, and the arithmetic closes to the cent on all 18.

**This generator has since been superseded.** synthgen v1 ([Tier 2 Block 2](TIER2_BLOCK2.md))
replaced it with four layouts per kind, quirks and difficulty tiers. This file records what
v0 was and why. [SYNTHGEN_V0.md](../synthgen/SYNTHGEN_V0.md) walks through its pipeline
from the six businesses down to the cents, and this file does not repeat that.

---

## The measurement

| | Result |
|---|---|
| Documents | **18**: 6 fictional SMEs × 3 consecutive months. **9 in 2024** (an exact-year factor exists), **9 in 2025** (the factor must fall back to 2024) |
| Keys that validate | **18 of 18**. The model re-derives every arithmetic relation |
| Arithmetic closes to the cent | **18 of 18** |
| PDFs hand-checked from the printed page | **3 of 3** (15 of 15 individual checks) |
| Consumption span | **2,251 – 159,028 kWh** |
| Tests | **48 passing** |

### What this does not say

- **One layout, one fictional retailer, no quirks.** An accuracy figure measured on this
  set means "the pipeline reads bills of *this* shape", not "it reads Singapore electricity
  bills". The realism limits are listed below.
- **The tariff structure is not verified.** The flat rate per tension category, the LT/HT
  threshold and the postal-sector mapping are all `secondary only` in
  [SOURCES.md §6–8](../SOURCES.md), and still are. Every rate and identifier in the
  generator is fictional.
- **No billing period crosses a month or a year.** An early plan draft said the set covered
  a December/January boundary. It does not: every period is a whole calendar month.
  Periods that cross a month or year arrived as v1 quirks.

---

## Files added

| Path | Purpose |
|---|---|
| `src/carbon_r/money.py` | `round2()` and `CENT`, the **only** definition of rounding to cents (`Decimal`, `ROUND_HALF_UP`), shared by the generator, the validator and Block 3's sanity check |
| `src/carbon_r/models/electricity_bill.py` | `ElectricityBill`: frozen, `extra="forbid"`, with a validator that re-derives every arithmetic relation |
| `src/carbon_r/synthgen/rng.py` | Random streams keyed by document identity |
| `src/carbon_r/synthgen/profiles.py` | 1 fictional retailer, 3 tariff plans, 6 fictional SMEs, postal-sector and floor constraints |
| `src/carbon_r/synthgen/electricity.py` | The consumption model and all money, as exact decimals |
| `src/carbon_r/synthgen/render.py`, `templates/electricity_bill_a.html.j2` | Jinja2 → WeasyPrint PDF. Layout `elec_a`, the only one in v0 |
| `scripts/generate_docs.py` | `--kind --seed --out --tiers`. Writes the pairs and `manifest.json` |
| `SYNTHGEN_SPEC.md`, `SYNTHGEN_V0.md` (now under [docs/synthgen/](../synthgen/)) | The rulebook for every document kind, and the v0 walk-through |
| `tests/test_electricity_bill_model.py` | Invariants hold, and each broken relation is *rejected* |
| `tests/test_synthgen.py` | Determinism, stability, structure, arithmetic checked independently, and the PDF's text layer |

---

## Decisions

### The answer key cannot be inconsistent

`ElectricityBill`'s validator recomputes readings → kWh, kWh × rate → energy charge,
subtotal, GST and total. A key whose figures disagree with each other cannot be
constructed. Block 3 had to give extraction a *separate*, validator-free model for exactly
this reason: a key has to be strict, but a reading has to be allowed to be wrong field by
field ([TIER1_BLOCK3.md](TIER1_BLOCK3.md)).

### The template does no arithmetic and contains no dates

Every figure and every date is computed in Python, formatted, and handed to Jinja2. The
template only places them. Two defects below are why "no dates" became a rule rather than
just a habit.

### A fixed monthly charge: one term more than the checklist asked for

The identity checked is `kWh × rate + fixed charge + GST = total`, not the checklist's
`kWh × rate + GST`. This was deliberate. With four plausible dollar figures on the page,
the extractor has to learn that "total" is not "energy charge".

### The rate belongs to a tariff plan, not a customer

There are three plans across six companies, so a rate cannot identify a customer. The
high-tension plan has the *lower* rate (27.65 c/kWh) and the *higher* standing charge
(S$32), which is the real shape of a voltage category: a lower rate per kWh does not mean
a cheaper bill.

### The consumption model

`base × seasonal × noise × days / 30.4`. Each profile has its own seasonal amplitude
(0.18 for a cold store, 0.05 for a dental clinic), because one global value gave all six
businesses the same curve. Scaling by days stops a 29-day February looking like a quiet
month. One profile was resized: 14,500 kWh/month made no sense on a high-tension
industrial plan, so Kranji Cold Store became 180,000.

### Adding a document never changes another one

Each document draws from its own random stream, keyed `seed:account:period:doc_kind`.
Document numbers come from each profile's `first_doc_number`, not from loop position. A
test prepends a profile and asserts that all 18 existing keys stay **byte-identical**.

### Conforming to SYNTHGEN_SPEC.md, after v0 already worked

The spec arrived partway through the block, and v0 broke several of its non-negotiables.
The generator was changed and **the whole set regenerated**:

| Spec | Was | Now |
|---|---|---|
| 3.3 common key fields | absent | `doc_kind`, `layout_id`, `is_holdout_layout`, `difficulty_tier`, `degradation` on every key |
| 3.3 naming | `bill_date` | `document_date`, one name across all kinds |
| 3.2 output layout | `data/generated/electricity/doc_XXX.*` | `data/generated/electricity_bill/clean/elec_XXX.*` |
| 1.4 RNG label | `seed:account:period` | `seed:account:period:doc_kind` |
| 1.7 identifiers | a realistic-looking UEN | `SYN-0000-0001` |
| G1 address constraints | a 7th-floor car workshop; a Kranji street with a Toa Payoh postcode | ground-floor flag, postal sector per profile |
| G3 document date | always period end + 4 days | lag drawn per document, 2–9 days |
| 8.5 stability | untested | the prepend test above |
| 8.9 coherence | HTML only | `pdftotext` on the rendered PDF |

**The reseed changed every number**, because adding `doc_kind` to the RNG label shifts
every draw. It was done at this point on purpose: no eval had run yet, so no recorded
result depended on the old figures. Doing it after Block 3 would have invalidated measured
results.

**One VERIFY item was settled differently from what the spec suggests.** Spec 1.7 wants
identifiers that cannot collide with real ones, verified as invalid. ACRA's UEN format page
returned HTTP 403, so the format could not be checked. Rather than assume a realistic
pattern is never issued, the UEN is now obviously synthetic, and no check is needed to know
it belongs to nobody.

### The factor-year rule, handed to Block 3

> **The emission-factor year comes from the activity period, never from `document_date`.**

`elec_009` is the document that makes this testable: the period is **1–31 Dec 2024** and
the document date is **6 Jan 2025**. Both readings give 0.402 kg CO₂/kWh, which is what
makes it a trap. The figure looks right while a citation driven by `document_date` would
wrongly announce a substitution from 2025. The eval therefore has to check the stated
method as well as the tonnes. The rule is exposed as `ElectricityBill.factor_year` and
asserted by a test.

---

## Defects found, and by what

1. **A test that passed or failed by chance.** It compared Kranji's seasonal spread against
   Serangoon's over different months and expected Kranji's to be wider. Measured
   **0.0241 vs 0.0250**, so it failed, and ±10% noise is far larger than a 0.025 spread
   anyway. It was replaced by a direct test of `seasonal_multiplier` and an exact check
   that each profile's own amplitude is passed through.
2. **A float tolerance that could not tell rounding from error.** `rel_tol=1e-4` failed on
   Serangoon (1.0501075 vs 1.05) only because consumption rounds to whole kWh, which at
   2,400 kWh is about 2e-4. Replaced with exact integer equality.
3. **A worked example computed by hand was wrong.** `9200 × 1.12 × 0.973` was written as
   10,029. It is 10,025.8. `monthly_consumption_kwh` gained injectable `noise` and
   `seasonal`, so every expected value in a test is pasted from script output. **No
   literal is produced by mental arithmetic.**
4. **A date that does not exist** ("31 Apr") appeared in a plan example. This is why no
   date is written by hand anywhere: all dates come from `calendar.monthrange`.
5. **Tests compared PDF bytes.** WeasyPrint stamps a creation time, so byte equality cannot
   show determinism. Tests compare the JSON and HTML instead.

---

## Verification evidence

```
$ make docs
electricity_bill: 18 documents written to data/generated/electricity_bill  (seed 20261016)
  6 accounts x 3 months; 9 in 2024, 9 in 2025
  tiers {'clean': 18}, layouts {'elec_a': 18}, quirks none
  arithmetic closes to the cent on 18 of 18
  consumption 2,251 - 159,028 kWh

$ make check
ruff check .            → All checks passed!
ruff format --check .   → 26 files already formatted
pytest -q               → 48 passed
```

**Three PDFs checked by hand.** Each was transcribed from `pdftotext` output of the
*printed page*, not from the JSON key, then recomputed with `Decimal` / `ROUND_HALF_UP`:

| | elec_003 (LT) | elec_009 (HT) | elec_017 (LT) |
|---|---|---|---|
| Period | 01–31 Mar 2024 | 01–31 Dec 2024 | 01–31 Oct 2025 |
| Document date | 06 Apr 2024 | **06 Jan 2025** | 09 Nov 2025 |
| Readings | 346,969 − 342,673 = **4,296 kWh** | 10,052,675 − 9,913,075 = **139,600 kWh** | 898,440 − 892,299 = **6,141 kWh** |
| Energy | 4,296 × 29.86/100 = **1,282.79** | 139,600 × 27.65/100 = **38,599.40** | 6,141 × 30.12/100 = **1,849.67** |
| Fixed | 12.00 | 32.00 | 12.00 |
| Subtotal | 1,294.79 | 38,631.40 | 1,861.67 |
| GST @ 9% | 116.53 | 3,476.83 | 167.55 |
| Total | **1,411.32** ✓ | **42,108.23** ✓ | **2,029.22** ✓ |

The sample covers both plans, both tension categories, both years, and the `elec_009`
date trap. `git status` stays clean after `make docs`.

---

## Known realism limits of v0

These were recorded but not implemented here. They are tracked as spec Part 7, and v1
addressed several of them as quirks.

| # | Limit | What a real bill does instead |
|---|---|---|
| E1 | Calendar-month periods | Meter-read cycles of 28–34 days, starting mid-month |
| E2 | No balance forward, amount due or due date | More money figures, and "amount due" ≠ the period total |
| E3 | No network or transmission charges | Itemised separately from energy |
| E4 | No CT-meter multiplier | The high-tension register is scaled |
| E5 | Uniform noise | Roughly normal, with occasional extremes |
| E6 | Calendar days | A business closed on Sundays scales with trading days |
| E7 | Flat-rate high-tension plan | Capacity (kW) and peak/off-peak charges |

---

## Carried forward

- **Block 3 takes the factor year from the activity period.** Done there, and checked on
  `elec_009`.
- **SOURCES.md §6–8** (tariff structure, LT/HT threshold, postal sectors) are still
  `secondary only`. No README claim of realism rests on them.
