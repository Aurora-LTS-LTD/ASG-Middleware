# Aurora Platform — Monorepo Restructure Blueprint

**Status:** Approved · execution in progress · 2026-05-31
**Branch:** `feature/monorepo-restructure` (off `feature/operational-core-split`)
**Goal:** Turn `ASG-Middleware` into a clean monorepo — a shared backend core layer
(`shared_packages/aurora_shared`), two containerized backend services
(`aurora-main-api` = M1, `aurora-api-core` = M2), and a unified `front-end/` layer —
so work on one surface doesn't step on another.

## Decisions locked
- **aurora-website (marketing site):** stays its OWN repo + Firebase Hosting
  (`~/Desktop/aurora-website`, Next.js static export, site `aurora-marketing`). NOT
  imported here. ASG-Middleware only captures leads via `POST /api/v1/marketing/lead`.
- **ceo-dashboard:** sourced from the LIVE static UI under `server_files/app/static/`
  (option A2 — real decoupling), not the stale root copies. Implementation: bundle-at-build
  (M1 `COPY`s `front-end/ceo-dashboard`), so serving topology / IAP / LB routing is unchanged.

## Target tree
```
ASG-Middleware/
├─ shared_packages/aurora_shared/   # DB (connection + models.py), auth middleware, config, shared schemas
├─ services/
│  ├─ aurora-main-api/  (M1)  app/ + Dockerfile + requirements.txt
│  └─ aurora-api-core/  (M2)  app/ + Dockerfile + requirements.txt (slim)
├─ front-end/
│  ├─ ceo-dashboard/        # live dashboard.html, onboarding.html, accountant/ UI (extracted from backend)
│  └─ accountant-portal/    # gitlink (Tauri Mac shell), relocated from root
├─ legacy/desktop/          # stale pywebview bundle + stale root *.html
└─ docs/, .gitignore, .gcloudignore, .dockerignore, README
# aurora-website stays external (own repo + Firebase)
```

## Phase 1 — Ghost-file purge → `legacy/desktop/`
`git mv` (history-preserving, reversible), gated by `git grep` showing zero live refs:
- `main.py`, `database.py`, `routers/`, `services/`, `desktop_app.py` (the coupled pywebview bundle).
- Stale root `dashboard.html`, `client_portal.html` (Apr-6 copies; live versions are in `server_files/app/static/`).
- Open: archive vs eventual `git rm`.

## Phase 2A — Front-end consolidation (low risk; first)
1. Create `front-end/`.
2. `front-end/ceo-dashboard/` ← `git mv` the front-end ASSETS ONLY from `server_files/app/static/`
   (`dashboard.html`, `onboarding.html`, `accountant/`, CSS/JS). LEAVE runtime dirs
   (`pdfs/`, `kyc_uploads/`, `receipts/`) in the backend (generated storage, AURORA_RUNTIME→/tmp).
3. `front-end/accountant-portal/` ← `git mv accountant-portal front-end/accountant-portal` (gitlink;
   no `.gitmodules`, so `git mv` rewrites the indexed path; update parent refs in `.gcloudignore`/`.dockerignore`/docs).
4. Backend serving (M1 only — `main_core` serves no static): repoint `StaticFiles` mount +
   `/dashboard`,`/onboarding`,`/accountant` `FileResponse` paths; M1 Dockerfile `COPY`s `front-end/ceo-dashboard`.
- Validation: gitlink resolves; `git grep "app/static"`/`"accountant-portal/"` sweep; M1 `/dashboard` still 200s.

## Phase 2B — Backend shared/service extraction
- Step 2.1 (read-only): dependency-boundary audit → SHARED / M1-only / M2-only manifest. **Gating.**
- Then `git mv` shared → `shared_packages/aurora_shared/`, service code → `services/<svc>/app/`.
- `models.py` singular in shared; `migrate_phase*.py` + `create_tables` stay with M1.

## Phase 3 — Import resolution & static validation (2B)
- Rewrite shared refs `app.{database,middleware,config,schemas,<shared svc>}` → `aurora_shared.*`;
  keep service-internal `app.*`.
