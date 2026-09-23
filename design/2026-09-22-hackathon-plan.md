# SpecLino ハッカソンMVP 実装計画

## Context

- **目的**: Zenn「第5回 Agentic AI Hackathon with Google Cloud」に SpecLino を提出する。締切 **2026-10-15 23:59**。提出後〜12/1 はデフォルトブランチ修正不可。全提出プロジェクトが 12/1 までデプロイを動作確認可能な状態で維持する（要項原文で確認）。適格性は問題なし。
- **製品の核**: 仕様（Markdown）とコードを共通語彙の中間表現（Spec IR / Code IR）へ変換し、決定的な検査器（SMT）で「どの要求が、どの条件で、どう違反されるか」を反例つきで出す。エージェントは証拠収集と人への確認を担い、判定はしない。
- **正本文書**（M0 で `design/` に保存）:
  - `docs/SpecLino_暫定仕様書_v0.6.md`（製品全体: 規約・ゲート・エージェント権限・外部連携）
  - **ユーザー提供「仕様–実装の意味差分レビュー基盤 PoC v0.1 実装依頼書」**（意味検証コアの規範。IR データモデル、Python 部分集合、検査アルゴリズム、判定規則、Golden fixtures、モジュール分割はこの文書に従う。本計画はハッカソン向けの取捨選択と統合方針だけを書く）
- **審査との対応**: 審査軸②（自律性＋可観測性・セキュリティ）とテーマ「ガバナンスを意識した使えるエージェント」に、「エージェントは判定できない」「実行前の権限検査と監査」「確認できないものを通さない」という設計が直結する。
- **制約**: GCP予算 $300 / Terraform管理 / 初期はローカル開発 / ソロ・ほぼフルタイム / スピード重視（学習モードでのコード寄与依頼はしない）。
- **必須要件の充足**: 実行基盤=Cloud Run、AI=ADK + Gemini。デプロイURLは Identity Platform のメール/パスワード固定テストアカウント。`docs/` は Git 管理外のまま。

## 合意済みの決定

| 項目 | 決定 |
| --- | --- |
| 意味検証コア | PoC v0.1 文書のとおり: **Spec IR / Code IR ＋ 型付き Expr ＋ Z3 による充足性検査**。経路列挙（記号的状態、イベントの条件・引数・回数）で解析。私案の dominator 方式は置き換える |
| デモ | ①PoC 文書の注文キャンセル（R-CANCEL-01〜07、Golden fixtures と同一）を `examples/orders/` に実装した題材 ②SpecLino 自身の `ToolGateway.invoke`（自己検証） |
| 最終判定 | Finding の verdict は正直に4値（VERIFIED_IN_SCOPE / VIOLATED / UNKNOWN / REVIEW）を保つ。**ゲートはフェイルクローズ**: 段階的エスカレーション後も必須ルールに UNKNOWN / REVIEW が残れば失敗 |
| スタック | Python 3.12 + FastAPI + ADK(Python) + Gemini + z3-solver |
| GitHub連携 | GitHub App + Webhook + Checks API |
| Web UI | React + Vite SPA を FastAPI と同一コンテナから配信。読取り専用 |
| 認証 | Identity Platform（メール/パスワード）。IAP は Google アカウント必須で要項に抵触するため不採用 |
| データ | PostgreSQL（ローカル compose / 本番 Cloud SQL 最小）＋ 成果物 JSON は GCS |
| GCP | 専用プロジェクト新規作成、asia-northeast1 |

## 判定モデル（verdict と gate の分離）

- **Finding.verdict**（PoC 10.3）: VERIFIED_IN_SCOPE / VIOLATED（symbolic_counterexample と observed_counterexample を区別）/ UNKNOWN（未対応構文・未解決呼出し・契約不足・上限・観測境界不足）/ REVIEW（仕様解釈、語彙対応、未記載の追加挙動、過大近似の反例）。severity は verdict と別。解析できないものを違反と表示しない。
- **エスカレーション**（未解決の Finding ごと）: ①決定的パス（既定上限: 深さ2・64経路・ソルバー500ms）→ ②エージェントが `analyze_focus` でルール指向の再解析（上限を深さ4・256経路・1000ms まで選択的に引き上げ、適用条件と矛盾する経路を枝刈り）→ ③【Should】`test_run` による観測証拠 → ④【Should】承認者の構造化回答（`/speclino answer` で語彙対応・契約の選択、`/speclino attest` で人の確認）→ ⑤**ゲート失敗**。
- **Gate**（`fail_on` 方針）: GitHub の必須チェックは `mandatory` ルールに対し `fail_on: [VIOLATED, UNKNOWN, REVIEW]`。Check の summary は「違反 n / 未検証 m（ゲート: 失敗）」と分けて表示し、未検証には検証可能にする具体策（契約登録・対応構文への単純化・テスト追加・仕様の明確化）を添える。`advisory` ルールと CLI 既定は警告運用（終了コード 0/1/2 は PoC 15章）。
- **空虚な成功の禁止**: 適用条件が充足不能なら VERIFIED を出さず仕様/ドメイン診断（REVIEW）。成功条件は拒否条件と独立に持つ（PoC 10.4）。未記載の追加処理は open-world で REVIEW、明示禁止なら VIOLATED（10.5）。
- **規約（規約検査器）**: 規約の `applies_to` が明示的に不一致なら skipped（適合件数に含めない）。それ以外は同じ verdict 体系。

## 意味検証コア（PoC v0.1 の採用範囲と補足）

