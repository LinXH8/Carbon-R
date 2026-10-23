"""The standard-library xlsx reader, against the DESNZ workbooks it was written for.

Two of these tests need the workbooks, which are git-ignored, so they skip when the files
are absent - a fresh clone runs `make check` before anything has been downloaded. The
addressing behaviour that matters most is tested without any file at all.
"""

import zipfile
from pathlib import Path

import pytest

from carbon_r.config import PROJECT_ROOT
from carbon_r.xlsx import SheetNotFoundError, column, read_sheet, sheet_names

WORKBOOK = PROJECT_ROOT / "data" / "raw" / "desnz" / "ghg-conversion-factors-2026-full-set.xlsx"
needs_workbook = pytest.mark.skipif(
    not WORKBOOK.exists(), reason="DESNZ workbook not downloaded (git-ignored)"
)


def test_column_strips_the_row_number():
    assert column("A1") == "A"
    assert column("AB35") == "AB"
    assert column("D100") == "D"


def _tiny_workbook(path: Path, sheets: list[str]) -> Path:
    """The two parts the reader resolves names through, and nothing else."""
    rels = "".join(
        f'<Relationship Id="rId{i}" Target="worksheets/sheet{i}.xml"/>'
        for i, _ in enumerate(sheets, 1)
    )
    entries = "".join(
        f'<sheet name="{name}" sheetId="{i}" r:id="rId{i}"/>' for i, name in enumerate(sheets, 1)
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f"{rels}</Relationships>",
        )
        archive.writestr(
            "xl/workbook.xml",
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f"<sheets>{entries}</sheets></workbook>",
        )
    return path


def test_a_missing_sheet_names_the_sheets_that_exist(tmp_path):
    """Runs without the DESNZ workbook: a fresh clone has none.

    The first version fell back to passing this test file itself as the workbook, which
    raised a zip error rather than SheetNotFoundError, so it failed on every clone that had
    not downloaded the workbooks - including CI. Found by the Tier 2 Block 5 clean-clone run.
    """
    workbook = _tiny_workbook(tmp_path / "tiny.xlsx", ["Index", "Fuels"])
    with pytest.raises(SheetNotFoundError) as caught:
        read_sheet(workbook, "No Such Sheet")
    assert "No Such Sheet" in str(caught.value)
    assert "Fuels" in str(caught.value) and "Index" in str(caught.value)


@needs_workbook
def test_sheet_names_resolve_through_relationships_not_by_position():
    """`Fuels` is the fourth sheet by position AND `sheet4.xml`, but that is a coincidence.

    `Index` is the third sheet with sheetId 38, so neither position nor sheetId can be
    used to find a sheet's file. Only the relationship id can.
    """
    names = sheet_names(WORKBOOK)
    assert names["Fuels"] == "xl/worksheets/sheet4.xml"
    assert names["Index"] == "xl/worksheets/sheet3.xml"
    assert "WTT- fuels" in names


@needs_workbook
def test_cells_are_addressed_so_an_omitted_cell_cannot_shift_a_column():
    """The format omits empty cells entirely; zipping against a header would misalign.

    Row 36 of the Fuels sheet has no value in column B - the fuel name is only printed on
    row 35, the first of LPG's four unit rows - so a positional reader would read LPG's
    litres figure as if it were in column C.
    """
    rows = read_sheet(WORKBOOK, "Fuels")
    assert rows[35]["B35"] == "LPG"
    assert "B36" not in rows[36]
    assert rows[36]["C36"] == "litres"


@needs_workbook
def test_values_stay_strings_so_decimal_sees_the_file_not_a_float():
    rows = read_sheet(WORKBOOK, "Fuels")
    value = rows[35]["D35"]
    assert isinstance(value, str)
    # The file holds an IEEE-754 double, which is why the loader rounds to the published
    # precision rather than trusting the literal.
    assert value.startswith("2939.36094")


@needs_workbook
def test_rows_are_keyed_by_the_spreadsheets_own_numbering():
    """So a comment can cite "sheet Fuels, row 35" and a person can find the cell."""
    rows = read_sheet(WORKBOOK, "Fuels")
    assert min(rows) >= 1
    assert rows[22]["D22"] == "kg CO2e"
