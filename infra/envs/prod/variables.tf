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

variable "enable_sandbox" {
  description = <<-EOT
    speclino-sandbox サービス（Cloud Run sandboxes、Preview）を作るか。
    Preview 機能はプロジェクトが許可されていないと apply が失敗し、for_each の他 2 サービスも
    巻き込むため、既定は false。S38（sandbox PoC）で true にする。
    false のとき test_run は無効（`SPECLINO_SANDBOX_KIND=disabled` 相当）で、動的証拠が
    取れないぶん未検証はゲート失敗のまま残る。
  EOT
  type        = bool
  default     = false
}

variable "public_url" {
  description = <<-EOT
    web サービスの公開 URL（`https://speclino-web-....run.app`）。
    Cloud Run の URL は作成後に決まるため、初回 apply では空のままにし、
    `terraform output web_url` で得た値を tfvars に書いて 2 回目の apply で反映する。
    空のときは Check の詳細リンクとフロントの絶対 URL が使えない。
  EOT
  type        = string
  default     = ""
}

variable "worker_url" {
  description = <<-EOT
    worker サービスの URL。web が Cloud Tasks のタスクを作るときの宛先になる。
    public_url と同じく作成後に決まるため、初回 apply では空にし、
    `terraform output worker_url` の値を tfvars に書いて 2 回目の apply で反映する。
    空のあいだ web は Cloud Tasks を使わない設定のままになる（初回はサンプルイメージなので影響なし）。
  EOT
  type        = string
  default     = ""
}

variable "database_url" {
  description = <<-EOT
    worker が使う Cloud SQL への接続 URL
    （例 `postgresql+asyncpg://speclino:<pw>@/speclino?host=/cloudsql/<接続名>`）。
    S26 で Cloud SQL を作ってから設定する。空のときは worker は既定のローカル DB を見る。
  EOT
  type        = string
  default     = ""
  sensitive   = true
}

variable "cloudsql_instance" {
  description = "worker に接続する Cloud SQL インスタンスの接続名（`<project>:<region>:<instance>`）。空なら接続しない。"
  type        = string
  default     = ""
}

variable "budget_currency_code" {
  description = <<-EOT
    予算の通貨。**課金アカウントの通貨と一致していないと API が 400 を返す**。
    確認方法:
      curl -H "Authorization: Bearer $(gcloud auth print-access-token)" \
        https://cloudbilling.googleapis.com/v1/billingAccounts/<課金アカウントID>
    の `currencyCode`。T-NET（XXXXXX-XXXXXX-XXXXXX）は JPY。
  EOT
  type        = string
  default     = "JPY"
}

variable "budget_amount" {
  description = <<-EOT
    予算額（budget_currency_code の単位、小数なし）。
    既定 45000 円 ≒ クーポン $300 相当（1 ドル 150 円換算）。
  EOT
  type        = string
  default     = "45000"
}

variable "github_app_id" {
  description = <<-EOT
    本番 GitHub App の App ID（秘密ではない。秘密鍵は Secret Manager 経由）。
    App を作ってから tfvars に書く。空のあいだ worker は GitHub を呼べない。
  EOT
  type        = string
  default     = ""
}
