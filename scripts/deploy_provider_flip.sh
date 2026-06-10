#!/usr/bin/env bash
#
# Aurora LTS — provider-flip deploy (OTP + KYC + PayPlus → real backends).
# =========================================================================
# Flips the onboarding providers on a Cloud Run service using Secret Manager
# references. Run by an operator with `gcloud run deploy` rights — NOT by the app.
#
# WHY --update-* (not --set-*): --set-env-vars / --set-secrets REPLACE the whole
# set, which would WIPE ITA_BACKEND / STORAGE_BACKEND / AUDIT_BIGQUERY_BACKEND.
# Those are on the startup HARD-FAIL list — wiping them to stub would crash-loop
# the revision. --update-* MERGES, preserving everything already set.
#
# SAFETY GATES THIS RELIES ON (app/config/backend_check.py):
#   • A selector flipped to a real backend WITHOUT its matching secret → the
#     revision HARD-FAILS at boot (never serves traffic). So this command sets
#     every matching secret. If you drop one, the deploy will not go healthy
#     (and your dashboard stays Offline — see below).
#   • On a cloud_run service ITA_BACKEND / STORAGE_BACKEND / AUDIT_BIGQUERY_BACKEND
#     must already be real (not stub) or the boot hard-fails regardless of this flip.
#
# KYC IS KEYLESS (Workload Identity): there is NO GCS_KYC_SA_KEY_JSON secret.
# Signed URLs are produced via the IAM signBlob API using the runtime SA's
# ambient credentials. PREREQUISITE IAM on the runtime SA (aurora-run@):
#   gcloud iam service-accounts add-iam-policy-binding aurora-run@${PROJECT:-aurora-lts-prod}.iam.gserviceaccount.com \
#     --member="serviceAccount:aurora-run@${PROJECT:-aurora-lts-prod}.iam.gserviceaccount.com" \
#     --role="roles/iam.serviceAccountTokenCreator"
#   gcloud storage buckets add-iam-policy-binding gs://<bucket> \
#     --member="serviceAccount:aurora-run@..." --role="roles/storage.objectCreator"
#   gcloud storage buckets add-iam-policy-binding gs://<bucket> \
#     --member="serviceAccount:aurora-run@..." --role="roles/storage.objectViewer"
# Verify it all works (keyless) with: python3 scripts/verify_kyc_key.py <bucket>
# =========================================================================
set -euo pipefail

# ── EDIT THESE ──────────────────────────────────────────────────────────────
PROJECT="${PROJECT:-aurora-lts-prod}"
REGION="${REGION:-me-west1}"
SERVICE="${SERVICE:?set SERVICE — e.g. aurora-api-nonprod. NOTE: 'aurora-api' is PRODUCTION}"
GCS_BUCKET_KYC="${GCS_BUCKET_KYC:?set the KYC bucket — e.g. asg-kyc-sandbox (NOT the prod 7yr bucket)}"
PAYPLUS_API_BASE="${PAYPLUS_API_BASE:?set the PayPlus SANDBOX REST base URL (obtain from PayPlus; NOT restapi.payplus.co.il)}"
SENDGRID_FROM="${SENDGRID_FROM:-}"   # optional — leave UNSET to use the app default otp@api-aurora-lts.com (the verified prod sender); a fake domain would make every real email OTP fail
SMS_PROVIDER="${SMS_PROVIDER:-stub}"   # 'stub' = phone OTP log-only; email OTP is real. Set inforu/twilio to test SMS.
# ─────────────────────────────────────────────────────────────────────────────

if [[ "$SERVICE" == "aurora-api" ]]; then
  echo "⚠️  SERVICE=aurora-api is PRODUCTION. Real customer OTPs, KYC PII to GCS, and PayPlus tokenization."
  read -r -p "    Type the service name again to confirm a PROD flip: " confirm
  [[ "$confirm" == "aurora-api" ]] || { echo "Aborted."; exit 1; }
fi

echo "→ Flipping providers on ${SERVICE} (${PROJECT}/${REGION})"
echo "  OTP_BACKEND=production  KYC_BACKEND=gcs  PAYPLUS_BACKEND=production  SMS_PROVIDER=${SMS_PROVIDER}"
echo "  bucket=${GCS_BUCKET_KYC}  payplus_base=${PAYPLUS_API_BASE}"

# Assemble env vars with a '|' delimiter — it cannot collide with the values
# (emails contain '@', URLs contain ':' and '/'; none contain '|'). SENDGRID_FROM
# is appended only if the operator set it, so prod falls back to the verified
# app default otp@api-aurora-lts.com rather than a fake sandbox domain.
ENV_VARS="OTP_BACKEND=production|KYC_BACKEND=gcs|PAYPLUS_BACKEND=production|SMS_PROVIDER=${SMS_PROVIDER}|SECRET_BACKEND=env|GCS_BUCKET_KYC=${GCS_BUCKET_KYC}|PAYPLUS_API_BASE=${PAYPLUS_API_BASE}"
[[ -n "$SENDGRID_FROM" ]] && ENV_VARS="${ENV_VARS}|SENDGRID_FROM=${SENDGRID_FROM}"

gcloud run deploy "${SERVICE}" \
  --project="${PROJECT}" \
  --region="${REGION}" \
  --update-env-vars="^|^${ENV_VARS}" \
  --update-secrets="SENDGRID_API_KEY=sendgrid-api-key:latest,PAYPLUS_API_KEY=payplus-api-key:latest,PAYPLUS_TERMINAL_NUMBER=payplus-terminal-number:latest" \
  --no-traffic \
  --tag=flip

# NOTE: no GCS_KYC_SA_KEY_JSON — KYC signing is keyless (Workload Identity + signBlob).
# If the service ever had that secret bound, drop it: --remove-secrets=GCS_KYC_SA_KEY_JSON

echo ""
echo "✓ Deployed as a NO-TRAFFIC revision tagged 'flip' (zero blast radius)."
echo "  1. Smoke-test the tagged URL (it serves under https://flip---${SERVICE}-...run.app):"
echo "       curl -fsS https://flip---<...>.run.app/api/v1/onboarding/health | jq"
echo "       → expect otp_backend=production, kyc_backend=gcs, payplus_backend=production"
echo "     If the revision is crash-looping, a required secret is missing (the paired-cred gate)."
echo "       gcloud run revisions list --service=${SERVICE} --region=${REGION}"
echo "       gcloud logging read 'resource.labels.service_name=${SERVICE} severity>=ERROR' --limit=20"
echo "  2. When healthy, migrate traffic:"
echo "       gcloud run services update-traffic ${SERVICE} --region=${REGION} --to-latest"
echo "  ROLLBACK: gcloud run services update-traffic ${SERVICE} --region=${REGION} --to-revisions=<prev>=100"
