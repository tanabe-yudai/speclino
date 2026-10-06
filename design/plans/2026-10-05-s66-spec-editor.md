# S66 仕様エディタと承認の画面 — 実装計画

> 実装者向け: この計画をタスク順に実行する。各タスクの終わりにコミットする（`(#108)`）。

**Goal:** 4 種類の知識のうち spec / skill / instructions の下書きを画面で書き、仕様は決定的なプレビュー（診断・正規形・図・要求の追跡）を見ながら直し、前の改訂との差分を見て承認できるようにする。sync トークンを設定画面で管理できるようにする。

**Architecture:** バックエンドは `POST /api/workspaces/{slug}/specs/preview` を 1 本だけ足す（判断は `services/spec_preview.py`、S63 の `compile_spec` / `render` を呼ぶだけ。LLM なし）。画面は S62 の REST（artifacts / draft / approve / revisions / bundles/current）と S68 の sync-tokens を使う。ロジック（診断の並べ替え、行への移動の位置、要求の追跡、mermaid ブロックの切り出し、行の差分、SKILL.md の frontmatter 検査、競合の文言）は `web/src/services/` の純関数に置き、vitest で試験する。コンポーネントは表示とイベントだけ。

**Tech Stack:** FastAPI / pydantic / pytest（実 DB は `speclino-s66-db`、port 55466）。React 19 / react-router 7 / radix tabs / vitest（node 環境）/ oxlint / tsc。図の描画に `mermaid`（dynamic import で別チャンク）。

**正本:** `design/2026-10-05-team-knowledge-registry.md` 3.1–3.2・4 章・9 章、`src/speclino/spec/uml/grammar.md`、S62 / S63 / S68 の報告。

---

## 画面とパス

| パス | 画面 |
| --- | --- |
| `/w/:slug/knowledge` | `/w/:slug/knowledge/specs` へ移す |
| `/w/:slug/knowledge/:kind`（`specs` / `skills` / `instructions`） | 種類ごとの一覧（状態・有効な改訂・下書き・今の bundle の世代）。種類のタブと「規約」（既存の `/w/:slug/policies`）へのリンク |
| `/w/:slug/knowledge/:kind/new` | 新規作成（名前＋エディタ） |
| `/w/:slug/knowledge/:kind/:id` | エディタ（下書きがあれば下書き、無ければ有効な改訂を土台に編集） |
| `/w/:slug/knowledge/:kind/:id/revisions` | 改訂の一覧・差分・承認 |

上部バーに「知識」を足す（「規約」の隣）。設定画面に「sync トークン」タブ（全メンバーに一覧、発行・失効は owner / approver）。

## 判断の記録

- 承認は**保存済みの下書き**に対して行う（番号と content_hash を照合）。未保存の変更があれば承認ボタンは無効にし、理由を出す
- spec の承認ボタンは、保存済みの本文のプレビューにエラーが 1 つでもあれば無効（サーバも 422 `spec_not_compilable` で拒否する）
- 自己承認: 下書きの `author` が自分なら「作者本人の承認になります（監査と画面に印が残ります）」を出す。下書きを直した人の判定はサーバの `self_approved` に任せ、承認後の改訂一覧に印を出す
- 409 `draft_changed` は「他の人が先に保存・承認した」ことを示すダイアログにし、読み直し（自分の本文は捨てる）か、本文をクリップボードに写してから読み直すかを選ばせる
- Mermaid の描画の失敗は「描画できない」として SpecLino の診断（parse）とは別の欄に出す（S63 の判断）。`securityLevel: 'strict'`
- 差分は行単位の LCS（`services/textDiff.ts`）。大きすぎる入力（行数の積が上限超え）は差分を出さず「大きすぎて差分を出せない」と表示（黙って間違った差分を出さない）。spec は正規形（preview API を前後の本文で呼ぶ）の差分も出す
- 旧形式の入口（`domain` / `legacy_ir`）は JSON の欄で編集できる（折りたたみ）。JSON として読めなければ保存・プレビューに使わず欄の下にエラーを出す
- 一覧のスコープ表示は省く（一覧 API が返さないため。エディタと改訂の画面に出す）

---

## Task 1: プレビュー API（TDD）

