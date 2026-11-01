-- 0007: the Scope 2 method is part of what makes a peer cell distinct (Tier 2 Block 4 Part B, 2b)
--
-- UOB's assurance statement (PDF p.189) prints its location- and market-based figures under
-- one row label and one column, told apart only by the words in the cell. Keyed on (source,
-- page, label, column) alone, the second of each pair was dropped as a duplicate before it
-- reached the table. The key now includes the method AS DRAFTED.
--
-- `drafted_method` keeps what the model read, as `drafted_row_label` does for the label: the
-- author may correct `method` at verification (OCBC p.58 states none), and a key on the
-- corrected value would let a re-load of the original draft insert the misread again. It is
-- filled from `method` only when the column is first added; no method had been corrected
-- before then, because there was no way to correct one.
--
-- `nulls not distinct` (Postgres 15+): most rows have no method, and with nulls distinct two
-- such rows would never conflict, so `on conflict` would never fire and a second load would
-- duplicate every one of them.
--
-- Widening a unique key cannot invalidate existing rows. Idempotent: safe to re-run.

do $$
begin
    if not exists (
        select 1 from information_schema.columns
        where table_name = 'peer_disclosures' and column_name = 'drafted_method'
    ) then
        alter table peer_disclosures add column drafted_method text;
        update peer_disclosures set drafted_method = method;
    end if;
end
$$;

alter table peer_disclosures drop constraint if exists peer_disclosures_drafted_method_check;
alter table peer_disclosures add constraint peer_disclosures_drafted_method_check
    check (drafted_method is null or drafted_method in ('location', 'market'));

alter table peer_disclosures drop constraint if exists peer_disclosures_cell_key;
alter table peer_disclosures add constraint peer_disclosures_cell_key
    unique nulls not distinct
    (source_id, pdf_page, drafted_row_label, column_header, drafted_method);

comment on column peer_disclosures.drafted_method is
    'The method the drafting model read. Never updated, so a corrected method stays measurable.';
