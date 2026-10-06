# S73 対話による知識の作成 Implementation Plan

**Goal:** 仕様・スキル・指示書・チーム規約を、画面の上で AI と会話しながら下書きできるようにする。会話は本人だけが読め、保存する改訂には本人が確かめた会話の要約を `note` として残せる。

**Architecture:** `domain/knowledge_chat.py`（型だけ）→ `db/repositories/knowledge_chats.py`（`workspace` と `owner_uid` で必ず絞る）→ `services/knowledge_chat.py`（会話の CRUD、発言の 3 段の処理、提案の許可リストと診断、要約、費用の予約）→ `api/routes/knowledge_chat.py`（固定のエラーコードだけを返す）→ web の `ChatPanel`（`KnowledgeEditor` とチーム規約の画面）。LLM は既存の `TextLlm`（Gemini の JSON）。テストは台本の `TextLlm`。

**Tech Stack:** Python 3.12 / SQLAlchemy async / PostgreSQL / Alembic / FastAPI / React + TS / vitest / Playwright。

設計の根拠: `design/2026-10-06-knowledge-chat.md`（C1–C9 は拘束）、`design/2026-10-05-team-knowledge-registry.md`（D5: AI は本文の提案だけ）。

## 決めたこと（設計の細部）

| 項目 | 決定 |
| --- | --- |
| 編集欄の本文（提案の土台） | 種類ごとの文字列: 仕様 = `markdown`、指示書 = `body`、スキル = `{"skill_md","files"}` の正規 JSON、規約 = 編集中のルールの正規 JSON。サーバは受け取った文字列の SHA-256（`sha256:...`）を `base_hash` として提案に結び付ける。画面は同じ直列化で今の本文のハッシュを計算し、違えば「エディタに反映」を無効にする |
| 提案の形（C4） | 仕様 `{markdown}`、スキル `{skill_md, files}`、指示書 `{body}`、規約 `{rule: {id,title,text,level,tier,verifier{id,version,params},applies_to{paths,languages}}}`。それ以外の欄は捨てる（件数だけ診断に出す） |
| 診断（C5） | 仕様は `compile_spec`（`to_thread`、結び付いた artifact の今の `domain` / `legacy_ir` をサーバが DB から読む）。スキル・指示書は `SkillContent` / `InstructionsContent`（scope は仮の値）。規約は編集中のルールに提案を重ね（無い必須欄は `version="1"`・`on_missing_input="unknown"`）、`PolicyRule` と検査器の一覧（`validate_package`）で確かめる |
| 費用（C7） | `cost_ledger` に `workspace`・`uid`・`purpose`（NULL 可）を足す。`ChatBudget.reserve` は既存と同じ advisory lock の中で、全体枠（＋パイプラインの余裕）・本人の日次の費用（全体の 20%）・ワークスペースの日次の費用（全体の 50%）・本人の日次の発言数（`purpose='chat_message'` の予約の行が 50 未満）を数える。最悪の額 = 入力のバイト数 × 入力単価 ＋ 4,096 × 出力単価。会話用の LLM は `max_output_tokens=4096` |
| 入力の大きさ | 1 回のプロンプトは 64KB（UTF-8）まで。規則・編集欄の本文（64KB まで）・新しい発言（8KB まで）・知識の名前（4KB まで）は必須、次に文法（仕様だけ、入れば）、残りに会話を新しい順で 48KB まで。必須部分だけで 64KB を超えれば 413 |
| 会話の上限 | 1 会話 200 件・512KB。発言の前に「今の大きさ＋発言＋返答の上限 64KB」で確かめ、超えるなら 409 `chat_full` |
| 冪等（C8） | 同じ `idempotency_key` の発言が done / failed なら LLM を呼ばずに前の結果（200）を返す。pending なら 409 `message_pending`。確定のときは行を `FOR UPDATE` で読み直し、持ち主・保留の発言が残っていることを確かめる（消えていれば応答を捨て 404）。版は確定でも進める |
| 失敗 | 予約の拒否・LLM の失敗・応答の形式の不正は、保留の発言を failed にして固定のコードで返す（429 / 502）。ログには例外の型名だけ |
| エラー（C9） | 会話の API は body を自前で読み（64KB＋α で打ち切り）、検証の失敗は 422 `invalid_request` と固定の文だけ。全体の `RequestValidationError` のハンドラも `/knowledge/chats` の経路では固定の応答にする |
| 監査（C9） | `record_chat_event(session, actor, action, workspace, chat_id, *, outcome, message_count)` だけを使う（`knowledge.chat.create` / `.delete` / `.message` / `.purge`）。本文・題・提案は入れない |
| note | 知識の作成・下書きの保存に `note`（500 文字）と `note_source`（`manual` / `chat_summary`）。note は下書きと同じ照合（expected_revision / expected_hash）の中で保存する。監査には `note_source` だけ（本人の申告）。規約は既存の `note` に同じ `note_source` を足す |
| メンバーの削除 | `remove_member` が同じトランザクションでその人の会話を消し、件数だけ監査に残す。ワークスペースの削除は FK の `ON DELETE CASCADE` |
| E2E の偽の LLM | `SPECLINO_CHAT_LLM=scripted` で台本の会話 LLM（`adapters/llm/chat_scripted.py`）を使う。`env=prod` では設定の検証で拒否する |

