# Aurora Platform — Two-Server Split Blueprint

**Status:** Draft for review · read-only audit · 2026-05-31
**Scope:** `~/Desktop/ASG-Middleware/server_files/app/**` (FastAPI backend only — Mac Shell, accountant portal, marketing site out of scope)
**Authoring constraint:** all classifications cite `file:line`. Conflicts between scans were resolved against the brief's explicit cues (PayPlus, payouts, blueprints, Copilot → M2; ITA, uniform file, hashavshevet, invoices → M1).

---

## 0. Executive Findings (read this first)

1. **Three M2 capabilities named in the brief are NOT IMPLEMENTED today.** Repo-wide grep for `anomaly`, `ofac`, `aml_screen`, `bank_reconcil` returns zero production code. Only stray references: `models.py:2573` mentions `"predictive_site_anomaly_v3"` as a placeholder federated-model name in a comment. **Implication:** the split must reserve namespace for these on M2, but there is nothing to migrate today. They are greenfield.
2. **`validate_backend_selectors` does not exist.** Repo-wide grep returns zero hits. Backend selectors (`ITA_BACKEND`, `KYC_BACKEND`, `PAYPLUS_BACKEND`, `OTP_BACKEND`, `GEMINI_BACKEND`) gate at first use, not at startup. There is no monolithic startup gate to remove — the cleanup work in §2.5 is per-call-site, not one-line.
3. **PayPlus IS implemented**, lives in `services/onboarding/payplus_client.py`, and is wired into both `onboarding.py` (tokenize / store card) and `internal.py:281` (trial-end auto-charge cron). Splitting PayPlus to M2 forces M2 to own the entire subscription billing surface (`Subscription`, `PaymentMethod`, `SubscriptionPayment`).
4. **The `accountant.py` and `admin_compliance.py` routers are mixed-bucket.** Their core data (invoices, receipts, COA mappings, exports, audit cursors) is M1; their money endpoints (`/earnings`, `/payouts`, `/referrals`, `/payouts/{id}/approve`, `/mark-paid`) read M2 tables (`RevenueShareLedger`, `AccountantPayout`, `AccountantReferral`). Split at endpoint level — do NOT keep these routers whole on either side.
5. **The Copilot stack is the cleanest extraction in the repo.** Whole subtrees (`services/copilot/*`, `services/llm/*`, `services/autonomous/*`, `schemas/category_dto.py`, the 1200-line Copilot block in `admin_exec.py`, and 12 dedicated tables) can move to M2 without touching M1 code.
6. **The current `Business → Organization` expand/contract migration is incomplete.** Both tables coexist. The split MUST resolve the legacy `Business` path before partitioning — otherwise M1 and M2 will both write to `businesses` and skew.

---

## 1. Migration Manifest — what moves to the M2 Core Server

### 1.1 Routers — full transplant to M2

| Router | Path prefix | Citation | Notes |
|---|---|---|---|
| `admin_exec.py` (large portions) | `/api/v1/admin/exec` | `main.py:48`, `admin_exec.py:50` | See §1.1.1 for per-section split |
| `native_shell.py` | `/api/v1/admin/exec/native` | `main.py:49`, `native_shell.py:81` | Mac Shell hardware-bound CEO cockpit; M2 by design |
| `admin_users.py` | `/api/v1/admin` | `main.py:47`, `admin_users.py:44,113` | Admin users + org list feed the M2 admin UI |
| `admin_break_glass.py` | `/api/v1/admin/break-glass` | `main.py:46`, `admin_break_glass.py:30` | Break-glass is founder-emergency surface for M2 ops; uses `require_admin_iap_strict` |

#### 1.1.1 `admin_exec.py` per-section split (file is 88KB / ~2350 lines)

