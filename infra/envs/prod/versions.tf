terraform {
  required_version = ">= 1.12"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 8.0"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 8.0"
    }
  }

  # infra/bootstrap で作った GCS バケットを backend にする。
  # バケット名は環境ごとに変わるためここでは空にし、init 時に -backend-config で渡す:
  #   terraform init \
  #     -backend-config="bucket=<infra/bootstrap の state_bucket output>" \
  #     -backend-config="prefix=envs/prod"
  backend "gcs" {}
}

# user_project_override / billing_project は、ユーザー ADC で
# billingbudgets・identitytoolkit を呼ぶために必要。
# これらの API はリクエストに quota project（X-Goog-User-Project ヘッダ）を要求するが、
# provider は user_project_override = true のときしかヘッダを送らない。
# 無いと Google 既定の共有プロジェクト（projects/764086051850）が consumer になり、
# プロジェクト側で API を有効化していても 403 SERVICE_DISABLED になる。
provider "google" {
  project               = var.project_id
  region                = var.region
  user_project_override = true
  billing_project       = var.project_id
}

# Cloud Run sandbox の launch_stage / sandbox_launcher など、
# google プロバイダにまだ GA で降りてきていない属性を使うために使用する。
provider "google-beta" {
  project               = var.project_id
  region                = var.region
  user_project_override = true
  billing_project       = var.project_id
}