## Task 1: 表とモデル

- [ ] 失敗するテスト: `tests/unit/db/test_models.py` に `knowledge_chats` の列・型・CHECK・索引・FK（`ON DELETE` の挙動）と `cost_ledger` の新しい列
- [ ] `domain/knowledge_chat.py` に `KnowledgeChatKind` などの型、`db/models.py` に `KnowledgeChatRecord`、`CostLedgerEntry` に 3 列
- [ ] `migrations/versions/0012_knowledge_chats.py`（追加だけ）。`alembic upgrade head` → `downgrade 0011` → `upgrade head`、`alembic check` → commit

## Task 2: リポジトリと持ち主の境界

- [ ] 失敗する統合テスト: 作成・一覧（自分のだけ、kind / artifact で絞る）・取得・削除・題の変更（版の照合）・結び付け。他人（同じワークスペースの owner を含む）と別のワークスペースは None
- [ ] `db/repositories/knowledge_chats.py`（すべての問い合わせが `workspace` と `owner_uid` を条件に持つ）→ commit

## Task 3: 会話の CRUD のサービスと監査

- [ ] 失敗するテスト: 結び付けの検査（artifact のワークスペース・種類、規約は結び付けない）、監査の行に本文が無い、メンバーの削除で会話が消える
- [ ] `services/knowledge_chat.py`（create / list / get / delete / patch）、`record_chat_event`、`members.remove_member` の後片付け → commit

## Task 4: 提案の許可リストと診断（純粋関数）

- [ ] 失敗する単体テスト: 種類ごとの許可リスト（名前・scope・target・domain・legacy_ir・compiled を落とす）、型に合わない提案も `raw_proposal` と診断で返す、規約の未登録の検査器・版の違い、`base_hash`
- [ ] 実装 → commit

## Task 5: 費用の予約（ChatBudget）

- [ ] 失敗する統合テスト: 全体枠・本人の 20%・ワークスペースの 50%・本人の 50 発言、精算で予約が実費になる、別の人・別のワークスペースは数えない
- [ ] `CostLedgerRepository` に絞り込みの集計、`ChatBudget` → commit

## Task 6: 発言の処理（C8）と要約

- [ ] 失敗する統合テスト: 返答と提案が保存される、同じ冪等キーは LLM を呼ばない、pending は 409、LLM の途中で会話を消すと応答を捨てて行が戻らない、LLM の失敗・予約の拒否で精算と failed、入力の上限、会話の上限、プロンプトに他人の会話・他のワークスペースの名前が入らない、要約は保存しない
- [ ] 実装（短いトランザクション → ロックなしの LLM → 確かめて確定）→ commit

## Task 7: REST とエラー（C9）

- [ ] 失敗する統合テスト: 6 本の API、メンバー以外・別のワークスペースは 404、他人の会話は owner でも 404、検証の失敗・LLM の失敗・予約の拒否の応答に入力・LLM の生の応答・プロンプト・例外の文が出ない、`caplog` にも出ない
- [ ] `api/routes/knowledge_chat.py`、ルータの登録、検証エラーのハンドラ、設定 `chat_llm`、`openapi.json` の再生成 → commit

## Task 8: 改訂の note

- [ ] 失敗するテスト: 作成・保存で note が残る、500 文字を超えると 422、`note_source` は監査に残り本文は残らない、規約の `note_source`
- [ ] `knowledge.py` の API → `knowledge_registry` → `ArtifactRepository`、`policies.py` → commit

## Task 9: 画面

- [ ] `web/src/types/knowledgeChat.ts`、`web/src/api/knowledgeChat.ts`、`web/src/services/knowledgeChat.ts`（編集欄の本文、提案の反映、ハッシュ、要約の note、エラーの文言。vitest）、`web/src/hooks/useKnowledgeChat.ts`
- [ ] `components/knowledge/ChatPanel.tsx`（告知、自分の会話の一覧、会話、提案の差分、「エディタに反映」、削除）
- [ ] `KnowledgeEditor`（パネル、変更の説明の欄と「会話の要約を使う」、新規の保存の後に PATCH で結び付け）、一覧の「チャットで新規作成」、改訂の画面の「変更の説明」、チーム規約の画面のパネルとルールの編集欄への反映
- [ ] `npm run lint && npm run typecheck && npm run build && npx vitest run` → commit

## Task 10: 仕上げ

- [ ] Playwright: 偽の LLM で「チャットで新規作成 → 発言 → エディタに反映 → 要約を使って保存 → 改訂の画面に説明」
- [ ] `make lint` / `make typecheck` / `make test`（実 DB）/ `make selfcheck`、マイグレーションの往復
- [ ] 報告書
