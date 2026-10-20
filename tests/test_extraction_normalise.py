"""Printed strings -> typed values, and the page's own arithmetic re-checked.

Entirely offline: no `.env`, no database, no API key, no generated files on disk. The
round-trip test builds its printed strings from the generator's own renderer context, so
it proves `normalise` inverts what the layout prints rather than what I assumed it prints.
"""

from datetime import date
from decimal import Decimal

import pytest

from carbon_r.models.extraction import (
    DATE_FORMATS,
    ExtractedElectricityBill,
    NormalisedBill,
    activities_for,
    check_consistency,
    normalise,
)
from carbon_r.synthgen.electricity import build_bill_series
from carbon_r.synthgen.render import bill_context

BILLS = {bill.doc_id: bill for bill in build_bill_series()}

# The layout prints the unit inline with the figure ("139,600 kWh"), so a transcription
# reasonably includes it. Both forms must normalise identically.
CONTEXT_TO_FIELD = {
    "vendor_name": "vendor_name",
    "vendor_uen": "vendor_uen",
    "customer_name": "customer_name",
    "customer_address": "customer_address",
    "account_number": "account_number",
    "document_date": "document_date",
    "period_start": "period_start",
    "period_end": "period_end",
    "meter_number": "meter_number",
    "previous_reading": "previous_reading",
    "current_reading": "current_reading",
    "consumption": "consumption",
    "reading_basis": "reading_basis",
    "tariff_plan_code": "tariff_plan_code",
    "tariff_plan_name": "tariff_plan_name",
    "tension": "tension",
    "currency": "currency",
    "tariff_cents_per_kwh": "tariff_cents_per_kwh",
    "energy_charge": "energy_charge",
    "fixed_charge": "fixed_charge",
    "subtotal": "subtotal",
    "gst_percent": "gst_percent",
    "gst_amount": "gst_amount",
    "total": "total",
}


def perfect_reading(doc_id: str, **overrides) -> ExtractedElectricityBill:
    """What a flawless transcription of this document's page would look like."""
    context = bill_context(BILLS[doc_id])
    fields = {field: context[key] for key, field in CONTEXT_TO_FIELD.items()}
    return ExtractedElectricityBill(
        **{
            **fields,
            "consumption_unit": "kWh",
            "credit_adjustment": None,
            "notes": None,
            **overrides,
        }
    )


# --- the round trip: renderer -> printed strings -> normalise -> the answer key ---------


@pytest.mark.parametrize("doc_id", ["elec_001", "elec_009", "elec_018"])
def test_a_perfect_reading_normalises_back_to_the_answer_key(doc_id):
    """The strongest statement this module can make: a correct read reproduces the key.

    Every figure here came from the generator, through the renderer's formatting, and back
    out through `normalise`. If the two sides ever disagree, an eval failure would be
    ambiguous between a bad read and a bad parse - so this pins the parse.
    """
    key = BILLS[doc_id]
    normalised, problems = normalise(perfect_reading(doc_id))

    assert problems == []
    assert normalised is not None

    assert normalised.consumption_kwh == key.consumption_kwh
    assert normalised.previous_reading_kwh == key.previous_reading_kwh
    assert normalised.current_reading_kwh == key.current_reading_kwh
    assert normalised.period_start == key.period_start
    assert normalised.period_end == key.period_end
    assert normalised.document_date == key.document_date
    assert normalised.is_estimated_reading == key.is_estimated_reading
    assert normalised.tariff_cents_per_kwh == key.tariff_cents_per_kwh
    assert normalised.energy_charge_sgd == key.energy_charge_sgd
    assert normalised.fixed_charge_sgd == key.fixed_charge_sgd
    assert normalised.subtotal_sgd == key.subtotal_sgd
    assert normalised.gst_rate == key.gst_rate
    assert normalised.gst_amount_sgd == key.gst_amount_sgd
    assert normalised.total_sgd == key.total_sgd
    assert normalised.customer_name == key.customer_name
    assert normalised.account_number == key.account_number
    assert normalised.tariff_plan_code == key.tariff_plan_code


def test_the_factor_year_comes_from_the_period_not_the_document_date():
    """elec_009's period is Dec 2024 but its bill date is Jan 2025. The trap document."""
    key = BILLS["elec_009"]
    assert key.period_start.year != key.document_date.year  # the trap is still live

    normalised, _ = normalise(perfect_reading("elec_009"))
    assert normalised.factor_year == 2024
    assert normalised.document_date.year == 2025


