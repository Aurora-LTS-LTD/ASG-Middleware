# Aurora LTS — Post-Launch Architectural Master Plan

> Status: draft for review · Author: engineering · Context: written on launch day,
> immediately after the self-service onboarding wizard + hardening pass shipped.

This plan sequences the three workstreams that turn the **launch build** (which is
production-correct in *code* but runs against stubbed external providers) into a
**fully live** system. Each workstream is gated on something we do not control from
code — a regulator-issued key, a cloud-infra provisioning run, or a cross-service
contract — so each is written as: *what exists today → what's gated → cutover steps
→ rollback.*

**Guiding principle (unchanged):** no unilateral flips. Enabling real ITA allocations,
real customer billing, or real audit export each has business/compliance/ops
sign-off as an explicit gate. Code lands ahead of the gate; the gate is flipped by a
human with the credentials and the authority.

---

## 0. Where launch leaves us (the honest baseline)

| Capability | Code | Live? | Gate to go live |
|---|---|---|---|
| Business-owner registration/onboarding | ✅ shipped (wizard → real `/onboarding/*` FSM) | ⛔ partial | `OTP_BACKEND`, `KYC_BACKEND`, `PAYPLUS_BACKEND` → real providers |
| Owner/accountant login, invoices, lifecycle | ✅ shipped, merged | ✅ | — |
| ITA allocation issuance | ✅ hardened (`app/services/ita/*`) | ⛔ | ITA `SOFTWARE_HOUSE_ID` + RSA key + compliance sign-off |
| BigQuery audit export | ✅ exporter + schemas + provisioning script | ⛔ | run `provision_bigquery_audit.sh` + `AUDIT_BIGQUERY_BACKEND=gcp` |
| Append-only audit guards | ✅ app-level (tested 8/8) | ✅ (app) | DB-level triggers = WS2 hardening item |
| Cross-portal data sync | ⚠️ pull-on-load only | ⚠️ | WS3 below |

The onboarding pipeline shares the **same gating shape** as ITA/BigQuery: the
application code is complete and tested; going live is an *environment + provider*
action, not a code action.

---

## WS1 — Real ITA Production Integration

### Today
- `app/services/ita/{auth,client,vat_filing}.py`: RS256 request signing
  (`sign_request`), deterministic idempotency (`build_request_id = "{invoice}:{retry}"`),
  9-digit seller-tax-id validation, retryable-vs-terminal verdict (429/5xx retry;
  other 4xx terminal), granular `error_code`s, full audit to `ita_audit_log`.
- `app/services/allocation_queue.py`: backoff `[30s, 2m, 10m, 1h×7]`, `MAX_RETRIES=10`,
  early-terminal on non-retryable, terminal status `rejected`.
- `ITA_BACKEND=mock` everywhere. Runbook: `docs/runbooks/ita-production-cutover.md`.

### Gated on
ITA-issued `ITA_SOFTWARE_HOUSE_ID`; a real RSA private key (`AURORA_ITA_PRIVATE_KEY`
PEM) in Secret Manager; business/regulatory sign-off.

### Build-ahead (do now, no gate)
1. **Key handling.** Load the PEM from Secret Manager (`SECRET_BACKEND=gcp`), never
   from env in prod; add a boot-time `backend_check` that asserts the key parses +
   the `kid` matches before accepting traffic (fail closed). Support **dual-key
   rotation** (active + next `kid`) so a key roll is zero-downtime.
2. **Inbound webhooks.** ITA posts allocation status changes (approved/revoked) and
   periodic reconciliation. Add `POST /api/v1/ita/webhook`:
   - Verify the ITA signature (their public key, pinned by `kid`).
   - Idempotent by ITA event id (dedupe table); append to `ita_audit_log`.
   - Transition the invoice via the **existing** `invoice_lifecycle.transition()` —
     never mutate status directly.
   - Always 2xx fast; do work async on the allocation queue.
3. **Reconciliation job.** Daily: for every `finalized` invoice with an allocation,
   confirm ITA still holds it; flag drift to an ops dashboard. Read-only against ITA.

### Cutover (gated)
Canary `10 → 50 → 100` invoices behind a per-business `ita_enabled` flag; watch
`ita_audit_log` success rate + latency; `ITA_BACKEND=mock` is the instant rollback.

### Risks
Clock skew on JWT `exp` (mitigate: NTP + skew tolerance); key compromise (mitigate:
rotation + Secret Manager audit logs); ITA rate limits (mitigate: the existing queue
backoff + a global token bucket).

---

## WS2 — BigQuery Immutable Audit Infrastructure

