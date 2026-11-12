# Tier 3 Block 1 — peer disclosures for the remaining companies

Written for someone who was not there: the design decisions and their reasons, the defects
found and what caught them, and the evidence behind every number. Environment details and
career notes live in the local build log, not here.

**Status (2026-11-11):** all twelve peer companies now have verified rows. The verified file
holds 170 + 53 rows from 13 files: 170 drafted from PDF pages, and 53 read by code from
the two issuers' workbooks for FLCT and CICT, whose reports print no GHG table. The STI constituent
check is done.

## What this block has to do

Tier 2 Block 4 verified 87 rows from 5 of the 12 peer companies. This block brings the other
7 through the same process (CLAUDE.md rule 4): drafted by a vision model from page images,
each figure checked by the author against its cited page, a seeded blind sample checked
first, and only verified rows served by `lookup_peer_disclosure`. The pipeline is the one
from Tier 2 Block 4, pass 2b, unchanged except where a report broke one of its assumptions
(below).

## The STI list

The peers were described as "12 STI companies" without that ever being checked. The author
captured the constituents table on SGX's STI page on 2026-11-10 as screenshots (local
only). It shows all 30 constituents, and all 12 peer companies are among them. Recorded in
`docs/SOURCES.md` as source 17, verified from primary. The index is reviewed periodically,
so the check holds as of that date, not for the report years.

## Pages drafted

Each page was proposed by Claude after viewing it, from the candidates found by the
script's `scan` (which uses the text layer only to find pages), and confirmed by the author.
The reasons for each choice are in the comments on `TABLE_PAGES` in
`src/carbon_r/peer_sources.py`.

| Source id | PDF pages | Notes |
|---|---|---|
| `peer_mlt_2026` | 46 | Appendix performance table; p.24 repeats the Scope 3 categories |
| `peer_mpact_2026` | 34, 35 | Two printed pages per PDF page; by country, no group totals in the table |
| `peer_keppel_2025` | 45, 50 | p.45's Scope 3 table has no year column: 2025 declared by the author (`PAGE_YEARS`) |
| `peer_stengg_2025` | 37 | Unit in the section heading only; footnote 1 says the last three months of 2025 are estimates |
| `peer_seatrium_2025` | 70 | Scope 1 and 2 by country; Scope 3 is an infographic only (p.58), not drafted |
| `peer_seatrium_2025_databook` | 13 | The group table, Scope 3 categories 1-15 |

**FLCT and CICT print no GHG table** in the reports held. FLCT's has chart labels rounded to
thousands; CICT's has charts and prose, and points to a performance summary on its website.
The figures are in the issuers' workbooks (FLCT ESG Databook FY2025, CICT SR 2025 data pack),
which the author downloaded. The author's decision (2026-11-10): read those cells in Python,
with no model, keeping the value Excel displays under the cell's number format rather than
the stored float (some cells store 14-16 decimals behind a one-decimal display), and cite
file, sheet and cell. Built and done (below).

## The inventories, taken before drafting

As in pass 2b, the author listed every report-year cell on each page before the draft
existed, so `completeness` can count cells the drafter skipped. The first lists named every
year column (three per row). `completeness` compares only report-year cells, so the
comparative lines would all have shown as skipped. With the author's permission and before
any drafting, Claude removed the comparative-year lines mechanically and changed Keppel
p.45's Scope 3 header to the `(no year column)` convention. The originals are kept locally.

## Two changes made after drafting

Both were made after the drafts existed and after their completeness output had been seen.
Neither touched a figure, the drafting instruction or the model's replies. They are recorded
here because a measure changed after its result was known.

1. **A year header with a footnote superscript was not read as a year** (defect). ST
   Engineering p.37 prints "2025¹". The year parser (`peer_disclosure._years_named`) matched
   years with `\b`, and `re` counts a superscript digit as a word character, so "2025¹"
   named no year and all 7 report-year cells were dropped as comparatives. Caught by
   `completeness`: 0 of 7. Fix: superscript digits are removed before the year is read.
   Tests for "2025¹" and "FY2025/26²³" fail without it. The draft was re-run from the cached
   reply (no model call). The fix changes no earlier report: no header in the other drafts
   carries a superscript.
