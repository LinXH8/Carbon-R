# How synthgen v0 generates the electricity bills

> **What this file is.** A walk-through of how the 18 synthetic electricity bills in
> `data/generated/electricity_bill/` are produced, from the six fictional businesses down to
> the cents on the page. It describes what the code *does today*, so it is the file to read
> before changing the generator or trusting a number that came out of it.
>
> **Companions.** [SYNTHGEN_SPEC.md](SYNTHGEN_SPEC.md) is the rulebook for all document kinds
> (this implementation conforms to it). [CLAUDE.md](../../CLAUDE.md) holds the project-wide rules.
> [SOURCES.md](../SOURCES.md) records what is verified and what is not.
> Decisions and their dates live in the author's local build log, which is not committed.

---

## 1. Why the documents are manufactured at all

Carbon R claims you can upload an electricity bill and get a Scope 2 figure you can audit.
That claim is only worth something if you can say **how often it is right** — and to say that
you need, for every test document, both the document and the correct answer.

Real bills supply neither reliably: they are confidential, few, uncommittable to a public
repo, and you would have to read the right answer off each one by hand, making your own
labelling a source of error.

Manufacturing them inverts the problem. **You know the answer because you chose it.** Nobody
read 139,600 kWh off a page and hoped; the generator decided the cold store used 139,600 kWh
and then drew a bill that says so.

```
synthgen   :  answer   ──►  document      (decide the truth, then draw it)
extraction :  document ──►  answer        (Block 3: the model sits the exam)
eval       :  do the two answers match?   (the score)
```

Everything below exists to make that last comparison trustworthy.

---

## 2. What comes out

```
data/generated/electricity_bill/          (git-ignored, as is every generated artefact)
├── clean/
│   ├── elec_001.pdf      the exam question — a rendered A4 tax invoice
│   ├── elec_001.json     the answer key — every value on that page, plus its derivation
│   ├── … elec_018.pdf / elec_018.json
└── manifest.json         seed, generator version, counts by tier/layout/quirk/year, citations
```

18 documents: **six fictional businesses × three consecutive monthly bills each**. Nine bills
fall in 2024 and nine in 2025, which matters because the EMA factor table stops at 2024 — half
the set finds an exact-year factor and half must fall back and say so.

| doc | customer | period | document date | kWh | total |
|---|---|---|---|---|---|
| `elec_001` | Kopi Corner | 2024-01-01…01-31 | 2024-02-03 | 4,016 | 1,320.19 |
| `elec_002` | Kopi Corner | 2024-02-01…02-29 | 2024-03-09 | 3,530 | 1,162.01 |
| `elec_003` | Kopi Corner | 2024-03-01…03-31 | 2024-04-06 | 4,296 | 1,411.32 |
| `elec_004` | Lion City Laundry | 2024-04-01…04-30 | 2024-05-07 | 9,639 | 3,150.33 |
| `elec_005` | Lion City Laundry | 2024-05-01…05-31 | 2024-06-09 | 9,522 | 3,112.24 |
| `elec_006` | Lion City Laundry | 2024-06-01…06-30 | 2024-07-04 | 9,942 | 3,248.94 |
| `elec_007` | Kranji Cold Store | 2024-10-01…10-31 | 2024-11-07 | 159,028 | 47,963.53 |
| `elec_008` | Kranji Cold Store | 2024-11-01…11-30 | 2024-12-08 | 136,930 | 41,303.53 |
| **`elec_009`** | **Kranji Cold Store** | **2024-12-01…12-31** | **2025-01-06** | **139,600** | **42,108.23** |
| `elec_010` | Tampines Print Works | 2025-02-01…02-28 | 2025-03-05 | 4,423 | 1,465.19 |
| `elec_011` | Tampines Print Works | 2025-03-01…03-31 | 2025-04-05 | 6,021 | 1,989.83 |
| `elec_012` | Tampines Print Works | 2025-04-01…04-30 | 2025-05-08 | 5,561 | 1,838.80 |
| `elec_013` | Serangoon Dental Group | 2025-05-01…05-31 | 2025-06-05 | 2,474 | 825.32 |
| `elec_014` | Serangoon Dental Group | 2025-06-01…06-30 | 2025-07-08 | 2,251 | 752.10 |
| `elec_015` | Serangoon Dental Group | 2025-07-01…07-31 | 2025-08-07 | 2,738 | 911.99 |
| `elec_016` | Jurong Auto Services | 2025-09-01…09-30 | 2025-10-05 | 6,506 | 2,149.05 |
| `elec_017` | Jurong Auto Services | 2025-10-01…10-31 | 2025-11-09 | 6,141 | 2,029.22 |
| `elec_018` | Jurong Auto Services | 2025-11-01…11-30 | 2025-12-03 | 5,907 | 1,952.40 |