- **IR**: `Expr`（ref/const/eq/ne/lt/le/gt/ge/and/or/not/add/sub、名前空間 before/after/outcome、型は boolean/integer/列挙/opaque ID）、`Rule`（applicability / modality must|must_not / assertion expression|event_requirement / claim_scope / approval_ref）、`Path`（guard / state_updates / events / outcome / precision / unknown_causes / source_refs）、`Finding`（13.3 の契約）。JSON Schema で `additionalProperties: false`、その後に参照解決・型整合・矛盾・承認ハッシュを検証。
- **入力ファイル**（対象リポジトリ内、base SHA から読む）: `.speclino/domain.yaml`（列挙・型・入口仮定）、`.speclino/bindings.yaml`（behaviors の entrypoint・variables、operations の symbol・claim_scope・argument_bindings・summary_ref）、`.speclino/contracts/*.yaml`（外部操作の承認済み要約）、`specs/*.md` と `specs/ir/*.json`（承認済み Spec IR。approval は「デフォルトブランチへマージ済み」または `/speclino approve-spec <hash>`）。
- **補足（自己検証に必要な拡張）**: bindings の `variables` に、契約済みの**純粋な呼出し結果**を名前付き変数として登録できるようにする（例: `permitted: pure_call:speclino.agent.permissions.Permissions.allows`）。`ToolGateway.invoke` の `if not self.permissions.allows(call.tool)` を `before.permitted` として参照するため。
- **Python 部分集合**: PoC 3.2 のとおり（if/elif/else、早期 return、認識済み raise、定数、ローカル、登録済み属性、and/or/not、比較、整数加減算、静的直接呼出し）。3.3 の対象外構文は影響ルールを UNKNOWN。フレームワークの応答変換は明示アダプター（FastAPI の `HTTPException(status_code=定数)` と `Response(status_code=定数)` のみ）。
- **path_analyzer**: PoC 9 章の順序と意味（早期 return 後を実行済みにしない、前状態と後状態の区別、コメント・名前を根拠にしない）。呼出しは深さ上限までローカル要約、外部は contracts、未知は unknown_causes。上限超過は影響ルールを UNKNOWN。
- **conformance_engine**: 各 Rule × Path について `入口仮定 ∧ ドメイン ∧ 適用条件 ∧ 経路条件 ∧ ¬保証` を Z3 で判定。SAT→反例（overapproximate な経路なら REVIEW: potential_violation）、UNSAT 全経路→VERIFIED_IN_SCOPE、UNKNOWN/timeout→UNKNOWN。event_requirement は「総数を引数絞り込みの前に数える」。【Should】有限ドメインの総当たり検算で Z3 結果をクロスチェック。
- **構造解析（CFG 到達可能性・支配関係）を第二の証拠源に**（【Should】上位）: 経路列挙＋Z3 が主。CFG の構造解析は (a) must_not（回数 0）の**健全な不在証明**（entry→効果→拒否出口の CFG 経路が無ければ実行上も無い。ループがあっても健全なので、経路列挙が UNKNOWN の関数でも VERIFIED_IN_SCOPE(precision=structural) にできる）(b) 効果ノードが出口を支配する場合の枝刈り（Z3 は出口の到達可能性 1 回で済む）(c) 支配するが到達可能性未証明 → REVIEW: potential_violation(structural)。合成規則: 経路解析が exact ならそれを優先し、incomplete の部分だけ構造証拠で埋める。条件・値・引数・回数はドミネーターでは判定できないため、単独では使わない。
- **semantic_diff**（【Should】）: base 仕様＋base コード、head 仕様＋head コード、Code IR 同士の差分で 新規/既存/解消 違反と 追加/変更/削除 要求を分類。**Must の範囲**: base の Rule 集合と head の差分（削除・未承認変更 → REVIEW: stale_approval → ゲート失敗）。
- **出力**: `run-manifest.json / spec-ir.json / code-ir.json / findings.json / semantic-diff.json / review.md` を run ごとに GCS（ローカルは FS）へ保存し、UI と Check はここから描画。
- **手書き Code IR**（PoC 18 章 M1→M2 の順序）: 解析器が完成する前に、題材コードの経路を人手で記述した `code-ir.json`（PoC 6.7 の形式）。M1 では検査器・ゲート・レポートをこれだけで試験し、M2 では解析器が Python ソースから同じ Finding を出すことの期待出力として再利用する。`speclino check --code-ir <file>` はこの IR を直接入力するモードで、replay（AC-013）とデバッグにも使う。
- **既存の図生成器との接続**（PoC 11.3）はハッカソン対象外。`trace_importer` の正規化 JSON 契約（11.2）だけ Should で用意。

## アーキテクチャ

エージェントが判定を上書きできない構造: (a) 判定を書くツールがない (b) `conformance_engine` は型付き Spec IR / Code IR / Evidence しか受けず、Code IR と Evidence を作るのは決定的なモジュール（path_analyzer、verifiers、trace_importer）だけ (c) LLM の文章は `agent_note` として保存され engine の入力にならない (d) `conformance` から `google.*` / `agent` / `adapters` の import をテストで禁止。乗っ取られた場合の最悪結果は「証拠不足→未検証→ゲート失敗」。

**本番は同一イメージを役割別 Cloud Run サービスに分ける**（`SPECLINO_ROLE=web|worker|sandbox`、`all` はローカル専用）。min-instances=0。**VPC / NAT は作らない**。理由:

1. **要らない** — Cloud SQL は Cloud SQL コネクタ（Unix ソケット）、Gemini / GCS / Secret Manager / Cloud Tasks は Google の内部経路、GitHub API は Cloud Run の既定 egress で直接出られる。NAT が要るのは VPC 経由で全 egress を流す構成にした場合だけ。
2. **固定費** — Serverless VPC コネクタはアイドルでも月 $8〜10、Cloud NAT も時間課金。min-instances=0 と db-f1-micro で「使わない時はほぼ無料」にした設計が崩れる（予算 $300 で 12/1 まで維持する要件）。
3. **時間とリスク** — サブネット・コネクタ容量・ルーティングの設定と障害モードが増える。

VPC が必要になるのは、Cloud SQL をプライベート IP のみにする / 固定 egress IP が要る / VPC 内の他リソースに繋ぐ場合。今回はいずれも該当せず、Cloud SQL は **authorized networks を空**にすることで「外から直接繋がらない」状態を VPC なしで実現している。

| サービス | 公開 | 実行SAのロール | 持つ秘密 | 呼べる主体 |
|---|---|---|---|---|
| web | 公開 | cloudsql.client、cloudtasks.enqueuer、tasks呼出し用SAへの iam.serviceAccountUser | Webhook署名秘密、MCPトークン | 全員（API は Identity Platform の ID トークン必須） |
| worker | IAM必須 | cloudsql.client、成果物 bucket objectAdmin、secretAccessor、aiplatform.user、sandbox への run.invoker | GitHub App 秘密鍵 | tasks呼出し用SA のみ |
| sandbox | IAM必須 | なし | なし | worker SA のみ |

```text
GitHub (PR / comment / rerun)        Editor (MCP, Bearer)     審査員ブラウザ (Identity Platform)
   | webhook (HMAC検証)                   |                            |
   v                                      v                            v
+------------------ Cloud Run: speclino-web (public) ----------------------------+
| FastAPI: /webhooks/github  /api/*  /mcp  / (React静的配信)                       |
+--------------+-----------------------------------------------------------------+
               | Cloud Tasks (OIDC)
               v
+------------------ Cloud Run: speclino-worker (IAM必須) -------------------------+
| ReviewRunService（決定的オーケストレータ）                                       |
| 0 DBで実行権を原子的に取得 → 1 base/head スナップショット固定（run-manifest）      |
| 2 spec_validator（承認ハッシュ照合・base との Rule 差分） → 3 verifiers（規約）     |
| 4 python_frontend → path_analyzer（既定上限） → 5 conformance_engine（Z3）        |
| 6 [UNKNOWN/REVIEW あり] ADK Agent ⇄ ToolGateway ⇄ analyze_focus / repo_search /  |
|   test_run / human_ask → Evidence 追加 → 再評価                                  |
| 7 gate（fail_on） → 8 鮮度確認(AC-011) → reporter → Check Run 更新 → 成果物保存    |
+----+--------------+---------------+---------------------+----------------------+
     v              v               v                     v
 Cloud SQL(PG)   GCS(成果物)  Gemini 3.8 Flash       Cloud Run: speclino-sandbox
                             (location=global)      (権限ゼロSA, sandbox_launcher)
 Identity Platform  Cloud Trace / Logging ← OpenTelemetry（ADK span + pipeline span）
 GitHub Actions --Workload Identity Federation--> build → Artifact Registry → deploy
```

**時間と冪等性**: パイプライン全体 15分（エージェント10分）< Cloud Run request timeout 20分 < Cloud Tasks dispatch deadline 25分。worker は `UPDATE review_runs SET exec_state='running' WHERE id=? AND exec_state='queued'` で実行権を取り、再配信は 200 で無視。外部副作用のID（check_run_id、コメントID）を保存。

## ディレクトリ構成（ルート: `/Users/tanabe_yudai/workspace/project/github/spec-lino/`）

```text
pyproject.toml  uv.lock  Makefile  Dockerfile  docker-compose.yml  CLAUDE.md  README.md  alembic.ini  migrations/
.github/workflows/{ci.yml, deploy.yml}
.speclino/{domain.yaml, bindings.yaml, contracts/, policy/speclino-core.yaml, exceptions.yaml, project.yaml}
specs/*.md  specs/ir/*.json                      # SpecLino 自身の仕様と承認済み Spec IR
examples/orders/{app/, specs/, .speclino/}       # 注文キャンセルの題材（PoC 4章）
schemas/{expr, spec-ir, code-ir, findings, bindings, domain, trace}.schema.json
skills/speclino-spec-authoring/SKILL.md
src/speclino/
  domain/          型定義のみ: expr, spec_ir, code_ir, finding, evidence, run, policy, tooling, question
  spec/            spec_reader.py  spec_extractor.py(Gemini, 候補生成のみ)  spec_validator.py  approvals.py
  frontend/python/ index.py(AST・シンボル索引)  subset.py(対応構文判定)  adapters/fastapi_outcome.py
  analysis/        path_analyzer.py  symbolic_state.py  calls.py(ローカル要約・contracts)  limits.py
  conformance/     engine.py(Z3)  finite_check.py  gate.py  exceptions.py  semantic_diff.py  reasons.py
  verifiers/       registry.py  make_targets.py  spec_format.py  ci_required.py  api_error_format.py
  reporter/        json_out.py  review_md.py  check_report.py
  trace/           importer.py  schema.py
  agent/           app.py  instructions/investigator_v1.md  gateway.py  permissions.py  budget.py
                   plugins/{audit_plugin, budget_plugin}.py
                   tools/{analyze_focus, repo_search, policy_check, plan_record, test_run, contract_lookup, docs_lookup, human_ask, honeypots}.py
  services/        review_run_service.py  snapshot_service.py  run_freshness.py  cost_guard.py  questions.py  replay_service.py
  ports/           code_host, blob_store, task_queue, sandbox, llm, clock
  adapters/        github/{app_auth, client, webhook, checks_mapping}.py  localgit/  storage/{gcs, fs}.py
                   queue/{cloud_tasks, inprocess}.py  sandbox/{docker_local, cloud_run_sandbox, disabled}.py
                   llm/{gemini, scripted}.py  auth/{identity_platform, dev}.py
  api/  mcp_server/  sandbox_runner/  db/  telemetry.py  settings.py  cli.py(speclino check / spec-extract / replay / eval)
web/src/{types/, api/, services/, auth/, pages/{Login,RunList,RunDetail}.tsx, components/{FindingCard,CounterexamplePanel,PathView,Timeline,QuestionCard,ScopeBlock}.tsx}
eval/golden/cancel_order/<case>/{case.yaml, app/service.py, spec-ir.json, expected-code-ir.json(手書き Code IR=解析器の正解), expected-findings.json}   eval/holdout/
tests/{unit, integration, agent, e2e}
infra/bootstrap/   infra/envs/prod/*.tf   design/   scripts/{smoke_prod.sh, seed_demo_prs.sh, create_test_user.sh}
```