2. **A listed header now matches the end of a drafted heading path** (a scorer change). The
   drafter writes a nested header in full, as its instruction asks: "SCOPE 1 & 2 > Scope 1 >
   2025*". The author listed "2025". The matcher compared whole headers, so all 14 Seatrium
   p.70 cells were read but counted as skipped, and the same 14 as extra. Now the listed
   header's parts must equal the last parts of the drafted header, with footnote marks and
   `*` ignored (`peer_verification.header_matches`). A listed header longer than the drafted
   one still never matches. A test fails without the change. Known limit: a bare year cannot
   tell apart two tables on one page that differ only by a spanning heading; the row label
   and scope must.

## Completeness

Run after both changes. "Method differs" means the cell was read, but the drafter's method
and the author's differ; verification decides which is right.

| Source id | Listed | Matched exactly | Method differs | Skipped | Read, not listed |
|---|---|---|---|---|---|
| `peer_mlt_2026` | 7 | 7 | 0 | 0 | 0 |
| `peer_mpact_2026` | 19 | 19 | 0 | 0 | 0 |
| `peer_keppel_2025` | 20 | 17 | 1 | 2 | 2 |
| `peer_stengg_2025` | 7 | 7 (after change 1) | 0 | 0 | 0 |
| `peer_seatrium_2025` | 14 | 7 (after change 2) | 7 | 0 | 0 |
| `peer_seatrium_2025_databook` | 16 | 13 | 2 | 1 | 1 |

- **Seatrium report p.70, with the note above:** before change 2, 0 of 14 matched and 14
  showed as skipped, though every cell had been read. After it, 7 match exactly and 7 differ
  only in method. Those 7 are the Scope 2 rows: the author listed market-based, and the
  drafter gave no method, because the page does not say. The market-based basis is stated
  in a footnote printed on p.71, which was not drafted.
- **Keppel:** the 2 skipped and the 2 extra are the same cells. The drafter kept the footnote
  letters ("emissionsᵃ", "emissionsᵇ"), which the label normaliser does not remove, and wrote
  "CO₂". These cells are left for the author to pair by hand. The method difference is p.45's
  Scope 1 and 2 total: drafted market-based, listed with no method.
- **Seatrium databook:** the skipped and extra cells are one cell. The page prints "Category
  4: Upstream Transporation and Distribution", and the drafter corrected the spelling. The
  drafted label is therefore not as printed, and verification corrects it. The two method
  differences (the I-REC row and the net total) were listed as market-based and drafted with
  no method.

## Drafting

Model `gpt-5.5-2026-04-23`, the drafter from Tier 2 Block 4, so the scored extractor
(`gpt-5.4-mini`) still differs from it (rule 4). 200 dpi, detail "original". 8 model calls
in all, one per page. 83 rows kept; every other cell read was a comparative-year column.
Problems 0, malformed thousands 0, unparsed 0.

## Blind check

The author entered the 16 blind-sample figures from the pages before seeing the drafts.
15 of 16 match the draft.
The mismatch is MPACT p.35, Scope 2 market-based, China: the value entered is the one printed in the location-based China row of the same table. The
blind entry is kept as entered, and the sighted check settles which reading is right.

| Source id | Blind matched |
|---|---|
| `peer_mlt_2026` | 1 of 1 |
| `peer_mpact_2026` | 3 of 4 |
| `peer_keppel_2025` | 4 of 4 |
| `peer_stengg_2025` | 1 of 1 |
| `peer_seatrium_2025` | 3 of 3 |
| `peer_seatrium_2025_databook` | 3 of 3 |

## Verification

The author checked every row against its page, the blind rows first (above), then the rest
sighted. The author judged every figure, label and method correct as drafted except one
label, and asked Claude to enter the outcomes. The one correction is the Seatrium databook's
Category 4 label, restored to its printed spelling. The author had already listed that
spelling in the inventory, and it is the defect recorded under Completeness. Verified by Lin
Xiang Hong; `verified_on` is 2026-11-10 in UTC.

