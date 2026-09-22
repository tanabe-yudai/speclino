output "project_id" {
  description = "作成した GCP プロジェクト ID"
  value       = google_project.this.project_id
}

output "project_number" {
  description = "作成した GCP プロジェクト番号"
  value       = google_project.this.number
}

output "state_bucket" {
  description = "infra/envs/prod の GCS backend に渡すバケット名"
  value       = google_storage_bucket.tfstate.name
}
