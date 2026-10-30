# Tier 2 Block 4 — methodology retrieval with a retrieval eval

Written for someone who was not there: the design decisions and their reasons, the defects
found and what caught them, and the evidence behind every number. Environment details and
career notes live in the local build log, not here.

**Status:** Part A (methodology RAG) is done: code at `51a221a`, answer key revised once
(`fc73d26`), one retrieval change tried and reverted, threshold frozen at 0.30, test split
scored once (results below). Part B, first pass (peer ingestion and
`search_peer_disclosures`): done 28 Oct - see the Part B section. Part B pass 2a
(`peer_disclosures`, drafting from page images, the rule-4 amendment, verification): done for
3 reports, 58 rows verified - see the last section.

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
passages. Permission is to be requested from GHG Protocol. The run files committed under
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

## One retrieval change, tried on dev: the title in the embedded text — a negative result

**Why it was tried.** The three standards restate the same rules, and a passage's own words
do not say which standard they come from. Prepending the document's title to each chunk's
EMBEDDING input (never to the stored text, so citations and quote checks were unaffected)
might steer a question about "the Corporate Standard" to the Corporate Standard.

**How.** One flag, `EMBED_TITLE`, folded into the chunker version, so the change re-embedded
every row under a new version (`363fef74089f`, 768 rows replaced, 230793 tokens) and left
none of the old. Code at `a5a65a3`; baseline the same dev split on key v2 at `fc73d26`.

| Dev, key v2 | hit@1 | hit@5 | MRR | Answered, citing a key page |
|---|---|---|---|---|
| Overall, before → after | 4 → 5 of 18 | **12 → 10** of 18 | 0.383 → 0.389 | **12 of 15 → 9 of 16** |
| textbook | 2 → 3 of 6 | 4 → 4 of 6 | 0.400 → 0.583 | 4 of 5 → 3 of 6 |
| user | 2 → 1 of 6 | 3 → 3 of 6 | 0.389 → 0.278 | 3 of 5 → 3 of 4 |
| singlish | 0 → 1 of 6 | 5 → 3 of 6 | 0.361 → 0.306 | 5 of 5 → 3 of 6 |
| Corporate | 1 → 2 of 6 | 4 → 2 of 6 | 0.317 → 0.333 | 4 of 4 → 2 of 5 |
| Scope 2 | 0 → 0 of 6 | 3 → 3 of 6 | 0.222 → 0.222 | 3 of 5 → 3 of 6 |
| Scope 3 | 3 → 3 of 6 | 5 → 5 of 6 | 0.611 → 0.611 | 5 of 6 → 4 of 5 |

Generation-level refusals: still 6 of 6 unanswerable; wrong refusals of answerable questions
3 → 2.

**Why it lost.** Two of the losses are the title doing the opposite of what was intended:
corp_base_year_choice and corp_scope1_kitchen lost their key page from the top 5 to passages
from a different standard than the one the question concerns. It helped top-1 for textbook
questions, which use the documents' own vocabulary, and hurt the singlish and Corporate
cells. Every difference is one or two questions in 18, so it is not evidence of a real
effect either way; it is no evidence of a gain, which is what keeping it would need.

**Reverted.** `EMBED_TITLE = False` restores the original chunker version exactly: re-ingest
gave 768 rows under `20927f94c1b4`, 0 rows of `363fef74089f`, 220871 tokens (the same count
as the first ingest). Both dev runs are committed under `data/evals/runs/`.

## The retrieval threshold — frozen at 0.30

On dev, nearest-passage similarity overlaps: answerable 0.360–0.798 (median 0.541),
off-corpus 0.357–0.609, near-topic 0.369–0.587. Every threshold that refuses an unanswerable
question refuses one the judge already refuses, so above 0.3603 a threshold only costs
answerable questions. Decision (a judgement call on 6 unanswerable dev questions): a floor
of about 0.30, recorded as doing **no measurable work on dev** — the refusals come from the
generation-level check. The title experiment's dev run showed the same overlap (answerable
0.360–0.784, unanswerable up to 0.640). Frozen as `RETRIEVAL_THRESHOLD = 0.30` in
`tools/methodology.py` before the test split was run.

## Results: dev and test, on the final setup (`b2fc2e4`)

Chunker `20927f94c1b4`, `text-embedding-3-small`, k = 5, threshold 0.30, judge
`gpt-5.4-mini-2026-03-17`, question set v2. Clean tree for both. Test was run **once**, after
the threshold was frozen; nothing has been changed since.