| Source id | Rows | Correct as drafted | Corrected | Rejected | Blind matched |
|---|---|---|---|---|---|
| `peer_mlt_2026` | 7 | 7 | 0 | 0 | 1 of 1 |
| `peer_mpact_2026` | 19 | 19 | 0 | 0 | 3 of 4 |
| `peer_keppel_2025` | 20 | 20 | 0 | 0 | 4 of 4 |
| `peer_stengg_2025` | 7 | 7 | 0 | 0 | 1 of 1 |
| `peer_seatrium_2025` | 14 | 14 | 0 | 0 | 3 of 3 |
| `peer_seatrium_2025_databook` | 16 | 15 | 1 (label) | 0 | 3 of 3 |

In all: 83 rows, 82 correct as drafted, 1 corrected, none rejected, and every figure read
correctly. The MPACT blind mismatch was settled sighted: the draft was right, and the
author's blind reading had come from the location-based row above. The blind entry is
kept as made, so the miss stays recorded.

The methods stay as drafted:
- **Keppel p.45, the Scope 1 and 2 total:** market-based. The page's own note says its Scope
  2 figure uses the market-based method.
- **Seatrium report p.70, Scope 2:** no method. The page does not state one; the
  market-based basis is in a footnote on p.71, which was not drafted.
- **Seatrium databook, the I-REC row and the net total:** no method.

`apply` was run twice per worksheet; the second run updated 0. `export` was run twice, and
the two files are byte-identical (170 rows). `make seed-peer` then gave 0 inserted, 170
unchanged. `make leak-check` found 0 new, `make check` and `make test` passed (1029 passed,
1 skipped), and `lookup_peer_disclosure` returned `ok` with the cited page for ST
Engineering Scope 1, MLT Scope 2 market-based and Keppel Scope 3 category 11.

## The workbook reader: why "no new tooling" changed

The block's plan said the pass-2b pipeline would be used unchanged. That held for the six
PDF reports. It could not hold for FLCT and CICT. Neither report prints a GHG table, and
the companion files the author downloaded are Excel workbooks, not PDFs. The pipeline reads
page images and cites a PDF page, and a workbook has neither. The author chose
(2026-11-10) to read the cells by code, with no model, and approved the plan on 2026-11-11.

What was built, tests first at each step:

1. **The displayed value.** `xlsx.read_cells` reads each cell's number format, and
   `xlsx.render_number` reproduces what the workbook shows: General for whole numbers,
   `0`, `0.0`, `#,##0`, `#,##0.0` and the like, rounded half away from zero on the stored
   decimal string, as Excel rounds. This matters because the workbooks store doubles behind
   their formats: one FLCT cell stores sixteen decimals behind a one-decimal display. The
   displayed value is the figure as printed. Any other format is refused, never
   approximated: a percentage, a date, an accounting format, or General over a fraction.
   Over both workbooks, every refused cell is outside the GHG report-year columns
   (intensity, percentages, comparative years).
2. **Migration 0008.** A row cites a PDF page or a sheet and cell, never both and never
   neither. The cell key gains the sheet and cell, so cells that share a label and a column
   stay distinct. For a PDF row the sheet and cell are empty, a constant within its report,
   so the existing rows' sort order and seeded blind samples are unchanged. A test checks
   this against every committed blind sample. The verified file writes the two fields only
   when set, so the 170 existing rows serialise as before: the re-export is byte-identical.
3. **The reader** (`carbon_r.peer_workbook`, `scripts/read_peer_workbook.py`). The author
   declares each table (`WORKBOOK_TABLES`) and lists each report-year cell with its
   classification; the inventory replaces the model's reading. Python checks each listed
   cell against the workbook: label and header as printed, the header naming the report
   year, the unit, and a non-blank value in a supported format. It also checks every table
   against the list, so completeness holds in both directions by construction. Any problem
   writes nothing. A table can name a heading cell, so FLCT's per-region rows are recorded
   as "Region > Category ...", the page drafter's convention. Rows are tagged
   `carbon_r.workbook_reader v1`, and each records the sha256 of its sheet in place of a
   page image. Worksheets, `apply`, `export` and the leak check take these rows unchanged.
4. **The citation** names the sheet and cell: "<report>, sheet '<sheet>', cell L9".

