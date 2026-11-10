# Tier 3 Block 1 — peer disclosures for the remaining companies

Written for someone who was not there: the design decisions and their reasons, the defects
found and what caught them, and the evidence behind every number. Environment details and
career notes live in the local build log, not here.

**Status (2026-11-11):** the six PDF reports are verified, which brings the verified file
to 170 rows from 11 reports of ten companies.
The new rows come from MLT, MPACT, Keppel, ST Engineering and Seatrium (report and
databook); the six reports' row counts are in the Verification table.
The STI constituent check is done. FLCT and CICT are still open: their figures are only in the issuers' workbooks, and the cell reader the author
chose is not built yet (below).

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
file, sheet and cell. Not yet built.

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

## Known limits

- **Footnotes were not reviewed**, as for OCBC and UOB. ST Engineering's footnote 1 says the
  last three months of its 2025 environmental data are estimates. Its 7 rows do not say so,
  although rule 7 asks for estimated readings to be flagged. The same applies to the
  footnote markers on Seatrium's 2025 figures.
- Seatrium p.70's Scope 2 rows carry no method, though the report states market-based
  elsewhere (p.71).
- Completeness was measured with two matching changes made after drafting (above).

## Follow-ups

1. **Make the local leak check scan untracked files.** `make leak-check` scans commits since
   the base, the index and any paths passed, but not untracked files. A new document is
   therefore unchecked until it is staged. This block's record was only checked when its path
   was passed by hand. That run found three short counts and page numbers beside company
   names, all coincidences, and the record was reworded. The pre-push hook and CI still see
   the file once it is committed, so nothing can be pushed unchecked. The gap is in the local
   check only, before `git add`.
2. **Carry footnote caveats onto peer rows.** At minimum, ST Engineering's statement that
   the last three months of 2025 are estimates belongs on its 7 rows (rule 7). This needs
   the author's decision on how a footnote enters a verified row.
3. **Read the FLCT and CICT workbooks** (the author's decision above).
