"""Aurora LTS — shared backend core.

The import-closed layer used by BOTH backend services:
  • database/  — SQLAlchemy connection + the single canonical models.py (one schema)
  • middleware/ — auth_middleware (JWT / IAP / break-glass / native-session)
  • schemas/    — cross-service DTOs (category_dto)
  • services/   — auth_service, auth_oidc, exec_events, webauthn_service,
                  whatsapp_identity, identity/ (org + membership + pairing + tax_id)

Extracted from server_files/app during the monorepo split (Phase 2B). Imported
as `aurora_shared.*` by aurora-main-api (M1) and aurora-api-core (M2).
"""