The blind sample is kept for workbook rows (author's decision): no model is involved, but
it would also catch a wrongly rendered number format. **Workbook rows are not eval 5a
rows.** That eval measures extraction from PDF pages, so `eval_peer_extraction.py` excludes
and counts any row without a page. A test fails if one gets through. CLAUDE.md rule 4 now
reads: peer rows are either drafted by an LLM from report pages, or read by code from the
issuer's own workbook, and every row is verified by hand.

**Defects caught by the tests while building:**
- A table's declared unit was first matched as a substring, so "tCO2e" was found inside a
  title printing "ktCO2e". The unit must now be a whole token.
- `load` would have failed on every draft written before 0008, which has no sheet or cell
  field. Those fields now default to empty.

**Registration.** Both workbooks are in `data/peer_sources.json`, with sha256 and a download
date of 2026-11-10. The URLs, recorded later, are the landing pages the files came from. Neither workbook states its period's
dates, only the year:
- FLCT's dates (1 October to 30 September) come from its FY2025 report, which states them
  (`from_companion`).
- CICT's were first recorded as a calendar year assumed from its FY2024 report. The author
  then confirmed them from CICT's Sustainability Report 2025, which states it covers 1
  January to 31 December 2025, so they are `from_companion`, citing SR 2025 (2026-11-11). CICT's searchable prose stays the FY2024
  report, one year behind these figures.

Workbooks are peer sources but not prose corpus (`PEER_CORPUS_IDS`): they are never chunked
or searched.

**Noted by the author when listing the cells (2026-11-11).** CICT's market-based and
location-based Scope 2 figures are identical in every year of the data pack. That is
likely genuine, meaning no certificates were bought, but the data pack does not say so.
It is recorded here, not inferred into a row.

**Blind entries for the workbooks.** The author gave the first reading of FLCT's Scopes 1 &
2 L10 as "8.7k". Asked which cell to check, without being shown the draft, the author read
the cell again and gave 9.2, which is what was recorded. The first reading is kept here
because it differed. CICT's two values were given without cell references, and their order
was confirmed by the author before entry.

## Verification of the workbook rows

The author's tables were proposed by Claude from labels and headers only, checked by the
author, and the inventories filled by the author: the classification (scope, category,
method) is the author's. Claude pre-filled the copy-only columns (sheet, cell, label and
header as the workbook shows them); the reader re-checks those against the workbook.

The first FLCT read stopped with nothing written. The author had typed the region into the
37 Scope 3 labels, where the reader adds it from the table heading itself. On the author's
instruction Claude stripped the prefix (the original is kept locally), and the re-read
matched. CICT matched on the first read.

| Source id | Listed | Read | Problems | Unlisted | Shown other than stored | Blind matched | Outcome |
|---|---|---|---|---|---|---|---|
| `peer_cict_2025_datapack` | 11 | 11 | 0 | 0 | 1 | 2 of 2 | 11 confirmed |
| `peer_flct_2025_databook` | 42 | 42 | 0 | 0 | 4 | 8 of 8 | 42 confirmed |

The author checked every row against the workbook and confirmed all 53 as read: none
corrected, none rejected. Verified by Lin Xiang Hong. `apply` was run twice per worksheet;
the second run updated 0. The export was run twice and the files are byte-identical (all
170 + 53 rows). The 170 PDF rows are unchanged in it: the diff adds the workbook rows and changes
only the file's note. `make seed-peer` gave 0 inserted, all rows unchanged. The lookup returned
`ok` with a sheet-and-cell citation for CICT Scope 2 market-based (one row) and for FLCT
Scope 3 category 13 (four rows, one per region, each labelled with its region).

The lookup's disclosure text said every figure was drafted by a model from a page image,
which was no longer true. It now names both routes, and the instruction to state the page
says "page (or sheet and cell)".

## Eval 5a, re-run on every verified PDF row

Run `peer_extraction_20261111T155045Z`, clean tree at `ab096bb`, extractor
`gpt-5.4-mini-2026-03-17`, labels drafted by `gpt-5.5-2026-04-23`. 17 pages, 170 PDF rows;
the 53 workbook rows were excluded and counted by the script. The 9 pages of the first run
came from the reply cache, so the 87-row line repeats the recorded run exactly (image 82,
layout 60, pdfplumber 25 identified). It is a replay, not a second measurement. The 8 new
pages took 24 model calls.

