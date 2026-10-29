"""peer_disclosures drafting (Tier 2 Block 4 Part B, 2a). Every figure here is invented."""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from carbon_r.config import get_settings
from carbon_r.db import MIGRATIONS_DIR
from carbon_r.models.peer_disclosure import (
    UPSERT_DISCLOSURE,
    DraftedCell,
    PeerDisclosure,
    PeerTableReading,
    blind_sample,
    parse_figure,
    report_year_of_header,
    to_disclosures,
)
from carbon_r.peer_sources import TABLE_PAGES, PeerSource
from carbon_r.sources import PEER_IDS


def _source(company: str = "DBS", start: str = "2025-01-01", end: str = "2025-12-31") -> PeerSource:
    return PeerSource(
        company=company,
        sector="bank",
        document_kind="sustainability_report",
        period_start=start,
        period_end=end,
        period_basis="stated",
        period_pdf_page=1,
        period_printed_page=None,
        url="https://example.invalid/report.pdf",
        url_note=None,
        local_filename="peer/x.pdf",
        sha256="0" * 64,
        page_count=10,
        licence_note="test",
        notes="test",
    )


def _cell(**overrides) -> DraftedCell:
    values = {
        "row_label_printed": "Scope 1",
        "column_header_printed": "2025",
        "figure_printed": "1,234",
        "footnote_markers_printed": None,
        "unit_printed": "tCO2e",
        "scope": "1",
        "scope3_category": None,
        "method": None,
        "method_evidence_printed": None,
    } | overrides
    return DraftedCell(**values)


def _reading(*cells: DraftedCell) -> PeerTableReading:
    return PeerTableReading(
        page_has_ghg_table=True, table_title_printed="T", cells=list(cells), notes=None
    )


# --- the column header decides the year ---------------------------------------------------


@pytest.mark.parametrize(
    ("header", "year"),
    [
        ("2025", 2025),
        ("FY2025", 2025),
        ("FY25", 2025),
        ("FY 2025", 2025),
        ("FY2025/26", 2026),
        ("FY25/26", 2026),
        ("2025/26", 2026),
        ("Singapore > 2025", 2025),
        ("Total Portfolio > FY2025", 2025),
        ("2025 vs 2024", None),
        ("2019-2025", None),  # a range is two years, not a split year
        ("% change", None),
        ("Target", None),
        ("", None),
    ],
)
def test_report_year_of_header(header, year):
    assert report_year_of_header(header) == year


def test_only_the_reports_own_year_column_is_kept():
    reading = _reading(
        _cell(column_header_printed="2025"),
        _cell(column_header_printed="2024"),
        _cell(column_header_printed="2025 vs 2024", figure_printed="5"),
        _cell(column_header_printed="% change", figure_printed="5"),
    )
    draft = to_disclosures(reading, _source(), pdf_page=3)
    assert [r.column_header for r in draft.rows] == ["2025"]
    assert draft.dropped == {"column_not_report_year": 3}


def test_a_split_financial_year_matches_the_year_it_ends():
    source = _source(company="Mapletree Logistics Trust", start="2025-04-01", end="2026-03-31")
    reading = _reading(
        _cell(column_header_printed="FY25/26"), _cell(column_header_printed="FY24/25")
    )
    draft = to_disclosures(reading, source, pdf_page=3)
    assert [r.column_header for r in draft.rows] == ["FY25/26"]
    assert (draft.rows[0].period_start, draft.rows[0].period_end) == (
        date(2025, 4, 1),
        date(2026, 3, 31),
    )


# --- the figure ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("printed", "value"),
    [
        ("1,234", Decimal("1234")),
        ("12", Decimal("12")),
        ("1,234,567.8", Decimal("1234567.8")),
        ("0.5", Decimal("0.5")),
        ("−7", Decimal("-7")),
        ("2,5113", None),  # a fused footnote marker: never a best guess
        ("1,23", None),
        ("1,234 tCO2e", None),
        ("12a", None),
        ("~1,000", None),
    ],
)
def test_parse_figure_is_strict(printed, value):
    assert parse_figure(printed) == value


def test_a_malformed_figure_is_kept_unparsed_and_flagged():
    draft = to_disclosures(_reading(_cell(figure_printed="2,5113")), _source(), pdf_page=3)
    (row,) = draft.rows
    assert row.figure is None and row.figure_printed == "2,5113"
    assert "malformed thousands" in row.notes and "did not parse" in row.notes
    assert len(draft.problems) == 1