| | Dev hit@1 | Dev hit@5 | Dev MRR | **Test hit@1** | **Test hit@5** | **Test MRR** |
|---|---|---|---|---|---|---|
| Overall | 4 of 18 | 12 of 18 | 0.383 | **8 of 18** | **14 of 18** | **0.562** |
| textbook | 2 of 6 | 4 of 6 | 0.400 | 4 of 6 | 6 of 6 | 0.792 |
| user | 2 of 6 | 3 of 6 | 0.389 | 1 of 6 | 4 of 6 | 0.311 |
| singlish | 0 of 6 | 5 of 6 | 0.361 | 3 of 6 | 4 of 6 | 0.583 |
| Corporate | 1 of 6 | 4 of 6 | 0.317 | 3 of 6 | 5 of 6 | 0.667 |
| Scope 2 | 0 of 6 | 3 of 6 | 0.222 | 3 of 6 | 5 of 6 | 0.575 |
| Scope 3 | 3 of 6 | 5 of 6 | 0.611 | 2 of 6 | 4 of 6 | 0.444 |

| Refusals and answers | Dev | Test |
|---|---|---|
| Retrieval-level refusals (threshold 0.30) | 0 of 6 unanswerable, 0 of 18 answerable | 0 of 6, 0 of 18 |
| Generation-level refusals of unanswerable questions | 6 of 6 | 6 of 6 |
| Generation-level refusals of answerable questions (wrong) | 2 of 18 | 1 of 18 |
| Answered, citing a key page | 11 of 16 | 12 of 17 |

Nearest-passage similarity on test: answerable 0.417–0.791, near-topic 0.364–0.635,
off-corpus 0.382–0.484. No test question fell below 0.30, so on test too the floor did
nothing.

Reading the comparison:

- Test scored higher than dev. With 6 questions a cell, that is the size of the difference
  two draws of six questions can produce. It is not evidence that retrieval works better
  on test questions, and it is not caused by tuning: the one change tried on dev was
  reverted.
- User-style questions are the weakest cell on both splits for hit@1 (2 of 6, 1 of 6).
  Questions phrased away from the standards' vocabulary retrieve worst, which is what the
  styles were built to show.
- Test's five answered-off-key questions are recorded as failures against the key as it
  stood when test ran: corp_sold_solar (cited S2 p.40, 46), s2_market_based (S2 p.51, 67),
  s2_td_losses (S2 p.98, although the key page ranked 1st), s3_spend_caution (S3 p.82, key
  page ranked 3rd) and s3_use_of_sold (S2 p.37, 51: Scope 2 pages for a Scope 3 question).
  Some of these may be pages the grep-only check missed. The key is not revised after
  test to find out. s3_purchased_goods was refused although it is answerable.
- **The judge is not deterministic.** Dev was run twice on identical retrieval (top 5
  identical for 24 of 24 questions, `fc73d26` and `b2fc2e4`). Answered went 15 → 16, citing
  a key page 12 → 11, and wrong refusals 3 → 2. Generation-level counts carry about one
  question of run-to-run noise; retrieval counts carry none.

Cost: dev 50329 and test 51052 tokens (judge calls; question embeddings cached).

## End to end through `/chat` (`d70010e`)

One question not in the key, in user phrasing: "We lease two delivery vans and our own
drivers run them every day. Does the diesel they burn count as our Scope 1, or is it someone
else's?" The agent called `search_methodology` once (restricted to the Corporate Standard),
which returned `ok` with 3 verified quotes from PDF pages 29, 31 and 32; the reply quoted
them with document and page. 2 turns; `unverified_figures` and `unverified_quotes` both
empty. Two things the audit does not catch, read by hand:

- The reply is hedged ("if … owned/controlled by you under your consolidation approach")
  where the standard has a direct rule for this case: under operational control "the lessee
  only accounts for emissions from leased assets that it operates" (PDF p.34). That page
  was not retrieved.
- The third quote is a figure caption that carries the page footer ("30 CHAPTER 4") — the
  reading-order artefact left in by decision, now visible to a user.

## Known limits

- **A verified quote is real, not necessarily relevant.** In the title experiment,
  corp_scope2_grid ("the power station emission also count under us?") was answered from
  Corporate p.29, a passage on transmission and distribution losses. The quote passed the
  verbatim check because it is on that page. The check proves the words exist in the
  source; it does not prove they answer the question. Only the hit and cited-page metrics
  against the hand-checked key catch that, and only on the eval set.
- **The key's completeness is uneven between splits.** Test pages were added from grep
  alone (see above).
- **Small cells.** 6 answerable questions per style per split, 2 unanswerable. A difference
  of one or two questions is not a result.
- **The retrieval-level threshold does no measurable work** on dev; the refusals depend on
  the judging model.

---

# Part B, first pass — peer-report ingestion and `search_peer_disclosures`

Scope of this pass: ingest the 14 peer report files (12 companies; intake B0 at `6cea555`)
into `document_chunks` with `corpus = 'peer'`, and give the agent a hybrid search over them.
Not in it: a `peer_disclosures` figures table, LLM-drafted labels and the rule-4 amendment
they need, and any eval. Nothing in this pass labels anything.

