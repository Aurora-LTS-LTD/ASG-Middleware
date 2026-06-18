# aurora-api — deploy config (config-as-code)

The runtime configuration of the `aurora-api` Cloud Run service, captured from the
repo so it is reproducible and reviewable (it previously lived only in the live
service). Captured 2026-06-18 from revision `aurora-api-00102-qaf` (`v1-15-7`).

## Files
- **`env.prod.yaml`** — all NON-SECRET env vars (the operational config).
- **`restore-service-config.sh`** — recreates the full service (env + secrets +
  scaling + VPC + SA + resources) given an image tag. Secrets are referenced by
  Secret Manager name; no secret values are stored in the repo.

## How deploys actually work
Routine deploys go through **`cloudbuild.yaml`** (build → migration job → green@0%
→ `/health` smoke → traffic shift → tag stable). Its `deploy-green` step sets ONLY
`--image`, so it **preserves** everything in this directory. You only run
`restore-service-config.sh` to **recreate** the service or repair drifted config —
never for a normal deploy.

```bash
# recreate / repair (review first!):
./restore-service-config.sh me-west1-docker.pkg.dev/aurora-lts-prod/aurora/api:v1-15-7
```

## Infra baked into the restore script
- Region `me-west1`, project `aurora-lts-prod`, SA `aurora-run@…`.
- Cloud SQL: `aurora-lts-prod:me-west1:aurora-pg` (private IP → Direct VPC egress,
  `--network default --subnet default --vpc-egress private-ranges-only`).
- Scaling: `min-instances 1`, `max-instances 4`; concurrency 80; timeout 120s;
  cpu 2; memory 1Gi.
- DB pool budget tuned for `db-f1-micro`: pool 5 + overflow 5 (≈20 conns/instance).

## ⚠️ Pre-launch states to flip before real customer traffic
These are deliberate launch-window settings, NOT production-final:
- **`ITA_BACKEND=mock` + `AURORA_ALLOW_MOCK_ITA=1`** — invoice allocation returns
  FAKE numbers. Flip to `ITA_BACKEND=production` (+ real creds) and REMOVE the
  allow-mock flag the moment the ITA Software-House cutover completes.
- **`PAYPLUS_API_BASE=https://sandboxapi.payplus.co.il`** — sandbox payments. Point
  at the production PayPlus base (+ prod terminal/key secrets) for real charges.
- **`SMS_PROVIDER=stub`** + **`ONBOARDING_REQUIRE_PHONE_OTP=false`** — email-only
  onboarding. Provision Inforu/Twilio + flip phone OTP on when ready.
- **`OCR_BACKEND` / `DLP_BACKEND` / `GEMINI_BACKEND = stub`** — those features no-op.

## ⚠️ Cloud SQL capacity (the launch gate)
`aurora-pg` is **`db-f1-micro`** (~22 usable connection slots). The current config
(pool 5+5, max-instances 1) is safe for pre-launch / low traffic, but worst-case
under autoscaling far exceeds the slot budget. **Bump the tier before real
concurrent traffic** (restarts the DB ~1–2 min):
```bash
gcloud sql instances patch aurora-pg --project aurora-lts-prod --tier db-custom-1-3840
# then raise AURORA_CLOUD_RUN_MAX_INSTANCES + max-instances accordingly.
```
Deliberately deferred 2026-06-18 — no benefit (and a cost + restart) until launch.

See also the memory notes: db-unblock-and-prod-config, frontend-hosting.
