# Carbon R: the API, the page and the loaders, in one image.
#
# System packages are the two things Python cannot install for itself:
#   poppler-utils           pdftoppm, which rasterises a PDF page for the vision model
#   pango / harfbuzz-subset WeasyPrint's renderer, for synthgen's documents
#   fonts-dejavu-core       one font family, so rendered pages have glyphs
#
# No key, no .env and no third-party document is copied in (.dockerignore). The GHG
# Protocol corpus is mounted at run time if the user has downloaded it; see README.

FROM ghcr.io/astral-sh/uv:0.12.18 AS uv

FROM python:3.13-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        poppler-utils libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /usr/local/bin/uv

ENV UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Dependencies first, in their own layer: a source change does not reinstall them.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project

COPY src ./src
COPY scripts ./scripts
COPY db ./db
COPY data/evals ./data/evals
COPY data/peer_sources.json ./data/peer_sources.json
RUN uv sync --locked --no-dev

# Non-root. The uploads directory is created here, owned by that user, so a named volume
# mounted on it inherits the ownership and /upload can write.
RUN useradd --create-home --uid 10001 carbon \
    && mkdir -p /app/data/uploads /app/data/generated /app/data/raw \
    && chown -R carbon:carbon /app/data
USER carbon

EXPOSE 8000

# The slim image has no curl; Python is already here.
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=6 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=4)" || exit 1

ENTRYPOINT ["sh", "scripts/docker_entrypoint.sh"]
