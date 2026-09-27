---
name: speclino-spec-authoring
description: SpecLino で検査できる仕様（specs/*.md と Spec IR）を書き、候補を抽出し、検証して承認に回すまでの手順と、実装前にチーム規約を読む手順。仕様の追加・変更、承認済み仕様に沿った実装、要求 ID の付け方、Spec IR の語彙、チーム規約、検証結果（VIOLATED / UNKNOWN / REVIEW）の読み方が必要なときに使う。
---

# SpecLino の仕様を書く

SpecLino は「仕様（Spec IR）」と「コード（Code IR）」を Z3 で突き合わせ、要求ごとに
VERIFIED_IN_SCOPE / VIOLATED / UNKNOWN / REVIEW を出す。LLM（あなた）は候補を書くだけで、
判定と承認はしない。判定はエンジン、承認は人（`/speclino approve-spec`）が行う。

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
