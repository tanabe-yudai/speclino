# 本番デプロイ手順（S12 / S13 / S26）

GCP プロジェクトの作成から、Cloud Run への初回デプロイまで。**`terraform apply` と課金に関わる
操作はユーザーが実行する**（Claude は plan とレビューまで）。

前提: `gcloud` が `13311633+tanabe-yudai@users.noreply.github.com` で認証済み、課金アカウント `XXXXXX-XXXXXX-XXXXXX`
（T-NET、OPEN）、Terraform v1.12 以上。

## 1. bootstrap（プロジェクト・API・tfstate バケット）

作るもの: プロジェクト `example-project`、API 18 個、GCS バケット `example-project-tfstate`。
**この時点では課金が発生するリソースは無い。**

```sh
cd infra/bootstrap
terraform init

# plan を固定してから中身を確認する
terraform plan -out=tfplan \
  -var project_id=example-project \
  -var billing_account=XXXXXX-XXXXXX-XXXXXX
terraform show tfplan | grep -E '^  # |^Plan:'      # 20 to add, 0 to change, 0 to destroy

terraform apply tfplan
terraform output state_bucket                        # → example-project-tfstate
```

> プロジェクト ID が既に使われていると `already exists` で失敗する。その場合は
> `-var project_id=example-project-<接尾辞>` にして、以降の手順でも同じ値を使う。

## 2. prod（Cloud Run・Tasks・Secret・WIF・Identity Platform・予算）

`tfvars` を用意する:

```sh
cd ../envs/prod
cp terraform.tfvars.example terraform.tfvars
# project_id / billing_account / github_repository を確認する
```

```sh
terraform init -backend-config="bucket=example-project-tfstate"
terraform plan -out=tfplan
terraform show tfplan | grep -E '^  # |^Plan:'
terraform apply tfplan
terraform output
```

作られるもの（**この時点で課金対象になるのは Cloud Run と GCS のみ。min-instances=0 なので
アイドル時はほぼ無料**）:

| 種類 | 内容 |
| --- | --- |
| Cloud Run | `speclino-web`（公開）、`speclino-worker`（tasks SA のみ）。初回はサンプルイメージ |
| Cloud Tasks | キュー `speclino-runs`（asia-northeast1） |
| Secret Manager | 空のコンテナ 4 つ（値は次の手順で投入） |
| IAM | SA 5 つ（web / worker / sandbox / tasks / deployer）と最小権限 |
| WIF | GitHub Actions 用（`tanabe-yudai/spec-lino` からのみ） |
| Identity Platform | メール/パスワードのみ有効化 |
| 予算 | $300 に対し 17% / 33% / 67% / 100% でアラート |
| GCS | `example-project-artifacts`（成果物） |

`speclino-sandbox` は **既定で作らない**（`enable_sandbox = false`）。Cloud Run sandboxes は
Preview で、プロジェクトが許可されていないと apply 全体が失敗するため。S38 で
`-var enable_sandbox=true` にして試す。

### Identity Platform が失敗する場合

`google_identity_platform_config` は、プロジェクトで Identity Platform が一度も初期化されて
いないと失敗することがある。その場合は
<https://console.cloud.google.com/customer-identity> を一度開いて有効化し、apply をやり直す。

## 3. 秘密の投入（Terraform の state には載せない）

```sh
P=example-project
# GitHub App（本番用）の秘密鍵と Webhook secret
gcloud secrets versions add github-app-private-key --data-file=./speclino.pem --project=$P
printf '%s' "$(openssl rand -hex 32)" | \
  gcloud secrets versions add github-webhook-secret --data-file=- --project=$P
printf '%s' "$(openssl rand -hex 32)" | \
  gcloud secrets versions add mcp-token --data-file=- --project=$P
printf '%s' "$(openssl rand -hex 32)" | \
  gcloud secrets versions add session-secret --data-file=- --project=$P
```

Webhook secret と MCP トークンは控えておく（GitHub App の設定と、エディタの MCP 設定に使う）。
秘密鍵は worker の SA だけが読める（`secrets.tf` の `secret_accessors`）。

## 4. Cloud SQL（S26）

```sh
gcloud sql instances create speclino-db \
  --project=$P --region=asia-northeast1 \
  --database-version=POSTGRES_16 --edition=ENTERPRISE \
  --tier=db-f1-micro --storage-size=10GB --no-backup
gcloud sql databases create speclino --instance=speclino-db --project=$P
gcloud sql users create speclino --instance=speclino-db --project=$P --password="$(openssl rand -hex 24)"
```

**クーポンの登録はここで行う**（Cloud SQL が最も費用を使う。登録期限 10/20）。

接続は Cloud Run の Unix ソケット経由:
`postgresql+asyncpg://speclino:<pw>@/speclino?host=/cloudsql/<接続名>`。
接続名は `gcloud sql instances describe speclino-db --format='value(connectionName)'`。

## 5. イメージのビルドとデプロイ

