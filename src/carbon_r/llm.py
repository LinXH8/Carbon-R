"""The one place an OpenAI client is built: explicit timeout, one retry, a log line per retry.

Every client used to be `OpenAI(api_key=...)`, which takes the SDK's defaults: a 600 s read
timeout and 2 retries, so one stalled call could hold a request for 3 x 600 s = 30 minutes
before failing. A live `/chat` turn on 30 Oct took about 25 minutes between the tool result
and the reply, with no error logged - consistent with timed-out attempts being retried, but
not proven: nothing could show a retry, because the SDK logs retries at DEBUG and `main.py`
pins the `openai` logger at WARNING.

Now:

    OPENAI_TIMEOUT_S     120 s per attempt. A slow answer fails in minutes, not half an hour.
    OPENAI_MAX_RETRIES   1. A transient error (connection, 429, 5xx, timeout) gets one more
                         try; a provider that is down is reported after two attempts.

A caller with a known-slow call passes its own timeout and says why (the peer drafter).

Each retry is logged at WARNING on `carbon_r.llm`, read from the `x-stainless-retry-count`
header the SDK puts on every attempt ("0" first, "1" on the first retry). The hook sees the
request, not the failure that caused it, so the line says a retry happened and to what, and
the preceding failure is whatever the caller then reports.
"""

from __future__ import annotations

import logging

from openai import DefaultHttpxClient, OpenAI

from carbon_r.config import get_settings

OPENAI_TIMEOUT_S = 120.0
OPENAI_MAX_RETRIES = 1

log = logging.getLogger("carbon_r.llm")


def log_retry(request, *, timeout_s: float, max_retries: int) -> None:
    """Request hook: a WARNING for any attempt after the first."""
    attempt = request.headers.get("x-stainless-retry-count", "0")
    if attempt != "0":
        log.warning(
            "openai retry %s of %s: %s %s (timeout %.0f s per attempt)",
            attempt,
            max_retries,
            request.method,
            request.url.path,
            timeout_s,
        )


def openai_client(
    *, timeout_s: float = OPENAI_TIMEOUT_S, max_retries: int = OPENAI_MAX_RETRIES
) -> OpenAI:
    """An OpenAI client with an explicit timeout and retry budget, logging every retry."""

    def hook(request) -> None:
        log_retry(request, timeout_s=timeout_s, max_retries=max_retries)

    return OpenAI(
        api_key=get_settings().openai_api_key,
        timeout=timeout_s,
        max_retries=max_retries,
        http_client=DefaultHttpxClient(timeout=timeout_s, event_hooks={"request": [hook]}),
    )


__all__ = ["OPENAI_MAX_RETRIES", "OPENAI_TIMEOUT_S", "log_retry", "openai_client"]
