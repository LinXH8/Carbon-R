# Sources

Every dataset and reference document Carbon R relies on, with its provenance and
verification status. Per rule 5 in [CLAUDE.md](CLAUDE.md), a value is `secondary only`
until it has been read from the publisher's own document, and must not be presented as
established until then.

**No file listed here is committed.** Source documents live in git-ignored `data/raw/`.

| # | Source | Publisher | Used for | Retrieved | Status |
|---|--------|-----------|----------|-----------|--------|
| 1 | [Singapore Energy Statistics 2025, Ch. 2](https://www.ema.gov.sg/resources/singapore-energy-statistics/chapter2) | EMA | Scope 2 grid emission factor | 2026-10-15 | **verified from primary** |
| 2 | [GHG Protocol Corporate Standard (revised)](https://ghgprotocol.org/corporate-standard) | WRI / WBCSD | Methodology; Tier 2 RAG corpus | 2026-10-16 | obtained — skim pending, see guide |
| 3 | [GHG Protocol Scope 2 Guidance](https://ghgprotocol.org/scope-2-guidance) | WRI / WBCSD | Location- vs market-based method | 2026-10-16 | obtained — skim pending, see guide |
| 4 | [IRAS — 2024 GST Rate Change Guide (1st ed.)](https://www.iras.gov.sg/media/docs/default-source/e-tax/etaxguide_2024-gst-rate-change---a-guide-for-gst-registered-businesses_1st-edition.pdf) | IRAS | Synthetic bill GST line | 2026-10-16 | **verified from primary** |
| 5 | [NCCS — Carbon tax](https://www.nccs.gov.sg/singapores-climate-action/mitigation-efforts/carbontax/) | NCCS | Tier 3 carbon-tax insight line | 2026-10-16 | **verified from primary** |
| 6 | SG non-residential tariff *structure* (flat rate per tension category) | — | Shape of `synthgen` bills | — | **secondary only — VERIFY** |
| 7 | Low-tension / high-tension supply threshold | — | `synthgen` plan assignment | — | **secondary only — VERIFY** |
| 8 | Singapore postal sector ↔ area mapping (first two digits) | — | Constraining `synthgen` addresses | 2026-10-16 | **secondary only — VERIFY** |
| 9 | [UK GHG Conversion Factors 2026](https://www.gov.uk/government/publications/greenhouse-gas-reporting-conversion-factors-2026) | DESNZ | Scope 1 LPG, diesel, petrol factors | 2026-10-18 | **verified from primary** |
| 10 | [UK GHG Conversion Factors 2025](https://www.gov.uk/government/publications/greenhouse-gas-reporting-conversion-factors-2025) | DESNZ | Scope 1 factors, prior publication year | 2026-10-18 | **verified from primary** |
| 11 | Whether Singapore pump fuel is blended with biofuel (NEA specification) | — | Mineral vs average-blend DESNZ rows | — | **assumption — VERIFY** |
| 12 | GST treatment of Singapore pump prices and fuel-card statements | — | `synthgen` fuel-card money lines | — | **secondary only — VERIFY** |
| 13 | Common Singapore LPG cylinder sizes and pricing basis | — | `synthgen` LPG invoice shape | — | **secondary only — VERIFY** |

---

## 1. EMA grid emission factor — verified from primary

**Local copy:** `data/raw/ema/SES_2025_tabular.xlsx`, sheet `T2.4` (git-ignored).
**Table title:** "Electricity Grid Emission Factor and Upstream Fugitive Methane Emission
Factor, 2005 - 2024". Attributed in-sheet to `Source: Energy Market Authority (EMA)`.

**Unit, verbatim:** `kg CO2 / kWh`

**Definition, verbatim** (footnote 1 to Table 2.4):

> The Grid Emission Factor (GEF) measures the average CO2 emissions emitted per unit of
> net electricity generation in the system by all grid-connected power units. The Build
> Margin (BM) emission factor refers to the average CO2 emissions emitted per unit of net
> electricity generation by the most recently built power units.

The footnote further directs readers to the "Technical Notes and Glossary" section of the
SES publication for more detail on the GEF.

**Series loaded** (all 20 years, `verified = true`, in `scripts/seed_factors.py`):

| 2005 | 2006 | 2007 | 2008 | 2009 | 2010 | 2011 | 2012 | 2013 | 2014 |
|---|---|---|---|---|---|---|---|---|---|
| 0.526 | 0.530 | 0.505 | 0.497 | 0.497 | 0.508 | 0.509 | 0.478 | 0.439 | 0.428 |

| 2015 | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 |
|---|---|---|---|---|---|---|---|---|---|
| 0.422 | 0.424 | 0.421 | 0.421 | 0.409 | 0.407 | 0.409 | 0.417 | 0.412 | **0.402** |

The full series is loaded rather than the latest year alone because the fallback-year rule
("use the latest published year at or before the document's year, and state the
substitution") cannot be tested against a single row.

**Not loaded:** Build Margin and the Upstream Fugitive Methane factor, both also in Table
2.4. Neither is a location-based Scope 2 electricity factor. Keeping them out of
`emission_factors` removes the chance of a lookup returning the wrong one.

### Resolved: the data.gov.sg "Average Operating Margin" question

The project checklist asked whether the SES series matches the data.gov.sg *Average
Operating Margin* series. **It does — same series, older label.** Do not treat them as
different measures.

Compared across all 16 overlapping years (resource
[`d_3de362b580b2dd2fd50cc1006d4edd4f`](https://data.gov.sg/datasets/d_3de362b580b2dd2fd50cc1006d4edd4f/view)),
rounding its 4-decimal values half-up to the 3 decimals SES publishes, **31 of 32 values
match exactly** (16 GEF + 16 Build Margin):

- `avg_opt_margin_kgco2-per-kwh` ≡ SES `Grid Emission Factor (GEF)`
- `build_margin_kgco2-per-kwh` ≡ SES `Build Margin (BM)`

**But SES remains the only source used here**, for two reasons:

1. The data.gov.sg resource **ends at 2020** (16 rows, 2005–2020; last updated June 2024).
   It has no 2021–2024 values, including the current factor.
2. Its one disagreement is **2020: 0.408 vs SES 0.407** — EMA revised that value and the
   data.gov.sg snapshot never picked the revision up. It is a stale copy, not a second
   opinion.

Consequence for the code: no runtime fallback to the data.gov.sg API, ever. A missing
factor is a `no_results`.

## 2–3. GHG Protocol standards — obtained, checklist skim still open

Both PDFs are in `data/raw/ghgp/` and permanently git-ignored — redistribution is not
ours to grant, and the Completion check requires no third-party PDFs in the repo.

Used for methodology language and, from Tier 2 Block 4, as the retrieval corpus for
`search_methodology`, where each chunk keeps its document title and page so answers cite a
page rather than paraphrase from memory.

These are not numeric claims, so there is no VERIFY status to clear — the open item is
Block 1's own checklist line, *"skim the contents pages,"* which only a human can do.
[docs/ghg-protocol-skim-guide.md](docs/ghg-protocol-skim-guide.md) maps both tables of
contents to what matters for Carbon R, so the skim is five minutes rather than twenty:
Corporate Standard ch. 1, 4, 6 and Appendix A; Scope 2 Guidance §4 (location- vs
market-based), §6.6 (matching factors — the fallback-year rule) and §6.10.

## 4. GST rate — verified from primary

Carried as `GST_RATE=0.09` in `.env`. IRAS's site itself is client-rendered (same
EMA-style problem: the page shell loads but the rate text does not), so the search
instead went to IRAS's own static e-Tax guide PDF, which is not JS-walled.

**Source:** IRAS, *"2024 GST Rate Change: A Guide for GST-registered Businesses"* (1st
edition), retrieved 2026-10-16.

**Quote, verbatim** (para. 1):

> (i) from 7% to 8% with effect from 1 Jan 2023 (first rate change); and
> (ii) from 8% to 9% with effect from 1 Jan 2024 (second rate change).

No further change has been announced since. `GST_RATE=0.09` is correct.

## 5. Carbon tax rate — verified from primary

**Source:** NCCS, "Carbon Tax" page, retrieved 2026-10-16.

**Quote, verbatim:**

> It was raised to S$45/tCO2e with effect from 1 Jan 2026 for 2026 and 2027, with a view
> to reaching S$50-80/tCO2e by 2030.

Needed only for the Tier 3 insight line; the offset percentage is still unconfirmed and
should be re-checked against NCCS when that line is built, since it was not stated on
this page.

## 6–7. Singapore tariff structure and the LT/HT threshold — VERIFY outstanding

`synthgen` v0's bills are shaped as *energy charge at a flat c/kWh for a tension category,
plus a flat monthly service charge, plus GST*. **Every rate, charge, plan name, vendor name
and UEN in `src/carbon_r/synthgen/profiles.py` is fictional** — nothing is quoted from SP
Group, EMA or any Open Electricity Market retailer, and every rendered page says so in a
footer.

What is **not** verified is the claim that this is the real shape of Singapore
non-residential pricing — as against consumption blocks, or time-of-use rates with a
capacity charge for larger accounts. Two consequences:

1. No README, resume or docstring may describe the generator as modelling real Singapore
   tariff structure until this is read from an EMA or SP Group document.
2. **No LT/HT threshold value appears anywhere in the code or docs.** Kranji Cold Store is
   sized at 180,000 kWh/month to be unambiguously a large consumer rather than to sit near
   any particular boundary, so the set stays coherent whatever the real threshold turns out
   to be.

If verification shows block tiers or time-of-use pricing is normal at these account sizes,
that becomes a **Tier 2 layout**, not a correction to v0.

## 8. Postal-sector ↔ area mapping — VERIFY outstanding

Used only to keep fictional addresses internally coherent: a Kranji street should not carry
a Toa Payoh postal code. Unconstrained Faker produced exactly that, plus a 7th-floor car
workshop — recorded as lesson 13 in `SYNTHGEN_SPEC.md`.

Sectors used in `src/carbon_r/synthgen/profiles.py`: **52** Tampines/Pasir Ris, **53**
Serangoon Garden/Hougang/Ang Mo Kio, **55** Bishan/Serangoon, **61** Jurong, **72** Kranji.

**Taken from secondary sources only** (property and encyclopaedia sites); SingPost's own
documentation was not read. The cost of being wrong is nil — these are fictional addresses
on documents labelled synthetic, and nothing in the repo asserts the mapping is correct —
but it must not be stated as fact, and must be verified before any claim that the generated
addresses are realistic.

Related: `SYNTHGEN_SPEC.md` 1.7 asks for identifiers that are *impossible* to collide with
real ones, and asks that the chosen format be verified as invalid. **ACRA's UEN format page
returns HTTP 403**, so no such verification is available. The vendor UEN is therefore
self-evidently synthetic (`SYN-0000-0001`) rather than realistic-but-assumed-invalid: no
verification is needed to know it is nobody's UEN.

## Still to obtain

| Source | Needed for | Check on arrival |
|---|---|---|
| US EPA supply-chain factors | Tier 3: spend-based Scope 3 | File version, licence; `jurisdiction='US'` |
| SGX STI constituent list | Tier 3: peer disclosures | Changes over time — record the date |
| IFRS S2 | Reporting-requirement context | Access terms: local use only, never committed |


---

## 9. UK DESNZ fuel combustion factors — verified from primary

**Publication:** *UK Government GHG Conversion Factors for Company Reporting*, Department
for Energy Security and Net Zero. Two publication years are loaded: **2026** (published
11 June 2026, last updated 31 July 2026) and **2025**.

**Licence:** **Open Government Licence v3.0**, per the gov.uk publication page.

**Local copies** (git-ignored, never committed):

```
data/raw/desnz/ghg-conversion-factors-2026-full-set.xlsx
data/raw/desnz/ghg-conversion-factors-2025-full-set.xlsx
```

**Sheet:** `Fuels`. The sheet states its own scope in cell `B6` — **`Scope 1`** — and its
year in `F6`. Its guidance (`A8`), verbatim:

> Fuels conversion factors should be used for primary fuel sources combusted at a site or
> in an asset owned or controlled by the reporting organisation.

**Column:** `D`, headed **`kg CO2e`** — the total of CO2, CH4 and N2O. Columns E, F and G
break that total down, but each is *already expressed in CO2e* ("kg CO2e of CO2 per unit"),
so there is no kg-CO2 column to choose instead. Every row loaded here is therefore `CO2e`,
unlike EMA's grid factor, which is CO2 only.

**Values loaded** (all `verified = true`, `jurisdiction = 'UK'`, `scope = 1`,
`method = 'combustion'`, `gas_basis = 'CO2e'`):

| Category | Fuel row (column B) | Unit | Row | 2025 | 2026 |
|---|---|---|---|---|---|
| `fuel_lpg` | LPG | tonnes | 35 | 2939.36095 | 2939.36095 |
| `fuel_diesel` | Diesel (100% mineral diesel) | litres | 76 | 2.66155 | 2.66155 |
| `fuel_petrol` | Petrol (100% mineral petrol) | litres | 100 | 2.33984 | **2.35372** |

Only petrol per litre moved between the two publications, which is why both years are
loaded separately rather than one being treated as current.

**On precision.** The workbook stores IEEE-754 doubles, so cell `D35` reads
`2939.3609499999998` where DESNZ publishes `2939.36095`. The loader carries the published
5-decimal value and `make verify-desnz` re-reads the sheet and diffs against it; the
residue is of order 1e-13 and the check fails if it ever exceeds 1e-9.

**The unit string is composed, not quoted.** DESNZ splits across two cells what EMA put in
one: column D's header (`kg CO2e`) over column C's unit (`tonnes`, `litres`). Both tokens
are verbatim — including the plural denominators, which is why the stored units read
`kg CO2e / tonnes` rather than `per tonne`. The agent matches the string exactly, so it
reproduces the publisher rather than tidying it.

### Decisions recorded, with their reasons

**Gross vs net calorific value: not applicable to the rows loaded.** Each fuel is published
in four units — `tonnes`, `litres`, `kWh (Net CV)`, `kWh (Gross CV)` — and only the two kWh
rows carry a CV basis. The mass and volume rows have a single value each. No kWh row is
loaded, so no CV basis has to be chosen.

**`WTT- fuels` is not loaded.** Well-to-tank is upstream Scope 3. Loading it beside the
combustion factor would let a lookup return the wrong one.

**LPG is loaded per tonne only.** DESNZ publishes LPG in tonnes and litres and has **no
per-kg row**, while an LPG invoice is written in kg. The conversion kg → tonnes is
therefore an explicit, cited step inside `calculate_emissions`, shown in the derivation,
rather than a factor quietly restated per kg. The per-litre LPG row is deliberately *not*
loaded: nothing in this project measures LPG by volume, and an unused row is a row a
lookup can land on by mistake.

**100% mineral diesel and petrol, not the average biofuel blend. ⚠️ VERIFY.** DESNZ's own
guidance (cell `A12`, and the worked example at `A17`) tells forecourt purchasers to use
the average blend — but that is advice about *UK* forecourt fuel, which carries a mandated
biofuel content.

> No Singapore road-fuel biofuel blending mandate was found, and NEA has capped FAME
> content in pump petrol and diesel since 1 July 2019, so the mineral rows are the closer
> match for Singapore pump fuel.

**This is a documented assumption, not a verified fact: NEA's own fuel specification has
not been read.** It is row 11 in the table above and must not be presented as established.
Choosing the blend would additionally pull in DESNZ's `Outside of scopes` requirement to
report the biogenic portion separately — an obligation attached to a blend these documents
do not claim to contain.

**No back series.** A DESNZ publication is a standalone set for its own publication year,
not a history. The table therefore holds 2025–2026 and nothing earlier, and an activity
year of 2024 correctly returns `no_results` rather than a substituted figure. One synthetic
LPG invoice (`lpg_001`) has a 2024 supply period precisely so that path is exercised.

### Every DESNZ-sourced result is disclosed as non-Singapore

`jurisdiction = 'UK'` drives `FactorCitation.is_non_singapore`, which raises the
`non_singapore_factor` flag on every figure using one of these rows. The agent states it,
and the page renders the jurisdiction unconditionally.

---

## 11, 12, 13. Open assumptions in the Scope 1 documents

These shape the *synthetic documents*, not any emission factor, and none is presented as
fact anywhere in the repo.

| # | Assumption as used | Where | Cost if wrong |
|---|---|---|---|
| 11 | Singapore pump fuel is effectively unblended, so mineral DESNZ rows apply | `scripts/load_desnz_factors.py` | A real factor choice. Recorded as an assumption; see section 9 |
| 12 | Pump prices on a fuel-card statement are GST-exclusive, with GST as its own line | `synthgen/profiles.py`, `models/fuel_card_statement.py` | Money decoys only — **litres are unaffected either way, so no emissions figure moves** |
| 13 | 12.7 kg and 50 kg are plausible commercial cylinder sizes, priced per cylinder | `synthgen/profiles.py` | Realism of the invoice only; the cylinder arithmetic is tested regardless of the sizes |

The GST convention (12) is enforced by `FuelCardStatement`'s validator, so the generated
documents are at least internally consistent with the convention they state.
