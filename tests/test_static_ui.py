"""The page is served, and serving it did not shadow the API.

Mounting static files at `/` would make every route declared after the mount unreachable -
a one-character difference that would take `/health` and `/chat` down while the page
itself kept working. These tests are cheap insurance against exactly that.
"""

from fastapi.testclient import TestClient

from carbon_r import main

client = TestClient(main.app)


def test_the_page_is_served_at_the_root():
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<title>Carbon R</title>" in response.text


def test_the_page_assets_are_served():
    for path, content_type in (
        ("/static/app.css", "text/css"),
        ("/static/app.js", "javascript"),
        ("/static/render.js", "javascript"),
    ):
        response = client.get(path)
        assert response.status_code == 200, path
        assert content_type in response.headers["content-type"], path


def test_mounting_the_page_did_not_shadow_the_api():
    assert client.get("/health").status_code == 200
    assert client.post("/chat", json={}).status_code == 422  # validated, not 404


def test_the_page_renders_figures_rather_than_parsing_the_reply():
    """A guard on the rule, not on the markup.

    The UI must take its numbers from the structured `figures` field. If someone ever
    reaches for `reply` to find a number, this is the test that should start looking wrong.
    """
    renderer = client.get("/static/render.js").text
    assert "final.figures" in renderer
    assert "figure.kg_co2e" in renderer
    # No regex hunting for digits in the model's prose, and no float round-trip.
    for script in (renderer, client.get("/static/app.js").text):
        assert "reply.match" not in script
        assert "parseFloat" not in script
        assert "Number(" not in script


def test_the_page_shows_every_part_of_a_citation():
    """Factor, unit, publisher, both years, source, page, jurisdiction, verification, caveats."""
    renderer = client.get("/static/render.js").text
    for field in (
        "f.factor",
        "f.unit",
        "f.publisher",
        "f.year_used",
        "f.year_requested",
        "f.source_document",
        "f.source_page",
        "f.jurisdiction",
        "f.verified",
        "f.substitution_note",
        "f.source_url",
        "required_disclosures",
    ):
        assert field in renderer, field


def test_the_audit_line_states_what_the_audit_found():
    """The old page printed the pass sentence even under a failed audit."""
    renderer = client.get("/static/render.js").text
    assert "Figure audit passed" in renderer
    assert "Figure audit failed" in renderer


def test_the_page_and_its_assets_are_always_revalidated():
    # No build step means no hashed filenames: without no-cache an edit can stay invisible.
    for path in ("/", "/app", "/static/app.css", "/static/landing.js"):
        assert client.get(path).headers["cache-control"] == "no-cache", path


def test_a_question_does_not_need_a_document():
    """The composer is always there; attaching a document is optional.

    The page used to hide the question until a file was uploaded, though /chat has always
    answered without one (methodology questions need none).
    """
    page = client.get("/app").text
    assert 'id="question"' in page
    assert 'id="attach"' in page
    assert "hidden" not in page.split('id="composer"')[1].split(">")[0]


def test_a_failed_audit_stays_visible_when_folded_away():
    """The audit findings sit in a closed activity panel; their count must not.

    Folding the red notices into the panel is a layout choice. Hiding that the audit failed
    would not be: the closed summary carries the number of flags.
    """
    renderer = client.get("/static/render.js").text
    script = client.get("/static/app.js").text
    assert "unverified_figures" in renderer
    assert "unverified_quotes" in renderer
    assert "audit flag" in script
    assert "activity-flags" in script
