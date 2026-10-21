"""Settings, read from the environment.

Deliberately tolerant of missing configuration: a partly configured checkout should boot
and report what it is missing (see /health) rather than crash on import. That keeps the
setup path debuggable for someone who has cloned the repo and not yet filled in .env.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = ""

    openai_api_key: str = ""
    # No default. The model id must come from the account that will be billed for it;
    # `scripts/list_models.py` prints the real list. See CLAUDE.md on unverified values.
    openai_model: str = ""
    # Same rule: no default. Embeddings from different models are not comparable, so this
    # value is stored on every chunk and every search filters on it (tools/methodology.py).
    openai_embedding_model: str = ""

    # Singapore GST, as a fraction. Verified from IRAS's own e-Tax guide (9% effective
    # 1 Jan 2024) — see SOURCES.md §4. Config rather than a literal so the generator and
    # the extraction sanity check cannot drift apart; re-check if IRAS ever changes it.
    gst_rate: float = 0.09

    @property
    def database_configured(self) -> bool:
        """True only for a usable URL — the .env template ships a <PASSWORD> placeholder."""
        return bool(self.database_url) and "<PASSWORD>" not in self.database_url

    @property
    def llm_configured(self) -> bool:
        return bool(self.openai_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