@pytest.mark.parametrize("doc_id", sorted(BILLS))
def test_a_perfect_reading_of_every_document_is_internally_consistent(doc_id):
    normalised, problems = normalise(perfect_reading(doc_id))
    assert problems == []
    assert check_consistency(normalised) == []


# --- the parsers ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "printed",
    ["04 Dec 2025", "04 December 2025", "2025-12-04", "04/12/2025", "04-12-2025"],
)
def test_every_layout_date_format_parses_to_the_same_day(printed):
    normalised, problems = normalise(perfect_reading("elec_001", period_start=printed))
    assert problems == []
    assert normalised.period_start.isoformat() == "2025-12-04"


def test_day_first_is_the_assumed_convention_for_slashed_dates():
    """04/12/2025 is 4 December, not 12 April: these are Singapore documents."""
    normalised, _ = normalise(perfect_reading("elec_001", period_start="04/12/2025"))
    assert (normalised.period_start.day, normalised.period_start.month) == (4, 12)
    assert "%d/%m/%Y" in DATE_FORMATS
    assert "%m/%d/%Y" not in DATE_FORMATS


@pytest.mark.parametrize(
    ("printed", "unit", "expected_kwh"),
    [
        ("139,600", "kWh", 139600),
        ("139,600 kWh", "kWh", 139600),  # unit inline, as the layout prints it
        ("139600", "kWh", 139600),
        ("8.24", "MWh", 8240),  # the Tier 2 unit_mwh quirk, already handled
        ("0.5", "MWh", 500),
        ("1", "GWh", 1_000_000),
    ],
)
def test_consumption_converts_to_kwh_in_python(printed, unit, expected_kwh):
    normalised, problems = normalise(
        perfect_reading("elec_001", consumption=printed, consumption_unit=unit)
    )
    assert problems == []
    assert normalised.consumption_kwh == expected_kwh


def test_an_unrecognised_consumption_unit_is_a_problem_not_an_assumption():
    """Treating an unknown unit as kWh could understate emissions 1000-fold."""
    normalised, problems = normalise(
        perfect_reading("elec_001", consumption="8.24", consumption_unit="therms")
    )
    assert normalised.consumption_kwh is None
    # Readings are in the bill's unit too, so they are not assumed to be kWh either.
    assert {p.field for p in problems} == {
        "consumption_kwh",
        "previous_reading_kwh",
        "current_reading_kwh",
    }
    assert all("therms" in p.reason for p in problems)


@pytest.mark.parametrize(
    ("previous", "current", "unit", "expected"),
    [
        # Block 3, B3: the register lines of an MWh bill are in MWh as well.
        ("285.727 MWh", "292.791 MWh", "MWh", (285727, 292791)),
        ("285.727", "292.791", "MWh", (285727, 292791)),  # unit only in the unit column
        ("285,727 kWh", "292,791 kWh", "kWh", (285727, 292791)),
        ("285,727", "292,791", None, (285727, 292791)),
    ],
)
def test_meter_readings_convert_in_the_bills_unit(previous, current, unit, expected):
    normalised, problems = normalise(
        perfect_reading(
            "elec_001",
            previous_reading=previous,
            current_reading=current,
            consumption="7.064" if unit == "MWh" else "7,064",
            consumption_unit=unit,
        )
    )
    assert [p for p in problems if "reading" in p.field] == []
    assert (normalised.previous_reading_kwh, normalised.current_reading_kwh) == expected


def test_a_whole_number_in_mwh_is_never_read_as_kwh():
    """Before B3, `clean` stripped "MWh" as noise: "285 MWh" with no unit column became
    285 kWh - 1000x low, with no problem raised."""
    normalised, problems = normalise(
        perfect_reading("elec_001", consumption="285 MWh", consumption_unit=None)
    )
    assert problems == []
    assert normalised.consumption_kwh == 285_000


def test_an_inline_unit_that_contradicts_the_unit_column_is_a_problem():
    normalised, problems = normalise(
        perfect_reading("elec_001", consumption="7,064 kWh", consumption_unit="MWh")
    )
    assert normalised.consumption_kwh is None
    assert [p.field for p in problems] == ["consumption_kwh"]
    assert "MWh" in problems[0].reason


def test_gst_is_stored_as_a_fraction_matching_the_answer_key():
    normalised, _ = normalise(perfect_reading("elec_001", gst_percent="9"))
    assert normalised.gst_rate == Decimal("0.09")
    assert normalised.gst_rate == BILLS["elec_001"].gst_rate