Consumption spans **2,251 – 159,028 kWh**, a 71× range. Totals span S$752.10 – S$47,963.53.

**`elec_009` is the one to remember.** Its activity happened entirely in December 2024, but
the bill is dated 6 January 2025. It is the only document where the billing period and the
document date disagree about the year — see §7.

---

## 3. The code

| File | Lines | Role |
|---|---|---|
| [`src/carbon_r/money.py`](../../src/carbon_r/money.py) | 25 | `round2()` and `CENT`. The **only** definition of "round to cents" in the project |
| [`src/carbon_r/models/electricity_bill.py`](../../src/carbon_r/models/electricity_bill.py) | 149 | `ElectricityBill` — the answer-key schema *and* its self-check |
| [`src/carbon_r/synthgen/profiles.py`](../../src/carbon_r/synthgen/profiles.py) | 248 | The cast: one retailer, three tariff plans, six businesses |
| [`src/carbon_r/synthgen/rng.py`](../../src/carbon_r/synthgen/rng.py) | 44 | Seeded, label-keyed random streams and the injectable `draw()` |
| [`src/carbon_r/synthgen/electricity.py`](../../src/carbon_r/synthgen/electricity.py) | 241 | Consumption model and all money arithmetic |
| [`src/carbon_r/synthgen/render.py`](../../src/carbon_r/synthgen/render.py) | 113 | Jinja2 environment, pre-formatting, HTML → PDF |
| [`.../templates/electricity_bill_a.html.j2`](../../src/carbon_r/synthgen/templates/electricity_bill_a.html.j2) | 141 | Layout `elec_a`, the only v0 layout |
| [`scripts/generate_docs.py`](../../scripts/generate_docs.py) | 184 | CLI; writes the pairs and the manifest |

Nothing here touches the database, an API key or a model provider. `make docs` runs offline.

---

## 4. The pipeline, stage by stage

### 4.1 The cast — `profiles.py`

**One fictional retailer.** `Straits Power Retail Pte Ltd`, UEN `SYN-0000-0001`, support
address on the reserved `.example` TLD. Not SP Group; no real company, logo or branding
appears anywhere.

**Three tariff plans.** The crucial point: *a price per kWh belongs to the retailer's plan,
not to the customer*. Two businesses on the same plan print the same rate.

| Plan code | Name | Tension | Rate | Monthly fixed |
|---|---|---|---|---|
| `SPR-LT-FIX-24` | Business Fixed 24 | Low Tension | 29.86 c/kWh | S$12.00 |
| `SPR-HT-FIX-24` | Industrial Fixed 24 | High Tension | 27.65 c/kWh | S$32.00 |
| `SPR-LT-FIX-25` | Business Fixed 25 | Low Tension | 30.12 c/kWh | S$12.00 |

The high-tension plan has a **lower** rate but a **higher** standing charge. That is the real
shape of a voltage category — a large consumer takes supply at higher voltage, pays less per
unit and more as a fixed charge — and it means "cheaper per kWh" and "cheaper bill" are not
the same thing anywhere in this set.

**Six businesses.**

