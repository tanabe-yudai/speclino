# SpecLino

仕様（Markdown）とコードを共通語彙の中間表現（Spec IR / Code IR）へ変換し、決定的な検査器（Z3）で「どの要求が、どの条件で、どう違反されるか」を反例つきで出す。エージェント（ADK + Gemini）は証拠収集と人への確認を担い、判定はしない。

## 正本となる文書（実装前に読む）

- `design/2026-09-22-semantic-conformance-poc-v0.1.md` — 意味検証コアの規範（IR、Python 部分集合、検査アルゴリズム、判定規則、Golden fixtures、モジュール分割）
- `design/2026-09-22-hackathon-plan.md` — ハッカソン向けの取捨選択、判定モデル（verdict と gate の分離、フェイルクローズ）、アーキテクチャ、バックログ
- `design/product-spec-summary.md` — 製品全体の要件（規約、ゲート、エージェント権限、NFR）

## タスク管理（必ず守る）

進捗は GitHub Project「SpecLino」（https://github.com/users/tanabe-yudai/projects/3）で、ストーリー = Issue（`story` ラベル、`Sxx:` 接頭辞）単位に管理する。各 Issue は 概要 / 完了条件 / 優先度 / 依存関係 / サブタスク / 成果 を持つ。

- **セッション開始時**: `scripts/story.sh board` で In Progress / Blocked を確認し、`scripts/story.sh next` で着手可能なストーリー（依存が全て Done）を優先度順に見る。
- **着手時**: `scripts/story.sh start <issue>` → Status=In Progress。着手コメントに計画の要点を書く（`gh issue comment`）。
- **完了時**: `scripts/story.sh done <issue> -r <成果ファイル>` → 本文「## 成果」に追記、Status=Done、クローズ。成果には 実装内容 / 確認方法と結果 / コミット・PR / 判断の記録 / 残課題 を書く。
- **停止時**: `scripts/story.sh block <issue> "<理由>"`。解消したら `start` で戻す。
- **選択**: 優先度（P0 → P1 → P2）と依存関係から動的に選ぶ。独立したストーリーは並列に進めてよい（サブエージェントは worktree で分離、1 ストーリー 1 エージェント）。
- **サブタスク**: Issue 内のチェックリストで管理。並列委譲や別担当が必要なものだけ sub-issue に切り出し、親の「依存関係」に列挙する。
- コミットメッセージに `(#issue)` を付ける。

## コマンド

- `make setup` / `make lint`（ruff）/ `make typecheck`（mypy）/ `make test` / `make eval` / `make check`（lint+typecheck+test）/ `make format`
- CI のジョブは**検査の段階**で分ける（lint / typecheck / test / eval / self-check / web の lint・typecheck・build / secrets）。言語でまとめない
- `make db-up && make db-migrate`（ローカル PostgreSQL）
- `uv run speclino check --repo . --base main --head HEAD --out build/review`（`--code-ir <file>` で手書き Code IR を直接入力）
- `make dev`（FastAPI、`SPECLINO_ROLE=all`）、`cd web && npm run dev`

## 構成と規約

- `src/speclino/domain/` — 型定義のみ（pydantic / enum）。ロジックを書かない。
- `src/speclino/conformance/`（検査器・ゲート）と `src/speclino/analysis/`（path_analyzer）は **LLM・adapters に依存しない**。`google.*` / `agent` / `adapters` の import はテストで禁止。手書き IR だけで単体試験できること。
- ビジネスロジックは `services/` `conformance/` `analysis/` に集約し、`api/` と `web/` に置かない。
- Python 3.12、`uv`、ruff、mypy strict。TypeScript は `web/src/types/` に型を分離。
- `schemas/*.schema.json` は `domain/` の pydantic モデルから生成する（手で編集しない）。
- 静的解析で対象リポジトリのモジュールを import しない。IR の式を `eval` しない。
- 判定は 4 値（VERIFIED_IN_SCOPE / VIOLATED / UNKNOWN / REVIEW）を正直に保つ。ゲートは必須ルールに UNKNOWN / REVIEW が残れば失敗（フェイルクローズ）。解析できないものを違反と表示しない。
- Conventional Commits。**コミット・プッシュはユーザー承認後**。

## 確認が必要な操作

GCP プロジェクト作成・課金紐付け・`terraform apply`・GitHub App 作成・ブランチ保護の変更・リポジトリ公開・コミット/プッシュは、実行前にユーザーへ確認する。

## 日程

締切 2026-10-15 23:59。M0 9/22–23、M1 〜9/27、M2 〜10/1、M3 〜10/5（Must 完了）、M4 〜10/9（Should、10/9 夜に切捨て）、10/13 コードフリーズ、10/14 提出。12/1 までデプロイ維持。
