"""A read-only xlsx reader, in the standard library.

`openpyxl` and `pandas` are not installed in this environment and are not worth adding
for the one thing they would be used for: reading a handful of rows out of a publisher's
workbook once, so the numbers can be transcribed into a loader as literals.

An xlsx file is a zip of XML. Cell values live in `xl/worksheets/sheetN.xml`; strings are
interned in `xl/sharedStrings.xml` and referenced by index when a cell carries `t="s"`.
That is the whole format as far as this module is concerned.

Two properties matter for the use this gets:

1. **Cells are addressed, not positional.** A row in the XML omits empty cells entirely,
   so zipping values against a header row would silently shift a column. `read_sheet`
   returns `{"A35": "LPG", ...}` per row, keyed by the spreadsheet's own reference, and a
   caller asks for the column it wants by letter.
2. **Values stay strings.** Floats are what the file holds (`2.6615500000000001`), and
   turning them into Python floats before they reach `Decimal` is the corruption the
   `Decimal` discipline exists to prevent. Callers pass the string to `Decimal`.

Not a general xlsx library: no formulas, no styles, no dates, no inline strings beyond
what the files in use contain. It reads published statistical tables and nothing else.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


class SheetNotFoundError(LookupError):
    """The workbook has no sheet by that name. Lists what it does have."""


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    """The interned string table, or empty if the workbook has none.

    A `<si>` can hold several `<t>` runs when part of the text is styled differently;
    they are concatenated, which is what the cell displays.
    """
    try:
        raw = archive.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    return [
        "".join(t.text or "" for t in si.iter(_MAIN + "t"))
        for si in ET.fromstring(raw).iter(_MAIN + "si")
    ]


def sheet_names(path: Path) -> dict[str, str]:
    """`{sheet name: archive path}`, in workbook order.

    The mapping goes through the relationship ids rather than assuming `sheetN.xml`
    matches the Nth sheet: in the DESNZ workbooks it does not - `Fuels` is the fourth
    sheet by position and `sheet4.xml` on disk, but `Bioenergy` is fifth and `sheet5.xml`
    while `Index` is third and `sheet3.xml` with sheetId 38. Resolving the relationship is
    the only reliable route.
    """
    with zipfile.ZipFile(path) as archive:
        rels = {
            rel.get("Id"): rel.get("Target")
            for rel in ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        }
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        names: dict[str, str] = {}
        for sheet in workbook.iter(_MAIN + "sheet"):
            target = rels.get(sheet.get(_REL + "id"), "")
            names[sheet.get("name") or ""] = f"xl/{target.lstrip('/')}"
        return names


def read_sheet(path: Path, sheet: str) -> dict[int, dict[str, str]]:
    """`{row number: {cell reference: value}}` for one named sheet.

    Empty cells are absent rather than present-and-blank, mirroring the file. Row numbers
    are the spreadsheet's own, so a value can be cited as "sheet Fuels, row 35" in a
    comment and found again by a person opening the file.
    """
    names = sheet_names(path)
    if sheet not in names:
        raise SheetNotFoundError(f"{path.name} has no sheet {sheet!r}. Sheets: {sorted(names)}")

    with zipfile.ZipFile(path) as archive:
        strings = _shared_strings(archive)
        root = ET.fromstring(archive.read(names[sheet]))

    rows: dict[int, dict[str, str]] = {}
    for row in root.iter(_MAIN + "row"):
        cells: dict[str, str] = {}
        for cell in row.iter(_MAIN + "c"):
            value = cell.find(_MAIN + "v")
            if value is None or value.text is None:
                continue
            if cell.get("t") == "s":
                index = int(value.text)
                cells[cell.get("r") or ""] = strings[index] if index < len(strings) else ""
            else:
                cells[cell.get("r") or ""] = value.text
        if cells:
            rows[int(row.get("r") or 0)] = cells
    return rows


def column(reference: str) -> str:
    """`"AB35"` -> `"AB"`."""
    return "".join(ch for ch in reference if ch.isalpha())


__all__ = ["SheetNotFoundError", "column", "read_sheet", "sheet_names"]
