# uv run の暗黙 sync を止める（macOS 26 で z3-solver が毎回再インストールされ、並列実行がロック待ちになる）
export UV_NO_SYNC := 1

.PHONY: setup test lint typecheck check format dev db-up db-migrate eval openapi web-build schemas sandbox-image diagrams

setup:
	UV_NO_SYNC= uv sync --all-groups
	cd web && npm ci

test:
	uv run pytest -m "not live"

# CI ではステージごとに分かれたジョブが lint / typecheck / test を個別に実行する。
# 手元でまとめて回すときは `make check`。
lint:
	uv run ruff check .
	uv run ruff format --check .

typecheck:
	uv run mypy src

check: lint typecheck test

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
	uv run python -c "from speclino.api.app import create_app; from speclino.settings import Settings; import json; print(json.dumps(create_app(Settings(env='local', role='all')).openapi(), ensure_ascii=False, indent=2, sort_keys=True))" > openapi.json

web-build:
	cd web && npm run build

schemas:
	uv run speclino schemas --out schemas

sandbox-image:
	docker build -t speclino-sandbox-runner:latest docker/sandbox

# アーキテクチャ図（design/*.mmd が正本。README の mermaid ブロックと同じ内容）
diagrams:
	npx --yes @mermaid-js/mermaid-cli@11 -i design/architecture.mmd -o design/architecture.png -b white -s 2
	npx --yes @mermaid-js/mermaid-cli@11 -i design/architecture.mmd -o design/architecture.svg -b white
	npx --yes @mermaid-js/mermaid-cli@11 -i design/pipeline.mmd -o design/pipeline.png -b white -s 2
	npx --yes @mermaid-js/mermaid-cli@11 -i design/pipeline.mmd -o design/pipeline.svg -b white
