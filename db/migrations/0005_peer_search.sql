-- 0005: indexes for searching peer reports (Tier 2 Block 4 Part B)
--
-- `search_peer_disclosures` ranks peer chunks two ways and fuses the rankings: by embedding
-- distance (an exact scan, as for methodology - see 0004 and ADR 0002) and by full-text
-- match on the generated `tsv` column. The GIN index serves the keyword leg; the btree
-- serves the company / report-year filters both legs apply.
--
-- No columns change: company, sector, report_year and tsv were created by 0004 so that this
-- part would not alter a populated table.
--
-- Idempotent: safe to re-run.

create index if not exists document_chunks_tsv_idx
    on document_chunks using gin (tsv);

create index if not exists document_chunks_peer_filter_idx
    on document_chunks (corpus, company, report_year);
