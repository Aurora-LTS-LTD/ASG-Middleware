#!/usr/bin/env bash
#
# Aurora LTS — provider-flip (OTP + KYC + PayPlus → real backends).
# =========================================================================
# Flips the onboarding PROVIDERS on an ALREADY-DEPLOYED aurora-api revision by
# updating env vars + Secret Manager references ONLY. It does NOT build or ship
# code — it reuses whatever container image is already running on the service.
#
# ─────────────────────────────────────────────────────────────────────────
# ⚠️  CODE / IMAGE DEPLOYS GO THROUGH cloudbuild.yaml — *NOT* THIS SCRIPT.
# ─────────────────────────────────────────────────────────────────────────
# aurora-api MUST be built from services/aurora-main-api/Dockerfile with the
# repo root as build context (see the Dockerfile header + cloudbuild.yaml).
#
# DO NOT run `gcloud run deploy <svc> --source .` for this service. The repo
# root has NO Dockerfile and NO Procfile (only an empty main.py), so --source
# falls back to Buildpacks and builds a container WITHOUT the FastAPI app —
# every route 404s (incl. /api/v1/onboarding/health). That exact mistake is
# what stranded the last flip. To ship code:
#
#     gcloud builds submit --config cloudbuild.yaml \
#       --substitutions=_VERSION=vX-Y-Z --project=aurora-lts-prod
#
# (cloudbuild builds the Dockerfile, runs migrations, canaries at 0%, smoke-
#  tests onboarding/health, and only then shifts traffic — it is self-gating.)
#
# WHY --update-* (not --set-*): --set-env-vars / --set-secrets REPLACE the whole
# set, which would WIPE ITA_BACKEND / STORAGE_BACKEND / AUDIT_BIGQUERY_BACKEND.
# Those are on the startup HARD-FAIL list — wiping them to stub would crash-loop
# the revision. --update-* MERGES, preserving everything already set.
#
# SAFETY GATES THIS RELIES ON (app/config/backend_check.py):
#   • A selector flipped to a real backend WITHOUT its matching secret → the
#     revision HARD-FAILS at boot (never serves). So this command sets every
#     matching secret. Drop one and the new revision will not go healthy.
#   • On a cloud_run service ITA_BACKEND / STORAGE_BACKEND / AUDIT_BIGQUERY_BACKEND
#     must already be real (not stub) or boot hard-fails regardless of this flip.
#
# KYC IS KEYLESS (Workload Identity): there is NO GCS_KYC_SA_KEY_JSON secret.
# Signed URLs are produced via the IAM signBlob API using the runtime SA's
# ambient credentials. PREREQUISITE IAM on the runtime SA (aurora-run@), already
# applied in prod:
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

# ── PRE-FLIGHT: refuse to flip onto a wrong / Buildpack image ────────────────
# This script only sets env + secrets; it relies on the CORRECT app image
# already running. If a `--source` (Buildpack) image is live, or the live image
# isn't serving onboarding/health, flipping would just decorate a broken
# revision (the failure mode that stranded the last attempt). Stop loudly and
# point the operator at cloudbuild.yaml.
echo "→ Pre-flight: verifying the live ${SERVICE} image actually runs the app…"
LIVE_IMAGE="$(gcloud run services describe "${SERVICE}" --project="${PROJECT}" --region="${REGION}" \
  --format='value(spec.template.spec.containers[0].image)' 2>/dev/null || true)"
echo "    live image: ${LIVE_IMAGE:-<none>}"
if [[ -z "$LIVE_IMAGE" || "$LIVE_IMAGE" == *"cloud-run-source-deploy"* ]]; then
  echo "✗ The live image is empty or a 'gcloud run deploy --source .' (Buildpack) build."
  echo "  aurora-api must be the Dockerfile image. Ship it FIRST, then re-run this script:"
  echo "    gcloud builds submit --config cloudbuild.yaml --substitutions=_VERSION=vX-Y-Z --project=${PROJECT}"
  exit 1
fi

SERVICE_URL="$(gcloud run services describe "${SERVICE}" --project="${PROJECT}" --region="${REGION}" \
  --format='value(status.url)' 2>/dev/null || true)"
