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


PREVIEWS = ("minimal", "soft", "workpaper", "basalt", "graphite", "anthracite")


def test_the_design_previews_and_their_assets_are_served():
    for name in (*PREVIEWS, "index"):
        response = client.get(f"/static/preview/{name}.html")
        assert response.status_code == 200, name
        assert "<title>Carbon R" in response.text, name
    for name in PREVIEWS:
        assert "text/css" in client.get(f"/static/preview/{name}.css").headers["content-type"]
    for path in ("/static/preview/render.js", "/static/preview/preview.js"):
        assert "javascript" in client.get(path).headers["content-type"], path


def test_the_preview_renderer_takes_numbers_from_figures_only():
    """The same guard as the main page, on the renderer the previews share."""
    script = client.get("/static/preview/render.js").text
    assert "final.figures" in script
    assert "figure.kg_co2e" in script
    assert "reply.match" not in script
    assert "parseFloat" not in script
    assert "Number(" not in script


def test_the_preview_renderer_shows_every_part_of_a_citation():
    """Factor, unit, publisher, both years, source, page, jurisdiction, verification, caveats."""
    script = client.get("/static/preview/render.js").text
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
        assert field in script, field


def test_the_preview_sample_figures_were_computed_and_audited():
    """sample.json comes from scripts/build_ui_sample.py, not from a hand-typed number."""
    import json

    samples = json.loads(client.get("/static/preview/sample.json").text)
    assert set(samples) == {"electricity", "fuel", "flagged"}
    for key, sample in samples.items():
        final = sample["final"]
        assert final["figures"], key
        for figure in final["figures"]:
            assert figure["lines"] and all(line["factor"]["publisher"] for line in figure["lines"])
    assert samples["electricity"]["final"]["unverified_figures"] == []
    assert samples["flagged"]["final"]["unverified_figures"]  # the audit caught the reply