## The finding that shaped the tool: tables do not survive as figures

DBS Sustainability Report 2025, p. 79 (PDF p. 79), in both pdftotext modes: footnote
superscripts fuse onto the numbers - a six-digit location-based total comes out with
footnote 4 attached, and the Scope 1 label with footnote 9 attached - and reading order
loses which column is 2024 and which 2025. Layout mode keeps rows but interleaves a second
table printed beside the first. In this repo the pattern is shown only with invented
values of the same shape ("240,5763", "Total scope 17", "2,5113"): the repo must not state
unverified peer figures, and the real ones came from the very extraction shown here to be
unreliable.

Decision: peer passages are searchable and citable as prose; a number in one is not a
number the report states. The tool returns passages, marks every verified quote that holds
a figure (`contains_figures`), and the figure audit refuses any number that only a peer
passage, a judge's quote from one or the judge's reason could have supplied
(`agent/audit.py`, `_figure_view`). Page, report year and period stay allowed. Verified
peer figures are left to a later `peer_disclosures` table, checked by hand.

## Printed pages: the 80% rule, and why its candidate range was widened

A report gets a page offset (printed page = PDF page − offset) only if one offset matches
at least 80% of the pages whose footer yields a number (`chunking.choose_offset`);
otherwise its citations give the PDF page and say so. The rule and threshold were set by
the author before the preview ran.

The first preview tried offsets 0, 1 and 2. Keppel failed at 5/99 for offset 0, but its
printed page is its PDF page **+1** on 94 of 99 readable footers (an unnumbered front page
the PDF omits). The candidate range was widened to −2..+2 after seeing that, as a general
rule rather than a Keppel case, with the threshold unchanged. The re-run preview was
diffed against the first: **Keppel was the only decision that changed** (None → −1, 94/99,
95%); CICT's best candidate moved from 0 (4/65) to −1 (30/65), still below 80%.

| Report | Readable footers | Decision |
|---|---|---|
| DBS | 126/128 | 0 (126/126) |
| OCBC | 142/144 | 1 (142/142) |
| UOB | 188/190 | 1 (188/188) |
| CLAR report | 58/60 | 2 (57/58) |
| MLT | 59/60 | 1 (59/59) |
| FLCT | 47/50 | 2 (46/47) |
| Keppel | 99/103 | −1 (94/99) |
| ST Engineering | 76/79 | 0 (75/76) |
| Seatrium report | 111/126 | 2 (110/111) |
| MPACT | 42/44 | none: two printed pages per PDF page (2×PDF−4 and 2×PDF−3) |
| Sembcorp | 132/135 | none: two-up, as MPACT |
| CICT | 65/67 | none: footers split between −1 (30) and −4 (29) |
| CLAR data pack | 13/69 | none: almost no page numbers |
| Seatrium databook | 14/39 | none: best 3/14 |

Spot-checked by hand before embedding: DBS PDF p.79's footer (report name and running
title) ends in "79", and the citation reads "p. 79 (PDF p. 79)"; OCBC PDF p.23's footer
reads "OCBC Sustainability Report 2025 22" and the citation reads "p. 22 (PDF p. 23)".

## Ingest

Same chunker as methodology (`20927f94c1b4`, 300/50 words, page-bounded), so methodology
rows were not touched. Each row carries company, sector and `report_year` — the calendar
year in which the report's period ends (MPACT and MLT, 1 April to 31 March, are 2026).

| Run | Result |
|---|---|
| First | 2,884 rows inserted over 14 sources, 954,002 embedding tokens, 62 s |
| Second | every source `unchanged`, 0 inserted, 0 tokens |
| Methodology re-run after the change below | all three `unchanged`, 0 tokens; still 768 rows |
| Integrity | 0 rows of another version or model, 0 duplicate positions, 0 null text or embedding, 0 wrong widths, 0 orphan sources, 0 peer rows missing metadata, 0 methodology rows with peer metadata |

Change to the replace logic, made before ingesting: "unchanged" used to compare (page,
index, text hash). It now also compares the printed page and the peer metadata. Without
that, setting an offset after an ingest would have left the old citations in place and
been reported as unchanged on every later run. Tests cover both cases.

## Retrieval

Two legs under one filter (corpus, embedding model, chunker version; plus company, sector,
report year when given): the 50 nearest by cosine distance (exact scan) and the 50 best by
`ts_rank_cd` against the question's words OR-ed together. Fused by reciprocal rank fusion
(k = 60) in Python, ties to the lower chunk id; 8 passages. The methodology judge is
reused with "company sustainability reports" in place of "GHG Protocol documents"; a test
pins the methodology wording byte for byte (sha256), since Part A's eval was scored with
it. No similarity floor: the methodology floor did no measurable work, and a fused rank is
not a similarity.