### Today
- `app/services/compliance/bigquery_export.py`: cursor-tracked incremental export,
  tamper-evident hash chain `sha256(prev + Σ row_hashes)`, PII redaction,
  `insert_rows_json`. `AUDIT_BIGQUERY_BACKEND=stub` (NDJSON to /tmp).
- `schemas/bigquery/{action_logs,ita_audit_log}.json`; `scripts/provision_bigquery_audit.sh`
  (dataset + day-partitioned tables + dataset-scoped WRITER IAM); phase30 cursor
  migration; app-level immutability guards (tested 8/8).

### Gated on
An ops run of `provision_bigquery_audit.sh` (creates the `asg_audit` dataset/tables/IAM),
then `AUDIT_BIGQUERY_BACKEND=gcp` + `GOOGLE_CLOUD_PROJECT` + `BIGQUERY_AUDIT_DATASET`.

### Build-ahead (do now, no gate)
1. **Immutability at the database, not just the app.** The current guards are
   SQLAlchemy `before_update`/`before_delete` listeners — they protect the app path
   but not a raw `psql`. Add Postgres triggers (`RAISE EXCEPTION` on
   UPDATE/DELETE for `action_logs`, `ita_audit_log`) as a migration; the app-level
   guard stays as defense-in-depth + the `AURORA_AUDIT_ALLOW_OVERRIDE` escape hatch.
2. **Export scheduling + observability.** Cloud Scheduler → `POST /internal/audit-export`
   (already exists); export the per-run row count + the terminal hash-chain head to a
   metric; alert if the cursor stalls or a hash-chain break is detected.
3. **Chain verifier.** A read-only `verify_audit_chain()` that walks BigQuery and
   re-derives the hash chain, comparing against the stored heads — run weekly, surface
   to compliance. This is the actual "prove it wasn't tampered with" tool.
4. **Partition + retention policy.** 7-year retention on the partitioned tables
   (Israeli tax record-keeping); document the legal hold posture.

### Cutover (gated)
Provision in a non-prod GCP project first; run one manual export; verify rows land +
`verify_audit_chain()` passes; then prod. Rollback = `AUDIT_BIGQUERY_BACKEND=stub`
(export halts; the cursor resumes cleanly later — no data loss, BigQuery is a sink).

---

## WS3 — Continuous Cross-Portal Data Synchronization

The three frontends (CEO, Accountant, Business-Owner) read the **same** M1 data but
today each pulls on page load via React Query. "Continuous sync" means a change one
actor makes (an accountant finalizes an invoice; an owner cancels one; ITA approves an
allocation) reflects in the others without a manual refresh.

### Phased approach
1. **Phase A — tighten polling (no new infra).** Standardize React Query
   `staleTime`/`refetchInterval` + `refetchOnWindowFocus` per data class
   (invoices/allocations: short; KPIs: longer). Cheap, ships immediately, covers 80%.
2. **Phase B — server push (SSE).** Add `GET /api/v1/events/stream` (Server-Sent
   Events; one-way, proxy-friendly, no WebSocket infra). Emit
   `{entity, id, event, scope}` envelopes on lifecycle transitions, allocation status
   changes, and ITA webhooks. Each portal subscribes filtered by its auth scope
   (`get_business_filter` / accountant engagement) and invalidates the matching React
   Query keys. **Authorization is the hard part** — an owner must never receive
   another business's events; the scope filter is enforced server-side per connection.
3. **Phase C — fan-out at scale.** If Cloud Run instance count makes in-process SSE
   fan-out insufficient, back it with Pub/Sub (one topic, per-instance subscription) so
   any instance can deliver to any connected client. Defer until Phase B's metrics say so.

### Consistency model
M1 (Postgres) stays the **single source of truth**; events are *cache-invalidation
hints*, never the data itself — a dropped event degrades to the existing poll, never
to stale-forever or wrong data. Events carry no PII (id + scope only); the client
re-fetches through the normal scoped endpoint.

### Risks
Scope leakage across tenants (mitigate: per-connection server-side scope, integration
test per role); reconnect storms (mitigate: jittered backoff + `Last-Event-ID` resume);
event/DB ordering (mitigate: emit only *after* commit, inside the lifecycle transition).

---

## Suggested sequence

1. **Unblock launch:** flip onboarding providers (`OTP`/`KYC`/`PAYPLUS`) in a non-prod
   project, run the full registration journey, then prod — this is what makes
   "self-service signup" actually live.
2. **WS2 build-ahead** (DB triggers + chain verifier) — pure code, high compliance value.
3. **WS1 build-ahead** (webhooks + reconciliation) while awaiting ITA credentials.
4. **WS3 Phase A** (polling) immediately; **Phase B** (SSE) once WS1 webhooks exist
   (they're the richest event source).
5. **Gated cutovers** (ITA canary, BigQuery provisioning) when sign-off + creds land.
