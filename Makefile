.PHONY: setup test lint format dev db-up db-migrate eval openapi web-build schemas

setup:
	uv sync --all-groups
	cd web && npm ci

test:
	uv run pytest -m "not live"

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy src

format:
	uv run ruff format .
	uv run ruff check --fix .

dev:
	uv run uvicorn speclino.api.app:app --reload --port 8080

db-up:
	docker compose up -d db

db-migrate:
	uv run alembic upgrade head

eval:
	@echo "TODO(S09)"

openapi:
	@echo "TODO"

web-build:
	cd web && npm run build

schemas:
	uv run speclino schemas --out schemas