**Files:** Create `src/speclino/services/spec_preview.py`、`src/speclino/api/routes/spec_preview.py`、`tests/unit/services/test_spec_preview.py`、`tests/integration/services/test_spec_preview_routes.py`。Modify `src/speclino/api/app.py`（ルータ登録）、`openapi.json`（再生成）。

- [ ] 単体: `preview(markdown, domain, legacy_ir) -> SpecPreview`。正常な本文 → `compiled` あり・`canonical == render(compiled)`。誤りのある本文 → `compiled is None`・`canonical is None`・エラー診断に span（line/column）がある
- [ ] 統合: メンバーは 200、別ワークスペースの人は 404、未ログインは 401、512KB を超える body は 413、不正な JSON は 422
- [ ] ルート: body をストリームで読み 512KB を超えたら打ち切って 413。`PreviewRequest`（`extra="forbid"`、`markdown`、`domain?`、`legacy_ir=[]`）
- [ ] `make lint typecheck`、該当テスト緑 → commit

## Task 2: web の型と API クライアント

**Files:** Create `web/src/types/knowledge.ts`、`web/src/types/syncToken.ts`、`web/src/api/knowledge.ts`、`web/src/api/syncTokens.ts`。Modify `web/src/api/errors.ts`・`client.ts`（`ApiError.code` を省略可能で足す）。

- [ ] 型: ArtifactKind / Scope / SkillContent / InstructionsContent / SpecContent / ArtifactRevision / ArtifactSummary / Bundle / ApproveResult / ParseResult / SpecDiagnostic / CompiledSpec（必要な欄）/ SpecPreview
- [ ] API: list / create / getDraft（404 `draft_not_found` → null）/ saveDraft / approve / listRevisions / currentBundle（404 → null）/ previewSpec、sync トークンの list / issue / revoke
- [ ] typecheck → commit

## Task 3: 純関数（TDD、vitest）

**Files:** Create `web/src/services/knowledge.ts`（種類とパスの対応、スコープ、編集の土台、承認できるか）、`specPreview.ts`（診断の並べ替え・行の位置・要求の追跡・mermaid ブロック）、`skillFrontmatter.ts`、`textDiff.ts`、と各 `.test.ts`。Modify `web/src/services/paths.ts`。

- [ ] 各関数のテストを先に書き、落ちることを確かめてから実装 → commit

## Task 4: 仕様エディタの部品と hook

**Files:** Create `web/src/components/spec-editor/{MarkdownInput,MermaidPreview,CanonicalView,DiagnosticsList,RequirementTrace}.tsx`、`web/src/hooks/useSpecPreview.ts`（500ms の debounce、古い応答を捨てる）、`web/src/services/mermaidRender.ts`（mermaid の dynamic import と初期化）。

- [ ] 診断から textarea の行へ移る（選択とスクロール、フォーカス）。未形式化の行を強調、mandatory の未形式化は「ゲートで REVIEW」の印
- [ ] 部品の静的描画テスト（renderToStaticMarkup）→ commit

## Task 5: 画面（一覧・エディタ・差分と承認）とナビゲーション

**Files:** Create `web/src/pages/knowledge/{KnowledgeList,KnowledgeEditor,SpecEditorPane,SkillEditorPane,InstructionsEditorPane,ScopePicker,ApprovalPanel,Revisions,ConflictDialog}.tsx`。Modify `web/src/App.tsx`、`web/src/components/shell/TopBar.tsx`、`AccountMenu.tsx`（スマホ幅のリンク）。

- [ ] lint / typecheck / build → commit

## Task 6: sync トークンの管理

**Files:** Create `web/src/components/settings/SyncTokensTab.tsx`。Modify `web/src/pages/Settings.tsx`、`web/src/services/settingsTabs.ts`（+test）、`web/src/types/command.ts`。

- [ ] 発行（名前＋プロジェクト）→ 平文を 1 回だけ表示、一覧、失効（確認ダイアログ）。member には発行・失効を出さない → commit

## Task 7: 通しの確認

- [ ] `make lint` / `make typecheck` / `make test`（DB あり）、`npm run lint` / `typecheck` / `test` / `build`
- [ ] ブラウザ（Playwright MCP）: 下書き → 診断が消える → 承認 → bundle の世代が進む。スクリーンショットを報告に残す
