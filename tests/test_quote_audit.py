"""`audit_quotes`: every quoted span in a reply must be something a tool returned."""

from carbon_r.agent.audit import audit_figures, audit_quotes

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


def test_a_quoted_scope_label_is_not_a_quotation():
    # Interlude D: a reply naming "Scope 1, 2, and 3" was flagged as an unsourced quote.
    assert audit_quotes('Emissions are grouped as "Scope 1, 2, and 3".', []) == []
    assert audit_quotes("This is “Scope 2”.", []) == []


def test_a_quote_that_mentions_a_scope_is_still_checked():
    assert audit_quotes('It says "leased vans are always Scope 1".', []) == [
        "leased vans are always Scope 1"
    ]


def test_the_users_own_words_quoted_back_pass():
    # Interlude D / TIER2_BLOCK4 run 2c: an off-topic refusal echoed the question.
    reply = 'The tools do not cover "best Hainanese chicken rice."'
    asked = ["Which canteen has the best Hainanese chicken rice."]
    assert audit_quotes(reply, [], asked) == []
    assert audit_quotes(reply, []) == ["best Hainanese chicken rice."]


def test_words_neither_the_user_nor_a_tool_wrote_are_still_flagged():
    reply = 'You asked about "the cheapest chicken rice".'
    asked = ["Which canteen has the best Hainanese chicken rice?"]
    assert audit_quotes(reply, PAYLOADS, asked) == ["the cheapest chicken rice"]


def test_the_users_words_never_clear_a_figure():
    # A figure the user typed is not one a tool produced (CLAUDE.md rules 1 and 2), so a
    # quote of the user carries no exemption for the numbers in it.
    reply = 'You said "our diesel use was 4,200 litres last year".'
    asked = ["I think our diesel use was 4,200 litres last year; what are the emissions?"]
    assert audit_quotes(reply, [], asked) == []
    assert audit_figures(reply, []) == ["4,200"]


def test_a_full_stop_inside_the_quote_of_the_users_question_passes():
    # Live check after 9394e02: the user's "?" came back as "." inside the quotation marks.
    reply = "I can’t recommend food places like “the best Hainanese chicken rice.”"
    asked = ["Where can I find the best Hainanese chicken rice?"]
    assert audit_quotes(reply, [], asked) == []


def test_a_full_stop_added_to_a_tool_quote_passes():
    reply = 'The standard says it "reports the fuel it burns as a direct emission."'
    assert audit_quotes(reply, PAYLOADS) == []


def test_a_quote_never_ends_partway_through_a_number():
    # "2." occurs in "2.5" as text, so plain substring matching accepted this before.
    source = [{"text": "Scope 1 emissions were 2.5 t."}]
    reply = 'The report says "emissions were 2."'
    assert audit_quotes(reply, source) == ["emissions were 2."]
    # Not a quote check: the figure audit accepts 2 as 2.5 rounded to 0 places, by design.
    assert audit_figures(reply, source) == []


def test_a_quote_cut_short_of_a_number_exempts_none_of_its_figures():
    # Five words or more, so a quote that matched would exempt its figures; this one must not.
    source = [{"tool": "search_methodology", "text": "Total emissions for the year were 2.7 t."}]
    reply = 'The report says "Total emissions for the year were 2."'
    assert audit_quotes(reply, source) == ["Total emissions for the year were 2."]
    assert audit_figures(reply, source) == ["2"]


def test_a_quote_in_no_source_is_still_flagged_with_its_full_stop():
    reply = 'It says "leased vans are always Scope 1."'
    asked = ["Are leased vans Scope 1?"]
    assert audit_quotes(reply, PAYLOADS, asked) == ["leased vans are always Scope 1."]
