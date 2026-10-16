# synthgen Specification: Synthetic Bills, Invoices and Receipts (Tier 2 and 3)

> **What this file is.** The rulebook for every synthetic document Carbon R generates after
> Tier 1 Block 2. It compiles the design, the decisions and every mistake caught while building
> `synthgen` v0 (electricity bills), so the next document types don't repeat them.
>
> **Who reads it.** Claude Code, before planning any synthgen work; Aaron, when reviewing that
> plan.
>
> **How to use it.** Read Part 1 (rules) and Part 2 (lessons) in full. Then read the section for
> the document type being built (Part 4). Every plan must say which rules it relies on and must
> pass the review checklist in Part 9 before any code is written.
>
> Anything marked **⚠️ VERIFY** must be checked against a primary source before it is stated in
> code comments, `SOURCES.md`, the README or the resume. Plausible-but-fictional numbers are
> fine in documents labelled synthetic; claims about how real Singapore documents work are not.

---

## Part 0: The idea in one paragraph

Real bills and invoices are private, few, and have no answer key. So Carbon R **manufactures**
them. The generator first decides the correct values in Python (the answer key), then draws a
document from those values (the exam question). Extraction later reads the document without
seeing the key, and the eval compares the two.

```
synthgen   :  answer   ──►  document      (decide the truth, then draw it)
extraction :  document ──►  answer        (the model sits the exam)
eval       :  do the two answers match?   (marking)
```

Every rule below exists to make that comparison trustworthy.

---

## Part 1: Non-negotiable rules

These apply to every document type, every tier.

### 1.1 Truth comes first, and only from code
- Every number on a document is decided in Python **before** rendering, and the JSON answer key
  holds exactly those numbers.
- No LLM computes, chooses or checks a number. An LLM may only add **text variety** (e.g. line-item
  descriptions like "Frozen chicken thigh 2kg pack"), and if it does, **a different model (or
  none) does extraction**. Don't grade a model with its own homework.
- The template does **no arithmetic and no date literals**. It prints pre-formatted strings from
  a validated model instance only.

### 1.2 Money is exact
- All money and rate fields are `Decimal`, never `float`.
- Construct from strings: `Decimal("0.09")`, `Decimal(str(setting))`. Never `Decimal(0.09)`.
- Rounding uses the one shared helper, `carbon_r.money.round2` (`ROUND_HALF_UP`, to cents). Never
  define a second rounding function anywhere.
- Round **only where a real vendor rounds**: each line amount, and the GST amount. Subtotals and
  totals are plain sums of already-rounded figures and are never re-rounded.
- `gst_rate` comes from `get_settings()` and is written into each answer key so the key is
  self-describing.

### 1.3 Every answer key is self-checking, and also checked by someone else
- Each document type has a frozen Pydantic v2 model (`frozen=True, extra="forbid"`) with a
  `@model_validator(mode="after")` that re-derives **every** arithmetic relation on the document.
  An inconsistent key must be unconstructible.
- A separate test recomputes the totals from the JSON's raw inputs **without** using the model's
  validator. A validator cannot be its own witness.

### 1.4 Reproducible, and stable when the set grows
- One master seed per generator (recorded in `manifest.json` and the README).
- Each document gets its **own** RNG, seeded from a formatted label:
  `random.Random(f"{seed}:{account}:{year}-{month:02d}:{doc_kind}")`.
  Adding a new profile must not change any existing document.
- Never seed with `hash(...)`. `PYTHONHASHSEED` randomises string hashes per process.
- Determinism tests compare **JSON and rendered HTML**, not PDF bytes. WeasyPrint stamps a
  timestamp into PDF metadata (set `SOURCE_DATE_EPOCH` if byte-identical PDFs are ever needed).
  Degraded images must also be reproducible: seed the degradation pipeline from the same label.

### 1.5 Tests never depend on luck
- Functions that draw randomness accept **injectable** values (`noise=`, `seasonal=`, etc.) so a
  test can fix them. A test that asserts on whatever the RNG happened to draw is testing the RNG.
- Never write a test whose outcome depends on noise being smaller than the effect being tested.
  Test the effect directly in the function that produces it.

