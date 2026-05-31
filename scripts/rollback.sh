#!/usr/bin/env bash
# =====================================================================
# Aurora LTS — Blue-Green Rollback Script  (P2-19)
# =====================================================================
# Instantly shifts all traffic back to the revision tagged :stable,
# bypassing the CI/CD pipeline.  Use this when:
#   • A post-deploy anomaly is detected (elevated error rate, latency)
#   • The smoke test passed but a regression slips through
#   • You need to roll back while a hotfix is being prepared
#
# USAGE:
#   ./scripts/rollback.sh [--dry-run] [--region me-west1] [--service aurora-api]
#
# PREREQUISITES:
#   gcloud auth login
#   gcloud config set project aurora-lts-prod
# =====================================================================

set -euo pipefail

REGION="${REGION:-me-west1}"
PROJECT="${PROJECT:-aurora-lts-prod}"
SERVICE="${SERVICE:-aurora-api}"
STABLE_TAG="stable"
DRY_RUN=false

# Parse flags
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)    DRY_RUN=true; shift ;;
    --region)     REGION="$2"; shift 2 ;;
    --service)    SERVICE="$2"; shift 2 ;;
    --project)    PROJECT="$2"; shift 2 ;;
    *)            echo "Unknown flag: $1"; exit 1 ;;
  esac
done

echo "========================================"
echo "Aurora LTS — Emergency Rollback"
echo "========================================"
echo "Service:  $SERVICE"
echo "Region:   $REGION"
echo "Project:  $PROJECT"
echo "Dry-run:  $DRY_RUN"
echo ""

# Resolve the revision URL tagged :stable
STABLE_REVISION=$(gcloud run services describe "$SERVICE" \
  --region="$REGION" --project="$PROJECT" \
  --format='value(status.traffic[?tag="stable"].revisionName)' 2>/dev/null || true)

if [ -z "$STABLE_REVISION" ]; then
  echo "ERROR: No revision tagged '${STABLE_TAG}' found on service '${SERVICE}'."
  echo "       Run a successful deploy first so the :stable tag is set."
  exit 1
fi

echo "Rolling back to: $STABLE_REVISION"
echo ""

# Describe current traffic split
echo "Current traffic:"
gcloud run services describe "$SERVICE" \
  --region="$REGION" --project="$PROJECT" \
  --format='table(status.traffic[].revisionName,status.traffic[].percent,status.traffic[].tag)'
echo ""

if [ "$DRY_RUN" = "true" ]; then
  echo "[DRY RUN] Would run:"
  echo "  gcloud run services update-traffic $SERVICE \\"
  echo "    --to-revisions=${STABLE_REVISION}=100 \\"
  echo "    --region=$REGION --project=$PROJECT"
  exit 0
fi

read -rp "⚠️  Confirm rollback to ${STABLE_REVISION}? (yes/no): " CONFIRM
if [ "$CONFIRM" != "yes" ]; then
  echo "Rollback cancelled."
  exit 0
fi

gcloud run services update-traffic "$SERVICE" \
  --to-revisions="${STABLE_REVISION}=100" \
  --region="$REGION" \
  --project="$PROJECT"

echo ""
echo "✅  Rollback complete. 100% of traffic now on: $STABLE_REVISION"
echo ""
echo "Verify:"
echo "  curl https://<your-domain>/api/v1/onboarding/health"
echo ""
echo "To re-deploy after fixing the issue, run Cloud Build with the"
echo "fixed version tag — it will follow the full blue-green pipeline."
