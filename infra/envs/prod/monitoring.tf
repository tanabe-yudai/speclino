# 最小構成: Cloud Run の 5xx 応答が一定数を超えたらアラート。
# 簡略化のため「5xx リクエスト数の絶対値」を見ており、全リクエストに対する比率
# （真の error ratio）ではない。MQL の比率条件への差し替えは必要になった時点で行う。
resource "google_monitoring_alert_policy" "run_5xx" {
  project      = var.project_id
  display_name = "SpecLino Cloud Run 5xx"
  combiner     = "OR"

  conditions {
    display_name = "5xx response count (5m)"

    condition_threshold {
      filter          = "resource.type = \"cloud_run_revision\" AND metric.type = \"run.googleapis.com/request_count\" AND metric.label.\"response_code_class\" = \"5xx\""
      comparison      = "COMPARISON_GT"
      threshold_value = 5
      duration        = "300s"

      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_RATE"
      }
    }
  }

  notification_channels = var.monitoring_notification_channels

  alert_strategy {
    auto_close = "1800s"
  }
}
