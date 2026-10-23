# Tier 2 Block 4 — methodology retrieval with a retrieval eval

Written for someone who was not there: the design decisions and their reasons, the defects
found and what caught them, and the evidence behind every number. Environment details and
career notes live in the local build log, not here.

**Status:** in progress. Part A (methodology RAG) code is at `51a221a`; the answer key was
revised once (below); the retrieval threshold is **not yet frozen** and the test split has
**not been run**. Part B (peer-disclosure hybrid search) follows Block 5.

## What this block has to do

Add the one place in Carbon R where similarity search is allowed — retrieving GHG Protocol
methodology prose (CLAUDE.md rule 3) — with both kinds of "I don't know", and measure it:
hit rate against pages labelled from the documents, and how often each refusal fires.

## The corpus, and why none of it is in the repo

Three documents: the Corporate Standard (2004, 116 PDF pages), the Scope 2 Guidance (2015,
120) and the Scope 3 Standard (2011, 152). Their terms were read before anything was
ingested:

- ghgprotocol.org's Terms of Use (Last Updated: February 2023) prohibit reproducing or
  displaying GHG Protocol Content "for any public or commercial purpose" and extracting it
  with "data mining technology".
- The Corporate and Scope 3 Standards carry a copyright notice and no licence grant. The
  Scope 2 Guidance is CC BY-NC-ND 3.0.

Decision: the corpus is local only. Chunks live in the project's own database; no GHG
Protocol text is committed — the eval's anchors are committed as sha256 hashes only, with
the quotes in a git-ignored file — and a public deployment would show citations, not
passages. Permission has been requested from GHG Protocol. The run files committed under
`data/evals/runs/` carry ids, pages, scores and counts, never passage text or the judge's
quotes or reasons.

## Chunking

| Decision | Reason, with the measurement |
|---|---|
| Text from `pdftotext` reading order; `-layout` only for footer page numbers | On Corporate PDF p.20, layout mode interleaves the two columns line by line |
| A chunk never spans a page; 300-word windows, 50-word overlap | The citation is exactly the page the words are on. 768 chunks: 262 / 222 / 284 |
| Printed page only where the footer confirms `pdf_page − 2` | Confirmed on 111 of 116, 103 of 120 and 133 of 152 pages; the rest cite the PDF page and say so |
| Drop lines holding one capital letter | The Corporate Standard's vertical margin tabs ("STANDARD", "GUIDANCE"): 823, 16 and 54 such lines |
| No section headings | A numbered-heading pattern matched footnotes and numbered list items as readily as headings |
| Exact scan, no approximate index | 768 rows; deterministic results keep the eval reproducible from a SHA |

Known artefact, left in: a page's footer can land mid-chunk where reading order crosses the
columns ("18 CHAPTER 3" on Corporate p.20). Stripping footer lines risks cutting real text.

Every search filters on corpus, embedding model and chunker version; ingestion replaces a
source's rows in one transaction, so a parameter change leaves no stale rows. Two runs of
the ingest at `51a221a`: first 768 inserted (220871 embedding tokens), second 0 inserted,
every source unchanged, 0 tokens; integrity queries all 0 (rows of another version or
model, duplicate positions, nulls, wrong widths, orphan sources).

## The audit of quotes

`unverified_quotes` checks every double-quoted span in a reply against the strings tool
payloads returned **in the session**, the same set `unverified_figures` uses — not only the
current turn. A quote from a passage retrieved in an earlier turn of the same conversation
is therefore accepted. This was a deliberate widening of "from that turn", accepted on
review: the figure audit already works that way, and quoting an earlier result is
reporting, not invention.

## Defects found, and what caught them

| Defect | Caught by |
|---|---|
| Dropping every one-character line deleted real content — 986, 78 and 192 such lines exist, and the digits and markers among them are table cells and footnotes | A chunker unit test (`"\tc"` vanished) |
| The first split left textbook dev with no off-corpus unanswerable question | Reading the split table; now stratified by style × kind, with a test |
| Unanswerable "RECs for carbon neutrality" was partly answered by the Scope 2 Guidance (p.84 one certificate per MWh, p.19 "zero emissions" market-based totals, p.78 100 percent renewable goals) | A corpus search before the set was marked reviewed; replaced by a refrigerant leak-rate question, which no standard states |
| Plan revision 1 had the two older documents' page counts swapped | `pdfinfo` |

## The answer key, and its one revision

48 questions, drafted by reading the pages, never from retrieval: 36 answerable (12 per
phrasing style — textbook, user, singlish — four per document per style) and 12
unanswerable (two per style per kind: off-corpus, near-topic). Split dev/test, stratified.

### Why it was revised

The first dev run (`51a221a`) showed 6 of 15 answered questions citing pages the key did not
list, often a second standard restating the same rule. Rule 4 allows the key to be checked
against the documents when an eval disagrees with it. The rule applied to both splits:

> A page is added only if it states the answer itself. Where a question names a standard
> ("the Corporate Standard", "the Scope 3 Standard"), only that standard's pages count.

Candidates for **dev** were the pages that run retrieved or cited, plus a grep of all three
documents for the answer's wording. Candidates for **test** came from **grep only** — no
retrieval run on test. That is an asymmetry: grep misses a page that states the answer in
other words, which retrieval can surface, so the test key may remain slightly less complete
than dev's. Every proposal was confirmed by hand before it went in. Question set v1 → v2.

