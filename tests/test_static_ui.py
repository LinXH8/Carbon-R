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


def test_a_flagged_reply_is_shown_with_its_warning_above_it():
    """Follow-up 1 (Part B 2b): the warning is not only a count in a closed panel."""
    renderer = client.get("/static/render.js").text
    answer = renderer.split("export function renderAnswer")[1].split("export function")[0]
    assert "final.warnings" in answer
    assert answer.index("final.warnings") < answer.index("class: 'reply'")
    assert "unsupported_hand_verification" in renderer and "no_results_unstated" in renderer


def test_the_no_figure_note_shows_only_where_a_figure_was_expected():
    renderer = client.get("/static/render.js").text
    activity = renderer.split("export function renderActivity")[1].split("export function")[0]
    note = activity.index("No figure was calculated")
    condition = activity.rindex("if (", 0, note)
    assert "final.figure_expected" in activity[condition:note]


# The same two cases, run: the page's own renderActivity in Node with a minimal DOM, so the
# condition is exercised and not only read. Skipped where Node is not installed.
_DOM_SHIM = """
class Node { constructor(tag) { this.tag = tag; this.children = []; this.textContent = ''; }
  append(c) { this.children.push(c); } setAttribute() {} set className(v) {} }
globalThis.document = { createElement: (t) => new Node(t),
  createTextNode: (t) => Object.assign(new Node('#text'), { textContent: t }) };
const text = (n) => (n.textContent || '') + n.children.map(text).join('');
const { renderActivity } = await import(process.argv[1]);
const final = JSON.parse(process.argv[2]);
console.log(JSON.stringify(renderActivity(final).nodes.map(text)));
"""


def _activity_text(final: dict) -> str:
    import json
    import shutil
    import subprocess

    import pytest

    from carbon_r.config import PROJECT_ROOT

    node = shutil.which("node")
    if node is None:
        pytest.skip("needs node")
    script = PROJECT_ROOT / "src" / "carbon_r" / "static" / "render.js"
    result = subprocess.run(
        [node, "--input-type=module", "-e", _DOM_SHIM, "--", script.as_uri(), json.dumps(final)],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    return " ".join(json.loads(result.stdout.splitlines()[-1]))


_PLAIN = {"figures": [], "stopped_reason": "completed", "tool_calls": [],
          "unverified_figures": [], "unverified_quotes": []}  # fmt: skip


def test_a_plain_question_shows_no_no_figure_note():
    assert "No figure was calculated" not in _activity_text(_PLAIN | {"figure_expected": False})


def test_a_question_that_expected_a_figure_shows_the_note():
    assert "No figure was calculated" in _activity_text(_PLAIN | {"figure_expected": True})
