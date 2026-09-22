# uv run の暗黙 sync を止める（macOS 26 で z3-solver が毎回再インストールされ、並列実行がロック待ちになる）
export UV_NO_SYNC := 1

.PHONY: setup test lint format dev db-up db-migrate eval openapi web-build schemas sandbox-image

setup:
	UV_NO_SYNC= uv sync --all-groups
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
	uv run python -m speclino.eval.runner eval/golden/cancel_order --mode handwritten --out build/eval/handwritten.md
	uv run python -m speclino.eval.runner eval/golden/cancel_order --mode source --out build/eval/source.md

openapi:
	@echo "TODO"

web-build:
	cd web && npm run build

schemas:
	uv run speclino schemas --out schemas

sandbox-image:
	docker build -t speclino-sandbox-runner:latest docker/sandbox
