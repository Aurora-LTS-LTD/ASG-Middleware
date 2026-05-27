# ============================================================
# Aurora LTS — Terraform outputs
# ============================================================

output "cloud_run_url" {
  description = "Public HTTPS URL of the deployed Cloud Run service."
  value       = google_cloud_run_v2_service.api.uri
}

output "cloud_sql_connection_name" {
  description = "Connection name for the Cloud SQL Auth Proxy (PROJECT:REGION:INSTANCE)."
  value       = google_sql_database_instance.postgres.connection_name
}

output "vault_bucket_url" {
  description = "gs:// URL of the Document Vault bucket."
  value       = "gs://${google_storage_bucket.vault.name}"
}

output "kyc_bucket_url" {
  description = "gs:// URL of the KYC bucket."
  value       = "gs://${google_storage_bucket.kyc.name}"
}
