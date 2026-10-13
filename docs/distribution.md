# 知識を配る

`speclino sync`・MCP・A2A・Claude Code プラグイン。

## `speclino sync` と lock

設定 → 「エージェント接続」→「リポジトリ同期（speclino sync）」で、プロジェクト限定・読み取り専用の
リポジトリ同期トークン（`slsync_…`）を発行する（発行・失効は owner / approver）。トークンは環境変数だけで渡す（引数・lock・ログには出さない）。

```sh
export SPECLINO_SYNC_TOKEN=slsync_...
uv run speclino sync --url https://<your-speclino> --repo . --repo-id <GitHub のリポジトリ ID>   # 2 回目以降は --repo-id 不要
```

| 書き出し先 | 中身 |
| --- | --- |
| `.claude/skills/<name>/`、`.agents/skills/<name>/` | スキル |
| `AGENTS.md` / `CLAUDE.md` の管理区画（`<!-- speclino:begin bundle=<n> -->` 〜 `<!-- speclino:end -->`） | 指示書。区画の外は触らない |
| `.speclino/specs/<name>.md` | 仕様の読み取り専用の写し（判定には使わない） |
| `.speclino/lock.json` | bundle の世代、各メンバーの改訂と内容のハッシュ、書き出したファイルのハッシュ、サーバの署名、期限（30 日） |

lock の意味は「この checkout が宣言している bundle」で、**利用の証明ではない**。書き出しはパスを検査し
（`..`・絶対パス・symlink・許可リスト外は拒否）、一時ディレクトリに作ってから置き換える。失敗すれば手元の
ファイルと lock は前のまま。lock をコミットすると、ゲートが鮮度を確かめる（`KNOWLEDGE-LOCK-001`）。

## MCP / A2A / Claude Code プラグイン

- **MCP**（`/mcp`）: `context_get`（規約）、`spec_get` / `skill_get`、`sync_status`（lock が最新か）、`spec_suggest`
  （仕様の書き換えの提案）、`knowledge_pull`（取り込みの計画）、`verification_start` / `verification_get`。読み取り系は
  リポジトリ同期トークンでも呼べ、検査の開始はエージェント接続トークン（`slmcp_…`、設定 →「エージェント接続」で owner が発行）に限る
- **エージェントの取り込み**: 人は承認するだけで、手元への書き出しはエージェントが行う。`sync_status` で lock の鮮度を
  確かめ、`pull_needed` なら `knowledge_pull` が返す署名つきの計画（`plan_url`、1 回だけ・10 分で失効）を、サーバが配る
  実行スクリプト（`apply_script` の URL と sha256）に渡して当て、lock ごと作業の PR にコミットする（スキル
  `speclino-knowledge-sync`）。書き出し先はクライアントの名乗りで決まる（Claude Code は `.claude/skills`、それ以外は
  `.agents/skills`。CLAUDE.md があれば管理区画に `@AGENTS.md` の橋を書く）
- **A2A**（`/a2a`、Agent Card は `/.well-known/agent-card.json`）: 仕様のレビュー・要求の相談・仕様の下書きの相談・
  検査の後の聞き取り。返すのは提案だけで、判定も承認もしない。`uv run speclino a2a-send --help` が最小のクライアント
  （サーバ側は `SPECLINO_A2A_ENABLED=true`）
- **Claude Code プラグイン**: [`plugins/speclino/`](../plugins/speclino/) が Skill（仕様の書き方・知識の取り込み）と
  MCP 接続を配る。`claude plugin marketplace add <owner>/<repo>` → `claude plugin install speclino@speclino`。
  接続先とトークンは `SPECLINO_MCP_URL` / `SPECLINO_MCP_TOKEN` の環境変数で渡し、`.claude/settings.json` に書かない

取得は記録され（`knowledge_fetches`）、可観測性の画面で「誰が・どのトークンで・どの世代を最後に取ったか」として見える。
