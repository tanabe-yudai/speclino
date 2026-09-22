# worker への review-run 実行キュー。
#
# 注意: dispatch deadline（Cloud Tasks が呼出し先からの応答を待つ上限。既定300秒、
# 最大1800秒）はキューではなく "タスク作成時" の CreateTask リクエストで指定する。
# google_cloud_tasks_queue にはその設定項目が無いため、ここには存在しない。
# ReviewRunService（S20）が worker を呼び出す HTTP タスクを作る際、
# 「パイプライン全体15分 < Cloud Run timeout 20分 < dispatch deadline 25分」
# （design/2026-09-22-hackathon-plan.md の「時間と冪等性」）を満たす値
# （目安 1500s 程度）を dispatch_deadline として指定すること。
resource "google_cloud_tasks_queue" "runs" {
  project  = var.project_id
  location = var.region
  name     = "speclino-runs"

  rate_limits {
    max_dispatches_per_second = 5
    max_concurrent_dispatches = 2
  }

  retry_config {
    max_attempts = 3
  }
}