| Docs | Business | `base_kwh` | Amplitude | Plan | Months | Postal sector |
|---|---|---|---|---|---|---|
| 001–003 | Kopi Corner Pte Ltd | 3,800 | 0.10 | `SPR-LT-FIX-24` | 2024-01…03 | 53 |
| 004–006 | Lion City Laundry Pte Ltd | 9,200 | 0.08 | `SPR-LT-FIX-24` | 2024-04…06 | 53 |
| 007–009 | Kranji Cold Store Pte Ltd | 180,000 | 0.18 | `SPR-HT-FIX-24` | 2024-10…12 | 72 |
| 010–012 | Tampines Print Works Pte Ltd | 5,100 | 0.11 | `SPR-LT-FIX-25` | 2025-02…04 | 52 |
| 013–015 | Serangoon Dental Group Pte Ltd | 2,400 | 0.05 | `SPR-LT-FIX-25` | 2025-05…07 | 55 |
| 016–018 | Jurong Auto Services Pte Ltd | 6,700 | 0.12 | `SPR-LT-FIX-25` | 2025-09…11 | 61 |

Two fields carry most of the realism:

- **`base_kwh` is usage in a month of *average length* (30.4 days)** — not "in a calendar
  month". The distinction is what makes the day-scaling term in §4.3 meaningful.
- **`seasonal_amplitude` belongs to the business, not to the weather.** A cold store runs
  compressors against ambient heat and swings ±18%; a small, fully-conditioned dental surgery
  barely moves at ±5%. The *timing* of the peak is global; only the size of the swing is local.

**Document numbers are owned by the profile** (`first_doc_number`), not taken from loop
position, so adding a seventh business cannot renumber anyone else's documents.

**Addresses are constrained by the profile.** Faker has no `en_SG` locale, so streets are
literals and Faker supplies digits only, within two constraints: a coffee shop, laundry, cold
store, print works or workshop gets a **ground-floor unit** (`#01-xx`), and the postal code
carries the **sector matching its area**. Both constraints exist because unconstrained Faker
produced a seventh-floor car workshop and a Kranji address with a Toa Payoh postcode.
*The sector-to-area mapping is from secondary sources only — [SOURCES.md](../SOURCES.md) #8.*

### 4.2 Randomness — `rng.py`

Every document draws from a stream keyed by **which document it is**, never by its position
in a loop:

```python
doc_rng(seed, account_number, f"{year}-{month:02d}", doc_kind)
# → random.Random("20261016:8820-3309-01:2024-12:electricity_bill")
```

Why it matters: with one shared stream handed out in order, inserting a business at the top
would shift every later draw. All 18 bills would change, and a recorded accuracy figure
("16 of 18") would silently refer to documents that no longer exist. With a label per
document, adding a business adds its documents and touches nothing else. A test proves it.

Two traps closed here:

- **Seed from the formatted string, never from `hash(...)`.** `PYTHONHASHSEED` randomises
  `str.__hash__` per process, so a `hash()`-derived seed would produce different bills on
  different runs — surfacing as intermittently failing tests rather than as a visible bug.
- **`draw(value, rng, low, high)`** returns `value` when one is injected. Every random
  quantity goes through it, so a test can fix any single factor and assert an exact answer
  instead of asserting on whatever the RNG happened to produce.

### 4.3 How much electricity — `electricity.py`

```python
consumption_kwh = round(base_kwh × seasonal × noise × days_in_period / 30.4)
```

Three multipliers on the business's typical month:

| Factor | Range | What it represents |
|---|---|---|
| `seasonal` | 0.82 – 1.18 | Time of year. `1 + amplitude × cos(2π(month − 5) / 12)`, peaking in **May**, the hottest month. Shared in timing, per-business in size |
| `noise` | 0.90 – 1.10 | This month happened to be busier or quieter — a long weekend, a machine down |
| `days / 30.4` | 0.92 – 1.02 | Month length. A 28-day February covers ~10% less time than a 31-day March and must bill ~10% less |

