"""The figure audit: which numbers a reply is allowed to contain.

The audit is only worth having if it is both strict and quiet - it must catch a number
the tools never produced, and it must not cry wolf over the ordinary ways a correct
answer restates a figure it was given. Both halves are tested here, because a noisy
audit gets switched off and then catches nothing.
"""

from decimal import Decimal

import pytest

from carbon_r.agent.audit import (
    audit_figures,
    figures_in,
    has_impossible_scope,
    has_malformed_thousands,
    is_table_like,
    numbers_in,
)

PAYLOADS = [
    {
        "tool": "calculate_emissions",
        "status": "ok",
        "message": "139,600 kWh x 0.402 kg CO2 / kWh = 56,119.2 kg CO2e",
        "data": {
            "kg_co2e": "56119.200",
            "tonnes_co2e": "56.119200",
            "inputs": {
                "activity_amount": "139600",
                "factor": {"factor": "0.402", "year_used": 2024, "gst_rate": "0.09"},
            },
            "disclosures": {"not_assured": True},
        },
    }
]


def audit(reply: str) -> list[str]:
    return audit_figures(reply, PAYLOADS)


# --- what must pass ---------------------------------------------------------------


def test_a_figure_the_tool_returned_passes():
    assert audit("Scope 2 emissions are 56119.200 kg CO2e.") == []


def test_thousands_separators_pass():
    assert audit("56,119.2 kg CO2e from 139,600 kWh.") == []


def test_a_stated_rounding_passes():
    """Reporting 56,119.2 for a returned 56119.200 is reporting, not computing."""
    assert audit("About 56,119 kg CO2e (56.12 tonnes).") == []


def test_a_fraction_written_as_a_percentage_passes():
    assert audit("GST on the bill is 9%.") == []


def test_scope_and_tier_labels_are_not_figures():
    assert audit("These are Scope 2 emissions, Scope 1 is not covered.") == []


def test_a_number_inside_a_word_is_not_scanned():
    assert audit("Reported as kg CO2e in document elec_001.pdf.") == []


def test_the_citation_year_passes():
    assert audit("The factor is EMA's 2024 grid emission factor, 0.402 kg CO2 / kWh.") == []


# --- what must be caught ----------------------------------------------------------


def test_an_invented_total_is_flagged():
    assert audit("Your emissions are 60,000 kg CO2e.") == ["60,000"]


def test_an_annualised_figure_is_flagged():
    """12 x the month is arithmetic, and arithmetic is not the model's job."""
    assert audit("Annualised that is roughly 673,430 kg CO2e.") == ["673,430"]


def test_a_mistyped_factor_is_flagged():
    assert audit("The factor used was 0.42 kg CO2 / kWh.") == ["0.42"]


def test_every_untraceable_number_is_listed_in_order():
    assert audit("Between 50,000 and 60,000 kg.") == ["50,000", "60,000"]


# --- the allowed set --------------------------------------------------------------


def test_numbers_are_found_however_deeply_nested():
    """Compared as Decimals: 56119.200 and 56119.2 are one number, not two."""
    found = numbers_in(PAYLOADS)
    assert all(Decimal(v) in found for v in ("0.402", "56119.200", "2024", "0.09"))


def test_booleans_are_not_numbers():
    assert numbers_in({"not_assured": True, "substituted": False}) == set()


def test_an_empty_session_allows_no_figure_at_all():
    assert audit_figures("It is 42 kg.", []) == ["42"]


def test_disclosure_sentences_are_printable_not_directives():
    """A disclosure is read by a person. The first live run copied 'State both years.'"""
    from carbon_r.agent.prompt import DISCLOSURE_SENTENCES

    for name, sentence in DISCLOSURE_SENTENCES.items():
        assert not sentence.rstrip().endswith("years."), name
        assert "State " not in sentence, name
        assert "Say " not in sentence, name


# --- numbered-list markers ---------------------------------------------------------------


def test_a_list_marker_at_the_start_of_a_line_is_not_a_figure():
    reply = "To load it:\n1. Download the three standards\n2) Run the ingest"
    assert audit_figures(reply, []) == []


def test_a_figure_later_on_a_list_line_is_still_flagged():
    assert audit_figures("1. Use 0.42 kg per kWh", []) == ["0.42"]


def test_an_indented_marker_is_a_marker_too():
    assert audit_figures("Steps:\n  3. Ask again", []) == []


def test_a_number_mid_line_followed_by_a_full_stop_is_still_flagged():
    assert audit_figures("That was step 3. Then ask.", []) == ["3"]


def test_a_decimal_at_the_start_of_a_line_is_not_a_marker():
    assert audit_figures("1.5 kg was emitted.", []) == ["1.5"]


def test_a_year_starting_a_line_is_still_audited():
    """The marker is capped at two digits, so "2024. " is not mistaken for one."""
    assert audit_figures("2024. That year's factor.", []) == ["2024"]


@pytest.mark.parametrize("identifier", ["R-32", "R-410a", "CO2e", "PM2.5", "GHG-2", "32-R"])
def test_a_number_joined_to_letters_is_an_identifier_not_a_figure(identifier):
    assert figures_in(f"The refrigerant {identifier} was replaced.") == []


def test_a_negative_number_and_a_year_range_are_still_figures():
    assert figures_in("The change was -3.5 tonnes.") == ["-3.5"]
    assert "2024" in figures_in("In FY 2024-25 usage rose.")


# --- fused footnote markers make a quote table-like (Part B) --------------------------------


@pytest.mark.parametrize(
    ("text", "malformed"),
    [
        ("Total 240,5763 tCO2e", True),  # 240,576 + note 3 (invented)
        ("Total 2,5113 tCO2e", True),
        ("Total 1,48 tCO2e", True),  # two digits after the comma
        ("Total 240,576 tCO2e", False),
        ("Total 1,234,567 tCO2e", False),
        ("In 2024, the bank cut 3, then 4, sites.", False),  # commas in prose
        ("Total 12345 tCO2e", False),  # no comma: a fusion here is invisible to this rule
    ],
)
def test_a_malformed_thousands_group(text, malformed):
    assert has_malformed_thousands(text) is malformed


@pytest.mark.parametrize(
    ("text", "impossible"),
    [
        ("Total scope 17 1,250 tCO2e", True),  # Scope 1 + note 7 (invented)
        ("Scope 4 avoided emissions", True),
        ("Scope 1, Scope 2 and Scope 3", False),
        ("Scope 3 category 15 investments", False),
        ("Scope 3 categories 1-15", False),
    ],
)
def test_an_impossible_scope_label(text, impossible):
    assert has_impossible_scope(text) is impossible


def test_the_fused_example_is_table_like_although_it_has_one_figure():
    quote = "Total scope 17 2,5113 tCO2e."
    assert len(figures_in(quote)) == 1  # the count and share rules alone would let it through
    assert is_table_like(quote)


def test_ordinary_prose_with_a_scope_and_a_number_is_not_table_like():
    assert not is_table_like("We cut Scope 3 category 6 travel emissions by 1,200 tonnes.")
