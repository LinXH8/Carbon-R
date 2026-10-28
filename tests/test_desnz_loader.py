"""The DESNZ factor loader: which rows, with what provenance, and idempotently.

The loader carries literals and re-reads the workbook to check them, so there are two
kinds of test here:

- the rows it will write, which are pure and always run;
- the diff against the publisher's own spreadsheet, which needs the git-ignored workbook
  and skips without it.

CLAUDE.md rule 5 is the point of the second group: a value with regulatory significance is
only as good as the document it was read from, and a transcription checked once is a
transcription nobody can re-check.
"""

from decimal import Decimal

import pytest

from load_desnz_factors import (
    FLOAT_NOISE,
    FUEL_FACTORS,
    GAS_BASIS,
    JURISDICTION,
    LICENCE,
    METHOD,
    PUBLICATIONS,
    PUBLISHED_PLACES,
    PUBLISHER,
    SCOPE,
    SHEET,
    TOTAL_COLUMN,
    WORKBOOK_DIR,
    read_fuel_totals,
    rows,
)

ROWS = rows()

needs_workbooks = pytest.mark.skipif(
    not all((WORKBOOK_DIR / p["workbook"]).exists() for p in PUBLICATIONS.values()),
    reason="DESNZ workbooks not downloaded (git-ignored)",
)


# --- what gets loaded -------------------------------------------------------------------


def test_one_row_per_fuel_and_publication_year():
    assert len(ROWS) == 6
    assert {(r["category"], r["year"]) for r in ROWS} == {
        ("fuel_lpg", 2025),
        ("fuel_lpg", 2026),
        ("fuel_diesel", 2025),
        ("fuel_diesel", 2026),
        ("fuel_petrol", 2025),
        ("fuel_petrol", 2026),
    }


def test_the_table_key_is_unique_across_the_rows():
    """(category, unit, year) is the factor's identity, so two rows cannot collide."""
    keys = [(r["category"], r["unit"], r["year"]) for r in ROWS]
    assert len(set(keys)) == len(keys)


def test_lpg_is_loaded_per_tonne_only():
    """DESNZ has no per-kg row, and nothing here measures LPG by volume.

    An unused per-litre row would be a row a lookup could land on by mistake.
    """
    units = {r["unit"] for r in ROWS if r["category"] == "fuel_lpg"}
    assert units == {"kg CO2e / tonnes"}


def test_diesel_and_petrol_are_loaded_per_litre():
    for category in ("fuel_diesel", "fuel_petrol"):
        units = {r["unit"] for r in ROWS if r["category"] == category}
        assert units == {"kg CO2e / litres"}


def test_no_kwh_row_is_loaded_so_no_cv_basis_has_to_be_chosen():
    """Only the kWh rows carry a gross/net CV basis, and none is loaded."""
    assert not any("kWh" in r["unit"] for r in ROWS)
    assert not any("CV" in r["unit"] for r in ROWS)


def test_every_row_is_scope_one_combustion_co2e_and_non_singapore():
    for row in ROWS:
        assert row["scope"] == SCOPE == 1
        assert row["method"] == METHOD == "combustion"
        assert row["gas_basis"] == GAS_BASIS == "CO2e"
        assert row["jurisdiction"] == JURISDICTION == "UK"


def test_every_row_carries_the_provenance_the_schema_requires():
    """Rule 2: a factor cannot be inserted anonymously."""
    for row in ROWS:
        for field in (
            "category",
            "factor",
            "unit",
            "year",
            "publisher",
            "source_document",
            "source_url",
            "source_page",
            "retrieved_at",
            "jurisdiction",
        ):
            assert row[field], f"{row['category']} {row['year']} has no {field}"
        assert row["verified"] is True


def test_the_source_page_locates_the_cell_a_person_would_open():
    """A sheet reference, not a page: this is a spreadsheet source."""
    for row in ROWS:
        assert f"sheet '{SHEET}'" in row["source_page"]
        assert "row " in row["source_page"]
        assert f"column {TOTAL_COLUMN}" in row["source_page"]


def test_the_licence_travels_with_every_row():
    assert all(LICENCE in row["notes"] for row in ROWS)
    assert LICENCE == "Open Government Licence v3.0"


def test_the_publisher_is_named_in_full():
    """'DESNZ' is an abbreviation; a citation should carry the department's name."""
    assert PUBLISHER == "Department for Energy Security and Net Zero"
    assert all(row["publisher"] == PUBLISHER for row in ROWS)


def test_the_source_url_is_the_publication_page_not_the_asset():
    """Asset paths change; the publication page is the citation that stays valid."""
    for row in ROWS:
        assert row["source_url"].startswith("https://www.gov.uk/government/publications/")
        assert not row["source_url"].endswith(".xlsx")


def test_the_mineral_choice_is_recorded_in_the_notes():
    """It is an assumption, not a verified fact, and the row says so."""
    for category in ("fuel_diesel", "fuel_petrol"):
        notes = [r["notes"] for r in ROWS if r["category"] == category]
        assert all("100% mineral" in n for n in notes)
        assert all("SOURCES.md" in n for n in notes)
        assert all("not verified" in n for n in notes)


def test_the_lpg_notes_explain_why_a_conversion_is_needed():
    notes = [r["notes"] for r in ROWS if r["category"] == "fuel_lpg"]
    assert all("no per-kg row" in n for n in notes)