@pytest.mark.parametrize(
    ("printed", "expected"), [("Actual", False), ("Estimated", True), ("ESTIMATED", True)]
)
def test_reading_basis_becomes_a_boolean(printed, expected):
    normalised, _ = normalise(perfect_reading("elec_001", reading_basis=printed))
    assert normalised.is_estimated_reading is expected


# --- one bad field costs one field ------------------------------------------------------


def test_an_unparseable_field_is_reported_and_the_rest_still_parse():
    normalised, problems = normalise(perfect_reading("elec_009", total="thirty-eight thousand"))
    assert normalised is not None
    assert normalised.total_sgd is None
    # Everything else survived.
    assert normalised.consumption_kwh == BILLS["elec_009"].consumption_kwh
    assert normalised.subtotal_sgd == BILLS["elec_009"].subtotal_sgd

    assert [p.field for p in problems] == ["total_sgd"]
    assert problems[0].printed == "thirty-eight thousand"


def test_a_nonsense_date_is_reported_rather_than_guessed():
    _, problems = normalise(perfect_reading("elec_001", period_end="31 Feb 2025"))
    assert [p.field for p in problems] == ["period_end"]
    assert "date formats" in problems[0].reason


def test_a_missing_field_is_absent_rather_than_a_problem():
    """A null means 'not printed'. That is a reading, not a parse failure."""
    normalised, problems = normalise(perfect_reading("elec_001", meter_number=None))
    assert problems == []
    assert normalised.meter_number is None


def test_an_empty_reading_yields_no_results_material():
    empty = ExtractedElectricityBill.model_construct(
        **dict.fromkeys(ExtractedElectricityBill.model_fields, None)
    )
    normalised, problems = normalise(empty)
    assert normalised is None
    assert problems[0].field == "(document)"


def test_notes_alone_do_not_count_as_a_reading():
    fields = dict.fromkeys(ExtractedElectricityBill.model_fields, None)
    fields["notes"] = "The page is a photograph of a wall."
    normalised, _ = normalise(ExtractedElectricityBill.model_construct(**fields))
    assert normalised is None


# --- the page's own arithmetic -----------------------------------------------------------


def test_a_doctored_total_is_flagged_against_the_other_printed_figures():
    normalised, _ = normalise(perfect_reading("elec_009", total="42,108.24"))
    flags = check_consistency(normalised)

    assert len(flags) == 1
    flag = flags[0]
    assert flag.relation == "total = subtotal + gst_amount"
    assert flag.printed == Decimal("42108.24")
    assert flag.expected == Decimal("42108.23")
    assert flag.difference == Decimal("0.01")


def test_a_misread_consumption_flags_only_the_relations_that_use_it():
    """One bad field must not cascade: each relation is checked from printed inputs."""
    normalised, _ = normalise(perfect_reading("elec_009", consumption="13,960"))
    relations = {flag.relation for flag in check_consistency(normalised)}

    assert "energy_charge = round2(consumption_kwh x tariff_cents_per_kwh / 100)" in relations
    assert "consumption_kwh = current_reading - previous_reading" in relations
    # The money relations downstream of the printed subtotal are untouched.
    assert "total = subtotal + gst_amount" not in relations
    assert "gst_amount = round2(subtotal x gst_rate)" not in relations


def test_relations_whose_inputs_were_not_read_are_skipped_not_failed():
    normalised, _ = normalise(perfect_reading("elec_001", gst_percent=None))
    relations = {flag.relation for flag in check_consistency(normalised)}
    assert "gst_amount = round2(subtotal x gst_rate)" not in relations


# --- the layout joins two key fields under one label -------------------------------------


def test_the_plan_name_is_split_from_the_code_the_layout_appended():
    """Layout elec_a prints "Tariff plan" as `{name} ({code})`; the key holds them apart.

    A faithful transcription therefore contains both. The page conflates them, so Python
    separates them - the answer key is not touched (rule 4) and neither is the reading.
    """
    key = BILLS["elec_009"]
    reading = perfect_reading(
        "elec_009",
        tariff_plan_name=f"{key.tariff_plan_name} ({key.tariff_plan_code})",
    )
    normalised, problems = normalise(reading)

    assert problems == []
    assert normalised.tariff_plan_name == key.tariff_plan_name == "Industrial Fixed 24"
    assert normalised.tariff_plan_code == key.tariff_plan_code


def test_an_unrelated_parenthetical_is_left_alone():
    """Only the code is stripped. Trimming anything in brackets would hide a difference."""
    normalised, _ = normalise(
        perfect_reading("elec_001", tariff_plan_name="Business Fixed 24 (promotional)")
    )
    assert normalised.tariff_plan_name == "Business Fixed 24 (promotional)"