Refusals, decided before anything is embedded where possible: `not_ingested`;
`no_match_for_filter`, whose message lists what that company or sector does have and the
report-year convention; and `generation`, as for methodology.

After the verbatim check, a quote whose text repeats an earlier one (whitespace and case
collapsed) is dropped and the first citation kept; `quotes_repeated` counts them. Chunks
overlap, so the judge can cite one sentence from two chunks (the OCBC demo below did). This
is done in the peer tool only: `verified_citations` is shared with methodology, whose
output, scored in Part A, stays as it was.

## Defects found, and what caught them

| Defect | Caught by |
|---|---|
| The keyword leg's OR-query was a cast in a FROM clause, which Postgres rejects; every unfiltered or company-filtered search errored | The first live demo run; the offline test only checked the SQL text |
| `contains_figures` treated "Scope 3" as a figure, so every Keppel quote was flagged; the audit itself does not count scope labels | Reading the demo output. The flag now uses the audit's own definition (`figures_in`); retrieval and judge unchanged |
| Asked without a document, the agent did not call the tool and said "no peer documents are loaded in this session" | The adversarial runs. The "[No documents have been uploaded …]" note now says the standards and peer reports are not uploads; the prompt's opening line names both searches |

## The figure audit's scope, revised after the second adversarial run

The second run's reply (a) quoted DBS verbatim, and the audit flagged "32", "2021" and
"100" from inside those quotes ("R-32"; "IPCC … 2021 – 100 year"). The author changed
the audit's scope rather than withholding quotes:

- **A number inside a verified quote is allowed** - a quoted span of at least five words
  that `audit_quotes` accepts is the source's own sentence. Numbers in the agent's own
  prose that only peer text could have supplied stay flagged, and a short quoted number
  ("2,5113" alone) gets no exemption.
- **Table-like quotes are withheld** by the tool (citation only, "see the table on p. N")
  and earn no exemption in a reply even when verbatim, since the passage text still holds
  the row. The rule, set by the author and not tuned: **more than 3 figures, or figures
  over 25% of the quote's whitespace-separated tokens** (`audit.is_table_like`), tested at
  both boundaries (3 of 20 / 4 of 20; 2 of 8 / 2 of 7).
- **A number joined to letters, directly or by a hyphen, is an identifier, not a figure**:
  R-32, R-410a, CO2e, PM2.5, GHG-2. This generalises the earlier "Scope 3" fix; negative
  numbers and year ranges are still figures.

A gap found after the third run: a quote could carry one fused figure and still pass the
count and share rules. "Total scope 17 2,5113 tCO2e." (an invented example of the DBS
pattern) has one figure in five tokens, since the fused "scope 17" reads as a scope label.
The author closed it with two more signs of a fused footnote marker, either of which makes
a quote table-like (`has_malformed_thousands`, `has_impossible_scope`, tested both ways):

- **a malformed thousands group** - a comma between digits followed by other than exactly
  three digits ("2,5113", "240,5763", "1,48"); commas in prose ("In 2024, the bank") are
  not between digits and do not count;
- **an impossible scope label** - "scope N" with N other than 1, 2 or 3 ("scope 17");
  "Scope 3 category 15" stays valid.

**Residual gap:** a fusion with no comma is undetectable by these rules - "500" with note 1
reads "5001", which looks like any other number. Only the count and share rules stand
between such a quote and a user. A side effect in the other direction: "Scope 1,2" written
without a space is a malformed group, so a quote holding it is withheld.

## Demo questions (once each, recorded as run, not tuned)

| Question (filter) | Result |
|---|---|
| OCBC's financed-emissions reporting lag (OCBC) | ok, 3 quotes from p. 22 (PDF p. 23); one quote appears twice (two overlapping chunks; since fixed, see Retrieval) |
| Consolidation approach for the boundary (CLAR) | ok, 1 quote, data pack PDF p. 2: the operational control approach |
| Does Keppel set Scope 3 targets? (Keppel) | ok, 3 quotes, pp. 33, 39, 46 — **but the quotes show tracking and a commitment to report all relevant categories, not a reduction target; the judge's "yes" overstates them** |
| Sembcorp's 2030 targets (Sembcorp) | ok, 3 quotes, PDF p. 37 (no printed page: two-up layout) |
| Who reports Scope 3 business travel? (none) | ok, 2 quotes: DBS p. 79, CICT PDF p. 58 |
| CICT's Scope 2 (CICT, 2025) | `no_match_for_filter`: names report_year 2024, 1 Jan to 31 Dec 2024 |
| MPACT's Scope 2 (MPACT, 2025) | `no_match_for_filter`: names report_year 2026, "1 Apr 2025 to 31 Mar 2026, a financial year ending 31 March", and the convention |
| Best Hainanese chicken rice recipe (none) | `generation`: passages mention the dish only as heritage |

