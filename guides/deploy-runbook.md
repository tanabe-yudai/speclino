# 本番デプロイ手順（S12 / S13 / S26）

GCP プロジェクトの作成から、Cloud Run への初回デプロイまで。**`terraform apply` と課金に関わる
操作はユーザーが実行する**（Claude は plan とレビューまで）。

前提: `gcloud` が自分のアカウントで認証済みで、使える課金アカウント（`<billing_account_id>`）があること、
Terraform v1.12 以上。

以下の `<...>` は自分の環境の値に置き換える: `<project_id>`（GCP プロジェクト ID）、`<billing_account_id>`、
`<owner>/<repo>`（このリポジトリを置いた GitHub のリポジトリ）、`<web-url>`（Cloud Run の web サービスの URL。
手順 3 で分かる）、`<workspace>`（SpecLino のワークスペースの識別子）。

## どこで実行するか

各手順の先頭に `cd` を書いてある。**手順 1・2 だけ Terraform のディレクトリ**で、
残りはリポジトリ直下（`docker build .` が Dockerfile を見つけるため）。

```sh
export REPO=<このリポジトリを clone した場所>
export P=<project_id>                  # プロジェクト ID（例: speclino-prod。世界で一意）
export REGION=asia-northeast1
```

シェルを開き直したらこの 3 つを設定し直す（以降の手順は `$P` / `$REGION` を使う）。

**先に ADC の quota project を設定する**（bootstrap の apply 後、プロジェクトができてから）:

```sh
gcloud auth application-default set-quota-project $P
```

`gcloud auth application-default login` で作る ADC には quota project が付かない。
`billingbudgets` と `identitytoolkit` はリクエストに quota project を要求するため、未設定だと
Google 既定の共有プロジェクト（`projects/764086051850`）が使われ、**プロジェクト側で API を
有効化していても** `SERVICE_DISABLED` で失敗する。Cloud Run や IAM は要求しないので、
この 2 つだけが落ちる。

## 1. bootstrap（プロジェクト・API・tfstate バケット）

作るもの: プロジェクト `<project_id>`、API 18 個、GCS バケット `<project_id>-tfstate`。
**この時点では課金が発生するリソースは無い。**

```sh
cd "$REPO/infra/bootstrap"
terraform init

# plan を固定してから中身を確認する
terraform plan -out=tfplan \
  -var project_id=$P \
  -var billing_account=<billing_account_id>
terraform show tfplan | grep -E '^  # |^Plan:'      # 20 to add, 0 to change, 0 to destroy

terraform apply tfplan
terraform output state_bucket                        # → <project_id>-tfstate
```

> プロジェクト ID が既に使われていると `already exists` で失敗する。その場合は
> `-var project_id=<project_id>-<接尾辞>` にして、以降の手順でも同じ値を使う。

### bootstrap の state を GCS へ移す

bootstrap は tfstate バケット自体を作るため、**初回はローカル state**
（`infra/bootstrap/terraform.tfstate`）で apply する。バケットができた後に、その中へ移す。
移しておくと、手元の state を失っても復旧でき、別のマシンからも操作できる。

```sh
cd "$REPO/infra/bootstrap"
cp backend.tf.migrate backend.tf          # backend "gcs" {} を有効化する
terraform init -migrate-state \
  -backend-config="bucket=<project_id>-tfstate" \
  -backend-config="prefix=bootstrap"
# → 「Do you want to copy existing state to the new backend?」に yes

terraform plan -var project_id=$P -var billing_account=<billing_account_id>
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

prod の state は bootstrap が作った GCS バケットに置く（`gs://<project_id>-tfstate/envs/prod/`、
versioning 有効）。`tfvars` を用意する:

```sh
cd "$REPO/infra/envs/prod"
cp terraform.tfvars.example terraform.tfvars
# project_id / billing_account / github_repository を確認する
```

