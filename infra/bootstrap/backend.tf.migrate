# bootstrap の state を、bootstrap 自身が作った GCS バケットへ移すための backend 定義。
#
# 最初の apply 時点ではこのバケットがまだ存在しないため、既定ではこのファイルを
# `.tf` として置かない（ローカル state で apply する）。バケットができた後に
# `backend.tf` として有効化し、`terraform init -migrate-state` で移行する。
# 手順は guides/deploy-runbook.md「bootstrap の state を GCS へ移す」を参照。
terraform {
  backend "gcs" {}
}
