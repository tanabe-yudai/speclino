# 予算 $300 に対し 17% / 33% / 67%（≒ $50 / $100 / $200）で通知する。
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
      currency_code = "USD"
      units         = "300"
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

  all_updates_rule {
    monitoring_notification_channels = var.budget_notification_channels
    # 通知チャネルを明示指定した場合のみ、既定の課金管理者宛メール通知を止める。
    disable_default_iam_recipients = length(var.budget_notification_channels) > 0
  }
}

data "google_project" "current" {
  project_id = var.project_id
}
