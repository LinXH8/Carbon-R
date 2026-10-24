"""FastAPI application: the page, /health, /upload, /chat, /chat/stream.

/chat is a thin shell on purpose. It resolves the session, tells the agent which
documents exist, runs one turn of the loop and returns the result with its tool
transcript and figure audit attached. Everything interesting - the tool selection, the
handles, the arithmetic, the audit - lives in `carbon_r.agent`, which has no HTTP in it
and is tested without a server.
"""

import logging
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from carbon_r import __version__
from carbon_r.agent.loop import run_agent
from carbon_r.agent.session import Session, get_or_create_session, get_session
from carbon_r.api.models import ChatRequest, ChatResponse, UploadResponse
from carbon_r.api.stream import SSE_HEADERS, SSE_MEDIA_TYPE, stream_chat
from carbon_r.api.uploads import UploadRejected, save_upload
from carbon_r.config import get_settings
from carbon_r.db import check_connection

# Carbon R's own loggers at INFO (the per-tool `run_id=` lines), and nothing else: the root
# logger is left alone, so libraries keep their defaults. The HTTP clients are pinned at
# WARNING explicitly, because an earlier root-level basicConfig made them log every provider
# request. uvicorn keeps its own configuration.
HTTP_CLIENT_LOGGERS = ("httpx", "httpx2", "httpcore", "openai")


def _configure_logging() -> None:
    ours = logging.getLogger("carbon_r")
    if not ours.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        ours.addHandler(handler)
    ours.setLevel(logging.INFO)
    ours.propagate = False
    for name in HTTP_CLIENT_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


_configure_logging()

app = FastAPI(
    title="Carbon R",
    version=__version__,
    summary="Cited, deterministic GHG accounting from uploaded documents",
)

STATIC_DIR = Path(__file__).parent / "static"

# Mounted at /static rather than at /, which would shadow every route declared after it.
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def landing() -> FileResponse:
    """The landing page: what Carbon R does, and the way in. Decorative 3D, no figures."""
    return FileResponse(STATIC_DIR / "landing.html")


@app.get("/app", include_in_schema=False)
def index() -> FileResponse:
    """The single-page UI. No build step: the files under static/ are served as written."""
    return FileResponse(STATIC_DIR / "index.html")


class HealthResponse(BaseModel):
    """Reports configuration state so a half-configured checkout explains itself."""

    status: str = Field(description="'ok' when the app is serving")
    version: str
    db_connected: bool = Field(description="DATABASE_URL is set and the database answers")
    llm_configured: bool = Field(description="A model provider API key is present")
    model: str | None = Field(
        default=None, description="Configured model id, or null if not yet chosen"
    )


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        status="ok",
        version=__version__,
        db_connected=check_connection(),
        llm_configured=settings.llm_configured,
        model=settings.openai_model or None,
    )


@app.post("/upload", response_model=UploadResponse)
async def upload(
    file: Annotated[UploadFile, File(description="An electricity bill: PDF, PNG or JPEG")],
    session_id: Annotated[str | None, Form()] = None,
) -> UploadResponse:
    """Store a document and return the id the agent will address it by."""
    session = _require_session(session_id) if session_id else get_or_create_session(None)[0]

    try:
        document = save_upload(
            session,
            filename=file.filename or "",
            data=await file.read(),
            content_type=file.content_type,
        )
    except UploadRejected as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc

    return UploadResponse(
        session_id=session.session_id,
        document_id=document.document_id,
        filename=document.filename,
        content_type=document.content_type,
        size_bytes=document.size_bytes,
    )


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    """One turn of the agent loop."""
    session, message = _prepare_turn(request)
    return ChatResponse.from_run(run_agent(session, message))


@app.post("/chat/stream", include_in_schema=False)
def chat_stream(request: ChatRequest) -> StreamingResponse:
    """The same turn, streamed: each tool result as it lands, then one `final` event.

    Not in the schema because the body and the terminal event are `/chat`'s contract; this
    endpoint only changes when the client learns about them. Scripts and tests use /chat.
    """
    session, message = _prepare_turn(request)
    return StreamingResponse(
        stream_chat(session, message),
        media_type=SSE_MEDIA_TYPE,
        headers=SSE_HEADERS,
    )


def _prepare_turn(request: ChatRequest) -> tuple[Session, str]:
    """Everything both chat routes must do before the agent runs.

    Shared so the streaming route cannot drift into accepting a request the plain route
    would have refused - an unknown document or a missing API key must fail the same way
    whichever route is called.
    """
    settings = get_settings()
    if not settings.llm_configured or not settings.openai_model:
        raise HTTPException(
            status_code=503,
            detail="OPENAI_API_KEY and OPENAI_MODEL are not configured; see .env.example.",
        )

    session = (
        _require_session(request.session_id)
        if request.session_id
        else get_or_create_session(None)[0]
    )

    unknown = [d for d in request.document_ids if d not in session.documents]
    if unknown:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown document(s) in this session: {', '.join(unknown)}.",
        )

    document_ids = request.document_ids or list(session.documents)
    return session, _with_document_context(request.message, session, document_ids)


def _require_session(session_id: str) -> Session:
    """A session id the server does not know is a 404, never a silent new session.

    A client that believes it is continuing a conversation and is not would get answers
    with no documents in them and no explanation of why.
    """
    session = get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"No session {session_id!r}.")
    return session


def _with_document_context(message: str, session: Session, document_ids: list[str]) -> str:
    """Tell the agent which documents it may read. It cannot discover them otherwise.

    Appended to the user's message rather than baked into the system prompt, because the
    set changes with every upload while the prompt is constant - and a constant prompt is
    what keeps a provider-side cache useful.
    """
    if not document_ids:
        return message + "\n\n[No documents have been uploaded in this session.]"
    listed = "\n".join(
        f"- {document_id} ({session.documents[document_id].filename})"
        for document_id in document_ids
    )
    return f"{message}\n\n[Documents available in this session:\n{listed}\n]"