The first run of this list errored on every searched question (the SQL defect above); its
output is kept with the local notes. The off-topic question returned `generation`, as
expected. A Postgres-backed test now executes the vector, keyword and count queries inside
a rolled-back transaction (skipped without a database); run against the old FROM-clause
form, it fails with the same `SyntaxError`.

## Adversarial agent runs at `/chat`

Replies as given, except that sentences quoted from the reports are replaced by
`[quote, N words]`: the reports are the issuers' copyright and are not reproduced here.

**First run (before the routing fix).** No run called `search_peer_disclosures`.

- a. "What is DBS's Scope 1?" — `unverified_figures` [] — "I can’t answer from the uploaded
  documents because none have been uploaded in this session. For DBS’s Scope 1, I would
  need to search DBS’s sustainability report in the peer corpus. If you’d like, upload the
  report or ask me to search the peer disclosures corpus for DBS and I’ll return the
  verified passage and page."
- b. "Which bank has lower Scope 2, DBS or OCBC?" — [] — "I can’t compare DBS and OCBC
  Scope 2 from verified peer reports because no peer documents are loaded in this session.
  To proceed, please upload the relevant DBS and OCBC sustainability reports, or ask me to
  search the peer corpus once it’s available."
- c. "Which of these companies serves the best Hainanese chicken rice in their staff
  canteen?" — [], `unverified_quotes` ["best Hainanese chicken rice."] — "I can’t determine
  that from the materials available here. You haven’t uploaded any documents, and I don’t
  have a source for staff canteen food quality or “best Hainanese chicken rice.” If you
  upload relevant documents or point me to a source, I can help check what they say."

**Second run (after it).**

- a. — one search (DBS) — `unverified_figures` **["32", "2021", "100"]** — **fails**: no
  emissions figure is stated, but the reply quotes passages carrying numbers (a refrigerant
  "R-32"; "IPCC … 2021 – 100 year"), which the prompt says not to do, and the audit
  flagged them:

  > DBS says Scope 1 GHG emissions comprise [quote, 21 words] DBS Sustainability Report
  > (1 Jan 2025 to 31 Dec 2025), p. 79.
  >
  > It also says: [quote, 54 words] DBS Sustainability Report (1 Jan 2025 to 31 Dec 2025),
  > p. 80.
  >
  > And: [quote, 39 words] DBS Sustainability Report (1 Jan 2025 to 31 Dec 2025), p. 80.
  >
  > Peer disclosures are self-reported by each company, under its own boundary,
  > consolidation approach and reporting period, and are not assured by Carbon R; they are
  > not comparable like-for-like.

- b. — two searches (DBS, OCBC) — [] — **passes**:

  > I can’t tell you which bank has lower Scope 2 on a like-for-like basis from the reports
  > alone.
  >
  > DBS says, [quote, 30 words] DBS Sustainability Report (1 Jan 2025 to 31 Dec 2025),
  > p. 79 (PDF p. 79)
  >
  > OCBC says, [quote, 14 words] OCBC Sustainability Report (1 Jan 2025 to 31 Dec 2025),
  > p. 57 (PDF p. 58)
  >
  > But the reports use different reporting approaches and boundaries, and the figures are
  > self-reported under each bank’s own consolidation approach and reporting period, so I
  > should not rank them as “lower” from peer disclosures alone. If you want, I can point
  > you to each bank’s reported Scope 2 page so you can compare them manually.

- c. — no search — [], `unverified_quotes` ["best Hainanese chicken rice."] — refused
  honestly without searching. Judged against the expectation set beforehand (a generation
  refusal) this failed; **the author then changed the expectation**: refusing a plainly
  off-topic question without searching is a pass, because a search that cannot be relevant
  only adds a model call and a list of irrelevant citations. The question was replaced by a
  peer-shaped one the reports could plausibly answer (run 3, c). The quote flag is the
  known false positive on a user's own phrase (Interlude D).

  > I can’t answer that from the available sources.
  >
  > The tools I have can only search greenhouse-gas methodology documents and
  > sustainability reports for reporting/boundary/method questions. They do not cover menu
  > quality, staff canteen food, or “best Hainanese chicken rice.”
  >
  > If you want, I can help with either of these instead:
  > - a GHG accounting question, or
  > - how specific listed companies report emissions or boundaries.

**Third run** (after the audit change; c replaced). 3 of 3 pass.

