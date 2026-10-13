# はじめる

ローカルでの起動と、CLI による 1 件の検査。

## クイックスタート（ローカル）

必要なもの: Python 3.12、[uv](https://docs.astral.sh/uv/)、Node.js 20+、Docker（DB とサンドボックス用）。

```sh
make setup          # uv sync + npm ci
make db-up          # ローカル PostgreSQL（docker compose）
make db-migrate     # alembic upgrade head
make dev            # FastAPI（http://localhost:8080、SPECLINO_ROLE=all）
cd web && npm run dev   # 画面（Vite。/api は :8080 に転送）
```

ローカルの既定は開発用の認証（`SPECLINO_AUTH_KIND=dev`、`SPECLINO_ENV=local` のときだけ有効）。
ログイン画面の「開発用ログイン」で、利用者 `judge` として入る。ワークスペースは CLI で作る:

```sh
uv run speclino workspace create <slug> --owner-uid judge --name "<表示名>"
uv run speclino policy seed --team <slug>   # 既存の規約パッケージを最初の改訂として取り込む（任意）
```

リポジトリ（プロジェクト）の登録は GitHub App の接続が要る（[guides/github-app-setup.md](guides/github-app-setup.md)）。
UML の仕様を書いて違反する PR を検査するまでの通しの手順は
[docs/demo-knowledge-registry.md](demo-knowledge-registry.md)、題材は [examples/shop/](../examples/shop/)。

```sh
make test           # 単体 + 統合（統合は SPECLINO_DATABASE_URL が要る）
make selfcheck      # SpecLino を自分自身に当てる（[自己検証](operations.md#自己検証)を参照）
```

## 1 件の検査を実行する（CLI、DB なし）

CLI は DB も GitHub App も無しで動く（自己検証と、GCP を畳んだ後の手元の確認のため）。仕様の入力は 2 通り:
リポジトリの `specs/`（旧形式の Spec IR）と、`--specs-dir`（sync で書き出した UML の仕様。[自己検証](operations.md#自己検証)）。
まず同梱の題材（`examples/orders/`：注文キャンセル、旧形式の要求 9 件）を検査する。

```sh
uv run speclino check --repo . --project examples/orders --head WORKTREE --out build/review
```

```text
VERIFIED_IN_SCOPE: 9
VIOLATED: 0
UNKNOWN: 0
REVIEW: 0
gate: pass (必須ルールはすべて成立)
```

`build/review/` に `run-manifest.json`（入力のハッシュ・版・上限）、`spec-ir.json`、`code-ir.json`、
`findings.json`、`gate.json`、`review.md` が出る。`run_id` と実行時刻を除けば同じ入力から同じ結果になる
（`speclino replay build/review` が凍結した IR から再評価し、保存された結果と一致するか確かめる）。

違反を作って試す: `examples/orders/app/service.py` の `inventory.restore(...)` を `if user.is_admin:` の中に
移して同じコマンドを実行すると、`R-CANCEL-04` が VIOLATED になり、反例（非管理者の所有者・PENDING）が
`review.md` に出る。`--base main` を足すと、この変更が**新規に持ち込んだ**違反かどうかも分かる。

主なオプション: `--fail-on VIOLATED --fail-on UNKNOWN --fail-on REVIEW --blocking`（必須運用）、
`--depth` / `--max-paths` / `--solver-timeout-ms`（解析の上限）、`--code-ir <file>`（手書き Code IR を入力）。
終了コードは 0（ゲート条件に該当しない）/ 1（該当、`--blocking`）/ 2（入力不正・実行不能）。