```sh
terraform init \
  -backend-config="bucket=<project_id>-tfstate" \
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
| WIF | GitHub Actions 用（`<owner>/<repo>` からのみ） |
| Identity Platform | メール/パスワードのみ有効化 |
| 予算 | $300 に対し 17% / 33% / 67% / 100% でアラート |
| GCS | `<project_id>-artifacts`（成果物） |

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

ADC の quota project が未設定（上の「前提」を参照）。**ADC への設定だけでは足りず**、
provider 側でも `user_project_override = true` が要る（`versions.tf` に設定済み。これが無いと
provider は `X-Goog-User-Project` ヘッダを送らない）。

```sh
gcloud auth application-default set-quota-project $P
terraform plan -out=tfplan && terraform apply tfplan   # 失敗した分だけ作られる
```

### 予算が 400 invalid argument で失敗する場合

**予算の通貨は課金アカウントの通貨と一致していないといけない。** 確認:

```sh
curl -s -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  https://cloudbilling.googleapis.com/v1/billingAccounts/<billing_account_id>
# → "currencyCode": "JPY"
```

既定は `budget_currency_code = "JPY"` / `budget_amount = "45000"`（≒ $300）。課金アカウントの通貨が
違うときは tfvars で合わせる。

なお、通知チャネルを指定しない場合に `all_updates_rule` を空で送るのも同じ 400 になるため、
`budget.tf` ではブロックごと省略している（既定の課金管理者宛メールが使われる）。

それでも `google_identity_platform_config` が失敗する場合は、プロジェクトで Identity Platform
が一度も初期化されていない可能性がある。<https://console.cloud.google.com/customer-identity>
を一度開いて有効化してから apply をやり直す。

## 3. 本番 GitHub App の作成と秘密の投入

### 3-1. 先に値を作って控える

Webhook secret は **GitHub App と Secret Manager の両方に同じ値**を入れるので、先に作って
手元に出しておく。

```sh
export WEBHOOK_SECRET="$(openssl rand -hex 32)"
echo "WEBHOOK_SECRET=$WEBHOOK_SECRET"
```

> MCP トークンはここでは作らない（S54 以降、静的トークンは廃止）。ワークスペースの owner が
> UI（`/workspaces/<slug>/mcp-tokens`）から発行する。手順は「10. MCP トークン（S54）」を参照。

### 3-2. GitHub App を作る（秘密鍵はここで生成される）

<https://github.com/settings/apps/new> で作る。詳細と権限は
[github-app-setup.md](github-app-setup.md) を参照。本番用の値:

| 項目 | 値 |
| --- | --- |
| GitHub App name | `SpecLino` |
| Homepage URL | `<web-url>`（必須。App のページに公開表示される。リポジトリは提出まで private なので web URL にしておく） |
| Webhook URL | `<web-url>/webhooks/github` |
| Webhook secret | 上で作った `$WEBHOOK_SECRET` |

権限: Checks = Read and write / Contents = Read-only / **Issues = Read-only** /
Pull requests = Read and write / Metadata = Read-only。
イベント: Pull request・Issue comment・Check run・Check suite。

> **先に権限を設定して保存する**。イベントの選択肢は選んだ権限で絞り込まれるため、
> Checks を設定するまで Check run / Check suite は、Issues を設定するまで Issue comment は
> 一覧に出てこない。

作成後のページで:

1. **App ID** を控える（`SPECLINO_GITHUB_APP_ID` に使う）
2. 下の方の **Private keys → Generate a private key** を押す
   → `speclino.2026-xx-xx.private-key.pem` が**ダウンロードされる**（GitHub 側には二度と
   表示されないので、無くしたら作り直す）
3. **Install App** → 対象リポジトリだけを選んでインストール

### 3-3. Secret Manager へ入れる

```sh
PEM="$HOME/Downloads/speclino.2026-xx-xx.private-key.pem"   # 実際のファイル名に置き換える
gcloud secrets versions add github-app-private-key --data-file="$PEM" --project=$P

