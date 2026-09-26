# web / worker / sandbox は同一イメージを SPECLINO_ROLE で切り替えるだけの
# 3つの Cloud Run サービス（design: 「本番は同一イメージを役割別 Cloud Run サービスに
# 分ける」）。sandbox だけ Cloud Run sandboxes（Preview）の launch_stage / sandbox_launcher
# を使うため、provider を google-beta に揃えている（for_each の1リソース内では
# provider を条件分岐できないため。google-beta は GA リソースの上位互換）。
locals {
  # timeout / concurrency も役割ごとに決める。
  # パイプライン全体15分 < Cloud Run timeout 20分 < Cloud Tasks dispatch deadline 25分
  # （design の「時間と冪等性」）なので web / worker は 1200s。
  cloud_run_services = {
    web = {
      cpu         = "1"
      memory      = "1Gi"
      timeout     = "1200s"
      concurrency = 80
    }
    worker = {
      cpu         = "1"
      memory      = "2Gi"
      timeout     = "1200s"
      concurrency = 80
    }
  }

  # Preview 機能（Cloud Run sandboxes）を使う sandbox サービスは opt-in。
  # 初回 apply で web/worker まで巻き込んで失敗しないよう、既定では作らない。
  #
  # sandbox は未信頼コードを動かす。Cloud Run sandboxes はサンドボックス単位の CPU・メモリ
  # 上限を指定できず親コンテナと資源を共有するため（S38）、親の側で締めて影響範囲を限る:
  #   concurrency = 1  1 インスタンスで同時に 1 実行だけ。未信頼コードが資源を食い潰しても
  #                    同じコンテナで動く別の run を巻き込まない。**これが一番効く**
  #   memory 512Mi     ローカル Docker の --memory 512m と揃える（両環境の保証を比較可能にする）
  #   timeout 300s     test_run の TIMEOUT_S = 120s に対して十分な余裕。1200s だと、止まらない
  #                    サンドボックスが 20 分インスタンスを占有しうる
  sandbox_service = var.enable_sandbox ? {
    sandbox = {
      cpu         = "1"
      memory      = "512Mi"
      timeout     = "300s"
      concurrency = 1
    }
  } : {}

  all_services = merge(local.cloud_run_services, local.sandbox_service)

  # DB を使う役割（sandbox はテスト実行だけなので使わない）。
  uses_cloudsql = {
    web     = var.cloudsql_instance != ""
    worker  = var.cloudsql_instance != ""
    sandbox = false
  }

  # 全サービス共通の環境変数。
  common_env = {
    SPECLINO_ENV                = "prod"
    GOOGLE_CLOUD_PROJECT        = var.project_id
    GOOGLE_CLOUD_LOCATION       = "global"
    GOOGLE_GENAI_USE_ENTERPRISE = "true"
    # 成果物は GCS に置く（既定の fs は Cloud Run のインスタンスごとに消える）。
    SPECLINO_BLOB_KIND  = "gcs"
    SPECLINO_GCS_BUCKET = google_storage_bucket.artifacts.name
  }

  # web だけが Cloud Tasks にタスクを作る（worker は自分で enqueue しない）。
  # `gcloud run services update --set-env-vars` で足すと次の apply で消えるため、
  # 環境変数は Terraform 側で持つ（run.tf の ignore_changes はイメージのみ）。
  #
  # worker の URL を同じリソースの別インスタンスから参照すると循環になるので、
  # public_url と同じく変数で受け取り 2 回目の apply で解決する。
  web_queue_env = var.worker_url == "" ? {} : {
    SPECLINO_QUEUE_KIND                             = "cloud_tasks"
    SPECLINO_CLOUD_TASKS_PROJECT                    = var.project_id
    SPECLINO_CLOUD_TASKS_LOCATION                   = var.region
    SPECLINO_CLOUD_TASKS_QUEUE                      = google_cloud_tasks_queue.runs.name
    SPECLINO_CLOUD_TASKS_WORKER_URL                 = var.worker_url
    SPECLINO_CLOUD_TASKS_OIDC_SERVICE_ACCOUNT_EMAIL = google_service_account.this["tasks"].email
  }

  web_env = merge(local.web_queue_env, {
    SPECLINO_AUTH_KIND           = "identity_platform"
    SPECLINO_FIREBASE_PROJECT_ID = var.project_id
  })

  worker_github_env = var.github_app_id == "" ? {} : { SPECLINO_GITHUB_APP_ID = var.github_app_id }

  # sandbox_url が空のあいだ worker は隔離実行を無効のままにする（S38）。
  # sandbox サービス自身の uri を参照すると同一リソース内の循環になるため、worker_url と
  # 同じく変数で受け取り 2 回目の apply で解決する。
  worker_sandbox_env = var.sandbox_url == "" ? {} : {
    SPECLINO_SANDBOX_KIND = "cloud_run"
    SPECLINO_SANDBOX_URL  = var.sandbox_url
  }

  worker_env = merge(local.worker_github_env, local.worker_sandbox_env)

  service_env = {
    web     = merge(local.common_env, local.web_env, local.public_url_env)
    worker  = merge(local.common_env, local.worker_env, local.public_url_env)
    sandbox = local.common_env
  }

  public_url_env = var.public_url == "" ? {} : { SPECLINO_PUBLIC_URL = var.public_url }
}

resource "google_cloud_run_v2_service" "services" {
  for_each = local.all_services
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

    timeout                          = each.value.timeout
    max_instance_request_concurrency = each.value.concurrency

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

      dynamic "env" {
        for_each = local.service_env[each.key]
        content {
          name  = env.key
          value = env.value
        }
      }

      # Secret Manager の値は参照だけを宣言する（値は Terraform の state に入らない）。
      dynamic "env" {
        for_each = var.mount_secrets ? local.secret_env[each.key] : {}
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.this[env.value].secret_id
              version = "latest"
            }
          }
        }
      }

      # 接続 URL の host=/cloudsql/<接続名> が指すソケットの置き場所。
      dynamic "volume_mounts" {
        for_each = local.uses_cloudsql[each.key] ? [1] : []
        content {
          name       = "cloudsql"
          mount_path = "/cloudsql"
        }
      }
    }

    # web / worker とも Cloud SQL に Unix ソケットで繋ぐ（S26）。
    # web は run の一覧・詳細を読み、webhook で run を作る。sandbox は DB を使わない。
    dynamic "volumes" {
      for_each = local.uses_cloudsql[each.key] ? [1] : []
      content {
        name = "cloudsql"
        cloud_sql_instance {
          instances = [var.cloudsql_instance]
        }
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

# sandbox: worker SA からのみ呼出し可能（enable_sandbox のときだけ作る）。
resource "google_cloud_run_v2_service_iam_member" "sandbox_invoker" {
  count = var.enable_sandbox ? 1 : 0

  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.services["sandbox"].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.this["worker"].email}"
}
