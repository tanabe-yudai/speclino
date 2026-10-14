---
name: speclino-spec-authoring
description: SpecLino で検査できる仕様（Markdown と厳格な UML）を書き、承認済みの仕様と規約に沿って実装し、検査の結果（VIOLATED / UNKNOWN / REVIEW）を読む手順。仕様の追加・変更の下書き、要求 ID と図の要素 ID の付け方、チーム規約の読み方、ゲートの要確認の確定の仕方が必要なときに使う。
---

# SpecLino の仕様を書く

<!-- mcp-instructions:start -->
SpecLino はチームの知識（規約・スキル・指示書・仕様）を承認済みの bundle として配り、仕様（Markdown と
UML）とコードを Z3 で照合して VERIFIED_IN_SCOPE / VIOLATED / UNKNOWN / REVIEW の 4 値を出す。
作業の前に `sync_status` で `.speclino/lock.json` の鮮度を確かめ、`pull_needed` なら `knowledge_pull` が返す
実行スクリプト（`apply_script`）に `--plan-url` を渡して当て、lock ごと作業の PR にコミットする（手順はスキル
`speclino-knowledge-sync`。ファイルや lock を手で書かない）。実装の前に `context_get` で規約を、`spec_get` /
`skill_get` で承認済みの仕様・スキルを読む。検査は `verification_start` → `verification_get`。
仕様を書いたら `spec_draft_save` で下書きとして保存し、返る `editor_url` を人に渡して承認を頼む。
あなた（LLM）は証拠を集め提案を書くだけで、判定も承認もしない（verdict を書き換える手段は無い）。
知識の承認は人が SpecLino の画面で行い、ゲートの要確認の確定は承認者が PR に `/speclino attest` と
コメントするか PR を Approve する。
<!-- mcp-instructions:end -->

## SpecLino エージェントに相談する（A2A）

仕様の書き方や判定の理由を、関数呼び出しではなく会話で相談したいときは、SpecLino の議論役
（A2A のエージェント）に聞く。役割は次のとおり分ける。

- **A2A（会話）**: 仕様の下書きのレビュー、要求の意味や判定の理由の説明、検査の後の聞き取り。
  あいまいな点は質問が返り（入力待ち）、同じタスクに文章で答えると続きが進む
- **MCP（関数呼び出し）**: `context_get` / `verification_start` / `verification_get`、
  チームの知識を読む `sync_status` / `knowledge_pull` / `spec_get` / `skill_get`、仕様の下書きを保存する
  `spec_draft_save`。語彙の取得と検証の実行・結果の読み取りは、こちらで行う

接続先は Agent Card（`https://<your-host>/.well-known/agent-card.json`、認証不要）に書いてある。
`/a2a` への送信にはエージェント接続トークン（`slmcp_…`。設定 → エージェント接続で owner が発行）を Bearer で付ける。

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
検査器だけが行い、知識の承認は人が SpecLino の画面で、要確認の確定は承認者が PR で行う。

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
- `level`: `mandatory` は違反・未検証・要確認があるとゲートが落ちる。`advisory` は警告
- `verifier`: `null` の規約は機械検証されない（助言のみ）。`llm.checklist` は LLM 支援で、判定は常に REVIEW
  （人の確定まで必須ならゲートを止める）
- `ignored_project_policies`: チーム規約と同じ id なので無視されたプロジェクト側の規約。チーム規約は
  プロジェクト側で上書き・無効化できない。免除が必要なら `.speclino/exceptions.yaml`（期限・承認者つき。
  `exceptions_allowed: false` の規約は免除できない）

## 1. 承認済みの仕様を読む

仕様の正本は SpecLino の bundle にある。リポジトリの `.speclino/specs/<name>.md` は sync で書き出した
読み取り専用の写しで、ゲートは写しではなく bundle の仕様で検査する（写しを直すと lock の照合で UNKNOWN）。
本文は `spec_get(repo_full_name, name)` で読む。仕様にはワークスペース全体のもの（要求仕様など、全リポジトリに
当たる）とリポジトリ別のもの（振る舞いなど）がある。

## 2. 仕様を書く（Markdown ＋ UML）

文法の正本は `src/speclino/spec/uml/grammar.md`（parser は決定的・全か無か）。要点:

