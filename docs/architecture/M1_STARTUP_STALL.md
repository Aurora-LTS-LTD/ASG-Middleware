# M1 (aurora-api) — monorepo image startup stall

**Status:** RESOLVED · v0.2.3 live on 100% traffic since 2026-05-31 · revision `aurora-api-00055-reh`
**Resolution commits (branch `feature/monorepo-restructure`):**
  - `4eea2a5` refactor(database): lazy SQLAlchemy engine init with `_LazyEngine` proxy + double-checked locking
  - `edb5d60` fix(database): bind sessionmaker to the `_LazyEngine` proxy instead of the raw `get_engine` callable
    (caught at v0.2.2 canary verification — SQLAlchemy 2.0's `sessionmaker(bind=…)` does NOT evaluate a callable
    bind; it calls `.connect()` directly, raising `AttributeError: 'function' object has no attribute 'connect'`
    on every DB session)

**Cutover summary (M1):**
  - Migration Job `aurora-api-db-setup` runs `python -m app.db_setup` out-of-band (created at v0.2.2, executed
    clean at v0.2.3 — Phase 6 `'function' object has no attribute 'connect'` warning gone).
  - Gated rollout 10 → 50 → 100 % over ~10 min via `gcloud run services update-traffic`.
  - Gates at each step: zero 5xx, zero `WORKER TIMEOUT`/`QueuePool`/`Traceback`, `Engine bound` exactly once
    per (instance × worker), worker churn flat.
  - Rollback revision (`aurora-api-00045-fug`) preserved at 0%; can be re-promoted instantly if needed.

**M2 (aurora-api-core) cutover:**
  - v0.2.0 (buggy, same lazy-engine flaw) -> v0.2.3 (`aurora-api-core-00008-daj`) at 100%.
  - Stable URL: `https://aurora-api-core-fpql4rs7aa-zf.a.run.app`.
  - `/api/v1/core/health` returns `"compliance_backends":"live"` (ITA_BACKEND=production, AUDIT_BIGQUERY_BACKEND=gcp).

**Live impact during cutover:** NONE — the lazy-engine fix shifted import-time work to first-request,
so the boot path is non-blocking and the gated traffic shift saw 0% error rate.

---

## Historical record (kept for postmortem traceability)

**Original status:** OPEN · blocks the M1 cutover to the monorepo layout · 2026-05-31
**Live impact during investigation:** NONE. The live tax API ran on its pre-monorepo image and was healthy.
All investigation below was on `--no-traffic` canaries; live traffic was never shifted during diagnosis.

## Symptom
A Cloud Run revision built from the **new monorepo image** (`services/aurora-main-api/Dockerfile`,
`app.main:app`, with `aurora_shared` installed as a wheel) **stalls for ~6 minutes during Python
import at startup** and never reaches `[STARTUP] Server is ready!` within the observed window.
The current live image (pre-monorepo, `server_files/` layout) boots in **seconds**.

## Evidence (canary revisions `…-00047-tep` v0.2.0, `…-00048-sep` v0.2.1)
- `gunicorn` "Booting worker" logs at **T0**; TCP startup probe passes immediately (port bound).
- `[DATABASE] Engine bound …` — a **module-import-time** print in `aurora_shared/database/connection.py`
  (right after `create_engine`, before any DB connection) — does not appear until **~T0 + 6 min**.
- No `[STARTUP]` banner / migrations / "Server is ready!" within the window; **no** Traceback,
  **no** `Worker failed to boot`, **no** `WORKER TIMEOUT`, **no** `Connection refused`.
- So the worker is blocked **inside the import of `app.main`** (before the lifespan startup event),
  spending ~6 min to reach the early `from aurora_shared.database import …` line.

## Ruled out
- **Migrations.** v0.2.1 gates the 18 `migrate_phase*` + `create_tables()` OFF the boot path
  (moved to `app.db_setup`, run via `python -m app.db_setup`). v0.2.1 stalls **identically** → not migrations.
- **VPC egress / DB networking.** The canary inherited M1's Direct VPC egress
  (`network-interfaces default/default`, `vpc-egress private-ranges-only`) + `--add-cloudsql-instances`
  — verified identical to the working live revision. (This is what `aurora-pg` private-IP needs; see [[monorepo-deploy-facts]].)
- **Env / secrets.** 62 env vars incl. `AURORA_RUNTIME`, `DATABASE_URL`, `JWT_SECRET` correctly inherited.
- **Local import.** `cd services/aurora-main-api && python -c "import app.main"` imports **instantly**
  (137 routes) — the stall only manifests **in the Cloud Run container**, not locally.

## Leading hypotheses
1. A module imported by `app.main` does **blocking network I/O at import time** that hangs in the
   container and only releases on a ~min-scale timeout (GCP metadata-server probe, a GCP/Vertex/
   Document AI client constructed at module level, an OIDC/JWKS fetch, etc.). The old `server_files`
   image presumably imports the same code fast — so suspect an import-ORDER or packaging difference.
2. An `aurora_shared` **wheel-vs-in-tree** import difference (the shared layer is now an installed
   wheel, not in-tree) interacting with the above.
3. `gunicorn` runs without `--preload`, so each worker imports independently — amplifies any slow import.

## Repro + root-cause (local, needs Docker — NOT available in the agent env)
```bash
# from repo root
docker build -f services/aurora-main-api/Dockerfile -t m1-stall-test .
# run with prod-like runtime + a throwaway DB; watch where import blocks
docker run --rm -e AURORA_RUNTIME=cloud_run -e DATABASE_URL='postgresql+psycopg://u:p@host/db' \
  -e SKIP_SEED_ADMIN=1 -p 8080:8080 m1-stall-test
```
To pinpoint the blocking import, add temporary instrumentation to `services/aurora-main-api/app/main.py`:
```python
import faulthandler, sys
faulthandler.dump_traceback_later(30, repeat=True, file=sys.stderr)  # dump stacks every 30s
```
Rebuild + run; the dumped traceback will show exactly which import/call is blocked. Alternatively
`PYTHONPROFILEIMPORTTIME=1` (or `python -X importtime`) to log per-module import time.

## Once fixed
Rebuild as a fresh tag (`v0.2.2`), `gcloud run deploy aurora-api --image …:v0.2.2 --no-traffic --tag candidate`
(inherits config + VPC egress), verify the candidate boots clean, then a gated traffic shift. Note:
`api:latest` currently points at the stalling `v0.2.1` — nothing auto-deploys from it (the live
service is pinned to a digest), but don't deploy `:latest`. See [[monorepo-deploy-facts]] for deploy flags.
