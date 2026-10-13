# GitHub App の設定と実 PR での確認手順（S21）

SpecLino を PR に接続するには GitHub App が要る。開発用と本番用で **App を分ける**（開発用の
Webhook URL はトンネルで頻繁に変わり、秘密鍵も手元に置くため）。

## 1. App を作る

<https://github.com/settings/apps/new>（個人）または Organization の設定から作る。

| 項目 | 開発用 | 本番用 |
| --- | --- | --- |
| GitHub App name | `SpecLino (dev)` | `SpecLino` |
| Homepage URL（必須） | リポジトリ URL | web サービスの URL（App ページに公開表示されるため、private リポジトリの URL は避ける） |
| Webhook URL | `https://<トンネル>/webhooks/github` | `https://<Cloud Run web>/webhooks/github` |
| Webhook secret | 任意の乱数（`openssl rand -hex 32`） | Secret Manager に保存した値 |
| Where can this be installed | Only on this account | Only on this account |

**Repository permissions**（これ以外は No access）:

| 権限 | 値 | 用途 |
| --- | --- | --- |
| Checks | Read and write | `SpecLino / gate` の Check Run 作成・更新 |
| Contents | Read-only | base / head のスナップショット取得 |
| Issues | Read-only | **`issue_comment` イベントを受け取るために必要**（Pull requests 権限だけでは選択肢に出ない）。コメントの投稿は PR に対して行うので Pull requests の write で足り、Issues は read で十分 |
| Pull requests | Read and write | 質問・承認待ちのコメント投稿。`pull_request_review` イベント（PR の Approve による確定）もこの権限で受け取れる（受け取るだけなら Read-only で足りる） |
| Metadata | Read-only | 必須（自動で付く） |

**Subscribe to events**: `Pull request`、`Pull request review`、`Issue comment`、`Check run`、`Check suite`。

> イベントの選択肢は**選んだ権限で絞り込まれる**。先に権限を設定して保存しないと、
> Check run / Check suite（Checks 権限）と Issue comment（Issues 権限）は一覧に現れない。

実装が処理するのは次だけ（それ以外は 200 を返して無視する）:

- `pull_request`: `opened` / `synchronize` / `reopened`
- `pull_request_review`: `submitted` で `review.state` が `approved` のものだけ（承認者の Approve を要確認の確定として扱う。
  commented / changes_requested / dismissed は無視）
- `issue_comment`: `created`（本文の `/speclino approve-spec|answer|attest|rerun`。引数なしの `attest` はまとめて確定）
- `check_run`: `rerequested` / `requested_action`、`check_suite`: `rerequested`

### 既存の App に `Pull request review` を足す

PR の Approve による確定（下記）を使うには、作成済みの App にもイベントを足す。

1. GitHub の **Settings → Developer settings → GitHub Apps → 対象の App → Edit** を開く
2. **Permissions & events** の Repository permissions で `Pull requests` が Read-only 以上であることを確かめる
   （SpecLino は Read and write にしている。変えていなければそのままでよい）
3. 同じ画面の **Subscribe to events** で `Pull request review` にチェックを入れ、**Save changes**
4. 権限を変えた場合だけ、インストール先（リポジトリの Settings → GitHub Apps、または Organization の設定）で
   新しい権限の承認を求められるので承認する。イベントの追加だけなら承認は要らない
5. App の **Advanced → Recent Deliveries** で、PR を Approve したときに `pull_request_review` が 200 で
   届くことを確かめる（応答の `status` が `queued` なら確定して再検査した、`handled` なら理由が `reason` に出る）

### 要確認をまとめて確定する（`/speclino attest` か PR の Approve）

必須の要確認（`KNOWLEDGE-BINDINGS-001` の対応表・規約・免除・契約の変更、LLM 支援の規約の指摘）は、
プロジェクトの承認者（設定画面の「仕様の承認者」。GitHub のユーザー ID で照合）が次のどちらかをすると、
その PR の最新の検査の確定できる要確認がすべて確定する。ハッシュを打つ必要はない（サーバが run の指摘の値を使う）。

