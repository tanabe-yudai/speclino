# 予算（既定 45,000 円 ≒ $300）に対し 17% / 33% / 67% / 100% で通知する。
# 通貨は課金アカウントの通貨と一致させる必要がある（variables.tf の budget_currency_code）。
#
# 注意: billingbudgets API の書き込みには、このプロジェクトの IAM 権限だけでなく
# 課金アカウント側のロール（roles/billing.costsManager や billing account 単位の
# roles/billingbudgets.editor 相当）が実行者に必要な場合がある。apply 時に
# permission denied になった場合は課金アカウントの IAM を確認すること。
resource "google_billing_budget" "prod" {
  billing_account = var.billing_account
  display_name    = "SpecLino prod"

  budget_filter {
    projects = ["projects/${data.google_project.current.number}"]
  }

  amount {
    specified_amount {
      currency_code = var.budget_currency_code
      units         = var.budget_amount
    }
  }

  # 実際の支出（CURRENT_SPEND）ベースでの閾値。予測支出（FORECASTED_SPEND）は使わない。
  threshold_rules {
    threshold_percent = 0.17
    spend_basis       = "CURRENT_SPEND"
  }
  threshold_rules {
    threshold_percent = 0.33
    spend_basis       = "CURRENT_SPEND"
  }
  threshold_rules {
    threshold_percent = 0.67
    spend_basis       = "CURRENT_SPEND"
  }

  # 通知チャネルを指定したときだけ all_updates_rule を置く。
  # 中身が空（チャネルなし・pubsub なし・disable_default_iam_recipients = false）の
  # all_updates_rule は API が 400 invalid argument で拒否する。
  # ブロックごと省略すると既定の通知（課金アカウント管理者宛メール）が使われる。
  dynamic "all_updates_rule" {
    for_each = length(var.budget_notification_channels) > 0 ? [1] : []
    content {
      monitoring_notification_channels = var.budget_notification_channels
      # チャネルを明示したので、既定の課金管理者宛メールは止める。
      disable_default_iam_recipients = true
    }
  }
}

data "google_project" "current" {
  project_id = var.project_id
}