型は `domain/` と `web/src/types/` に分離、ロジックは `services/` `conformance/` `analysis/` に集約し UI 層に置かない。`conformance/` と `analysis/` は LLM 非依存で、手書き IR だけで単体試験できる。

## データモデル（MVP）

`projects`（settings: 承認者ID、権限、test.run 可否、fail_on）/ `review_runs`（exec_state と gate_status を別カラム、`manifest` jsonb＝run-manifest、`budget`、`parent_run_id`、`check_run_id`、成果物 URI）/ `findings`（PoC 13.3 の契約＋ escalation_state）/ `evidence`（kind, producer@ver, origin=static_analysis|runtime_trace|human, payload, blob_uri, hash）/ `tool_executions`（decision, args_hash, result_hash）/ `run_events` / `questions` / `approvals`（approve-spec / answer / attest）/ `audit_events` / `webhook_deliveries` / `cost_ledger`。Code IR と Spec IR の本体は GCS の JSON（DB には hash と URI）。

## 題材とデモPR

**Golden fixtures（PoC 17.1 の 27 ケース）**: `eval/golden/cancel_order/` に、正しい実装・変異（状態誤り、409→400、所有者拒否、無権限許可、在庫復元削除／管理者経路のみ／2回／別注文の商品、監査に所有者ID、早期return後の呼出し、到達不能分岐、コメントのみ、変数名変更、同値書換え、ヘルパー分割、未記載通知、未解決 getattr、ループ、上限、承認後の仕様変更、凍結入力の再実行）。**Must は先頭 12 ケース、M4 で全件。**

**SpecLino 自己検証**: `R-TOOL-01`（`before.permitted` が偽なら `tool_execute` の呼出し 0 回・`audit_denied` 1 回・outcome=denied）、`R-TOOL-02`（真なら `tool_execute` 1 回、引数 `tool == before.call.tool`）。ToolGateway は対応部分集合で素直に書く。【Should】`R-GATE-01`（必須ルール空集合で通過を返さない）、`R-RUN-01`（古い実行は公開しない）。

**規約**: 【Must】`MAKE-001`（setup/test/lint、空レシピ AC-004）、`SPEC-FMT-001`（要求ID・必須節 AC-002）。【Should】`CI-001`、`API-ERR-001`。例外は全項目必須、期限切れ無効（AC-009）。

**デモPR（12/1 まで open）**
1. **意味差分の主デモ** — `examples/orders` で在庫復元を管理者経路だけに移す → Z3 の反例（PENDING・非管理者・所有者・呼出し 0 回）を仕様行とコード行つきで提示。エージェント不要＝費用ゼロ。
2. **エージェント調査** — 同PRで、在庫復元をヘルパー3段の奥に移し独立 if を増やす → 既定上限で UNKNOWN（limit）→ エージェントが `analyze_focus(R-CANCEL-04, depth=4)` を選び再解析 → VIOLATED 確定。計画・ツール実行・監査がタイムラインに出る。
3. **自己検証** — ToolGateway で `registry.execute` を `allows` 判定の前に移す → R-TOOL-01 違反（symbolic_counterexample）。
4. **ガバナンス** — Spec IR を削除／未承認変更して通そうとする → REVIEW: stale_approval → ゲート失敗。`/speclino approve-spec` 後に再検証。
5. **フェイルクローズ** — `for` ループで在庫復元 → UNKNOWN → 具体策つきでゲート失敗。
6. 【Should】**語彙対応の確認** — 新しい `stock.release` 呼出し → REVIEW（契約なし）→ エージェントが `repo_search`／`contract_lookup` で候補を集め `human_ask` → `/speclino answer` で子 run。 / **例外付き通過**。 / **観測証拠** — `test_run` で observed_counterexample。

## エージェント（`agent/`）

