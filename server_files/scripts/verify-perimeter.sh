#!/usr/bin/env bash
# =====================================================================
# Aurora LTS — Perimeter Verification (run after every deploy)
# =====================================================================
# Validates every layer of the production perimeter end-to-end:
#   DNS  →  TLS / Cert  →  LB  →  IAP  →  Cloud Run ingress  →  Cloud Armor
#
# Exits 0 on full pass, non-zero on first failure.
# Each check prints a green ✓ on pass or a red ✗ with detail on fail.
#
# Usage:
#   bash scripts/verify-perimeter.sh
#
# Environment variables (with sensible defaults):
#   PROJECT          aurora-lts-prod
#   REGION           me-west1
#   API_HOST         api-aurora.com
#   ADMIN_HOST       admin.aurora-ltd.co.il
#   LB_IP            34.117.188.234
#   MARKETING_HOST   aurora-ltd.co.il
# =====================================================================

set -u
export LC_ALL=C

PROJECT="${PROJECT:-aurora-lts-prod}"
REGION="${REGION:-me-west1}"
API_HOST="${API_HOST:-api-aurora.com}"
ADMIN_HOST="${ADMIN_HOST:-admin.aurora-ltd.co.il}"
MARKETING_HOST="${MARKETING_HOST:-aurora-ltd.co.il}"
LB_IP="${LB_IP:-34.117.188.234}"

PASS=0
FAIL=0

# Color helpers (only when stdout is a TTY)
if [ -t 1 ]; then
  GREEN=$'\033[32m' ; RED=$'\033[31m' ; YEL=$'\033[33m' ; DIM=$'\033[2m' ; OFF=$'\033[0m'
else
  GREEN= ; RED= ; YEL= ; DIM= ; OFF=
fi

check() {
  local name="$1" ; local actual="$2" ; local expected_pattern="$3"
  if printf '%s' "$actual" | grep -Eq "$expected_pattern" ; then
    printf "  ${GREEN}✓${OFF} %s ${DIM}(%s)${OFF}\n" "$name" "$(printf '%s' "$actual" | head -c 60)"
    PASS=$((PASS + 1))
  else
    printf "  ${RED}✗${OFF} %s\n      ${DIM}expected pattern: %s${OFF}\n      ${DIM}got:              %s${OFF}\n" \
      "$name" "$expected_pattern" "$(printf '%s' "$actual" | head -c 200)"
    FAIL=$((FAIL + 1))
  fi
}

section() {
  printf "\n${YEL}── %s ──${OFF}\n" "$1"
}

# =====================================================================
section "DNS resolution"
# =====================================================================
API_A=$(dig +short A "$API_HOST" @1.1.1.1 2>/dev/null | head -1)
check "DNS A: $API_HOST" "$API_A" "^${LB_IP}$"

ADMIN_A=$(dig +short A "$ADMIN_HOST" @1.1.1.1 2>/dev/null | head -1)
check "DNS A: $ADMIN_HOST" "$ADMIN_A" "^${LB_IP}$"

# =====================================================================
section "SSL certificates (LB managed)"
# =====================================================================
API_CERT=$(gcloud compute ssl-certificates describe aurora-api-cert-v2 --project="$PROJECT" --global \
  --format='value(managed.status)' 2>/dev/null)
check "Cert: aurora-api-cert-v2 status=ACTIVE" "$API_CERT" "^ACTIVE$"

ADMIN_CERT=$(gcloud compute ssl-certificates describe admin-cert --project="$PROJECT" --global \
  --format='value(managed.status)' 2>/dev/null)
check "Cert: admin-cert status=ACTIVE" "$ADMIN_CERT" "^ACTIVE$"

# =====================================================================
section "Load balancer plumbing"
# =====================================================================
FWD_443=$(gcloud compute forwarding-rules describe aurora-https-fr --project="$PROJECT" --global \
  --format='value(IPAddress,portRange)' 2>/dev/null)
check "Forwarding rule aurora-https-fr @:443" "$FWD_443" "^${LB_IP}.*443-443$"

