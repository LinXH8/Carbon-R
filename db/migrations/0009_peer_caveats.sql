-- 0009: a caveat printed with a verified peer figure (Tier 3 Block 1, proposal 1)
--
-- A footnote can qualify a figure - "the last three months of 2025 are best estimates" (ST
-- Engineering's 2025 data). Rule 7 asks for such a reading to be flagged, so the footnote
-- enters the row it qualifies:
--
-- - `caveat_printed`: the footnote text as printed (verbatim, without its marker);
-- - `caveat_kind`: estimate, restated, boundary, method or other;
-- - where it is printed: `caveat_pdf_page`, or for a workbook `caveat_cell` ("Sheet!A1") -
--   one or the other, since a footnote is often on another page than its figure;
-- - `caveat_by`, `caveat_at`: who added it and when. A caveat is added after the row was
--   verified, by a separate command that touches only these columns.
--
-- A caveat is whole or absent, and only ever on a verified row. Every condition below says
-- `is not null` explicitly: a CHECK passes when it evaluates to NULL, and without them a
-- caveat printed nowhere, or with no kind, was accepted (caught by the migration's test).
-- Nothing here changes a figure, label or method. The verified view is re-created so it carries the new columns.
--
-- Idempotent: safe to re-run.

alter table peer_disclosures add column if not exists caveat_printed text;
alter table peer_disclosures add column if not exists caveat_kind text;
alter table peer_disclosures add column if not exists caveat_pdf_page integer;
alter table peer_disclosures add column if not exists caveat_cell text;
alter table peer_disclosures add column if not exists caveat_by text;
alter table peer_disclosures add column if not exists caveat_at timestamptz;

alter table peer_disclosures drop constraint if exists peer_disclosures_caveat_check;
alter table peer_disclosures add constraint peer_disclosures_caveat_check
    check (
        (caveat_printed is null and caveat_kind is null and caveat_pdf_page is null
         and caveat_cell is null and caveat_by is null and caveat_at is null)
        or (caveat_printed is not null and length(btrim(caveat_printed)) > 0
            and caveat_kind is not null
            and caveat_kind in ('estimate', 'restated', 'boundary', 'method', 'other')
            and caveat_by is not null and caveat_at is not null
            and verified_at is not null
            and ((caveat_pdf_page is not null and caveat_pdf_page >= 1 and caveat_cell is null)
                 or (caveat_pdf_page is null and caveat_cell is not null
                     and caveat_cell ~ '^.+![A-Z]{1,3}[1-9][0-9]*$')))
    );

create or replace view peer_disclosures_verified as
    select * from peer_disclosures
    where verified_at is not null and verification <> 'rejected';

comment on column peer_disclosures.caveat_printed is
    'A footnote that qualifies this verified figure, as printed. Set by the author after '
    'verification; never changes the figure.';
