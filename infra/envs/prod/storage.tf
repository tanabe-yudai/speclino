resource "google_storage_bucket" "artifacts" {
  project  = var.project_id
  name     = "${var.project_id}-artifacts"
  location = var.region

  uniform_bucket_level_access = true
  force_destroy               = false

  lifecycle_rule {
    condition {
      age = 90
    }
    action {
      type = "Delete"
    }
  }
}

# web は画面と API（review.md・プロジェクトの overview・規約の成否）で成果物を読むだけ。
# 書くのは worker だけなので、web には読み取りだけを付ける。
resource "google_storage_bucket_iam_member" "web_object_viewer" {
  bucket = google_storage_bucket.artifacts.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.this["web"].email}"
}

resource "google_storage_bucket_iam_member" "worker_object_admin" {
  bucket = google_storage_bucket.artifacts.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.this["worker"].email}"
}