### Pages added (48: 25 dev, 23 test)

Dev:

| Question | Pages added | Basis |
|---|---|---|
| corp_six_gases | C p.100 | Glossary: the standard's GHGs are the six Kyoto gases, listed |
| corp_company_vans | C p.31; S3 p.48 | Transport in owned or controlled vehicles is scope 1 |
| corp_biomass | C p.65; S3 p.64 | Biomass CO2 reported separately from the scopes |
| corp_scope1_kitchen | S3 p.29, 30, 43; S2 p.108; S3 p.142 | Combustion in owned or controlled sources is scope 1 (definitions, Table 5.1, glossaries) |
| corp_scope2_grid | C p.88; S2 p.36, 108; S3 p.30, 43, 71, 142 | Generation of purchased electricity consumed is scope 2 |
| s2_location_based | S2 p.27 | Restates the definition |
| s2_sold_certificates | S2 p.46 | A consumer of energy whose certificates were sold forfeits the claim |
| s2_district_cooling | S2 p.37, 108 | Cooling is a form of purchased energy tracked in scope 2 |
| s3_exclusions | S3 p.25, 62 | Disclose and justify exclusions |
| s3_inbound_courier | S3 p.37 | Table 5.4, category 4 row |
| s3_waste | S3 p.37 | Table 5.4, category 5 row |

Test:

| Question | Pages added | Basis |
|---|---|---|
| corp_equity_share | S2 p.104; S3 p.31, 139 | Same definition (glossaries, Table 5.2) |
| corp_significance_threshold | C p.103; S2 p.108 | Glossaries: the company (or programme, verifier) determines it |
| corp_operational_control | C p.99; S2 p.103; S3 p.138 | Glossaries: the operational control definition |
| corp_outsourced | S3 p.106 | "Scope 3 emissions include outsourced activities" |
| s2_dual_reporting | S2 p.14, 61 | The two-ways requirement and its condition |
| s2_market_based | S2 p.27, 28 | The market-based method reflects the consumer's chosen supply |
| s2_td_losses | C p.29, 30; S3 p.36, 43 | End consumers do not report T&D losses in scope 2; scope 3 category 3 |
| s2_residual_mix | S2 p.29, 58, 103, 108 | Without contractual data, use the residual mix |
| s3_data_quality | S3 p.78 | Table 7.6: the indicators and their descriptions |
| s3_use_of_sold | S3 p.108 | Category 11 includes lifetime use-phase emissions |

### Borderline pages considered and rejected — they do not state the answer themselves

| Split | Question | Page | Why rejected |
|---|---|---|---|
| dev | corp_six_gases | C p.65 | A reporting requirement listing the gases, not a statement of coverage |
| dev | corp_base_year_choice | C p.38 | Guidance ("should … earliest relevant point"), not the requirement asked about |
| dev | corp_base_year_choice | S3 p.102 | A scope 3 base-year rule |
| dev | s2_tenant | S2 p.43 | Endnote about tenants who do not pay for electricity individually |
| dev | s3_allocation | S3 p.83 | Box on supplier data granularity; partial, and garbled in extraction |
| test | corp_leased_operated | C p.33 | The answer under the other consolidation approaches |
| test | corp_leased_operated | S2 p.36 | About the energy only |
| test | corp_outsourced | C p.33 | "Candidates for scope 3", not a ruling |
| test | s2_market_based | S2 p.13 | Summary table list |
| test | s2_market_based | S2 p.47 | Flowchart, garbled in extraction |
| test | s3_fifteen_categories | S3 p.123 | Mentions the 15 categories in passing |
| test | s3_purchased_goods | S3 p.36 | Category 1 row interleaved with category 2 in extraction |

Also rejected without being borderline: pages from another standard for a question naming
one (S3 p.8, S3 p.140 and S2 p.105 for the Corporate Standard's gases — S2 p.105 lists
*seven*, adding NF3; C p.9 and S2 p.23 for the Scope 3 Standard's exclusions), glossary
definitions that do not name the scope or category, tables of contents, figures and worked
company examples.

### Dev under both keys (same run, re-scored from cache: 0 model calls)

| | hit@1 | hit@5 | MRR | Answered, citing a key page |
|---|---|---|---|---|
| Key v1 | 2 of 18 | 9 of 18 | 0.219 | 9 of 15 |
| Key v2 | 4 of 18 | 12 of 18 | 0.383 | 12 of 15 |

Per style under v2: textbook hit@5 4 of 6, user 3 of 6, singlish 5 of 6. Per document:
Corporate 4 of 6, Scope 2 3 of 6, Scope 3 5 of 6. Refusals are unchanged by the key: the
judge refused all 6 unanswerable dev questions, and 3 of 18 answerable ones.

## The retrieval threshold (not yet frozen)

On dev, nearest-passage similarity overlaps: answerable 0.360–0.798 (median 0.541),
off-corpus 0.357–0.609, near-topic 0.369–0.587. Every threshold that refuses an unanswerable
question refuses one the judge already refuses, so above 0.3603 a threshold only costs
answerable questions. Decision (a judgement call on 6 unanswerable dev questions): a floor
of about 0.30, recorded as doing **no measurable work on dev** — the refusals come from the
generation-level check. To be frozen after the one retrieval change below has been
measured.