- PR に **`/speclino attest`**（引数なし）とコメントする。1 人運用で作成者＝承認者のとき（GitHub では自分の PR を
  Approve できない）はこちら
- GitHub で PR を **Approve** する（Approve したコミット `review.commit_id` が最新の検査の head と一致するときだけ）

共通の規則:

- 最新の run と最新の完了した run の head が一致するときだけ確定する。検査中・未検査のコミットがあるとき、古い
  コミットへの Approve では何もしない。理由は webhook の応答（Recent Deliveries）と監査 `attestation.bulk` に残る
  （webhook は PR にコメントを書かない）。検査が終わってからやり直せばよい
- 確定済み（`/speclino attest <指紋> <hash> ack` で問題として記録したものを含む）は上書きしない
- 確定のあとは同じ head で再検査し、ゲートのコメントと Check を更新する（確定が検査に反映されていなければ、もう一度
  `/speclino attest` とコメントすると再検査し直す）
- 1 件ずつの `/speclino attest <指紋> <sha256:…> [ack]` もそのまま使える（形はゲートのコメントの各指摘の「根拠」に出る。
  問題として記録する `ack` はこの形だけ）

作成後に **Generate a private key** して `.pem` をダウンロードし、App ID を控える。

## 1.1 ユーザー認可（S55 のリポジトリ登録）

ワークスペースの owner がリポジトリを登録するとき、GitHub App のユーザー認可（web application flow）で
本人がそのリポジトリの admin であり、App がインストールされていることを確かめる。App の設定で次を行う。

| 項目 | 値 |
| --- | --- |
| Callback URL | `https://<Cloud Run web>/github/callback`（**完全一致**。ワイルドカードの一致は無効にする。有効だと認可コードを配下の任意のパスに送れてしまう） |
| Client secrets | 「Generate a new client secret」で生成し、Secret Manager の `github-app-client-secret` に投入する |
| Expire user authorization tokens | 有効を推奨（access token は 8 時間。SpecLino は確認の直後に失効させ、refresh token は使わない） |
| Request user authorization (OAuth) during installation | 使わない（インストール済みのリポジトリを後から登録するので、通常の認可で足りる） |

```sh
printf '%s' '<client secret>' | gcloud secrets versions add github-app-client-secret --data-file=- --project=<project_id>
```

Client ID は秘密ではないので `infra/envs/prod/terraform.tfvars` の `github_client_id` に書く。

- 「Where can this be installed」が **Only on this account** のあいだは、App 所有アカウントのリポジトリしか
  登録できない（他のアカウントは App をインストールできない）。他チームに開くときに公開を判断する
- 認可の `state` はサーバーに保存して 1 回だけ使い、PKCE（S256）を付ける。トークン交換は
  `Accept: application/json` で行い、確認が済んだら `DELETE /applications/{client_id}/grant` で認可ごと取り消す
  （access token と refresh token がまとめて失効する。取り消せなかったら登録しない）
  （根拠は GitHub Docs「Generating a user access token for a GitHub App」「About the user authorization callback URL」）

## 2. インストールする

App の設定 → Install App → 対象リポジトリだけを選ぶ（All repositories にしない）。

## 3. ローカルで実 PR に繋ぐ

```sh
# 1) トンネル（例: cloudflared。ngrok でもよい）
cloudflared tunnel --url http://localhost:8080
# → https://xxxx.trycloudflare.com が出る。App の Webhook URL をこれに更新する

# 2) 環境変数（export する。Settings は .env を読まない）
export SPECLINO_GITHUB_APP_ID=<App ID>
export SPECLINO_GITHUB_PRIVATE_KEY_PATH=$HOME/.secrets/speclino-dev.pem
export SPECLINO_GITHUB_WEBHOOK_SECRET=<作成時の secret>
export SPECLINO_PUBLIC_URL=https://xxxx.trycloudflare.com
export SPECLINO_AGENT_ENABLED=false   # まず静的判定だけで確認する（費用ゼロ）

# 3) DB とサーバー
make db-up && make db-migrate && make dev
```

