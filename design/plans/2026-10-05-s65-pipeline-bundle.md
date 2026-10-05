# S65: チェックの流れを bundle に差し替える 実装計画

> 親計画: `design/plans/2026-10-05-team-knowledge-registry.md`（S65）。仕様: `design/2026-10-05-team-knowledge-registry.md` 1 章の不変条件・3.3・5.2・8 章。Issue #107。

**Goal:** ワークスペースに登録されたプロジェクトの run は、開始時に bundle を 1 つ固定し（`ensure_bundle` → `resolve_for_project`）、仕様と規約をその bundle から読む。仕様は承認時にサーバが作った `content.compiled` を使い、旧形式（`compiled.legacy`）は今のエンジンに、UML の義務は S64 の検査に通す。出るべき要求 ID を manifest に書き、欠けたものは UNKNOWN で補う。lock の鮮度（S68）は必須の Finding。replay は manifest の bundle を解決して決定的な検査をすべて再実行する。

**不変条件:** 偽の VERIFIED_IN_SCOPE を出さない。配線できない・解析できないものはすべて UNKNOWN。

## Architecture

- `services/knowledge_inputs.py`（新規。bundle → 検査器への入力）
  - `bundle_hash(bundle)`: 署名を含む正規 JSON の sha256（manifest 用）
  - `OperationResolver(bindings)`: 図の `<Class>.<method>` を Code IR の業務操作 ID へ（下記の対応規則）。`known_operations`
  - `spec_units(knowledge)`: spec の改訂ごとに (名前, CompiledSpec, DomainSpec, markdown)。順は artifact 名
  - `uml_compiler(markdown, domain, legacy)`: S62 の `SpecCompiler` に合わせた `compile_spec` のアダプター
  - `make_knowledge_resolver(factory)`: worker 用。1 トランザクションで `ensure_bundle` → `resolve_for_project`
- `services/check_pipeline.py`
  - `CheckRequest.knowledge: ResolvedKnowledge | None`、`github_repo_id: int | None`、`now: datetime | None`
  - knowledge が None なら今の Git の流れ（CLI のローカル実行・自己検証）
  - knowledge があれば: 仕様は bundle だけ（リポジトリの `specs/` は読まない）、`domain` は spec の `content.domain`、`bindings.yaml`・`contracts/`・`exceptions.yaml`・`.speclino/policy/*.yaml` はリポジトリから（D9・D10）、チーム規約は `knowledge.policy`
  - 旧形式は今のエンジン（検証・S41c の再利用・evaluate）を spec ごとの domain で通す
  - UML: 構造 → `check_structure`、順序・包含 → `check_trace`、遷移 → `check_state_machine`
  - 鮮度: `check_lock_snapshot`（github_repo_id が None なら UNKNOWN の `KNOWLEDGE-LOCK-001`）
  - 期待する ID: `expected_ids(compiled, [])` ＋ 規約（検査器があり on_missing_input が skipped でないルール。`<rule>:<path>` も出たとみなす）＋ lock。`fill_missing` で UNKNOWN を合成
  - manifest: `bundle_generation`・`bundle_hash`・`uml_parser_version`・`expected_requirement_ids`、`policy_team`/`policy_revision`
- `services/review_run_service.py`: `ReviewRunConfig.resolve_knowledge`（`resolve_team_policy` と同じ注入）。team があるプロジェクトは run の開始時に 1 回だけ解決し、その結果だけを使う。Git の仕様の承認の付け替え（`_reconcile_specs`）は bundle の run では行わない
- `api/deps.py`: `resolve_knowledge=make_knowledge_resolver(factory)` を配線
- `services/knowledge_registry.py`: `approve` の既定の compiler を `uml_compiler` に（REST も同じ既定を使う）
- `services/replay_service.py`: `replay_bundle(session, run_dir, *, project_id, repo_root)`。manifest の世代の bundle を `get_bundle` で引き、署名と `bundle_hash` を照合、`resolve_for_project` して `run_check` を同じ run_id・上限・時刻で再実行し、全 Finding（規約・lock・UML を含む）を比べる。既存の `replay` は bundle の run を拒否する（DB が要る）。LLM 支援（S70）の再評価は記録で行うためのフックを残す
- `services/spec_import.py` ＋ `speclino import-specs`: `specs/ir/*.spec-ir.json` を、要求 ID を含む `specs/*.md` ごとにまとめて spec の下書き（markdown・domain・legacy_ir）にする

## 図の操作とコードの対応（D9: 名前の一致が既定、bindings.yaml で上書き）

- 操作 `<C>.<m>`: (1) bindings の operations に同じキーがあればそれ、(2) 無ければ symbol が `.<C>.<m>` で終わる（または一致する）operation が 1 つだけならそれ。どちらも最後に `operation_by_symbol` で解析器が実際に使う操作 ID にそろえる。見つからない・複数 → None（S64 が UNKNOWN）
- 入口 `<C>.<m>`（順序・包含）: (1) bindings の behaviors に同じキー、(2) entrypoint が `.<C>.<m>` で終わる behavior が 1 つだけ。見つからなければその義務は UNKNOWN（「入口の操作に対応する振る舞いが無い」）
- 状態遷移図: 入口を持たないので、bindings の全 behavior の Code IR で検査する（behavior ごとに Finding）。behavior が無ければ Finding が出ず、欠落の UNKNOWN になる
- `known_operations`: bindings の operations のキー ∪ Code IR に現れた操作 ID
- Code IR は (behavior, domain) ごとに 1 回だけ作る。解析が例外なら、その behavior を使う義務はすべて UNKNOWN

## Tasks（TDD、タスクごとにコミット）

1. **承認の compiler**: `uml_compiler` を既定に。S62 のテスト（既定は拒否）を `unavailable_compiler` を明示する形に直し、REST で正しい仕様が承認できる・誤りは 422 を確かめる
2. **knowledge_inputs**: 対応規則・bundle_hash・spec_units の単体テスト
3. **check_pipeline の bundle 経路**（`tests/unit/services/test_check_pipeline_bundle.py`、DB なし）: `test_bundle_specs_replace_git_specs`、順序の義務の VERIFIED / VIOLATED、対応なし → UNKNOWN、欠けた義務・規約 → UNKNOWN、lock（repo ID なし → UNKNOWN）、manifest の欄、`test_selfcheck_still_reads_frozen_copy`。`write_artifacts` は spec が無い run も書ける
4. **取り込み**（`tests/integration/services/test_spec_import.py`）: `import_specs`、CLI、`test_legacy_spec_same_findings_as_before`（examples/orders を取り込み・承認した bundle の Findings が Git の Findings と同じ）
5. **worker**（`tests/integration/services/test_check_pipeline_bundle_worker.py` 相当を test_review_run_service と同じ形で）: `test_run_pins_bundle`（run の途中で承認して世代が進んでも、run は開始時の bundle のまま）
6. **replay**（`tests/integration/services/test_replay_bundle.py`）: 一致、`test_replay_reruns_policy_verifiers`（保存した規約 Finding を書き換えると不一致）、bundle_hash の不一致は拒否
7. `make lint` / `typecheck` / `test` / `selfcheck`

テストの置き場はリポジトリの流儀（`tests/unit/...`・`tests/integration/services/...`）。DB は専用コンテナ `speclino-s65-db`（55465）。