| The 83 new rows | Image | Layout | pdfplumber |
|---|---|---|---|
| Present, label and header matched | 48 | 41 | 22 |
| Present by figure | 59 | 81 | 26 |
| By figure only: cosmetic / substantive | 11 / 0 | 26 / 14 | 1 / 3 |
| Identified (strict + cosmetic) | 59 | 67 | 23 |
| Not present either way | 24 | 2 | 57 |

Every figure returned was exactly as printed, with no marker fused on, in every arm.

**The image arm's misses are row selection, not reading.** They come from two pages:

| Page | Image, present by figure | Layout, present by figure |
|---|---|---|
| `peer_keppel_2025` p.45 | 4 of 16 | 16 of 16 |
| `peer_seatrium_2025` p.70 | 2 of 14 | 14 of 14 |

The cached image readings show why:
- **Keppel p.45:** the extractor read the Scope 1 and 2 table and only the Scope 3 total.
  Its notes say it took the instruction to ask for the totals the page prints, not the
  category breakdown.
- **Seatrium p.70:** it read the two Total rows and none of the countries.

On the other six new pages the image arm found 53 of 53 by figure and layout 51 of 53. The
drafter, `gpt-5.5`, read every row of both pages from the same instruction. The
instruction was not changed after this result: a change made now would be tuned on the
eval, and the next measurement would not be independent.

## Caveats on verified rows (proposal 1, approved 2026-11-11)

A footnote that qualifies a figure enters the rows it qualifies, as the proposal described:

- **Migration 0009** adds the caveat columns: the text as printed, its kind (`estimate`,
  `restated`, `boundary`, `method`, `other`), where it is printed (a PDF page, or `Sheet!A1`
  for a workbook), and who added it and when. A caveat is whole or absent, and only ever on
  a verified row.
- **`verify_peer_disclosures.py caveat-sheet` / `caveat`.** The first writes a sheet of the
  source's verified rows with blank caveat columns. The author transcribes the footnote onto
  the lines it applies to. `caveat` sets the caveat columns only, on verified rows only, in
  one transaction or not at all. An identical caveat is left as it is, so a second run
  updates 0, and a different one is refused, never overwritten.
- **No emissions figure in a caveat** (`has_emissions_figure`): no decimal, no thousands
  separator, no whole number of 100 or more that is not a year, and no number followed by a
  CO2e or tonnage unit. A footnote that gives a figure should become a verified row.
- **The lookup** shows the caveat under its figure, and **the loop attaches** the caveats of
  the turn's figures to the reply in code, after `PEER_CAVEAT`. An estimate reads "partly
  estimated, as the report states", followed by the footnote. It never depends on the model.
- **The verified file** carries a caveat only on a row that has one, so every other row is
  written as before.

**A defect, caught by the migration's test before commit.** The first check constraint
accepted a caveat printed nowhere, and one with no kind. In SQL, a comparison with NULL is
neither true nor false, and a CHECK passes when it evaluates to NULL. Every condition now
tests `is not null` explicitly, and the test covers both cases.

**First case, applied 2026-11-12:** ST Engineering p.37, footnote 1 on the "2025¹" header.
The sheet listed the report's 7 verified rows. Applied blank, it changed 0 rows. The author
transcribed the footnote in full onto all 7 lines, with kind `estimate` and page 37. Applied
by Lin Xiang Hong: set on 7 rows; a second run set 0 and left 7 unchanged. Two exports were
byte-identical, the file diff only adds the 7 caveats, and `make seed-peer` gave 0
inserted. The lookup for ST Engineering Scope 1 returns the figure with "partly estimated,
as the report states" and the footnote, cited to PDF p. 37.

## Agent cases v3: 4 cases x 3 runs

Cases `data/evals/peer_agent_questions_v3.json`, sha256 `bd3b74e39c779c9a`. Cases 1-3 were
drafted by Claude and reviewed by the author; case 4 was added by the author. The file was
committed at `3fda85c` before the run, and the run is `peer_agent_20261111T161732Z`, on a
clean tree. The replies are kept locally only. Nothing was changed after the run.