Without the day term, February would appear to be a low-consumption month for metabolic
rather than calendar reasons — wrong in itself, and actively misleading once Tier 2 starts
pro-rating periods. Without noise, the 18 bills would lie on six smooth curves and a model
could predict the third from the first two without reading the page.

`round()` to an `int`, because meters count whole units. This is the only floating-point
arithmetic in the module, and it becomes an integer immediately — **no float ever reaches
money**.

### 4.4 Meter readings

A real bill shows the meter's running lifetime total at both ends of the period, and the usage
is the difference. So each business gets a plausible starting odometer and each month's usage
is added to it:

```python
opening = round(base_kwh × uniform(24, 150))      # months of service
current = previous + consumption                  # carried forward month to month
```

| Business | Opening read | ≈ months of service |
|---|---|---|
| Serangoon Dental Group | 73,868 | 30.8 |
| Kopi Corner | 335,127 | 88.2 |
| Tampines Print Works | 604,385 | 118.5 |
| Jurong Auto Services | 885,793 | 132.2 |
| Lion City Laundry | 1,013,245 | 110.1 |
| Kranji Cold Store | 9,617,117 | 53.4 |

Three deliberate choices:

- **Sized to the business, not drawn from a fixed absolute range.** A flat 100k–900k would put
  Kranji's 180,000 kWh/month meter at under five months of service.
- **`uniform`, not an integer month count.** An integer would make every opening read an exact
  multiple of `base_kwh`, so meters would start on suspiciously round numbers like 414,000.
  Round numbers are a fake-data tell.
- **The readings cannot contradict the usage.** Because `current` is *built by adding*
  consumption to `previous`, `current − previous == consumption` is true by construction. It
  also puts three kWh figures on the page, of which only the smallest is the answer — so an
  extractor that grabs the first number near "kWh" is caught.

### 4.5 Dates

```python
period_start  = 1st of the month
period_end    = calendar.monthrange(year, month)[1]     # the real last day
document_date = period_end + randint(2, 9) days
```

`monthrange` supplies the day count, so February 2024 correctly gets 29 days and no date can
name a day its month does not have. **No template contains a literal date** — every date on
the page is formatted from the model.

The lag is drawn per document rather than fixed, so `document_date` is not a constant offset
from `period_end` that a model could learn. Across the set the drawn lags are 3–9 days.

### 4.6 Money — exact decimals only

Computed in `electricity.py`, in the same order the validator re-checks them:

```python
energy_charge_sgd = round2(Decimal(consumption_kwh) × tariff_cents_per_kwh / 100)
fixed_charge_sgd  = plan.fixed_charge_sgd
subtotal_sgd      = energy_charge_sgd + fixed_charge_sgd
gst_amount_sgd    = round2(subtotal_sgd × gst_rate)
total_sgd         = subtotal_sgd + gst_amount_sgd
```

Five rules, each load-bearing:

1. **`Decimal`, never `float`.** Once in a while floating point lands on the wrong side of a
   cent: a subtotal of $1,001.50 at 9% is **$90.14** exactly but **$90.13** via floats. It is
   wrong only occasionally, which is worse than always — the three bills checked by hand would
   look fine.
2. **`Decimal(str(x))`, never `Decimal(float)`.** `Decimal(0.09)` is
   `0.0900000000000000022…`; `Decimal("0.09")` is exactly nine hundredths.
3. **Rounding happens at exactly two places** — the energy charge and the GST — because that is
   where a real vendor rounds. Subtotal and total are plain sums of already-rounded cents and
   are never re-rounded, so they cannot drift from the printed page.
4. **`ROUND_HALF_UP`, not Python's default banker's rounding.** Commercial invoices round half
   up. One shared helper, `carbon_r.money.round2`, is used by the generator, the validator and
   (from Block 3) the extraction sanity check, so the three cannot diverge.
