# web / worker / sandbox は同一イメージを SPECLINO_ROLE で切り替えるだけの
# 3つの Cloud Run サービス（design: 「本番は同一イメージを役割別 Cloud Run サービスに
# 分ける」）。sandbox だけ Cloud Run sandboxes（Preview）の launch_stage / sandbox_launcher
# を使うため、provider を google-beta に揃えている（for_each の1リソース内では
# provider を条件分岐できないため。google-beta は GA リソースの上位互換）。
locals {
  cloud_run_services = {
    web = {
      cpu    = "1"
      memory = "1Gi"
    }
    worker = {
      cpu    = "1"
      memory = "2Gi"
    }
    sandbox = {
      cpu    = "1"
      memory = "1Gi"
    }
  }
}

resource "google_cloud_run_v2_service" "services" {
  for_each = local.cloud_run_services
  provider = google-beta

  project  = var.project_id
  name     = "speclino-${each.key}"
  location = var.region

  # IAM (run.invoker) 側で web のみ公開・worker/sandbox は呼出し元 SA に限定する。
  # ingress 自体は全サービス共通で ALL のままにする（design の構成表どおり）。
  ingress = "INGRESS_TRAFFIC_ALL"

  # sandbox サービスは Cloud Run sandboxes（Preview）を使うため BETA を指定する。
  # google-beta provider がこの属性を受け付けない場合は下記をコメントアウトし、
  # TODO(S38): Cloud Run sandboxes PoC で改めて有効化する。
  launch_stage = each.key == "sandbox" ? "BETA" : null

  template {
    service_account = google_service_account.this[each.key].email

    # パイプライン全体15分 < Cloud Run timeout 20分 < Cloud Tasks dispatch deadline 25分
    # （design の「時間と冪等性」）に合わせて 1200s。
    timeout = "1200s"

    scaling {
      min_instance_count = 0
      max_instance_count = 2
    }

    containers {
      image = var.image

      # sandbox サービスがサンドボックス起動を担うコンテナであることを示す。
      # google-beta provider がこの属性を受け付けない場合は下記をコメントアウトし、
      # TODO(S38): Cloud Run sandboxes PoC で改めて有効化する。
      sandbox_launcher = each.key == "sandbox" ? true : null

      resources {
        limits = {
          cpu    = each.value.cpu
          memory = each.value.memory
        }
      }

      env {
        name  = "SPECLINO_ROLE"
        value = each.key
      }
      env {
        name  = "SPECLINO_ENV"
        value = "prod"
      }
      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }
      env {
        name  = "GOOGLE_CLOUD_LOCATION"
        value = "global"
      }
      env {
        name  = "GOOGLE_GENAI_USE_ENTERPRISE"
        value = "true"
      }
    }
  }

  lifecycle {
    ignore_changes = [
      # イメージの更新は .github/workflows/deploy.yml の
      # `gcloud run services update --image` が行い、Terraform の変数は書き換えない
      # 運用にする。ここで追従すると deploy.yml の更新を毎回 plan が差分として
      # 検出してしまうため無視する。
      template[0].containers[0].image,
    ]
  }
}

# web: 誰でも呼び出せる（Identity Platform の ID トークン検証は API 側で行う）。
resource "google_cloud_run_v2_service_iam_member" "web_public" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.services["web"].name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# worker: tasks SA（Cloud Tasks の OIDC）からのみ呼出し可能。
resource "google_cloud_run_v2_service_iam_member" "worker_invoker" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.services["worker"].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.this["tasks"].email}"
}

# sandbox: worker SA からのみ呼出し可能。
resource "google_cloud_run_v2_service_iam_member" "sandbox_invoker" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.services["sandbox"].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.this["worker"].email}"
}
