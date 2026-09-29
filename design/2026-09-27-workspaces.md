# ワークスペース（テナント境界）（2026-09-27）

チーム規約（S42）とプラグイン配信（MCP を他人に使わせる）で、「誰がどのリポジトリの仕様・規約・run を
見られるか」が必要になった。この文書はその境界の設計。`design/2026-09-26-team-policies-and-dx.md` の
「チーム」をここで定義し直す。

## 1. 問題

S42 は「`owner/repo` の owner ＝ チーム」とした（`services/policy_governance.team_of`）。これは
組織（organization）のリポジトリでしか成り立たない。

- 個人アカウントのリポジトリにはメンバーの概念が無い。権限はリポジトリごとの collaborator だけ。
  GitHub App のインストールはアカウントにつき 1 つなので、owner 単位で区切ると、リポジトリ A だけを
  共有された人に B も見えてしまう
- 1 つのチームが組織のリポジトリと個人のリポジトリの両方で開発することがある。owner 単位だと規約が割れる

加えて、今の認可は実質 1 テナント: UI は許可リスト（`allowed_users`）に載った人が全 run を見られ、
MCP は静的トークン 1 本で全リポジトリに触れる。自分とデモだけなら安全だが、配って使ってもらう段階で
他チームの仕様・規約・run が漏れる。

## 2. 方針: チームは SpecLino のワークスペースだけ

**「チーム」の正本はワークスペース 1 つに寄せる。** GitHub の組織・チーム・collaborator はチームの
正本にしない（同期もしない）。GitHub は「その人がそのリポジトリを登録してよいか」の確認にだけ使う。

チームの概念を複数の場所で持たないための線引き:

| 何の真実か | 正本 | 使い方 |
| --- | --- | --- |
| 誰がチームのメンバーか | SpecLino（ワークスペース） | 招待で管理 |
| どのリポジトリがチームのものか | SpecLino（ワークスペースへの登録） | リポジトリ単位で登録 |
| その人がリポジトリを登録してよいか | GitHub（リポジトリの admin 権限） | 登録の瞬間に 1 回だけ確認 |
| PR 上の承認（approve-spec） | GitHub（PR コメントの投稿者） | 従来どおり `projects.settings` の承認者 |

## 3. モデル

| テーブル | 内容 |
| --- | --- |
| `workspaces` | `slug`（不変の識別子。run-manifest の `policy_team` に記録する）、表示名 |
| `users` | Identity Platform の uid、メール、GitHub のログイン名と ID（GitHub 連携した人だけ） |
| `workspace_members` | (workspace, user, role)。role は `owner` / `approver` / `member` |
| `workspace_invitations` | 招待（メール宛、期限つき、1 回限り） |
| `projects.github_repo_id` | 追加し、**一意キーを `repo_full_name` からこれに移す**。GitHub は改名後の旧名を別リポジトリに再利用できるため、名前で同一性を決めると改名後に旧ワークスペースへ別リポジトリの run が流れる。webhook・API・MCP・登録はすべて ID で照合し、名前は表示用 |
| `projects.workspace_id` | 追加。**1 リポジトリは高々 1 ワークスペース**（ゲートが照らす規約を 1 つに決めるため）。登録は `WHERE workspace_id IS NULL` の条件付き更新で原子的に取る |
| `mcp_tokens` | ワークスペース単位のトークン。ハッシュだけ保存、失効できる、最終利用時刻 |

- `teams`（S42 の承認者の受け皿）は `workspace_members` の role=`approver` に置き換える。
  改訂の作者が自分を承認者に足せない、という S42 の性質は「role の変更は owner だけ」で保つ
- `policy_revisions.team` はワークスペースの `slug` を入れる。いまの値（owner 名）は同名の
  ワークスペースを作って移行する。排他（改訂作成・承認・seed の直列化）はワークスペース行のロックに
  一本化する（`teams` 行とワークスペース行に分けると S42 の直列化が崩れる）
- `team_of(repo)` は「リポジトリ → 登録先ワークスペースの slug」に置き換える。登録されていない
  リポジトリにはチーム規約を当てない（プロジェクト固有の規約だけで検査する）

## 4. 認可

- **UI / API**: 一覧・詳細・成果物・再実行・run 作成（`POST /review-runs`）・規約の読み書きを、
  ログインした人の所属ワークスペースで絞る。他ワークスペースのものは 404（存在を漏らさない）
- **MCP**: トークンからワークスペースを引き、`context_get` / `verification_start` の
  `repo_full_name` と `verification_get` の `run_id` がそのワークスペースのものかを確かめる。
  全体共通の静的トークン（`SPECLINO_MCP_TOKEN`）は本番では廃止する
- **webhook 起点の run**: run の起動は従来どおり（GitHub の署名が正本）。UI で見えるのは、
  そのリポジトリを登録したワークスペースのメンバーだけ。ただし PR コメントのコマンドは次を必須にする
  - `/speclino answer Q-n`: 質問 → run → project がコメント先のリポジトリ・PR と一致すること。
    子 run の親は同じ project の run に限る（いまは質問を全体から引いていて、別リポジトリの質問に
    回答できてしまう）
  - 承認者: 全体共通の承認者（`settings.approvers`）は廃止し、project ごとの承認者だけにする。
    run の作成は project の設定を書き換えない（いまは `create_run` が毎回 `settings={}` で
    upsert し、project の承認者を消している）
