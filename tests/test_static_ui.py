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
        ("/static/spring.js", "javascript"),
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
    script = client.get("/static/app.js").text
    assert "final.figures" in script
    assert "figure.kg_co2e" in script
    # No regex hunting for digits in the model's prose, and no float round-trip.
    assert "reply.match" not in script
    assert "parseFloat" not in script
    assert "Number(" not in script
