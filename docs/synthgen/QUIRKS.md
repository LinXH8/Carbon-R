# Quirks — what you will see on a synthetic document, by synthgen version

This is a field guide for anyone opening a generated PDF or image. For each version of the
generator it says what a page looks like, which irregularities ("quirks") it carries, where
on the page each one appears, what each is designed to test, and what the correct reading
is.

The **answer key is always the truth.** Every page sits beside a `.json` key that holds
every printed value, plus the values a correct reading must arrive at. The fields to look at
first are `quirks`, `difficulty_tier`, `layout_id`, `is_holdout_layout` and `degradation`.
Where this guide and a key disagree, the key wins, and this guide has a bug.

Related documents: [SYNTHGEN_SPEC.md](SYNTHGEN_SPEC.md) holds the rules every generator
follows. [TIER2_BLOCK2.md](../blocks/TIER2_BLOCK2.md) explains why v1 is built the way it is. The
catalogue the code enforces is `QUIRKS` in
[src/carbon_r/models/quirks.py](../../src/carbon_r/models/quirks.py).

---

## Versions at a glance

| | synthgen v0 | synthgen v1 |
|---|---|---|
| Built in | Tier 1 Block 2 (electricity), extended in Tier 2 Block 1 (LPG, fuel cards) | Tier 2 Block 2 |
| Generator versions | `synthgen-electricity-2`, `synthgen-lpg-1`, `synthgen-fuel-card-1` | `synthgen-electricity-3`, `synthgen-lpg-2`, `synthgen-fuel-card-2` |
| Answer-key `schema_version` | `"1"` | `"2"` |
| Where it is | `data/generated/<kind>/clean/` | `data/generated/synthgen_v1/<kind>/<tier>/` |
| Documents | 36: `elec_001–018`, `lpg_001–012`, `fuel_001–006` | 198: `elec_001–093`, `lpg_001–051`, `fuel_001–054` |
| Layouts | one per kind (`_a`) | four per kind (`_a` to `_d`); `_d` held out |
| Tiers | `clean` only | `clean`, `moderate`, `hard` |
| Quirks recorded in the key | **none** (the list is empty on every key) | 9 quirks, validated against the catalogue |
| Images | none; every document is a crisp PDF | moderate: PDF or scan PNG; hard: phone-photo PNG |

The same seed (`20261016`) drives both versions. **A document id does not mean the same
document in both versions.** For example, `lpg_007` is a dental clinic's invoice in v0 and
a catering kitchen's in v1. Always say which version you are looking at.

---

# synthgen v0

## What you will see

A crisp, single-page A4 PDF in one layout per kind. Every page carries a dashed yellow
"SYNTHETIC DOCUMENT" banner at the top and the same notice in the footer, together with the
generator version and seed.

| Kind | What is on the page |
|---|---|
| Electricity bill (`elec_a`) | Navy header; "Billed to" and "Account" panels; a meter table (previous reading, present reading, consumption in kWh, basis "Actual"); a charges table (consumption × c/kWh, monthly service charge, subtotal before GST, GST 9%, total payable). Dates like `04 Dec 2025` |
| LPG invoice (`lpg_a`) | Brown header; one cylinder line ("LPG cylinder, 50 kg", count, size, price each), delivery charge, cylinder rental, empty cylinders collected (a count with a dash for money), subtotal, GST, total |
| Fuel-card statement (`fuel_a`) | Green header; a transaction table (date, time, vehicle, station, product, litres, price/L, amount); a "Summary by product" table; gross amount, fleet rebate, amount before GST, GST, net payable |

## Quirks

The `quirks` list is **empty on all 36 keys**: v0 injected no irregularities, and every
document is `clean`.

Two behaviours that v1 records as quirks are nevertheless already on these pages. v0 did
not record them:

- **Every LPG invoice prints cylinder counts and sizes but no total kg.** That is the
  behaviour v1 calls `lpg_cylinders`. The kg (count × size) has to be computed.
- **`fuel_001–003` (Jurong Auto) list both Diesel and Petrol 95.** That is v1's
  `mixed_fuel`. Two products need two emission factors, and their litres must not be added
  together.

## Known flaws you may notice (fixed in v1, kept here for the record)

