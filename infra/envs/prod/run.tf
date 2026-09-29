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

  web_env = merge(local.web_queue_env, local.web_github_oauth_env, {
    SPECLINO_AUTH_KIND           = "identity_platform"
    SPECLINO_FIREBASE_PROJECT_ID = var.project_id
  })

  # GitHub App のユーザー認可（S55 のリポジトリ登録）。Client ID は秘密ではない
  web_github_oauth_env = var.github_client_id == "" ? {} : {
    SPECLINO_GITHUB_CLIENT_ID = var.github_client_id
  }

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

# worker: web SA からも呼出し可能にする（S54）。MCP の context_get はスナップショット取得に
# GitHub App の秘密鍵が要るが、web はその鍵を持たない（LazyCodeHost）ため、web は OIDC で
# worker の POST /internal/mcp/context に委譲する（design/2026-09-27-workspaces.md 8.1 節）。
resource "google_cloud_run_v2_service_iam_member" "worker_invoker_web" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.services["worker"].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.this["web"].email}"
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

# 本番のマイグレーション。deploy.yml がサービス更新の前に同じイメージで実行する。
# 秘密（DB の接続 URL）を GitHub Actions のランナーへ出さないため、ランナーから
# cloud-sql-proxy で直接繋がず、サービスと同じ接続経路（/cloudsql ソケット）を使う。
# 実行 SA は worker を流用する（cloudsql.client と database-url の参照権を既に持ち、
# deployer も actAs できる）。
#
# サービスより先にスキーマが進むので、マイグレーションは旧コードが新スキーマで動く形
# （テーブル・列の追加など）に限る。
resource "google_cloud_run_v2_job" "migrate" {
  count = var.cloudsql_instance != "" && var.mount_secrets ? 1 : 0

  project  = var.project_id
  name     = "speclino-migrate"
  location = var.region

  # 誤って消すとデプロイが止まるだけだが、terraform destroy で巻き込まない。
  deletion_protection = false

  template {
    task_count = 1

    template {
      service_account = google_service_account.this["worker"].email
      timeout         = "300s"
      # 失敗したマイグレーションを自動で再実行しない（原因を見てから流し直す）。
      max_retries = 0

      containers {
        image   = var.image
        command = ["uv", "run", "alembic", "upgrade", "head"]

        env {
          name = "SPECLINO_DATABASE_URL"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.this["database-url"].secret_id
              version = "latest"
            }
          }
        }

        volume_mounts {
          name       = "cloudsql"
          mount_path = "/cloudsql"
        }
      }

      volumes {
        name = "cloudsql"
        cloud_sql_instance {
          instances = [var.cloudsql_instance]
        }
      }
    }
  }

  lifecycle {
    # サービスと同じく、イメージは deploy.yml の `gcloud run jobs update --image` が更新する。
    ignore_changes = [template[0].template[0].containers[0].image]
  }
}

# ワークスペースの管理操作（作成・メンバー登録・project 登録・repository ID の backfill）。
# DB の秘密や GitHub App 秘密鍵を手元へ出さないため、`speclino` CLI（`speclino.cli:app`）を
# Cloud Run Job として実行できるようにする。
# `gcloud run jobs execute speclino-admin --args=workspace,create,... --wait` のように、
# 実行のたびに `--args` でサブコマンドを渡す（`--args` は実行ごとに置き換えられる。公式ドキュメント
# で確認済み。design/2026-09-27-workspaces.md 参照）。イメージ・SA・DB 接続は migrate Job と揃える。
# worker SA を流用するのは、DB（cloudsql.client / database-url の secretAccessor）と
# GitHub App 秘密鍵（github-app-private-key の secretAccessor）の参照権を既に持っているため
# （secrets.tf）。新たな IAM 付与は不要。
resource "google_cloud_run_v2_job" "admin" {
  count = var.cloudsql_instance != "" && var.mount_secrets ? 1 : 0

  project  = var.project_id
  name     = "speclino-admin"
  location = var.region

  # migrate と同じく、誤って消してもデプロイ全体を止めない（terraform destroy にも巻き込まない）。
  deletion_protection = false

  template {
    task_count = 1

    template {
      service_account = google_service_account.this["worker"].email
      timeout         = "300s"
      # 誤操作の再実行を自動でしない（内容を見てから流し直す。migrate と同じ理由）。
      max_retries = 0

      containers {
        image = var.image
        # `uv run speclino` がイメージ内の CLI エントリポイント（pyproject.toml の
        # [project.scripts]）。args は空のままにし、`gcloud run jobs execute --args` で渡す。
        command = ["uv", "run", "speclino"]

        # backfill-repo-ids が GitHub App の App ID を要る（秘密ではないので env で渡す）。
        # 空のあいだ（App 未作成）はこの Job にも渡さない（worker サービスと同じ条件）。
        dynamic "env" {
          for_each = local.worker_github_env
          content {
            name  = env.key
            value = env.value
          }
        }

        env {
          name = "SPECLINO_DATABASE_URL"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.this["database-url"].secret_id
              version = "latest"
            }
          }
        }

        # backfill-repo-ids が installation token を取るのに要る。worker サービスと同じ
        # Secret Manager の秘密を同じ環境変数名で渡す。
        env {
          name = "SPECLINO_GITHUB_PRIVATE_KEY"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.this["github-app-private-key"].secret_id
              version = "latest"
            }
          }
        }

        volume_mounts {
          name       = "cloudsql"
          mount_path = "/cloudsql"
        }
      }

      volumes {
        name = "cloudsql"
        cloud_sql_instance {
          instances = [var.cloudsql_instance]
        }
      }
    }
  }

  lifecycle {
    # サービス・migrate と同じく、イメージは deploy.yml 側で更新する。
    ignore_changes = [template[0].template[0].containers[0].image]
  }
}