PR の上での承認者（`/speclino approve-spec`・`/speclino attest`・PR の Approve）は環境変数ではなく、プロジェクトごとに
画面の「設定」→「プロジェクト」の「仕様の承認者（GitHub のログイン名）」で保存する（保存時に GitHub のユーザー ID を引き、
照合は ID で行う）。

秘密鍵と secret は **リポジトリに入れない**（`*.pem` と `.env` は `.gitignore` 済み。CI の gitleaks でも検査する）。

## 4. 実 PR で確認する

対象リポジトリに `examples/orders` 相当の題材を置き、違反を持ち込む PR を作る:

```sh
git switch -c demo/restore-admin-only
# 在庫復元を管理者経路だけに移す
git commit -am "demo: restore inventory only for admins" && git push -u origin HEAD
gh pr create --fill
```

確認すること:

- [ ] Check `SpecLino / gate` が作られ、`failure` で完了する
- [ ] Check の summary に「違反 1 / 未検証 0 / 適合 8（ゲート: 失敗）」と base との差分件数が出る
- [ ] 行注釈（annotation）が違反箇所に付く
- [ ] 詳細リンク（`details_url`）が Web UI の run 詳細を開く
- [ ] 同じ delivery を再送しても Check が重複しない（`gh api` で redeliver、または App の
      Advanced → Redeliver）
- [ ] push で新しい run が走り、**未完了の**古い run は `superseded` になって結果を公開しない
      （完了済みの run は履歴として残り、最新の run だけが Check に反映される）

## 5. 必須チェックにする（**ユーザー確認後に実行**）

デフォルトブランチのブランチ保護で次の 3 点を満たすこと。

1. **Require status checks to pass before merging** に `SpecLino / gate` を追加する
2. **ソースを App に固定**する（チェック名の横で App を選ぶ。別主体が同名の success を
   報告してもマージできないようにする）
3. **bypass を無効化**する（Allow specified actors to bypass / administrators の除外を外す）

確認方法:

| 状況 | 期待 |
| --- | --- |
| Check がまだ無い | マージボタンが無効 |
| Check が `failure` / `action_required` | マージボタンが無効 |
| 別の App / Actions が同名の `success` を報告 | マージボタンは無効のまま |
| Check が `success` | マージ可能 |

## 6. 本番 App との違い

本番は Webhook URL を Cloud Run の web サービスに向け、秘密鍵と Webhook secret は Secret Manager
に置く（`gcloud secrets versions add`。Terraform の state には入れない）。worker だけが秘密鍵を
読める（`roles/secretmanager.secretAccessor` を worker の SA にのみ付与）。

## トラブルシューティング

| 症状 | 原因と対処 |
| --- | --- |
| Webhook が 401 | `SPECLINO_GITHUB_WEBHOOK_SECRET` が App の secret と違う。App の Advanced → Recent Deliveries で本文と署名を確認する |
| Check が作られない | インストール先にリポジトリが含まれていない、または Checks 権限が Read-only。権限を変えたら **インストール側で承認**が要る |
| `installation token` の取得に失敗 | App ID と秘密鍵の組が違う。`.pem` は改行を含めてそのまま渡す |
| コマンド（`/speclino ...`）・Approve が無視される | プロジェクトの「仕様の承認者」に自分がいない、または GitHub のユーザー ID が未登録（画面で保存し直す）。承認者はサーバー側の設定でのみ決まる。`attest` は最新のコミットの検査が完了していないと何もしない（理由は Recent Deliveries の応答と監査 `attestation.bulk`） |
