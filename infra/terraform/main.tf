# ============================================================
# Aurora LTS — Terraform main: Cloud Run + Cloud SQL + GCS + Secrets
# ============================================================
#
# This is the FOUNDATIONAL skeleton for P1-25. Aurora's production
# infra exists today (was created via gcloud CLI commands); these
# resource definitions describe that infra so it can be IMPORTED
# under Terraform management without recreation.
#
# IMPORT PROCEDURE (one-shot, do this before the first `terraform plan`):
#
#   cd infra/terraform
#   terraform init
#
#   # ── Cloud SQL ──
#   terraform import google_sql_database_instance.postgres \
#     projects/aurora-lts-prod/instances/aurora-postgres-prod
#
#   # ── Cloud Run ──
#   terraform import google_cloud_run_v2_service.api \
#     projects/aurora-lts-prod/locations/me-west1/services/aurora-api
#
#   # ── GCS buckets ──
#   terraform import google_storage_bucket.vault aurora-vault-prod
#   terraform import google_storage_bucket.kyc   asg-kyc-prod
#
#   # ── Secret Manager (per secret) ──
#   for s in jwt-secret webauthn-step-up-secret aurora-ip-hash-salt \
#            sendgrid-api-key whatsapp-app-secret whatsapp-verify-token \
#            payplus-api-key payplus-terminal-number inforu-api-key \
#            twilio-account-sid twilio-auth-token \
#            aurora-pii-encryption-key; do
#     terraform import "google_secret_manager_secret.${s//-/_}" \
#       "projects/aurora-lts-prod/secrets/$s"
#   done
#
# After import, `terraform plan` should show MOSTLY no changes. Where
# it shows diffs, those are settings that drifted in console — adjust
# either the .tf or accept the import-time state and let Terraform
# normalise on the next apply.
# ============================================================

provider "google" {
  project = var.project_id
  region  = var.region
}

provider "google-beta" {
  project = var.project_id
  region  = var.region
}

# ─────────────────────────────────────────────────────────────
# Cloud SQL Postgres — the production database
# ─────────────────────────────────────────────────────────────
resource "google_sql_database_instance" "postgres" {
  name             = var.cloud_sql_instance_name
  database_version = "POSTGRES_16"
  region           = var.region

  settings {
    tier              = "db-custom-2-7680" # 2 vCPU / 7.5 GB — pairs with P1-03 budget (200 conns cap)
    availability_type = "ZONAL"            # bump to REGIONAL when revenue funds HA
    disk_size         = 20
    disk_type         = "PD_SSD"
    disk_autoresize   = true

    backup_configuration {
      enabled                        = true
      start_time                     = "02:00" # 02:00 IDT = 23:00 UTC prior day
      point_in_time_recovery_enabled = true
      transaction_log_retention_days = 7
    }

    ip_configuration {
      ipv4_enabled    = false # private-IP only
      private_network = "projects/${var.project_id}/global/networks/default"
    }

    insights_config {
      query_insights_enabled = true
    }
  }

  deletion_protection = true # do NOT remove this without an outage plan
}

# ─────────────────────────────────────────────────────────────
# Cloud Run — FastAPI backend
# ─────────────────────────────────────────────────────────────
resource "google_cloud_run_v2_service" "api" {
  name     = var.cloud_run_service_name
  location = var.region

  template {
    scaling {
      min_instance_count = var.cloud_run_min_instances
      max_instance_count = var.cloud_run_max_instances
    }

    containers {
      # Image is updated by CI; Terraform manages the surrounding shape.
      image = "me-west1-docker.pkg.dev/${var.project_id}/aurora-api/aurora-api:latest"

      ports { container_port = 8000 }

      resources {
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }
        startup_cpu_boost = true
      }

      env {
        name  = "AURORA_RUNTIME"
        value = "cloud_run"
      }
      env {
        name  = "AURORA_LOG_FORMAT"
        value = "json"
      }

      # Secret env vars are bound via Secret Manager — see secrets.tf.
      # (Refer to the import procedure block above for the secret list.)
    }

    vpc_access {
      egress = "ALL_TRAFFIC"
      network_interfaces {
        network    = "default"
        subnetwork = "default"
      }
    }
  }
}

# ─────────────────────────────────────────────────────────────
# GCS — Document Vault bucket (7-year retention)
# ─────────────────────────────────────────────────────────────
resource "google_storage_bucket" "vault" {
  name                        = var.vault_bucket_name
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  # 7-year retention lock — matches the ClientDocument.archived_until
  # CHECK constraint in migrate_phase21_vault.
  retention_policy {
    retention_period = 60 * 60 * 24 * 365 * 7 # 7 years in seconds
    is_locked        = true                   # lock once Aurora signs off
  }

  versioning { enabled = true }
}

# ─────────────────────────────────────────────────────────────
# GCS — KYC bucket (stricter IAM, separate from vault)
# ─────────────────────────────────────────────────────────────
resource "google_storage_bucket" "kyc" {
  name                        = var.kyc_bucket_name
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  lifecycle_rule {
    condition { age = 365 * 7 } # 7 years
    action { type = "Delete" }
  }
}
