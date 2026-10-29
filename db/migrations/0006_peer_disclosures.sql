-- 0006: peer_disclosures - emissions figures from peer reports, drafted then verified by hand
--
-- One row per table cell: one figure, for one scope, in one report's own period, as printed.
-- Rows are DRAFTED by a vision model reading page images (scripts/draft_peer_disclosures.py)
-- and count only once the author has checked them against the PDF page they cite (CLAUDE.md
-- rule 4, the peer-disclosure exception). A draft has verified_by / verified_at null; code
-- that uses a peer figure reads `peer_disclosures_verified`, never this table.
--
-- Every row carries the evidence for that check: the PDF page, and the row label and column
-- header exactly as printed. `figure_printed` is the text on the page with footnote markers
-- left out; `figure` is that text parsed in Python (commas stripped, never rescaled), null
-- when it does not parse. `drafted_figure_printed` is what the model read and is never
-- updated, so a correction leaves the draft visible and draft accuracy stays measurable.
--
-- `unit` is as printed and never converted. `scope` allows a combined total only as the
-- report prints it; nothing here is ever summed. `method` (location- or market-based) is
-- allowed on any row that includes Scope 2: a printed total carries its Scope 2 part's.
--
-- Verification (scripts/verify_peer_disclosures.py) sets verified_by, verified_at and
-- `verification`: confirmed (correct as drafted), corrected (`figure_printed` or `row_label`
-- changed; the drafted_* columns keep what the model read) or rejected (not a figure this
-- table should hold; kept for the record, left out of the view). `blind_figure_printed` is
-- the author's reading of a blind-sample row, entered before the draft value was seen. The
-- unique key uses `drafted_row_label`, so a corrected label still identifies its draft.
--
-- Idempotent: safe to re-run. The loader upserts on the unique key and never touches a
-- verified row (`... do update ... where peer_disclosures.verified_at is null`).

create table if not exists peer_disclosures (
    id                      bigserial   primary key,
    source_id               text        not null references sources(id),
    company                 text        not null,
    period_start            date        not null,
    period_end              date        not null,
    scope                   text        not null,
    scope3_category         smallint,
    figure_printed          text        not null,
    figure                  numeric,
    drafted_figure_printed  text        not null,
    unit                    text        not null,
    method                  text,
    pdf_page                integer     not null check (pdf_page >= 1),
    printed_page            integer,
    row_label               text        not null,
    drafted_row_label       text        not null,
    column_header           text        not null,
    notes                   text,
    drafted_by              text        not null,
    drafted_at              timestamptz not null,
    draft_request_hash      text        not null,
    page_image_sha256       text        not null check (page_image_sha256 ~ '^[0-9a-f]{64}$'),
    blind_sample            boolean     not null default false,
    verified_by             text,
    verified_at             timestamptz,
    verification            text,
    blind_figure_printed    text,

    constraint peer_disclosures_period_check check (period_end > period_start),
    constraint peer_disclosures_scope_check
        check (scope in ('1', '2', '3', '1+2', '1+2+3')),
    constraint peer_disclosures_scope3_category_check
        check (scope3_category is null or (scope = '3' and scope3_category between 1 and 15)),
    constraint peer_disclosures_method_check
        check (method is null
               or (method in ('location', 'market') and scope in ('2', '1+2', '1+2+3'))),
    constraint peer_disclosures_verified_pair_check
        check ((verified_by is null) = (verified_at is null)
               and (verification is null) = (verified_at is null)),
    constraint peer_disclosures_verification_check
        check (verification in ('confirmed', 'corrected', 'rejected')),
    constraint peer_disclosures_blind_check
        check (blind_figure_printed is null or blind_sample),
    constraint peer_disclosures_cell_key
        unique (source_id, pdf_page, drafted_row_label, column_header)
);

create or replace view peer_disclosures_verified as
    select * from peer_disclosures
    where verified_at is not null and verification <> 'rejected';

comment on table peer_disclosures is
    'Peer-report emissions figures, LLM-drafted from page images. A row counts only once '
    'verified by hand (verified_at set); read peer_disclosures_verified.';
comment on column peer_disclosures.drafted_figure_printed is
    'What the drafting model read. Never updated, so corrections stay measurable.';
