# Secret のコンテナだけを作る。値は Terraform state に載せたくないため
# ここでは作らず、apply 後に手動で投入する:
#
#   echo -n "<値>" | gcloud secrets versions add github-app-private-key --data-file=- --project=<project_id>
#   echo -n "<値>" | gcloud secrets versions add github-webhook-secret  --data-file=- --project=<project_id>
#   echo -n "<値>" | gcloud secrets versions add mcp-token              --data-file=- --project=<project_id>
#   echo -n "<値>" | gcloud secrets versions add session-secret         --data-file=- --project=<project_id>
#
# アクセス権は secret 単位で付与する（web: webhook-secret, session-secret, mcp-token /
# worker: github-app-private-key）。design のアーキテクチャ表に従う。

locals {
  secret_accessors = {
    "github-app-private-key" = ["worker"]
    "github-webhook-secret"  = ["web"]
    "mcp-token"              = ["web"]
    "session-secret"         = ["web"]
  }

  secret_accessor_bindings = {
    for pair in flatten([
      for secret_id, sa_keys in local.secret_accessors : [
        for sa_key in sa_keys : {
          key       = "${secret_id}-${sa_key}"
          secret_id = secret_id
          sa_key    = sa_key
        }
      ]
    ]) : pair.key => pair
  }
}

resource "google_secret_manager_secret" "this" {
  for_each = local.secret_accessors

  project   = var.project_id
  secret_id = each.key

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_iam_member" "accessors" {
  for_each = local.secret_accessor_bindings

  project   = var.project_id
  secret_id = google_secret_manager_secret.this[each.value.secret_id].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.this[each.value.sa_key].email}"
}
