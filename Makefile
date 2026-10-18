# Every recipe runs against the project's named virtualenv, not a bare .venv.
export UV_PROJECT_ENVIRONMENT := .venv-carbon-r

# WeasyPrint loads pango/cairo/gdk-pixbuf through dlopen. On this machine they are present
# via Homebrew, but anaconda shadows /opt/homebrew/lib on the dyld search path, so the load
# fails with "cannot load library 'libpango-1.0-0'". Exported here rather than hacked into
# the code, so `make docs` works in a fresh shell. Harmless on Linux/Docker.
export DYLD_FALLBACK_LIBRARY_PATH := /opt/homebrew/lib

.PHONY: help install run test lint fmt seed docs models db-up db-down check eval agent-smoke flow-10

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

docs:     ## Generate synthetic documents + answer keys into data/generated/
	uv run python scripts/generate_docs.py

models:   ## List model ids available to OPENAI_API_KEY
	uv run python scripts/list_models.py

db-up:    ## Start local Postgres (only if not using a hosted database)
	docker compose up -d db

db-down:  ## Stop local Postgres
	docker compose down

eval:     ## Score extraction and the pipeline against the answer keys
	uv run python scripts/eval_extraction.py

agent-smoke:  ## End-to-end flow check of /upload + /chat on 5 bills (NOT an accuracy measurement)
	uv run python scripts/agent_flow_check.py

# Ten documents chosen to span both factor years, so the factor-year substitution
# disclosure is actually exercised. The first five bills all fall in 2024 and never hit it.
flow-10:  ## Flow check on 10 bills across both factor years (NOT an accuracy measurement)
	uv run python scripts/agent_flow_check.py --docs \
	  elec_001,elec_002,elec_003,elec_004,elec_005,elec_010,elec_013,elec_016,elec_017,elec_018
