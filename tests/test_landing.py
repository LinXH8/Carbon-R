"""The landing page is served at the root, and the tool moved to /app intact.

The scene is decoration, so these guard what it must not do: break the way in, float the
three.js version, expose the canvas to assistive technology, or drift from the careful
Scope 1 / Scope 2 wording on the hover labels.
"""

import json
import re

from fastapi.testclient import TestClient

from carbon_r import main

client = TestClient(main.app)


def test_the_landing_page_is_served_at_the_root():
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert re.search(r"<h1>.*Carbon R</h1>", response.text)
    assert '<a class="start" href="/app">Start</a>' in response.text


def test_the_tool_is_served_at_app():
    response = client.get("/app")
    assert response.status_code == 200
    assert "<title>Carbon R</title>" in response.text
    assert 'id="dropzone"' in response.text


def test_the_landing_assets_are_served():
    for path, content_type in (
        ("/static/landing.css", "text/css"),
        ("/static/landing.js", "javascript"),
        ("/static/scene/scene.js", "javascript"),
        ("/static/scene/world.js", "javascript"),
        ("/static/scene/smoke.js", "javascript"),
        ("/static/scene/kit.js", "javascript"),
        ("/static/scene/merge.js", "javascript"),
        ("/static/scene/water.js", "javascript"),
        ("/static/scene/harbour.js", "javascript"),
        ("/static/scene/airfield.js", "javascript"),
    ):
        response = client.get(path)
        assert response.status_code == 200, path
        assert content_type in response.headers["content-type"], path


def test_three_is_pinned_to_an_exact_version():
    page = client.get("/").text
    importmap = json.loads(re.search(r'<script type="importmap">(.*?)</script>', page, re.S)[1])
    url = importmap["imports"]["three"]
    pinned = r"https://cdn\.jsdelivr\.net/npm/three@\d+\.\d+\.\d+/build/three\.module\.js"
    assert re.fullmatch(pinned, url)


def test_the_scene_is_hidden_from_assistive_technology():
    page = client.get("/").text
    assert '<div class="scene" id="scene" aria-hidden="true">' in page
    assert "tabindex" not in page


def test_the_hover_labels_keep_their_exact_wording():
    script = client.get("/static/scene/scene.js").text
    for wording in (
        "Scope 1: fuel burned in vehicles you own",
        "Scope 1: fuel burned on site",
        "Scope 2 for the businesses that buy its electricity",
        "Scope 1 for the operator; Scope 3 for businesses that ship goods on it",
        "Scope 1 for the operator; Scope 3 for business travel on it",
    ):
        assert f"'{wording}'" in script, wording


def test_moving_the_tool_did_not_shadow_the_api():
    assert client.get("/health").status_code == 200
    assert client.post("/chat", json={}).status_code == 422  # validated, not 404
