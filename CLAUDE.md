# SpecLino

チームがコーディングエージェントに渡す知識（規約・スキル・指示書 = AGENTS.md / CLAUDE.md・仕様）を SpecLino の画面で改訂・承認し、MCP / A2A / `speclino sync` で各自のエージェントに配り、守られていることをゲートで保証し、観測できるようにする。仕様は人が読める Markdown と厳格な UML（Mermaid の部分集合）で書き、決定的な parser で IR に変換し、決定的な検査器（Z3）で「どの要求が、どの条件で、どう違反されるか」を反例つきで出す。AI（ADK + Gemini）は執筆の提案・証拠収集・人への確認を担い、変換も判定もしない。

## 正本となる文書（実装前に読む）

- `design/2026-10-05-team-knowledge-registry.md` — 現在の位置づけ（知識レジストリ、bundle、UML → IR、配信、LLM 支援の規約、可観測性）。下の 3 つと食い違えばこちらが優先
- `design/2026-09-22-semantic-conformance-poc-v0.1.md` — 意味検証コアの規範（IR、Python 部分集合、検査アルゴリズム、判定規則、Golden fixtures、モジュール分割）
- `design/2026-09-22-hackathon-plan.md` — ハッカソン向けの取捨選択、判定モデル（verdict と gate の分離、フェイルクローズ）、アーキテクチャ、バックログ
- `design/product-spec-summary.md` — 製品全体の要件（規約、ゲート、エージェント権限、NFR）
- `src/speclino/spec/uml/grammar.md` — SpecLino が受け付ける Mermaid の部分集合（parser の正本）

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

- `make setup` / `make lint`（ruff）/ `make typecheck`（mypy）/ `make test` / `make selfcheck` / `make eval` / `make check`（lint+typecheck+test+selfcheck）/ `make format`
- CI のジョブは**検査の段階**で分ける（lint / typecheck / test / self-check / web の lint・typecheck・build / secrets）。言語でまとめない
- **`make test` と `make selfcheck` を混ぜない**。`test` は振る舞いと検出能力のテスト（デモブランチでも緑であるべき）、`selfcheck` は「今のリポジトリが自分の仕様に適合しているか」（デモブランチでは落ちるのが正しい）。後者は `@pytest.mark.selfcheck` を付け、`tests/fixtures/selfcheck/` の凍結コピーとの使い分けは同ディレクトリの README を読む
- `make db-up && make db-migrate`（ローカル PostgreSQL）
- `uv run speclino check --repo . --base main --head HEAD --specs-dir .speclino/specs --out build/review`（`--specs-dir` は sync で書き出した UML の仕様を読む。`--code-ir <file>` で手書き Code IR を直接入力）
- `make dev`（FastAPI、`SPECLINO_ROLE=all`）、`cd web && npm run dev`
- `uv run speclino import-specs --workspace <slug> --project-id <id> --author <uid> --repo <dir>`（Git の旧形式の仕様を下書きとして取り込む。承認は画面）
- `SPECLINO_SYNC_TOKEN=... uv run speclino sync --url <SpecLino> --repo . [--repo-id <id>]`（承認済みの知識を書き出し `.speclino/lock.json` を更新）
- `make schemas` / `make openapi` / `make diagrams`（`design/*.mmd` から png・svg）

## 構成と規約

- **正本の置き場所**: 規約（チーム）・スキル・指示書・仕様の正本は SpecLino（ワークスペース）。承認ごとに **bundle**（有効な改訂の集合、署名つきの連番）を作り、run・sync・replay は bundle 単位で固定する。リポジトリに残すのは `.speclino/bindings.yaml`（操作名とシンボルの対応。既定は名前の一致）・`contracts/`・`exceptions.yaml`・プロジェクト規約で、bundle の run は**信頼側（デフォルトブランチの先端）**から読む。PR がそれらを変えると必須の REVIEW `KNOWLEDGE-BINDINGS-001`（承認者が PR コメント `/speclino attest` で確定）
- **仕様の変換**: UML（クラス・シーケンス・状態遷移・アクティビティ）と旧形式（Spec IR v0.1）の 2 つの入口。`spec/uml/` は決定的・全か無か（読めないトークンはエラー）で、LLM・adapters に依存しない。文法は `spec/uml/grammar.md` を正本にし、構文を足すときは先に文法を直す
- **規約の検査**: 決定的な検査器（`api.*`・`naming.identifiers`・`error_message.format`・`lint.preset` ほか）と、LLM 支援（`tier: llm_assisted`、検査器 `llm.checklist`）。LLM 支援の verdict は常に REVIEW（区分は `assessment_outcome`）、人の確定は `attestations` に記録し verdict を書き換えない
- **配信と鮮度**: `speclino sync`・MCP（`context_get` / `spec_get` / `skill_get` / `sync_status` / `spec_suggest`）・A2A。`lock.json` の署名と書き出したファイルを `KNOWLEDGE-LOCK-001` で検査する。取得は `knowledge_fetches` に残し、可観測性の画面（`/w/:slug/observability`）は 1 つの集計（`services/observability.py`）から作る
- **コードの規約の正本は SpecLino のプロジェクト規約**（`.speclino/policy/speclino-core.yaml`）。実装前に読む。MCP の `context_get` でも、チーム規約と合わせて読める。主なもの:
  - `ARCH-001` domain/ は型だけ／`ARCH-002` conformance/・analysis/ は LLM・adapters に依存しない／`ARCH-003` ロジックを api/・web/ に置かない／`ARCH-004` 対象を import・eval しない／`VERDICT-001` 判定 4 値とフェイルクローズ
  - 機械検証つき: `MAKE-001`・`SPEC-FMT-001`・`SPEC-TEST-001`・`CI-001`・`API-ERR-001`
  - 規約を変えるときは、このファイルを PR で変える（ここに規約を書き足さない）
- 間に合わない・解析できない部分は UNKNOWN にするだけで、偽の VERIFIED_IN_SCOPE を出さない（範囲を広げても守る不変条件）
- Python 3.12、`uv`、ruff、mypy strict。TypeScript の型は `web/src/types/`。画面の用語は `web/src/services/labels.ts`・`knowledge.ts` にそろえる
- Conventional Commits。**コミット・プッシュはユーザー承認後**。

## 確認が必要な操作

GCP プロジェクト作成・課金紐付け・`terraform apply`・GitHub App 作成・ブランチ保護の変更・リポジトリ公開・コミット/プッシュは、実行前にユーザーへ確認する。

## 日程

締切 2026-10-15 23:59。M0 9/22–23、M1 〜9/27、M2 〜10/1、M3 〜10/5（Must 完了）、M4 〜10/9（Should、10/9 夜に切捨て）、10/13 コードフリーズ、10/14 提出。12/1 までデプロイ維持。

<!-- speclino:begin bundle=6 -->
@AGENTS.md
<!-- speclino:end -->
