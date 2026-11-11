"""data/peer_sources.json: its shape anywhere, its files where the reports are present."""

from collections import Counter

import pytest

from peer_intake import RAW, check, load_peer_sources

SOURCES = load_peer_sources()


def test_the_file_validates_and_names_twelve_companies():
    assert len({s.company for s in SOURCES.sources}) == 12


def test_each_file_appears_once():
    names = Counter(s.local_filename for s in SOURCES.sources)
    assert max(names.values()) == 1
    assert len({s.sha256 for s in SOURCES.sources}) == len(SOURCES.sources)


def test_an_assumed_start_is_never_labelled_stated():
    for s in SOURCES.sources:
        if "assumed" in s.notes.lower():
            assert s.period_basis == "start_assumed", s.local_filename


@pytest.mark.skipif(
    not all((RAW / s.local_filename).exists() for s in SOURCES.sources),
    reason="needs the peer reports in data/raw/peer/ (git-ignored)",
)
def test_every_file_matches_its_recorded_bytes():
    assert check() == 0


def test_every_declared_continuation_joins_two_consecutive_table_pages():
    from carbon_r.peer_sources import TABLE_CONTINUES, TABLE_PAGES

    for source_id, pages in TABLE_CONTINUES.items():
        for page, before in pages.items():
            assert before == page - 1, (source_id, page)
            assert {page, before} <= set(TABLE_PAGES[source_id]), (source_id, page)


def test_every_declared_page_year_is_a_table_page_of_the_reports_own_year():
    from carbon_r.peer_sources import PAGE_YEARS, TABLE_PAGES
    from carbon_r.sources import PEERS

    for source_id, pages in PAGE_YEARS.items():
        for page, year in pages.items():
            assert page in TABLE_PAGES[source_id], (source_id, page)
            assert year == PEERS[source_id].report_year, (source_id, page)


def test_workbook_tables_name_only_workbook_sources_and_do_not_overlap():
    from carbon_r.peer_sources import WORKBOOK_TABLES
    from carbon_r.sources import PEERS

    for source_id, tables in WORKBOOK_TABLES.items():
        assert PEERS[source_id].is_workbook, source_id
        for i, a in enumerate(tables):
            for b in tables[i + 1 :]:
                if a.sheet == b.sheet:
                    assert a.last_row < b.first_row or b.last_row < a.first_row, source_id
