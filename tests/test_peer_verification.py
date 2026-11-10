"""Applying the author's verification worksheet (CLAUDE.md rule 4). Values are invented."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from carbon_r.models.peer_verification import (
    NO_YEAR_COLUMN,
    WORKSHEET_COLUMNS,
    DraftedCellRef,
    DraftRow,
    InventoryEntry,
    WorksheetEntry,
    header_matches,
    inventory_digest,
    match_cells,
    normalise_label,
    plan_verification,
    report_year_cells,
)


def _draft(
    i: int, label: str, figure: str = "1,234", blind: bool = False, method: str | None = None
) -> DraftRow:
    return DraftRow(
        id=i,
        pdf_page=9,
        drafted_row_label=label,
        column_header="2025",
        method=method,
        drafted_figure_printed=figure,
        blind_sample=blind,
        notes=None,
    )


def _entry(label: str, outcome: str = "ok", **kw) -> WorksheetEntry:
    values = {
        "pdf_page": 9,
        "row_label": label,
        "column_header": "2025",
        "method": None,
        "blind_value": None,
        "outcome": outcome,
        "correct_figure": None,
        "correct_row_label": None,
        "correct_method": None,
        "note": None,
    } | kw
    return WorksheetEntry(**values)


def test_ok_confirms_the_draft_as_it_stands():
    plan = plan_verification([_draft(1, "Scope 1")], [_entry("Scope 1")])
    (u,) = plan.updates
    assert plan.problems == []
    assert (u.verification, u.figure_printed, u.figure, u.row_label) == (
        "confirmed",
        "1,234",
        Decimal("1234"),
        "Scope 1",
    )


def test_a_corrected_label_and_figure_are_taken_and_the_figure_reparsed():
    plan = plan_verification(
        [_draft(1, "Total scope 1¹⁹")],
        [
            _entry(
                "Total scope 1¹⁹",
                "corrected",
                correct_row_label="Total scope 1⁹",
                correct_figure="2,345",
                note="marker is 9",
            )
        ],
    )
    (u,) = plan.updates
    assert (u.verification, u.row_label, u.figure_printed, u.figure) == (
        "corrected",
        "Total scope 1⁹",
        "2,345",
        Decimal("2345"),
    )
    assert u.notes == "verified (corrected): marker is 9"


def test_a_rejected_row_is_marked_not_dropped():
    (u,) = plan_verification([_draft(1, "x")], [_entry("x", "rejected")]).updates
    assert u.verification == "rejected"


@pytest.mark.parametrize(
    "kw",
    [
        {"outcome": "corrected"},  # nothing corrected
        {"outcome": "ok", "correct_figure": "5"},  # a correction with ok
        {"outcome": "maybe"},
    ],
)
def test_an_inconsistent_line_is_refused(kw):
    with pytest.raises(ValidationError):
        _entry("Scope 1", **kw)


def test_a_draft_missing_from_the_worksheet_stops_everything():
    plan = plan_verification([_draft(1, "Scope 1"), _draft(2, "Scope 2")], [_entry("Scope 1")])
    assert plan.updates == []
    assert any("'Scope 2'" in p and "not in the worksheet" in p for p in plan.problems)


def test_a_line_matching_no_draft_stops_everything():
    plan = plan_verification([_draft(1, "Scope 1")], [_entry("Scope 1"), _entry("Scope 9")])
    assert plan.updates == [] and len(plan.problems) == 1


def test_a_line_listed_twice_stops_everything():
    plan = plan_verification([_draft(1, "Scope 1")], [_entry("Scope 1"), _entry("Scope 1")])
    assert plan.updates == [] and plan.problems


def test_a_corrected_figure_must_be_a_number():
    plan = plan_verification(
        [_draft(1, "Scope 1")], [_entry("Scope 1", "corrected", correct_figure="2,5113")]
    )
    assert plan.updates == [] and plan.problems


def test_blind_values_are_compared_with_the_draft_and_stored_not_used():
    drafts = [_draft(1, "a", "100", blind=True), _draft(2, "b", "200", blind=True)]
    plan = plan_verification(
        drafts, [_entry("a", blind_value="100"), _entry("b", blind_value="201")]
    )
    assert (plan.blind_checked, plan.blind_matched) == (2, 1)
    assert [u.figure_printed for u in plan.updates] == ["100", "200"]  # draft stands
    assert [u.blind_figure_printed for u in plan.updates] == ["100", "201"]


def test_a_blind_value_outside_the_sample_is_refused():
    plan = plan_verification([_draft(1, "a")], [_entry("a", blind_value="1")])
    assert plan.updates == [] and plan.problems


def test_a_csv_line_reads_into_an_entry():
    row = dict.fromkeys(WORKSHEET_COLUMNS, "") | {
        "pdf_page": "9",
        "row_label": "Scope 1",
        "column_header": "2025",
        "outcome (ok/corrected/rejected)": " OK ",
    }
    entry = WorksheetEntry.from_csv_row(row)
    assert entry.outcome == "ok" and entry.blind_value is None and entry.note is None


# --- completeness: listed before drafting, matched exactly after ---------------------------


def listed(label="Scope 1", page=5, header="2025", scope="1", method="", paired=""):
    return InventoryEntry.from_csv_row(
        {
            "pdf_page": str(page),
            "row_label_as_printed": label,
            "column_header": header,
            "scope": scope,
            "method": method,
            "paired_draft_label": paired,
        }
    )


def drafted(label="Scope 1", page=5, header="2025", scope="1", method=None, outcome="kept"):
    return DraftedCellRef(
        pdf_page=page,
        drafted_row_label=label,
        column_header=header,
        scope=scope,
        method=method,
        outcome=outcome,
    )


def test_labels_match_without_case_spacing_punctuation_or_footnote_markers():
    assert normalise_label("b(ii) Total Scope 2 (market-based)¹²") == (
        "b ii total scope 2 market based"
    )
    assert normalise_label("Scope 1  emissions*") == normalise_label("scope 1 emissions")
    assert normalise_label("tCO₂e") == "tco2e"  # a subscript is a digit, not a marker


def test_every_listed_cell_drafted_is_complete():
    result = match_cells(
        [listed("Scope 1"), listed("Scope 2", scope="2", method="market")],
        [drafted("SCOPE 1¹"), drafted("Scope 2", scope="2", method="market")],
        pages=[5],
    )
    assert (result.listed, result.read, len(result.matched)) == (2, 2, 2)
    assert result.skipped == [] and result.extra == [] and result.problems == []


def test_a_listed_cell_with_no_draft_is_skipped_and_an_unlisted_draft_is_extra():
    result = match_cells(
        [listed("Scope 1"), listed("Scope 3 category 6", scope="3")],
        [drafted("Scope 1"), drafted("Refrigerants", scope="1")],
        pages=[5],
    )
    assert [e.row_label for e in result.skipped] == ["Scope 3 category 6"]
    assert [c.drafted_row_label for c in result.extra] == ["Refrigerants"]


def test_page_scope_and_column_must_agree_too():
    cell = drafted("Scope 2", scope="2", method="location")
    for entry in (
        listed("Scope 2", scope="2", method="location", page=6),
        listed("Scope 2", scope="1", method="location"),
        listed("Scope 2", scope="2", method="location", header="Singapore > 2025"),
    ):
        result = match_cells([entry], [cell], pages=[5, 6])
        assert result.matched == [] and result.method_differs == []


def test_a_listed_year_matches_the_end_of_a_drafted_heading_path():
    # Seatrium p.70: the drafter wrote the nested header in full, the author listed the year.
    cell = drafted("Total", header="SCOPE 1 & 2 > Scope 1 > 2025*")
    for header in ("2025", "2025*", "Scope 1 > 2025", "scope 1 & 2 > scope 1 > 2025"):
        assert len(match_cells([listed("Total", header=header)], [cell], [5]).matched) == 1
    for header in ("2024", "Scope 2 > 2025", "Group > SCOPE 1 & 2 > Scope 1 > 2025"):
        assert match_cells([listed("Total", header=header)], [cell], [5]).matched == []
    assert header_matches("2025", "2025\u00b9")  # ST Engineering p.37's footnote marker


def test_one_drafted_cell_pairs_with_only_one_listed_cell():
    result = match_cells([listed("Scope 1"), listed("Scope 1 ")], [drafted("Scope 1")], [5])
    assert len(result.matched) == 1 and len(result.skipped) == 1
    assert any("listed twice" in p for p in result.problems)


def test_a_differently_spelt_row_is_paired_only_by_the_author():
    entry = listed("Direct emissions")
    cell = drafted("Scope 1 (direct)")
    assert match_cells([entry], [cell], [5]).matched == []  # never guessed
    paired = listed("Direct emissions", paired="Scope 1 (direct)")
    by_hand = match_cells([paired], [cell], [5])
    assert by_hand.matched == [] and len(by_hand.paired_by_hand) == 1  # never "exact"
    wrong = listed("Direct emissions", paired="Scope 1 (indirect)")
    assert match_cells([wrong], [cell], [5]).problems


def test_a_listed_page_that_is_not_a_table_page_is_a_problem():
    assert match_cells([listed(page=9)], [], pages=[5]).problems


def test_the_digest_ignores_the_later_pairing_and_line_order_but_not_the_list():
    a, b = listed("Scope 1"), listed("Scope 2", scope="2")
    assert inventory_digest([a, b]) == inventory_digest([b, listed("Scope 1", paired="x")])
    assert inventory_digest([a, b]) != inventory_digest([a])


def test_an_inventory_line_needs_a_label_header_and_valid_scope():
    with pytest.raises(ValueError):
        listed(label="")
    with pytest.raises(ValueError):
        listed(scope="4")


# --- the three defects of the first completeness run (2b, 31 Oct) ---------------------------
# Each test below failed against the first version of `match_cells`.


def test_a_cell_read_but_dropped_by_a_rule_counts_as_read_not_skipped():
    # Defect 1: UOB p.136's cells were read, then dropped for a unit printed on the page
    # before; the first run reported them as skipped by the drafter.
    result = match_cells(
        [listed("Singapore", scope="2", method="location")],
        [drafted("Singapore", scope="2", method="location", outcome="unit_not_ghg")],
        [5],
    )
    assert result.skipped == [] and len(result.matched) == 1
    assert result.dropped_by_rule == {"unit_not_ghg": 1}


def test_a_drafted_group_heading_prefix_matches_the_listed_row():
    # Defect 2: the drafter writes "Scope 1 > Refrigerants" for a row under a heading.
    result = match_cells([listed("Refrigerants")], [drafted("Scope 1 > Refrigerants")], [5])
    assert len(result.matched) == 1 and result.extra == []
    # ...but only the last segment, whole: a heading is not a label.
    assert match_cells([listed("Scope 1")], [drafted("Scope 1 > Refrigerants")], [5]).matched == []


def test_rows_with_one_label_are_told_apart_by_method_before_any_method_differs_pairing():
    result = match_cells(
        [listed("Total", scope="2", method="market"),
         listed("Total", scope="2", method="location")],
        [drafted("Scope 2 location-based > Total", scope="2", method="location"),
         drafted("Scope 2 market-based > Total", scope="2", method="market")],
        [5],
    )  # fmt: skip
    assert len(result.matched) == 2 and result.method_differs == []


def test_a_method_disagreement_is_reported_apart_and_is_not_a_clean_match():
    # Defect 3: OCBC's "Total emissions" - listed with no method, drafted as market-based.
    result = match_cells(
        [listed("Total emissions (tCO2e)", scope="1+2+3")],
        [drafted("Total emissions (tCO₂e)", scope="1+2+3", method="market")],
        [5],
    )
    assert result.matched == [] and result.skipped == [] and result.extra == []
    (pair,) = result.method_differs
    assert pair.listed.method is None and pair.drafted.method == "market"


def test_completeness_reads_every_report_year_cell_the_drafter_read_not_only_kept_rows():
    # Defect 1, the half that lived in the script: it fed `match_cells` the kept rows only.
    def cell(label, header):
        return {"row_label_printed": label, "column_header_printed": header,
                "scope": "2", "method": "location"}  # fmt: skip

    pages = [
        {
            "pdf_page": 136,
            "reading": {
                "cells": [
                    cell("Singapore", "2025"),
                    cell("Singapore", "2024"),
                    cell("Indonesia", "2025"),
                ]
            },  # fmt: skip
            "outcomes": ["unit_not_ghg", "column_not_report_year", "kept"],
        }
    ]
    cells = report_year_cells(pages, 2025)
    assert [(c.drafted_row_label, c.outcome) for c in cells] == [
        ("Singapore", "unit_not_ghg"),
        ("Indonesia", "kept"),
    ]


def test_a_draft_without_outcomes_is_refused_not_guessed():
    pages = [{"pdf_page": 1, "reading": {"cells": [{"row_label_printed": "x"}]}}]
    with pytest.raises(ValueError, match="re-run `draft`"):
        report_year_cells(pages, 2025)


def test_a_cell_listed_with_no_year_column_pairs_with_a_drafted_cell_whose_header_names_none():
    # UOB p.189: the author lists the cell as NO_YEAR_COLUMN; the model may print any header
    # that names no year (blank, a unit), so the header is not compared, only checked.
    entry = listed("Generation of Scope 1 emissions", page=189, header=NO_YEAR_COLUMN)
    result = match_cells(
        [entry], [drafted("Generation of Scope 1 emissions", page=189, header="tCO2e")], [189]
    )
    assert len(result.matched) == 1 and result.skipped == []
    dated = drafted("Generation of Scope 1 emissions", page=189, header="2025")
    assert match_cells([entry], [dated], [189]).skipped == [entry]


def test_completeness_counts_a_year_less_cell_on_a_declared_page_only():
    def cell(label, header):
        return {"row_label_printed": label, "column_header_printed": header,
                "scope": "1", "method": None}  # fmt: skip

    page = {
        "pdf_page": 189,
        "reading": {"cells": [cell("Scope 1", ""), cell("Scope 1", "2024")]},
        "outcomes": ["kept", "column_not_report_year"],
    }
    assert [c.column_header for c in report_year_cells([page], 2025)] == []
    declared = page | {"declared_year": 2025}
    assert [c.column_header for c in report_year_cells([declared], 2025)] == [""]


def test_worksheet_lines_told_apart_only_by_method_reach_their_own_drafts():
    drafts = [_draft(1, "Total", method="location"), _draft(2, "Total", method="market")]
    entries = [
        _entry("Total", method="market", outcome="rejected"),
        _entry("Total", method="location"),
    ]
    plan = plan_verification(drafts, entries)
    assert plan.problems == []
    assert {(u.id, u.verification) for u in plan.updates} == {(1, "confirmed"), (2, "rejected")}


def test_a_worksheet_line_reads_its_method():
    row = {"pdf_page": "9", "row_label": "Total", "column_header": "2025",
           "method": "market", "blind_value_by_author": "",
           "outcome (ok/corrected/rejected)": "ok",
           "correct_figure_as_printed": "", "correct_row_label_as_printed": "",
           "note": ""}  # fmt: skip
    assert WorksheetEntry.from_csv_row(row).method == "market"
    assert WorksheetEntry.from_csv_row(row | {"method": ""}).method is None


def test_a_hand_pairing_is_reported_apart_from_exact_matches_and_skips():
    entries = [
        listed("Generation of Scope 2 emissions (location-based)", scope="2", method="location",
               paired="Energy indirect (Scope 2) GHG emissions"),
        listed("Generation of Scope 2 emissions (market-based)", scope="2", method="market",
               paired="Energy indirect (Scope 2) GHG emissions"),
        listed("Scope 1"),
    ]  # fmt: skip
    cells = [
        drafted("Energy indirect (Scope 2) GHG emissions", scope="2", method="market"),
        drafted("Energy indirect (Scope 2) GHG emissions", scope="2", method="location"),
        drafted("Scope 1"),
    ]
    result = match_cells(entries, cells, [5])
    assert [m.listed.row_label for m in result.matched] == ["Scope 1"]
    assert [(m.listed.method, m.drafted.method) for m in result.paired_by_hand] == [
        ("location", "location"),
        ("market", "market"),
    ]
    assert result.skipped == [] and result.extra == [] and result.method_differs == []


# --- correcting the method (2b: OCBC p.58 "Total emissions" states no method) ----------------


def test_a_method_corrected_to_none_clears_it_and_says_so():
    drafts = [_draft(1, "Total emissions", method="market")]
    entry = _entry("Total emissions", outcome="corrected", method="market",
                   correct_method="none", note="Label states no method.")  # fmt: skip
    plan = plan_verification(drafts, [entry])
    assert plan.problems == []
    (update,) = plan.updates
    assert update.verification == "corrected" and update.method is None
    assert update.figure_printed == "1,234" and update.row_label == "Total emissions"
    assert "method corrected: drafted 'market', verified as not stated" in update.notes
    assert "Label states no method." in update.notes


def test_an_unchanged_method_stays_as_drafted():
    plan = plan_verification([_draft(1, "Total", method="market")],
                             [_entry("Total", method="market")])  # fmt: skip
    assert plan.updates[0].method == "market"


def test_a_method_correction_needs_outcome_corrected():
    with pytest.raises(ValidationError, match="a correction given"):
        _entry("Total", outcome="ok", correct_method="none")
    with pytest.raises(ValidationError):
        _entry("Total", outcome="corrected", correct_method="unknown")


def test_a_worksheet_without_the_correct_method_column_still_reads():
    row = {"pdf_page": "9", "row_label": "Total", "column_header": "2025", "method": "",
           "blind_value_by_author": "", "outcome (ok/corrected/rejected)": "ok",
           "correct_figure_as_printed": "", "correct_row_label_as_printed": "",
           "note": ""}  # fmt: skip
    assert WorksheetEntry.from_csv_row(row).correct_method is None
    corrected = row | {"outcome (ok/corrected/rejected)": "corrected",
                       "correct_method (location/market/none)": "none"}  # fmt: skip
    assert WorksheetEntry.from_csv_row(corrected).correct_method == "none"