### 1.6 No literal is typed by hand
- Every number in a worked example, unit test or plan is **pasted from a script that was run**,
  never computed mentally. If a plan says "computed, not estimated", the figure must be exactly
  what the script printed.
- When a reviewer supplies a cross-check figure, run it and **explain any disagreement** rather
  than accepting either side.

### 1.7 Fictional, labelled, and nothing real
- Fictional vendors, customers, accounts, meters, plates and UENs. No real company names, logos
  or branding (no SP Group, City Energy, real banks, real fuel brands).
- Identifiers should be **impossible** to collide with real ones where practical (e.g. a UEN
  pattern that cannot be valid, plates with a prefix that cannot be issued). ⚠️ VERIFY the formats
  you choose are actually invalid before claiming so.
- Footer on every page: `SYNTHETIC DOCUMENT - generated by Carbon R synthgen for testing. Not a
  real document. All parties and figures are fictional.` plus generator version and seed.
- Aaron's own bills may be used to check **which fields exist**. Never copy a layout, never commit
  one.

### 1.8 The answer key records the mess, not just the truth
- `quirks: list[str]` on every key, naming each injected irregularity (empty for clean docs).
- `layout_id`, `difficulty_tier` and `degradation` (parameters used) are recorded on every key.
- Where a quirk makes the printed value differ from the true value, the key stores **both**
  (see Part 5.2).

### 1.9 Repo hygiene
- Output goes to git-ignored `data/generated/<doc_kind>/`. PDFs and images are never committed.
- `git status` must be clean after `make docs`. The remote is public.

---

## Part 2: Lessons from building synthgen v0 (electricity, Tier 1 Block 2)

Every item below was a real mistake or near-miss. Each has a rule above or a check in Part 9.

| # | What went wrong | Lesson / rule |
|---|---|---|
| 1 | Plan's headline worked example said 10,029 kWh; the inputs give ~10,026. All downstream literals were built on it. | Rule 1.6. Run the numbers; never trust a plan's arithmetic, including the reviewer's. |
| 2 | Reviewer's own cross-check used the old ±12% seasonal swing after changing the laundry's to ±8%. Claude Code caught it by running the script. | When two corrections arrive together, recompute with **all** of them applied. |
| 3 | Consumption ignored month length, so 29-day February ≈ 31-day March. | Quantities that accrue over time scale with **days in the period**. Base figures are defined per *average-length* month (30.4 days). |
| 4 | Same seasonal swing for every business. | Behavioural parameters (seasonality, noise) belong to the **profile**, not to global constants. |
| 5 | A ~14,500 kWh/month company on a High Tension plan. | Profiles must be internally coherent: tariff category, consumption scale and business type must agree. Size HT accounts at large-consumer scale (Kranji = 180,000 kWh/month). |
| 6 | Plan claimed a December/January period boundary that didn't exist. | Claims about what a test set "exercises" must be checked against the generated documents, and backed by a test. |
| 7 | Discovered instead: doc_009 has period Dec 2024 but bill date 4 Jan 2025. | **Emission-factor year comes from the billing period, never the bill/invoice date.** Keep a document that distinguishes them in every set. |
| 8 | "31 Apr" appeared in a plan example. | No hand-written dates anywhere. All dates come from `calendar.monthrange` / model fields. |
| 9 | Test "Kranji's seasonal spread > Serangoon's" would have passed or failed by luck (0.024 vs 0.025, with ±10% noise). | Rule 1.5. Test functions directly with injected values. |
| 10 | Opening meter reads were `base_kwh × integer months`, so always round numbers. | Draw continuous values (`uniform`) for anything that should look organic. Round numbers are a fake-data tell. |
| 11 | Plan edits left two versions of a function and a second copy of `round2`. | After revising a plan, re-read it top to bottom for stale signatures, duplicated helpers and orphaned table rows. |
| 12 | WeasyPrint couldn't find pango on macOS (anaconda shadowed Homebrew). | Environment fixes go **in the Makefile target**, not only in notes. Currently `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib`. |
| 13 | Faker gave a car workshop a 7th-floor unit, a Serangoon address a far-west postal code, and a real-looking UEN. | Faker output must be constrained by profile: ground-floor units for F&B/workshops/cold stores; postal-code prefix matching the address area (⚠️ VERIFY the prefix mapping); impossible identifiers. |
| 14 | Random wobble made April > May for the laundry despite May being peak. | Not a bug. Noise larger than a seasonal swing is realistic. Don't "fix" realistic variation to make examples tidy. |
| 15 | `iras.gov.sg` and `ema.gov.sg` HTML pages return nothing to a fetcher. | Use the publisher's PDF or Excel download as the primary source. Cite edition and page. |

