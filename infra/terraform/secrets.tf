# ============================================================
# Aurora LTS — Secret Manager resources
# ============================================================
# One resource per logical secret. Terraform manages the secret
# CONTAINER (its name, replication policy, IAM); the VALUES inside
# are managed out-of-band (via `gcloud secrets versions add`).
#
# This separation is deliberate — secret values should never be in
# Terraform state, which itself lives in a GCS bucket.

locals {
  # All secrets share the same per-region (me-west1) replication and
  # automatic-rotation policy for now. Refine per-secret as needed.
  required_secrets = [
    "jwt-secret",
    "webauthn-step-up-secret",
    "aurora-ip-hash-salt",
    "sendgrid-api-key",
    "whatsapp-app-secret",
    "whatsapp-verify-token",
    "whatsapp-access-token",
    "payplus-api-key",
    "payplus-terminal-number",
    "inforu-api-key",
    "twilio-account-sid",
    "twilio-auth-token",
    "aurora-pii-encryption-key", # P1-23
  ]
}

resource "google_secret_manager_secret" "all" {
  for_each = toset(local.required_secrets)

  secret_id = each.key

  # Israeli data-residency: replicate ONLY in me-west1.
  replication {
    user_managed {
      replicas { location = var.region }
    }
  }

  labels = {
    managed_by = "terraform"
    component  = "aurora-backend"
  }
}

# IAM: the Cloud Run service account needs roles/secretmanager.secretAccessor
# on every secret to bind them as env vars. Adjust principal to your actual
# service account.
resource "google_secret_manager_secret_iam_member" "cloud_run_access" {
  for_each = toset(local.required_secrets)

  secret_id = google_secret_manager_secret.all[each.key].id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:aurora-api-runner@${var.project_id}.iam.gserviceaccount.com"
}
