"""Peer rows read by code from an issuer's workbook (Tier 3 Block 1: FLCT, CICT).

No model is involved: the author declares each table and lists each cell (sheet, cell, label
and header as printed, scope, method), and Python reads the value as the workbook displays
it. Every check here refuses rather than guesses. Invented workbook and values throughout.
"""

import zipfile
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from carbon_r.config import get_settings
from carbon_r.db import MIGRATIONS_DIR
from carbon_r.models.peer_disclosure import UPSERT_DISCLOSURE, PeerDisclosure, blind_sample
from carbon_r.peer_disclosures_file import VERIFIED_FILE, read_file
from carbon_r.peer_sources import PeerSource, WorkbookTable
from carbon_r.peer_workbook import (
    WorkbookInventoryEntry,
    read_workbook,
)
from carbon_r.tools.peer_figures import citation_text

_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_STYLES = ["0", "3", "164"]  # General, built-in #,##0, custom #,##0.0
GENERAL, THOUSANDS = range(2)  # style indexes into _STYLES


def _workbook(path: Path, cells: dict[str, tuple[str, str, int]], sheet: str = "GHG") -> Path:
    """`cells`: {ref: (value, "s" for text or "n" for number, style index)}."""
    strings: list[str] = []
    rows: dict[int, list[str]] = {}
    for ref, (value, kind, style) in cells.items():
        row = int("".join(ch for ch in ref if ch.isdigit()))
        if kind == "s":
            strings.append(value)
            xml = f'<c r="{ref}" t="s" s="{style}"><v>{len(strings) - 1}</v></c>'
        else:
            xml = f'<c r="{ref}" s="{style}"><v>{value}</v></c>'
        rows.setdefault(row, []).append(xml)
    sheet_xml = "".join(f'<row r="{r}">{"".join(c)}</row>' for r, c in sorted(rows.items()))
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>',
        )
        archive.writestr(
            "xl/workbook.xml",
            f'<workbook xmlns="{_MAIN}" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f'<sheets><sheet name="{sheet}" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        archive.writestr(
            "xl/sharedStrings.xml",
            f'<sst xmlns="{_MAIN}">' + "".join(f"<si><t>{s}</t></si>" for s in strings) + "</sst>",
        )
        archive.writestr(
            "xl/styles.xml",
            f'<styleSheet xmlns="{_MAIN}"><numFmts count="1">'
            '<numFmt numFmtId="164" formatCode="#,##0.0"/></numFmts><cellXfs count="3">'
            + "".join(f'<xf numFmtId="{i}"/>' for i in _STYLES)
            + "</cellXfs></styleSheet>",
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            f'<worksheet xmlns="{_MAIN}"><sheetData>{sheet_xml}</sheetData></worksheet>',
        )
    return path


# A table with a unit column (the CICT shape): labels in A, units in B, years in C and D.
_CELLS = {
    "A6": ("Metrics", "s", GENERAL),
    "B6": ("Unit", "s", GENERAL),
    "C6": ("2024", "n", GENERAL),
    "D6": ("2025", "n", GENERAL),
    "A7": ("Scope 11", "s", GENERAL),
    "B7": ("tonnes CO2e", "s", GENERAL),
    "C7": ("3000", "n", THOUSANDS),
    "D7": ("3456.4999999999995", "n", THOUSANDS),
    "A8": ("Scope 2 (Market-based)", "s", GENERAL),
    "B8": ("tonnes CO2e", "s", GENERAL),
    "D8": ("1,234", "s", GENERAL),  # a number typed as text
    "A9": ("Cat 6 Business travel", "s", GENERAL),
    "B9": ("tonnes CO2e", "s", GENERAL),
    "D9": ("-", "s", GENERAL),
}
_TABLE = WorkbookTable(
    sheet="GHG", label_column="A", header_row=6, first_row=7, last_row=9, unit_column="B"
)


def _source(**kw) -> PeerSource:
    values = dict(
        company="CapitaLand Integrated Commercial Trust",
        sector="reit",
        document_kind="data_pack",
        period_start="2025-01-01",
        period_end="2025-12-31",
        period_basis="stated",
        period_pdf_page=None,
        period_cell="GHG!D6",
        period_printed_page=None,
        url="https://example.invalid/pack.xlsx",
        url_note=None,
        local_filename="peer/pack.xlsx",
        sha256="0" * 64,
        page_count=None,
        licence_note="test",
        notes="test",
    )
    return PeerSource(**(values | kw))


def _entry(ref="D7", label="Scope 11", header="2025", scope="1", method=None, cat=None):
    return WorkbookInventoryEntry(
        sheet_name="GHG", cell_ref=ref, row_label=label, column_header=header, scope=scope,
        scope3_category=cat, method=method,
    )  # fmt: skip


def _read(tmp_path, entries, cells=_CELLS, tables=(_TABLE,)):
    return read_workbook(_workbook(tmp_path / "pack.xlsx", cells), _source(), list(tables), entries)


def _both(**kw):
    return [_entry(**kw), _entry(ref="D8", label="Scope 2 (Market-based)", scope="2",
                                 method="market")]  # fmt: skip


def test_listed_cells_are_read_as_displayed_and_cited_by_sheet_and_cell(tmp_path):
    draft = _read(tmp_path, _both())
    assert draft.problems == [] and draft.unlisted == []
    first, second = draft.rows
    assert (first.figure_printed, str(first.figure)) == ("3,456", "3456")
    assert (first.pdf_page, first.printed_page) == (None, None)
    assert (first.sheet_name, first.cell_ref, first.unit) == ("GHG", "D7", "tonnes CO2e")
    assert first.row_label == first.drafted_row_label == "Scope 11"  # as printed
    assert "more decimals" in first.notes  # the stored value is not what is shown
    assert second.figure_printed == "1,234" and "typed as text" in second.notes
    assert second.method == second.drafted_method == "market"


@pytest.mark.parametrize(
    ("entry", "problem"),
    [
        (_entry(label="Scope 1"), "label"),  # the workbook prints "Scope 11"
        (_entry(ref="C7", header="2024"), "report year"),  # a comparative column
        (_entry(header="FY2025"), "header"),
        (_entry(ref="D9", label="Cat 6 Business travel", scope="3", cat=6), "blank"),
        (_entry(ref="D12"), "outside"),
    ],
)
def test_a_listed_cell_that_does_not_check_out_is_a_problem(tmp_path, entry, problem):
    draft = _read(tmp_path, [entry, _entry(ref="D8", label="Scope 2 (Market-based)",
                                           scope="2", method="market")])  # fmt: skip
    assert any(problem in p for p in draft.problems), draft.problems


def test_a_method_on_a_row_without_scope_2_is_a_problem(tmp_path):
    draft = _read(tmp_path, _both(method="market"))
    assert any("method" in p for p in draft.problems)


def test_a_report_year_cell_nobody_listed_is_reported_and_a_blank_one_is_not(tmp_path):
    draft = _read(tmp_path, [_entry()])
    assert draft.unlisted == ["GHG!D8 'Scope 2 (Market-based)'"]  # D9 is "-": blank


def test_a_unit_printed_once_in_a_title_must_be_found_there(tmp_path):
    cells = {k: v for k, v in _CELLS.items() if not k.startswith("B")}
    cells["A5"] = ("Scope 1 and 2 emissions (ktCO2e)", "s", GENERAL)
    titled = WorkbookTable(
        sheet="GHG", label_column="A", header_row=6, first_row=7, last_row=8,
        unit_printed="ktCO2e", unit_cell="A5",
    )  # fmt: skip
    draft = _read(tmp_path, _both(), cells=cells, tables=(titled,))
    assert draft.problems == [] and {r.unit for r in draft.rows} == {"ktCO2e"}
    wrong = titled.model_copy(update={"unit_printed": "tCO2e"})
    draft = _read(tmp_path, _both(), cells=cells, tables=(wrong,))
    assert any("unit" in p for p in draft.problems)


def test_a_table_declares_its_unit_exactly_one_way():
    with pytest.raises(ValidationError):
        WorkbookTable(sheet="GHG", label_column="A", header_row=6, first_row=7, last_row=9)
    with pytest.raises(ValidationError):
        WorkbookTable(
            sheet="GHG", label_column="A", header_row=6, first_row=7, last_row=9,
            unit_column="B", unit_printed="ktCO2e", unit_cell="A5",
        )  # fmt: skip


def test_rows_are_tagged_as_read_by_code_not_drafted_by_a_model(tmp_path):
    draft = _read(tmp_path, _both())
    assert draft.drafted_by.startswith("carbon_r.workbook_reader")


# --- the row and the file ---------------------------------------------------------------


def _row(**kw) -> PeerDisclosure:
    values = dict(
        source_id="peer_cict_2025_datapack", company="X", period_start=date(2025, 1, 1),
        period_end=date(2025, 12, 31), scope="1", figure_printed="3,456", figure=3456,
        drafted_figure_printed="3,456", unit="tonnes CO2e", method=None, pdf_page=None,
        printed_page=None, sheet_name="GHG", cell_ref="D7", row_label="Scope 1",
        drafted_row_label="Scope 1", column_header="2025", notes=None,
    )  # fmt: skip
    return PeerDisclosure(**(values | kw))


def test_a_row_cites_a_page_or_a_cell_never_both_or_neither():
    _row()
    _row(pdf_page=3, sheet_name=None, cell_ref=None)
    for bad in (
        {"pdf_page": 3},
        {"sheet_name": None, "cell_ref": None},
        {"cell_ref": None},
        {"cell_ref": "d7"},
        {"printed_page": 4},
    ):
        with pytest.raises(ValidationError):
            _row(**bad)


def test_two_cells_with_one_label_and_column_are_distinct():
    # FLCT prints "Total (ktCO2e)" once per region, all under FY2025.
    assert _row().key != _row(cell_ref="D19").key


def test_the_key_change_leaves_every_committed_blind_sample_as_it_was():
    """The key gained the sheet and cell, empty for a PDF row. Within one report that is a
    constant, so the sorted order - and so the seeded blind sample - must be unchanged."""
    if not VERIFIED_FILE.exists():
        pytest.skip("no verified file")
    by_source: dict[str, list] = {}
    for row in read_file().rows:
        by_source.setdefault(row.source_id, []).append(row)
    for rows in by_source.values():
        sampled = blind_sample([r.key for r in rows])
        assert sampled == {r.key for r in rows if r.blind.in_sample}


def test_a_pdf_rows_json_is_unchanged_and_a_workbook_row_names_its_cell():
    if not VERIFIED_FILE.exists():
        pytest.skip("no verified file")
    rows = read_file().rows
    for row in rows:
        dumped = row.model_dump(mode="json")
        if row.pdf_page is not None:
            assert "sheet_name" not in dumped and "cell_ref" not in dumped
        else:
            assert dumped["sheet_name"] and dumped["cell_ref"] and dumped["printed_page"] is None
    assert any(r.pdf_page is not None for r in rows)


def test_a_cell_citation_names_the_sheet_and_cell():
    report = "FLCT ESG Databook (1 Oct 2024 to 30 Sep 2025)"
    assert citation_text(report, None, None, "Climate Action - Scopes 1 & 2", "L9") == (
        f"{report}, sheet 'Climate Action - Scopes 1 & 2', cell L9"
    )
    assert citation_text(report, 45, 46, None, None) == f"{report}, p. 45 (PDF p. 46)"


# --- the table ----------------------------------------------------------------------------


@pytest.mark.skipif(not get_settings().database_configured, reason="DATABASE_URL not configured")
def test_migration_0008_holds_cells_and_refuses_a_row_with_both_or_neither():
    """0006-0008 applied twice, in a transaction that is rolled back."""
    import psycopg

    from carbon_r.db import connect

    row = _row().model_dump() | {
        "source_id": "zz_test_source", "drafted_by": "carbon_r.workbook_reader v1",
        "drafted_at": "2026-11-11T00:00:00+00:00", "draft_request_hash": "zz",
        "page_image_sha256": "0" * 64,
    }  # fmt: skip
    names = ("0006_peer_disclosures.sql", "0007_peer_cell_key_method.sql",
             "0008_peer_workbook_cells.sql")  # fmt: skip
    with connect() as conn:
        try:
            for name in names + names:
                conn.execute((MIGRATIONS_DIR / name).read_text())
            conn.execute(
                "insert into sources (id, title, publisher, year, url, retrieved_at, sha256, "
                "kind, verification_status) values ('zz_test_source', 't', 'p', 1999, 'u', "
                "'2026-11-11', repeat('0', 64), 'peer_report', 'secondary only')"
            )
            assert conn.execute(UPSERT_DISCLOSURE, row).fetchone() == (True,)
            assert conn.execute(UPSERT_DISCLOSURE, row).fetchone() is None  # unchanged
            other = row | {"cell_ref": "D19"}  # same label and column, another cell
            assert conn.execute(UPSERT_DISCLOSURE, other).fetchone() == (True,)
            in_view = conn.execute(
                "select sheet_name, cell_ref from peer_disclosures "
                "where source_id = 'zz_test_source' order by cell_ref"
            ).fetchall()
            assert in_view == [("GHG", "D19"), ("GHG", "D7")]
            for bad in (
                row | {"pdf_page": 3, "cell_ref": "D20"},
                row | {"sheet_name": None, "cell_ref": None},
                row | {"cell_ref": "D 7"},
                row | {"printed_page": 4, "cell_ref": "D21"},
            ):
                with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
                    conn.execute(UPSERT_DISCLOSURE, bad)
            columns = {
                r[0]
                for r in conn.execute(
                    "select column_name from information_schema.columns "
                    "where table_name = 'peer_disclosures_verified'"
                )
            }
            assert {"sheet_name", "cell_ref"} <= columns
        finally:
            conn.rollback()


def test_a_note_in_a_value_column_is_not_an_unlisted_value(tmp_path):
    # FLCT prints "Included in Category 1" where a category has no figure of its own.
    cells = _CELLS | {"A9": ("Cat 4 Upstream transportation", "s", GENERAL),
                      "D9": ("Included in Category 1", "s", GENERAL)}  # fmt: skip
    draft = _read(tmp_path, _both(), cells=cells)
    assert draft.problems == [] and draft.unlisted == []
    listed = _read(tmp_path, [*_both(), _entry(ref="D9", label="Cat 4 Upstream transportation",
                                               scope="3", cat=4)], cells=cells)  # fmt: skip
    assert any("does not parse" in p for p in listed.problems)


def test_a_table_heading_prefixes_the_label_as_the_page_drafter_does(tmp_path):
    # FLCT's Scope 3 sheet: one table per region, the same category rows in each.
    cells = _CELLS | {"A6": ("Singapore (including corporate office)", "s", GENERAL)}
    headed = _TABLE.model_copy(update={"heading_cell": "A6"})
    draft = _read(tmp_path, _both(), cells=cells, tables=(headed,))
    assert draft.problems == []
    assert draft.rows[0].row_label == "Singapore (including corporate office) > Scope 11"
    assert draft.rows[0].drafted_row_label == draft.rows[0].row_label
