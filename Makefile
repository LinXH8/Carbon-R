# Every recipe runs against the project's named virtualenv, not a bare .venv.
export UV_PROJECT_ENVIRONMENT := .venv-carbon-r

.PHONY: help install run test lint fmt seed models db-up db-down check

help:
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | sed 's/:.*## /\t/' | expand -t20

install:  ## Create .venv-carbon-r and install all dependencies
	uv sync

run:      ## Serve the API on :8000 with reload
	uv run uvicorn carbon_r.main:app --reload

test:     ## Run the test suite
	uv run pytest -q

lint:     ## Check formatting and lint rules
	uv run ruff check .
	uv run ruff format --check .

fmt:      ## Apply formatting and autofixable lint rules
	uv run ruff format .
	uv run ruff check --fix .

check: lint test  ## Everything CI will run

seed:     ## Load emission factors into DATABASE_URL (idempotent)
	uv run python scripts/seed_factors.py

models:   ## List model ids available to OPENAI_API_KEY
	uv run python scripts/list_models.py

db-up:    ## Start local Postgres (only if not using a hosted database)
	docker compose up -d db

db-down:  ## Stop local Postgres
	docker compose down
