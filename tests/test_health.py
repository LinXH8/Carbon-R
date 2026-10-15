"""Health endpoint.

Must pass with no database and no API key configured — a fresh clone runs the suite
before .env is filled in.
"""

from fastapi.testclient import TestClient

from carbon_r import __version__
from carbon_r.main import app

client = TestClient(app)


def test_health_returns_ok():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_reports_version_and_configuration_flags():
    body = response_body()
    assert body["version"] == __version__
    # Booleans, not truthy strings: callers branch on these.
    assert isinstance(body["db_connected"], bool)
    assert isinstance(body["llm_configured"], bool)


def test_health_model_is_null_or_a_string():
    model = response_body()["model"]
    assert model is None or isinstance(model, str)


def response_body() -> dict:
    return client.get("/health").json()
