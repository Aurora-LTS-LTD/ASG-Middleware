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
