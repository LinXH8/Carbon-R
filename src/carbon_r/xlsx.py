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

Not a general xlsx library: no formulas, no dates. It reads published statistical tables
and nothing else.

`read_cells` (Tier 3 Block 1) adds what a peer workbook needs: each cell's number format, so
a figure can be recorded as the issuer's reader SEES it (`displayed`), inline strings, and a
formula's cached result. A workbook stores a double behind a format - FLCT's databook shows
one decimal over a 16-decimal float - and the displayed value is the figure as printed.
`render_number` knows a handful of plain numeric formats and refuses every other one.
"""

from __future__ import annotations

import hashlib
import re
import zipfile
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Literal
from xml.etree import ElementTree as ET

_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


class SheetNotFoundError(LookupError):
    """The workbook has no sheet by that name. Lists what it does have."""


class UnsupportedNumberFormatError(ValueError):
    """A number format `render_number` does not reproduce. Refused, never approximated."""


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


# --- cells as displayed (Tier 3 Block 1) ---------------------------------------------------

# The built-in formats a cell can name by id alone (ECMA-376 Part 1, 18.8.30), as far as the
# renderer supports them; any other built-in id resolves to its id and is then refused.
_BUILTIN_FORMATS = {0: "General", 1: "0", 2: "0.00", 3: "#,##0", 4: "#,##0.00", 49: "@"}
_PLAIN = re.compile(r"^(?P<group>#,##)?0(?:\.(?P<decimals>0+))?$")


@dataclass(frozen=True)
class Cell:
    """One non-empty cell: its stored value (a string, as in `read_sheet`), whether the file
    holds it as a number or as text, and the number format it is displayed with."""

    ref: str
    value: str
    kind: Literal["number", "text"]
    number_format: str


def _number_formats(archive: zipfile.ZipFile) -> list[str]:
    """The format code of each cell style, by style index (`<c s="...">`)."""
    try:
        root = ET.fromstring(archive.read("xl/styles.xml"))
    except KeyError:
        return []
    custom = {
        int(fmt.get("numFmtId") or 0): fmt.get("formatCode") or ""
        for fmt in root.iter(_MAIN + "numFmt")
    }
    xfs = root.find(_MAIN + "cellXfs")
    formats = []
    for xf in xfs if xfs is not None else []:
        fmt_id = int(xf.get("numFmtId") or 0)
        formats.append(custom.get(fmt_id) or _BUILTIN_FORMATS.get(fmt_id) or f"builtin:{fmt_id}")
    return formats


def read_cells(path: Path, sheet: str) -> dict[str, Cell]:
    """`{cell reference: Cell}` for one named sheet. Empty cells are absent."""
    names = sheet_names(path)
    if sheet not in names:
        raise SheetNotFoundError(f"{path.name} has no sheet {sheet!r}. Sheets: {sorted(names)}")
    with zipfile.ZipFile(path) as archive:
        strings = _shared_strings(archive)
        formats = _number_formats(archive)
        root = ET.fromstring(archive.read(names[sheet]))

    cells: dict[str, Cell] = {}
    for cell in root.iter(_MAIN + "c"):
        ref, kind = cell.get("r") or "", cell.get("t")
        style = int(cell.get("s") or 0)
        number_format = formats[style] if style < len(formats) else "General"
        if kind == "inlineStr":
            inline = cell.find(_MAIN + "is")
            text = (
                "".join(t.text or "" for t in inline.iter(_MAIN + "t"))
                if inline is not None
                else ""
            )
            cells[ref] = Cell(ref, text, "text", number_format)
            continue
        value = cell.find(_MAIN + "v")
        if value is None or value.text is None:
            continue
        if kind == "s":
            index = int(value.text)
            text = strings[index] if index < len(strings) else ""
            cells[ref] = Cell(ref, text, "text", number_format)
        elif kind in ("str", "b", "e"):  # a formula's text result, a boolean, an error
            cells[ref] = Cell(ref, value.text, "text", number_format)
        else:
            cells[ref] = Cell(ref, value.text, "number", number_format)
    return cells


def render_number(stored: str, number_format: str) -> str:
    """The stored number as `number_format` displays it, for the plain formats only: General
    (whole numbers only), "0", "0.0...", "#,##0", "#,##0.0...". Rounds half away from zero on
    the stored decimal string, as Excel does. Anything else raises: a percentage, a date, an
    accounting or multi-section format, General over a fraction (whose rounding to a column
    width is not reproduced), or a value that is not a number."""
    try:
        value = Decimal(stored)
    except InvalidOperation as exc:
        raise UnsupportedNumberFormatError(f"{stored!r} is not a number") from exc
    if not value.is_finite():
        raise UnsupportedNumberFormatError(f"{stored!r} is not a finite number")
    if number_format == "General":
        if value != value.to_integral_value():
            raise UnsupportedNumberFormatError(
                f"General over a fraction ({stored!r}) is not reproduced"
            )
        return str(int(value))
    match = _PLAIN.fullmatch(number_format)
    if match is None:
        raise UnsupportedNumberFormatError(f"number format {number_format!r} is not supported")
    decimals = len(match.group("decimals") or "")
    rounded = value.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)
    return f"{rounded:,.{decimals}f}" if match.group("group") else f"{rounded:.{decimals}f}"


def displayed(cell: Cell) -> str:
    """What the cell shows: text as typed (stripped), a number through its format."""
    if cell.kind == "text":
        return cell.value.strip()
    return render_number(cell.value, cell.number_format)


def sheet_sha256(path: Path, sheet: str) -> str:
    """The sha256 of one sheet's XML part: the evidence a workbook row cites, as a page
    image's hash is for a PDF row. Changes if any cell on the sheet changes."""
    names = sheet_names(path)
    if sheet not in names:
        raise SheetNotFoundError(f"{path.name} has no sheet {sheet!r}. Sheets: {sorted(names)}")
    with zipfile.ZipFile(path) as archive:
        return hashlib.sha256(archive.read(names[sheet])).hexdigest()


__all__ = [
    "Cell",
    "SheetNotFoundError",
    "UnsupportedNumberFormatError",
    "column",
    "displayed",
    "read_cells",
    "read_sheet",
    "render_number",
    "sheet_names",
    "sheet_sha256",
]
