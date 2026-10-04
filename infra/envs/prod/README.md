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
```

`mcp-token` には値を投入しない。S54 以降、MCP の認証はワークスペース単位のトークン（DB の
`mcp_tokens`、ハッシュだけ保存）に切り替え、`/workspaces/<slug>/mcp-tokens` で owner が発行する
（`guides/deploy-runbook.md` の「10. MCP トークン」）。この secret のコンテナとアクセス権は
ロールバック期間が過ぎるまで残す（消すと secret 自体が削除対象になり戻せなくなるため）。

アクセス権は secret 単位で付与済み（web: `github-webhook-secret` / `session-secret` /
（legacy）`mcp-token`、worker: `github-app-private-key`）。

## PR の terraform plan（`.github/workflows/terraform-plan.yml`）

`infra/**` を変える PR で plan を流し、結果を PR コメント 1 件にまとめて更新する。apply は手で行う。
読み取り専用の planner SA（`iam.tf` の `planner`）を WIF で使い、`-lock=false` で流す。

- secret の値は refresh で読まない（`google_secret_manager_secret_version` のリソース・データソースが
  無く、Cloud Run は `secret_key_ref` で参照するだけ）ので、`secretAccessor` は付けず refresh も行う
- 課金アカウントへの `roles/billing.viewer` 付与は、課金アカウント管理者でないと apply できない
- fork からの PR と、下の secret が無いあいだはジョブを飛ばす

有効にする手順:

1. `terraform apply`（planner SA・ロール・WIF の紐付けが増える）
2. repository secret `GCP_PLANNER_SA` に `terraform output -raw planner_sa` の値を設定する
3. repository secret `TF_PROD_TFVARS` に、手元の `terraform.tfvars` の中身をそのまま設定する
   （`*.tfvars` は .gitignore 対象。ジョブはこれをランナー内で `terraform.tfvars` に書き出す。
   PR コメントでは project ID と課金アカウント ID を伏せる）。tfvars を変えたら更新する

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
