# uv run の暗黙 sync を止める（macOS 26 で z3-solver が毎回再インストールされ、並列実行がロック待ちになる）
export UV_NO_SYNC := 1

.PHONY: setup test lint typecheck check format dev db-up db-migrate eval openapi web-build schemas sandbox-image diagrams selfcheck

setup:
	UV_NO_SYNC=0 uv sync --all-groups
	cd web && npm ci

# 振る舞いと検出能力のテスト。デモブランチ（SpecLino 自身の実装・仕様をわざと壊す）でも
# 緑であるべきなので、「今のリポジトリが適合していること」を前提にする selfcheck は除く。
test:
	uv run pytest -m "not live and not selfcheck"

# SpecLino を自分自身に当てた結果。デモブランチでは落ちるのが主役（CI の self-check ジョブと同じ主張）。
# pytest 側（selfcheck マーカー）と CLI 側（specs/ir を使った自己検査）の両方を実行する。
# 片方が落ちても もう片方を必ず動かし、終了コードは最後にまとめる。デモブランチでは凍結 fixture の
# ドリフト検査が必ず落ちるが、見せたいのは CLI が出す違反と反例なので、そこで止まっては困る。
selfcheck:
	@rc=0; \
	uv run pytest -m selfcheck || rc=$$?; \
	uv run speclino check --repo . --head WORKTREE --out build/self-check \
		--fail-on VIOLATED --fail-on UNKNOWN --fail-on REVIEW --blocking || rc=$$?; \
	exit $$rc

# CI ではステージごとに分かれたジョブが lint / typecheck / test / self-check を個別に実行する。
# 手元でまとめて回すときは `make check`。
lint:
	uv run ruff check .
	uv run ruff format --check .

typecheck:
	uv run mypy src

check: lint typecheck test selfcheck

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