- 図は Mermaid の部分集合 4 種: クラス図（語彙・構造・関係）、シーケンス図（入口ごとの呼出しの順序）、
  状態遷移図（遷移の関係）、アクティビティ図（`flowchart`。1 つの操作の制御フロー。`%%@ closed: true` のときだけ
  図に無い処理を違反にする）
- 各図の前に `%%@ id: <図 ID>`。シーケンス図・アクティビティ図は `%%@ entry: <クラス>.<メソッド>`、状態遷移図は
  `%%@ attribute: <変数名>`（例 `order.status`）
- **全要素に明示的な ID**（`[m1]`・`[t1]`・`[e1]`・関係は `A --> B : [r1] …`）。表示ラベルは ID にしない
- **ガードは式だけ**で、`before.` / `after.` / `outcome.` の名前空間が必須。自然言語のガードはエラー
- 参照する名前はクラス図か `domain` 節に宣言する。モジュール直下の関数は `<<module>>` のクラスの操作として書く
- 対応しない構文（`par`・`critical`・`subgraph`・`Note` など）や読めないトークンが 1 つでもあれば全体がエラー
- 要求の表（`| ID | level | 要求 | 形式化 |`）の `形式化` に `図ID#要素ID` を書くと、その要素の義務の判定が要求に
  結びつく。空か `-` は未形式化として追跡だけされる
- 1 要求 = 1 判定単位。「適切に」「速やかに」は書かない。決められないことは観測境界に「未確定」と書く

書いたら `spec_suggest(markdown, repo_full_name?)` で書き換えの提案と当てた後の診断を受けられる（IR は返さない。
提案は人に見せて 1 件ずつ採否を決める）。

書き上げたら `spec_draft_save(repo_full_name, name, markdown, note?, expected_revision?, expected_hash?)` で
SpecLino に下書きとして保存する（ワークスペースの MCP トークンだけ。作者はトークンを発行した人）。

- 新しい名前なら、そのリポジトリに当たる仕様の改訂 1 ができる
- 既にある仕様を直すときは、`spec_get` か前回の `spec_draft_save` が返した `revision` / `content_hash` を
  `expected_revision` / `expected_hash` に渡す。違うと言われたら他の人の下書きがあるので、上書きせず人に確かめる
- 返り値の `errors` が 0 でなければ `diagnostics` を見て直し、同じ名前で保存し直す（承認はエラー 0 が条件）
- `note` には何をなぜ変えたかを書く（承認者が読む）。保存したら `editor_url` を人に渡し、画面で承認を頼む

画面（エディタの「AI と作る」を含む）からも下書きを保存できる。owner / approver が承認すると新しい bundle になる。
**リポジトリのファイルを直しても、下書きを保存しても、承認にはならない。**

## 3. 検査して読む

```sh
speclino check --repo . --head WORKTREE --specs-dir .speclino/specs --out build/review   # ローカル（LLM・DB なし）
```

MCP なら `verification_start(repo_full_name, head_sha, base_sha)` → `verification_get(run_id)`（bundle の仕様で検査する）。

- `VIOLATED`: 反例（条件と実装の観測）が付く。仕様が正しいならコードを直す。仕様が古いなら画面で改訂して承認を通す
- `UNKNOWN`: 対応外の構文（要素数が静的に分からないループ、`getattr` など）・対応の見つからない操作・契約の無い外部呼出し・
  上限・lock の不整合。Finding の `next_steps` の具体策を行う。UNKNOWN は確定でも免除でも通らない（フェイルクローズ）
- `REVIEW`: LLM 支援の規約、bindings・契約・免除・プロジェクト規約の変更（`KNOWLEDGE-BINDINGS-001`）、lock が古い、
  全体の仕様の図が head で対象外になった、など。人の確定が要る

## 4. 確定（人が行う）

ゲートの要確認は、プロジェクトの承認者（GitHub のユーザー ID で照合）が PR に引数なしの `/speclino attest` と
コメントするか、PR を Approve すると、その PR の最新の検査の分がまとめて確定する（最新のコミットの検査が完了している
ときだけ）。1 件ずつ・問題として記録する（`ack`）ときは `/speclino attest <指紋> <sha256:…> [ack]`（形はゲートの
コメントの根拠に出る）。確定は verdict を書き換えず、内容のハッシュに結びつく。

`/speclino approve-spec <sha256:…>` は、リポジトリの `specs/ir` に置く旧形式の Spec IR の要求を PR で承認するための
コマンドで、今も使えるが旧形式専用。UML の仕様の承認には使わない（画面で承認する）。