- `LlmAgent(model="gemini-3.8-flash")` + `App(plugins=[AuditPlugin, BudgetPlugin])` + `Runner` + `InMemorySessionService`（永続化・中断再開・Tool Confirmation は使わない）。ローカル `GOOGLE_API_KEY`、本番 `GOOGLE_GENAI_USE_ENTERPRISE=true` + ADC。
- 入力: UNKNOWN / REVIEW の Finding、unknown_causes、既存証拠の要約、残り予算。リポジトリ由来の文字列は untrusted ラベルつき。
- ツール（全て `ToolGateway.invoke` 経由、書込みなし、GitHub トークンを渡さない）: 【Must】`analyze_focus`（behavior/rule 指定で上限を引き上げた決定的再解析）/ `repo_search` / `policy_check`（現在の verdict と unknown_causes）/ `plan_record`。【Should】`test_run` / `contract_lookup` / `docs_lookup` / `human_ask`（構造化選択肢のみ）/ ハニーポット（`repo_write`・`deploy_run`、常に拒否し `injection_suspected` を監査）。
- 上限: ツール20回・`asyncio.timeout(600)`・`RunConfig(max_llm_calls=40)`・ツール結果サイズ上限・`max_output_tokens`。**費用ガード**（`usage_metadata` から推定、日次累積枠を `cost_ledger` で管理、超過時はエージェント非起動→未検証のままゲート失敗）。
- human.ask（Should）: 質問作成時に選択肢・適用先・版一式を固定。`/speclino answer Q-n B` は `projects.settings` の承認者IDで検証。回答は human 由来の Evidence（契約選択・語彙対応）になり同じ版一式で子 run。仕様の意味変更は正本の修正へ誘導。

## GitHub App / Checks / 認証 / サンドボックス / インフラ

- **GitHub App**: Checks write / Contents read / Pull requests write / Metadata read。イベント `pull_request`、`check_run`、`check_suite`、`issue_comment`（approve-spec / answer / attest / rerun）。開発用と本番用を分ける。HMAC 検証、delivery_id 重複排除、同一PRの未完了 run を superseded。Check 名 `SpecLino / gate`。conclusion: ゲート失敗→`failure`、通過→`success`、例外付き通過→`neutral`、実行障害・回答待ち・superseded→`action_required`。**必須チェック化・ソース App 固定・bypass 無効化を M2 の完了条件**にし、欠落／非成功／別主体の同名 success でマージできないことを実PRで確認。
- **表示**: `review.md`（PoC 13章）を Check の summary/text と UI の RunDetail に使う。Finding には仕様行・コード経路・反例・仮定・claim_scope・解析範囲・観測状況（static_verdict と test_observation は別軸）。annotations 最大50件。
- **審査員体験**: App インストール不要。Identity Platform テストアカウント（`scripts/create_test_user.sh`、認証情報は提出フォームへ）でログイン → デモPRの run 一覧 → Finding と反例 → 実 Check へのリンク → 回数・費用枠つき「デモ再実行」。
- **認証**: Terraform `google_identity_platform_config`（メール/パスワードのみ）。フロント Firebase JS SDK、バックエンドで ID トークン検証と許可ユーザー照合。`SPECLINO_ENV=local` のみ dev 認証。MCP は静的 Bearer【Should】。
- **test.run（Should）**: `tests/` と pytest 設定は base、実装は head から別配置。`--import-mode=importlib`、`PYTHONSAFEPATH=1`、`-p no:cacheprovider`。宣言済みテストIDのみ実行。fork PR は無効。junit の assertion failure→observed_counterexample、成功→test_observation=OBSERVED（static verdict と別軸）、error/timeout→UNKNOWN。ローカルは Docker（`--network none --read-only --cap-drop ALL ...`）。本番は Cloud Run sandboxes（Preview）。**1日PoC**（信頼側からの強制停止・絶対パス・`--export-tar` 回収・資源制限）が不成立なら `disabled`。
- **Terraform**: `infra/bootstrap`（プロジェクト・API・tfstate）と `infra/envs/prod`。google 8.x、sandbox のみ google-beta。秘密は state に入れず `gcloud secrets versions add`。CI/CD は GitHub Actions + WIF。**9/26–27 に骨組み（Cloud Run web/worker・Tasks・Secret Manager・Identity Platform・WIF）を本番へ**、Cloud SQL は 10/2 頃（`db-f1-micro` 相当、ENTERPRISE、HAなし）。max=2、予算アラート $50/$100/$200（実行制御は費用ガード）。見込み $60〜150。クーポン登録は Cloud SQL 作成に合わせる（登録期限 10/20、起算日は未確認）。
- **セキュリティ（PoC 16章）**: 静的解析で対象モジュールを import しない、Expr を eval しない、解析に時間・メモリ・サイズ上限、テスト実行は隔離ジョブ、LLM へ渡す範囲を設定で制限、コメント・仕様内の命令を指示として扱わない、トレースの機密値は必要分のみ。

## マイルストーン