FWD_80=$(gcloud compute forwarding-rules describe aurora-http-fr --project="$PROJECT" --global \
  --format='value(IPAddress,portRange)' 2>/dev/null)
check "Forwarding rule aurora-http-fr @:80 (HTTPS redirect)" "$FWD_80" "^${LB_IP}.*80-80$"

URL_MAP_HOSTS=$(gcloud compute url-maps describe aurora-urlmap --project="$PROJECT" \
  --format='value(hostRules[].hosts.flatten())' 2>/dev/null)
check "URL map host rules include both api + admin" "$URL_MAP_HOSTS" "api-aurora.com.*admin.aurora-ltd.co.il|admin.aurora-ltd.co.il.*api-aurora.com"

# =====================================================================
section "Cloud Run posture"
# =====================================================================
INGRESS=$(gcloud run services describe aurora-api --project="$PROJECT" --region="$REGION" \
  --format='value(metadata.annotations."run.googleapis.com/ingress")' 2>/dev/null)
check "Cloud Run ingress = internal-and-cloud-load-balancing (SEC-205)" "$INGRESS" "^internal-and-cloud-load-balancing$"

RUN_IAM=$(gcloud run services get-iam-policy aurora-api --project="$PROJECT" --region="$REGION" \
  --format='value(bindings.members.flatten())' 2>/dev/null)
check "Cloud Run IAM: allUsers bound to invoker" "$RUN_IAM" "allUsers"
check "Cloud Run IAM: IAP SA bound to invoker" "$RUN_IAM" "service-9801563953@gcp-sa-iap"

# =====================================================================
section "IAP (admin perimeter)"
# =====================================================================
IAP_ENABLED=$(gcloud compute backend-services describe admin-backend --project="$PROJECT" --global \
  --format='value(iap.enabled)' 2>/dev/null)
check "IAP enabled on admin-backend" "$IAP_ENABLED" "^True$"

IAP_IAM=$(gcloud iap web get-iam-policy --resource-type=backend-services --service=admin-backend \
  --project="$PROJECT" --format='value(bindings.members.flatten())' 2>/dev/null)
check "IAP IAM: ibraheem@aurora-ltd.co.il in allowlist" "$IAP_IAM" "user:ibraheem@aurora-ltd.co.il"

# =====================================================================
section "Cloud Armor (WAF)"
# =====================================================================
SP_API=$(gcloud compute backend-services describe aurora-backend --project="$PROJECT" --global \
  --format='value(securityPolicy.basename())' 2>/dev/null)
check "aurora-backend security policy = aurora-armor" "$SP_API" "^aurora-armor$"

SP_ADMIN=$(gcloud compute backend-services describe admin-backend --project="$PROJECT" --global \
  --format='value(securityPolicy.basename())' 2>/dev/null)
check "admin-backend security policy = aurora-armor-admin (SEC-202)" "$SP_ADMIN" "^aurora-armor-admin$"

# =====================================================================
section "Reachability"
# =====================================================================
# Anonymous *.run.app should be unreachable (ingress lock)
RUN_URL=$(gcloud run services describe aurora-api --project="$PROJECT" --region="$REGION" \
  --format='value(status.url)' 2>/dev/null)
if [ -n "$RUN_URL" ]; then
  ANON_RUN_CODE=$(curl -sS -o /dev/null -w '%{http_code}' "$RUN_URL/" --max-time 10)
  check "Anonymous *.run.app rejected (404 or 403)" "$ANON_RUN_CODE" "^(403|404)$"
fi

# Anonymous api-aurora.com health = 200
ANON_API_CODE=$(curl -sS -o /dev/null -w '%{http_code}' "https://${API_HOST}/api/v1/onboarding/health" --max-time 10)
check "Anonymous api-aurora.com onboarding health = 200" "$ANON_API_CODE" "^200$"

# Anonymous admin host = 302 to Google login (IAP redirect)
ANON_ADMIN_CODE=$(curl -sS -o /dev/null -w '%{http_code}' "https://${ADMIN_HOST}/" --max-time 10)
check "Anonymous admin.aurora-ltd.co.il = 302 (IAP intercepts)" "$ANON_ADMIN_CODE" "^302$"

