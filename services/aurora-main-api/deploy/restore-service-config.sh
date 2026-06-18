#!/usr/bin/env bash
#
# restore-service-config.sh — config-as-code for the aurora-api Cloud Run service.
#
# The live service config (env, secrets, scaling, VPC, SA, resources) previously
# existed ONLY in the running Cloud Run service. This script reproduces it from
# the repo, so the service survives an accidental wipe or a from-scratch recreate.
#
# Normal deploys go through cloudbuild.yaml, whose deploy-green step sets ONLY
# --image and therefore PRESERVES everything below. Run THIS script only to
# recreate the service or repair drifted config — NOT for routine deploys.
#
# Usage:
#   ./restore-service-config.sh <IMAGE>
#   e.g. ./restore-service-config.sh me-west1-docker.pkg.dev/aurora-lts-prod/aurora/api:v1-15-7
#
# ⚠️ REVIEW before running against production. Captured 2026-06-18 (v1-15-7).
# Secrets are referenced by name from Secret Manager — never hard-coded here.

set -euo pipefail

IMAGE="${1:?usage: restore-service-config.sh <IMAGE e.g. me-west1-docker.pkg.dev/aurora-lts-prod/aurora/api:v1-15-7>}"
DIR="$(cd "$(dirname "$0")" && pwd)"
REGION="me-west1"
PROJECT="aurora-lts-prod"

SECRETS="\
DATABASE_URL=AURORA_DATABASE_URL:latest,\
JWT_SECRET=AURORA_JWT_SECRET:latest,\
AURORA_INTERNAL_TOKEN=AURORA_INTERNAL_TOKEN:latest,\
AURORA_IP_HASH_SALT=AURORA_IP_HASH_SALT:latest,\
WEBAUTHN_STEP_UP_SECRET=WEBAUTHN_STEP_UP_SECRET:latest,\
ANTHROPIC_API_KEY=ANTHROPIC_API_KEY:latest,\
SENDGRID_API_KEY=sendgrid-api-key:latest,\
PAYPLUS_API_KEY=payplus-api-key:latest,\
PAYPLUS_TERMINAL_NUMBER=payplus-terminal-number:latest,\
WHATSAPP_VERIFY_TOKEN=AURORA_WHATSAPP_VERIFY_TOKEN:latest,\
WHATSAPP_ACCESS_TOKEN=AURORA_WHATSAPP_ACCESS_TOKEN:latest,\
WHATSAPP_PHONE_NUMBER_ID=AURORA_WHATSAPP_PHONE_NUMBER_ID:latest,\
WHATSAPP_APP_SECRET=AURORA_WHATSAPP_APP_SECRET:latest,\
WHATSAPP_WEBHOOK_SECRET=AURORA_WHATSAPP_WEBHOOK_SECRET:latest,\
WHATSAPP_BOT_PHONE_E164=AURORA_WHATSAPP_BOT_PHONE_E164:latest"

gcloud run deploy aurora-api \
  --image "$IMAGE" \
  --region "$REGION" --project "$PROJECT" \
  --service-account "aurora-run@aurora-lts-prod.iam.gserviceaccount.com" \
  --set-cloudsql-instances "aurora-lts-prod:me-west1:aurora-pg" \
  --network default --subnet default --vpc-egress private-ranges-only \
  --min-instances 1 --max-instances 4 \
  --concurrency 80 --timeout 120 \
  --cpu 2 --memory 1Gi \
  --env-vars-file "$DIR/env.prod.yaml" \
  --set-secrets "$SECRETS"

echo "Done. Verify: curl -s -o /dev/null -w '%{http_code}\n' https://api-aurora-lts.com/api/v1/onboarding/health  (expect 200)"