printf '%s' "$WEBHOOK_SECRET" | \
  gcloud secrets versions add github-webhook-secret --data-file=- --project=$P
printf '%s' "$(openssl rand -hex 32)" | \
  gcloud secrets versions add session-secret --data-file=- --project=$P

gcloud secrets list --project=$P     # 4 つあること（mcp-token は値を入れなくてよい。10 章参照）
```

投入したらローカルの `.pem` は消してよい（`rm "$PEM"`）。リポジトリには置かない
（`*.pem` は `.gitignore` 済み）。秘密鍵は worker の SA だけが読める（`secrets.tf`）。

App ID は秘密ではないので tfvars に書く（環境変数は Terraform が持つ。`gcloud` で足すと
次の apply で消える）:

```sh
cd "$REPO/infra/envs/prod"
echo 'github_app_id = "<App ID>"' >> terraform.tfvars
terraform plan -out=tfplan && terraform apply tfplan
```

## 4. Cloud SQL（S26）

**クーポンの登録を先に済ませる**（ここから実際の課金が始まる。Cloud Run と違い停止しないので
起動している間ずっと課金される。登録期限 10/20）。どこで実行してもよい。

```sh
export DB_PASSWORD="$(openssl rand -hex 24)"

gcloud sql instances create speclino-db \
  --project=$P --region=$REGION \
  --database-version=POSTGRES_16 --edition=ENTERPRISE \
  --tier=db-f1-micro --storage-size=10GB --storage-type=HDD \
  --no-backup --availability-type=zonal

# RUNNABLE になるまで待つ（5〜10 分）。PENDING_CREATE のまま続けると下の 2 つが失敗する
until [ "$(gcloud sql instances describe speclino-db --project=$P --format='value(state)')" = RUNNABLE ]
do sleep 20; done

gcloud sql databases create speclino --instance=speclino-db --project=$P
gcloud sql users create speclino --instance=speclino-db --project=$P --password="$DB_PASSWORD"

# 平文接続を禁止し、12/1 まで維持する要件のため削除保護を入れる
gcloud sql instances patch speclino-db --project=$P \
  --ssl-mode=ENCRYPTED_ONLY --deletion-protection --quiet

gcloud sql instances describe speclino-db --project=$P --format='value(connectionName)'
```

### ネットワークの考え方

パブリック IP は付くが、**authorized networks は空（全 IP 拒否）**なので直接は繋がらない。
Cloud Run は `/cloudsql/<接続名>` の Unix ソケット（Cloud SQL コネクタ）で繋ぎ、これは
authorized networks とは別経路で **IAM（`roles/cloudsql.client`）+ 一時証明書**により認可される。
`cloudsql.client` を持つのは web / worker の SA だけ。

パブリック IP を外すにはプライベート IP が要り、そのためには VPC + プライベートサービス
アクセス + Serverless VPC コネクタ（アイドルでも月 $8〜）が必要になる。design の
「VPC / NAT は作らない」に従い、**authorized networks を空に保つ**ことで代替している。

> Cloud SQL に Security Group に相当するものは無い。ネットワーク層は authorized networks、
> 認可は IAM、認証は DB ユーザーのパスワードという 3 層で守る。

### 接続 URL を Secret Manager に入れる

パスワードを含むので tfvars には書かない（Terraform の state に載るため）。

```sh
CONN=$(gcloud sql instances describe speclino-db --project=$P --format='value(connectionName)')
printf '%s' "postgresql+asyncpg://speclino:${DB_PASSWORD}@/speclino?host=/cloudsql/${CONN}" | \
  gcloud secrets versions add database-url --data-file=- --project=$P