| Case | Automatic, all checks | Read by hand |
|---|---|---|
| MLT vs CLAR, Scope 1 (years differ) | 2 of 3 | Both figures with periods, no ranking, no offer: 3 of 3. Says the years differ: 1 of 3 (run 2: "financial year ending # March" against "calendar year"); runs 1 and 3 say only "different reporting periods", with both periods' dates given |
| FLCT market-based Scope 2 (workbook) | 3 of 3 | 3 of 3: ktCO2e, 1 Oct 2024 to 30 Sep 2025, "sheet ... cell L#" |
| `peer_stengg_2025` Scope 1 (caveat) | none of 3 | Caveat attached in code 3 of 3; the model also says it is partly estimated 3 of 3; unit printed correctly 3 of 3 (see the case defect below) |
| FLCT total Scope 3 | 1 of 3 | 1 of 3 (run 2: the four regional totals, "not one single company-wide total", no sum) |

(Figures in the replies are masked here as "#".)

**What failed, and why:**
- **FLCT total Scope 3, runs 1 and 3.** The question names no year, but the agent passed
  `report_year` 2024 in one run and 2026 in the other; FLCT's FY2025 is report_year 2025.
  Both lookups returned `no_results`. The tool's message names the year that is held, and the
  agent offered to look again instead of retrying. No figure was stated and nothing was
  summed, so nothing wrong reached the user. The question went unanswered.
- **The years-differ rule.** In 2 of 3 runs the reply gives both periods and says
  "different reporting periods", which is true, but does not say outright that the years
  do not match.
- **A defect in the case, not the agent.** The caveat case's unit was written as "'000 tCO",
  meant to match "'000 tCO₂e". The check requires the unit to end at a word boundary, and
  the "2" of "tCO₂e" continues the word, so it failed in every run though all 3 replies print the
  unit correctly. The case was frozen before the run and is left as it was. It should have
  read "'000 tCO2e".
- **"Verified by hand" flagged twice** (ST Engineering run 2, FLCT Scope 3 run 2). Both
  flagged sentences cite a source and state no figure, but they carry page numbers, dates or
  cell references, which the check counts as numbers. Both are false alarms. The check should
  ignore numbers that are pages, dates or cells.

The prompt was not changed in response (the cases are test-only).

## Fix pass after v3 (2026-11-12), and agent cases v4

Two fixes, made on the author's instruction from v3's failures, at `47cac06`:

- **Follow-up 6: no invented year.** When the question names no year, a `report_year` the
  model adds is dropped in code before the peer lookup and the peer search. The tool's
  message tells the model the year was not applied and to state each figure's own period.
  A named year is kept, and so is the year after it, because a report_year is the year a
  period ends: "2025" can mean a financial year ending in 2026. Recommended over "the latest
  year, stated in the reply" because each figure already carries its period, and an
  unfiltered lookup returns each year as its own row rather than choosing one.
- **Follow-up 7: citations are not figures.** The "verified by hand" check now sets aside
  dates, years, financial-year labels and the numbers in "Scope 3" or "Category 13".
  Page and cell references are set aside only when a lookup this turn returned a figure from
  that page or cell. The first version set every page reference aside, and an existing test
  caught it: a sentence citing the pages of a quoted passage, which no lookup returned,
  claims hand verification falsely and must still be flagged. Both v3 false alarms now pass.
  Known limit: a figure printed as a bare four-digit number from 1900 to 2099, with no
  thousands separator, is read as a year and set aside.

**Cases v4** (`data/evals/peer_agent_questions_v4.json`, sha256 `1d3cb8c3d63a2c66`) are v3
with only the ST Engineering unit corrected to "'000 tCO2e". v3 is unchanged. Run
`peer_agent_20261112T050629Z` at `47cac06`, on a clean tree. Before the run, Claude's first
commit of these changes carried three leak-check coincidences. The leak check's failure was
hidden by a pipe through `tail`; the commit was amended before any push. Read by hand:

| Case | v3 (`3fda85c`), by hand | v4 (`47cac06`), automatic | v4, by hand |
|---|---|---|---|
| MLT vs CLAR: both figures with periods, no ranking | 3 of 3 | 3 of 3 | 3 of 3 |
| ... says the two years differ | 1 of 3 | - | 1 of 3 (run 1: financial year ending March against calendar year; runs 2-3: "different reporting periods") |
| FLCT workbook figure: ktCO2e, Oct-Sep period, sheet and cell | 3 of 3 | 3 of 3 | 3 of 3 |
| ST Engineering: caveat attached (code) / model says estimated | 3 / 3 of 3 | 3 of 3 (unit now checked) | 3 / 3 of 3 |
| FLCT total Scope 3: no FLCT-wide total, regional figures, no sum | 1 of 3 | 3 of 3 | **none of 3** |
| "Verified by hand" flagged | 2 (both false) | - | 0 |

