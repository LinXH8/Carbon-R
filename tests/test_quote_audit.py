"""`audit_quotes`: every quoted span in a reply must be something a tool returned."""

from carbon_r.agent.audit import audit_quotes

PAYLOADS = [
    {
        "quotes": [
            {
                "quote": "reports the fuel it burns as a direct emission",
                "citation": "Test Standard, p. 12",
            }
        ],
        "passages": [{"text": "Emissions from a van it does not control are reported elsewhere."}],
    }
]


def test_a_quote_a_tool_returned_passes():
    reply = 'The standard says it "reports the fuel it burns as a direct emission" (p. 12).'
    assert audit_quotes(reply, PAYLOADS) == []


def test_curly_quotes_are_checked_too():
    reply = "It says “reports the fuel it burns as a direct emission”."
    assert audit_quotes(reply, PAYLOADS) == []


def test_whitespace_differences_do_not_matter():
    reply = 'It "reports the fuel   it burns\tas a direct emission".'
    assert audit_quotes(reply, PAYLOADS) == []


def test_a_quote_no_tool_returned_is_flagged_as_written():
    reply = 'It says "leased vans are always Scope 1".'
    assert audit_quotes(reply, PAYLOADS) == ["leased vans are always Scope 1"]


def test_each_piece_around_an_ellipsis_must_be_there():
    good = 'It "reports the fuel ... as a direct emission".'
    bad = 'It "reports the fuel ... as an indirect emission".'
    assert audit_quotes(good, PAYLOADS) == []
    assert audit_quotes(bad, PAYLOADS) == ["reports the fuel ... as an indirect emission"]


def test_a_quote_spliced_from_two_strings_is_flagged():
    reply = 'It says "as a direct emission. Emissions from a van".'
    assert audit_quotes(reply, PAYLOADS) == ["as a direct emission. Emissions from a van"]


def test_no_quotes_no_flags():
    assert audit_quotes("No quotation here.", []) == []
