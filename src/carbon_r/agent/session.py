"""Per-conversation state: the documents, the workspace of tool results, the transcript.

The workspace is the reason the model never has to carry a number. A tool result is kept
here under a handle (`factor_1`, `calc_1`), and a later tool call refers to the handle;
the values travel in Python, inside this process, never through the model's output.

Storage is IN-MEMORY and therefore Tier 1 only: a restart loses every session, and two
workers would not share one. That is a deliberate scope cut, not an oversight - sessions
that survive a restart need a table and a retention policy, which is a Tier 3 deployment
concern. The interface here (`get_session`, `create_session`) is the seam where that
would be swapped.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path

from carbon_r.tools.base import ToolResult


@dataclass(frozen=True)
class StoredDocument:
    """One uploaded file. `path` never leaves the process; the model sees `document_id`."""

    document_id: str
    filename: str
    content_type: str
    size_bytes: int
    path: Path


@dataclass
class Session:
    """One conversation, with everything the agent has learned in it."""

    session_id: str
    documents: dict[str, StoredDocument] = field(default_factory=dict)

    # Workspace. Keyed by handle for activities, factors and calculations, by document_id
    # for readings - a document has exactly one current reading, and re-reading it would
    # cost another vision call for the same page.
    readings: dict[str, dict] = field(default_factory=dict)
    # One entry per measurable quantity found on a document, `act_1`, `act_2`, ... A
    # fuel-card statement contributes two (diesel and petrol); an electricity bill one.
    # Keyed by handle rather than nested under the document so a calculation can name a
    # single activity without the model having to describe which one it means.
    activities: dict[str, dict] = field(default_factory=dict)
    factors: dict[str, dict] = field(default_factory=dict)
    calculations: dict[str, dict] = field(default_factory=dict)

    # Every tool result produced in this session, in order. The audit checks the model's
    # reply against the numbers in here, so it must record EVERY result, including the
    # ones the model ignored.
    tool_payloads: list[dict] = field(default_factory=list)

    # Provider conversation items, so a follow-up question keeps its context.
    history: list[dict] = field(default_factory=list)

    # What the user typed, turn by turn, without the document context the server appends.
    # The quote audit accepts a reply quoting these words; it must not accept the server's.
    user_messages: list[str] = field(default_factory=list)

    _counters: dict[str, int] = field(default_factory=dict)

    def add_document(self, document: StoredDocument) -> None:
        self.documents[document.document_id] = document

    def next_handle(self, kind: str) -> str:
        """`factor_1`, `factor_2`, ... Stable and readable, so a transcript is followable."""
        self._counters[kind] = self._counters.get(kind, 0) + 1
        return f"{kind}_{self._counters[kind]}"

    def record(self, tool: str, result: ToolResult) -> None:
        """Keep a tool result for the figure audit, whatever its status."""
        self.tool_payloads.append(
            {"tool": tool, "status": result.status, "message": result.message, "data": result.data}
        )

    def known_document(self, document_id: str) -> StoredDocument | None:
        return self.documents.get(document_id)


_SESSIONS: dict[str, Session] = {}


def create_session() -> Session:
    session = Session(session_id=f"sess_{uuid.uuid4().hex[:12]}")
    _SESSIONS[session.session_id] = session
    return session


def get_session(session_id: str) -> Session | None:
    return _SESSIONS.get(session_id)


def get_or_create_session(session_id: str | None) -> tuple[Session, bool]:
    """Returns the session and whether it already existed.

    An unknown id is reported to the caller rather than silently replaced: a client that
    believes it is continuing a conversation, and is not, would get answers with no
    documents in them and no explanation.
    """
    if session_id is None:
        return create_session(), False
    existing = get_session(session_id)
    if existing is None:
        return create_session(), False
    return existing, True


def reset_sessions() -> None:
    """Drop every session. For tests, and for a local restart with no state to keep."""
    _SESSIONS.clear()


def session_count() -> int:
    return len(_SESSIONS)
