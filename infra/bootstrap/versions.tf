terraform {
  required_version = ">= 1.12"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 8.0"
    }
  }

  # bootstrap はローカル state（tfstate バケット自体をここで作るため）。
  # infra/envs/prod はこのバケットを GCS backend として使う。
}

provider "google" {
  region = "asia-northeast1"
}