Verified v0 state (16 Oct 2026): 18 bills, all arithmetic and meter chains independently rechecked
to the cent. Hand-check figures: doc_003 total S$1,215.38; doc_009 S$43,292.37; doc_017 S$2,183.52.

---

## Part 3: Shared architecture

### 3.1 Package layout

```
src/carbon_r/
  money.py                         round2, CENT (the only rounding definition)
  models/
    electricity_bill.py            (exists)
    lpg_invoice.py                 Tier 2
    fuel_card_statement.py         Tier 2
    purchase_invoice.py            Tier 3 (spend-based Scope 3)
  synthgen/
    profiles.py                    customer profiles, vendors, plans/price lists
    rng.py                         doc_rng(label), shared injectable draw helpers
    electricity.py                 (exists)
    lpg.py, fuel_card.py, purchase.py
    quirks.py                      quirk catalogue + injectors (Tier 2 Block 2)
    degrade.py                     Augraphy / OpenCV pipeline (Tier 2 Block 2)
    render.py                      Jinja2 env + WeasyPrint; one function for all doc kinds
    templates/<doc_kind>_<layout>.html.j2
scripts/generate_docs.py           --kind, --seed, --out, --count, --tiers
tests/
```

### 3.2 Output layout

```
data/generated/
  <doc_kind>/
    clean/      doc_XXX.pdf + doc_XXX.json
    moderate/   doc_XXX.pdf|.png + doc_XXX.json
    hard/       doc_XXX.png + doc_XXX.json
    manifest.json     seed, generator_version, counts per tier/layout/quirk, citations used
```

Document IDs are unique **within a kind** and stable across runs. Prefix by kind if a single
eval ever mixes kinds (`elec_001`, `lpg_001`, `fuel_001`, `purch_001`).

### 3.3 Common answer-key fields (every document kind)

| Field | Type | Notes |
|---|---|---|
| `doc_id` | str | |
| `doc_kind` | Literal | `electricity_bill`, `lpg_invoice`, `fuel_card_statement`, `purchase_invoice` |
| `schema_version` | Literal | bump when fields change |
| `generator_seed`, `generator_version` | int, str | |
| `layout_id` | str | e.g. `lpg_b` |
| `is_holdout_layout` | bool | true for the one layout never used during tuning |
| `difficulty_tier` | Literal["clean","moderate","hard"] | |
| `quirks` | list[str] | from the catalogue only; validated against it |
| `degradation` | dict | applied effects and their parameters; `{}` for clean |
| `vendor_*`, `customer_*` | str | |
| `document_date` | date | invoice/bill/statement date. **Never** the factor year. |
| `period_start`, `period_end` | date | the activity period. **This** decides the factor year. |
| `currency` | Literal | `SGD` (or `USD` for the `usd_invoice` quirk) |
| `gst_rate` | Decimal | from settings |

### 3.4 What the generator does NOT know

The generator produces **activity data** (kWh, kg, litres, dollars) and its truth. It does
**not** look up emission factors or compute emissions. Expected emissions for calculation evals
are computed later by the eval harness from the activity truth plus the verified factor table.
This keeps the generator independent of factor updates, and means a factor correction never
invalidates a document.

---

## Part 4: Document types

### 4.1 Electricity bill (exists; Tier 2 extends it)

Already built. Relations: `consumption = present − previous`; `energy = round2(kWh × c/kWh ÷ 100)`;
`subtotal = energy + fixed`; `gst = round2(subtotal × rate)`; `total = subtotal + gst`.
Consumption = `base × seasonal(profile amplitude) × noise × days ÷ 30.4`, rounded to int.

