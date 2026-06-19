# Aurora LTS — Production Readiness Checklist

_Last updated: 2026-06-19 · Status: every surface running in **stub mode**_

This is the single page you return to whenever you obtain a new external
credential and want to know: "where does it go, and what flag does it
unblock?"

## Current state of the 5 surfaces

| Surface | URL / target | Status | Mode |
|---|---|---|---|
| **Marketing site** | `https://aurora-ltd.co.il` | ✅ Live | Static |
| **Business Portal** | `https://app.aurora-ltd.co.il` | ✅ Live | Talks to live API |
| **Backend API (M1)** | `https://api-aurora-lts.com` | ✅ Live | **All providers stub/mock** |
| **CEO Dashboard** | Tauri Mac app (local) | ✅ Buildable | Aurora theme bundled in API v1-15-8 |
| **Accountant Portal** | Tauri Windows app (build locally) | ✅ Buildable | Needs Windows machine + cert for production binary |

The system is **operationally complete in stub mode**. Real customers can
sign up, see the UI, walk through the wizard — but every external
integration (payments, OTP, KYC, WhatsApp, ITA) returns a fake "OK"
instead of doing real work.

## What to obtain, and what each unlocks

Order roughly reflects business priority — the first 3 must land before
you can charge a real customer.

### Tier 1 — Required to onboard the first real customer

| External credential | Where it plugs in | Today's flag | Flip to |
|---|---|---|---|
| **SendGrid API key** | Secret Manager: `SENDGRID_API_KEY` | `OTP_BACKEND=stub` | `OTP_BACKEND=production` |
| **Inforu or Twilio SMS creds** | Secret Manager: `INFORU_*` or `TWILIO_*` | (part of OTP_BACKEND=stub) | same flip as above |
| **GCS KYC service-account JSON** | Secret Manager: `GCS_KYC_SA_KEY_JSON` | `KYC_BACKEND=stub` | `KYC_BACKEND=gcs` |
| **PayPlus merchant API key** | Secret Manager: `PAYPLUS_API_KEY` | `PAYPLUS_BACKEND=stub` | `PAYPLUS_BACKEND=production` |
| **PayPlus terminal number** | env var `PAYPLUS_TERMINAL_NUMBER` | placeholder `YOUR_PAYPLUS_TERMINAL_HERE` | real terminal ID from PayPlus account |

**Unblocks**: full real signup → OTP → KYC upload → first card billing.

### Tier 2 — Required to unlock the WhatsApp surface

| External credential | Where it plugs in | Today's flag | Flip to |
|---|---|---|---|
| **Meta WhatsApp access token** | Secret Manager: `WHATSAPP_ACCESS_TOKEN` | placeholder `YOUR_META_ACCESS_TOKEN_HERE` | real Meta Cloud API token |
| **Meta verify token** | Secret Manager: `WHATSAPP_VERIFY_TOKEN` | placeholder | self-chosen secret string |
| **Meta phone number ID** | Secret Manager: `WHATSAPP_PHONE_NUMBER_ID` | placeholder | from Meta WhatsApp Business Manager |
| **Meta app secret** | Secret Manager: `WHATSAPP_APP_SECRET` | placeholder | from Meta Developers console |
| **Meta business verification** | n/a (external paperwork) | not started | submit business docs via Meta Business Manager |

**Unblocks**: real WhatsApp FSM (the chat-based onboarding + invoice flow).

### Tier 3 — Required for real ITA allocation numbers (compliance)

| External credential | Where it plugs in | Today's flag | Flip to |
|---|---|---|---|
| **ITA test credentials** | Secret Manager: `ITA_SIGNING_KEY_JSON` (private JWT signing key) | `ITA_BACKEND=mock` | `ITA_BACKEND=production` |
| **ITA Software House certification** | external paperwork (6-12 weeks) | not started | submit binder via ITA portal |
| **ITA assigned Software House ID** | env: `ITA_SOFTWARE_HOUSE_ID` | placeholder | issued upon cert |

**Unblocks**: every above-threshold invoice gets a real ITA allocation
number. Without it, the system still operates but writes mock allocation
IDs that won't pass an ITA audit.

### Tier 4 — Required to ship signed desktop apps

| External credential | Where it plugs in | Today's state | Flip to |
|---|---|---|---|
| **C** | local Keychain on the Mac that builds CEO Dashboard `.dmg` | no cert | active membership ($99/yr) |
| **App-specific Apple ID password** | local env at build time | none | created at appleid.apple.com → App-Specific Passwords |
| **Windows Authenticode cert** | local cert store on the Windows machine that builds Accountant `.msi` | no cert | OV or EV cert from a CA |

**Unblocks**: distributable, gatekeeper-friendly binaries that users
can install without "unknown developer" warnings.