def test_the_code_is_recovered_from_the_plan_line_when_its_own_field_is_null():
    """The failure the first eval run found, pinned.

    On one run the model put the whole "Tariff plan" line in `tariff_plan_name` and left
    `tariff_plan_code` null. The line was read correctly, yet both fields scored wrong.
    Both are now recovered from the single printed line.
    """
    key = BILLS["elec_009"]
    normalised, problems = normalise(
        perfect_reading(
            "elec_009",
            tariff_plan_name=f"{key.tariff_plan_name} ({key.tariff_plan_code})",
            tariff_plan_code=None,
        )
    )

    assert problems == []
    assert normalised.tariff_plan_name == key.tariff_plan_name
    assert normalised.tariff_plan_code == key.tariff_plan_code


def test_a_code_read_from_its_own_field_wins_over_the_one_in_the_name():
    """The parenthesised code is a fallback, not an override."""
    normalised, _ = normalise(
        perfect_reading(
            "elec_009",
            tariff_plan_name="Industrial Fixed 24 (SPR-HT-FIX-24)",
            tariff_plan_code="SPR-HT-FIX-99",
        )
    )
    assert normalised.tariff_plan_code == "SPR-HT-FIX-99"


@pytest.mark.parametrize(
    ("field", "printed", "expected"),
    [
        ("vendor_uen", "UEN SYN-0000-0001", "SYN-0000-0001"),
        ("vendor_uen", "SYN-0000-0001", "SYN-0000-0001"),
        ("account_number", "Account No: 8820-3309-01", "8820-3309-01"),
        ("meter_number", "Meter SP37331691", "SP37331691"),
    ],
)
def test_an_inline_field_label_is_stripped_from_identifiers(field, printed, expected):
    """Layout elec_a prints "UEN SYN-0000-0001 · <address>" as one text run.

    The third fresh eval run read `vendor_uen` as "UEN SYN-0000-0001" on elec_010 - a
    faithful transcription of that line, scored wrong against a key holding the bare
    identifier. The label belongs to the field, not to its value.
    """
    normalised, problems = normalise(perfect_reading("elec_009", **{field: printed}))
    assert problems == []
    assert getattr(normalised, field) == expected


def test_a_label_with_nothing_after_it_is_left_alone():
    """ "UEN" alone is not a UEN; emptying the field would hide a bad read."""
    normalised, _ = normalise(perfect_reading("elec_009", vendor_uen="UEN"))
    assert normalised.vendor_uen == "UEN"


def test_a_value_merely_beginning_with_label_letters_is_untouched():
    normalised, _ = normalise(perfect_reading("elec_009", vendor_uen="UENITED-1234"))
    assert normalised.vendor_uen == "UENITED-1234"


# --- activities, and the year boundary (Tier 2 Block 2) ---------------------------------
#
# Split literals from the independent scratch route in test_prorate.py:
# 9000 kWh over 16 Dec 2023 - 15 Jan 2024 is {2023: 4645, 2024: 4355}.


def test_a_single_year_bill_is_one_activity_carrying_its_year():
    bill = NormalisedBill(
        consumption_kwh=4296,
        period_start=date(2025, 11, 1),
        period_end=date(2025, 11, 30),
        document_date=date(2025, 12, 4),
    )
    (only,) = activities_for(bill)
    assert only.amount == Decimal(4296)
    assert only.factor_year == 2025
    assert only.derivation is None


def test_a_bill_across_31_december_is_split_by_days_into_two_years():
    bill = NormalisedBill(
        consumption_kwh=9000,
        period_start=date(2023, 12, 16),
        period_end=date(2024, 1, 15),
        is_estimated_reading=True,
    )
    first, second = activities_for(bill)
    assert (first.factor_year, first.amount) == (2023, Decimal(4645))
    assert (second.factor_year, second.amount) == (2024, Decimal(4355))
    assert (first.period_start, first.period_end) == (date(2023, 12, 16), date(2023, 12, 31))
    assert (second.period_start, second.period_end) == (date(2024, 1, 1), date(2024, 1, 15))
    assert "16 of 31 days in 2023" in first.derivation
    assert "remainder" in second.derivation
    # The estimate flag belongs to the reading, so both parts carry it.
    assert first.is_estimated and second.is_estimated


def test_without_a_readable_period_there_is_no_year_to_split_by():
    (only,) = activities_for(NormalisedBill(consumption_kwh=500))
    assert only.factor_year is None