ADMIN_REDIRECT=$(curl -sS -o /dev/null -D - "https://${ADMIN_HOST}/" --max-time 10 2>/dev/null \
  | grep -i '^location:' | head -1 | tr -d '\r')
check "IAP redirect points at accounts.google.com" "$ADMIN_REDIRECT" "accounts.google.com"

# =====================================================================
section "CORS allowlist (SEC-204)"
# =====================================================================
CORS_GOOD=$(curl -sS -o /dev/null -D - -X OPTIONS \
  -H "Origin: https://aurora-ltd.co.il" \
  -H "Access-Control-Request-Method: POST" \
  -H "Access-Control-Request-Headers: Content-Type" \
  "https://${API_HOST}/api/v1/marketing/lead" --max-time 10 2>/dev/null \
  | grep -i '^access-control-allow-origin:' | head -1 | tr -d '\r')
check "CORS preflight from aurora-ltd.co.il is allowed" "$CORS_GOOD" "aurora-ltd.co.il"

CORS_BAD=$(curl -sS -o /dev/null -D - -X OPTIONS \
  -H "Origin: https://evil.example.com" \
  -H "Access-Control-Request-Method: POST" \
  -H "Access-Control-Request-Headers: Content-Type" \
  "https://${API_HOST}/api/v1/marketing/lead" --max-time 10 2>/dev/null \
  | grep -ic '^access-control-allow-origin: https://evil.example.com')
check "CORS preflight from evil.example.com is denied (no allow-origin header)" "$CORS_BAD" "^0$"

# =====================================================================
section "Synthetic SQLi probe (Cloud Armor)"
# =====================================================================
# Real SQLi pattern that should be PREVIEW-matched on aurora-backend's WAF.
SQLI_CODE=$(curl -sS -o /dev/null -w '%{http_code}' \
  "https://${API_HOST}/?id=1%27%20OR%20%271%27%3D%271%27%20--" --max-time 10)
check "Synthetic SQLi reaches origin (preview mode = 200 / 404, not 403)" "$SQLI_CODE" "^(200|404)$"

# =====================================================================
section "Executive Dashboard (Appendix H Tier 1)"
# =====================================================================
# /executive on aurora-admin-ui should redirect anonymous traffic to IAP login.
EXEC_CODE=$(curl -sS -o /dev/null -w '%{http_code}' \
  "https://admin.aurora-ltd.co.il/executive" --max-time 10)
check "Anonymous /executive returns 302 (IAP intercepts)" "$EXEC_CODE" "^302$"

# New aurora-api exec endpoints must require auth (401 on anonymous).
EXEC_API_CODE=$(curl -sS -o /dev/null -w '%{http_code}' \
  "https://${API_HOST}/api/v1/admin/exec/dashboard-summary" --max-time 10)
check "Anonymous /api/v1/admin/exec/dashboard-summary returns 401" "$EXEC_API_CODE" "^401$"

# =====================================================================
section "AI Copilot Console (Appendix J Sprint 3)"
# =====================================================================
# /executive/copilot on aurora-admin-ui should redirect anonymous traffic to IAP.
COPILOT_PAGE_CODE=$(curl -sS -o /dev/null -w '%{http_code}' \
  "https://admin.aurora-ltd.co.il/executive/copilot" --max-time 10)
check "Anonymous /executive/copilot returns 302 (IAP intercepts)" "$COPILOT_PAGE_CODE" "^302$"

# Copilot conversations endpoint requires require_admin
COPILOT_CONV_CODE=$(curl -sS -o /dev/null -w '%{http_code}' \
  "https://${API_HOST}/api/v1/admin/exec/copilot/conversations" --max-time 10)
check "Anonymous /api/v1/admin/exec/copilot/conversations returns 401" "$COPILOT_CONV_CODE" "^401$"