- **許可リスト**: `allowed_users` は廃止した（S53。ユーザー判断 2026-09-27: 許可リストは欲しい機能では
  ない。新規登録は開けたまま、所属ワークスペースで絞る）

## 5. 切り替えの順序

追加のマイグレーションでも、旧コードが残っている間は旧 API が全 run を返し、旧 MCP は共通トークンで
全リポジトリに触れ、旧 worker は owner 名の規約を当てる。そこで:

1. 追加 DDL（新テーブル・列）を先に当てる。この時点では既存の 1 テナント運用のまま
2. API・MCP・worker・seed を新コードに切り替え、旧リビジョンにトラフィックが残っていないことを確かめる
3. そのあとで、他のワークスペース（自分とデモ以外）を作れるようにする

## 6. リポジトリの登録

1. ワークスペースの owner が UI から「リポジトリを追加」→ GitHub App のインストール画面へ
2. インストール時のユーザー認可（GitHub App の user access token）で戻ってくる
3. そのトークンで本人を特定し（`GET /user`）、`GET /repos/{owner}/{repo}/collaborators/{username}/permission`
   で admin であることを確かめてから、リポジトリ ID で `projects.workspace_id` を設定する。
   既に別ワークスペースに登録済みなら拒否
4. 以後そのトークンは保存しない（登録の確認だけに使う）

GitHub 連携をしない人（審査員のテストアカウント）は、メンバーとして招待されるだけで使える。
デモ用ワークスペースとテストアカウントの所属は seed で作る。

## 7. 非目標

- GitHub の組織・チームとの同期（あとから任意機能として足せる形にはしておく）
- 1 リポジトリを複数ワークスペースに登録すること
- ワークスペース単位の課金・費用上限（費用ガードは当面全体で 1 つ）
- SpecLino のユーザーと、approve-spec を書く GitHub ログイン名の突き合わせ（連携済みなら表示だけ揃える）

## 8. ストーリー分割（案）

| ストーリー | 内容 | P | 依存 |
| --- | --- | --- | --- |
| PR コマンドの境界修正（先行） | 回答の project 一致、親子 run の同一 project、全体承認者の廃止、run 作成で設定を消さない。**1 テナントの今でも穴なので先にやる** | P0 | — |
| ワークスペースの核 | テーブル、リポジトリ ID を一意キーに、`team_of` の置き換え、`teams` の移行、API の絞り込み、デモ seed、切り替え手順 | P1 | S42、先行修正 |
| ワークスペース単位の MCP トークン | 発行・失効・ハッシュ保存、ツールの絞り込み | P1 | 核 |
| リポジトリの登録 | GitHub App のユーザー認可、admin 権限の確認、登録 UI | P1 | 核 |
| 招待とメンバー管理 | 招待・受諾・role 変更（owner だけ）・監査 | P1 | 核 |

S43（規約の UI）と S45（プロジェクト画面）はワークスペースの核に依存させる。

## 8.1 S53（ワークスペースの核）で実装した形

3 章のモデルからの差分。codex レビュー（2026-09-28、計画 v1・v2 の 2 回）の採用分を含む。

- **表**: `workspaces` は作らず、S42 の `teams` をそのまま使う（slug = `teams.team`、表示名 `teams.name`）。
  改訂操作の排他（`TeamRepository.ensure_and_lock`）を 2 つの行に割らないため。`users` 表も作らない
  （必要になるのは招待の S56）
- **利用者の識別子は uid**（ID トークンの `sub`）。メールは表示用。メールをキーにすると、確認していない
  第三者がそのメールで先にサインアップして権限を得られる。`teams.approvers`（メール）は移さず読まない
- **role**: owner ⊇ approver ⊇ member。規約の承認は owner / approver。member は閲覧・手動 start・デモ再実行
- **project の同一性はリポジトリ ID**。`projects.repo_full_name` の一意制約は残し、同じ名前の別リポジトリ
  （ID 違い、または ID の無い S53 以前の行）が webhook に現れたら、旧行を `<name>#stale-<id 先頭>` に
  退避して登録（team）を外す。名前一致だけで既存行に ID を埋めない。既存行の ID は管理 CLI の
  `backfill-repo-ids` が GitHub API で埋め、取れない行は隔離する
- **webhook**: 重複排除の記録と DB の書き込みを 1 トランザクションに。enqueue は commit 後で、失敗したら
  記録を消して 500（再配信で作り直した run は supersede で旧 run を置き換える）。コメントのコマンドは
  ID で引いた project の承認者・質問だけを使い、コメントからは project を作らない
- **worker**: スナップショット取得・Check 作成は名前で行うので、その前に `GET /repos/{name}` の ID と
  project の ID を照合し、違えば run を失敗させる（公開しない）