- a. "What is DBS's Scope 1?" — one search (DBS), 3 verified quotes of which 1 table-like
  and withheld — `unverified_figures` [], `unverified_quotes` [] — **passes**: no figure
  stated; the figure is pointed to by page.

  > DBS reports Scope 1 as including: [quote, 21 words] DBS Sustainability Report (1 Jan 2025 to 31 Dec 2025), p. 79.
  >
  > DBS also discloses its Scope 1 calculation method: [quote, 36 words] DBS Sustainability Report (1 Jan 2025 to 31 Dec 2025), p. 80.
  >
  > The Scope 1 figure is in the table on p. 79.

- b. "Which bank has lower Scope 2, DBS or OCBC?" — two searches — [] and [] —
  **passes**, with three things read by hand: it names different boundaries and methods
  but not periods (both are calendar-year 2025 reports, so there is no period difference to
  name); one DBS quote is about Scope 3 and does not belong; and its last sentence offers to
  compare the banks "by reading their reported Scope 2 figures", which the prompt does not
  allow it to do.

  > Carbon R can’t say which bank has the lower Scope 2, because the peer reports are self-reported under different boundaries and methods and are not comparable like-for-like.
  >
  > DBS says: [quote, 30 words] DBS Sustainability Report 2025, p. 79.
  >
  > OCBC says: [quote, 14 words] OCBC Sustainability Report 2025, p. 57.
  >
  > OCBC also says: [quote, 23 words] OCBC Sustainability Report 2025, p. 57.
  >
  > DBS also says: [quote, 17 words] DBS Sustainability Report 2025, p. 80.
  >
  > If you want, I can help compare them on a like-for-like basis by reading their reported Scope 2 figures and noting the boundary differences, but I can’t rank them as “lower” from these disclosures alone.

- c. "Does OCBC disclose the emissions from its staff canteens?" — one search (OCBC),
  `no_results` at the generation level — [] and [] — **passes**:

  > OCBC’s sustainability report passages I found discuss its operational emissions boundary, Scope 1/2/3 approach, and targets for banking operational emissions, but they do not state emissions from staff canteens. See OCBC Sustainability Report (1 Jan 2025 to 31 Dec 2025), p. 57 for the emissions boundary and p. 61 / p. 139 for targets and operational emissions context.
  >
  > Carbon R does not have a verified disclosure for staff canteen emissions from OCBC. Would you like me to search a different OCBC report year or another company?

Run ids: first 19f42860…, e54f1a7f…, f9b16269…; second 6ae51b96…, 9a623f33…, 8183fa30…;
third de8c901f…, 4d11ebf2…, 720a3a02….

## Part B known limits

- **The judge can say yes to quotes that do not answer.** Keppel, "Does Keppel set
  targets for its Scope 3 emissions?": answered yes from quotes about tracking all
  relevant categories and committing to report them - not a reduction target. The
  verbatim check proves the words exist, not that they answer. To be measured by the
  next pass's peer retrieval eval; no tuning now.
- **A fusion with no comma is undetectable** by the table rule: "500" with note 1 reads
  "5001", which looks like any other number (see the audit section). Fusions that leave a
  malformed thousands group or an impossible scope label are withheld.
- **Two-up layouts and data books cite PDF pages only** (MPACT, Sembcorp, CICT, both data
  books).
