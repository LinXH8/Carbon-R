"""The figure audit: which numbers a reply is allowed to contain.

The audit is only worth having if it is both strict and quiet - it must catch a number
the tools never produced, and it must not cry wolf over the ordinary ways a correct
answer restates a figure it was given. Both halves are tested here, because a noisy
audit gets switched off and then catches nothing.
"""

from decimal import Decimal

from carbon_r.agent.audit import audit_figures, numbers_in

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
