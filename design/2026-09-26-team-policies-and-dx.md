# チーム規約と開発体験（2026-09-26）

> **2026-10-05 追記**: 2 章（正本の置き場所）は `2026-10-05-team-knowledge-registry.md` で置き換えた。仕様・スキル・指示書の正本も SpecLino に置き、リポジトリに残すのは `bindings.yaml`・`exceptions.yaml`・プロジェクト規約だけ（デフォルトブランチから読む）。ほかの章は当時の記録として残す。

S38 / S41 を終えた時点での、製品としての形の整理。判定モデル（`2026-09-22-hackathon-plan.md`）と
意味検証コア（`2026-09-22-semantic-conformance-poc-v0.1.md`）は変えない。ここで決めるのは
**正本の置き場所**と**UI の役割**、そして提出までに作る範囲。

## 1. 開発ループ

SpecLino は**実装しない。検証だけする**。実装するエージェント（Claude Code / Codex 等）は外部で、
SpecLino は「その実装が承認済み仕様とチーム規約に反していないか」を決定的に判定する。

```text
[仕様 PR]   人間（またはエージェント）が specs/*.md を書く
            → CI が spec-extract で Spec IR 候補と診断（W_AMBIGUOUS / W_UNEXTRACTED）を PR に付ける
            → 人間がレビューし、マージ ＝ 承認（approval はデフォルトブランチへのマージ。既存設計）

[実装 PR]   実装エージェントが MCP で「承認済み仕様 ＋ チーム規約」を読んで実装
            → 手元で speclino check（または MCP）→ UNKNOWN / VIOLATED を潰す
            → push → SpecLino のゲート（フェイルクローズ）→ 通ればマージ
```

PR が 2 種類あり、SpecLino の役割が違う。仕様 PR では**検証と差分の提示**、実装 PR では**判定**。
どちらも承認は Git 側（マージ）で起きる。UI は承認を**行わない**。

## 2. 正本の置き場所は「何の真実か」で分ける

| 種類 | 性質 | 正本 | 変更の統治 |
| --- | --- | --- | --- |
| 仕様（Markdown / Spec IR）、bindings、domain、contracts、例外 | **プロジェクト固有**。コードと一緒に変わる | Git（対象リポジトリ） | PR のマージ ＝ 承認 |
| **規約・エージェント制約** | **チーム固有**。全プロジェクトに横断して効く | **SpecLino**（改訂・監査つき） | UI で下書き → 承認者が承認 → 有効化 |

規約をプロジェクトごとの Git に置くと、同じ事実が N か所にできて必ず腐る。GitHub の branch
protection がリポジトリの外にあるのと同じで、チームの統治設定はコードの外に置く。

「Git に置く」の本当の理由は Git ではなく **バージョン・レビュー・監査・再現性** の 4 つ。
チーム規約はこの 4 つを SpecLino 側で満たす:

- **バージョン**: 改訂は不変で連番。過去の改訂は消えない
- **レビュー**: 下書き改訂は承認者（`projects.settings` と同じ承認者 ID の仕組みをチームに持ち上げる）が承認するまで有効にならない
- **監査**: 誰が・いつ・何を変えたかを `audit_events` に残す
- **再現性**: run-manifest に**適用した改訂番号**を記録し、`replay` はその改訂で再評価する。UI で規約を変えても過去の判定は変わらない

## 3. チーム規約のモデル

```text
Team（当面は 1 つ。GitHub App のインストール元 = チーム）
 └─ PolicyRevision（不変・連番・author / approver / created_at / approved_at / status: draft|active|superseded）
      └─ PolicyRule
           id / title / description / level（mandatory | advisory）
           verifier: 登録済み検査器 ID とパラメータ（MAKE-001 の targets 等）| None（human: 助言のみ）
           applies_to: 言語・パスの条件（既存の規約パッケージと同じ）
```

- **適用**: チーム配下の全プロジェクトに自動適用。プロジェクトは `.speclino/policy/` で**追加**はできるが、
  チーム規約を無効化はできない。免除は従来どおり `.speclino/exceptions.yaml`（全項目必須・期限つき）
- **検査器あり / 助言のみ**: 検査器が無いルールは advisory として登録し、UI では「機械検証されていない」と
  明示する。検査器を書けたものから mandatory に上げる。**散文の制約を段階的に機械検証へ移す**経路
- **既存の規約パッケージ**（`.speclino/policy/speclino-core.yaml`）は最初のチーム改訂として取り込む

### エージェントへの配信

実装エージェントは **MCP の `read_context`** でチーム規約を読む（既に語彙・規約・要求を返す口がある）。
CLAUDE.md に規約をコピーしない。「SpecLino から読め」の 1 行にする。チームの制約を 1 か所に置き、
エージェントはそこから読み、SpecLino はそこに照らして検査する。

## 4. UI の役割と画面

UI は**可視化と統治**であって、仕様の編集ではない。読み取り専用の範囲と、統治の範囲を分ける。

