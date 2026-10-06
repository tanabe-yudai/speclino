# S70: LLM 支援の規約と人の確定 実装計画

> 親計画: `design/plans/2026-10-05-team-knowledge-registry.md`（S70）。仕様: `design/2026-10-05-team-knowledge-registry.md` 6.2・6.3・8 章。Issue #112。

**Goal:** 決定的に検査できないチームの規約（「エラーメッセージが次の行動を示す」など）を LLM で評価する。ただし LLM が出せるのは REVIEW だけで、確定は人（owner / approver）が行う。同じコードの場所の結果は揺らがない（キーで記録を引き、replay は呼び直さない）。

**不変条件:** verdict は 4 値のまま。LLM 支援の Finding は常に REVIEW（区分は `assessment_outcome`）。LLM の失敗・時間切れ・費用枠の超過はそのルールの単位の UNKNOWN（VERIFIED にも VIOLATED にもしない）。`conformance/`・`analysis/` は LLM を import しない。

## Architecture

- `domain/policy.py`: `PolicyRule.tier: Literal["deterministic", "llm_assisted"] = "deterministic"`（既存の YAML はそのまま通る）。llm_assisted は検査器 ID `llm.checklist`（「mandatory には検査器が必須」の検証はそのまま通る）
- `domain/policy_params.py`: `LlmChecklistParams {items: [{id, question}], unit_kind: function|string_literal|endpoint, paths: [glob], call_names, max_units}`
- `domain/run.py`: `RunManifest.attestations`（このゲートで効いた確定の一覧。任意の欄の追加だけ）
- `services/llm_units.py`: 評価の単位を AST で取り出す（対象のコードは import しない）。単位の hash は `ast.unparse` の正規形（空白・コメントを除く）。文字列リテラルは値そのもの
- `services/llm_policies.py`: `assess(rule, units, *, llm, store, policy_revision, cost_guard=None, ...)`
  - キー = `AssessmentKey(policy_revision, rule_id, unit_hash, model, prompt_version)`。prompt_version はプロンプトの版＋チェック項目の hash（項目を変えれば記録を使わない）
  - 記録があれば LLM を呼ばない。新しいキーは 3 回（temperature 0、項目ごとに `{item_id, applies, quote}`）
  - 項目ごと: 「該当する」が 2 回以上 → issue、1 回 → split、0 回 → no_issue（no_issue は全員一致のときだけ。ゲートを止めない唯一の区分なので安全側に倒す）
  - Finding は (単位, 項目) ごとに 1 つ。verdict は常に REVIEW。引用・場所・項目を持つ。指紋 = (ルール ID, シンボル, 単位の hash, 項目)。`evidence_refs` に `fingerprint:` と `content_hash:` を入れる（画面・ゲートが読む）
  - 失敗（例外・時間切れ・形式違反・費用枠）→ その単位の UNKNOWN。記録は残さない
  - `replay_only=True`（llm なし）: 記録だけを使い、無いキーは UNKNOWN
- `verifiers/llm_checklist.py`: `llm.checklist` の登録。検査器自身は LLM を呼ばず、worker が先に作った Finding を返すだけ。この run で評価していない（CLI など）ルールは UNKNOWN
- `services/check_pipeline.py`（S65 は merge 済み）: `CheckRequest.llm_findings`・`llm_assessment_keys`・`attestations`。ゲートに渡す「阻止しない ID」= no_issue ∪ `apply_attestations`。`KNOWLEDGE-BINDINGS-001`（変更の通知）に指紋 = `KNOWLEDGE-BINDINGS-001:` ＋ 変わったファイルの内容の sha256 を付ける（新しい変更は確定し直し）
- `conformance/gate.py`: `triage(..., cleared_ids)`。REVIEW で cleared のものは `cleared` に分け、阻止しない（`decide_gate` は変えない）。`GateResult.cleared`、gate.json の `cleared_ids`
- `services/review_run_service.py`: run_check の前に llm_assisted のルールを評価（`ReviewRunConfig.policy_llm`、`assessment_store`）。ワークスペースの確定を読み、CheckRequest に渡す
- `migrations/versions/0011_llm_assessments.py`（down_revision = 0010）: `llm_assessments`（key unique, unit_symbol, items JSONB, created_at）、`attestations`（workspace, fingerprint, content_hash, disposition, actor, created_at, unique(workspace, fingerprint, content_hash)）
- `db/repositories/assessments.py`: `AssessmentRepository`（store）、`AttestationRepository`
- `services/attestations.py`: `attest(session, *, fingerprint, content_hash, disposition, actor, workspace)`（同じ鍵は上書き＝最新の処置が効く。監査に残す）、`apply_attestations(findings, attestations)`
- `api/routes/attestations.py`: `POST /api/workspaces/{slug}/attestations`（owner / approver、他は 403、別のワークスペースは 404）。`run_id` を渡せばその PR の head で再検査の run を作る
- `api/routes/webhooks.py`: `/speclino attest <fingerprint> <content_hash> [ack]` を記録（承認者の照合は既存の login の照合。非承認者は監査だけ）。承認後は既存の流れで再検査の run
- `services/replay_service.py`: `replay_bundle` は記録だけで LLM 支援を再評価し、Finding とキーを比べる（記録が無い → UNKNOWN の差異）
- web: Finding に区分のラベル、approver に REVIEW の確定ボタン

## Tasks（TDD、タスクごとにコミット）

1. **型**: tier・LlmChecklistParams・manifest の attestations、schemas 再生成。テスト: 既存 YAML が通る、llm_assisted の検証
2. **単位の取り出し**: `services/llm_units.py`。テスト: 空白・コメントだけの変更で hash が同じ、関数・文字列・エンドポイント
3. **assess**: `services/llm_policies.py`（InMemory の store）。受け入れ: `test_same_unit_same_key_no_llm_call`、`test_majority_of_three`、`test_split_is_review_split`、`test_llm_failure_is_unknown`（例外・時間切れ・形式違反・費用枠）
4. **DB**: マイグレーション・ORM・repository。テスト: upgrade/downgrade、store の往復、attestation の一意性
5. **ゲート**: `apply_attestations`・`triage` の cleared・check_pipeline の配線・KNOWLEDGE-BINDINGS-001 の指紋。受け入れ: `test_attestation_bound_to_content_hash`、no_issue は止めない、issue/split は mandatory なら止める、acknowledged_violation は止めたまま
6. **worker**: review_run_service で評価・確定の読み込み、deps の配線。テスト: worker 経由で LLM の Finding と manifest のキー
7. **確定の経路**: REST と PR コメント、再検査の run、監査。テスト: 403・404・承認者・非承認者
8. **replay**: 記録で再評価。受け入れ: `test_replay_uses_recorded_output`（LLM を呼ばない、記録が無ければ UNKNOWN の差異）
9. **web**: ラベルと確定ボタン（最小）
10. **境界**: import 制約テスト（`services/llm_units.py` は google を使わない、conformance/analysis は LLM なし）、make lint / typecheck / test / selfcheck
