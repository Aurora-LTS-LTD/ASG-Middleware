# ============================================================
# Aurora LTS — Terraform version + provider pinning
# ============================================================
# Pinned to the major-version lines stable as of 2026-05.
# Bump deliberately; never auto-upgrade across major versions.

terraform {
  required_version = ">= 1.6, < 2.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.40"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 5.40"
    }
  }

  # ── State backend ──
  # State lives in a GCS bucket so multiple operators (and CI) share
  # the same lock + version chain. Bucket name baked here so the
  # `terraform init` reproduces the same state on any machine.
  # CREATE THE BUCKET BEFORE FIRST `terraform init`:
  #   gcloud storage buckets create gs://aurora-tf-state-prod \
  #     --location=me-west1 --uniform-bucket-level-access \
  #     --public-access-prevention --versioning
  backend "gcs" {
    bucket = "aurora-tf-state-prod"
    prefix = "terraform/state"
  }
}