- `lpg_007–009` are LPG invoices for **Serangoon Dental Group**, an upper-floor clinic that
  would not cook with gas.
- Every LPG invoice prints price list **`IGS-COM-26`**, including `lpg_001`, whose supply
  period is December 2024. The fuel statements print `SFC-PUMP-26` on 2025 periods.
- Each LPG account receives the same cylinder count almost every month.
- The fuel statement header prints "Fleet fuel purchases" twice.

---

# synthgen v1

## How to read a v1 document

### Finding it

Each kind has a `manifest.json`, and each document's entry has a `file` field naming the
page to open, for example `hard/elec_024.png`. For a scanned or photographed document there
are three files side by side:

```
elec_024.png          the degraded page — what the pipeline actually reads
elec_024.clean.pdf    the crisp page it was made from — for debugging only
elec_024.json         the answer key
```

### Tiers: what the page looks like

| Tier | Injected quirks | What you see |
|---|---|---|
| `clean` | none | A crisp PDF |
| `moderate` | 1 or 2 | 25 of the 59 are crisp PDFs; the other 34 are **scans**: a PNG tilted by 0.2–0.8°, lightly blurred, with uneven lighting from one edge and JPEG artefacts |
| `hard` | 4 on electricity, all 3 on LPG and fuel | A **phone photo**: a PNG with the top edge pulled in (keystone), a shadow across the page from one side, a 0.3–1.0° tilt, blur, reduced resolution and stronger JPEG artefacts. A grey-beige border shows around the page |

Every effect's exact parameters are in the key's `degradation` field. For example:

```json
{"rendering": "photo", "keystone": 0.015, "rotate_deg": -0.895,
 "shadow": {"direction": "right", "strength": 0.354}, "blur_radius": 0.723,
 "downscale": 0.72, "jpeg_quality": 51}
```

A moderate document left as a crisp PDF has `{"rendering": "pdf"}`. A clean document has
`{}`.

**On a photo, tilt shifts the right-hand figures down slightly relative to their labels.**
Follow the row's rule line, not the horizontal. The tilt range was narrowed until every
value can still be paired with its label by eye.

### Layouts

Four per kind. **Layout `d` is the holdout**: it is never looked at while prompts are
tuned, it is excluded from `make eval` and the flow checks by default, and it is scored
separately. If you are tuning anything, do not open `_d` documents.

| Layout | Style | Date format | Notable wording |
|---|---|---|---|
| `elec_a` | Navy, tables, total at the bottom | `04 Dec 2025` | "Consumption", "Subtotal before GST", "Total payable" |
| `elec_b` | Serif, label/value rows, an **"Amount payable" box at the top right** | `04/12/2025` | "Units used", "Opening/Closing reading", "Price plan", "Total before GST" |
| `elec_c` | Monospaced, upper case; the total in a box at the bottom **left** | `2025-12-04` | "UNITS BILLED", "NET AMOUNT", "TOTAL THIS BILL" |
| `elec_d` *(holdout)* | Purple band; account facts in a sidebar, charges on the right | `04 December 2025` | "Energy consumed", "Charges excluding GST", "Charges payable" |
| `lpg_a` | Brown, tables; each cylinder line shows "delivered <date>" under it | `04 Dec 2025` | "Cylinder rental", "Empty cylinders collected" |
| `lpg_b` | Serif, docket style; **prints a total kg** | `04/12/2025` | "Cylinder hire … (not gas)", "Delivery, N trip(s)", "Total LPG delivered" |
| `lpg_c` | Monospaced, upper case | `04-12-2025` | "LPG 50KG CYL DELIVERED …", "CYL RENTAL", "EMPTIES RETURNED" |
| `lpg_d` *(holdout)* | Teal band and sidebar; **prints a total kg** | `04 December 2025` | "Net LPG supplied", "Delivery (N drop(s))" |
| `fuel_a` | Green; transactions first, then a summary by product | `04 Dec 2025` | "Fleet rebate", "Amount before GST", "Net payable" |
| `fuel_b` | Serif; **summary first**, transactions last | `2025-12-04` | "Volume (L)", "Fleet discount", "Statement total" |
| `fuel_c` | Monospaced, upper case | `04/12/2025` | "PRODUCT TOTAL", "REBATE", "TOTAL PAYABLE" |
| `fuel_d` *(holdout)* | Slate band; the vehicle is the first column of the transactions | `04 Dec 25` | "Grade", "Volume rebate", "Statement amount" |

