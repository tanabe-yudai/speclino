# 本番デプロイ手順（S12 / S13 / S26）

GCP プロジェクトの作成から、Cloud Run への初回デプロイまで。**`terraform apply` と課金に関わる
操作はユーザーが実行する**（Claude は plan とレビューまで）。

前提: `gcloud` が `13311633+tanabe-yudai@users.noreply.github.com` で認証済み、課金アカウント `XXXXXX-XXXXXX-XXXXXX`
（T-NET、OPEN）、Terraform v1.12 以上。

**先に ADC の quota project を設定する**（bootstrap の apply 後、プロジェクトができてから）:

```sh
gcloud auth application-default set-quota-project example-project
```

`gcloud auth application-default login` で作る ADC には quota project が付かない。
`billingbudgets` と `identitytoolkit` はリクエストに quota project を要求するため、未設定だと
Google 既定の共有プロジェクト（`projects/764086051850`）が使われ、**プロジェクト側で API を
有効化していても** `SERVICE_DISABLED` で失敗する。Cloud Run や IAM は要求しないので、
この 2 つだけが落ちる。

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

### bootstrap の state を GCS へ移す

bootstrap は tfstate バケット自体を作るため、**初回はローカル state**
（`infra/bootstrap/terraform.tfstate`）で apply する。バケットができた後に、その中へ移す。
移しておくと、手元の state を失っても復旧でき、別のマシンからも操作できる。

```sh
cd infra/bootstrap
cp backend.tf.migrate backend.tf          # backend "gcs" {} を有効化する
terraform init -migrate-state \
  -backend-config="bucket=example-project-tfstate" \
  -backend-config="prefix=bootstrap"
# → 「Do you want to copy existing state to the new backend?」に yes

terraform plan -var project_id=example-project -var billing_account=XXXXXX-XXXXXX-XXXXXX
# → "No changes." なら移行できている
```

移行後は `terraform.tfstate` がローカルに残るが、中身は空（`terraform.tfstate.backup` に
移行前の内容が残る）。以降は `terraform init -backend-config=...` だけでよい。

**移行したら `backend.tf` をコミットする**。コミットしないと、別のマシンやクリーンな
チェックアウトで backend が設定されず、気付かないままローカル state で操作してしまう。
（逆に、まだ bootstrap を apply していない環境でこのファイルがあると init が失敗するので、
バケットを作るまでは `backend.tf.migrate` のままにしておく。）

> 移行しない場合は `infra/bootstrap/terraform.tfstate` を消さないこと。失うと作成済みの
> プロジェクトとバケットを Terraform が管理できなくなる（`terraform import` で復旧は可能）。
> `.gitignore` 済みなので Git には入らない。

## 2. prod（Cloud Run・Tasks・Secret・WIF・Identity Platform・予算）

prod の state は bootstrap が作った GCS バケットに置く（`gs://example-project-tfstate/envs/prod/`、
versioning 有効）。`tfvars` を用意する:

```sh
cd ../envs/prod
cp terraform.tfvars.example terraform.tfvars
# project_id / billing_account / github_repository を確認する
```

```sh
terraform init \
  -backend-config="bucket=example-project-tfstate" \
  -backend-config="prefix=envs/prod"
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

### 2 回目の apply（URL が決まってから）

Cloud Run の URL は作成後に決まるため、自分自身を参照する設定は 2 回目の apply で入れる。

```sh
terraform output web_url worker_url
cat >> terraform.tfvars <<EOF
public_url = "<web_url>"
worker_url = "<worker_url>"
EOF
terraform plan -out=tfplan && terraform apply tfplan
```

これで web に `SPECLINO_QUEUE_KIND=cloud_tasks` と Cloud Tasks の宛先・OIDC SA が入る。
**環境変数は Terraform が持つ**（`gcloud run services update --set-env-vars` で足すと次の
apply で消える）。Cloud SQL を作った後は `database_url` と `cloudsql_instance` も tfvars に
足して apply する。

### 予算 / Identity Platform が 403 SERVICE_DISABLED で失敗する場合

ADC の quota project が未設定（上の「前提」を参照）。設定して plan からやり直す:

```sh
gcloud auth application-default set-quota-project example-project
terraform plan -out=tfplan && terraform apply tfplan   # 失敗した分だけ作られる
```

それでも `google_identity_platform_config` が失敗する場合は、プロジェクトで Identity Platform
が一度も初期化されていない可能性がある。<https://console.cloud.google.com/customer-identity>
を一度開いて有効化してから apply をやり直す。

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

## 6. 環境変数（Terraform で管理する）

役割ごとの環境変数は `infra/envs/prod/run.tf` が持つ。`gcloud run services update` で
足さないこと（次の `terraform apply` で消える）。tfvars に入れるのは次の 4 つ:

| 変数 | 値 | いつ |
| --- | --- | --- |
| `public_url` | `terraform output web_url` | 2 回目の apply |
| `worker_url` | `terraform output worker_url` | 2 回目の apply |
| `database_url` | Cloud SQL の接続 URL | Cloud SQL 作成後 |
| `cloudsql_instance` | `<project>:<region>:speclino-db` | Cloud SQL 作成後 |

秘密（GitHub App 秘密鍵・Webhook secret・MCP トークン）だけは Secret Manager から渡す:

```sh
gcloud run services update speclino-web --project=$P --region=asia-northeast1 \
  --set-secrets="SPECLINO_GITHUB_WEBHOOK_SECRET=github-webhook-secret:latest,SPECLINO_MCP_TOKEN=mcp-token:latest"
gcloud run services update speclino-worker --project=$P --region=asia-northeast1 \
  --set-secrets="SPECLINO_GITHUB_PRIVATE_KEY=github-app-private-key:latest"
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