def test_footnote_markers_are_recorded_not_parsed():
    draft = to_disclosures(
        _reading(_cell(figure_printed="2,511", footnote_markers_printed="3")), _source(), pdf_page=3
    )
    (row,) = draft.rows
    assert row.figure == Decimal("2511")
    assert "footnote marker(s) printed: '3'" in row.notes


def test_blank_cells_intensity_and_non_ghg_units_are_dropped():
    reading = _reading(
        _cell(figure_printed="-"),
        _cell(row_label_printed="Intensity", unit_printed="kgCO2e/m2"),
        _cell(row_label_printed="Intensity 2", unit_printed="tCO2e per employee"),
        _cell(row_label_printed="Energy", unit_printed="MWh"),
        _cell(row_label_printed="Scope 1 total"),
    )
    draft = to_disclosures(reading, _source(), pdf_page=3)
    assert [r.row_label for r in draft.rows] == ["Scope 1 total"]
    assert draft.dropped == {"no_figure": 1, "intensity_unit": 2, "unit_not_ghg": 1}


def test_a_repeated_cell_key_keeps_the_first():
    reading = _reading(_cell(figure_printed="1"), _cell(figure_printed="2"))
    draft = to_disclosures(reading, _source(), pdf_page=3)
    assert [r.figure_printed for r in draft.rows] == ["1"]
    assert draft.dropped == {"duplicate_cell_key": 1}


# --- what the model may and may not say -----------------------------------------------------


def test_method_on_a_non_scope_2_row_is_not_kept():
    draft = to_disclosures(
        _reading(_cell(method="market", method_evidence_printed="market-based")),
        _source(),
        pdf_page=3,
    )
    (row,) = draft.rows
    assert row.method is None and "not kept" in row.notes
    assert len(draft.problems) == 1


def test_a_total_that_includes_scope_2_keeps_its_method():
    cell = _cell(
        row_label_printed="Total GHG emissions, market-based",
        scope="1+2+3",
        method="market",
        method_evidence_printed="market-based",
    )
    (row,) = to_disclosures(_reading(cell), _source(), pdf_page=3).rows
    assert row.method == "market"


def test_a_stated_method_is_kept_with_its_evidence():
    cell = _cell(
        row_label_printed="Scope 2 (Market-based)",
        scope="2",
        method="market",
        method_evidence_printed="Scope 2 (Market-based)",
    )
    (row,) = to_disclosures(_reading(cell), _source(), pdf_page=3).rows
    assert row.method == "market"
    assert "method stated as: 'Scope 2 (Market-based)'" in row.notes


def test_a_scope_3_category_off_scope_3_is_not_kept():
    (row,) = to_disclosures(_reading(_cell(scope3_category=6)), _source(), pdf_page=3).rows
    assert row.scope3_category is None and "category 6" in row.notes


def test_the_reading_names_no_page_period_or_value():
    fields = set(PeerTableReading.model_fields) | set(DraftedCell.model_fields)
    assert not fields & {"pdf_page", "printed_page", "period_start", "period_end", "period"}
    assert "figure" not in fields  # only figure_printed: the value is parsed in Python


def test_page_period_and_printed_page_come_from_the_registry():
    peer = next(s for s in _registered() if s.source_id == "peer_ocbc_2025")  # offset 1
    (row,) = to_disclosures(_reading(_cell()), peer, pdf_page=58).rows
    assert row.pdf_page == 58 and row.printed_page == 57
    assert (row.period_start, row.period_end) == (peer.period_start, peer.period_end)

    sembcorp = next(s for s in _registered() if s.source_id == "peer_sembcorp_2025")
    (row,) = to_disclosures(_reading(_cell()), sembcorp, pdf_page=37).rows
    assert row.printed_page is None  # two-up: no offset


def _registered() -> list[PeerSource]:
    from carbon_r.peer_sources import load_peer_sources

    return load_peer_sources().sources


def test_the_row_model_refuses_method_or_category_on_the_wrong_scope():
    base = to_disclosures(_reading(_cell()), _source(), pdf_page=3).rows[0].model_dump()
    with pytest.raises(ValidationError):
        PeerDisclosure.model_validate(base | {"method": "location"})
    with pytest.raises(ValidationError):
        PeerDisclosure.model_validate(base | {"scope3_category": 1})


