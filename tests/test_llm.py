"""`openai_client`: an explicit timeout and retry budget, and a log line per retry. Offline."""

import logging

import httpx2

from carbon_r.llm import OPENAI_MAX_RETRIES, OPENAI_TIMEOUT_S, log_retry, openai_client


def request(retry_count: str) -> httpx2.Request:
    return httpx2.Request(
        "POST",
        "https://api.openai.invalid/v1/responses",
        headers={"x-stainless-retry-count": retry_count},
    )


def _placeholder_key(monkeypatch) -> None:
    """Building a client needs a key, never a network call. CI has no key."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-offline-placeholder")


def test_the_client_does_not_take_the_sdk_defaults(monkeypatch):
    _placeholder_key(monkeypatch)
    client = openai_client()
    assert client.max_retries == OPENAI_MAX_RETRIES == 1
    assert client.timeout == OPENAI_TIMEOUT_S == 120.0


def test_a_caller_can_set_its_own_timeout(monkeypatch):
    _placeholder_key(monkeypatch)
    client = openai_client(timeout_s=300.0, max_retries=0)
    assert client.timeout == 300.0 and client.max_retries == 0


def test_the_first_attempt_is_not_logged(caplog):
    with caplog.at_level(logging.WARNING, logger="carbon_r.llm"):
        log_retry(request("0"), timeout_s=120.0, max_retries=1)
    assert caplog.records == []


def test_a_retry_is_logged_with_its_number_and_path(caplog):
    with caplog.at_level(logging.WARNING, logger="carbon_r.llm"):
        log_retry(request("1"), timeout_s=120.0, max_retries=1)
    (record,) = caplog.records
    assert record.levelno == logging.WARNING
    assert record.getMessage() == (
        "openai retry 1 of 1: POST /v1/responses (timeout 120 s per attempt)"
    )
