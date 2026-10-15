-- 0001: emission_factors
--
-- The table every emission figure is traceable to. Rule 2 of CLAUDE.md ("every number
-- carries its source") is enforced here in the schema rather than left to callers:
-- publisher, source_document, source_url and retrieved_at are NOT NULL, so a factor
-- cannot be inserted anonymously.
--
-- Idempotent: safe to re-run.

create table if not exists emission_factors (
    id              bigserial primary key,

    -- What is being measured, and in what terms. The UNIQUE constraint below treats
    -- (category, unit, year) as the identity of a factor: the same category published in
    -- different units is a different row, which keeps unit conversion explicit in code
    -- rather than implicit in a lookup.
    category        text        not null,
    factor          numeric     not null check (factor >= 0),
    unit            text        not null,
    year            integer     not null check (year between 1990 and 2100),

    -- Provenance. All required.
    publisher       text        not null,
    source_document text        not null,
    source_url      text        not null,
    -- Page number, table id, or sheet reference. Nullable only because spreadsheet
    -- sources have no page; a PDF-sourced factor should always set it.
    source_page     text,
    retrieved_at    date        not null,

    -- Where the factor is valid. Drives the "non-Singapore factor" disclosure that
    -- UK DESNZ and US EPA rows require on every result that uses them.
    jurisdiction    text        not null default 'SG',

    -- False until the value has been read from the publisher's own document.
    -- A factor with verified = false must not appear in a result presented as established.
    verified        boolean     not null default false,

    notes           text,

    constraint emission_factors_category_unit_year_key unique (category, unit, year)
);

-- Supports the fallback-year rule: "latest published year at or before the bill's year".
create index if not exists emission_factors_category_year_idx
    on emission_factors (category, year desc);

comment on table emission_factors is
    'Emission factors with full provenance. Queried by exact SQL only, never by vector '
    'similarity — see CLAUDE.md rule 3.';
comment on column emission_factors.verified is
    'True only when the value was read from the publisher''s primary document.';
comment on column emission_factors.jurisdiction is
    'ISO-ish region code. Non-SG values must be disclosed as non-Singapore in results.';