| Lines | Section | Target | Routes (line) |
|---|---|---|---|
| 54–89 | Dashboard summary (Mission Control) | **M1** (KPIs aggregate tax/compliance state) | `GET /dashboard-summary` (56) |
| 91–101 | Finance summary | **M1** | `GET /finance-summary` (93) |
| 102–121 | WhatsApp analytics | **M1** | `GET /whatsapp-analytics` (107) |
| 122–258 | Vertical templates CRUD | **M2** (industry blueprints) | `GET /templates` (176), `POST /templates` (199), `PATCH /templates/{id}` (230) |
| 259–311 | Exec events stream | **BOUNDARY** — see §3.2 | `GET /events` (270), `POST /events` (286) |
| 312–736 | Categories + Branch Explorer | **M2** (business taxonomy is Copilot-managed blueprint material) | `GET/POST/PATCH/DELETE /categories*` (394,439,498,555,618,653,695) |
| 737–835 | Palette index | **BOUNDARY** — reads invoices (M1) + categories (M2). Move to M2; fetch invoice slice via the bridge | `GET /palette-index` (737) |
| 836–1442 | Copilot (Anthropic) | **M2** | `POST /copilot/conversations` (905), `GET /copilot/conversations` (928), `GET /copilot/conversations/{id}` (959), `POST /copilot/chat` (1013), `POST /copilot/approve` (1312) |
| 1447–1643 | WebAuthn step-up | **M2** | `POST /webauthn/register/start` (1472), `/register/finish` (1484), `/assert/start` (1519), `/assert/finish` (1533), `GET /webauthn/credentials` (1567), `GET /webauthn/preflight` (1605) |
| 1645–1689 | Copilot budget guardrails | **M2** | `GET /copilot/usage` (1655), `POST /copilot/budget-extend` (1665) |
| 1692–1891 | Vertex / Gemini | **M2** | `POST /receipts/{id}/classify-with-gemini` (1721) |
| 1894–1974 | Cross-provider LLM usage | **M2** | `GET /llm/usage` (1901) |
| 1977–2019 | Gemini run feed | **M2** | `GET /gemini/runs` (1981) |
| 2022–2347 | Growth & Milestone Activation | **M2** (autonomous-feature flag plane) | `GET /growth/summary` (2114), `GET /growth/milestones` (2123), `POST /growth/activate/{feature}` (2216) |

**Net result for `admin_exec.py`:** ~3 M1 endpoints (dashboard/finance/whatsapp summaries) stay on the Transparent Server; everything else moves. Recommend rebuilding the M1 summary endpoints as a thin new router rather than carving up the 88KB file.

#### 1.1.2 Mixed routers — extract M2 slice

| Router | M2 routes to extract | M1 routes that stay |
|---|---|---|
| `accountant.py` | `GET /earnings` (483), `GET /payouts` (570), `GET /referrals` (608) — these query `RevenueShareLedger`/`AccountantPayout`/`AccountantReferral` | `GET /book` (143), `GET /orgs/{id}/summary` (210), `POST /orgs/{id}/exports` (334), `GET /orgs/{id}/exports` (372), `GET /exports/{id}` (395), `GET/PUT /coa-mappings` (413,443) |
| `admin_compliance.py` | `POST /payouts/{id}/approve` (226), `POST /payouts/{id}/mark-paid` (243) | `GET /health` (67), `GET /dsar/{user_id}` (104), `POST /dsar-erase/{user_id}` (129), `POST /audit-export` (181), `GET /audit-cursor` (195) |
| `internal.py` | `POST /charge-trial-ends` (216), `POST /smart-reminders` (356), `POST /prune-exec-events` (398), `POST /eod-brief` (434) | `POST /close-month` (152), `POST /expire-invitations` (197), `POST /audit-export` (378) |
| `onboarding.py` | `POST /billing/payment-method` (603) — calls PayPlus tokenize; `POST /billing/plan` (572) — writes Subscription | everything else (identity, OTP, KYC stub upload, review/activate/abandon) stays on M1 |

#### 1.1.3 Boundary cases (routers)

- **`marketing.py`** (`main.py:45`, `marketing.py:180`) — public lead capture. Endpoint is anonymous, hits no tax data, but feeds onboarding which is M1. **Decision: M1.** Forms the public funnel into the Transparent Server.
- **`organizations.py`** — exposes `Organization`, `Membership`, `Invitation`, `/me/context`, and `/utils/validate-tax-id`. `Organization` is the shared-identity table. **Decision: keep router on M1; M2 reaches into the shared identity DB read-only.** See §3 for the contract.
- **`auth.py`** — login/register/me. Shared by both. **Decision: keep on M1, M2 validates the same JWT via shared `JWT_SECRET`.** See §3.

### 1.2 Services — full transplant to M2

