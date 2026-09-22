# SpecLino 用の新規 GCP プロジェクトと、tfstate 用バケットを含む基盤リソース。
# 実行順序: このディレクトリを apply → infra/envs/prod で backend-config にこの state_bucket を渡す。
#
# 注意: google_project の作成・billing_account の紐付けは、実際に apply する前に
# 必ずユーザーへ確認すること（CLAUDE.md の「確認が必要な操作」）。

resource "google_project" "this" {
  name       = var.project_id
  project_id = var.project_id

  # 個人アカウント（組織なし）の場合は org_id / folder_id を両方 null のままにする。
  org_id    = var.org_id
  folder_id = var.folder_id

  billing_account = var.billing_account

  # 誤って `terraform destroy` してもプロジェクトが即消えないようにする。
  # 本当に削除する場合は先にこの値を "ABANDON" 等へ変更してから destroy する。
  deletion_policy = "PREVENT"
}

locals {
  # S12 完了条件で有効化する API。sqladmin は S26 (Cloud SQL) 用に先行して有効化する。
  bootstrap_apis = [
    "run.googleapis.com",
    "cloudtasks.googleapis.com",
    "secretmanager.googleapis.com",
    "artifactregistry.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "sts.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "serviceusage.googleapis.com",
    "aiplatform.googleapis.com",
    "identitytoolkit.googleapis.com",
    "cloudbilling.googleapis.com",
    "billingbudgets.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "cloudtrace.googleapis.com",
    "sqladmin.googleapis.com", # S26 (Cloud SQL) 用に先行有効化
    "storage.googleapis.com",
  ]
}

resource "google_project_service" "apis" {
  for_each = toset(local.bootstrap_apis)

  project = google_project.this.project_id
  service = each.value

  # プロジェクト自体を削除するときに一緒に無効化されればよいので、
  # destroy 時の個別無効化は行わない（他サービスへの依存無効化も避ける）。
  disable_dependent_services = false
  disable_on_destroy         = false
}

# tfstate バケット。infra/envs/prod からは
# `terraform init -backend-config="bucket=<state_bucket output>"` で参照する。
resource "google_storage_bucket" "tfstate" {
  project  = google_project.this.project_id
  name     = "${var.project_id}-tfstate"
  location = "ASIA-NORTHEAST1"

  uniform_bucket_level_access = true

  # state を誤って失わないよう、force_destroy はしない。
  force_destroy = false

  versioning {
    enabled = true
  }

  depends_on = [google_project_service.apis]
}