def test_only_petrol_moved_between_the_two_publications():
    """A fact about the data worth pinning: it is why both years are loaded separately."""
    by_category = {}
    for category, spec in FUEL_FACTORS.items():
        by_category[category] = {year: value for year, (value, _) in spec["values"].items()}

    assert by_category["fuel_lpg"][2025] == by_category["fuel_lpg"][2026]
    assert by_category["fuel_diesel"][2025] == by_category["fuel_diesel"][2026]
    assert by_category["fuel_petrol"][2025] != by_category["fuel_petrol"][2026]


def test_the_series_starts_at_2025_so_an_earlier_activity_year_finds_nothing():
    """DESNZ publishes no back series. A 2024 period correctly gets no_results.

    `lookup_emission_factor` needs a row at or before the activity year, so this is what
    makes the deliberate 2024 LPG invoice reach the refusal path.
    """
    assert min(r["year"] for r in ROWS) == 2025


# --- the diff against the publisher's own file ------------------------------------------


@needs_workbooks
@pytest.mark.parametrize("year", sorted(PUBLICATIONS))
def test_every_literal_reproduces_the_workbook_at_the_published_precision(year):
    """Rule 5, made repeatable: the transcription is checkable, not trusted once."""
    path = WORKBOOK_DIR / PUBLICATIONS[year]["workbook"]
    totals = read_fuel_totals(path)

    for category, spec in FUEL_FACTORS.items():
        expected, expected_row = spec["values"][year]
        raw, row = totals[(spec["fuel_label"], spec["sheet_unit"])]
        assert row == expected_row, f"{category}: the row moved in the {year} workbook"
        assert raw.quantize(PUBLISHED_PLACES) == expected


@needs_workbooks
@pytest.mark.parametrize("year", sorted(PUBLICATIONS))
def test_the_residue_beyond_the_published_precision_is_only_float_noise(year):
    """The workbook stores IEEE-754 doubles; anything larger would be a real difference."""
    path = WORKBOOK_DIR / PUBLICATIONS[year]["workbook"]
    totals = read_fuel_totals(path)
    for spec in FUEL_FACTORS.values():
        raw, _ = totals[(spec["fuel_label"], spec["sheet_unit"])]
        assert abs(raw - raw.quantize(PUBLISHED_PLACES)) <= FLOAT_NOISE


@needs_workbooks
@pytest.mark.parametrize("year", sorted(PUBLICATIONS))
def test_the_sheet_states_the_scope_and_year_the_loader_claims(year):
    """The workbook declares both in its own header cells; neither is assumed."""
    from carbon_r.xlsx import read_sheet

    path = WORKBOOK_DIR / PUBLICATIONS[year]["workbook"]
    header = read_sheet(path, SHEET)[6]
    assert header["B6"] == f"Scope {SCOPE}"
    assert header["F6"] == str(year)


@needs_workbooks
def test_the_average_biofuel_blend_rows_exist_and_are_deliberately_not_loaded():
    """They are the rows DESNZ recommends for UK forecourt fuel. We load the mineral ones.

    Asserted rather than assumed: if a future publication dropped these rows, the reason
    recorded in docs/SOURCES.md for not using them would need revisiting.
    """
    path = WORKBOOK_DIR / PUBLICATIONS[2026]["workbook"]
    totals = read_fuel_totals(path)
    blend = totals[("Diesel (average biofuel blend)", "litres")][0]
    mineral = totals[("Diesel (100% mineral diesel)", "litres")][0]
    assert blend < mineral  # the blend is lower, which is why the choice matters
    assert not any("blend" in row["source_page"] for row in ROWS)


@needs_workbooks
def test_the_wtt_sheet_is_not_the_one_being_read():
    """Well-to-tank is upstream Scope 3; loading it beside combustion invites a wrong row."""
    from carbon_r.xlsx import sheet_names

    path = WORKBOOK_DIR / PUBLICATIONS[2026]["workbook"]
    names = sheet_names(path)
    assert "WTT- fuels" in names
    assert SHEET == "Fuels"
    assert names[SHEET] != names["WTT- fuels"]


@needs_workbooks
def test_the_reader_skips_the_metadata_above_the_first_table():
    """Row 5 is `Emissions source: | Fuels | Next publication date: | June 2027`.

    A column-B reader that did not wait for a header row would take that for a fuel called
    "Fuels" with a total of "June 2027" - which is exactly what a first pass at this did.
    """
    path = WORKBOOK_DIR / PUBLICATIONS[2026]["workbook"]
    totals = read_fuel_totals(path)
    assert not any(fuel == "Fuels" for fuel, _ in totals)


@needs_workbooks
def test_the_fuel_label_carries_forward_across_a_fuels_unit_rows():
    """LPG is named once, on the first of its four unit rows."""
    path = WORKBOOK_DIR / PUBLICATIONS[2026]["workbook"]
    totals = read_fuel_totals(path)
    assert ("LPG", "tonnes") in totals
    assert ("LPG", "litres") in totals
    assert totals[("LPG", "tonnes")][1] == 35
    assert totals[("LPG", "litres")][1] == 36


@needs_workbooks
def test_the_loaded_values_are_the_ones_this_project_cites():
    """A regression guard on the six numbers themselves, read from the sheet."""
    path = WORKBOOK_DIR / PUBLICATIONS[2026]["workbook"]
    totals = read_fuel_totals(path)
    assert totals[("LPG", "tonnes")][0].quantize(PUBLISHED_PLACES) == Decimal("2939.36095")
    assert totals[("Diesel (100% mineral diesel)", "litres")][0].quantize(
        PUBLISHED_PLACES
    ) == Decimal("2.66155")
    assert totals[("Petrol (100% mineral petrol)", "litres")][0].quantize(
        PUBLISHED_PLACES
    ) == Decimal("2.35372")
