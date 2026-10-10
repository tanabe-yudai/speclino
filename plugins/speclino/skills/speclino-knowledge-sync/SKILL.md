---
name: speclino-knowledge-sync
description: チームが SpecLino で承認したスキル・指示書（AGENTS.md の管理区画と CLAUDE.md の橋）・仕様の写しを、MCP から取り込んで手元を最新にする手順。作業を始める前、`.speclino/lock.json` が古い・無いと言われたとき、PR のゲートで KNOWLEDGE-LOCK-001 が REVIEW / UNKNOWN になったときに使う。
---

# チームの知識を取り込む（MCP）

人は知識を承認するだけで、取り込みはエージェント（あなた）が行う。ファイルを自分で書かず、
サーバが署名した計画を、サーバが配る実行スクリプトに渡す。

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
3. **当てる**: 実行スクリプトは `knowledge_pull` の `apply_script`（`url` と `sha256`）でサーバが
   最新のものを配る（プラグインの更新に依存しない）。リポジトリのルートで、下の Python を
   `python3 - <apply_script.url> <apply_script.sha256> <plan_url> <bundle_generation>` に heredoc で
   渡して実行する。取得 → sha256 の確認 → 一時ファイルに書いて実行 → 削除までを行い、
   終了コードは実行スクリプトのものをそのまま返す（一致しなければ実行しない）。結果は JSON 1 行
   （`written` / `deleted` / `unchanged` / `warnings` / `reason`）

   ```python
   import hashlib, os, subprocess, sys, tempfile, urllib.request as u
   from urllib.parse import urlparse

   url, sha, plan, gen = sys.argv[1:5]
   scheme, host = urlparse(url).scheme, urlparse(url).hostname
   local = scheme == "http" and host in ("localhost", "127.0.0.1")
   if scheme != "https" and not local:
       sys.exit("実行スクリプトの URL は https だけ")


   class NoRedirect(u.HTTPRedirectHandler):
       def redirect_request(self, *args, **kwargs):
           raise RuntimeError("リダイレクトは辿らない")


   body = u.build_opener(NoRedirect).open(url, timeout=30).read(1024 * 1024 + 1)
   if len(body) > 1024 * 1024 or hashlib.sha256(body).hexdigest() != sha:
       sys.exit("サイズか sha256 が合わない。実行しない")
   fd, path = tempfile.mkstemp(suffix=".py")
   try:
       with os.fdopen(fd, "wb") as f:
           f.write(body)
       cmd = [sys.executable, path, "--plan-url", plan, "--expect-generation", gen]
       sys.exit(subprocess.run(cmd + (["--allow-http"] if local else [])).returncode)
   finally:
       os.unlink(path)
   ```

   取得できない・sha256 が合わないときは、予備としてプラグイン同梱の
   `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/speclino_apply.py --plan-url <plan_url> --expect-generation <bundle_generation>`
   を使う（リポジトリに `plugins/speclino/scripts/speclino_apply.py` があればそちら。別の場所からなら
   `--repo <ルート>`）。旧い実行スクリプトは新しい計画を「lock の files が計画の書き出しと一致しない」
   などで拒否する（終了コード 2、何も書かない）ので、その場合はプラグインを更新する。
   実行スクリプトの URL も `plan_url` と同じトークンを含む。ログや PR に書かない
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
- 計画を自分で取得したり、中身をファイルに書き写したりしない。URL を実行スクリプトに渡すだけ。
  実行スクリプトも、取得して sha256 が一致したものだけを上の Python で実行する（手で書き直さない）
- `plan_url` はトークンを含む。ログや PR・コミットに書かない
