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
  description = "WIF の attribute_condition に使う \"owner/repo\"（このリポジトリを置いた GitHub のリポジトリ）"
  type        = string
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

variable "sandbox_url" {
  description = <<-EOT
    sandbox サービスの URL。worker が隔離実行を依頼する宛先になる（S38）。
    worker_url と同じ理由（同一リソースの別インスタンスを参照すると循環になる）で
    変数で受け取り、`terraform output sandbox_url` の値を tfvars に書いて 2 回目の apply で反映する。
    空のあいだ worker は隔離実行を無効（DisabledSandbox）のままにする。
  EOT
  type        = string
  default     = ""
}

variable "mount_secrets" {
  description = <<-EOT
    Secret Manager の値を Cloud Run の環境変数として参照するか（`secrets.tf` の secret_env）。
    参照する秘密にバージョンが無いとリビジョンが起動できないため、全ての値を投入してから
    true にする。DB のパスワードを含む接続 URL は `database-url` に入れる（tfvars に書くと
    Terraform の state に載ってしまう）。
  EOT
  type        = bool
  default     = false
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
    の `currencyCode`。例: 円建ての課金アカウントなら JPY。
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

variable "github_client_id" {
  description = <<-EOT
    本番 GitHub App の Client ID（秘密ではない。S55 のリポジトリ登録で使う）。client secret を
    Secret Manager の github-app-client-secret に投入してから tfvars に書く。空のあいだ web は
    client secret を注入せず、リポジトリ登録は 503（未設定）になる。
  EOT
  type        = string
  default     = ""
}

variable "user_project_override" {
  description = <<-EOT
    provider の user_project_override（versions.tf）。手元のユーザー ADC では true が要る。
    SA の資格情報で流す terraform-plan.yml は false にし、planner SA に serviceusage.services.use を持たせない。
  EOT
  type        = bool
  default     = true
}

variable "mount_bundle_signing_key" {
  description = <<-EOT
    知識の bundle・lock の署名鍵（Secret Manager の bundle-signing-key）を web・worker・admin Job に
    SPECLINO_BUNDLE_SIGNING_KEY として注入するか。値を投入してから true にする（バージョンの無い
    secret を参照するとリビジョンが起動できない）。env=prod で鍵が無いと新しいコードは起動しないので、
    知識レジストリを含むイメージをデプロイする前に true にする（docs/runbooks/knowledge-registry-cutover.md）。
  EOT
  type        = bool
  default     = false
}
