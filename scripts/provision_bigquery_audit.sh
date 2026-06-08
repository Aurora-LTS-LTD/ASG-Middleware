#!/usr/bin/env bash
#
# Aurora LTS — provision the BigQuery audit sink (GATED, ops-run).
# =================================================================
# Creates the `asg_audit` dataset + the action_logs / ita_audit_log tables
# (schemas in services/aurora-main-api/schemas/bigquery/) and grants the
# runtime service account dataset-scoped BigQuery Data Editor.
#
# This is NOT run by the app. Run it once, with bq/gcloud authenticated as a
# user that can create datasets + set dataset IAM. Idempotent: re-running skips
# anything that already exists.
#
# After this + the env vars (AUDIT_BIGQUERY_BACKEND=gcp, GOOGLE_CLOUD_PROJECT,
# BIGQUERY_AUDIT_DATASET=asg_audit) the daily export (POST /api/v1/internal/
# audit-export) lands rows in these tables. Until then, AUDIT_BIGQUERY_BACKEND
# stays `stub` (writes NDJSON to /tmp) — nothing here changes app behavior.
set -euo pipefail

PROJECT="${GOOGLE_CLOUD_PROJECT:-aurora-lts-prod}"
DATASET="${BIGQUERY_AUDIT_DATASET:-asg_audit}"
LOCATION="${BIGQUERY_LOCATION:-me-west1}"
SA="${AURORA_RUN_SA:-aurora-run@aurora-lts-prod.iam.gserviceaccount.com}"
SCHEMA_DIR="$(cd "$(dirname "$0")/../services/aurora-main-api/schemas/bigquery" && pwd)"

echo "Project=${PROJECT} Dataset=${DATASET} Location=${LOCATION}"

# 1. Dataset
if bq --project_id="$PROJECT" show --dataset "${PROJECT}:${DATASET}" >/dev/null 2>&1; then
  echo "✓ dataset ${DATASET} exists"
else
  bq --project_id="$PROJECT" mk --dataset --location="$LOCATION" \
    --description="Aurora LTS append-only compliance audit sink" "${PROJECT}:${DATASET}"
  echo "✓ created dataset ${DATASET}"
fi

# 2. Tables (day-partitioned on the event timestamp for cheap range scans)
declare -A PART=( ["action_logs"]="triggered_at" ["ita_audit_log"]="created_at" )
for t in action_logs ita_audit_log; do
  if bq --project_id="$PROJECT" show "${PROJECT}:${DATASET}.${t}" >/dev/null 2>&1; then
    echo "✓ table ${t} exists"
  else
    bq --project_id="$PROJECT" mk --table \
      --time_partitioning_field="${PART[$t]}" --time_partitioning_type=DAY \
      "${PROJECT}:${DATASET}.${t}" "${SCHEMA_DIR}/${t}.json"
    echo "✓ created table ${t}"
  fi
done

# 3. Dataset-scoped IAM (least privilege — NOT project-wide)
echo "Granting roles/bigquery.dataEditor on ${DATASET} to ${SA}…"
TMP="$(mktemp)"
bq --project_id="$PROJECT" show --format=prettyjson "${PROJECT}:${DATASET}" > "$TMP"
if grep -q "\"userByEmail\": \"${SA}\"" "$TMP" || grep -q "${SA}" "$TMP"; then
  echo "✓ ${SA} already has dataset access"
else
  python3 - "$TMP" "$SA" <<'PY'
import json, sys
path, sa = sys.argv[1], sys.argv[2]
d = json.load(open(path))
d.setdefault("access", []).append({"role": "WRITER", "userByEmail": sa})
json.dump(d, open(path, "w"))
PY
  bq --project_id="$PROJECT" update --source "$TMP" "${PROJECT}:${DATASET}"
  echo "✓ granted dataset WRITER to ${SA}"
fi
rm -f "$TMP"

echo "Done. Set AUDIT_BIGQUERY_BACKEND=gcp + GOOGLE_CLOUD_PROJECT=${PROJECT} +"
echo "BIGQUERY_AUDIT_DATASET=${DATASET} on the Cloud Run service, then verify a"
echo "manual POST /api/v1/internal/audit-export lands rows."
