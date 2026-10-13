# アーキテクチャ

Cloud Run の役割分担と、判定が起きる順序。

![SpecLino のアーキテクチャ（概要）](diagrams/overview.png)

以下は詳細な構成図と、判定の順序。

Cloud Run を役割別（web / worker / sandbox）に分け、秘密と権限を分離する。

```mermaid
flowchart TB
  subgraph USERS["チーム"]
    direction LR
    DEV["開発者・承認者<br/>ブラウザ（知識・規約・可観測性）"]
    ED["各自のコーディングエージェント<br/>（Claude Code / Codex 等）"]
  end

  subgraph REPO["対象リポジトリ（GitHub）"]
    direction LR
    LOCAL["sync の書き出し<br/>.claude/skills ・ .agents/skills<br/>AGENTS.md / CLAUDE.md の管理区画<br/>.speclino/specs ・ lock.json（署名つき）"]
    TRUSTED["デフォルトブランチ（信頼側）<br/>bindings.yaml ・ contracts/<br/>exceptions.yaml ・ プロジェクト規約"]
    GHAPP["GitHub App<br/>webhook ・ Checks API ・ PR コメント"]
  end

  subgraph GCP["GCP プロジェクト（asia-northeast1）"]
    direction TB

    subgraph GCP_RUN["Cloud Run（同一イメージ、SPECLINO_ROLE で役割切替）"]
      direction LR
      WEB["<b>speclino-web</b>（公開）<br/>FastAPI: /api/* ・ /mcp ・ /a2a ・ /api/sync/bundle<br/>/webhooks/github ・ React SPA 配信<br/>知識の改訂・承認 → bundle（署名）"]
      WORKER["<b>speclino-worker</b>（IAM 必須・Cloud Tasks の OIDC からのみ）<br/>bundle を固定 → 仕様（UML/IR）・規約・lock を検査（Z3）<br/>→ 未解決ならエージェント → gate → Checks 更新"]
      SANDBOX["<b>speclino-sandbox</b>（IAM 必須・worker からのみ）<br/>権限ゼロ SA・Rule.tests の ID だけを隔離実行"]
    end

    TASKS[("Cloud Tasks<br/>speclino-runs キュー")]

    subgraph GCP_DATA["データ / 秘密"]
      direction LR
      SQL[("Cloud SQL（PostgreSQL）<br/>artifacts / artifact_revisions / bundles<br/>policy_revisions / attestations / llm_assessments<br/>knowledge_fetches / runs / findings / audit")]
      GCS[("GCS<br/>run-manifest / spec-ir / code-ir<br/>findings / review.md")]
      SECRETS[("Secret Manager<br/>DB 接続・GitHub App 秘密鍵<br/>webhook 秘密・bundle 署名鍵")]
    end

    subgraph GCP_AUX["補助サービス"]
      direction LR
      IDP["Identity Platform<br/>メール / パスワード"]
      VERTEX["Vertex AI（Gemini）<br/>執筆の提案・LLM 支援の規約（常に REVIEW）<br/>証拠収集・確認。変換と判定はしない"]
      OBS["Cloud Logging / Cloud Trace"]
    end

    subgraph GCP_DEPLOY["デプロイ"]
      direction LR
      AR["Artifact Registry"]
      MIGRATE["Cloud Run Job<br/>speclino-migrate"]
    end

    NOTE["原則：UML → IR の変換と判定は決定的（parser と Z3）。<br/>AI は提案と証拠収集だけ。<br/>ゲートは必須ルールに UNKNOWN / REVIEW が残れば失敗（フェイルクローズ）"]
  end

  subgraph GHACTSG["GitHub Actions（CI/CD）"]
    GHACT["GitHub Actions<br/>push（WIF・長期キー無し）"]
  end

  DEV -- "ログイン" --> IDP
  DEV -- "改訂・承認・観測" --> WEB
  ED -- "MCP / A2A<br/>context_get ・ spec_get ・ skill_get<br/>sync_status ・ spec_suggest" --> WEB
  ED -- "speclino sync<br/>（sync トークン）" --> WEB
  ED -. "読み込む" .-> LOCAL
  GHAPP -- "webhook（HMAC 検証・重複排除）" --> WEB
  WEB -- "ID トークン検証" --> IDP
  WEB -- enqueue --> TASKS
  TASKS -- OIDC --> WORKER
  WORKER -- "head と信頼側を取得<br/>Checks API ・ PR コメント" --> GHAPP
  WORKER -- "ツールは ToolGateway 経由・書込みなし" --> VERTEX
  WEB -- "書き換えの提案のみ" --> VERTEX
  WORKER -- "Rule.tests の ID のみ" --> SANDBOX
  WEB --> SQL
  WORKER --> SQL
  MIGRATE --> SQL
  WEB -. "成果物を読んで表示" .-> GCS
  WORKER --> GCS
  WEB -. secret参照 .-> SECRETS
  WORKER -. secret参照 .-> SECRETS

  GHACT --> AR
  GHACT -- "1) migrate job 実行" --> MIGRATE
  GHACT -- "2) サービス更新" --> GCP_RUN

  GCP_RUN -.-> OBS

  classDef note fill:#fef7e0,stroke:#f9ab00,stroke-dasharray: 4 3,color:#333
  class NOTE note
```

