variable "project_id" {
  description = "infra/bootstrap で作成した GCP プロジェクト ID"
  type        = string
}

variable "region" {
  description = "デプロイ先リージョン"
  type        = string
  default     = "asia-northeast1"
}

variable "billing_account" {
  description = "予算アラート（google_billing_budget）を作る課金アカウント ID"
  type        = string
}

variable "image" {
  description = <<-EOT
    Cloud Run 3サービス共通のコンテナイメージ参照（Artifact Registry の完全パス）。
    初回 apply 時はまだ SpecLino のイメージが Artifact Registry に無いため、
    Google 公開のサンプルイメージを既定値にしている。
    以降のイメージ更新は Terraform ではなく .github/workflows/deploy.yml の
    `gcloud run services update --image` が行う（run.tf の lifecycle.ignore_changes 参照）。
  EOT
  type        = string
  default     = "us-docker.pkg.dev/cloudrun/container/hello"
}

variable "github_repository" {
  description = "WIF の attribute_condition に使う \"owner/repo\""
  type        = string
  default     = "tanabe-yudai/spec-lino"
}

variable "budget_notification_channels" {
  description = "予算アラートの通知先（google_monitoring_notification_channel の完全リソース名のリスト）。任意。"
  type        = list(string)
  default     = []
}

variable "monitoring_notification_channels" {
  description = "Cloud Run 5xx アラートの通知先（google_monitoring_notification_channel の完全リソース名のリスト）。任意。"
  type        = list(string)
  default     = []
}
