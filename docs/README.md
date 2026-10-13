# SpecLino のドキュメント

プロジェクトの概要とすぐ試す手順は [README.md](../README.md)。ここは詳細。

| ページ | 中身 |
| --- | --- |
| [concepts.md](concepts.md) | 考え方。何を保証し何を保証しないか、書く・承認・配る・確かめる・観測するの流れ、bundle、open-world |
| [architecture.md](architecture.md) | アーキテクチャ。Cloud Run の役割分担、判定が起きる順序、ローカルでの切り替え、エージェントが判定を上書きできない理由 |
| [getting-started.md](getting-started.md) | はじめる。ローカルでのサーバと画面の起動、CLI での 1 件の検査（オプション・終了コード・違反を作って試す・replay） |
| [knowledge.md](knowledge.md) | 知識を管理する。画面、改訂と承認、AI と作る、UML での仕様の書き方、旧形式の取り込み、`bindings.yaml` |
| [distribution.md](distribution.md) | 知識を配る。`speclino sync` と lock、MCP / A2A、Claude Code プラグイン |
| [verdicts.md](verdicts.md) | 判定を読む。UNKNOWN の読み方、`KNOWLEDGE-*` の Finding、LLM 支援の規約と確定、LLM に送るもの |
| [operations.md](operations.md) | 運用する。可観測性、CI での運用、自己検証、評価、既知の制約 |
| [demo-knowledge-registry.md](demo-knowledge-registry.md) | デモの手順。UML の仕様を書いて違反する PR を検査するまでの通し |
| [runbooks/knowledge-registry-cutover.md](runbooks/knowledge-registry-cutover.md) | 知識レジストリへの切り替えの手順 |
| [guides/github-app-setup.md](guides/github-app-setup.md) | GitHub App の作成と接続 |
| [guides/deploy-runbook.md](guides/deploy-runbook.md) | GCP への初回デプロイと運用 |
| [submission/README.md](submission/README.md) | ハッカソンの提出情報と、12/1 までデプロイを維持する予算計画 |

図の正本は [diagrams/](diagrams/)（`*.mmd`、`make diagrams` で png・svg を作り直す）。