def test_table_pages_name_registered_peer_sources():
    assert set(TABLE_PAGES) <= set(PEER_IDS)
    for source_id, pages in TABLE_PAGES.items():
        count = next(s for s in _registered() if s.source_id == source_id).page_count
        assert pages == sorted(set(pages)) and all(1 <= p <= count for p in pages)


# --- the blind sample -----------------------------------------------------------------------


def test_the_blind_sample_is_deterministic_and_order_free():
    keys = [("s", 1, f"row {i}", "2025") for i in range(20)]
    first = blind_sample(keys)
    assert len(first) == 4
    assert blind_sample(list(reversed(keys))) == first
    assert blind_sample(keys, seed=1) != first or len(keys) == 4


def test_the_blind_sample_takes_at_least_one():
    assert len(blind_sample([("s", 1, "Scope 1", "2025")])) == 1
    assert blind_sample([]) == set()


# --- the table, executed (needs a database; skipped in CI) ----------------------------------


@pytest.mark.skipif(not get_settings().database_configured, reason="DATABASE_URL not configured")
def test_the_table_keeps_drafts_out_of_the_verified_view_and_never_overwrites_a_verified_row():
    """Applies 0006 and runs the loader's upsert inside a transaction that is rolled back."""
    import psycopg

    from carbon_r.db import connect

    row = to_disclosures(_reading(_cell()), _source(), pdf_page=3).rows[0].model_dump() | {
        "source_id": "zz_test_source",
        "drafted_by": "zz-test-model",
        "drafted_at": "2026-10-29T00:00:00+00:00",
        "draft_request_hash": "zz",
        "page_image_sha256": "0" * 64,
    }
    with connect() as conn:
        try:
            conn.execute((MIGRATIONS_DIR / "0006_peer_disclosures.sql").read_text())
            conn.execute(
                "insert into sources (id, title, publisher, year, url, retrieved_at, sha256, "
                "kind, verification_status) values ('zz_test_source', 't', 'p', 1999, 'u', "
                "'2026-10-29', repeat('0', 64), 'peer_report', 'secondary only')"
            )
            first = conn.execute(UPSERT_DISCLOSURE, row).fetchone()
            again = conn.execute(UPSERT_DISCLOSURE, row).fetchone()
            changed = conn.execute(UPSERT_DISCLOSURE, row | {"figure_printed": "9"}).fetchone()
            in_view = conn.execute(
                "select count(*) from peer_disclosures_verified where source_id = 'zz_test_source'"
            ).fetchone()[0]

            conn.execute(
                "update peer_disclosures set verified_by = 'author', verified_at = now(), "
                "verification = 'confirmed', figure_printed = '1,234' "
                "where source_id = 'zz_test_source'"
            )
            redraft = conn.execute(UPSERT_DISCLOSURE, row | {"figure_printed": "8"}).fetchone()
            kept = conn.execute(
                "select figure_printed from peer_disclosures_verified "
                "where source_id = 'zz_test_source'"
            ).fetchone()[0]

            with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
                conn.execute(
                    "update peer_disclosures set verified_at = null "
                    "where source_id = 'zz_test_source'"
                )
            with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
                conn.execute(UPSERT_DISCLOSURE, row | {"row_label": "x", "method": "market"})

            # A corrected label: re-loading the original draft must not re-insert the misread.
            conn.execute(
                "update peer_disclosures set row_label = 'Scope 1 (fixed)', "
                "verification = 'corrected' where source_id = 'zz_test_source'"
            )
            reload = conn.execute(UPSERT_DISCLOSURE, row).fetchone()
            (count,) = conn.execute(
                "select count(*) from peer_disclosures where source_id = 'zz_test_source'"
            ).fetchone()

            # A rejected row is kept but leaves the verified view.
            conn.execute(
                "update peer_disclosures set verification = 'rejected' "
                "where source_id = 'zz_test_source'"
            )
            (rejected_in_view,) = conn.execute(
                "select count(*) from peer_disclosures_verified where source_id = 'zz_test_source'"
            ).fetchone()

            # A blind value only on a blind-sample row.
            with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
                conn.execute(
                    "update peer_disclosures set blind_figure_printed = '1' "
                    "where source_id = 'zz_test_source'"
                )
        finally:
            conn.rollback()

    assert first == (True,)  # inserted
    assert again is None  # unchanged: not touched
    assert changed == (False,)  # updated, still a draft
    assert in_view == 0
    assert redraft is None and kept == "1,234"
    assert reload is None and count == 1
    assert rejected_in_view == 0