## Recommended sequence

1. **Today** — register SendGrid + Inforu (or Twilio) accounts, set the
   secrets in Google Secret Manager, flip `OTP_BACKEND=production` →
   deploy. Real users can now sign up.

2. **This week** — open a PayPlus merchant account (the founder's
   israeli business documents are needed; PayPlus typically issues
   credentials in 2-5 business days). Flip `PAYPLUS_BACKEND=production`.

3. **This week** — create a GCS bucket for KYC uploads, generate a
   service-account JSON limited to that bucket, set the secret, flip
   `KYC_BACKEND=gcs`.

4. **Within 2 weeks** — open the Meta WhatsApp Business Manager account,
   submit business verification, obtain the 4 tokens, set them, flip the
   placeholders. Test the WhatsApp FSM end-to-end with the founder's
   own phone.

5. **Now-to-3 months** — start the ITA Software House paperwork track in
   parallel. Engineering work is already done; the gate is bureaucratic
   review (6-12 weeks).

6. **Before public launch** — purchase Apple Developer ID + Windows
   Authenticode cert, set up CI signing on the per-OS build machines.

## How to flip a flag (the actual procedure)

For any flag listed above:

```bash
# 1. Store the secret value in Secret Manager (one-time)
echo -n "<the actual secret>" | gcloud secrets create <SECRET_NAME> \
    --data-file=- --project=aurora-lts-prod

# 2. Grant Cloud Run access (one-time per secret)
gcloud secrets add-iam-policy-binding <SECRET_NAME> \
    --member=serviceAccount:aurora-api-runner@aurora-lts-prod.iam.gserviceaccount.com \
    --role=roles/secretmanager.secretAccessor \
    --project=aurora-lts-prod

# 3. Update the Cloud Run service to:
#    a. mount the new secret as env var
#    b. flip the *_BACKEND flag to its production value
gcloud run services update aurora-api \
    --region=me-west1 \
    --project=aurora-lts-prod \
    --update-secrets=<ENV_VAR_NAME>=<SECRET_NAME>:latest \
    --update-env-vars=<FLAG_NAME>=<NEW_VALUE>

# 4. Verify
curl -fsS https://api-aurora-lts.com/api/v1/onboarding/health
```

The startup guard in `services/aurora-main-api/app/config/backend_check.py`
will refuse to boot if a `*_BACKEND` flag is set to `production` but the
secret it depends on is missing — this is intentional ("fail closed").

## What's intentionally left in stub mode

These don't need flipping for normal operations; they're for future
work or only flip when you scale up:

- `RATE_LIMIT_BACKEND=memory` → flip to `redis` only when running multi-instance Cloud Run
- `OCR_BACKEND=stub` → flip to `documentai` when ready to process real receipts
- `DLP_BACKEND=stub` → flip to `gcp` when receipt PII redaction matters
- `GEMINI_BACKEND=stub` → flip to `vertex` when the AI categorizer is needed
- `AUDIT_BIGQUERY_BACKEND=stub` → flip to `gcp` when running the daily audit export

## Reference: where each surface's source lives

| Surface | Source path | Deploy pipeline |
|---|---|---|
| Backend M1 + bundled dashboard | `services/aurora-main-api/` | `cloudbuild.yaml` |
| Backend M2 (copilot) | `services/aurora-api-core/` | `cloudbuild.core.yaml` |
| Marketing site | `~/Desktop/aurora-website/` (separate repo) | `aurora-website/cloudbuild.yaml` |
| Business Portal | `front-end/business-portal/` | `front-end/business-portal/cloudbuild.yaml` |
| CEO Dashboard | `front-end/ceo-dashboard/` | bundled into M1 image; standalone Tauri build via `npm run build` |
| Accountant Portal | `front-end/accountant-portal/` | Tauri build via `npm run tauri build` on Windows |

Cloud Build trigger for the Business Portal can be wired (currently
manual): `gcloud builds triggers create github --repo-name=ASG-Middleware
--repo-owner=Aurora-LTS-LTD --branch-pattern=^main$
--build-config=front-end/business-portal/cloudbuild.yaml
--included-files=front-end/business-portal/**`.

## Verification cheat-sheet

Run any time to confirm everything is still alive:

```bash
for url in \
    "https://aurora-ltd.co.il/" \
    "https://app.aurora-ltd.co.il/" \
    "https://api-aurora-lts.com/api/v1/onboarding/health" \
    "https://api-aurora-lts.com/" ; do
  printf "%-60s " "$url"
  curl -sS -o /dev/null -w "HTTP %{http_code} in %{time_total}s\n" "$url"
done
```

All four should return 200. If any fail, check Cloud Run / Firebase
Hosting first — the front door is almost always the culprit, not the
code.