# WebAuthn register endpoint requires require_admin
WEBAUTHN_REG_CODE=$(curl -sS -o /dev/null -w '%{http_code}' -X POST \
  "https://${API_HOST}/api/v1/admin/exec/webauthn/register/start" --max-time 10)
check "Anonymous /api/v1/admin/exec/webauthn/register/start returns 401" "$WEBAUTHN_REG_CODE" "^401$"

# Copilot budget-extend (admin override) requires require_admin
COPILOT_BUDGET_CODE=$(curl -sS -o /dev/null -w '%{http_code}' -X POST \
  -H "Content-Type: application/json" -d '{}' \
  "https://${API_HOST}/api/v1/admin/exec/copilot/budget-extend" --max-time 10)
check "Anonymous /api/v1/admin/exec/copilot/budget-extend returns 401" "$COPILOT_BUDGET_CODE" "^401$"

# =====================================================================
section "Vertex AI / Gemini multi-workload (Appendix L Sprint 4)"
# =====================================================================
# LLM unified usage aggregate (Mission Control cost tile)
LLM_USAGE_CODE=$(curl -sS -o /dev/null -w '%{http_code}' \
  "https://${API_HOST}/api/v1/admin/exec/llm/usage" --max-time 10)
check "Anonymous /api/v1/admin/exec/llm/usage returns 401" "$LLM_USAGE_CODE" "^401$"

# GeminiRun feed (used by WhatsApp Hub + receipt detail view)
GEMINI_RUNS_CODE=$(curl -sS -o /dev/null -w '%{http_code}' \
  "https://${API_HOST}/api/v1/admin/exec/gemini/runs" --max-time 10)
check "Anonymous /api/v1/admin/exec/gemini/runs returns 401" "$GEMINI_RUNS_CODE" "^401$"

# Receipt classifier (POST-only — GET should 405; explicit POST checks 401)
RECEIPT_CLASSIFY_CODE=$(curl -sS -o /dev/null -w '%{http_code}' -X POST \
  -H "Content-Type: application/json" -d '{}' \
  "https://${API_HOST}/api/v1/admin/exec/receipts/1/classify-with-gemini" --max-time 10)
check "Anonymous /receipts/{id}/classify-with-gemini returns 401" "$RECEIPT_CLASSIFY_CODE" "^401$"

# =====================================================================
section "Growth & Milestone Activation Engine (Appendix M Sprint 5)"
# =====================================================================
# Growth summary (system-scale metrics)
GROWTH_SUMMARY_CODE=$(curl -sS -o /dev/null -w '%{http_code}' \
  "https://${API_HOST}/api/v1/admin/exec/growth/summary" --max-time 10)
check "Anonymous /api/v1/admin/exec/growth/summary returns 401" "$GROWTH_SUMMARY_CODE" "^401$"

# Milestone grid
GROWTH_MILESTONES_CODE=$(curl -sS -o /dev/null -w '%{http_code}' \
  "https://${API_HOST}/api/v1/admin/exec/growth/milestones" --max-time 10)
check "Anonymous /api/v1/admin/exec/growth/milestones returns 401" "$GROWTH_MILESTONES_CODE" "^401$"

# Activate endpoint (POST-only, step-up-gated)
GROWTH_ACTIVATE_CODE=$(curl -sS -o /dev/null -w '%{http_code}' -X POST \
  -H "Content-Type: application/json" -d '{}' \
  "https://${API_HOST}/api/v1/admin/exec/growth/activate/hcarl_orchestrator" --max-time 10)
check "Anonymous /growth/activate/{feature} returns 401" "$GROWTH_ACTIVATE_CODE" "^401$"

# =====================================================================
# Summary
# =====================================================================
printf "\n${YEL}─────────────────────────────────────────${OFF}\n"
TOTAL=$((PASS + FAIL))
if [ "$FAIL" -eq 0 ]; then
  printf "${GREEN}✓ ALL ${PASS}/${TOTAL} CHECKS PASSED${OFF}\n"
  exit 0
else
  printf "${RED}✗ ${FAIL} CHECK(S) FAILED${OFF}  (${PASS}/${TOTAL} passed)\n"
  exit 1
fi