```

全ての秘密に値が入ったら、`mount_secrets = true` にして apply する（値の無い秘密を参照すると
リビジョンが起動しないため、投入が済んでから有効化する）:

```sh
cd "$REPO/infra/envs/prod"
echo 'mount_secrets = true' >> terraform.tfvars
terraform plan -out=tfplan && terraform apply tfplan
```

## 5. イメージのビルドとデプロイ

初回だけ手元から push し、以降は GitHub Actions（`.github/workflows/deploy.yml`、
`vars.DEPLOY_ENABLED` で有効化）に任せる。

**先に Firebase（Identity Platform）のクライアント設定を取る**。Vite はこれをビルド時に
埋め込むので、実行時の環境変数では効かない。

> Identity Platform は Firebase Authentication を GCP 製品にしたもので、実体は同じ
> （`identitytoolkit.googleapis.com`）。クライアントは Firebase JS SDK を使うため、変数名が
> `VITE_FIREBASE_*` になっている。Identity Platform を有効化すると
> `Browser key (auto created by Firebase)` という API キーが自動で作られる。

```sh
KEY_UID=$(gcloud services api-keys list --project=$P \
  --filter='displayName:"Browser key"' --format='value(uid)')
export FB_API_KEY=$(gcloud services api-keys get-key-string "$KEY_UID" \
  --project=$P --format='value(keyString)')
echo "$FB_API_KEY"        # AIzaSy... で始まる 39 文字
```

apiKey は秘密ではない（ブラウザに配信される識別子）。保護はドメイン制限と Identity Platform
側の設定で行うので、CI の variables に平文で置いてよい。

**リポジトリ直下で実行する**（`docker build .` が Dockerfile を読むため）。

```sh
cd "$REPO"
gcloud auth configure-docker $REGION-docker.pkg.dev
IMAGE=$REGION-docker.pkg.dev/$P/speclino/speclino:$(git rev-parse --short HEAD)
docker build --platform linux/amd64 -t $IMAGE \
  --build-arg VITE_FIREBASE_API_KEY="$FB_API_KEY" \
  --build-arg VITE_FIREBASE_AUTH_DOMAIN=$P.firebaseapp.com \
  --build-arg VITE_FIREBASE_PROJECT_ID=$P \
  .
docker push $IMAGE

for S in web worker; do
  gcloud run services update speclino-$S --project=$P --region=$REGION --image=$IMAGE
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
| `github_app_id` | 本番 GitHub App の App ID | App 作成後 |

秘密（GitHub App 秘密鍵・Webhook secret）だけは Secret Manager から渡す。MCP トークンは
S54 以降 env に渡さない（ワークスペース単位で DB に持ち、UI から owner が発行する。10 章参照）:

```sh
gcloud run services update speclino-web --project=$P --region=asia-northeast1 \
  --set-secrets="SPECLINO_GITHUB_WEBHOOK_SECRET=github-webhook-secret:latest"
gcloud run services update speclino-worker --project=$P --region=asia-northeast1 \
  --set-secrets="SPECLINO_GITHUB_PRIVATE_KEY=github-app-private-key:latest"
```

web には GitHub App の秘密鍵を渡さない（`LazyCodeHost` により web は秘密鍵なしで起動する）。

## 7. マイグレーションとテストアカウント

**リポジトリ直下で実行する**（alembic の設定を読むため）。

```sh
cd "$REPO"
# Cloud SQL Auth Proxy でローカルから流す
cloud-sql-proxy ${P}:${REGION}:speclino-db &
SPECLINO_DATABASE_URL="postgresql+asyncpg://speclino:<pw>@localhost:5432/speclino" \
  uv run alembic upgrade head

# 審査員用のテストアカウント（Identity Platform）
gcloud identity platform tenants list --project=$P   # 省略可（テナントは使わない）
# コンソール → Identity Platform → ユーザーを追加（メール/パスワード）
```

作った認証情報は提出フォームにのみ記載し、リポジトリには置かない。

## 8. 動作確認

**リポジトリ直下で実行する**。