- Gates: `pip install -e shared_packages/aurora_shared`; per-service `py_compile`; import smoke
  (`import app.main` / `app.main_core`); route-registry assertions.

## Phase 4 — Docker / Cloud Build + ignore-context
- Backend build context = repo root.
- `.gcloudignore` + `.dockerignore`: replace anchored `accountant-portal/` with
  `front-end/accountant-portal/` (exclude the ~1.8 GB Tauri/Rust `target/`); keep `**/node_modules/`,
  `**/target/`, `**/.next/`, `venv/`. Do NOT exclude `front-end/ceo-dashboard/` (M1 bundles it).
- Dockerfiles: `docker build --file=services/<svc>/Dockerfile .`; M1 also `COPY front-end/ceo-dashboard`;
  M2 backend-only. Slim M2 `requirements.txt` from the 2B closure (drop WeasyPrint/DocAI/telegram → faster cold start).

## Phase 5 — Canary / no-traffic handoff
Per backend service: build new tag → `gcloud run deploy --no-traffic --tag candidate` → verify on the
tagged URL → gradual `update-traffic` → rollback = traffic re-point. M2 first (no real users), then M1.
Front-end has no Cloud Run backend impact.

## Sequence
Phase 1 → 2A → 2B → 3 → 4 → 5; one commit per phase; gated by the validations above.

---

## Step 2.1 — Confirmed dependency manifest (AST import-graph over `server_files/app`)
134 modules · M1 closure 112 · M2 closure 24 · SHARED (∩) 15. Includes lazy/in-function imports.
Verified the two surprising findings (see below) before recording.

**SHARED → `shared_packages/aurora_shared/` (15)**
`database` (+`connection`,`models`) · `middleware.auth_middleware` · `schemas.category_dto` ·
`services.auth_oidc` · `services.auth_service` · `services.exec_events` · `services.webauthn_service` ·
`services.whatsapp_identity` · `services.identity` (+`invitation_service`,`organization_service`,`pairing`,`tax_id`)

**M2-only → `services/aurora-api-core/` (9)**
`main_core` · `routers.copilot` · `routers.native_shell` · `services.copilot.{executor,guardrails,pricing_meta}` ·
`services.copilot.{anthropic_client,prompts,tools}` (M2-only once `llm/anthropic_provider.py` is severed — done in Phase 2A)

**M1-only → `services/aurora-main-api/` (~110)**
all 17 routers · all 18 `migrate_phase*` · `config.feature_flags` · `services.{ita,onboarding,billing,
compliance,exports,gcp,llm,receipts,whatsapp_*,telegram_*,invoice_service,payment_service,pdf_service,
vat_coach,exec_aggregator,allocation_queue,smart_reminders,…}` · **`services.autonomous.*`** (see below)

**Verified findings**
- **copilot ↔ M1 was a classifier artifact, not real coupling.** The only M1→copilot edge ran
  `llm/registry.py → llm/anthropic_provider.py → copilot.anthropic_client` (all lazy). No code calls
  `get_provider("anthropic")`. Severing the registry "anthropic" branch (Phase 2A) drops copilot.* out of
  M1's closure. `anthropic_provider.py` is now orphaned → relocates to M2 in Phase 2B.
- **`services.autonomous.*` is NOT dead — keep it (M1).** Pre-armed, dynamically loaded via
  `autonomous/registry.py:get_service()` gated by `config/feature_flags.py` + activation at
  `admin_exec.py:1590`. Deleting it would break the feature-flag activation contract.
- **`services.tax_engine.*` IS dead** — only `tests/test_tax_engine.py` imports it. Archived to
  `legacy/desktop/` in Phase 1b (with its test).
- Package `__init__` shims (`app.config`,`app.middleware`,`app.routers`,`app.schemas`,`app.services.copilot`)
  showed "unreached" only because submodules are imported directly — they travel with their package, not dead.

_Caveat: AST analysis can't see importlib/string imports; the `autonomous`/`tax_engine` dead-or-dynamic
question was the targeted exception, verified by a dedicated reference sweep._