| 期間 | 内容 | 完了条件 |
|---|---|---|
| **M0 9/22–23** | **Project「SpecLino」作成・Milestones・Issue テンプレ・S01〜S41 の起票・`scripts/story.sh`・CLAUDE.md の運用ルール**。設計書2件を `design/` に保存。雛形（uv, ruff, mypy, pytest, Makefile, compose, Alembic, Vite, CI）。`domain/` の IR 型と JSON Schema。スパイク2件（Z3 で cancel_order の 1 ルールを手書き IR から判定、ADK 2.9 Plugin で拒否） | ボードに全ストーリーが並び `story.sh next` が S03/S08/S11/S12/S14 を返す。`make setup test lint` が緑。手書き Spec IR + Code IR から R-CANCEL-04 の反例が出る |
| **M1 9/24–27** | **PoC M1**: spec_validator、conformance_engine（Z3）、gate、reporter（JSON + review.md）、Golden 12 ケースを手書き IR で。規約検査器2種。CLI `speclino check`（手書き Code IR 入力）。**並行（sonnet）**: Terraform 骨組み＋Identity Platform を本番へ | 手書き IR で Golden 12 ケースの verdict・category・反例が期待どおり。凍結入力の再実行で結果一致。本番でログイン→Tasks→worker→Gemini 往復 |
| **M2 9/28–10/1** | **PoC M2**: python_frontend + path_analyzer（部分集合、記号的状態、深さ上限、contracts、FastAPI outcome アダプター）。Golden 12 ケースを Python ソース入力で M1 と一致。base Rule 差分と approve-spec。**並行（sonnet）**: GitHub App（webhook・Checks・Tasks・冪等性・superseded）、localgit E2E | ソース入力で M1 と同じ Finding。実PRで Check と注釈。再配信で Check が重複しない。必須チェックの3条件を確認 |
| **M3 10/2–5** | ADK エージェント（Must ツール4種）・Gateway・監査・上限・費用ガード。SpecLino 自己検証（R-TOOL-01/02、bindings 拡張）。**並行（sonnet）**: Web UI（ログイン・一覧・Finding・反例・タイムライン）、Cloud SQL と本番デプロイ | 本番で デモPR 1〜5 が期待どおり（2 はエージェントの再解析で VIOLATED 確定）。ここで **Must 完了** |
| **M4 10/6–9** | Should を順に: semantic_diff（新規/既存/解消）→ 構造解析（不在証明・枝刈り）→ 例外＋replay → test.run ローカル＋observed 証拠 → human.ask＋contract_lookup（デモ6）→ MCP＋SKILL.md＋`spec-extract`（Gemini 抽出、候補のみ）→ Golden 全27＋holdout → 有限ドメイン検算 → ハニーポット・Cloud Trace → 本番 sandboxes PoC（1日） | 各項目が E2E で動く。**10/9 夜に動いていないものは切る** |
| **M5 10/10–12** | 本番 E2E、デモPR仕込み、デモ再実行ボタン、README（PoC 19章の項目）、IAM/秘密点検、アーキ図 | `scripts/smoke_prod.sh` が緑 |
| 10/13 | **コードフリーズ**、動画撮影 | — |
| 10/14 | 動画編集、Zenn記事、**提出** | — |
| 10/15 | 予備日 | — |

**優先順位**
- **Must**（10/5）: IR + Schema + validator、conformance_engine（Z3）+ gate（fail-closed）+ reporter、python_frontend + path_analyzer（部分集合）、Golden 12、規約検査器2種、base Rule 差分 + approve-spec、エージェント（analyze_focus / repo_search / policy_check / plan_record）+ Gateway + 監査 + 上限 + 費用ガード、GitHub Checks + 必須チェック + 冪等性 + AC-011、Identity Platform + 最小 UI、本番デプロイ、自己検証 R-TOOL-01/02
- **Should（実装順）**: semantic_diff → 構造解析（CFG 不在証明・枝刈り）→ 例外＋replay → test.run ローカル → human.ask＋contract_lookup → MCP＋スキル＋spec-extract → Golden 全件 → 有限ドメイン検算 → CI-001・API-ERR-001・R-GATE/RUN → ハニーポット・Cloud Trace → test.run 本番
- **Stretch**: trace_importer（pytest 計測アダプター）、ループ等の構文拡張、増分解析、既存図生成器アダプター

## タスク管理（GitHub Projects）

現状: `tanabe-yudai/spec-lino` は GitHub 上に private・空で存在。Issues / Projects 有効。`gh` は `project` スコープ済み。

- **ボード**: ユーザー所有の Project「SpecLino」を新規作成しリポジトリにリンク（M0 の最初のタスク）。フィールド: `Status`（Todo / In Progress / Blocked / Done）、`Priority`（P0=Must / P1=Should / P2=Stretch）、`Milestone`（GitHub Milestones M0〜M5、期限つき）、`Depends on`（テキスト。`#12,#15` 形式）。
- **ストーリー = Issue（機能単位）**。ラベル `story` ＋ 領域ラベル（core / analysis / agent / github / infra / ui / eval / docs）。本文テンプレート（`.github/ISSUE_TEMPLATE/story.md`）:

  ```markdown
  ## 概要
  ## 完了条件（動作確認できる単位で箇条書き）
  ## 優先度  P0 | P1 | P2
  ## 依存関係  Blocked by #n, #m（なければ「なし」）
  ## サブタスク
  - [ ] ...（必要に応じて。並列委譲するものは sub-issue に切り出す）
  ## 成果（完了時に追記: 実装した内容、確認方法と結果、コミット/PR、判断の記録、残課題）
  ```

- **運用ルール（リポジトリ直下 `CLAUDE.md` に記載）**:
  - 着手時: `scripts/story.sh start <issue>` → Status=In Progress、Issue に「着手」コメント（計画の要点）。
  - 完了時: `scripts/story.sh done <issue> --result <file>` → 本文の「## 成果」に追記、Status=Done、Issue をクローズ。Blocked になったら Status=Blocked と理由コメント。
  - コミットメッセージに `(#issue)` を付ける。
  - **タスク選択**: `scripts/story.sh next` が「Status≠Done かつ依存が全て Done」のストーリーを P0→P1→P2、同点なら Milestone 順で列挙。メインはこの一覧から動的に選ぶ。独立したストーリーは並列に着手（`sonnet` サブエージェントを worktree で分離し、1 ストーリー 1 エージェント）。
  - サブタスク: ストーリー内のチェックリストで管理し、並列委譲や別担当が必要なものだけ sub-issue に切り出す（親の「依存関係」に列挙）。
  - セッション開始時は `scripts/story.sh board` で In Progress / Blocked を確認してから作業する。
