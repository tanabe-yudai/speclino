# infra/envs/prod

SpecLino 本番環境の骨組み: Artifact Registry、Cloud Run（web / worker / sandbox）、
Cloud Tasks、Secret Manager（コンテナのみ）、サービスアカウントと IAM、
Workload Identity Federation（GitHub Actions 用）、Identity Platform（メール/パスワード）、
予算アラート、Cloud Run 5xx の監視アラート。Cloud SQL はまだ作らない（S26 で追加）。

## 適用順序

1. `infra/bootstrap` を apply し、`project_id` と `state_bucket` を確認する
2. このディレクトリで `terraform init`（backend にそのバケットを指定）
3. `terraform plan` / `terraform apply`（**プロジェクト作成ではないが、Cloud Run 公開・
   課金紐付けを含むため apply は必ずユーザーに確認してから実行する**）
4. apply 後、Secret Manager に値を投入する（下記「秘密の投入」）
5. `.github/workflows/deploy.yml` 用の repository secrets（`GCP_WIF_PROVIDER` /
   `GCP_DEPLOYER_SA` / `GCP_PROJECT_ID`）を `terraform output` の値で設定する
6. 初回イメージを push し、`gcloud run services update` で実イメージへ切り替える
   （もしくは push 後に deploy.yml を1回動かす）

## 手順

```sh
cd infra/envs/prod

terraform init \
  -backend-config="bucket=$(cd ../../bootstrap && terraform output -raw state_bucket)" \
  -backend-config="prefix=envs/prod"

cp terraform.tfvars.example terraform.tfvars
# terraform.tfvars を実際の値に編集する
# 値そのものに秘密は含めないが、リポジトリ直下の .gitignore は現状
# *.tfvars を除外していないため、コミットしないよう各自の作業ツリーで注意する
# （必要なら .gitignore に *.tfvars 追加を別途検討）

terraform plan

# ユーザー確認後のみ実行（Cloud Run の公開設定・課金紐付けを含む）
terraform apply
```

## 秘密の投入

このディレクトリでは Secret のコンテナ（`google_secret_manager_secret`）だけを作り、
値は state に載せないよう別途 `gcloud` で投入する。

```sh
echo -n "<値>" | gcloud secrets versions add github-app-private-key --data-file=- --project=<project_id>
echo -n "<値>" | gcloud secrets versions add github-webhook-secret  --data-file=- --project=<project_id>
echo -n "<値>" | gcloud secrets versions add session-secret         --data-file=- --project=<project_id>
openssl rand -hex 32 | tr -d '\n' | gcloud secrets versions add bundle-signing-key --data-file=- --project=<project_id>
```

`bundle-signing-key`（知識の bundle・lock の署名鍵）は、値を投入してから tfvars に
`mount_bundle_signing_key = true` を書いて apply する。web・worker・admin Job に
`SPECLINO_BUNDLE_SIGNING_KEY` として注入される。鍵の更新の間だけ、前の鍵を `bundle-signing-keys-old`
（`kid:secret`）に入れて `mount_bundle_signing_keys_old = true` にする。順序と鍵の更新は
`docs/runbooks/knowledge-registry-cutover.md`。

`mcp-token` には値を投入しない。S54 以降、MCP の認証はワークスペース単位のトークン（DB の
`mcp_tokens`、ハッシュだけ保存）に切り替え、`/workspaces/<slug>/mcp-tokens` で owner が発行する
（`docs/guides/deploy-runbook.md` の「10. MCP トークン」）。この secret のコンテナとアクセス権は
ロールバック期間が過ぎるまで残す（消すと secret 自体が削除対象になり戻せなくなるため）。

アクセス権は secret 単位で付与済み（web: `github-webhook-secret` / `session-secret` /
（legacy）`mcp-token`、worker: `github-app-private-key`、両方: `database-url` / `bundle-signing-key`）。

