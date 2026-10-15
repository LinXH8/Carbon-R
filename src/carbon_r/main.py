"""FastAPI application.

Tier 1 Block 1: /health only. /upload and /chat arrive in Block 4.
"""

from fastapi import FastAPI
from pydantic import BaseModel, Field

from carbon_r import __version__
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