### Two kinds of quirk

- **Structural** quirks describe what the document *is*: `lpg_cylinders` and `mixed_fuel`.
  They are recorded whenever they are true, **in every tier including clean**, and they do
  not count toward a tier.
- **Injected** quirks are placed by the generator, and only they decide the tier.

The `quirks` list in a key holds both kinds, in catalogue order.

**Read the tiers with this in mind.** For LPG and fuel, every injected quirk is a money
decoy, and the traps that change the activity (cylinders, two fuels) are structural. A hard
LPG or fuel document is therefore *harder to read*, not *harder to interpret*. And because
those kinds have exactly three injected quirks, every hard LPG or fuel document carries all
three. A failure there cannot be blamed on any one of them.

### How many documents carry each quirk

| Quirk | Class | Applies to | All v1 documents | Non-holdout (moderate / hard) |
|---|---|---|---|---|
| `estimated_reading` | injected | electricity | 27 | 24 (7 / 17) |
| `cross_month_period` | injected | electricity | 15 | 15 (7 / 8) |
| `cross_year_period` | injected | electricity | 15 | 15 (8 / 7) |
| `unit_mwh` | injected | electricity | 27 | 24 (6 / 18) |
| `credit_adjustment` | injected | electricity · LPG · fuel | 28 · 20 · 25 | 24 · 16 · 19 |
| `balance_forward` | injected | electricity · LPG · fuel | 27 · 19 · 24 | 24 · 15 · 19 |
| `gst_confusion` | injected | electricity · LPG · fuel | 26 · 19 · 23 | 23 · 15 · 18 |
| `lpg_cylinders` | structural | LPG | 32 | 32 (19 clean, 6 / 7) |
| `mixed_fuel` | structural | fuel | 31 | 24 (11 clean, 6 / 7) |

`make docs-plan` prints the non-holdout table. The "all documents" counts come from the
`make docs` output.

---

## The quirks, one by one

Each entry gives what the quirk is for, what you will see on the page, what the key records,
the correct reading, and documents to open. Example documents are all on non-holdout
layouts.

### `estimated_reading` — electricity

**What it is for.** A bill whose meter was not actually read: the consumption is the
retailer's estimate. The figure is still the period's billed consumption and is used as
printed, but every result built on it must say it rests on an estimate. This tests that
the flag is carried through rather than silently dropped.

**What you will see.** The reading basis reads **"Estimated"** instead of "Actual":

| Layout | Where |
|---|---|
| `elec_a` | the "Basis" column of the meter table |
| `elec_b` | the meter heading: "Meter SP… · reading type: Estimated" |
| `elec_c` | the "READ" column |
| `elec_d` | the sidebar: "Meter SP… (Estimated read)" |

Nothing else on the page changes, and the arithmetic is the same as for an actual read.

**What the key records.** `is_estimated_reading: true`.

**The correct reading.** Use the printed consumption, set the reading as estimated, and
state on the result that it rests on an estimated reading (`calculate_emissions` raises the
`estimated_reading` disclosure).

**Open:** `elec_020` (layout `b`, moderate, this quirk alone); `elec_024` (layout `b`,
hard, with three others).

### `cross_month_period` — electricity

**What it is for.** Real meters are read on a cycle, not on the 1st of the month. A
mid-month period inside one calendar year tests that the period is read from the page rather
than assumed to be a calendar month, and that the day count is taken from the dates.

**What you will see.** A billing period such as **15 Mar 2022 – 14 Apr 2022**: it starts
on a day between the 8th and the 22nd and ends the day before that date in the next month,
so it runs 28, 30 or 31 days. The "Days in period" (or "Number of days") line agrees with
the dates. These bills fall in 2022–2023, before each account's monthly run. Their meter
readings come from a reading wound back from the account's opening read, so they do not
continue any other bill's readings.

**What the key records.** `period_start` and `period_end` off the 1st and the month end,
both in the same year. `split_kwh_by_year` has a single entry.

**The correct reading.** One activity, for the period's year. Nothing is split.

**Open:** `elec_079` (layout `a`, hard, 15 Mar – 14 Apr 2022); `elec_080` (layout `b`,
moderate); `elec_081` (layout `c`, hard).

