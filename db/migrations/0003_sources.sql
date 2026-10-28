-- 0003: sources - the registry of every document Carbon R reads
--
-- docs/SOURCES.md records provenance for a human reader. This table records the same facts for
-- the code, so that a retrieved passage (0004) can cite its document by foreign key rather
-- than by a free-text title that could drift, and so that an ingest can refuse a local file
-- whose bytes are not the ones that were registered (sha256).
--
-- What it does NOT do: replace emission_factors' own provenance columns. Those stay as they
-- are, NOT NULL, per row (0001). Linking them here would be a second place for the same
-- fact; the registry lists the factor datasets for completeness only.
--
-- `licence` is nullable on purpose: null means "not yet read from the publisher's own
-- terms", which is a different statement from any licence string, and is not papered
-- over with a guess (rule 5).
--
-- Idempotent: safe to re-run.

create table if not exists sources (
    id                  text        primary key,
    title               text        not null,
    publisher           text        not null,
    year                integer     check (year between 1990 and 2100),
    url                 text        not null,
    licence             text,
    retrieved_at        date        not null,
    -- Of the local file's bytes. An ingest compares before reading.
    sha256              text        not null check (sha256 ~ '^[0-9a-f]{64}$'),
    kind                text        not null,
    verification_status text        not null,
    -- PDF page index minus the page number printed in the footer, where it is constant
    -- through the body of a document. Null for spreadsheets, or when no single offset holds.
    printed_page_offset integer,
    notes               text,

    constraint sources_kind_check
        check (kind in ('methodology', 'factor_dataset', 'peer_report')),
    constraint sources_verification_status_check
        check (verification_status in ('verified from primary', 'secondary only'))
);

comment on table sources is
    'Every document Carbon R reads, with its licence and the sha256 of the local copy. The '
    'files themselves are never committed; see SOURCES.md.';
comment on column sources.licence is
    'As read from the publisher''s own terms. Null = not yet read, never a guess.';