| Path | Citation | Notes |
|---|---|---|
| `services/copilot/` (entire dir, 6 files) | `services/copilot/anthropic_client.py:62` `stream_chat`, `executor.py:424` `execute_approved_tool`, `guardrails.py:154` `check_chat_guardrails`, `tools.py` ANTHROPIC_TOOLS | Anthropic SDK + tool execution + budget. Zero M1 dependencies. |
| `services/llm/` (entire dir, 5 files) | `llm/anthropic_provider.py:35`, `llm/vertex_provider.py:1`, `llm/pricing.py`, `llm/registry.py`, `llm/base.py` | Provider-neutral LLM façade; only Copilot + autonomous services consume it. |
| `services/autonomous/` (entire dir, 6 files) | `autonomous/base.py:1` `AbstractAutonomousService`, `hcarl_orchestrator.py:42`, `causal_insights.py:36`, `predictive_site.py:36`, `federated_sync.py`, `registry.py` | All four pre-armed services + the feature-flag-gated base class. Skeleton-only today. |
| `services/billing/` (entire dir, 3 files) | `billing/payout_service.py:33` `approve_payout`, `billing/revenue_share.py` `accrue_on_charge_success` / `close_month`, `billing/referrals.py` | Accountant rev-share money flow. Per brief: premium core. |
| `services/exec_aggregator.py` | top-level | CEO dashboard fan-in. Aggregates M1 data — receives via the bridge (§3). |
| `services/exec_events.py` | top-level | Writes `ExecEvent`. See §3.2 — keep one publisher on each server, both write to shared `exec_events` table or M2 owns the table and M1 publishes via webhook. |
| `services/webauthn_service.py` | top-level | Step-up for Copilot approve + payout approve. M2-driven; M1 never calls it. |
| `services/onboarding/payplus_client.py` | `services/onboarding/payplus_client.py:48` `payplus_tokenize`, `:116` `payplus_charge` | PayPlus tokenization + charge. Move out of `onboarding/` into M2 billing — see §1.2.1 |
| `services/onboarding/subscription_service.py` | — | Plan + trial state. Couples to `Subscription`/`PaymentMethod`/`SubscriptionPayment` tables which the brief assigns to M2 (PayPlus money flow). |
| `schemas/category_dto.py` | `schemas/category_dto.py:1–208` | Only used by Copilot tool definitions + `/categories` CRUD — both M2. |
| `config/feature_flags.py` | `feature_flags.py:61` `AutonomousFeature`, `:111` `MILESTONE_THRESHOLDS`, `:195` `is_feature_active` | Gates the four autonomous services; pure M2. |

#### 1.2.1 Service split note — `services/onboarding/`

Current package mixes M1 (KYC, OTP, basic onboarding wizard) with M2 (PayPlus payment-method tokenization, subscription plan creation).

- **Stay M1:** `onboarding/__init__.py` (after pruning), `onboarding/kyc_service.py`, `onboarding/otp_service.py`, `onboarding/onboarding_service.py`. KYC docs are ITA evidence; OTP is the universal verification path.
- **Move M2:** `onboarding/payplus_client.py`, `onboarding/subscription_service.py`. On M2, repackage as `services/billing/payplus_client.py` (or under a new `services/payments/` namespace) so the package name reflects its role.

### 1.3 Database tables — full transplant to M2

Citations are all in `app/database/models.py`.

| Table | `__tablename__` | Class line | Why M2 |
|---|---|---|---|
| VerticalTemplate | `vertical_templates` | 1814 | Industry blueprints |
| BusinessCategory | `business_categories` | 1911 | Two-level taxonomy managed by Copilot |
| CopilotConversation | `copilot_conversations` | 2031 | Copilot session |
| CopilotMessage | `copilot_messages` | 2068 | Copilot transcript |
| CopilotProvisioningRun | `copilot_provisioning_runs` | 2116 | Copilot WRITE-tool execution log |
| ClaudeApiUsage (renamed table) | `llm_api_usage` | 2175 | Cross-provider token accounting |
| GeminiRun | `gemini_runs` | 2225 | Vertex AI one-shot calls |
| DailyBriefCard | `daily_brief_cards` | 2286 | Gemini-narrated CEO brief |
| ProjectConstraint | `project_constraints` | 2352 | H-CARL hard constraints |
| HcarlPolicyState | `hcarl_policy_states` | 2418 | H-CARL training tuples |
| CausalInsight | `causal_insights` | 2492 | Causal explainability DAG |
| FederatedSyncLog | `federated_sync_logs` | 2563 | FL training audit |
| GrowthMilestone | `growth_milestones` | 2626 | Autonomous-feature unlock gate |
| WebauthnCredential | `webauthn_credentials` | 1985 | Step-up passkeys (Copilot approve / payout approve) |
| CeoSessionSnapshot | `ceo_session_snapshots` | 1958 | Mission Control "what changed" diff |
| ExecEvent | `exec_events` | 1862 | See §3.2 boundary discussion |
| NativeDeviceKey | `native_device_keys` | 2683 | Mac Shell device registry |
| NativeHandshakeChallenge | `native_handshake_challenges` | 2766 | Mac Shell ephemeral challenge |
| BreakGlassToken | `break_glass_tokens` | 1764 | Founder emergency JWT (lives where the admin UI lives → M2) |
| Subscription | `subscriptions` | 892 | PayPlus money flow (per brief) |
| PaymentMethod | `payment_methods` | 946 | PayPlus tokenized instrument |
| SubscriptionPayment | `subscription_payments` | 994 | PayPlus charge ledger |
| RevenueShareLedger | `revenue_share_ledger` | 1364 | Accountant commission accrual |
| AccountantPayout | `accountant_payouts` | 1409 | Founder-approved payouts |
| AccountantReferral | `accountant_referrals` | 1447 | Acquisition attribution |