## PR の terraform plan（`.github/workflows/terraform-plan.yml`）

`infra/**` を変える PR で 2 つのジョブを流す。apply は手で行う。

- `validate`（自動）: `terraform fmt -check -recursive` と `terraform init -backend=false` → `terraform validate`。
  secret・OIDC なし、`contents: read` のみ
- `plan`（承認制）: environment `terraform-plan` の必須レビュアーが承認してから走る。読み取り専用の
  planner SA（`iam.tf` の `planner`）を WIF で使い、`-lock=false -var=user_project_override=false` で
  plan を流して結果を PR コメント 1 件にまとめて更新する

`pull_request` では **PR 自身のワークフローと Terraform（provider・lock ファイルを含む）が動く**。承認者は
ワークフロー・`*.tf`・`.terraform.lock.hcl` の差分を読み、planner の資格情報と tfvars を渡してよいと
判断してから承認する（承認しなければ secret も OIDC の planner も渡らない）。`pull_request_target` は使わない。

権限の境界:

- WIF（`wif.tf`）は SA ごとに claim で絞る。planner は `attribute.environment/terraform-plan`、deployer は
  `attribute.workflow_ref/<owner>/<repo>/.github/workflows/deploy.yml@refs/heads/main` だけ。PR で
  書き換えたワークフローや他のブランチから deployer にはなれない
- planner SA は書込み・actAs・`secretAccessor`・`serviceusage.services.use` を持たない。
  `user_project_override=false` で流すため quota project のヘッダを送らない（SA の資格情報では SA の
  プロジェクトが quota project になる）。手元のユーザー ADC では既定の `true` のまま使う
- secret の値は refresh で読まない（`google_secret_manager_secret_version` のリソース・データソースが
  無く、Cloud Run は `secret_key_ref` で参照するだけ）
- 課金アカウントへの `roles/billing.viewer` 付与は、課金アカウント管理者でないと apply できない
- fork からの PR と、environment secret が無いあいだは plan を飛ばす

有効にする手順（順番どおり）:

1. GitHub の Settings → Environments で `terraform-plan` を作り、Required reviewers を設定する
   （Deployment branches は PR のブランチで走らせるため制限しない）。apply より先に作る:
   ワークフローが参照した時点で environment は保護なしで自動作成され、planner の WIF 紐付けが
   ある状態で保護が無いと、承認なしで planner を使えてしまう
2. `terraform apply`（planner SA・ロール・WIF の claim 対応と SA ごとの紐付け）
3. environment `terraform-plan` の **environment secret**（repository secret ではない）に設定する
   - `GCP_PLANNER_SA` = `terraform output -raw planner_sa`
   - `TF_PROD_TFVARS` = 手元の `terraform.tfvars` の中身そのまま（`*.tfvars` は .gitignore 対象。
     ジョブはランナー内で `terraform.tfvars` に書き出す。PR コメントでは project ID と課金アカウント ID を
     伏せる）。tfvars を変えたら更新する
4. deploy.yml は main から起動する（push、または main を選んだ workflow_dispatch）。他のブランチで
   workflow_dispatch しても WIF で拒否される

## 既知の TODO

- `run.tf` の sandbox サービスは `launch_stage = "BETA"` と `sandbox_launcher = true` を
  試みている。`terraform validate`（google-beta 8.3.0）では受理されたが、実際の
  Cloud Run sandboxes PoC（TODO(S38)）で apply して初めて有効性を確認できる。
  provider が拒否する場合はこの2属性をコメントアウトすること。
- `identity.tf` の `authorized_domains` は Cloud Run web の URL 確定後（初回 apply後）に
  追加する（TODO(S13)）。
- `budget.tf` の `google_billing_budget` は課金アカウント側の IAM 権限が別途必要になる
  場合がある。

## 検証済みコマンド（apply は含まない）

```sh
terraform init -backend=false
terraform validate
terraform fmt -check
```
