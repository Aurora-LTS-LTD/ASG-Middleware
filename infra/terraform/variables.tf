# ============================================================
# Aurora LTS — Terraform variables
# ============================================================

variable "project_id" {
  description = "GCP project ID."
  type        = string
  default     = "aurora-lts-prod"
}

variable "region" {
  description = "GCP region. Israeli region for data-residency."
  type        = string
  default     = "me-west1"
}

variable "cloud_run_service_name" {
  description = "Cloud Run service hosting the FastAPI backend."
  type        = string
  default     = "aurora-api"
}

variable "cloud_sql_instance_name" {
  description = "Cloud SQL Postgres instance ID (the part after PROJECT:REGION:)."
  type        = string
  default     = "aurora-postgres-prod"
}

variable "cloud_run_min_instances" {
  description = "Cold-start floor. 0 = scale-to-zero (cheap; cold starts hurt UX)."
  type        = number
  default     = 0
}

variable "cloud_run_max_instances" {
  description = "Hard ceiling on horizontal scaling. Coordinated with AURORA_CLOUD_RUN_MAX_INSTANCES (P1-03)."
  type        = number
  default     = 10
}

variable "vault_bucket_name" {
  description = "GCS bucket for the Document Vault."
  type        = string
  default     = "aurora-vault-prod"
}

variable "kyc_bucket_name" {
  description = "GCS bucket for KYC documents (separate from vault for stricter IAM)."
  type        = string
  default     = "asg-kyc-prod"
}