**24 tables.** Plus three reserved namespaces with no current implementation: `anomaly_*`, `ofac_*` / `aml_*` / `sanctions_*`, `bank_reconciliation_*`.

### 1.4 Migrations to run on M2 only

(From `main.py:184–360`; phase files at `server_files/app/migrate_phase*.py`)

| Phase | Title | M2 reason |
|---|---|---|
| 6 | Identity Foundation | Organizations + memberships — but shared (§3.1); see Boundary |
| 6b | Aurora Onboarding | KYC/Subscription/PaymentMethod — split: KYC stays M1, billing rows go M2. Phase needs to be carved. |
| 14 | Vertical Templates + ExecEvents | M2 (templates); ExecEvent see §3.2 |
| 15 | Categories + WebAuthn + CEO snapshots | M2 (plus the invoice GCS-retention column subset that stays M1) |
| 16 | AI Copilot Console | M2 |
| 17 | Vertex AI / Gemini + rename `claude_api_usage` → `llm_api_usage` | M2 |
| 18 | Pre-Armed Autonomous | M2 |

### 1.5 Environment variables for M2 only

From `.env` inventory (names only — no values shown anywhere):

- `ANTHROPIC_API_KEY` — Copilot. `services/copilot/anthropic_client.py:46`.
- `VERTEX_PROJECT`, `VERTEX_LOCATION`, `VERTEX_DEFAULT_MODEL`, `VERTEX_DEFAULT_FAST_MODEL` — Vertex AI. `services/llm/vertex_provider.py:44,48,69,70`.
- `GEMINI_BACKEND`, `GEMINI_DAILY_BUDGET_CENTS`, `GEMINI_MAX_INPUT_CHARS` — Vertex/Gemini wrapper. `services/gcp/gemini.py:26,106`.
- `PAYPLUS_BACKEND`, `PAYPLUS_API_KEY`, `PAYPLUS_TERMINAL_NUMBER`, `PAYPLUS_API_BASE` — billing.
- `KYC_BACKEND`, `GCS_BUCKET_KYC`, `GCS_KYC_SA_KEY_JSON` — *but* see §2.4: KYC is M1, so these stay M1 unless KYC moves.
- `OTP_BACKEND`, `INFORU_API_KEY`, `INFORU_SENDER_ID`, `TWILIO_*`, `WHATSAPP_OTP_TEMPLATE_NAME` — OTP delivery is shared (used by both onboarding M1 and accountant-portal M1); these stay M1.
- `MIN_ORGS_FOR_HCARL`, `MIN_INVOICES_FOR_PREDICTIVE_SITE`, `MIN_DATA_POINTS_FOR_CAUSAL`, `MIN_ACTIVE_ORGS_FOR_FL` — autonomous-feature thresholds. `config/feature_flags.py:111–172`.
- `AURORA_AUTONOMOUS_KILL_SWITCH` — kill switch. `feature_flags.py:247`.

---

## 2. Legacy Cleanup — what stays on M1 and what to prune

### 2.1 Routers that stay whole on M1

| Router | Citation | Notes |
|---|---|---|
| `invoices.py` | `main.py:34`, `invoices.py:135–365` | ITA allocation, VAT, invoice lifecycle — pure M1 |
| `receipts.py` | `main.py:41`, `receipts.py:215–502` | OCR pipeline, review queue — pure M1 |
| `payments.py` | `main.py:36`, `payments.py:78–230` | Invoice payment tracking (NOT subscriptions) |
| `pdf.py` | `main.py:37`, `pdf.py:79–151` | Invoice PDF gen + uniform-file fodder |
| `telegram.py` | `main.py:38`, `telegram.py:58–201` | Telegram ingestion channel |
| `whatsapp.py` | `main.py:33`, `whatsapp.py:278–545` | WhatsApp ingestion channel |
| `auth.py` | `main.py:35`, `auth.py:86–237` | Login/register/me (shared JWT — §3.1) |
| `organizations.py` | `main.py:39`, `organizations.py:162–386` | Shared identity owner (see §3.1) |
| `accountant_auth.py` | `main.py:50`, `accountant_auth.py:322,393–955` | Accountant portal OTP + device mgmt |
| `marketing.py` | `main.py:45`, `marketing.py:161–180` | Public lead capture |
| `accountant.py` (M1 routes) | see §1.1.2 | Book, COA mappings, exports |
| `admin_compliance.py` (M1 routes) | see §1.1.2 | Health, DSAR, audit-export, audit-cursor |
| `internal.py` (M1 routes) | see §1.1.2 | close-month, expire-invitations, audit-export |
| `onboarding.py` (M1 routes) | see §1.1.2 | Identity, OTP, KYC, review, activate |

