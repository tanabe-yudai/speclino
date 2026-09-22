resource "google_artifact_registry_repository" "speclino" {
  project       = var.project_id
  location      = var.region
  repository_id = "speclino"
  format        = "DOCKER"
  description   = "SpecLino の web / worker / sandbox コンテナイメージ"
}
