"""What a workbook cell DISPLAYS, which is what a peer row records as printed (Tier 3 Block 1).

A workbook stores a double behind a number format. The issuer's reader sees the formatted
value, so that is the figure "as printed"; the stored float is not. The cases below are the
shapes found in the FLCT and CICT workbooks (values invented): a one-decimal display over a
16-decimal float, a whole-number display over a 14-decimal float, a number typed as text.
Anything the renderer does not know is refused, never guessed.
"""

import zipfile
from pathlib import Path

import pytest

from carbon_r.xlsx import (
    UnsupportedNumberFormatError,
    displayed,
    read_cells,
    render_number,
    sheet_sha256,
)


@pytest.mark.parametrize(
    ("stored", "number_format", "shown"),
    [
        ("12.3400000000000016", "#,##0.0", "12.3"),  # 16 stored decimals, one shown
        ("4321.87654321098765", "#,##0", "4,322"),  # 14 stored decimals, none shown
        ("1234567", "#,##0", "1,234,567"),
        ("1234.5", "#,##0", "1,235"),  # half away from zero, as Excel rounds
        ("-1234.5", "#,##0", "-1,235"),
        ("0.05", "0.0", "0.1"),
        ("2.675", "0.00", "2.68"),  # the decimal string, not the binary double
        ("6.6", "0", "7"),
        ("1.2E-3", "0.0", "0.0"),
        ("3456", "General", "3456"),
        ("3456.0", "General", "3456"),
    ],
)
def test_a_number_renders_as_its_format_displays_it(stored, number_format, shown):
    assert render_number(stored, number_format) == shown


@pytest.mark.parametrize(
    ("stored", "number_format"),
    [
        ("12.3400000000000016", "General"),  # General's own rounding is not reproduced
        ("0.25", "0.0%"),
        ("1234.5", "#,##0.0;-#,##0.0"),
        ("1234.5", '_-* #,##0_-;-* #,##0_-;_-* "-"??_-;_-@_-'),
        ("45000", "dd/mm/yyyy"),
        ("not a number", "#,##0"),
    ],
)
def test_anything_else_is_refused_not_guessed(stored, number_format):
    with pytest.raises(UnsupportedNumberFormatError):
        render_number(stored, number_format)


_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def _styled_workbook(path: Path) -> Path:
    """One sheet, a custom and two built-in formats, shared, inline and formula strings."""
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
            '<sheets><sheet name="GHG" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        archive.writestr(
            "xl/sharedStrings.xml",
            f'<sst xmlns="{_MAIN}"><si><t>Scope 1</t></si><si><t>1,192</t></si>'
            "<si><r><t>Scope 2</t></r><r><t>¹</t></r></si></sst>",
        )
        archive.writestr(
            "xl/styles.xml",
            f'<styleSheet xmlns="{_MAIN}"><numFmts count="1">'
            '<numFmt numFmtId="164" formatCode="#,##0.0"/></numFmts>'
            '<cellXfs count="3"><xf numFmtId="0"/><xf numFmtId="3"/><xf numFmtId="164"/>'
            "</cellXfs></styleSheet>",
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            f'<worksheet xmlns="{_MAIN}"><sheetData>'
            '<row r="2"><c r="A2" t="s"><v>0</v></c><c r="B2" s="1"><v>4321.87654321098765</v>'
            '</c><c r="C2" s="2"><v>12.3400000000000016</v></c></row>'
            '<row r="3"><c r="A3" t="s"><v>2</v></c><c r="B3" t="s"><v>1</v></c>'
            '<c r="C3" t="inlineStr"><is><t>-</t></is></c>'
            '<c r="D3" t="str"><f>A3</f><v>Scope 2¹</v></c><c r="E3"/></row>'
            "</sheetData></worksheet>",
        )
    return path


def test_cells_carry_their_number_format_and_read_as_displayed(tmp_path):
    cells = read_cells(_styled_workbook(tmp_path / "w.xlsx"), "GHG")
    assert cells["B2"].kind == "number" and cells["B2"].number_format == "#,##0"
    assert cells["C2"].number_format == "#,##0.0"
    assert displayed(cells["B2"]) == "4,322"
    assert displayed(cells["C2"]) == "12.3"
    assert displayed(cells["A3"]) == "Scope 2¹"  # rich-text runs joined
    assert displayed(cells["B3"]) == "1,192"  # a number typed as text, kept as typed
    assert displayed(cells["C3"]) == "-"  # inline string
    assert displayed(cells["D3"]) == "Scope 2¹"  # a formula's cached result
    assert "E3" not in cells  # an empty cell is absent, as in `read_sheet`


def test_the_sheet_hash_changes_when_the_sheet_does(tmp_path):
    first = sheet_sha256(_styled_workbook(tmp_path / "a.xlsx"), "GHG")
    assert first == sheet_sha256(_styled_workbook(tmp_path / "b.xlsx"), "GHG")
    other = tmp_path / "c.xlsx"
    with zipfile.ZipFile(tmp_path / "a.xlsx") as src, zipfile.ZipFile(other, "w") as dst:
        for item in src.infolist():
            data = src.read(item)
            if item.filename == "xl/worksheets/sheet1.xml":
                data = data.replace(b"4321.87654321098765", b"4321.9")
            dst.writestr(item, data)
    assert sheet_sha256(other, "GHG") != first
