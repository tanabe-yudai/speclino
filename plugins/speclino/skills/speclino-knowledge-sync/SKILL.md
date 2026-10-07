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
   `sync_status(repo_full_name, lock_json)` を呼ぶ。`pull_needed` が false なら取り込みは要らない
2. **計画を受け取る**: `knowledge_pull(repo_full_name, lock_json)` を呼ぶ（lock があれば必ず渡す。
   外れた知識の削除はサーバが lock を確かめたときだけ計画される）
3. **計画を保存する**: 返ってきた JSON を一字も変えずに、リポジトリの外の一時ファイル
   （例: `/tmp/speclino-plan.json`）に保存する
4. **当てる**: リポジトリのルートで `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/speclino_apply.py <計画のファイル>`
   （別の場所からなら `--repo <ルート>`）。結果は JSON 1 行（`written` / `deleted` / `unchanged` /
   `reason`）
5. **結果を確かめる**:
   - 終了コード 0: 取り込めた。一時ファイルを消す
   - 終了コード 2: 前提が崩れたので何も書いていない（手で直したファイル・利用者のファイルとの衝突・
     symlink・管理区画の印の崩れ・remote の不一致など）。`reason` をそのまま利用者に伝えて判断を仰ぐ。
     ファイルを消す・直すなどで勝手に回避しない
   - 終了コード 1: 予期しない失敗で、元に戻した。`reason` を利用者に伝える
6. **コミットする**: `.speclino/lock.json` と `written` / `deleted` のパスを、作業の変更と同じ PR に
   含める（ゲートは PR の lock とファイルを照合する）

## 守ること

- lock・書き出されたファイル・AGENTS.md / CLAUDE.md の `<!-- speclino:begin … -->` 〜
  `<!-- speclino:end -->` の中を手で書き換えない（ゲートで UNKNOWN になる）。直したい内容は
  SpecLino で改訂を出して承認してもらい、もう一度取り込む。区画の外は自由に書いてよい
- 計画の中身を自分でファイルに書き写さない。必ず実行スクリプトに渡す
- `knowledge_pull` が大きすぎると断ったときは、利用者に CLI の `speclino sync`
  （`SPECLINO_SYNC_TOKEN` に sync トークン）を頼む
