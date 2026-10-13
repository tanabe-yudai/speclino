# サービスアカウントと IAM。ロール構成は web / worker / sandbox の3サービスごとに
# 下表のとおり。
#
#   web:      cloudsql.client / aiplatform.user（S59: A2A の議論役）/ cloudtasks.enqueuer / tasks SA への serviceAccountUser /
#             worker サービスへの run.invoker（run.tf、S54: MCP の context_get を委譲） /
#             成果物バケットへの storage.objectViewer（storage.tf、読むだけ）
#             （secretAccessor は web 用 secret に限定するため secrets.tf 側で個別付与）
#   worker:   cloudsql.client / secretAccessor（secrets.tf で個別付与）/ aiplatform.user /
#             成果物バケットへの storage.objectAdmin（storage.tf） /
#             sandbox サービスへの run.invoker（run.tf） / logging.logWriter / cloudtrace.agent
#   sandbox:  ロールなし（権限ゼロ SA）
#   tasks:    worker サービスへの run.invoker（run.tf、Cloud Tasks の OIDC トークン発行用 SA）
#   deployer: run.developer / artifactregistry.writer / web・worker・sandbox SA への
#             serviceAccountUser（GitHub Actions からのデプロイ用）
#   planner:  読み取り専用（PR の terraform plan 用、.github/workflows/terraform-plan.yml）。
#             viewer / iam.securityReviewer / storage.bucketViewer /
#             課金アカウントの billing.viewer / tfstate バケットの storage.objectViewer。
#             書込み・actAs・secretAccessor は持たない

locals {
  service_accounts = {
    web      = "speclino-web"
    worker   = "speclino-worker"
    sandbox  = "speclino-sandbox"
    tasks    = "speclino-tasks"
    deployer = "speclino-deployer"
    planner  = "speclino-planner"
  }
}

resource "google_service_account" "this" {
  for_each = local.service_accounts

  project      = var.project_id
  account_id   = each.value
  display_name = "SpecLino ${each.key}"
}

# --- web -------------------------------------------------------------------

resource "google_project_iam_member" "web_cloudsql_client" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.this["web"].email}"
}

resource "google_project_iam_member" "web_cloudtasks_enqueuer" {
  project = var.project_id
  role    = "roles/cloudtasks.enqueuer"
  member  = "serviceAccount:${google_service_account.this["web"].email}"
}

# Cloud Tasks の OIDC トークンを tasks SA として発行するために actAs が要る。
resource "google_service_account_iam_member" "web_actas_tasks" {
  service_account_id = google_service_account.this["tasks"].name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.this["web"].email}"
}

# --- worker ------------------------------------------------------------------

resource "google_project_iam_member" "worker_cloudsql_client" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.this["worker"].email}"
}

resource "google_project_iam_member" "worker_aiplatform_user" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.this["worker"].email}"
}

# web の A2A の議論役（ADK + Gemini）が Vertex AI を呼ぶ
resource "google_project_iam_member" "web_aiplatform_user" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.this["web"].email}"
}

resource "google_project_iam_member" "worker_logging_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.this["worker"].email}"
}

resource "google_project_iam_member" "worker_cloudtrace_agent" {
  project = var.project_id
  role    = "roles/cloudtrace.agent"
  member  = "serviceAccount:${google_service_account.this["worker"].email}"
}

# storage.objectAdmin（成果物バケット）は storage.tf、
# run.invoker（sandbox サービス）は run.tf、
# secretAccessor（github-app-private-key）は secrets.tf 側で定義する。

# --- sandbox -----------------------------------------------------------------
# ロールなし。run.invoker（worker からの呼出し許可）は run.tf 側の
# google_cloud_run_v2_service_iam_member で定義する（サービス側の IAM のため）。

# --- tasks ---------------------------------------------------------------
# run.invoker（worker サービス呼出し用）は run.tf 側で定義する。

# --- deployer（GitHub Actions） -----------------------------------------------

resource "google_project_iam_member" "deployer_run_developer" {
  project = var.project_id
  role    = "roles/run.developer"
  member  = "serviceAccount:${google_service_account.this["deployer"].email}"
}

resource "google_artifact_registry_repository_iam_member" "deployer_writer" {
  project    = var.project_id
  location   = google_artifact_registry_repository.speclino.location
  repository = google_artifact_registry_repository.speclino.repository_id
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.this["deployer"].email}"
}

# deploy.yml が `gcloud run services update` するために web/worker/sandbox の
# 実行 SA を actAs できる必要がある。
resource "google_service_account_iam_member" "deployer_actas" {
  for_each = toset(["web", "worker", "sandbox"])

  service_account_id = google_service_account.this[each.value].name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.this["deployer"].email}"
}

# --- planner（GitHub Actions の terraform plan、読み取り専用） ------------------
# apply は手で行う（README）。PR では plan だけを流すので、state と管理対象の読み取りに
# 必要な最小のロールに絞る。secret の値は refresh で読まない（secret のコンテナと IAM、
# Cloud Run の secret_key_ref は参照だけで、secret_version のリソース・データソースは無い）
# ため secretAccessor は付けない。
#
#   viewer                       Cloud Run / Tasks / AR / Secret のメタデータ / WIF / Identity Platform /
#                                監視 / SA と project・AR・secret・Run の IAM ポリシーの読取り
#   iam.securityReviewer         viewer に無い IAM ポリシーの読取り（storage.buckets.getIamPolicy など）
#   storage.bucketViewer         成果物バケットのメタデータ（storage.buckets.get は viewer に無い）
#
# serviceusage.serviceUsageConsumer は付けない（quota・課金の消費を許すため）。terraform-plan.yml は
# -var=user_project_override=false で流し、X-Goog-User-Project を送らない。SA の資格情報では
# client-based API の quota project が SA の作られたプロジェクト（このプロジェクト）になる。
locals {
  planner_project_roles = toset([
    "roles/viewer",
    "roles/iam.securityReviewer",
    "roles/storage.bucketViewer",
  ])
}

resource "google_project_iam_member" "planner" {
  for_each = local.planner_project_roles

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.this["planner"].email}"
}

# google_billing_budget（budget.tf）の refresh は課金アカウント側の billing.budgets.get が要る。
# 課金アカウントへの IAM 付与は課金アカウント管理者でないと apply できない。
resource "google_billing_account_iam_member" "planner_billing_viewer" {
  billing_account_id = var.billing_account
  role               = "roles/billing.viewer"
  member             = "serviceAccount:${google_service_account.this["planner"].email}"
}

# tfstate バケットは infra/bootstrap が "<project_id>-tfstate" の名前で作る（ここでは管理しない）。
# plan は -lock=false で流すので、ロックファイルの書込み権限は要らない。
resource "google_storage_bucket_iam_member" "planner_tfstate_viewer" {
  bucket = "${var.project_id}-tfstate"
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.this["planner"].email}"
}