- `scripts/story.sh` は `gh issue` / `gh project item-edit` の薄いラッパ（bash）。Project の item id・field id は `.github/project.json` にキャッシュ。

**初期バックログ**（M0 で Issue 化。ID は起票順の目安、`Depends on` は起票後に実番号へ置換）

| ID | ストーリー | P | Depends on | MS |
|---|---|---|---|---|
| S01 | リポジトリ雛形と CI（uv/ruff/mypy/pytest/Makefile/compose/gitleaks/CLAUDE.md/Issue テンプレ/story.sh） | P0 | — | M0 |
| S02 | 設計書の保存（PoC v0.1、暫定仕様書要約、architecture.md） | P0 | — | M0 |
| S03 | IR 型と JSON Schema（Expr / Spec IR / Code IR / Finding / bindings / domain / trace） | P0 | S01 | M0 |
| S04 | spec_validator（Schema・参照解決・型整合・矛盾・承認ハッシュ） | P0 | S03 | M1 |
| S05 | conformance_engine（Z3。空虚な成功の禁止、open-world、precision→REVIEW） | P0 | S03 | M1 |
| S06 | gate（fail_on、mandatory/advisory、exec_state 分離） | P0 | S05 | M1 |
| S07 | reporter（run-manifest / findings.json / review.md） | P0 | S05 | M1 |
| S08 | examples/orders 題材（アプリ、仕様 Markdown、Spec IR、domain/bindings/contracts） | P0 | S03 | M1 |
| S09 | Golden 12 ケース・手書き IR 版＋`make eval` | P0 | S05, S07, S08 | M1 |
| S10 | CLI `speclino check`（手書き Code IR 入力、終了コード、凍結入力の再現性） | P0 | S06, S07 | M1 |
| S11 | 規約検査器 MAKE-001 / SPEC-FMT-001 | P0 | S03 | M1 |
| S12 | Terraform bootstrap＋prod 骨組み（Cloud Run web/worker、Tasks、Secret Manager、WIF、budget） | P0 | S01 | M1 |
| S13 | 認証（Identity Platform TF、ID トークン検証、dev 認証、create_test_user） | P0 | S12 | M1 |
| S14 | DB スキーマ（Alembic）と repositories | P0 | S03 | M1 |
| S15 | python_frontend（AST 索引、部分集合判定、FastAPI outcome アダプター） | P0 | S03 | M2 |
| S16 | path_analyzer（記号的状態、経路列挙、上限、contracts、ローカル要約） | P0 | S15 | M2 |
| S17 | Golden 12 ケース・Python ソース版（S09 と一致） | P0 | S09, S16 | M2 |
| S18 | base/head Rule 差分と approve-spec（stale_approval） | P0 | S04, S14 | M2 |
| S19 | GitHub App アダプター（webhook 検証、重複排除、installation token、Checks） | P0 | S01, S14 | M2 |
| S20 | ReviewRunService パイプライン（スナップショット、Tasks、実行権、superseded/AC-011、成果物保存） | P0 | S06, S07, S16, S18, S19 | M2 |
| S21 | 必須チェック設定と localgit/実PR E2E | P0 | S20 | M2 |
| S22 | エージェント基盤（ADK App/Plugins、ToolGateway、permissions、budget、audit、cost_guard） | P0 | S14 | M3 |
| S23 | エージェントツール Must（analyze_focus / repo_search / policy_check / plan_record）＋パイプライン統合 | P0 | S16, S20, S22 | M3 |
| S24 | SpecLino 自己検証（R-TOOL-01/02、bindings 純粋呼出し拡張、specs/） | P0 | S16, S22 | M3 |
| S25 | Web UI（Login / RunList / RunDetail: Finding・反例・経路・タイムライン） | P0 | S13, S20 | M3 |
| S26 | Cloud SQL・本番デプロイ・smoke_prod | P0 | S12, S13, S20 | M3 |
| S27 | デモ PR 1〜5 の仕込みと本番確認 | P0 | S21, S23, S24, S26 | M3 |
| S28 | semantic_diff（新規/既存/解消） | P1 | S16, S20 | M4 |
| S29 | 構造解析（CFG 不在証明・枝刈り・structural REVIEW） | P1 | S05, S16 | M4 |
| S30 | 例外（AC-009）＋replay（AC-013） | P1 | S06, S20 | M4 |
| S31 | test.run ローカル（Docker sandbox、junit→observed、別軸表示） | P1 | S22, S08 | M4 |
| S32 | human.ask＋contract_lookup＋`/speclino answer`（子 run） | P1 | S19, S23 | M4 |
| S33 | MCP サーバー＋SKILL.md＋`spec-extract`（Gemini 候補抽出） | P1 | S04, S20 | M4 |
| S34 | Golden 全27＋holdout＋指標レポート | P1 | S17 | M4 |
| S35 | 有限ドメイン検算 | P1 | S05 | M4 |
| S36 | CI-001 / API-ERR-001 / R-GATE-01 / R-RUN-01 | P1 | S11, S24 | M4 |
| S37 | ハニーポット・Cloud Trace（OTel） | P1 | S22 | M4 |
| S38 | test.run 本番（Cloud Run sandboxes PoC 1 日） | P1 | S31, S12 | M4 |
| S39 | デモ再実行ボタン（回数・費用枠制限） | P1 | S25, S22 | M5 |
| S40 | 提出物（README、アーキ図、動画、Zenn 記事、提出フォーム、公開切替） | P0 | S27 | M5 |
| S41 | trace_importer（pytest 計測アダプター）/ ループ等の構文拡張 / 増分解析 | P2 | S31, S16 | — |

