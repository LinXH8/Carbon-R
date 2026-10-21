-- 0004: document_chunks - the one table in Carbon R searched by similarity
--
-- Rule 3 in CLAUDE.md: factor VALUES are fetched by exact SQL and never by embedding
-- distance. This table is the sanctioned exception - methodology prose (and, from Part B
-- of Tier 2 Block 4, peer sustainability reports) - and it holds no factor values.
--
-- Three columns exist so that two searches can never silently compare unlike things:
--
--   embedding_model  vectors from different models are not comparable, and this table may
--                    hold rows from more than one while a model change is in progress.
--   chunker_version  a hash of the chunk parameters. Changing the window size produces
--                    different text, so different hashes - without this column the old rows
--                    would sit beside the new ones and both would be searched.
--   corpus           methodology and peer passages answer different questions.
--
-- Every search filters on all three (tools/methodology.py builds that SQL in one place).
--
-- Identity is the position of a chunk - (source, version, model, page, index) - not its
-- text: the same sentence printed on two pages is two citations, and must be two rows.
--
-- No approximate (HNSW / IVFFlat) index. The corpus is a few hundred chunks, so an exact
-- scan is effectively instant and fully deterministic, which keeps the retrieval eval
-- reproducible from a commit SHA. An approximate index is the upgrade path if the corpus
-- grows, and the ADR records it.
--
-- The peer columns (company, sector, report_year) and the keyword column (tsv) are here
-- from the start, nullable, so Part B does not alter a populated table.
--
-- Idempotent: safe to re-run.

-- Supabase keeps extensions in an `extensions` schema that is on the search path; a plain
-- Postgres (the docker-compose image) has no such schema. Install into it where it exists.
-- If the extension is already installed anywhere, both branches are a no-op.
do $$
begin
    if exists (select 1 from pg_namespace where nspname = 'extensions') then
        create extension if not exists vector with schema extensions;
    else
        create extension if not exists vector;
    end if;
end
$$;

create table if not exists document_chunks (
    id              bigserial   primary key,
    source_id       text        not null references sources (id),
    corpus          text        not null,
    chunker_version text        not null,
    embedding_model text        not null,

    -- Citation. pdf_page is the physical page (1-based); printed_page is the number in the
    -- page's own footer, set only where the footer confirms it, otherwise null.
    pdf_page        integer     not null check (pdf_page >= 1),
    chunk_index     integer     not null check (chunk_index >= 0),
    printed_page    integer,
    section         text,

    text            text        not null check (length(text) > 0),
    content_sha256  text        not null check (content_sha256 ~ '^[0-9a-f]{64}$'),
    embedding       vector(1536) not null,

    -- Part B: peer reports. Null for methodology rows.
    company         text,
    sector          text,
    report_year     integer,

    tsv             tsvector generated always as (to_tsvector('english', text)) stored,

    constraint document_chunks_corpus_check check (corpus in ('methodology', 'peer')),
    constraint document_chunks_position_key
        unique (source_id, chunker_version, embedding_model, pdf_page, chunk_index)
);

-- Serves the filter every search applies before it ranks.
create index if not exists document_chunks_filter_idx
    on document_chunks (corpus, embedding_model, chunker_version, source_id);

comment on table document_chunks is
    'Passages for similarity search: methodology prose and peer reports only. Never holds an '
    'emission factor value (CLAUDE.md rule 3).';
comment on column document_chunks.printed_page is
    'Footer page number, only where the footer confirms pdf_page - sources.printed_page_offset.';