- **API / MCP**: API・MCP から project は作らない。MCP（S54 まで全体共通トークン）も登録済みの
  リポジトリにだけ答える（未登録の非公開リポジトリの仕様・規約を読ませない）
- **管理経路**: 管理 CLI `speclino workspace ...` を Cloud Run Job `speclino-admin` で実行する
  （DB の秘密を手元に出さない）。手順は `guides/deploy-runbook.md`
- **開放条件**: 他のチームにワークスペースを開くのは S54（ワークスペース単位の MCP トークン）の後

## 8.2 S54（ワークスペース単位の MCP トークン）で実装した形

- **トークン**: `mcp_tokens`（team, name, token_hash, created_by, last_used_at, revoked_at）。平文は
  `slmcp_` + 32 バイトの乱数で、発行時の応答に 1 回だけ出す。保存は SHA-256 だけ（高エントロピーなので
  低速ハッシュは不要）。失効は `revoked_at` を入れる（行は消さない）。`last_used_at` は 1 分以上古いときだけ更新
- **管理**: 発行・一覧・失効は owner だけ（approver は含めない）。API は `/api/workspaces/{slug}/mcp-tokens`、
  UI は `/workspaces/{slug}/mcp-tokens`。owner 以外・別ワークスペースのトークン ID・失効済みは一律 404
- **認証と絞り込み**: `BearerAuth` がトークンからワークスペースを決めて `scope["state"]` に載せ、ツールは
  `ctx.request_context.request.state` から読む。3 つのツールとも、トークンのワークスペースに登録された
  リポジトリ・run だけに答え、それ以外は「無い」と同じ応答。全体共通の `SPECLINO_MCP_TOKEN` は廃止し、
  MCP は web / all で常にマウントする
- **context_get の委譲**: スナップショット取得に GitHub App の秘密鍵が要り、鍵は worker だけが持つ。
  本番の web は OIDC で worker の `POST /internal/mcp/context` を呼び、認可済みの project_id と ref だけを
  渡す（チームは worker が project から引く）。worker の run.invoker は tasks SA と web SA。ローカルは
  プロセス内で同じサービス（`services/mcp_context.py`）を呼ぶ
- **切り替え**: コードを先に出す（旧 secret の注入が残っていても無視される）。Terraform は注入を外すだけで、
  `mcp-token` secret はロールバック期間が過ぎるまで消さない

## 8.3 S55（リポジトリの登録）で実装した形

6 章の流れを、公式ドキュメントで確かめた事実と codex レビュー（2026-09-29）で具体化した。

- **認可**: GitHub App のユーザー認可（web application flow）。インストール時の OAuth は使わず、インストール済みの
  リポジトリを後から登録する。認可 URL に state と PKCE（S256）を付け、redirect_uri は App の Callback URL と
  完全一致（ワイルドカード無効）で、トークン交換にも同じ値を送る
- **試行**: `repo_link_attempts`（state の SHA-256、uid、ワークスペース、リポジトリ名、PKCE の verifier、期限 10 分）。
  コールバックで条件付きの更新により 1 回だけ消費する（別の uid・期限切れ・使用済みは拒否）
- **確かめること**: 6 章の `GET /user` ＋ collaborators permission ではなく、ユーザートークンで
  `GET /user/installations` → `/user/installations/{id}/repositories` に列挙され、かつ `permissions.admin` であること。
  `GET /repos/{owner}/{repo}` の 200 は公開リポジトリなら App が入っていなくても返るので、インストールの証明にならない
- **登録**: project の解決（リポジトリ ID で。無ければ作る、同名で ID 違いの旧行は退避）・条件付きの更新
  （`team IS NULL`）・監査（actor は uid）を 1 トランザクションで行い、別ワークスペースに登録済みなら全体を戻す
- **トークン**: 保存しない。確認が済んだら `DELETE /applications/{client_id}/grant` で認可ごと取り消し
  （access token と refresh token がまとめて失効する。`/token` は access token 1 本だけで refresh token が残る）、
  **取り消せたときだけ登録する**。拒否・例外の経路でも取り消しを試みる。値は例外の文面・ログ・監査・DB に出さない
- **制約**: 本番の App は「Only on this account」なので、App 所有アカウントのリポジトリだけが登録できる

## 9. 判断の記録

- **owner をチームとみなす案は捨てた**: 個人アカウントのリポジトリで成り立たない（ユーザー指摘）
- **GitHub をメンバーシップの正本にしない**: 組織・チーム・collaborator のどれを採っても、個人
  リポジトリと混在するチームを表せない。GitHub は登録時の権限確認だけに使い、チームの概念を
  SpecLino の 1 か所に寄せる
- **1 リポジトリ 1 ワークスペース**: ゲートが照らす規約を一意にするため
- **codex レビュー（2026-09-27）で 4 点採用**: PR コマンドの境界（回答・承認者）、リポジトリ ID を
  一意キーに、切り替えの順序、排他の一本化。GitHub 仕様の前提（インストール時のユーザー認可で
  admin を確認できる、アカウントにつきインストール 1 つ）は公式ドキュメントで確認済み