初回だけ手元から push し、以降は GitHub Actions（`.github/workflows/deploy.yml`、
`vars.DEPLOY_ENABLED` で有効化）に任せる。

**先に Firebase（Identity Platform）のクライアント設定を取る**。Vite はこれをビルド時に
埋め込むので、実行時の環境変数では効かない。コンソール → Identity Platform →
「アプリケーションの設定の詳細」から apiKey と authDomain を控える。

```sh
gcloud auth configure-docker asia-northeast1-docker.pkg.dev
IMAGE=asia-northeast1-docker.pkg.dev/$P/speclino/speclino:$(git rev-parse --short HEAD)
docker build --platform linux/amd64 -t $IMAGE \
  --build-arg VITE_FIREBASE_API_KEY=<apiKey> \
  --build-arg VITE_FIREBASE_AUTH_DOMAIN=$P.firebaseapp.com \
  --build-arg VITE_FIREBASE_PROJECT_ID=$P \
  .
docker push $IMAGE

for S in web worker; do
  gcloud run services update speclino-$S --project=$P --region=asia-northeast1 --image=$IMAGE
done
```

> ローカルが Apple Silicon なので `--platform linux/amd64` が要る（付け忘れると
> Cloud Run が起動に失敗する）。build-arg を省くとログイン画面が「開発モードで入る」に
> なってしまうので、本番では必ず渡す。

GitHub Actions から自動デプロイする場合は、リポジトリ変数に
`VITE_FIREBASE_API_KEY` / `VITE_FIREBASE_AUTH_DOMAIN` / `VITE_FIREBASE_PROJECT_ID` を設定する
（クライアント設定なので secret ではなく variable でよい）。

## 6. 環境変数の設定

```sh
WEB_URL=$(gcloud run services describe speclino-web --project=$P --region=asia-northeast1 --format='value(status.url)')
WORKER_URL=$(gcloud run services describe speclino-worker --project=$P --region=asia-northeast1 --format='value(status.url)')

gcloud run services update speclino-web --project=$P --region=asia-northeast1 \
  --set-env-vars="SPECLINO_PUBLIC_URL=$WEB_URL,SPECLINO_QUEUE_KIND=cloud_tasks,SPECLINO_BLOB_KIND=gcs,SPECLINO_GCS_BUCKET=$P-artifacts,SPECLINO_AUTH_KIND=identity_platform,SPECLINO_FIREBASE_PROJECT_ID=$P,SPECLINO_WORKER_URL=$WORKER_URL" \
  --set-secrets="SPECLINO_GITHUB_WEBHOOK_SECRET=github-webhook-secret:latest,SPECLINO_MCP_TOKEN=mcp-token:latest"

gcloud run services update speclino-worker --project=$P --region=asia-northeast1 \
  --set-env-vars="SPECLINO_PUBLIC_URL=$WEB_URL,SPECLINO_BLOB_KIND=gcs,SPECLINO_GCS_BUCKET=$P-artifacts,SPECLINO_DATABASE_URL=<Cloud SQL の URL>" \
  --set-secrets="SPECLINO_GITHUB_PRIVATE_KEY=github-app-private-key:latest" \
  --add-cloudsql-instances=<接続名>
```

web には GitHub App の秘密鍵を渡さない（`LazyCodeHost` により web は秘密鍵なしで起動する）。

## 7. マイグレーションとテストアカウント

```sh
# Cloud SQL Auth Proxy でローカルから流す
cloud-sql-proxy $P:asia-northeast1:speclino-db &
SPECLINO_DATABASE_URL="postgresql+asyncpg://speclino:<pw>@localhost:5432/speclino" \
  uv run alembic upgrade head

# 審査員用のテストアカウント（Identity Platform）
gcloud identity platform tenants list --project=$P   # 省略可（テナントは使わない）
# コンソール → Identity Platform → ユーザーを追加（メール/パスワード）
```

作った認証情報は提出フォームにのみ記載し、リポジトリには置かない。

## 8. 動作確認

```sh
scripts/smoke_prod.sh                       # ヘルス / SPA / 401 / webhook 署名 / MCP Bearer
open "$WEB_URL"                             # ログイン画面 → テストアカウントで入る
```

テストアカウントは `SPECLINO_FIREBASE_API_KEY=<apiKey> scripts/create_test_user.sh` で作る。
ログインまで含めて確認するなら:

```sh
SPECLINO_FIREBASE_API_KEY=<apiKey> \
SPECLINO_SMOKE_EMAIL=<email> SPECLINO_SMOKE_PASSWORD=<password> \
  scripts/smoke_prod.sh
```

その後 `guides/github-app-setup.md` に従って本番 GitHub App の Webhook URL を
`$WEB_URL/webhooks/github` に設定し、デモ PR（`demos/README.md`）を作る。

## 片付け（12/1 以降）

```sh
cd infra/envs/prod && terraform destroy
gcloud sql instances delete speclino-db --project=$P
# プロジェクト自体は deletion_policy=PREVENT。消すときは bootstrap の main.tf を編集してから
```