**FLCT total Scope 3 got worse, and the automatic checks missed it.** The year fix worked:
all 3 runs looked up without a year and got every row. But with all of FLCT's Scope 3 rows
in front of it, the model answered from the first "Total" row, Singapore's (cell L18):
- Run 1 called it "FLCT's verified Scope 3 total" and named no region.
- Run 2 called it FLCT's Scope 3 total "for Singapore (including corporate office) > Total".
- Run 3 said the figures are "reported by region and a total", implying an FLCT-wide total
  exists, and gave no figure.

No run summed the regions or stated a figure that was not verified, so every automatic check
passed. The checks cannot see a verified figure presented under the wrong scope of company.
This is v3's 1 of 3 behaviour turned into a misstatement, not an improvement. The prompt was
not changed (the cases are test-only).

## Known limits

- **Footnotes were reviewed for one report only.** ST Engineering's footnote 1 (the last
  three months of 2025 are estimates) is carried on its 7 rows as a caveat (`1de4491`).
  The other reports' footnotes, such as the markers on Seatrium's 2025 figures, are not
  reviewed.
- Seatrium p.70's Scope 2 rows carry no method, though the report states market-based
  elsewhere (p.71).
- Completeness was measured with two matching changes made after drafting (above).
- CICT's labels keep their footnote digits as printed ("Scope 11" is Scope 1, "Scope 32" is
  Scope 3), and the lookup returns them so. The scope field is right; the label reads oddly.
- The two workbooks' URLs are the landing pages they were downloaded from, recorded as
  such, not links to the files.

## Follow-ups

The total of verified rows is written as 170 + 53 throughout: the sum equals a verified peer
figure, which the leak check would rightly flag in a tracked file.

1. **Make the local leak check scan untracked files.** `make leak-check` scans commits since
   the base, the index and any paths passed, but not untracked files. A new document is
   therefore unchecked until it is staged. This block's record was only checked when its path
   was passed by hand. That run found three short counts and page numbers beside company
   names, all coincidences, and the record was reworded. The pre-push hook and CI still see
   the file once it is committed, so nothing can be pushed unchecked. The gap is in the local
   check only, before `git add`.
2. **Carry footnote caveats onto peer rows.** Built, and the first case (ST Engineering's
   footnote 1) applied (above). Footnotes on the other reports are still not reviewed. A
   second candidate, a method caveat (noted by the author, 2026-11-11): FLCT's notes say its Singapore
   market-based Scope 2 uses the grid factor as a proxy, because no verified residual-mix
   factor exists.
3. **Read the FLCT and CICT workbooks.** Done (above).
4. **A verified figure of exactly zero.**
   One cell in FLCT's databook is zero, so the leak check now matches any bare zero on a
   line that names a peer or a tonnes unit. The test file was reworded to avoid it. Whether
   a zero, which reveals nothing, should be exempt is for the author to decide.
5. **Record the two workbooks' download URLs, and check CICT's dates.** Done 2026-11-11:
   the landing pages are recorded, and CICT's dates are confirmed from its SR 2025.
6. **The agent sets `report_year` unasked.** Fixed 2026-11-12 (`47cac06`): a year the
   question does not name is dropped in code.
7. **The "verified by hand" check** counted page numbers, dates and cell references as
   figures. Fixed 2026-11-12 (`47cac06`).
8. **A regional total presented as the company's.** In v4, given all of FLCT's Scope 3 rows,
   the agent took Singapore's "Total" (L18) as FLCT's Scope 3 total in 2 of 3 runs, and in
   the third implied an FLCT-wide total exists. The automatic checks cannot see this. To
   decide: a code-side note when a lookup returns several "Total" rows for different
   tables (e.g. "these are regional totals; there is no company-wide total"), or a check
   that a reply's "total" names its table.
