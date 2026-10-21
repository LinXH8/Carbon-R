"""Text to vectors, through the configured embedding model and nothing else.

The model id is a setting with no default (`OPENAI_EMBEDDING_MODEL`), and it travels with
every vector: `embed_texts` returns the id it used, the ingest stores it on every chunk, and
every search filters on it. Vectors from two models are not comparable, and nothing here
would notice if they were mixed - so the id is never implicit.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from openai import OpenAI

from carbon_r.config import get_settings

# The width of `document_chunks.embedding`. A model returning anything else is refused
# rather than truncated or padded.
DIMENSIONS = 1536

# Inputs per request. Well inside the provider's per-request limits for 300-word chunks.
BATCH_SIZE = 100

Embedder = Callable[[Sequence[str]], list[list[float]]]


class EmbeddingNotConfiguredError(RuntimeError):
    """OPENAI_API_KEY or OPENAI_EMBEDDING_MODEL is unset."""


def configured_model() -> str:
    model = get_settings().openai_embedding_model
    if not model:
        raise EmbeddingNotConfiguredError(
            "OPENAI_EMBEDDING_MODEL is not set. See .env.example; chunks are searched only "
            "against the model that embedded them."
        )
    return model


def openai_embedder(model: str | None = None, usage: list[dict] | None = None) -> Embedder:
    """An `Embedder` bound to one model. `usage` receives the provider's token counts."""
    settings = get_settings()
    if not settings.openai_api_key:
        raise EmbeddingNotConfiguredError("OPENAI_API_KEY is not set.")
    model = model or configured_model()
    client = OpenAI(api_key=settings.openai_api_key)

    def embed(texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), BATCH_SIZE):
            batch = list(texts[start : start + BATCH_SIZE])
            response = client.embeddings.create(model=model, input=batch)
            if usage is not None and getattr(response, "usage", None) is not None:
                usage.append(response.usage.model_dump())
            ordered = sorted(response.data, key=lambda item: item.index)
            vectors.extend(item.embedding for item in ordered)
        check_dimensions(vectors, expected=len(texts))
        return vectors

    return embed


def check_dimensions(vectors: list[list[float]], *, expected: int) -> None:
    if len(vectors) != expected:
        raise RuntimeError(f"Asked for {expected} embeddings, received {len(vectors)}.")
    widths = {len(vector) for vector in vectors}
    if widths and widths != {DIMENSIONS}:
        raise RuntimeError(
            f"Embedding width {sorted(widths)} does not match the table's {DIMENSIONS}."
        )


def to_pgvector(vector: Sequence[float]) -> str:
    """pgvector's text input form. Passed as a parameter and cast with `::vector`."""
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"
