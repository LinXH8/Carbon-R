# GHG Protocol — contents, with what matters for Carbon R

Checklist Block 1, last item: "Skim the contents pages of the GHG Protocol Corporate
Standard and Scope 2 Guidance." Both PDFs are in git-ignored `data/raw/ghgp/`.
Below is each document's structure plus a note on why a chapter matters here, so the
skim is 5 minutes rather than 20.

## Corporate Accounting and Reporting Standard (revised)

| | | Relevance to Carbon R |
|---|---|---|
| Chapter 1 | GHG Accounting and Reporting Principles | **Read.** Relevance, completeness, consistency, transparency, accuracy. These are the five words to use in an interview when asked why provenance matters. |
| Chapter 2 | Business Goals and Inventory Design | Skim. |
| Chapter 3 | Setting Organizational Boundaries | Skim — equity share vs control. Matters at Tier 3 allocation. |
| Chapter 4 | Setting Operational Boundaries | **Read.** This is where Scope 1 / 2 / 3 are defined. The vocabulary the agent's system prompt must use correctly. |
| Chapter 5 | Tracking Emissions Over Time | Skim — base years. |
| Chapter 6 | Identifying and Calculating GHG Emissions | **Read.** The activity-data x emission-factor method Carbon R implements in `calculate_emissions`. Cite this chapter when asked where the method comes from. |
| Chapter 7 | Managing Inventory Quality | Skim — maps onto the eval harness. |
| Chapter 8 | Accounting for GHG Reductions | Skip for now. |
| Chapter 9 | Reporting GHG Emissions | Skim — what a disclosure must contain; shapes the Tier 3 report. |
| Chapter 10 | Verification of GHG Emissions | **Note only.** Why Carbon R must say "estimates are not assured". |
| Chapter 11 | Setting GHG Targets | Skip. |
| Appendix A | Accounting for Indirect Emissions from Electricity | **Read.** Directly the Tier 1 problem. |
| Appendix B | Accounting for Sequestered Atmospheric Carbon | Skip. |
| Appendix C | Overview of GHG Programs | Skip. |
| Appendix D | Industry Sectors and Scopes | Skim. |

## Scope 2 Guidance

The chapters that bear on Tier 1 directly:

- **1 Introduction** — 1.2 the Corporate Standard's relationship, 1.3 key questions on
  scope 2 accounting, 1.9 changes from the Corporate Standard.
- **4 Scope 2 Accounting Methods** — 4.1 approaches to accounting scope 2, 4.2 emission
  rate approach, 4.3 the decision-making value of each method. **This is the
  location-based vs market-based distinction.** Carbon R is location-based only, and this
  is the chapter that justifies saying so explicitly on every result.
- **5 Identifying Scope 2 Emissions** — 5.1 organizational boundaries, 5.2 operational
  boundaries, 5.3 defining scope 2, 5.5 avoiding double counting.
- **6 Calculating Emissions** — the operative chapter. Note especially:
  - 6.3 Collect activity data  → the kWh the extractor pulls off the bill
  - 6.5 Choose emission factors for each method
  - **6.6 Match emission factors** → the year/jurisdiction matching rule, i.e. exactly the
    fallback-year behaviour Block 3 implements
  - 6.7 Calculate emissions
  - 6.10 Location-based emission factors → where the EMA grid factor belongs
  - 6.11 Market-based emission factors data
- **9 Setting Reduction Targets**, **10 Energy Attribute Certificates**,
  **11 How Companies Can Drive Electricity Supply Changes** — out of scope for Tier 1.

## Two sentences worth taking into the system prompt

1. Carbon R reports **location-based** Scope 2 using the published average grid emission
   factor for the jurisdiction and year (Scope 2 Guidance ch. 4, 6.10). It does not
   compute market-based figures, because that needs contractual instruments the documents
   do not contain.
2. Factors are **matched** to the activity data's year and jurisdiction (Scope 2 Guidance
   6.6); where the exact year is unpublished, the substitution is stated rather than
   silently applied.