if [[ -z "$SERVICE_URL" ]] || ! curl -fsS --max-time 15 "${SERVICE_URL}/api/v1/onboarding/health" >/dev/null 2>&1; then
  echo "✗ ${SERVICE_URL:-<no url>}/api/v1/onboarding/health did not return 200 — the live image is"
  echo "  not serving the onboarding app. Deploy the correct image via cloudbuild.yaml first."
  exit 1
fi
echo "✓ Live image is a Dockerfile build and onboarding/health is 200 — safe to flip providers."

echo "→ Flipping providers on ${SERVICE} (${PROJECT}/${REGION})"
echo "  OTP_BACKEND=production  KYC_BACKEND=gcs  PAYPLUS_BACKEND=production  SMS_PROVIDER=${SMS_PROVIDER}"
echo "  bucket=${GCS_BUCKET_KYC}  payplus_base=${PAYPLUS_API_BASE}"

# Assemble env vars with a '|' delimiter — it cannot collide with the values
# (emails contain '@', URLs contain ':' and '/'; none contain '|'). SENDGRID_FROM
# is appended only if the operator set it, so prod falls back to the verified
# app default otp@api-aurora-lts.com rather than a fake sandbox domain.
ENV_VARS="OTP_BACKEND=production|KYC_BACKEND=gcs|PAYPLUS_BACKEND=production|SMS_PROVIDER=${SMS_PROVIDER}|SECRET_BACKEND=env|GCS_BUCKET_KYC=${GCS_BUCKET_KYC}|PAYPLUS_API_BASE=${PAYPLUS_API_BASE}"
[[ -n "$SENDGRID_FROM" ]] && ENV_VARS="${ENV_VARS}|SENDGRID_FROM=${SENDGRID_FROM}"

# `services update` (NOT `deploy --source`): reuses the live image and just
# MERGES env + secrets onto a new NO-TRAFFIC revision tagged 'flip'. Zero build,
# zero blast radius — existing traffic stays on the current revision.
gcloud run services update "${SERVICE}" \
  --project="${PROJECT}" \
  --region="${REGION}" \
  --update-env-vars="^|^${ENV_VARS}" \
  --update-secrets="SENDGRID_API_KEY=sendgrid-api-key:latest,PAYPLUS_API_KEY=payplus-api-key:latest,PAYPLUS_TERMINAL_NUMBER=payplus-terminal-number:latest" \
  --no-traffic \
  --tag=flip

# NOTE: no GCS_KYC_SA_KEY_JSON — KYC signing is keyless (Workload Identity + signBlob).
# If the service ever had that secret bound, drop it: --remove-secrets=GCS_KYC_SA_KEY_JSON
# NOTE: ONBOARDING_REQUIRE_PHONE_OTP is an onboarding flag, NOT a provider — it is
# intentionally not touched here. For E2E without SMS, set it separately:
#   gcloud run services update ${SERVICE} --region=${REGION} \
#     --update-env-vars=ONBOARDING_REQUIRE_PHONE_OTP=false

echo ""
echo "✓ Provider env + secrets applied to a NO-TRAFFIC revision tagged 'flip' (zero blast radius)."
echo "  1. Smoke-test the tagged URL (it serves under https://flip---${SERVICE}-...run.app):"
echo "       curl -fsS https://flip---<...>.run.app/api/v1/onboarding/health | jq"
echo "       → expect otp_backend=production, kyc_backend=gcs, payplus_backend=production"
echo "     If the revision is crash-looping, a required secret is missing (the paired-cred gate)."
echo "       gcloud run revisions list --service=${SERVICE} --region=${REGION}"
echo "       gcloud logging read 'resource.labels.service_name=${SERVICE} severity>=ERROR' --limit=20"
echo "  2. When healthy, migrate traffic:"
echo "       gcloud run services update-traffic ${SERVICE} --region=${REGION} --to-latest"
echo "  ROLLBACK: gcloud run services update-traffic ${SERVICE} --region=${REGION} --to-revisions=<prev>=100"
