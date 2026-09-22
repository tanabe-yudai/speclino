variable "project_id" {
  description = "作成する GCP プロジェクト ID。apply 前に実際に利用可能か（未使用か）を確認すること。"
  type        = string
  default     = "example-project"
}

variable "billing_account" {
  description = "プロジェクトに紐付ける課金アカウント ID（例: XXXXXX-XXXXXX-XXXXXX）。"
  type        = string
}

variable "org_id" {
  description = "組織 ID。個人アカウント配下など組織を使わない場合は null のままにする。folder_id と併用不可。"
  type        = string
  default     = null
}

variable "folder_id" {
  description = "フォルダ ID。フォルダ配下に作らない場合は null のままにする。org_id と併用不可。"
  type        = string
  default     = null
}
