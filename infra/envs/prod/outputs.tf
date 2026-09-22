output "web_url" {
  description = "speclino-web の公開 URL"
  value       = google_cloud_run_v2_service.services["web"].uri
}

output "worker_url" {
  description = "speclino-worker の URL（IAM 必須、tasks SA のみ呼出し可）"
  value       = google_cloud_run_v2_service.services["worker"].uri
}

output "sandbox_url" {
  description = "speclino-sandbox の URL（IAM 必須、worker SA のみ呼出し可）"
  value       = var.enable_sandbox ? google_cloud_run_v2_service.services["sandbox"].uri : null
}

output "artifact_registry" {
  description = "deploy.yml が push する Artifact Registry リポジトリの完全パス"
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.speclino.repository_id}"
}

output "wif_provider" {
  description = "GitHub Actions の google-github-actions/auth@v2 に渡す workload_identity_provider"
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "deployer_sa" {
  description = "GitHub Actions の service_account に渡す deployer SA のメールアドレス"
  value       = google_service_account.this["deployer"].email
}