5. **`gst_rate` comes from `get_settings()`**, not a literal, and is written into every answer
   key so the key stays self-describing. 9% is verified from primary ([SOURCES.md](../SOURCES.md) #4).

### 4.7 Validation — the key checks itself

`build_bill()` returns a **validated** `ElectricityBill`. The model is frozen, forbids unknown
fields, and its `@model_validator(mode="after")` re-derives every relation:

```
consumption_kwh   == current_reading_kwh − previous_reading_kwh
energy_charge_sgd == round2(consumption_kwh × tariff_cents_per_kwh / 100)
subtotal_sgd      == energy_charge_sgd + fixed_charge_sgd
gst_amount_sgd    == round2(subtotal_sgd × gst_rate)
total_sgd         == subtotal_sgd + gst_amount_sgd
period_start      <= period_end <= document_date
```

An internally inconsistent answer key is therefore **unconstructible**. A mistake in the
generator fails at the moment the bill is created — before anything reaches disk, and long
before a PDF exists. This matters because the key is the arbiter of truth for every eval
downstream: a wrong key sends you debugging a prompt that was fine.

### 4.8 Rendering — `render.py` and the template

`bill_context()` turns the validated model into **pre-formatted strings**; the template prints
them. Consequences: the template does no arithmetic and holds no date literals, and the page
can only print values that already passed the validator.

The Jinja environment uses `StrictUndefined`, so a missing field raises rather than rendering
blank. WeasyPrint turns the HTML into an A4 PDF with a real text layer — which is what the
hand-check and the coherence test read.

Every page carries the synthetic notice twice, at the top and in the footer, with the
generator version and seed:

> SYNTHETIC DOCUMENT - generated by Carbon R synthgen for testing. Not a real document.
> All parties and figures are fictional.

WeasyPrint is imported lazily inside `write_pdf()`, so the test suite runs without the native
pango/cairo libraries.

---

## 5. One bill, end to end: `elec_009`

Kranji Cold Store, December 2024. Every figure below is what the code actually produced.

**Inputs** — `base_kwh` 180,000; `seasonal_amplitude` 0.18; plan `SPR-HT-FIX-24` (27.65 c/kWh,
S$32.00/month); December has 31 days.

```
seasonal   = 1 + 0.18 × cos(2π(12 − 5)/12)        = 0.844115
noise      (drawn from this document's stream)     = 0.900996
consumption = round(180000 × 0.844115 × 0.900996 × 31/30.4)
                                                   = 139,600 kWh

previous_reading = 9,913,075            (carried from the November bill)
current_reading  = 9,913,075 + 139,600  = 10,052,675

period_start  = 2024-12-01
period_end    = 2024-12-31
document_date = 2024-12-31 + 6 days     = 2025-01-06

energy_charge = round2(139600 × 27.65 / 100)      = S$ 38,599.40
fixed_charge                                       = S$      32.00
subtotal      = 38599.40 + 32.00                   = S$ 38,631.40
gst           = round2(38631.40 × 0.09)            = S$  3,476.83
total         = 38631.40 + 3476.83                 = S$ 42,108.23
```

**The answer key** (`elec_009.json`, abridged — money fields serialise as strings so the
decimals round-trip exactly):

```json
{
  "doc_id": "elec_009",
  "doc_kind": "electricity_bill",
  "generator_seed": 20261016,
  "generator_version": "synthgen-electricity-2",
  "layout_id": "elec_a",
  "is_holdout_layout": false,
  "difficulty_tier": "clean",
  "degradation": {},
  "vendor_name": "Straits Power Retail Pte Ltd",
  "vendor_uen": "SYN-0000-0001",
  "customer_name": "Kranji Cold Store Pte Ltd",
  "customer_address": "31 Neo Tiew Crescent North, #01-11, Singapore 724435",
  "account_number": "8820-3309-01",
  "document_date": "2025-01-06",
  "period_start": "2024-12-01",
  "period_end": "2024-12-31",
  "meter_number": "SP37331691",
  "previous_reading_kwh": 9913075,
  "current_reading_kwh": 10052675,
  "consumption_kwh": 139600,
  "is_estimated_reading": false,
  "tariff_plan_code": "SPR-HT-FIX-24",
  "tension": "High Tension",
  "currency": "SGD",
  "tariff_cents_per_kwh": "27.65",
  "energy_charge_sgd": "38599.40",
  "fixed_charge_sgd": "32.00",
  "subtotal_sgd": "38631.40",
  "gst_rate": "0.09",
  "gst_amount_sgd": "3476.83",
  "total_sgd": "42108.23",
  "quirks": []
}
```

---

## 6. What the generator deliberately does **not** do

**It never looks up an emission factor, and never computes emissions.** The generator produces
*activity data* (kWh) and its truth. Expected emissions are computed later by the eval harness
from that activity truth plus the verified factor table. This keeps the documents independent
of factor updates: a correction to an EMA value can never invalidate a generated document.

**No language model is involved.** No prompt, no API call, no key required. Every number is
decided in Python before anything is rendered.

---

## 7. The factor-year rule, and why `elec_009` exists

> **The emission-factor year comes from the ACTIVITY PERIOD, never from `document_date`.**

`elec_009` is the only document in the set where the two disagree: the electricity was consumed
in **December 2024**, but the bill is dated **6 January 2025**.

| Reading | Year | Factor found | What the agent would say |
|---|---|---|---|
| **Billing period** ✓ | 2024 | 0.402, exact year | "EMA 2024 grid factor, 0.402 kg CO₂/kWh" |
| `document_date` ✗ | 2025 | none → falls back to 2024 | "no 2025 factor published; substituted 2024" |

Both produce the **same tonnage**, which is exactly why it is a trap: the number looks right
while the stated method and citation are wrong. An eval that only compares tonnes passes a
pipeline that is reasoning incorrectly. **The eval must check the stated method, not only the
number.**

The rule is recorded in three places: the `ElectricityBill` module docstring, the
`bill.factor_year` property, and a test asserting `elec_009`'s three dates.

---

## 8. Determinism and stability

| Property | How it is guaranteed | How it is tested |
|---|---|---|
| Same seed → same answer keys | Label-keyed RNG streams | JSON of all 18 compared across two runs |
| Same seed → same pages | Rendering is a pure function of the key | Rendered HTML compared across two runs |
| Different seed → different output | — | Guards against a seed being accepted and ignored |
| **Adding a business changes nothing** | RNG labels and document numbers are owned by the profile | A seventh profile is *prepended*; all 18 existing keys must stay byte-identical |

**PDF bytes are not compared**, and should not be: WeasyPrint stamps a creation timestamp into
PDF metadata, so identical input yields differing bytes. Set `SOURCE_DATE_EPOCH` if
byte-identical PDFs are ever needed.

---

## 9. How it is verified

**48 automated tests pass** — 15 on the model, 30 on the generator, plus the 3 health
tests inherited from Block 1.

The model tests prove the validator is load-bearing rather than decorative: a correct bill
constructs, and each broken relation in turn (wrong consumption, wrong energy charge, wrong
subtotal, wrong GST, wrong total, period after its end, bill dated before its period closes,
an unknown field) must raise `ValidationError`.

The generator tests cover: every key validates; the account/month structure; nine bills per
factor year; the arithmetic **recomputed from raw JSON inputs without the validator** (a
validator cannot be its own witness); both worked examples asserted with script-produced
literals and injected noise; day scaling (Feb 2024 = 8,776 kWh < Mar 2024 = 9,382 kWh with
noise and seasonality held fixed); amplitude reaching the curve and being passed per profile;
noise staying inside ±10%; opening reads scaling with the business and never landing on a round
multiple; determinism and stability; plan consistency; profile coherence; synthetic
identifiers; date sanity; and the `elec_009` trap.

**One test reads the actual PDF.** For each layout it runs `pdftotext` over a rendered bill and
asserts that every printed identifier, reading, date and money figure appears, along with the
synthetic notice. `render_html` already guarantees the page can only print values from the key,
but a CSS or font fault could silently drop a column, and that guarantee is worth checking end
to end at least once.

**Three bills are also checked by hand** each time the set changes — figures transcribed from
the printed page, not from the keys, then recomputed with a calculator. All 15 checks pass on
the current set; §5 traces one of them, `elec_009`, end to end.

---

## 10. Running it

```bash
make install          # uv sync — pulls jinja2, weasyprint, faker
make docs             # generate the 18 pairs + manifest
make check            # ruff check, ruff format --check, pytest
```

```bash
uv run python scripts/generate_docs.py --help
  --kind   electricity_bill        the only kind in v0
  --seed   20261016                master seed; recorded in every key and the manifest
  --out    data/generated          output root
  --tiers  clean                   moderate/hard need the Tier 2 degradation pipeline
```

**Use `make docs`, not a bare `uv run`, on macOS.** WeasyPrint loads pango/cairo through
`dlopen`, and anaconda shadows `/opt/homebrew/lib` on the dyld search path, so the libraries
fail to load. The Makefile exports `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib`.

`git status` stays clean after `make docs`: `data/generated/*` and a global `*.pdf` rule both
apply, so no generated artefact can be committed.

---

## 11. Known limits — read this before quoting an accuracy figure

An accuracy number from this set means **"the pipeline reads bills of *this* shape correctly"**,
not "it reads Singapore electricity bills correctly". The differences, none of them implemented
in v0:

| # | v0 does | A real bill does | Path |
|---|---|---|---|
| E1 | Whole calendar-month periods | Follows the meter-read cycle: mid-month, 28–34 days | `cross_month_period` quirk |
| E2 | Shows only this period's charges | Carries a prior balance, payments received, an amount due ≠ this total, and a due date | `balance_forward` quirk |
| E3 | One fixed charge for all non-energy cost | Itemises network / transmission / market-support charges | needs a verified source first |
| E4 | Prints true kWh | HT meters are CT-scaled: register units × a multiplier | `ct_multiplier` quirk |
| E5 | Uniform noise | Roughly normal, with occasional extremes | clipped normal draw |
| E6 | Calendar days | Operating days — a business closed Sundays scales differently | per-profile operating calendar |
| E7 | HT plan is flat-rate | Capacity (kW) and peak/off-peak charges | needs verification, then an HT layout |

Also, by design in v0: **one layout, no quirks, no degradation, and no holdout layout** — a
holdout only means something once there are layouts to tune against. All three arrive in
Tier 2 Block 2.

**Unverified claims about the real world** are logged rather than assumed. In particular the
Singapore tariff *structure* (flat rate per tension category), the LT/HT supply threshold, and
the postal-sector mapping are all `secondary only` in [SOURCES.md](../SOURCES.md) #6–8. Every
rate, charge, plan name, vendor and UEN in the generator is fictional, and **no LT/HT threshold
value appears anywhere** in the code or docs — Kranji is sized to be unambiguously a large
consumer rather than to sit near any particular boundary.

---

## 12. Changing things

| To change | Edit | Then |
|---|---|---|
| A business, its scale, plan or seasonality | `CUSTOMER_PROFILES` in `profiles.py` | `make docs && make check`; re-run the hand check |
| Add a business | append to `CUSTOMER_PROFILES` with the next free `first_doc_number` | the stability test proves existing documents are untouched |
| Tariff rates or plans | `TARIFF_PLANS` in `profiles.py` | worked-example literals in `tests/test_synthgen.py` will need re-running |
| Seasonality, noise, month length | constants at the top of `electricity.py` | every consumption value changes |
| The page itself | `templates/electricity_bill_a.html.j2` | keep arithmetic and date literals out; the PDF text-layer test will catch a dropped field |
| GST | `GST_RATE` in `.env` | it is read from settings and written into every key |

**Never type a literal by hand.** Every number in a test or a worked example must be pasted
from a script that was actually run. The first draft of this generator shipped a worked example
of 10,029 kWh where the inputs give 10,025.8, and several downstream literals were built on it.
