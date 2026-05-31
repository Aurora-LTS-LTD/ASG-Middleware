"""
Aurora LTS — Phase 16 Migration (Appendix J Sprint 3 — AI Copilot Console)
===========================================================================

Probes the four new tables that back the AI Copilot Console:

  1. copilot_conversations       — One row per chat thread
  2. copilot_messages            — Full Anthropic-style transcript
  3. copilot_provisioning_runs   — What blueprints actually executed
  4. claude_api_usage            — Token + cost tracking for guardrails

All tables are created by SQLAlchemy `create_tables()` from the models
in app/database/models.py. This migration's job is to probe + log.

Idempotent. Safe to re-run on every startup.

Sprint 3 ships in tandem with WebAuthn (Phase 15 webauthn_credentials
already present). Approving Copilot tool calls requires step-up; that
contract is enforced at the router layer (require_step_up dep) not
here.
"""

from sqlalchemy import text

from aurora_shared.database.connection import engine


_EXPECTED = [
    "copilot_conversations",
    "copilot_messages",
    "copilot_provisioning_runs",
    "claude_api_usage",
]


def _table_exists(conn, table_name: str) -> bool:
    try:
        conn.execute(text(f"SELECT 1 FROM {table_name} LIMIT 1"))
        conn.commit()
        return True
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return False


def run_phase16_migrations() -> None:
    print("=" * 60)
    print("[MIGRATE_P16] Phase 16 — AI Copilot Console (Appendix J)")
    print("=" * 60)

    found, missing = [], []

    with engine.connect() as conn:
        for t in _EXPECTED:
            if _table_exists(conn, t):
                found.append(t)
            else:
                missing.append(t)

    for t in found:
        print(f"[MIGRATE_P16] ✅ {t} present")
    for t in missing:
        print(f"[MIGRATE_P16] ⚠️ {t} MISSING — ensure create_tables() ran first")

    print("-" * 60)
    print(f"[MIGRATE_P16] Tables: {len(found)} present, {len(missing)} missing")
    print("=" * 60)


if __name__ == "__main__":
    run_phase16_migrations()