### 2.2 Services that stay on M1

| Path | Citation | Role |
|---|---|---|
| `services/invoice_service.py` | top-level | Invoice finalize → tax calc → ITA allocation → PDF → audit |
| `services/payment_service.py` | top-level | Invoice payment recording (NOT subscription billing) |
| `services/ita_mock_service.py` + `services/ita/` | `services/ita/client.py:1` `request_allocation_number` | ITA backend |
| `services/allocation_queue.py` | top-level | Retry queue for failed allocations |
| `services/tax_compliance.py` | top-level | Invoice threshold + VAT dispatch |
| `services/tax_engine/` | `constants.py`, `calculator.py`, `brackets.py` | Israeli VAT/tax constants |
| `services/exports/` | `exports/uniform_file.py`, `hashavshevet.py`, `service.py` | Uniform file + Hashavshevet exporters |
| `services/receipts/` | `receipts/pipeline.py:1` `process_receipt`, `confidence.py` | OCR pipeline + confidence router |
| `services/vat_coach.py` | top-level | Heuristic VAT advice |
| `services/whatsapp_engine.py` | top-level | WhatsApp invoice FSM (65KB) |
| `services/whatsapp_meta_client.py`, `whatsapp_sender.py`, `whatsapp_resend.py`, `whatsapp_identity.py`, `whatsapp_analytics.py`, `whatsapp_strings.py` | top-level | Meta integration + outbound layer |
| `services/telegram_bot.py` | top-level | Telegram FSM (57KB) |
| `services/telegram_identity.py` | top-level | Telegram pairing |
| `services/onboarding/kyc_service.py`, `otp_service.py`, `onboarding_service.py` | — | Onboarding (excluding PayPlus + subscription — see §1.2.1) |
| `services/compliance/bigquery_export.py` | `compliance/bigquery_export.py:50` `export_audit_to_bigquery` | ITA-evidence BigQuery export |
| `services/compliance/dsar.py` | `compliance/dsar.py:47` `build_dsar_bundle` | Privacy law / GDPR DSAR |
| `services/gcp/document_ai.py` | `gcp/document_ai.py:1` `parse_expense` | Receipt OCR |
| `services/identity/tax_id.py` | `identity/tax_id.py` `validate_tax_id_israel` | Israeli ת.ז./ח.פ./מע"מ |
| `services/reminder_service.py`, `smart_reminders.py` | — | Reminders are heuristic today (M1); flag if AI-driven later |

### 2.3 Tables that stay on M1

(All citations in `app/database/models.py`.)

| Table | Line | Notes |
|---|---|---|
| Business | 52 | Legacy entity — see §2.6 cleanup |
| Invoice | 108 | ITA allocation, VAT, GCS archival |
| Payment | 315 | Invoice payment records |
| Receipt | 1048 | OCR receipt evidence |
| Expense | 1142 | Hashavshevet source rows |
| TelegramSession | 362 | Telegram bot FSM state |
| WhatsAppSession | 410 | WhatsApp bot FSM state |
| WhatsAppOutboundLog | 460 | Outbound audit (ITA evidence) |
| ActionLog | 196 | System-wide tax audit log |
| OnboardingState | 751 | Multi-step onboarding state |
| OtpVerification | 794 | Onboarding OTP challenges |
| KycDocument | 839 | KYC documents (tax-side evidence) |
| ItaAuditLog | 1216 | Per-request ITA call evidence |
| Export | 1267 | Uniform file / Hashavshevet exports |
| AccountantCoaMapping | 1311 | Accountant COA translation |
| MarketingLead | 1506 | Waitlist captures |
| TaxObligation | 1554 | Virtual tax shield projections |
| VirtualLedger | 1611 | Append-only tax-state ledger |
| VirtualBalance | 1650 | Denormalized snapshot |
| RemittanceLink | 1676 | gov.il payment portal links |
| PaymentConfirmation | 1714 | User payment confirmation |
| AuditExportCursor | 1479 | BigQuery export ETL cursor |
| AccountantDevice | 2828 | Accountant portal device |
| AccountantRefreshToken | 2887 | Accountant portal refresh |
| AccountantOtpAttempt | 2933 | Accountant portal OTP |

### 2.4 Migrations to run on M1 only

Phases **7, 8, 9, 11, 12, 21**, plus the M1 carve-outs of phases **6b, 15, 17** (see §1.4).

### 2.5 Startup gates — what to refactor (not "remove"; the brief's `validate_backend_selectors` does not exist)