### `cross_year_period` — electricity

**What it is for.** A cycle that straddles 31 December covers two calendar years, and
Singapore's grid factor is published per year. The kWh must be divided by days and each
part multiplied by its own year's factor. This is the quirk behind the rule that the
calculation refuses a factor for the wrong year.

**What you will see.** A period such as **14 Dec 2023 – 13 Jan 2024**. Nothing on the page
announces the split, so the reader has to notice that the dates cross a year end. The
tariff plan is the one for the period's *starting* year (for example `SPR-LT-FIX-23`).

**What the key records.** `split_kwh_by_year`, for example `{"2023": 5060, "2024": 3654}`.
It is divided by days: each year but the last gets its share rounded half up, and the last
takes the remainder, so the parts always sum to `consumption_kwh`.

**The correct reading.** Two activities, one per year, each with its own factor. Which
crossings show two *different* factors:

| Crossing | Factors applied | Two different factors? |
|---|---|---|
| 2022/23 | 2022 → 0.417, 2023 → 0.412 | yes |
| 2023/24 | 2023 → 0.412, 2024 → 0.402 | yes |
| 2024/25 | 2024 → 0.402; 2025 → 2024's 0.402 by substitution, disclosed | no |
| 2025/26 | both years fall back to 2024's 0.402, each disclosed | no |

There are 15 such bills, spread 4/4/4/3 over those crossings and covering all seven
accounts.

**Open:** `elec_068` (layout `b`, moderate, 2023/24: the flow-check bill); `elec_064`
(layout `a`, moderate, 2022/23, also in MWh); `elec_066` (layout `c`, moderate, this
quirk alone).

### `unit_mwh` — electricity

**What it is for.** Large accounts are sometimes billed in megawatt-hours. Reading "5.805"
as kWh understates the emissions 1,000-fold and still looks plausible. This tests that the
unit printed beside the figure is read and converted, in Python.