```sh
cd "$REPO"
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

## 9. ワークスペースの初期設定（S53）

S53 からは、登録されたリポジトリの run しか誰にも見えない（design/2026-09-27-workspaces.md）。
デプロイ直後は既存 project がどのワークスペースにも属していないため、この手順を流すまで
UI に何も出ない。**リポジトリ直下で実行する**必要は無い（`gcloud run jobs execute` はどこからでもよい）。

DB の秘密や GitHub App 秘密鍵を手元へ出さないため、`speclino workspace` CLI を Cloud Run Job
（`speclino-admin`、`infra/envs/prod/run.tf`）で実行する。`--args` は実行のたびに置き換えられる。

**順序（初回）**: S53 を含むイメージを Deploy する**前に** `terraform apply` で `speclino-admin` を作る。
Deploy は migrate の直後、サービスを更新する前に `backfill-repo-ids`（9-2）を流す。Job が無ければ
Deploy はそこで失敗し、サービスは旧イメージのまま残る（backfill 前の新しいサービスが webhook を受けると、
ID の無い既存 project が別リポジトリとして退避され、登録と `settings` を失うため）。

```sh
cd "$REPO"
export P=<project_id>
export REGION=asia-northeast1
```

### 9-1. 自分の uid を確かめる

ログイン後、ブラウザの開発者ツールか `curl` で `GET /api/me` を呼ぶと `{uid, email, workspaces}`
が返る。この `uid` を以降の `--owner-uid` / メンバー登録に使う（Identity Platform の `sub`。
メールではない）。

```sh
curl -s "$WEB_URL/api/me" -H "Authorization: Bearer <ID トークン>"
```

### 9-2. 既存 project の github_repo_id を埋める（Deploy が自動で流す）

改名・削除後の同名リポジトリ再利用を安全に扱うため、まず全 project の GitHub リポジトリ ID を
確定させる（design の codex レビュー #3。名前一致では自動で埋めない）。

```sh
gcloud run jobs execute speclino-admin --project=$P --region=$REGION \
  --args=workspace,backfill-repo-ids --wait
gcloud run jobs logs read speclino-admin --project=$P --region=$REGION --limit=50
```

`isolated` と出た行（ID が確認できない・衝突した project）は team が外れたままになる。ログの
理由（`not_found` / `id_conflict`）を見て、リポジトリの改名・削除やインストール状況を確認する。

### 9-3. 自分のワークスペースを作る

```sh
gcloud run jobs execute speclino-admin --project=$P --region=$REGION \
  --args=workspace,create,<workspace>,--owner-uid,<自分の uid>,--name,<表示名> --wait
```

### 9-4. 審査員をメンバーに追加する（閲覧のみ）

審査員は member（run の閲覧・手動 start・デモ再実行のみ。規約承認はできない）。

```sh
gcloud run jobs execute speclino-admin --project=$P --region=$REGION \
  --args=workspace,add-member,<workspace>,<審査員の uid>,--role,member --wait
```

### 9-5. リポジトリを登録する

`spec-lino` 自身とデモ用リポジトリを登録する（project が無ければ先に webhook を1回受けて
作らせるか、既存の project 行があること。API からは project を新規作成しない）。

```sh
gcloud run jobs execute speclino-admin --project=$P --region=$REGION \
  --args=workspace,add-project,<workspace>,<owner>/<repo> --wait
```

デモリポジトリも同様に繰り返す。登録状況は一覧で確認できる:

```sh
gcloud run jobs execute speclino-admin --project=$P --region=$REGION \
  --args=workspace,list --wait
gcloud run jobs logs read speclino-admin --project=$P --region=$REGION --limit=50
```

### 9-6. PR コメントの承認者を設定する

`/speclino approve-spec` を受け付ける GitHub ログイン名を project ごとに設定する（ワークスペースの
approver ロールはチーム規約の承認用で、別物）。引数を省くと全員外す:

```sh
gcloud run jobs execute speclino-admin --project=$P --region=$REGION \
  --args=workspace,set-pr-approvers,<owner>/<repo>,<GitHub のログイン名> --wait