Repo-wide grep for `validate_backend_selectors` returns zero hits. There is no monolithic startup-time gate to delete. The migration-phase try/except chain in `main.py:184–360` is the only real "gate", and it is non-fatal (warns and continues). Per-server cleanup:

- **`main.py:184–360`** — the 18 migrate_phase calls. Once split, M1's `main.py` should only import phases 4, 5, 7, 8, 9, 11, 12, 13, 19, 20, 21 (plus the M1 slices of 6b, 15, 17). M2's `main.py` should only import 4, 5, 6, 6b, 13, 14, 15, 16, 17, 18, 19, 20.
- **`main.py:419–448`** — Telegram bot init. M1 owns the Telegram channel (per `routers/telegram.py`), so this stays on M1.
- **`main.py:362–370`** — WhatsApp resend worker. M1 (channel lives there).
- **Backend selector resolution sites:**
  - `services/ita/client.py:1` reads `ITA_BACKEND` — M1 keeps.
  - `services/gcp/gemini.py:26,106` reads `GEMINI_BACKEND` — both servers use Gemini, but for different workloads. M1 needs it for receipt classify; M2 needs it for the general LLM. Each side gets its own copy + budget cap.
  - `services/onboarding/payplus_client.py:42` reads `PAYPLUS_API_BASE` — moves to M2.
  - `services/onboarding/otp_service.py` (implicit) reads `OTP_BACKEND` / `INFORU_*` / `TWILIO_*` — M1 keeps (onboarding lives there).
  - `services/onboarding/kyc_service.py` reads `KYC_BACKEND`, `GCS_BUCKET_KYC` — M1 keeps.

### 2.6 Schema cleanups that need to happen before the split

1. **`Business` → `Organization` expand/contract is unfinished** (`main.py:546–596` shows the dual-write). Both tables still receive new rows. Before the split, decide: keep `Business` as M1 legacy (and have M2 read `Organization` only) OR collapse to `Organization` only. The current dual-write makes the partition ambiguous.
2. **`payment_methods.provider` enum** (`models.py:956`) accepts `'payplus' | 'tranzila' | ...`. If PayPlus moves to M2, the column moves with the table.
3. **`receipts.gemini_classification_json` + `receipts.gemini_classified_at`** columns (added Phase 17) — Receipt table is M1, but Gemini classification is M2. Either keep the columns on M1 and have M2 write them via the bridge, or denormalize a Gemini-classification table to M2. Decision deferred to §3.

---

## 3. Synchronization Contract — the Bridge

**Goal:** keep the two servers eventually consistent on shared state, deny direct cross-server DB access (no shared connection strings), and make sensitive flows (signing, payouts, financial events) audit-grade.

### 3.1 Shared identity (the unavoidable cross-cutting concern)

The following tables are read by both servers and must live in exactly one place with a strict read/write contract.

| Table | Owner | Other side's access | Reasoning |
|---|---|---|---|
| `User` (`models.py:228`) | **M1** (login/auth lives there) | M2 reads via signed JWT — never reads the row directly | Auth JWT is the source of truth at the request edge |
| `Organization` (`models.py:509`) | **M1** (created during onboarding) | M2 reads via bridge | Both sides need org metadata; M1 is the writer because onboarding lives there |
| `Membership` (`models.py:597`) | **M1** | M2 reads via JWT-embedded claim | Role check happens at the gateway, not at the DB |
| `AccountantEngagement` (`models.py:646`) | **M1** | M2 reads to compute rev-share | M1 owns the SMB↔accountant link |
| `Invitation` (`models.py:703`) | **M1** | M2 receives "invitation accepted" event | Onboarding-side concern |

**Implementation note:** keep these tables in the M1 database. M2 calls a thin `GET /api/v1/internal/org/{id}` and `GET /api/v1/internal/me/context` over the bridge to hydrate. JWTs include `org_id`, `role`, and `accountant_engagement_id` claims so per-request authorization can stay local without a round-trip.

### 3.2 `ExecEvent` — boundary table

`ExecEvent` (`models.py:1862`) is published by both M1 services (template/category/webauthn lifecycle — see `admin_exec.py:218,246,486,496,540,596,641,681,724,1501,1550`) and M2 services (Copilot, autonomous activation — `admin_exec.py:2320`).

**Recommended split:** M2 owns the table (the consumer is the CEO dashboard, which is M2). M1 publishes via the bridge — fire-and-forget POST to `/internal/exec-events` with HMAC-signed body. Loss of one event is acceptable (these are UI breadcrumbs, not durable audit). M1's local audit-of-record stays in `ActionLog`.

### 3.3 The financial-bridge contract — M2 → M1 invoice signing

