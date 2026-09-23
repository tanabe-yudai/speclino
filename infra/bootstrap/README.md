# infra/bootstrap

SpecLino 専用の GCP プロジェクトを新規作成し、基盤 API の有効化と tfstate 用 GCS バケットを用意する。

state は**初回だけローカル**（`terraform.tfstate`）。tfstate バケット自体をここで作るため、
最初からそこには置けないという鶏卵問題への対処。apply 後に `backend.tf.migrate` を
`backend.tf` にコピーして `terraform init -migrate-state` すれば、作ったバケットへ移せる
（手順は `guides/deploy-runbook.md`「bootstrap の state を GCS へ移す」）。

`infra/envs/prod` は最初からこのバケットを backend にする（prefix は `envs/prod`）。

## 前提

- gcloud アカウント: `13311633+tanabe-yudai@users.noreply.github.com`
- 課金アカウント ID: `XXXXXX-XXXXXX-XXXXXX`（有効であることを確認済み。ただし apply 直前に再確認する）
- `project_id`（既定 `example-project`）が実際に利用可能（他プロジェクトで未使用）かは **apply 前に `gcloud projects describe <project_id>` 等で確認する**

## 手順

**プロジェクト作成・課金紐付けを伴うため、apply は必ずユーザーに確認してから実行する。**

```sh
cd infra/bootstrap
terraform init

# 差分確認
terraform plan \
  -var project_id=example-project \
  -var billing_account=XXXXXX-XXXXXX-XXXXXX

# ユーザー確認後のみ実行
terraform apply \
  -var project_id=example-project \
  -var billing_account=XXXXXX-XXXXXX-XXXXXX
```

組織配下で作る場合は `-var org_id=<組織ID>` または `-var folder_id=<フォルダID>` を追加する（個人アカウントで組織を使わない場合は指定不要）。

## apply 後

```sh
terraform output state_bucket
```

この値を `infra/envs/prod` の `terraform init -backend-config="bucket=<値>"` に使う。

## 有効化する API

run / cloudtasks / secretmanager / artifactregistry / iam / iamcredentials / sts / cloudresourcemanager /
serviceusage / aiplatform / identitytoolkit / cloudbilling / billingbudgets / logging / monitoring / cloudtrace /
sqladmin（S26 の Cloud SQL 用に先行有効化）/ storage
