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
| Pull requests | Read and write | 質問・承認待ちのコメント投稿 |
| Metadata | Read-only | 必須（自動で付く） |

**Subscribe to events**: `Pull request`、`Issue comment`、`Check run`、`Check suite`。

実装が処理するのは次だけ（それ以外は 200 を返して無視する）:

- `pull_request`: `opened` / `synchronize` / `reopened`
- `issue_comment`: `created`（本文の `/speclino approve-spec|answer|attest|rerun`）
- `check_run`: `rerequested` / `requested_action`、`check_suite`: `rerequested`

作成後に **Generate a private key** して `.pem` をダウンロードし、App ID を控える。

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
export SPECLINO_APPROVERS='["<自分の GitHub ログイン>"]'
export SPECLINO_PUBLIC_URL=https://xxxx.trycloudflare.com
export SPECLINO_AGENT_ENABLED=false   # まず静的判定だけで確認する（費用ゼロ）

# 3) DB とサーバー
make db-up && make db-migrate && make dev
```

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
| コマンド（`/speclino ...`）が無視される | `SPECLINO_APPROVERS` に自分のログインが無い。承認者はサーバー側設定でのみ決まる |
