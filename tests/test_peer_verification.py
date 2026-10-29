"""Applying the author's verification worksheet (CLAUDE.md rule 4). Values are invented."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from carbon_r.models.peer_verification import (
    WORKSHEET_COLUMNS,
    DraftRow,
    WorksheetEntry,
    plan_verification,
)


def _draft(i: int, label: str, figure: str = "1,234", blind: bool = False) -> DraftRow:
    return DraftRow(
        id=i,
        pdf_page=9,
        drafted_row_label=label,
        column_header="2025",
        drafted_figure_printed=figure,
        blind_sample=blind,
        notes=None,
    )


def _entry(label: str, outcome: str = "ok", **kw) -> WorksheetEntry:
    values = {
        "pdf_page": 9,
        "row_label": label,
        "column_header": "2025",
        "blind_value": None,
        "outcome": outcome,
        "correct_figure": None,
        "correct_row_label": None,
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
