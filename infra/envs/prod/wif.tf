# GitHub Actions から WIF で deployer SA / planner SA を使うための Workload Identity Federation。
# attribute_condition でこのリポジトリ以外からの assertion を拒否し、SA ごとの workloadIdentityUser は
# OIDC トークンの claim で絞る（リポジトリ単位で両方を許すと、id-token: write を持つ任意の
# ワークフロー、PR で書き換えた ci.yml も含め、deployer になれてしまう）。
#   deployer: main の deploy.yml だけ（attribute.workflow_ref）
#   planner:  environment terraform-plan のジョブだけ（attribute.environment。承認者つきの environment）

resource "google_iam_workload_identity_pool" "github" {
  project                   = var.project_id
  workload_identity_pool_id = "github-actions"
  display_name              = "GitHub Actions"
  description               = "SpecLino の deploy.yml から Cloud Run / Artifact Registry へアクセスするための WIF プール"
}

resource "google_iam_workload_identity_pool_provider" "github" {
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github"
  display_name                       = "GitHub OIDC"

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
    "attribute.ref"        = "assertion.ref"
    # 例: "<owner>/<repo>/.github/workflows/deploy.yml@refs/heads/main"
    "attribute.workflow_ref" = "assertion.workflow_ref"
    "attribute.event_name"   = "assertion.event_name"
    # environment claim は environment を参照するジョブのトークンにしか無い。無いときは
    # どの紐付けにも一致しない値にする（欠けた claim の扱いに依存しない）。
    "attribute.environment" = "has(assertion.environment) ? assertion.environment : \"__none__\""
  }

  # このリポジトリ以外からの OIDC トークンでは deployer SA を使えないようにする。
  attribute_condition = "assertion.repository == \"${var.github_repository}\""

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

# deploy.yml は main への push と workflow_dispatch で走る。どちらも main から起動すれば
# workflow_ref は "<repo>/.github/workflows/deploy.yml@refs/heads/main" になる。他のブランチからの
# workflow_dispatch、PR、他のワークフローは一致しない。
resource "google_service_account_iam_member" "deployer_wif" {
  service_account_id = google_service_account.this["deployer"].name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.workflow_ref/${var.github_repository}/.github/workflows/deploy.yml@refs/heads/main"
}

# terraform-plan.yml（PR の plan）が使う読み取り専用の SA。environment terraform-plan
# （必須レビュアーの承認後にだけジョブが走る）を参照するジョブに限る。
resource "google_service_account_iam_member" "planner_wif" {
  service_account_id = google_service_account.this["planner"].name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.environment/terraform-plan"
}
