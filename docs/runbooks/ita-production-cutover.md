# ITA Production Cutover — Runbook (GATED)

> 🚨 **DO NOT EXECUTE WITHOUT WRITTEN BUSINESS + REGULATORY SIGN-OFF.**
> Flipping `ITA_BACKEND=production` makes Aurora issue **real Israel Tax Authority
> allocation numbers** for live invoices. This is irreversible per-allocation and
> regulated. The application code is ready (see below); the cutover is intentionally
> held until ASG provides the ITA-issued software-house credentials + the signed
> private key, and compliance approves.

## Status: code-ready, cutover held
The production ITA path is fully implemented + hardened (WS2):
- `app/services/ita/auth.py` — RS256 JWT signing (`iss`=software-house-id, `sub`=seller
  tax id, `aud`, `iat`, `exp`=+TTL, `jti`=request_id). 9-digit tax-id validation before signing.
- `app/services/ita/client.py` — `_production_call` (timeout, masked logs, always-write
  `ita_audit_log`), **deterministic idempotency** (`request_id="<invoice_id>:<retry_count>"`),
  retryability verdict (429 + 5xx + transport → retry; other 4xx → permanent), granular `error_code`.
- `app/services/allocation_queue.py` — honors the verdict: permanent failures go terminal
  (`allocation_status="rejected"`) immediately; transient ones back off `[30s,2m,10m,1h…]` to `MAX_RETRIES=10`, then terminal.
- `app/services/ita/vat_filing.py` — VAT filing reuses `sign_request` (the `build_ita_jwt` crash is fixed). Stays on `VAT_FILING_BACKEND=stub` for this cutover.

`ITA_BACKEND` defaults to `mock` everywhere; nothing here changes behavior until the env/secret payload below is applied.

## Prerequisites (business / ops — external)
1. **ITA software-house certification** complete; ASG has its **`ITA_SOFTWARE_HOUSE_ID`** (currently missing — hard blocker).
2. The **RSA private key** whose public counterpart is registered at ITA, as a PEM file.
3. Compliance sign-off recorded (the binder).

## Env + secret payload (`gcloud run services update aurora-api …`)
| Var / Secret | Value | Notes |
|---|---|---|
| `ITA_BACKEND` | `production` | HARD-FAIL today (`mock`) per `backend_check.py` |
| `ITA_SOFTWARE_HOUSE_ID` | ASG's ITA id | **required** — JWT `iss` |
| `ITA_PRIVATE_KEY_SECRET` → secret `AURORA_ITA_PRIVATE_KEY:latest` | PEM RSA key | `--update-secrets`; loaded via `get_secret()` |
| `ITA_API_BASE` / `ITA_ALLOCATION_PATH` / `ITA_AUDIENCE` / `ITA_JWT_TTL_SECONDS` / `ITA_TIMEOUT_SECONDS` | defaults | OK unless ITA specifies otherwise |
| `VAT_FILING_BACKEND` | `stub` | keep VAT filing OFF for this cutover |

```bash
# Provision the signing key (one-time)
gcloud secrets create AURORA_ITA_PRIVATE_KEY --data-file=ita_private_key.pem --project=aurora-lts-prod
gcloud secrets add-iam-policy-binding AURORA_ITA_PRIVATE_KEY \
  --member=serviceAccount:aurora-run@aurora-lts-prod.iam.gserviceaccount.com \
  --role=roles/secretmanager.secretAccessor --project=aurora-lts-prod
```

## Cutover sequence (gated)
1. Build + push the image via `cloudbuild_verify.yaml` (import-gate + lifespan-gate green).
2. Deploy a **no-traffic candidate** with the env/secret above.
3. **Boot gate:** confirm logs show `Server is ready` + `backend_check` passes (no `ITA_BACKEND` hard-fail).
4. **Smoke (single real invoice):** finalize one small live invoice → verify a real allocation number + an `ita_audit_log` row (`backend=production`, `success=true`).
5. **Gated canary:** shift traffic **10 → 50 → 100%**, watching 5xx + `ita_audit_log` failure rate + latency between steps.
6. **Rollback** on elevated 5xx / allocation failures: shift traffic back to the prior revision; `ITA_BACKEND` stays `production` only on the healthy revision. (Per-allocation issuance is not reversible — reverse business-side via credit notes.)
7. Tag the release once stable.

## Verification of *this* PR (code only — no cutover performed)
- `tests/test_ita_integration.py` — 16/16 (idempotency determinism, tax-id validation, sign_request guard order, retryable/error_code contract, `ITA_BACKEND` defaults to `mock`).
- No env/secret changed; no key provisioned; `ITA_BACKEND` remains `mock`.
