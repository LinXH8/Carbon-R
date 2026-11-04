"""Eval 5a's fixed parts, offline: the instruction differs by its input sentence only, and
the page scorer counts presence and fields. Invented rows; no model call."""

from carbon_r.models.peer_disclosure import DRAFT_INSTRUCTION, DraftedCell
from eval_peer_extraction import ARMS, instruction, score_by_figure, score_page


def test_each_arm_changes_only_the_first_sentence():
    rest = DRAFT_INSTRUCTION.split("\n", 1)[1]
    assert instruction("vision") == DRAFT_INSTRUCTION
    for arm in ARMS:
        assert instruction(arm).split("\n", 1)[1] == rest
    assert len({instruction(a).split("\n", 1)[0] for a in ARMS}) == 3


def _cell(label="Scope 1", header="2025", figure="1,234", unit="tCO2e", scope="1", method=None):
    return DraftedCell(row_label_printed=label, column_header_printed=header,
                       figure_printed=figure, footnote_markers_printed=None, unit_printed=unit,
                       scope=scope, scope3_category=None, method=method,
                       method_evidence_printed=None)  # fmt: skip


def _verified(label="Scope 1", header="2025", figure="1,234", unit="tCO2e", scope="1",
              method=None) -> dict:  # fmt: skip
    return {"row_label": label, "column_header": header, "figure_printed": figure,
            "unit": unit, "scope": scope, "method": method}  # fmt: skip


def _score(verified, cells, declared_year=None, carried=None):
    return score_page(verified, cells, ["kept"] * len(cells), pdf_page=9, report_year=2025,
                      declared_year=declared_year, carried_unit=carried)  # fmt: skip


def test_a_correct_reading_is_present_with_every_field_right():
    s = _score([_verified()], [_cell(), _cell(header="2024")])
    assert (s["present"], s["missed"], s["figure_exact"], s["unit_exact"]) == (1, 0, 1, 1)
    assert s["extra_kept"] == 0  # the 2024 cell is not a report-year cell


def test_a_fused_footnote_marker_is_counted():
    s = _score([_verified(figure="240,576")], [_cell(figure="2,405,763")])
    assert s["present"] == 1 and s["figure_exact"] == 0 and s["figure_fused_marker"] == 1


def test_a_row_read_only_under_another_year_is_missed_and_said_so():
    s = _score([_verified()], [_cell(header="2024")])
    assert s["missed"] == 1 and s["missed_read_under_another_year"] == 1
    assert s["missed_rows"] == ["Scope 1"]


def test_a_heading_label_matches_its_last_segment_and_method_is_scored():
    v = _verified(label="Scope 2 location-based > Total", scope="2", method="location")
    s = _score([v], [_cell(label="Total", scope="2", method="market")])
    assert (s["present"], s["method_differs"], s["method_exact"]) == (1, 1, 0)


def test_a_carried_unit_counts_and_a_declared_year_page_ignores_the_header():
    s = _score([_verified(header="Coverage")], [_cell(header="", unit="")],
               declared_year=2025, carried="tCO2e")  # fmt: skip
    assert (s["present"], s["unit_exact"]) == (1, 1)


def test_an_unverified_report_year_cell_is_extra():
    s = _score([_verified()], [_cell(), _cell(label="Scope 3")])
    assert s["extra_kept"] == 1


# --- by figure: label and header ignored ----------------------------------------------------


def _by_figure(verified, cells):
    strict = _score(verified, cells)
    return score_by_figure(verified, cells, strict["missed_rows"], report_year=2025,
                           declared_year=None)  # fmt: skip


def test_footnote_letters_on_a_label_are_cosmetic():
    v = _verified(label="Category 2: Capital Goods", header="Singapore > 2025")
    s = _by_figure(
        [v], [_cell(label="Category 2: Capital Goodsiii, iv", header="Singapore > 2025")]
    )
    assert (s["by_figure_present"], s["by_figure_only_cosmetic"]) == (1, 1)


def test_a_lost_spanning_heading_is_substantive():
    v = _verified(header="Total Portfolio > 2025")
    s = _by_figure([v], [_cell(header="2025")])
    assert (s["by_figure_present"], s["by_figure_only_substantive"]) == (1, 1)


def test_one_label_read_for_two_rows_is_substantive():
    a = _verified(label="b(i) Total scope 2 (location-based)", figure="1,000", scope="2")
    b = _verified(label="b(ii) Total scope 2 (market-based)", figure="900", scope="2")
    cells = [_cell(label="Scope 2", figure="1,000", scope="2"),
             _cell(label="Scope 2", figure="900", scope="2")]  # fmt: skip
    s = _by_figure([a, b], cells)
    assert (s["by_figure_present"], s["by_figure_only_substantive"]) == (2, 2)


def test_a_figure_only_under_another_year_is_a_wrong_year_read_not_present():
    s = _by_figure([_verified()], [_cell(header="2024")])
    assert (s["by_figure_present"], s["by_figure_missed_wrong_year"]) == (0, 1)


def test_a_strict_match_is_not_counted_as_by_figure_only():
    s = _by_figure([_verified()], [_cell()])
    assert (s["by_figure_present"], s["by_figure_only"]) == (1, 0)