判定はこの順序でしか起きない。エージェントは **5 で未解決が残ったときだけ** 呼ばれ、証拠を足して
engine に再評価させる。エージェントの文章は判定に入らない。

```mermaid
flowchart LR
  S0["<b>0 bundle を固定</b><br/>承認済みの仕様・規約<br/>（署名・ハッシュを照合）"]
  S1["<b>1 スナップショット</b><br/>head と信頼側<br/>（デフォルトブランチ）"]
  S2["<b>2 仕様の IR</b><br/>承認時に parser が固定した IR<br/>（UML / 旧形式）"]
  S3["<b>3 規約の検査器</b><br/>決定的な検査器<br/>LLM 支援は REVIEW のみ<br/>lock の鮮度"]
  S4["<b>4 path_analyzer</b><br/>Python 部分集合 → Code IR<br/>経路・イベント・上限"]
  S5["<b>5 conformance</b><br/>Z3 ・ オートマトン照合<br/>欠けた要求は UNKNOWN で補う"]
  S6{"未解決<br/>UNKNOWN / REVIEW<br/>が残るか"}
  S7["<b>6 ADK Agent ⇄ ToolGateway</b><br/>analyze_focus / repo_search<br/>test_run / human_ask"]
  S8["<b>7 gate（フェイルクローズ）</b><br/>確定（attestation）を適用<br/>→ Checks API"]

  S0 --> S1 --> S2 --> S3 --> S4 --> S5 --> S6
  S6 -- "いいえ" --> S8
  S6 -- "はい" --> S7
  S7 -- "証拠を足して再評価<br/>（判定は engine のまま）" --> S5
  S7 -- "回答待ち / 予算切れ" --> S8

  classDef deterministic fill:#e8f0fe,stroke:#1a73e8
  classDef agent fill:#fef7e0,stroke:#f9ab00
  class S0,S1,S2,S3,S4,S5,S8 deterministic
  class S7 agent
```

（画像版: [architecture.png](diagrams/architecture.png) / [pipeline.png](diagrams/pipeline.png)。図の正本は
`docs/diagrams/*.mmd` で、`make diagrams` で作り直す）

ローカルでは同じコードが in-process キュー・ローカル FS・Docker サンドボックスに切り替わる
（`SPECLINO_QUEUE_KIND` / `SPECLINO_BLOB_KIND` / `SPECLINO_SANDBOX_KIND`）。

エージェントが判定を上書きできないのは、(a) 判定を書くツールが存在しない (b) 検査器は型付きの
IR と Evidence しか受け取らず、それを作るのは決定的なモジュールだけ (c) LLM の文章は保存されるだけで
検査器の入力にならない、という構造による。