The hot path: M2 (Copilot, autonomous workflows, premium money flow) needs M1 to produce an ITA-signed invoice. Proposed contract:

```
POST  https://m1.api-aurora-lts.com/api/v1/internal/invoice/draft
  Auth:   workload identity (Google OIDC RS256 service account JWT,
          allowlisted via AURORA_ADMIN_IAP_ALLOWLIST — same path
          auth_middleware.py:103-179,206-212,356-375 already supports)
  Headers:
    X-Aurora-Internal: 1                # existing internal-cron header
    X-Aurora-Idempotency-Key: <uuid>    # caller-generated, M1 dedupes
    X-Aurora-Signature: <hmac-sha256>   # body HMAC over shared secret
  Body: { organization_id, line_items, customer_ref, ... }
  Returns: { invoice_id, allocation_number, pdf_url, status }
```

**Why workload OIDC, not mTLS or scoped JWTs:**
1. `auth_middleware.py:103-179,287-315` already verifies Google RS256 OIDC tokens from allowlisted service accounts. Zero new auth code on M1.
2. Cloud Run → Cloud Run intra-project calls get OIDC tokens for free (the standard pattern).
3. mTLS adds cert-rotation toil for two services co-located in the same project; the security gain is marginal vs. workload identity + IP allowlist + Cloud Armor.
4. A custom "scoped JWT service token" reinvents OIDC poorly.

**HMAC body signature is additive defense-in-depth** — protects against a compromised OIDC token replaying the wrong body. Secret rotates with `JWT_SECRET`.

### 3.4 Async event bus — for non-critical fan-out

For events where eventual consistency is acceptable (subscription payment succeeded → trigger M1 invoice generation; KYC approved → unlock M2 features; Gemini classification finished → write back to `receipts.gemini_classification_json`):

**Recommendation: Cloud Pub/Sub topics, one per direction.**

