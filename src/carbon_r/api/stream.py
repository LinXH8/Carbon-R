"""Server-sent events for `/chat`, so the wait is legible rather than invented.

The agent takes fifteen to twenty-five seconds on an electricity bill: a vision call, a
SQL lookup, the calculation, then the answer. A spinner that claims nothing is honest but
unhelpful, and a skeleton that advances on a timer would be the interface inventing
progress - which is the one thing this project does not do anywhere else. So the real tool
results are pushed as they happen, and the client shows the audit trail being built.

`run_agent` is synchronous and stays that way: it is plain, testable, and the loop is the
most inspected code in the project. It is run here on a worker thread, with its
`on_event` callback pushing onto a queue that this generator drains. The generator itself
is synchronous, so FastAPI runs it in its threadpool and nothing blocks the event loop.

Every event is a JSON object with a `type`, and a run ends with exactly one `final` -
including when the provider fails, which arrives as a `final` carrying
`stopped_reason="provider_error"`. One terminal shape means the client has one parse path
and no branch in which it waits forever.
"""

from __future__ import annotations

import json
import queue
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor

from carbon_r.agent.loop import run_agent
from carbon_r.agent.session import Session
from carbon_r.api.models import ChatResponse

#: Pushed onto the queue when the worker is done, so the drain loop knows to stop.
_DONE = object()

SSE_MEDIA_TYPE = "text/event-stream"

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    # Without this an nginx in front of the app buffers the whole stream and delivers it
    # at the end, which looks exactly like the blocking request this replaces.
    "X-Accel-Buffering": "no",
}


def _frame(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


def stream_chat(session: Session, message: str) -> Iterator[str]:
    """Run one agent turn, framing its progress and then its result as SSE."""
    events: queue.Queue = queue.Queue()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(run_agent, session, message, on_event=events.put)
        future.add_done_callback(lambda _: events.put(_DONE))

        while True:
            event = events.get()
            if event is _DONE:
                break
            yield _frame(event)

        try:
            run = future.result()
        except Exception as exc:  # pragma: no cover - run_agent catches provider errors
            # Anything that reaches here is a bug rather than a provider outage, but the
            # client is mid-stream and must still be told the run is over.
            yield _frame({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
            return

    yield _frame({"type": "final", **ChatResponse.from_run(run).model_dump()})