Tier 2 additions: 3–4 layouts (one held out), quirks from Part 5, the realism backlog in Part 7
items E1–E7 as they become quirks.

### 4.2 LPG invoice (Tier 2, Scope 1)

**Who uses it.** F&B (Kopi Corner, catering), laundries with gas dryers, small kitchens.

**Typical shape** (⚠️ VERIFY against the general structure of Singapore LPG supplier invoices;
model the shape only, never a real supplier's layout):

| Line | Example | Notes |
|---|---|---|
| Cylinders delivered | 4 × 50 kg | quantity × size. Sizes plausibly 12.7 / 14 / 50 kg (⚠️ VERIFY common sizes). |
| Price per cylinder **or** per kg | S$165.00 / cylinder | price list belongs to the **vendor**, like tariff plans (Part 2 lesson 4) |
| Delivery charge | S$10.00 | decoy money line |
| Cylinder rental / deposit | S$5.00 × 4 | decoy, **not** fuel |
| Empty cylinders returned | 4 | decoy count |
| GST 9% | | |

**Truth fields:** `cylinders: list[{size_kg, count}]`, `lpg_kg` (derived = Σ size × count),
line amounts, subtotal, GST, total.

**Relations:** `lpg_kg = Σ size_kg × count`; each line `= round2(qty × unit price)`;
`subtotal = Σ lines`; `gst = round2(subtotal × rate)`; `total = subtotal + gst`.

**Consumption model:** kg per month from profile base × seasonal (usually small amplitude:
cooking load) × noise × days ÷ 30.4, then **rounded up to whole cylinders**. Delivery frequency
can be per-delivery invoices rather than monthly (realistic, and it makes `missing_month`
meaningful).

**Key extraction trap:** the answer is **kg of LPG**, which may not be printed at all
(`lpg_cylinders` quirk). Rental and deposit lines are not fuel.

**Realism note to verify:** many Singapore F&B outlets use **piped town gas billed in kWh**
rather than LPG cylinders. ⚠️ VERIFY before claiming either is typical. If town gas is common,
consider a `town_gas_bill` kind in Tier 3; it needs a different emission factor and is a good
"same unit, different fuel" trap against electricity.

### 4.3 Fuel-card statement (Tier 2, Scope 1)

**Who uses it.** Businesses with vans: catering deliveries, laundry pickup, auto services.

**Shape:** a monthly statement listing transactions.

| Column | Example | Notes |
|---|---|---|
| Date / time | 03 Mar 2026 07:42 | inside the statement period |
| Vehicle | fictional plate | per profile fleet; use a format that cannot be real (⚠️ VERIFY) |
| Station | fictional station name | |
| Product | Diesel / Petrol 95 | `mixed_fuel` quirk mixes them |
| Litres | 62.41 | 2 dp |
| Pump price / L | S$2.89 | from a vendor price list by month |
| Amount | S$180.36 | `round2(litres × price)` |
| Discount / rebate | −S$12.63 | decoy; reduces money, **not** litres |

Statement totals: litres per product, gross amount, discounts, net payable. **GST treatment:**
⚠️ VERIFY whether Singapore pump prices are GST-inclusive and how fuel-card statements show GST.
Choose one convention, document it, and make the validator enforce it.

**Truth fields:** transactions list; `litres_by_product: dict[product, Decimal]` (derived);
money totals.

**Relations:** each amount = `round2(litres × price)`; product litre totals = Σ litres per
product; net = gross − discounts (+ GST if exclusive).

**Generation model:** fleet size × fills per vehicle per week (Poisson-like draw, seeded) ×
litres per fill (uniform within tank size). Prices drawn once per month per product, shared by
all customers of that vendor.

**Key extraction traps:** summing litres across products (wrong: different factors), using money
instead of litres, counting discount lines, vehicles serving other clients (allocation, see
Makan Mates).

### 4.4 Purchase invoice (Tier 3, spend-based Scope 3)

**Purpose:** test `match_factor_category`, spend totals and `convert_currency`.

**Shape:** supplier invoice with line items: description, quantity, unit, unit price, amount;
subtotal, GST, total; currency; invoice date.

**Truth fields:** line items each with `true_category` (the US EPA category the generator
intended, ⚠️ VERIFY category names against the loaded EPA file), `amount`, `currency`;
totals; `invoice_date` (drives the FX date).

- The generator **chooses the category first**, then picks or writes a description for it. That
  chosen category is the ground truth for `match_factor_category`.
- Descriptions may come from a curated list or an LLM (text variety only, Rule 1.1). Include
  deliberately **ambiguous** items (e.g. "cleaning supplies", "disposables") and record
  `ambiguity: low|high` so the eval can expect "please categorise this item".
- USD invoices (`usd_invoice` quirk) record the currency only. The **FX rate is not in the
  answer key**; the eval fetches and freezes it, per the data plan.

### 4.5 Company-year bundle (Tier 2 Block 3 eval scenario)

For the calculation eval (Makan Mates and ~20 variations), add a **scenario generator** that
produces a full year for one company: 12 electricity bills, LPG invoices, fuel-card statements,
plus a `scenario.json` with revenue, contract share and the truth activity totals.

- Expected emissions are computed by the eval harness from activity truth × **verified** factors
  from the DB (Part 3.4), **not** copied from the project context. The context file's
  30.1 / 39.4 / 21 t figures used approximate factors and are illustrative only.
- Variations: a missing month, an estimated reading, mixed SGD/USD spend, a different industry,
  a contract share of 0% and 100% (edge cases for allocation).

---

## Part 5: Quirks

### 5.1 Catalogue

Each quirk is **controlled** (injected at a chosen rate) and **recorded** (named in `quirks`).

| Quirk | Applies to | What changes on the page | What the key records | Tests |
|---|---|---|---|---|
| `estimated_reading` | electricity | "ESTIMATED" basis | `is_estimated_reading=true` | flagged, not silently trusted |
| `cross_month_period` | electricity | period e.g. 15 Mar – 14 Apr | per-calendar-month split by days | pro-rating, factor year per month |
| `unit_mwh` | electricity | consumption printed in MWh | printed value + unit; true kWh | unit conversion (1000× errors) |
| `missing_month` | any series | one month absent from the set | scenario lists the gap | noticing gaps across a year |
| `lpg_cylinders` | LPG | only "4 × 50 kg", no total kg | true `lpg_kg` | computing kg, not reading it |
| `credit_adjustment` | electricity, LPG | credit line for a prior overcharge | true period activity excludes it | no double-counting |
| `mixed_fuel` | fuel card | diesel + petrol rows | litres by product | separate factors |
| `usd_invoice` | purchase, LPG | currency USD | currency, invoice date | triggers FX |
| `multiple_premises` | electricity | two meters on one bill | activity per meter + total | not taking only the first |
| `gst_confusion` | any | GST-inclusive figures shown prominently, or GST before discount | the correct pre-/post-GST values | amounts with vs without tax |
| `cross_year_period` | electricity | period 15 Dec – 14 Jan | per-year split by days | two factor years in one bill |
| `ct_multiplier` | electricity (HT) | register units × multiplier | true kWh | multiplying, not reading the register |
| `balance_forward` | electricity, LPG | prior balance, payment received, amount due | this period's charges only | "amount due" ≠ period total |

Start with `estimated_reading`, `cross_month_period`, `unit_mwh`, `lpg_cylinders`. Add the rest
once the eval harness reports per-quirk accuracy.

### 5.2 Printed vs true values

When a quirk makes the page differ from the truth, the key holds both:

```json
{
  "doc_id": "elec_042",
  "quirks": ["cross_month_period", "unit_mwh", "estimated_reading"],
  "printed": {"consumption": "8.24", "consumption_unit": "MWh",
              "period_start": "2026-03-15", "period_end": "2026-04-14"},
  "truth": {"consumption_kwh": 8240, "is_estimated_reading": true,
            "monthly_split_kwh": {"2026-03": 4669, "2026-04": 3571}}
}
```

- The split is computed by **days** (17 March days, 14 April days → 31 days total), rounded so
  the parts sum exactly to the whole (assign the rounding remainder to the last month). The
  numbers above are illustrative; real ones come from the script (Rule 1.6).
- Extraction is scored against `printed` (did it read the page?), and the pipeline against
  `truth` (did it interpret it correctly?). Report both. They fail for different reasons.

### 5.3 Injection rules
- Quirks are applied to the truth model **before** rendering, by functions in `quirks.py` that
  return a new validated model. The validator must understand each quirk (e.g. with
  `ct_multiplier`, `consumption = (present − previous) × multiplier`).
- Rates are configured per tier (e.g. moderate = 1–2 quirks, hard = 3+), drawn from the
  document's own RNG so adding a quirk type doesn't reshuffle others.
- Never inject a quirk combination that is impossible on a real document (e.g. `unit_mwh` on a
  tiny dental clinic is fine; `ct_multiplier` on a Low Tension account is not).

---

## Part 6: Layouts, degradation, tiers and sizing

### 6.1 Layouts
- 3–4 layouts per document kind. Vary: field order, table vs label-value, label wording
  ("Consumption" / "Units used" / "kWh billed"), date formats (`04 Dec 2025`, `04/12/2025`,
  `2025-12-04`), thousands separators, where the total sits.
- **One layout per kind is held out** (`is_holdout_layout=true`). It is never looked at while
  tuning prompts. Its accuracy is reported separately. This is the honest number.

### 6.2 Degradation (moderate and hard tiers)
- Pipeline: render PDF → rasterise (`pdf2image`) → Augraphy (ink bleed, paper texture, shadows)
  and/or OpenCV/Pillow (rotate 1–3°, blur, downscale, JPEG compression, uneven lighting).
- Seed every effect from the document label; record effect names and parameters in
  `degradation`. Keep the clean PDF alongside the degraded image for debugging.
- Hard tier = phone-photo look: perspective skew, shadow, lower resolution.
- Check by eye that degraded text is still **humanly readable**. An unreadable document has no
  fair answer and pollutes the eval.

### 6.3 Tiers
| Tier | Quirks | Rendering |
|---|---|---|
| clean | none | crisp PDF |
| moderate | 1–2 | some scanned |
| hard | 3+ | phone-photo effects |

### 6.4 Sizing
- **100–200 documents in total** across kinds and tiers, not 50. With 10 hard documents, one error
  moves accuracy by 10 points.
- **Variety beats volume**: 50 documents over 4 layouts and many quirk combinations test more than
  200 from one layout.
- Always report **counts with percentages** ("37 of 50, 74%").

---

## Part 7: Realism backlog (known limits)

Recorded so accuracy claims stay honest: results describe *these modelled formats*, not real
Singapore documents. Promote an item to a quirk or refinement when it's worth the time.

| # | Limit in current generator | What real documents do | Path |
|---|---|---|---|
| E1 | Calendar-month periods | Meter-read cycles, mid-month, 28–34 days | `cross_month_period` |
| E2 | No balance-forward / amount-due / due date | Several more money figures | `balance_forward` |
| E3 | No network/transmission charges | Itemised separately from energy | ⚠️ VERIFY structure first |
| E4 | No CT multiplier on HT | Register × multiplier | `ct_multiplier` |
| E5 | Uniform noise | Roughly normal, with occasional extremes | clipped normal draw |
| E6 | Calendar days, not operating days | Closed Sundays/public holidays | per-profile operating calendar |
| E7 | HT plan is flat-rate | Capacity (kW) and peak/off-peak charges | ⚠️ VERIFY, then HT layout |
| G1 | Faker units/postal codes unconstrained | Ground floor for F&B/workshops; postcode matches area | constrain in `profiles.py` |
| G2 | Real-looking UEN | | impossible identifier pattern |
| G3 | Every bill dated exactly 4 days after period end | Varies by a few days | per-doc lag draw |

---

## Part 8: Tests every document kind must have

1. A hand-written correct document constructs; each broken relation raises `ValidationError`
   (proves the validator is load-bearing).
2. `Decimal` fields round-trip through JSON unchanged.
3. Every generated JSON validates against its model.
4. Same seed → identical JSON and HTML. Different seed → different output.
5. Adding a profile does not change any existing document (stability test).
6. Independent arithmetic recheck from raw JSON inputs, without the validator.
7. Worked examples asserted with **script-produced** literals and **injected** random values.
8. Each quirk: the injector produces a document whose `printed` and `truth` differ in exactly the
   intended way, and the validator accepts it.
9. Coherence: every value printed on the page appears in the key (render-from-model guarantees
   this; test it once per layout by extracting the PDF text layer).
10. Price-list consistency: same vendor + same price list → same unit prices.
11. Date sanity: `period_start ≤ period_end ≤ document_date`, and at least one document per kind
    where the period year ≠ the document-date year.
12. Holdout: exactly one layout per kind is marked holdout, and the tuning scripts exclude it.

All tests pass with no `.env`, no database and no API key.

---

## Part 9: Process and review checklist

### 9.1 Workflow per block
1. **Plan first** (plan mode). Show the plan; Aaron reviews before code.
2. **Run every literal** in the plan through a scratch script and paste the output.
3. Implement; `make docs`; `make check`.
4. **Hand check 3 documents** with a calculator, spanning different profiles, years and layouts.
   Record the actual figures in the local build log, not "checked".
5. Open a few PDFs/images and look: is anything implausible (addresses, scale, dates)?
6. Update `SOURCES.md` (new VERIFY items) and the local build log (decisions, traps,
   realism limits); commit.

### 9.2 Plan review checklist (Aaron, before approving)
- [ ] Every worked-example number came from a script, and the plan says so.
- [ ] Every test asserts a fixed answer, not something depending on random draws.
- [ ] No duplicated helpers, stale signatures or leftover old versions in the plan.
- [ ] Profiles are coherent (business type, scale, tariff/price list, address).
- [ ] Factor year is taken from the activity period, never the document date.
- [ ] Every claim about how *real* Singapore documents work is either VERIFIED with a source or
      logged as ⚠️ VERIFY.
- [ ] Quirks are recorded in the key; printed vs true values are both stored where they differ.
- [ ] A holdout layout exists and is excluded from tuning.
- [ ] Environment fixes live in the Makefile.
- [ ] Nothing real: names, logos, identifiers, layouts.

---

## Part 10: Open VERIFY items for synthetic documents

- [ ] Singapore non-residential electricity tariff structure (flat rate per tension category?) *(SOURCES.md #6)*
- [ ] LT/HT supply threshold (state none until verified) *(SOURCES.md #7; none stated anywhere)*
- [ ] Network/transmission charge structure on retailer bills
- [ ] Common LPG cylinder sizes and how LPG invoices price them (per cylinder vs per kg)
- [ ] Whether piped town gas (billed in kWh) is more common than LPG for SME F&B
- [ ] GST treatment of pump prices and fuel-card statements
- [ ] Postal-code prefix ↔ area mapping used to constrain Faker *(in use from secondary sources; SOURCES.md #8)*
- [ ] Identifier formats (UEN, vehicle plate) that are guaranteed invalid *(ACRA UEN page returns 403; v0 sidesteps it with the self-evidently synthetic `SYN-0000-0001`)*
- [ ] US EPA supply-chain category names matching the loaded factor file
- [ ] Current GST rate (verified 9% from IRAS e-Tax Guide, 17th ed., 30 Jan 2026; re-check before release)

---

## Part 11: Starter prompt for Claude Code

> Read `SYNTHGEN_SPEC.md` in full, then `CLAUDE.md` and the local build log. We are starting
> **[Tier 2 Block 1: LPG invoices and fuel-card statements]**. In plan mode, write a plan that
> follows Parts 1, 3, 4.2, 4.3 and 8 of the spec. For each rule in Part 1, say where the plan
> satisfies it. Start with one layout per kind and no quirks; quirks, extra layouts and
> degradation come in Tier 2 Block 2. Every number in the plan must be pasted from a script you
> ran. List any new ⚠️ VERIFY items. Show me the plan before writing code.

Adjust the bracketed block name per session. For Tier 2 Block 2, point it at Parts 5 and 6; for
Tier 3 spend-based Scope 3, Part 4.4; for the calculation eval, Part 4.5.
