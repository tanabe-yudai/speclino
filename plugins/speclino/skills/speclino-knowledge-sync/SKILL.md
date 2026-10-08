---
name: speclino-knowledge-sync
description: チームが SpecLino で承認したスキル・指示書（AGENTS.md / CLAUDE.md の管理区画）・仕様の写しを、MCP から取り込んで手元を最新にする手順。作業を始める前、`.speclino/lock.json` が古い・無いと言われたとき、PR のゲートで KNOWLEDGE-LOCK-001 が REVIEW / UNKNOWN になったときに使う。
---

# チームの知識を取り込む（MCP）

人は知識を承認するだけで、取り込みはエージェント（あなた）が行う。ファイルを自分で書かず、
サーバが署名した計画を同梱の実行スクリプトに渡す。

## 手順

1. **鮮度を確かめる**: `repo_full_name` は `owner/name`（`git remote get-url origin` から）。
   `.speclino/lock.json` があればその本文を文字列のまま `lock_json` に渡して
   `sync_status(repo_full_name, lock_json, instructions_file?, skills_dir?)` を呼ぶ。
   `instructions_file` / `skills_dir` は 2 の `knowledge_pull` と同じ値を渡す（Claude Code は
   `CLAUDE.md` / `.claude/skills`。lock の配置と違えば `layout_change` で `pull_needed` になる）。
   `pull_needed` が false なら取り込みは要らない
2. **計画の URL を受け取る**: `knowledge_pull(repo_full_name, lock_json)` を呼ぶ。lock があれば
   必ず渡す（渡さないと外れた知識の削除が計画されず、古い知識が残る）。応答は `plan_url`
   （1 回だけ・10 分で失効）・`bundle_generation`・`layout`（実際の書き出し先）・`summary`。
   計画の本文は応答に無い。
   書き出し先は取り込む側が選ぶ。`instructions_file`（AGENTS.md / CLAUDE.md）と
   `skills_dir`（.agents/skills / .claude/skills）は、省略すると今の配置を保つ（初回は AGENTS.md と
   .agents/skills）。**Claude Code は初回（lock が無い・旧い配置のとき）に
   `instructions_file="CLAUDE.md"`, `skills_dir=".claude/skills"` を指定する**。それ以外の
   エージェントは省略してよい。配置を変えるときだけ指定する（前の配置のファイルは計画が消す）
3. **当てる**: リポジトリのルートで
   `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/speclino_apply.py --plan-url <plan_url> --expect-generation <bundle_generation>`
   を実行する（別の場所からなら `--repo <ルート>`）。スクリプトが URL から計画を取得して当てる。
   結果は JSON 1 行（`written` / `deleted` / `unchanged` / `warnings` / `reason`）
4. **結果を確かめる**:
   - 終了コード 0: 取り込めた。`warnings` があれば利用者に伝える
   - 終了コード 2: 前提が崩れたので何も変えていない（手で直したファイル・利用者のファイルとの衝突・
     symlink・管理区画の印の崩れ・remote の不一致・URL の失効など）。`reason` をそのまま利用者に
     伝えて判断を仰ぐ。ファイルを消す・直すなどで勝手に回避しない。URL の失効・取得済み（HTTP 404）
     なら 2 からやり直す
   - 終了コード 1: 予期しない失敗。元に戻せなかったときは、前の中身を残した一時ディレクトリが
     `reason` に書いてある。利用者に伝える
5. **コミットする**: `.speclino/lock.json` と `written` / `deleted` のパスを、作業の変更と同じ PR に
   含める（ゲートは PR の lock とファイルを照合する）

## 守ること

- lock・書き出されたファイル・AGENTS.md / CLAUDE.md の `<!-- speclino:begin … -->` 〜
  `<!-- speclino:end -->` の中を手で書き換えない（ゲートで UNKNOWN になる）。直したい内容は
  SpecLino で改訂を出して承認してもらい、もう一度取り込む。区画の外は自由に書いてよい
- 前の配置の古い写し（別のファイルの管理区画、別のルートの同名スキル）が残っているとゲートが
  REVIEW にする（`reasons` に `stale_copy`）。`warnings` に出たディレクトリは、SpecLino の写しなら
  手で消す
- 計画を自分で取得したり、中身をファイルに書き写したりしない。URL を実行スクリプトに渡すだけ
- `plan_url` はトークンを含む。ログや PR・コミットに書かない