## 進め方

- スピード重視。ストーリー着手時に writing-plans で分解、`conformance/` `analysis/` `verifiers/` は TDD（手書き IR の fixture 駆動）。
- 委譲: 仕様が明確なストーリー（S03 Schema、S07、S08、S09、S11、S12、S13、S14、S19、S25、S17 の変異コード、SKILL.md）は `sonnet` へ並列委譲、探索は `haiku`。設計判断・S05・S16・S22・S20 の統合はメイン。
- 動作確認できる単位で区切り、**コミット・プッシュは必ずユーザー承認後**（Conventional Commits、`(#issue)` 付き）。大きな変更後は `/simplify` と codex の差分レビュー。
- GCP プロジェクト作成、課金紐付け、`terraform apply`、GitHub App 作成、リポジトリ公開、ブランチ保護の変更は実行前に確認。Project 作成と Issue 起票は承認後の M0 で実行。

## 検証

- Unit: conformance_engine はテーブル駆動＋決定性（同一入力→同一出力・finding id 安定）＋「必須ルールに UNKNOWN/REVIEW が残れば gate 失敗」＋空虚な成功の禁止。path_analyzer は Golden の Python ソースから期待 Code IR（早期 return 後・到達不能分岐・前後状態）。gateway は「拒否が実行より先・監査に残る」。`conformance` の import 制約。
- Agent: `ScriptedLlm` で上限・拒否・費用枠超過・analyze_focus の再解析を決定的にテスト。実 LLM は `@pytest.mark.live`。
- 冪等性: 再配信・再送で Check・コメント・課金が重複しない。
- Golden: `make eval` が verdict × category の混同行列と反例一致率を出し、**対応範囲外を適合扱いにしない・誤った VERIFIED 0 件**を CI で強制。PoC 17.3 の指標（要求数・承認数・対応範囲数・判定済み数・未解析数を併記）。
- ローカル E2E: `speclino check --repo . --base main --head demo/...`（GitHub なし）→ compose + トンネル + 開発用 App で実PR。
- 本番: `scripts/smoke_prod.sh`（ログイン→POST /review-runs→ポーリング→デモPR 1 の VIOLATED と反例を assert→replay 一致）。Cloud Trace で ADK span。UI は Chrome DevTools MCP で確認。

## 主なリスク

| リスク | 回避策 |
|---|---|
| 締切・過大スコープ | PoC の M1→M2 順（手書き IR で検査器を先に固定）を守り、Golden 12 で早期に品質を固定。Should は上から順、10/9 切捨て、10/13 フリーズ |
| path_analyzer の複雑さ | 部分集合を厳守し、対象外は UNKNOWN。記号的状態は前後名前空間で分離。経路上限で打ち切り |
| Z3 の可搬性・性能 | `z3-solver` の py3.12 wheel（macOS arm64 / linux x86_64）を M0 で確認。ソルバー timeout→UNKNOWN。有限ドメイン検算を Should で |
| フェイルクローズによる摩擦 | verdict は正直に保ち、ゲート失敗の理由と具体策を必ず表示。CLI は advisory 既定 |
| ADK 2.x の変化 | 2.9.x 固定・`uv.lock`。LlmAgent / Plugin / Runner の最小面。`google-adk[mcp]` は入れない |
| サンドボックス（Preview） | デモ 1〜5 は非依存。1日 PoC、不成立なら `disabled` |
| 費用暴走 | 費用ガード、サイズ上限、max=2、再実行回数制限 |
| 公開リポジトリ | gitleaks を CI に。`docs/` は管理外 |

## 外部仕様の確認状況

確認済み: google-adk 2.9.2（Plugin フック、`App(plugins=)`）、`gemini-3.8-flash` と `GOOGLE_GENAI_USE_ENTERPRISE`、Checks API と必須チェックの充足条件、Cloud Run sandboxes（Preview）、mcp 2.2、Cloud Tasks deadline と再配信、Cloud Run timeout 既定300秒、Cloud SQL unix socket、ハッカソン要項。
**未確認**（実装時）: z3-solver の現行版と wheel、Identity Platform の無料枠と Terraform スキーマ、Cloud SQL / Cloud Run の東京単価、`sandbox do` の資源制限フラグ、クーポン起算日。

## レビュー履歴

- **codex フルレビュー**: 事実関係は「基本方針を否定する誤りなし」。11件中 10件採用・1件部分採用（動的証拠の信頼境界: base/head 分離は採用、信頼側別プロセスでの効果観測は不採用→ observed 証拠は別軸表示とし静的 verdict を上書きしない）。
- **ユーザーレビュー①**: 確認不能を最終結果に残さない→エスカレーション＋ゲートのフェイルクローズ／学習モード不要／Identity Platform／適格性 OK。
- **ユーザーレビュー②**: 「意味差分レビュー基盤 PoC v0.1」を意味検証コアの規範として採用。dominator 方式を Spec IR / Code IR + Z3 の経路検査へ置換、Golden 27 ケースを評価基盤に、注文キャンセルを題材に追加、モジュール分割と CLI を PoC に合わせた。verdict（4値、正直）と gate（フェイルクローズ）を分離して両レビューを両立。
- **ユーザーレビュー③**: ドミネーターは第二の証拠源（不在証明・枝刈り）として Should 上位に統合。
- **ユーザーレビュー④**: GitHub Projects でストーリー単位の進捗管理（概要／完了条件／成果／優先度／依存関係、サブタスク可）。`story.sh` と CLAUDE.md で着手・完了の更新を定型化し、依存と優先度から動的に選択・並列化。