| 画面 | 役割 | 読み書き |
| --- | --- | --- |
| **チーム規約** | 改訂の一覧・ルールの編集（下書き）・承認・監査ログ。各ルールの「検査器あり / 助言のみ」と直近 run での成否 | 統治（書き込みは承認フロー経由のみ） |
| **プロジェクト** | インストール済みリポジトリごとに: 承認済み仕様と要求（テスト宣言の有無）、適用されている規約、run 履歴 | 読み取り専用（Git の内容を表示） |
| **run 詳細**（既存を拡充） | 増分解析の再利用、test_run の観測、質問の状態、適用した規約改訂 | 読み取り専用 |
| **設定** | `projects.settings`（fail_on / エージェント有効 / test_run 有効 / 承認者）を型付きで | 統治 |
| **オンボーディング** | GitHub App を入れる → `.speclino/` を置く → PR を開く。各段階の到達状態 | 読み取り専用 |

### 4.1 S47（オンボーディングと設定）で実装した形

- **置き場所**: 設定画面（`/w/:slug/settings`）の「プロジェクト」タブに、project ごとの「はじめ方」と「設定」を並べる。
  ホームとプロジェクト画面の「まだ検査されていない」からここへ案内する
- **設定の型**: `domain/project_settings.ProjectSettings`（`fail_on` / `agent_enabled` / `test_run_enabled` /
  `approvers`）。未定義のキーは拒否。`fail_on` は違反・未検証・要確認の 3 つから外せない（フェイルクローズを
  設定で弱めさせない。表示は固定）。`approvers` は GitHub のログイン名（bot は不可、重複不可、50 人まで）
- **効き方**: worker は run のたびに project の設定を読み、エージェントと test_run は「サーバ全体の設定 AND project の設定」
  （project 側は止める方向にだけ効く）。保存値が型に合わなければ、両方を止めた安全側の値で動き、画面に出す
- **権限**: 読むのはメンバー、変更は owner / approver（それ以外は 403 で、拒否も `project.settings` の監査に残る）。
  非メンバー・別ワークスペースの project は 404。ただし `approvers`（webhook が approve-spec / answer / attest の
  受付に使う）を変えられるのは owner だけ。approver が変えようとすると 403（`approvers_owner_only`、監査に残し何も書かない）。
  approver が承認者を据え置いたまま他の項目を変えるのは通す。画面の承認者の編集欄は owner にだけ出す（codex レビューの指摘）
- **オンボーディングの段階**: web は GitHub App の秘密鍵を持たない（worker だけ）ので、`.speclino/` を GitHub に直接
  問い合わせない。手元の記録から導く: App は `projects.installation_id` があれば完了（無ければ未確認）、
  `.speclino/` は完了した検査があれば完了・最新の検査が `domain.yaml` / `bindings.yaml` / Spec IR の不足で失敗していれば
  「まだ」・それ以外は未確認、初回の検査は完了した検査があれば完了。推測で「ある」「無い」を出さない

## 5. 非目標（提出までにやらない）

- 複数チーム・組織階層・ロール体系（承認者 ID の列挙で足りる）
- UI からの仕様編集・承認（Git 側で行う）
- `.speclino/policy/` の廃止（プロジェクト固有の追加規約として残す）
- トレース計測（S41a、#49 で保留中）

## 6. ストーリー分割と優先順

| ストーリー | 内容 | 優先 | 依存 |
| --- | --- | --- | --- |
| S42（#52） | チーム規約パッケージのコア: DB（team / policy_revisions / policy_rules）、API、worker での適用、manifest 記録、replay | P1 | — |
| S43（#53） | チーム規約の UI: 一覧・編集（下書き）・承認・監査ログ | P1 | S42 |
| S44（#54） | MCP でチーム規約を配信（`read_context` 拡張、SKILL.md 更新） | P1 | S42 |
| S45（#55） | プロジェクト画面（仕様・要求・規約適用・run 履歴、読み取り専用） | P1 | — |
| S46（#56） | 仕様 PR bot（CI で spec-extract の候補と診断を PR にコメント） | P2 | — |
| S47（#57） | オンボーディングと設定画面 | P2 | S45 |
| S48（#58） | デモ 5: 対応外構文で UNKNOWN → test_run → 宣言済みテスト失敗 → VIOLATED（observed） | P1 | #38 |
| S49（#59） | `@pytest.mark.spec` と Spec IR `tests` の突き合わせ検査器（SPEC-TEST-001） | P1 | — |
| S50（#60） | `test_run` の判定ロジックを自己検証の対象に（R-TEST-01〜03） | P2 | #38 |
| S51（#61） | Cloud Run sandbox の資源を親サービス側で締める（CPU / メモリ / concurrency / max_instances） | P1 | #38 |

**順序**: S42 → S43 / S44（並列）→ S48 → S49 → S51 → S45 → S50 → S46 / S47。
S40（提出物）は 10/8 に着手。それまでに S42〜S44・S48・S49・S51 を目標にし、S45 以降は入れば入れる。

## 7. 判断の記録

- 規約をプロジェクト単位の Git で持たない（ユーザー判断、2026-09-26）。同じ事実を N か所に置かない
- UI は承認を行わない。仕様の承認は Git、規約の承認は UI 側の承認フロー。**どちらも「承認なしに何も変わらない」**
- チーム規約の変更は過去の判定を変えない（manifest に改訂を記録、replay はその改訂を使う）
