---
name: speclino-spec-authoring
description: SpecLino で検査できる仕様（specs/*.md と Spec IR）を書き、候補を抽出し、検証して承認に回すまでの手順と、実装前にチーム規約を読む手順。仕様の追加・変更、承認済み仕様に沿った実装、要求 ID の付け方、Spec IR の語彙、チーム規約、検証結果（VIOLATED / UNKNOWN / REVIEW）の読み方が必要なときに使う。
---

# SpecLino の仕様を書く

<!-- mcp-instructions:start -->
SpecLino は「仕様（Spec IR）」と「コード（Code IR）」を Z3 で突き合わせ、要求ごとに
VERIFIED_IN_SCOPE / VIOLATED / UNKNOWN / REVIEW の 4 値を出す。実装の前に `context_get` で
語彙・既存の要求・チーム規約を読み、`verification_start` で検証を開始し、`verification_get` で
結果を読む。LLM（あなた）は証拠を集め候補を書くだけで、判定も承認もしない。判定はエンジンだけが
行う（verdict を書き換える手段は無い）。承認は人が PR コメントで行う（`/speclino approve-spec`）。
承認済みの判定は書き換えない。チームが承認したスキル・指示書・仕様は `skill_get` / `spec_get`
で読む。作業の前に `sync_status` で手元の `.speclino/lock.json` の鮮度を確かめ、`pull_needed` なら
`knowledge_pull` が返す最新の実行スクリプト（`apply_script`）に `--plan-url` を渡して当て、lock ごと作業の
PR にコミットする
（手順はスキル `speclino-knowledge-sync`。ファイルや lock を手で書かない）。
<!-- mcp-instructions:end -->

## SpecLino エージェントに相談する（A2A）

仕様の書き方や判定の理由を、関数呼び出しではなく会話で相談したいときは、SpecLino の議論役
（A2A のエージェント）に聞く。役割は次のとおり分ける。

- **A2A（会話）**: 仕様の下書きのレビュー、要求の意味や判定の理由の説明、検査の後の聞き取り。
  あいまいな点は質問が返り（入力待ち）、同じタスクに文章で答えると続きが進む
- **MCP（関数呼び出し）**: `context_get` / `verification_start` / `verification_get`、
  チームの知識を読む `sync_status` / `knowledge_pull` / `spec_get` / `skill_get`。語彙の取得と検証の実行・結果の
  読み取りは、こちらで行う

接続先は Agent Card（`https://<your-host>/.well-known/agent-card.json`、認証不要）に書いてある。
`/a2a` への送信にはワークスペースの MCP トークン（`slmcp_…`）を Bearer で付ける。

スキルは、メッセージの本文の先頭に YAML front matter を書いて選ぶ（DataPart で
`{"skill": ...}` を送れるクライアントはそれでもよい）。本文は front matter の後ろに続ける。

`spec_review`（仕様の Markdown の下書きを検証し、候補・診断・修正案を返す。書き込みはしない）:

```text
---
skill: spec_review
repo: owner/name
behavior: orders.cancel
path: specs/orders.md
---
### R-CANCEL-01
注文は発送前ならキャンセルできる
```

`requirement_consult`（要求の意味・判定の理由を説明する。`commit` を付けると検査を起動する）:

```text
---
skill: requirement_consult
repo: owner/name
requirement_id: R-CANCEL-01
commit: 0123abc
---
この要求は発送済みの注文にも効く？
```

`run_clarification`（検査の「要確認」の点を質問し、答えを補足として記録する）:

```text
---
skill: run_clarification
run_id: 00000000-0000-0000-0000-000000000000
---
要確認の点を聞かせて
```

入力待ちに返信するときは、スキルを指定し直さず、返ってきた `task_id` / `context_id` を付けて
本文だけを送る。

**補足は正式な回答ではない。** 聞き取りで記録した補足は判定にもゲートにも効かない。判定は
検査器だけが行い、承認は人が PR で行う。

## チームの知識を取り込む（`knowledge_pull` / `speclino sync`）

チームが SpecLino で承認したスキル・指示書・仕様は、リポジトリごとに配信される。エージェントは
MCP の `knowledge_pull` で計画の取得 URL と最新の実行スクリプト（`apply_script`）を受け取り、
`--plan-url` を渡して当てる（手順は
スキル `speclino-knowledge-sync`）。人が手元で取り込むときは CLI の `speclino sync` を使う。
どちらも同じ計画と同じ実行スクリプトで書き出す。

- **書き出し**: リポジトリのルートで `speclino sync --url https://<your-host> --repo-id <GitHub のリポジトリ ID>`
  （2 回目からは `--repo-id` を省ける）。トークンは環境変数 `SPECLINO_SYNC_TOKEN` に sync 用の
  トークン（`slsync_…`。ワークスペースの owner / approver が発行し、1 つのリポジトリに限る）を入れる。
  引数には書かない
- **書き出し先**: スキルは `.claude/skills/<name>/` と `.agents/skills/<name>/`、指示書は
  `AGENTS.md` / `CLAUDE.md` の `<!-- speclino:begin bundle=<n> -->` 〜 `<!-- speclino:end -->` の
  区画（区画の外は書き換えない）、仕様は `.speclino/specs/<name>.md`（読み取り専用の写し）。
  最後に `.speclino/lock.json`（どの bundle を宣言しているか、書き出したファイルのハッシュ、署名、
  30 日の期限）を書く
- **手で直さない**: 書き出したファイルや区画の中身を直すと、PR のゲートで lock の照合
  （`KNOWLEDGE-LOCK-001`）が UNKNOWN になる。直したいときは SpecLino で改訂を出して承認を通し、
  もう一度取り込む。取り込みは、手で変えたファイルや利用者のファイルを上書き・削除せずに、何も書かず
  止まって理由を出す
- **MCP**: `sync_status(repo_full_name, lock_json)` に lock の本文を渡すと、今の承認済みの知識と
  比べて `pull_needed`・`reasons`・`verdict`・`changed`（変わった知識）を返す（判定はゲートと同じ）。
  `knowledge_pull(repo_full_name, lock_json)` は署名つきの書き出し計画の使い捨ての取得 URL
  （1 回だけ・10 分）を返す。lock を渡さないと削除は計画されない。
  `spec_get` / `skill_get(repo_full_name, name)` は承認済みの本文を返す。sync 用トークンでも
  呼べるが、検証の開始（`verification_start`）はできない
- **MCP**: `spec_suggest(markdown, repo_full_name?)` は仕様の本文の書き換えの提案（本文の位置と置換・
  理由・当てた後の診断）を返す。IR は返さない（IR は SpecLino の parser が本文から作る）。提案は人に
  見せて 1 件ずつ採否を決め、勝手に当てない。sync 用トークンでは呼べない

## 0. チーム規約を読む（仕様を書く前にも、実装する前にも）

チームの規約（Makefile の必須ターゲット、仕様の書式、層の依存制約など）は SpecLino が正本を持ち、
ゲートはそれに照らして検査する。リポジトリの CLAUDE.md / AGENTS.md に写しがあっても、正は
SpecLino 側。MCP の `context_get(repo_full_name)` の `policies` を読んでから作業する。

- `origin`: `team`（チームの承認済み改訂。`team_policy` に改訂番号）か `project`（`.speclino/policy/` の追加規約）
- `level`: `mandatory` は違反・未検証があるとゲートが落ちる。`advisory` は警告
- `verifier`: `null` の規約は機械検証されない（助言のみ）。検査で落ちなくても守る対象であることは変わらない
- `ignored_project_policies`: チーム規約と同じ id なので無視されたプロジェクト側の規約。チーム規約は
  プロジェクト側で上書き・無効化できない。免除が必要なら `.speclino/exceptions.yaml`（期限・承認者つき。
  `exceptions_allowed: false` の規約は免除できない）

## 1. 語彙を先に読む

仕様で使ってよい名前は `.speclino/domain.yaml`（変数・列挙・opaque 型）と
`.speclino/bindings.yaml`（振る舞い `behaviors` と操作 `operations`）に宣言されたものだけ。
MCP が使えるなら `context_get(repo_full_name)` で同じ内容と既存の要求一覧が取れる。

- 変数参照: `before.<変数>`（入口の値）、`after.<変数>`（終了時の値）、`outcome.status`（HTTP 状態）
- 列挙値は宣言された値だけ（例 `OrderStatus.PENDING`）。「未発送」のような自然言語で状態集合を作らない
- 操作は `operations` のキー（例 `inventory_restore_command`）。コードのシンボル名は書かない
- 語彙に無い概念が必要なら、仕様を近似せず domain / bindings への追加を提案する

## 2. Markdown を書く（SPEC-FMT-001）

`specs/<名前>.md` に次の節を必ず置く。要求 ID は `### <ID>` 見出しで宣言し、重複させない。

```markdown
---
id: SPEC-XXX-001
version: "1.0"
owner: <担当>
---

# <対象の振る舞い>

## 前提
- 入口までに済んでいること、検査しないこと

## 要求
### R-XXX-01
- 条件: `before.…` で書ける入口条件
- 期待する結果: 状態（after）・応答（outcome）・操作の呼出し回数と引数

## 観測境界
- 何を観測して判定するか、何が対象外か（永続化・外部サービス内部など）
```

書き方の規則:

- 1 要求 = 1 判定単位。「〜し、〜しない」は 2 つの要求に分ける（`R-X-01` と `R-X-01b`）
- 条件は入口の値だけで書く（`before`）。期待する結果は終了時の値・応答・呼出し回数で書く
- 「適切に」「速やかに」「必要に応じて」は書かない。決められないなら前提か観測境界に「未確定」と明記する
- 拒否経路の「〜しない」は回数 0 の要求として明示する（open-world では未記載の呼出しは REVIEW 止まり）

## 3. Spec IR の候補を作る

```sh
speclino spec-extract --spec specs/<名前>.md --behavior <behaviors のキー> \
  --project . --out specs/ir/<名前>.spec-ir.json
```

- 出力は候補（`approval_ref` 無し）。`W_AMBIGUOUS`（曖昧語）と `W_UNEXTRACTED`（語彙で表せない要求）は
  本文を直すか語彙を足して解消する。`E_REF_UNDEFINED` 等の error が残る候補は承認できない
- 手で書く／直すときは `schemas/spec-ir.schema.json` に従う。式は JSON の木（`ref` / `const` /
  `not` / `and` / `or` / `eq` / `ne` / `lt` / `le` / `gt` / `ge` / `add` / `sub`）で、Python の式文字列は書かない
- 呼出しの要求は `event_requirement`（`event_kind: call`, `phase: invoked`, `count: {operator, value}`,
  必要なら `arguments_equal`）。状態・応答は `expression`

## 4. 検証して読む

```sh
speclino check --repo . --head WORKTREE --out build/review   # ローカル
speclino check --repo . --base main --head HEAD --out build/review  # base との意味差分も出す
```

MCP なら `verification_start(repo_full_name, head_sha, base_sha)` → `verification_get(run_id)`。

- `VIOLATED`: 反例（条件と実装の観測）が付く。仕様が正しいならコードを直す。仕様が古いなら本文を直して再承認
- `UNKNOWN`: 対応外の構文・契約の無い外部呼出し・上限。Finding の `next_steps` にある具体策
  （契約の登録、構文の単純化、テストの宣言）を行う。UNKNOWN のまま通過はしない（フェイルクローズ）
- `REVIEW`: 未承認の解釈、削除された要求、近似された経路、仕様に無い操作の呼出し。人の確認が要る
- `semantic-diff.json` / review.md の「意味差分」で、この変更が新規違反・解消・判定不能化のどれを
  起こしたかを見る

## 5. 承認

Spec IR の追加・変更は PR で行い、承認者が PR コメントで `/speclino approve-spec <sha256:…>` を書く
（ハッシュは Check の指摘に出る）。削除は `/speclino approve-spec removed:<sha256:…>`。
デフォルトブランチにマージ済みの要求は base 由来として自動的に承認済み扱いになる。