- **Run 3 (b) offered to compare reported Scope 2 figures** ("I can help compare them on a
  like-for-like basis by reading their reported Scope 2 figures"), against the prompt's
  rule that it never states or compares a peer figure. Recorded for the next pass's eval;
  (b) is not re-run against a changed prompt.
- **Run 3 (b) included an irrelevant quote**: a DBS sentence about what its Scope 3
  excludes, in an answer about Scope 2. The judge passed it as an answer to the DBS search;
  also for the next pass's eval.
- **No eval yet.** The demo and the adversarial runs are spot checks, not a hit rate.

---

# Part B, pass 2a — `peer_disclosures`, drafted from page images

Scope: a table for peer emissions figures, a way to draft its rows, and the rule that makes
drafting allowed. Two to three reports from different sectors are drafted, then the author
reviews and verifies them. Not in it: agent access to the table, any eval. This record gives
counts only. The verified figures themselves are committed in one file,
`data/peer_disclosures_verified.json` (author's decision after verification, below); no
draft that was not verified, and no figure read from a text layer, is committed anywhere.

## Why page images, not the text layer

The first pass showed the PDF text layer fusing footnote markers onto table figures and
losing which column is which year (see "tables do not survive as figures" above). An earlier
plan drafted labels from that same text; it was dropped before any code. The drafter here
reads the rendered page (`extract.rasterise_page`, 200 dpi, `pdftoppm -f N -l N`) through
the same `_parse` call as `extract_from_document`, into a validated `PeerTableReading`.

Two departures from the extractor's request, both recorded on every draft:

- **Detail `original`, not `high`.** `high` lets the provider scale the image down, and a
  footnote superscript on a dense table is the detail that matters. DBS pages are wide
  (1927 × 1077 pt).
- **A different model**, `gpt-5.5-2026-04-23` (confirmed on the account by
  `scripts/list_models.py`), because the extractor, `gpt-5.4-mini-2026-03-17`, is the model a
  later eval would score against these rows. The script refuses the configured extractor.

## The model transcribes; Python decides

The model returns strings as printed - row label, column header (with any spanning heading,
"Singapore > 2025"), figure without its footnote markers, the markers separately, unit - and
two classifications with the printed words behind them: the scope, and location- or
market-based where the page says so. It never supplies a page, a period or a number.

`to_disclosures` (pure, tested) then:

- keeps a cell only if its column header names exactly one year and it is the report's own
  (`report_year_of_header`: "FY2025/26" is one year ending 2026; "2019-2025" and
  "2025 vs 2024" are two and are dropped). Comparatives are dropped, not given an inferred
  period;
- takes the period from `data/peer_sources.json`, the page from the call, the printed page
  from `PRINTED_PAGE_OFFSETS`;
- parses the figure strictly (`parse_figure`: a plain number or nothing - "2,5113" is
  None and flagged as a possible fused marker);
- drops blank cells, intensity units and non-CO2e units, and counts every drop by reason.

## The table and the rule

`db/migrations/0006_peer_disclosures.sql`; columns in [DATA.md](../DATA.md). A draft has
`verified_by` / `verified_at` null (a check keeps them set together); code reads
`peer_disclosures_verified`. The loader's upsert never touches a verified row and leaves an
unchanged draft alone. A Postgres-backed test covers both, and fails with the verified-row
guard removed. CLAUDE.md rule 4 now carries the peer exception (`b20f9d3`), written before
any row was drafted.

The blind sample (20%, seed `20261029`) is drawn when a report is drafted, before anyone has
seen a value. Its rows are masked in the review printout.

## Pages drafted

`peer_sources.TABLE_PAGES`, from each report's notes and a text-layer scan that only locates
pages. Each page was confirmed by viewing its image.

| Report | Pages | Where from |
|---|---|---|
| DBS (bank) | PDF 74, 79 | Notes. The scan finds 5 candidate pages; p.74 turned out to hold an intensity chart and no table (the model said so; confirmed by eye) |
| CLAR ESG data pack (REIT) | PDF 7, 8, 20, 21 | Notes say "from p.7"; the scan finds 12. Table 1.1 for Singapore (7-8) and Total Portfolio (20-21); Australia, UK & EU and United States left for later; p.59 and p.66 are disclosure indexes |
| Sembcorp (energy) | PDF 37, 39, 40 | Notes say "e.g. PDF p.37-40"; the scan finds 37, 39, 40; p.38 is an intensity chart |

## Results so far

| Report | Pages read | Cells read | Kept | Dropped | Footnote markers recorded | Malformed / unparsed | Tokens |
|---|---|---|---|---|---|---|---|
| DBS | 2 of 2 | 36 | 18 | 18 (comparative column) | 11 | 0 / 0 | 31,733 |
| CLAR data pack | 4 of 4 | 76 | 30 | 46 (comparative column) | 10 | 0 / 0 | 31,320 |
| Sembcorp | 3 of 3 | 22 | 10 | 12 (comparative column) | 3 | 0 / 0 | 36,088 |

Pages with no table (CLAR p.21, Sembcorp p.37, p.39; DBS p.74) were said so by the model and
confirmed by viewing each page: intensity, refrigerant, energy and target content only.

The DBS run is the test of the decision above: the page whose text layer fused a marker onto
the Scope 1 label and onto figures came back with markers in their own field on 11 of 18
cells and no malformed figure. Whether the markers and figures were read *correctly* is what
the author's verification will say. The rows are drafts, and none is verified.

One discrepancy was seen before review, in a label rather than a figure: the draft gives the
Scope 1 total's label the marker "19", which the page also prints on the employee-commuting
row; the text layer reads that label as "scope 1" plus "9", which fits the notes' sequence
(the next two rows carry 11 and 12). Left as drafted, for verification to settle; it is the
kind of misread the evidence columns exist to expose.

**Checked by the author against the PDF (29 Oct), DBS:** blind first - the 4 sampled rows,
whose values the review printout masked - **4 of 4 matched the draft**. Then sighted: **17 of
18 rows correct as drafted; 1 corrected** - the label marker above (it is 9; the draft's "19"
had an extra 1). **Figures: 18 of 18 correct.** No row missing, none rejected. Recorded in a
git-ignored worksheet and applied with `scripts/verify_peer_disclosures.py apply`: 18 updated
(17 confirmed, 1 corrected), 18 in `peer_disclosures_verified`, 0 unverified; a second apply
updated 0, and a re-load of the drafts inserted 0 (the corrected label did not come back).

**CLAR and Sembcorp, checked the same way:** blind **6 of 6** and **2 of 2** matched;
sighted **30 of 30** and **10 of 10** correct as drafted. Applied: 40 confirmed.

**All three reports: 58 rows, 57 correct as drafted, 1 corrected (a label marker, not a
figure); figures 58 of 58; blind 12 of 12; none missing, none rejected.** Every row is in
`peer_disclosures_verified`. Limits of that result: 58 cells from 5 table pages of 3
reports, all clean digital PDFs; one checker, who also chose the pages; the drafter
transcribed figures, it did not decide which rows a report should have - a row it left out
is caught only if the checker notices (Sembcorp's biogenic rows were left out by its reading
of the instruction and accepted on review).

**Row completeness is not measured yet.** Everything above measures whether a drafted cell
matches the page. Whether the drafter skipped a cell the page prints has no number: the
dropped-cell counts cover only cells it read, and the author's "none missing" is one sighted
pass per page, not a measurement. A completeness check (for example, the author listing the
page's rows before seeing the draft) is left for a later pass.

Drafting first stopped on the provider (`insufficient_quota`) after CLAR's p.7 and p.8; the
cached replies were reused, and the re-run resumed at p.20.

Loaded twice: first 18 inserted, second 0 inserted, 0 updated, 18 untouched. After load:
verified 0, drafts 18, orphans 0, duplicate keys 0, method on a row without Scope 2 0.

## Defects found, and what caught them

| Defect | Caught by |
|---|---|
| `method` was allowed on Scope 2 rows only; DBS prints a location-based and a market-based *total*, which would have lost the distinction. Now allowed on any row that includes Scope 2 (model, migration, field description; the description change re-read DBS) | The first DBS draft's two `problems` |
| `rasterise` renders every page of a PDF and keeps 4 - fine for a bill, wrong for a 128-page report | Reading it before use; `rasterise_page` added |
| A region heading spanning the year columns ("Singapore", "Total Portfolio") was not part of any cell, so CLAR's rows could not say which table they came from | Viewing CLAR p.7 before drafting; column headers now carry "Spanning heading > header" |
| The leak check failed on every run: 3-digit figures coincide with page counts and lockfile hashes, and untracked never-committed folders were scanned | Its first run (13 hits, all in files untouched this session). Now fails only on a figure new since HEAD, in the index or in named paths, and lists the coincidences |
| Correcting a row label would have broken re-loading: the loader matched rows by label, so the original misread would have come back as a new draft. The key now uses `drafted_row_label`, which is never corrected; a Postgres test re-loads after a correction and finds 1 row | Designing `verify`, before the first correction was applied |
| A provider error ended the draft with a traceback | The quota error; now reported per page, exit 1, cache kept |

## Committing the verified figures (author's decision, 30 Oct)

After verification the author reversed the earlier "no real peer figures in the repo" rule
for verified rows only:

- `data/peer_disclosures_verified.json` holds every row of `peer_disclosures_verified` (58),
  each with its citation, verification (who, when, confirmed / corrected), the draft values
  labelled `draft`, and the blind entry. Rows in key order, sorted JSON keys: a second
  export is byte-identical. `figure` must equal `parse_figure(figure_printed)` or the file
  does not load.
- `make seed-peer` (`scripts/peer_disclosures_file.py load`) inserts missing rows, leaves
  identical ones, and stops with nothing written on a row that differs. Two loads on the
  author's database: 0 inserted, 58 unchanged each time. A Postgres test exports, deletes,
  loads and re-exports (identical), loads again (0 inserted), then changes a figure and
  gets a conflict; an unverified draft is not exported.
- CLAUDE.md: real peer figures only in that file; a draft never verified, or a text-layer
  figure, nowhere. `leak-check` now excludes that file from the search, fails on any peer
  figure new since HEAD elsewhere, and fails if the file's verified figure is a draft value
  that was never verified. Shown to fail (exit 1) on a scratch file holding one verified
  figure.
- **Defect, found after committing:** comparing with HEAD stops working once a leak is
  committed - the figure is then "already at HEAD". The check was re-run by hand against the
  parent commit (0 new), then fixed (`carbon_r.leak_check`, `--base`, default
  `origin/main`): every commit in `base..HEAD` is scanned file by file against base, so a
  figure added and later removed is still caught, and so is the working tree. Tested in a
  throwaway repository (9 tests); with the commit scan disabled, the two commit tests fail.
- The worksheets (`data/generated/peer_verification/`) stay git-ignored; the author backs
  them up.
- README and DATA.md state the figures are as self-reported by each company, checked by the
  author against the cited page on the stated date, not independently assured, and that they
  are facts with page citations from reports that are not redistributed.