**What you will see.** The readings, the consumption and the quantity on the energy
charge line are all printed in **MWh to three decimals** (for example "285.727 MWh →
289.637 MWh, 3.910 MWh"). The **rate stays in cents per kWh** ("31.42 c/kWh"). The energy
charge therefore works out as MWh × 1,000 × rate ÷ 100, and a reader who multiplies the
MWh by the rate directly is a thousand-fold out.

**What the key records.** `consumption_unit_printed: "MWh"`. The key's `consumption_kwh`
and readings are still in **kWh** (3910, not 3.910).

**The correct reading.** Convert to kWh before anything else.

**Open:** `elec_056` (layout `b`, moderate, this quirk alone); `elec_064` (layout `a`,
moderate, with a cross-year period).

### `credit_adjustment` — electricity, LPG, fuel

**What it is for.** A credit for something on an **earlier** document. About half the
credits print a quantity (kWh, kg or litres), and that quantity is a **decoy**: it belongs
to a prior period. This tests that a reader does not subtract it from this period's
activity, which would double-count, while still reading the money correctly.

**What you will see.** One extra row in the charges, starting **"Credit: …"**, with the
amount as a deduction (−). It sits after the last ordinary charge and before the subtotal.
When the credit names a quantity, that quantity is printed in the row's first number column.

| Kind | With a quantity (about half) | Money only |
|---|---|---|
| Electricity | "Credit: Over-estimated reading on the August 2024 bill" · `346 kWh` · −103.32 (the kWh priced at this bill's tariff) | "Credit: Billing correction, May 2024" · −19.25 |
| LPG | "Credit: Unused cylinder returned from the November 2025 delivery" · `50 kg` · −158.50 (one cylinder at this year's price) | "Credit: Delivery charge refunded, April 2025 (late delivery)" · −9.00 (one delivery fee) |
| Fuel | "Credit: Reversal of a duplicated Diesel charge, March 2026 statement" · `44.91` litres · −134.28 (litres at that earlier month's pump price, which this page does not show) | "Credit: Card fee waived, June 2026" · −5.00 |

On LPG layouts `a`, `c` and `d` the decoy "50 kg" lands under the *Cylinders* or *Qty*
column. That placement is deliberate: it looks like a delivery quantity, and it is not one.

**What the key records.** `credit_amount_sgd`, `credit_reference`, and, when a quantity is
printed, `credit_quantity` and `credit_quantity_unit`. The subtotal relation becomes
`subtotal = charges − credit`, with GST on the reduced subtotal. **`consumption_kwh`,
`lpg_kg` and `litres_by_product` are unchanged.**

**The correct reading.** Take the credit off the money; leave the activity alone. Restating
the earlier period's activity is not modelled anywhere (a known limit).

**Open:** `elec_024` (layout `b`, hard, 346 kWh); `lpg_029` (layout `a`, moderate, 50 kg,
600 kg delivered); `fuel_009` (layout `c`, hard, 44.91 L); `elec_021`, `lpg_015` and
`fuel_012` for money-only credits.

### `balance_forward` — electricity, LPG, fuel

**What it is for.** A real bill often shows the account as well as the period: what was
owed, what was paid, and what is due now. **"Amount due" is not this period's charges.**
This tests that a reader takes the period total, not the biggest bold number at the bottom.

**What you will see.** An **account summary** block of four rows:

```
Previous balance                    1,075.17
Payment received, thank you          −608.01
Charges this period                 1,069.29
Amount due (SGD)                    1,536.45
```

It is titled "Account summary", or "Your account" on `elec_b` and "ACCOUNT SUMMARY" on the
monospaced layouts. Where it sits:

| Layout | Electricity | LPG | Fuel |
|---|---|---|---|
| `a` | above the meter table | above the delivery table | above the transactions |
| `b` | after the meter readings | at the end | after the summary, before the transactions |
| `c` | after the charges, before the total box | at the end | after the totals |
| `d` | at the foot of the charges column | at the foot of the main column | at the end |

The previous balance is 85–115% of this period's total. In about 60% of these documents it
was **paid in full**, so "Amount due" happens to equal this period's total; the trap only
shows its teeth on the roughly 40% paid in part, where 40–80% of the balance was paid.

**What the key records.** `balance_forward_sgd`, `payment_received_sgd` and
`amount_due_sgd`, where `amount_due = balance − payment + total_sgd`. **`total_sgd` stays
this period's charges.**

**The correct reading.** The period's total is "Charges this period" (equal to the total
line in the charges table), never "Amount due".

**Open:** `lpg_015` (layout `c`, hard, partial payment: due 1,536.45 against 1,069.29);
`elec_048` (layout `b`, hard, partial); `elec_068` (layout `b`, moderate).

### `gst_confusion` — electricity, LPG, fuel

**What it is for.** Two numbers both labelled "current charges", one with GST and one
without. This tests that a reader keeps pre-GST and GST-inclusive figures apart; each
answer-key field is one specific one of them.

**What you will see.**
1. A grey **headline box** near the top of the page, whose label reads "Current charges
   (incl. GST)" (or "CURRENT CHARGES INCL GST" on the monospaced layouts) and whose figure
   is the **total including GST**, in large type. It is the first money figure a reader
   meets.
2. In the charges table, the **pre-GST subtotal row is relabelled "Current charges"**,
   where it normally reads "Subtotal before GST" (or "Total before GST", "Net amount",
   "Amount before GST", and so on, depending on the layout).

So "Current charges" appears twice, attached to two different numbers. The page is not
wrong: the headline says "incl. GST". It is ambiguous at a glance, which is the point.

**What the key records.** `gst_inclusive_display: true`. The key's `subtotal_sgd` (before
GST) and `total_sgd` (after GST) are as on any other document.

**The correct reading.** `subtotal_sgd` is the row directly above the GST line; `total_sgd`
is the headline figure, which also equals the table's final total.

**Open:** `elec_044` (layout `b`, moderate, this quirk alone: 1,312.58 against 1,430.71);
`lpg_026` and `fuel_020` (layout `b`, moderate, alone).

### `lpg_cylinders` — LPG (structural)

**What it is for.** Gas is sold in cylinders, and the activity a factor applies to is the
**kg of gas**, which is count × cylinder size. A page that prints no kg total tests that the
multiplication is done (in Python) and that the right lines go into it.

**What you will see.**

- On layouts **`a` and `c`**: cylinder lines only, such as "LPG cylinder, 50 kg … 12" or
  "LPG 50KG CYL DELIVERED 03-12-2025 … 12". There is **no kg total anywhere**.
- On **`b` and `d`**, a total is printed ("Total LPG delivered: 600 kg" or "Net LPG
  supplied: 600 kg"), so those layouts do *not* carry the quirk.

The decoys around the cylinder lines are not gas:
- cylinder rental or hire (charged per cylinder, labelled "not gas" on `b` and `d`);
- the delivery charge;
- empty cylinders collected (a count, with a dash for money);
- on a credited invoice, a returned cylinder's "50 kg" (see `credit_adjustment`).

**What the key records.** `prints_total_kg: false`. `lpg_kg` is the sum over the cylinder
lines of count × size.

**The correct reading.** Sum count × size over the **delivery lines only**: two sizes on
one visit (the catering kitchen's 50 kg and 12.7 kg) are both counted, and rental,
returned empties and credits never are.

**Open:** `lpg_007` (layout `a`, clean: 3 × 50 kg + 3 × 12.7 kg = 188.1 kg, never printed);
`lpg_029` (layout `a`); compare with `lpg_014` (layout `b`, which prints its total).

### `mixed_fuel` — fuel cards (structural)

**What it is for.** A fleet that burns two fuels produces two activities that take two
different factors. Adding the litres together first is wrong by the difference between the
factors. This tests that litres stay per product.

**What you will see.** Both **Diesel** and **Petrol 95** appear in the transactions, and
the per-product summary has two rows, each with its own litres and amount. The fleet rebate
is per litre across both products and reduces only money. Three fleets are mixed: Jurong
Auto, Kopi Corner and Kranji Cold Store.

**What the key records.** `litres_by_product` with two entries. There is deliberately no
total-litres field.

**The correct reading.** Two activities, `diesel` and `petrol`, never one pooled figure.

**Open:** `fuel_012` (layout `b`, hard photo: Diesel 101.10 L, Petrol 65.02 L); `fuel_009`
(layout `c`, hard).

---

## Things that vary in v1 but are not quirks

Several things differ from v0 without being quirks. None of them is a trap, and none is
listed in `quirks`.

| What | Where you will see it |
|---|---|
| **Price lists and tariff plans by year.** Codes carry the period's year, and so do the prices | `SPR-LT-FIX-22` … `-26` and `SPR-HT-FIX-22` … `-26` on bills; `IGS-COM-24/25/26` on LPG invoices; `SFC-PUMP-25/26` on fuel statements |
| **Per-delivery LPG invoices** (Tampines Print Works). One invoice per visit; the period is the days since the previous visit, ending on the delivery date | `lpg_010–012`, `lpg_042–051`; invoice numbers carry the full date (`IGS-20260218-…`) |
| **Top-up deliveries.** A month's cylinders arrive on two visits: two dated lines, and a delivery charge for "2 visits" | Kopi Corner and Lion City Laundry, for example `lpg_001`, `lpg_002`, `lpg_005` |
| **Mixed cylinder sizes.** 50 kg mains plus 12.7 kg on a side station, on one visit | Tampines Catering Kitchen, `lpg_007–009` and `lpg_034–041` |
| **A rebate per fuel card.** 0.06/L on the two original fleets, 0.05, 0.08 and 0.04 on the new ones | the rebate line of each statement |
| **The catering kitchen** replaces the dental clinic as an LPG customer | from `lpg_007` on |

## Oddities you may notice that are not traps

- **Statement and invoice numbers like `SFC-202607-7-01` or `IGS-202505-1-07`.** They are
  built from the account number's last four characters, hyphen included. They were left as
  is so the v0 fuel statements stay unchanged in v1. Not a deliberate trap, but **not
  harmless either**: in the v1 baseline the model "corrected" `SFC-…-7-01` to `SFC-…-01`
  on 3 of 42 fuel statements, so an odd-looking identifier costs field accuracy.
- **Readings that don't follow on.** A standalone `cross_month_period` or
  `cross_year_period` bill's readings do not continue from any other bill on the same
  meter.
- **Amount due equal to the total.** On a `balance_forward` document paid in full, the
  account summary looks redundant; see that quirk above.
- **Credits you can't check from the page.** A fuel credit's amount cannot be recomputed
  from the page alone (it uses an earlier month's pump price), and neither can an
  electricity "Billing correction" (a fictional amount). Their money is recorded in the
  key; their quantity, where printed, is a decoy.

---

# Company-year scenarios (`synthgen-scenario-1`, Tier 2 Block 3)

A scenario is one fictional business's documents for one reporting year, for the
calculation eval: `data/generated/scenarios/<scenario_id>/`, with v1's per-kind folders
plus a `scenario.json` that names the company, its revenue and contract revenue (both
fictional, for allocation), the reporting year, the active period, and what was withheld.
21 scenarios, 676 documents, seed `20261020` (not v1's, so an account that also has v1
bills does not repeat v1's draws). Built from the v1 builders, templates and injectors, so
every page looks like a v1 page on layouts `a`, `b` or `c`. **Layout `d` is never used.**

Two things are new, and one v1 quirk is used in a new way:

### Withheld documents (a property of the set, not a quirk)

`missing_month` is not a page quirk: no page shows it. A scenario withholds documents
(`intended.withheld` in `scenario.json`), and the gap is in the SET. The meter keeps
running underneath it, so the bill after a withheld month starts from a reading the set
never shows. **The correct reading:** for electricity and fuel cards, every uncovered day
of the active period is a gap and its months are listed. For LPG, a month with no invoice
is ordinary - a kitchen runs on cylinders already on site - and only a run longer than
45 days (`LPG_MAX_GAP_DAYS`) is a gap. **Open:** `makan_mates_missing_month` (March
electricity), `makan_mates_lpg_quiet_month` (August LPG: not a gap),
`makan_mates_lpg_long_gap` (April-June LPG: a gap).

### `usd_invoice` — LPG (structural, scenarios only)

**What you will see.** "Amount (USD)" in the column heading, "USD 1,747.71" as the total,
price list `IGS-USD-25`. Everything else is a normal LPG invoice: the cylinders, sizes and
so the kg are unaffected.

**What the key records.** `currency: "USD"`, and `usd_invoice` in `quirks`. The money
fields keep their `_sgd` names and hold USD - the names predate a second currency.

**The correct reading.** The kg, and therefore the emissions figure, do not depend on the
currency. Nothing is converted: there is no FX in Tier 2, and the USD prices are their own
fictional list, not a conversion of the SGD list at any rate. The roll-up lists the
document as non-SGD.

**Not modelled:** the page charges GST at 9% in USD. Whether a Singapore tax invoice in a
foreign currency must also show the GST amount in SGD is a VERIFY item that has not been
checked against IRAS's own guidance; the synthetic page makes no claim either way.

**Open:** `makan_mates_usd_lpg`, `lpg_004` to `lpg_006`.

### A meter cycle across the whole year

`makan_mates_meter_cycle` bills electricity from the 15th to the 14th, so the year has
thirteen bills: the first runs 15 Dec 2024 to 14 Jan 2025 and the last 15 Dec 2025 to
14 Jan 2026 (`cross_year_period` on both, `cross_month_period` on the rest). **The correct
reading:** only the 2025 days of each count toward 2025; the others are listed as outside
the reporting year, not dropped and not counted.

---

## Planned, and not in any version yet

| Quirk | What it would test | Why it is not here yet |
|---|---|---|
| `missing_month` | noticing a gap in a year of an account's documents | **Done differently in Block 3:** a property of a scenario's set (above), not a page quirk |
| `usd_invoice` with conversion | a non-SGD amount converted at the invoice date | The USD page exists (above); converting it needs `convert_currency`, which is Tier 3 |
| `multiple_premises` | two meters on one bill, so not taking only the first | Needs a two-meter extraction schema |
| `ct_multiplier` | a high-tension register reading × a CT multiplier | Not started; the high-tension plan is flat-rate and that structure is unverified |

---

## Keeping this file current

- **Adding a quirk:** add its entry, with what the page shows and an example document,
  before the generator version that introduces it is committed.
- **New generator version:** a version that changes what any page looks like gets its own
  section here. Never rewrite an earlier version's section: that set may still be on disk,
  with eval runs recorded against it.
- **Changed counts:** the counts and example ids above come from `make docs-plan` and the
  v1 manifests at seed `20261016`. If they change, re-run both and paste the new figures;
  do not edit them by hand.
