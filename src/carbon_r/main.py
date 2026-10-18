"""FastAPI application: /health, /upload, /chat.

/chat is a thin shell on purpose. It resolves the session, tells the agent which
documents exist, runs one turn of the loop and returns the result with its tool
transcript and figure audit attached. Everything interesting - the tool selection, the
handles, the arithmetic, the audit - lives in `carbon_r.agent`, which has no HTTP in it
and is tested without a server.
"""

from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from carbon_r import __version__
from carbon_r.agent.loop import run_agent
from carbon_r.agent.session import Session, get_or_create_session, get_session
from carbon_r.api.models import ChatRequest, ChatResponse, UploadResponse
from carbon_r.api.uploads import UploadRejected, save_upload
from carbon_r.config import get_settings
from carbon_r.db import check_connection

app = FastAPI(
    title="Carbon R",
    version=__version__,
    summary="Cited, deterministic GHG accounting from uploaded documents",
)


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
    run = run_agent(session, _with_document_context(request.message, session, document_ids))
    return ChatResponse.from_run(run)


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
