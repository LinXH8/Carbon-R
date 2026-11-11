-- 0008: a peer row may cite a workbook cell instead of a PDF page (Tier 3 Block 1)
--
-- FLCT and CICT print no GHG table in their reports; the figures are in the issuers' own
-- workbooks (FLCT ESG Databook FY2025, CICT SR 2025 data pack). Those rows are read by code
-- (`carbon_r.peer_workbook`), not drafted by a model, and cite the sheet and cell the value
-- was read from - the cell takes the place of the page.
--
-- - `sheet_name`, `cell_ref` (A1 style): set together, for a workbook row only.
-- - `pdf_page` becomes nullable: a row cites a page or a cell, never both, never neither.
--   A workbook row has no printed page either.
-- - `page_image_sha256` holds, for a workbook row, the sha256 of the sheet's XML part: the
--   evidence the row was read from, as the page image is for a PDF row.
-- - The cell key gains the sheet and cell, so two cells with one label and one column
--   (FLCT prints "Total (ktCO2e)" for each region, all under FY2025) are both kept. For a
--   PDF row both are null, and `nulls not distinct` makes the key behave exactly as before.
-- - `peer_disclosures_verified` is `select *`, which Postgres expands when the view is
--   created; it is re-created so it carries the new columns.
--
-- Idempotent: safe to re-run.

alter table peer_disclosures add column if not exists sheet_name text;
alter table peer_disclosures add column if not exists cell_ref text;
alter table peer_disclosures alter column pdf_page drop not null;

alter table peer_disclosures drop constraint if exists peer_disclosures_cell_ref_check;
alter table peer_disclosures add constraint peer_disclosures_cell_ref_check
    check (cell_ref is null or cell_ref ~ '^[A-Z]{1,3}[1-9][0-9]*$');

alter table peer_disclosures drop constraint if exists peer_disclosures_location_check;
alter table peer_disclosures add constraint peer_disclosures_location_check
    check ((pdf_page is not null and sheet_name is null and cell_ref is null)
           or (pdf_page is null and sheet_name is not null and cell_ref is not null
               and printed_page is null));

alter table peer_disclosures drop constraint if exists peer_disclosures_cell_key;
alter table peer_disclosures add constraint peer_disclosures_cell_key
    unique nulls not distinct
    (source_id, pdf_page, sheet_name, cell_ref, drafted_row_label, column_header,
     drafted_method);

create or replace view peer_disclosures_verified as
    select * from peer_disclosures
    where verified_at is not null and verification <> 'rejected';

comment on table peer_disclosures is
    'Peer-report emissions figures: drafted by an LLM from report pages, or read by code from '
    'the issuer''s own workbook. A row counts only once verified by hand (verified_at set); '
    'read peer_disclosures_verified.';
comment on column peer_disclosures.sheet_name is
    'Workbook rows only: the sheet the value was read from (with cell_ref, the citation).';
comment on column peer_disclosures.cell_ref is
    'Workbook rows only: the A1 reference of the cell the value was read from.';