```

他のワークスペース（自分とデモ以外）を作れるようにするのは S54（ワークスペース単位の MCP
トークン）の後（design の切り替えの順序）。

## 10. MCP トークン（S54）

全体共通の静的トークン（`SPECLINO_MCP_TOKEN` / Secret Manager の `mcp-token`）は廃止し、
ワークスペース単位のトークン（DB の `mcp_tokens`、ハッシュだけ保存）に切り替えた。発行・失効は
UI から owner だけができる（design/2026-09-27-workspaces.md 8.1 節、切り替えの順序）。

**順序（コード先行、続けて Terraform）**:

1. **デプロイ**: S54 を含むイメージへ切り替える（マイグレーション 0005 → サービス更新）。この
   時点で旧静的トークンは使えなくなる。MCP は web ロールで常にマウントされる（DB に有効な
   トークンが無ければ誰も使えない）。旧 secret の注入が残っていてもコードは読まない
2. **すぐに `terraform apply`**（ユーザー確認の上で）: web SA に worker の `run.invoker` を付け
   （`worker_invoker_web`）、`SPECLINO_MCP_TOKEN` の注入を外す。**invoker が付くまで `context_get` は
   web → worker の呼び出しが 403 になり失敗する**（verification_start / verification_get は動く）。
   `mcp-token` の Secret 自体とアクセス権（`secret_accessors`）はロールバック期間が過ぎるまで残す
   （消すと secret が削除対象になり戻せなくなるため）
3. **UI からトークンを発行する**: owner としてログインし、`$WEB_URL/workspaces/<slug>/mcp-tokens`
   を開く。名前を付けて発行すると平文がその場に 1 回だけ表示される（画面を閉じると二度と見えない。
   紛失したら失効させて新しく発行し直す）
4. **エディタの MCP 設定を差し替える**: Claude Code 等の MCP クライアント設定にある
   `Authorization: Bearer <旧 MCP_TOKEN>` を、3 で発行した値に置き換える
5. **`context_get` を実際に呼んで確かめる**: `scripts/smoke_prod.sh` は「Bearer なし・不正なトークンは
   401」しか見ないので、web → worker の委譲（invoker・worker の秘密鍵）が動くことはエディタから
   `context_get` を 1 回呼んで確かめる

## 11. リポジトリの登録（S55）

ワークスペースの owner が画面（ヘッダーの「リポジトリ」→ 追加）から登録する。GitHub で本人が admin で、
SpecLino の GitHub App がインストール済みであることを確かめてから登録する（`guides/github-app-setup.md` 1.1）。

**順序**（値の無い secret を注入するとリビジョンが起動できないので 2 回に分ける）:

1. `terraform apply`（ユーザー確認の上で）: `github-app-client-secret` の入れ物を作る。`github_client_id` が空の
   あいだは web に注入しない
2. GitHub App の設定: Callback URL を完全一致で登録（ワイルドカード無効）、client secret を生成して投入
   （`guides/github-app-setup.md` 1.1）
3. `infra/envs/prod/terraform.tfvars` に `github_client_id = "<Client ID>"` を書き、もう一度 `terraform apply`
   （web に `SPECLINO_GITHUB_CLIENT_ID` と client secret が入る）
4. Deploy（マイグレーション 0006 → サービス更新）。3 と順序は問わないが、両方が済むまで登録は 503
5. 画面から登録を試す: 既に登録済みのリポジトリなら「すでに登録済み」と出る

## 片付け（12/1 以降）

```sh
cd "$REPO/infra/envs/prod" && terraform destroy
gcloud sql instances delete speclino-db --project=$P
# プロジェクト自体は deletion_policy=PREVENT。消すときは bootstrap の main.tf を編集してから
```