- `aurora-m2-to-m1` — M2 publishes (subscription charged, payout approved, blueprint provisioned)
- `aurora-m1-to-m2` — M1 publishes (invoice finalized, receipt OCR'd, KYC approved, exec event)

Both servers subscribe with workload-identity authentication. Idempotency via message attribute `aurora-event-id`. Dead-letter topic for retries.

**Why Pub/Sub over webhooks-only:** retries, ordering keys per org, dead-lettering, and audit retention come built-in. Webhooks would be cheaper but force us to re-implement these.

### 3.5 Forbidden patterns

1. **No shared `DATABASE_URL`.** Each server owns its DB. The bridge crosses HTTP/Pub-Sub, never raw SQL.
2. **No cross-server `Depends(get_db)` import.** If you see a router on M2 importing `from app.database import Invoice`, that's a leak.
3. **No re-use of `JWT_SIGNING_KEY` for cross-server auth.** That key signs native-session JWTs minted for the Mac Shell. Workload OIDC is a different key.
4. **No moving `ActionLog`.** It is the M1 ITA-binder evidence. M2 maintains its own audit trail (`CopilotProvisioningRun`, `RevenueShareLedger.created_at`, etc.).

---

## 4. Boundary cases — flagged for explicit decision

### 4.1 Identity & access (already discussed §3.1)
Resolved: M1 owns; M2 reads via bridge + JWT claims.

### 4.2 `ExecEvent`
Resolved §3.2: M2 owns the table, M1 publishes via internal HTTP.

### 4.3 `receipts.gemini_classification_json` / `gemini_classified_at`
The Receipt row is M1 (OCR pipeline, ITA evidence). The Gemini classification is M2. Two options:
- **(A)** Add a new M2-owned `receipt_gemini_classifications` table keyed by receipt_id. M1 stays clean. M2 reads receipt slice via bridge. Recommend.
- **(B)** Keep columns on M1; M2 writes them via `PATCH /internal/receipts/{id}/classification`. Simpler, but couples M1 schema to an M2 capability.

### 4.4 `BreakGlassToken`
Tokens unlock `require_admin` everywhere (`auth_middleware.py:377-439`). If M1 and M2 each enforce `require_admin`, they must both validate against this table. **Decision: M2 owns the table; M1 calls `/internal/break-glass/validate/{jti}` from `auth_middleware.py:383` on every break-glass-claim'd request.** Cost: one extra hop on a very rare path. Benefit: single source of truth.

### 4.5 `WebauthnCredential`
Used to gate Copilot `approve` and payout `approve`. Both are M2 surfaces, so credential storage moves to M2 cleanly. No M1 dependency.

### 4.6 `auth_middleware.py` itself
Both servers need this module verbatim. It must become a small shared library (Python package or copied file with a version pin), or each server gets its own copy that diverges over time (worse). **Recommend: extract to a thin `aurora_auth` package versioned independently, installed via private PyPI or git submodule.**

### 4.7 `services/gcp/` (storage, secrets, dlp)
- `storage.py` — both servers read/write GCS. Shared.
- `secrets.py` — both call Secret Manager. Shared.
- `dlp.py` — only `receipts/pipeline.py` calls it today. Stays M1.
- `document_ai.py` — only `receipts/pipeline.py` calls it. Stays M1.
- `gemini.py` — called by both. Each side gets its own daily budget cap.

### 4.8 `services/make_service.py`
Referenced from `.env` as `MAKE_WEBHOOK_URL`. Search for call sites returns no hits in services or routers. Likely vestigial. Verify before partitioning — if dead code, drop entirely; otherwise classify per call site.

### 4.9 `accountant_*` portal auth (`AccountantDevice`, `AccountantRefreshToken`, `AccountantOtpAttempt`)
The accountant uses the portal for tax-side actions (book, exports, COA). Stays M1. If the portal later exposes Copilot to accountants, M2 calls the bridge to validate the device — same pattern as §3.3.

### 4.10 `Subscription` / `PaymentMethod` / `SubscriptionPayment` placement
The brief says PayPlus → M2. Subscriptions are the PayPlus money flow, so they move with PayPlus. But subscriptions also trigger M1 invoice generation (`services/billing/revenue_share.py` references `SubscriptionPayment` to accrue commissions; an ₪0 trial invoice is explicitly *not* generated per ITA rules — see `models.py:994` comments). The bridge contract in §3.3 covers this: M2 emits `subscription.payment_succeeded` to Pub/Sub, M1 generates the tax invoice for it.

---

## 5. Greenfield M2 capabilities — explicitly absent today

The brief lists these as M2 owners. Repo audit confirms **none are implemented**:

| Capability | Current state | Recommended placeholder |
|---|---|---|
| Anomaly detection | Not present. Only string literal `"predictive_site_anomaly_v3"` in `models.py:2573` as a future federated-model name. | Reserve namespace `services/anomaly/` and tables `anomaly_signals`, `anomaly_resolutions` on M2. |
| Bank reconciliation | Not present. Zero grep hits. | Reserve `services/bank_reconciliation/` and table `bank_transaction_matches` on M2. |
| AML / Sanctions (OFAC, IL-MOF) | Not present. Zero grep hits. | Reserve `services/screening/` and tables `sanctions_screening_runs`, `sanctions_hits` on M2. Note compliance overlap with M1 — see below. |

**Compliance overlap note:** AML/Sanctions screening typically lives in tax-evidence territory (Israeli MOF is the same ministry as ITA). The brief assigns it to M2 because it is "premium core." Resolve before building: if Aurora needs to surface sanctions hits in the ITA evidence pack, the hits must either replicate to M1 or live in M1 with M2 calling in. Defer until product spec exists.

---

## 6. Recommended execution sequence (advisory; not part of the audit)

1. Finish the `Business → Organization` collapse before partitioning — otherwise the legacy dual-write splits across two servers and skews.
2. Extract `auth_middleware.py` to a shared package (§4.6) so both servers can adopt without divergence.
3. Stand up the M2 service with empty routers + the autonomous service skeletons + Copilot. Run it in shadow mode behind the same IAP gate.
4. Move the M2-only tables to a new database. Backfill from M1 with a one-shot ETL.
5. Cut over Copilot + admin_exec M2 routes to the new server. M1 keeps everything else.
6. Migrate `services/billing/*` + PayPlus + subscription tables to M2. Wire the Pub/Sub bridge for `subscription.payment_succeeded` → M1 invoice generation.
7. Carve `accountant.py` and `admin_compliance.py` at the endpoint level. Move the payout/earnings/referral routes to M2.
8. Decommission the M2-side imports from M1's `main.py` once the bridge is steady.

---

## 7. Open questions for the founder

1. **Subscription invoices** — does an ITA invoice need to be generated for every `SubscriptionPayment.status='succeeded'`, or only above the ₪5,000 threshold? Affects whether the bridge is hot-path or async.
2. **AML/Sanctions placement** — is this a customer-facing compliance feature (then M1) or a premium-tier risk product (then M2)? Currently ambiguous.
3. **`make_service.py` status** — alive or dead? Affects §4.8.
4. **`receipts.gemini_classification_json` placement** — Option A (new M2 table) or Option B (M1 column, M2 writes via bridge)? Affects §4.3.
5. **Mac Shell ownership** — `native_shell.py` is M2 because the Mac Shell is the founder's CEO cockpit, but if accountants also get a native app later it may need to span both servers. Defer.

---

*End of blueprint.*
