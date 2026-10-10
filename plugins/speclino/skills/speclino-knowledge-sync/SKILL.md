---
name: speclino-knowledge-sync
description: チームが SpecLino で承認したスキル・指示書（AGENTS.md の管理区画と CLAUDE.md の橋）・仕様の写しを、MCP から取り込んで手元を最新にする手順。作業を始める前、`.speclino/lock.json` が古い・無いと言われたとき、PR のゲートで KNOWLEDGE-LOCK-001 が REVIEW / UNKNOWN になったときに使う。
---

# チームの知識を取り込む（MCP）

人は知識を承認するだけで、取り込みはエージェント（あなた）が行う。ファイルを自分で書かず、
サーバが署名した計画を同梱の実行スクリプトに渡す。

## 手順

1. **鮮度を確かめる**: `repo_full_name` は `owner/name`（`git remote get-url origin` から）。
   `.speclino/lock.json` があればその本文を文字列のまま `lock_json` に渡して
   `sync_status(repo_full_name, lock_json, local_hashes?, managed_inner?, agent?)` を呼ぶ。
   - `managed_inner` は手元にある AGENTS.md / CLAUDE.md をキーにし、値は管理区画
     （`<!-- speclino:begin … -->` 〜 `<!-- speclino:end -->`）の中身の sha256（区画が無ければ
     空文字）。**Claude Code は CLAUDE.md があれば必ず `CLAUDE.md` のキーを入れる**（CLAUDE.md が
     あると Claude Code は AGENTS.md を読まないので、`@AGENTS.md` の橋があるかを確かめる。
     無ければ `bridge_missing`）
   - 名乗り（スキルの書き出し先）は MCP の clientInfo から自動で決まる。届かないクライアントは
     `agent` に自分の名前（例 `"claude-code"`、`"codex"`）を渡す（2 の `knowledge_pull` も同じ）
   `pull_needed` が false なら取り込みは要らない
2. **計画の URL を受け取る**: `knowledge_pull(repo_full_name, lock_json, agent?)` を呼ぶ。lock が
   あれば必ず渡す（渡さないと外れた知識の削除が計画されず、古い知識が残る）。応答は `plan_url`
   （1 回だけ・10 分で失効）・`bundle_generation`・`layout`（実際の書き出し先）・`summary`。
   計画の本文は応答に無い。書き出し先はサーバが決める（ふつうは引数を足さない）:
   - 指示書の本文は AGENTS.md。CLAUDE.md が手元にあれば、その管理区画に `@AGENTS.md` の橋を
     書く（CLAUDE.md は作らない）
   - スキルは前回のルート（lock の `skill_roots`）に、名乗りのルート（Claude Code なら
     `.claude/skills`、それ以外は `.agents/skills`）を足した全てに同じ内容を書く（減らさない）
   - `skills_dir` はそのルートだけに書く（上限を超えたと言われたときなど）。
     `instructions_file="CLAUDE.md"` は本文を CLAUDE.md だけに書く旧い配置
3. **当てる**: リポジトリのルートで、**最新の実行スクリプト**を使って
   `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/speclino_apply.py --plan-url <plan_url> --expect-generation <bundle_generation>`
   を実行する（別の場所からなら `--repo <ルート>`）。リポジトリに
   `plugins/speclino/scripts/speclino_apply.py` があればそちらを使う（サーバと同じ版）。
   旧い実行スクリプトは新しい計画（橋の `if_exists`）を「lock の files が計画の書き出しと一致しない」
   で拒否する（終了コード 2）ので、プラグインを更新する。スクリプトが URL から計画を取得して当てる。
   結果は JSON 1 行（`written` / `deleted` / `unchanged` / `warnings` / `reason`）
4. **結果を確かめる**:
   - 終了コード 0: 取り込めた。`warnings` があれば利用者に伝える。「git に無視されていて
     コミットされない」の警告は、そのままだとゲートで UNKNOWN になるので、`.gitignore` の直し方
     （例: `.claude/` を `.claude/*` と `!.claude/skills/` に分ける）を利用者に示して判断を仰ぐ。
     「Claude Code が AGENTS.md の指示書を読まない可能性」の警告（`.claude/CLAUDE.md` や
     `CLAUDE.local.md` だけがある）は、ルートに CLAUDE.md を作って取り込み直すか `@AGENTS.md` を
     書くかを利用者に尋ねる
   - 終了コード 2: 前提が崩れたので何も変えていない（手で直したファイル・利用者のファイルとの衝突・
     symlink・管理区画の印の崩れ・remote の不一致・URL の失効・旧い実行スクリプトなど）。`reason` を
     そのまま利用者に伝えて判断を仰ぐ。ファイルを消す・直すなどで勝手に回避しない。URL の失効・
     取得済み（HTTP 404）なら 2 からやり直す
   - 終了コード 1: 予期しない失敗。元に戻せなかったときは、前の中身を残した一時ディレクトリが
     `reason` に書いてある。利用者に伝える
5. **コミットする**: `.speclino/lock.json` と `written` / `deleted` のパスを、作業の変更と同じ PR に
   含める（ゲートは PR の lock とファイルを照合する。CLAUDE.md の橋も `written` に出たら含める）

## 守ること

- lock・書き出されたファイル・AGENTS.md / CLAUDE.md の `<!-- speclino:begin … -->` 〜
  `<!-- speclino:end -->` の中を手で書き換えない（ゲートで UNKNOWN になる）。直したい内容は
  SpecLino で改訂を出して承認してもらい、もう一度取り込む。区画の外は自由に書いてよい
- 前の配置の古い写し（本文を載せていない側のファイルの管理区画、lock の `skill_roots` の外の
  ルートの同名スキル）が残っているとゲートが REVIEW にする（`reasons` に `stale_copy`）。
  `warnings` に出たディレクトリは、SpecLino の写しなら手で消す
- CLAUDE.md があるのに `@AGENTS.md` の橋が無いと、ゲートが REVIEW にする（`bridge_missing`）。
  取り込み直せば橋が入る
- 計画を自分で取得したり、中身をファイルに書き写したりしない。URL を実行スクリプトに渡すだけ
- `plan_url` はトークンを含む。ログや PR・コミットに書かない
