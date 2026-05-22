"""
Aurora LTS — Phase 17 Migration (Appendix L Sprint 4 — Vertex AI / Gemini)
============================================================================

Three concerns:

  1. claude_api_usage → llm_api_usage RENAME + provider column.
     We do this in-place because the schema is small (a few thousand rows
     at most) and the SQLAlchemy model now points at the new name. The
     rename is idempotent — re-runs skip if already done.

  2. New tables: gemini_runs, daily_brief_cards
     Created by SQLAlchemy create_tables(); this script just probes.

  3. Idempotent ALTER TABLE on `receipts`:
     ADD COLUMN IF NOT EXISTS gemini_classification_json (TEXT, nullable)
     ADD COLUMN IF NOT EXISTS gemini_classified_at (TIMESTAMP, nullable)

  4. Idempotent ALTER TABLE on the renamed `llm_api_usage`:
     ADD COLUMN IF NOT EXISTS provider VARCHAR(32) NOT NULL DEFAULT 'anthropic'
     CREATE INDEX IF NOT EXISTS idx_llm_api_usage_provider_day
       ON llm_api_usage(provider, created_at DESC)

This migration is idempotent. Safe to re-run on every startup.

Rename strategy:
  • If both `claude_api_usage` and `llm_api_usage` exist, we treat it as
    "already migrated" and skip — never attempt to merge (data could
    diverge in unpredictable ways).
  • If only `claude_api_usage` exists, ALTER TABLE RENAME TO llm_api_usage.
  • If only `llm_api_usage` exists (fresh installs post-Sprint-4), no-op.

Cloud SQL ALTER TABLE RENAME is metadata-only on Postgres — atomic
and instant. Existing FKs that reference `claude_api_usage` would need
to be updated, but there are none (the table is a leaf in the dep graph).
"""

import logging

from sqlalchemy import text

from app.database.connection import engine

log = logging.getLogger(__name__)


_EXPECTED_NEW_TABLES = ["gemini_runs", "daily_brief_cards"]


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


def _apply_ddl(conn, label: str, ddl: str) -> tuple[bool, str]:
    """Run a single DDL statement defensively. Returns (ok, message)."""
    try:
        conn.execute(text(ddl))
        conn.commit()
        return (True, "applied")
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        msg = str(e).lower()
        if "already exists" in msg or "duplicate" in msg:
            return (True, "already-exists")
        return (False, str(e)[:200])


def run_phase17_migrations() -> None:
    print("=" * 60)
    print("[MIGRATE_P17] Phase 17 — Vertex AI / Gemini multi-workload (Appendix L)")
    print("=" * 60)

    with engine.connect() as conn:
        # ── 1. RENAME claude_api_usage → llm_api_usage (idempotent) ──
        has_claude = _table_exists(conn, "claude_api_usage")
        has_llm = _table_exists(conn, "llm_api_usage")

        if has_llm and has_claude:
            # Multi-worker race during create_tables() can leave us in this
            # state: the SQLAlchemy model's __tablename__ was bumped to
            # 'llm_api_usage', so create_tables() made an EMPTY new table
            # while the old 'claude_api_usage' still has historical data.
            #
            # Resolution path (idempotent):
            #   (a) If the new table is EMPTY → drop it, then rename the old
            #       table into its place. Preserves historical rows.
            #   (b) If the new table already has data → leave both alone
            #       (do NOT silently merge — that risks losing data).
            #       Operator must manually reconcile.
            try:
                new_count = conn.execute(
                    text("SELECT COUNT(*) FROM llm_api_usage")
                ).scalar() or 0
                old_count = conn.execute(
                    text("SELECT COUNT(*) FROM claude_api_usage")
                ).scalar() or 0
            except Exception as e:
                print(f"[MIGRATE_P17] count probe failed: {e}")
                new_count = -1
                old_count = -1

            print(
                f"[MIGRATE_P17] BOTH tables exist: "
                f"claude_api_usage={old_count} rows, llm_api_usage={new_count} rows"
            )

            if new_count == 0 and old_count > 0:
                print(
                    "[MIGRATE_P17] Recovering from multi-worker race: "
                    "dropping empty llm_api_usage + renaming claude_api_usage"
                )
                ok, msg = _apply_ddl(
                    conn,
                    "drop_empty_llm_api_usage",
                    "DROP TABLE IF EXISTS llm_api_usage CASCADE",
                )
                if ok:
                    ok2, msg2 = _apply_ddl(
                        conn,
                        "rename_claude_to_llm",
                        "ALTER TABLE claude_api_usage RENAME TO llm_api_usage",
                    )
                    print(
                        f"[MIGRATE_P17] {'✅' if ok2 else '❌'} "
                        f"recovery rename ({msg2})"
                    )
                else:
                    print(f"[MIGRATE_P17] ❌ drop failed: {msg}")
            elif new_count > 0 and old_count == 0:
                print(
                    "[MIGRATE_P17] ✅ llm_api_usage is the active table; "
                    "claude_api_usage is empty leftover — dropping it"
                )
                _apply_ddl(
                    conn,
                    "drop_empty_claude_api_usage",
                    "DROP TABLE IF EXISTS claude_api_usage CASCADE",
                )
            else:
                print(
                    "[MIGRATE_P17] ⚠️ Both tables have data — manual "
                    "reconciliation required. Skipping auto-rename."
                )
        elif has_claude and not has_llm:
            print("[MIGRATE_P17] Renaming claude_api_usage → llm_api_usage…")
            ok, msg = _apply_ddl(
                conn,
                "rename_claude_to_llm",
                "ALTER TABLE claude_api_usage RENAME TO llm_api_usage",
            )
            if ok:
                print(f"[MIGRATE_P17] ✅ claude_api_usage → llm_api_usage ({msg})")
            else:
                print(f"[MIGRATE_P17] ❌ rename failed: {msg}")
        elif has_llm:
            print("[MIGRATE_P17] ✅ llm_api_usage already present (rename completed previously)")
        else:
            print(
                "[MIGRATE_P17] ⚠️ Neither claude_api_usage nor llm_api_usage exists — "
                "ensure create_tables() ran first"
            )

        # ── 2. Add `provider` column to llm_api_usage (idempotent) ──
        if _table_exists(conn, "llm_api_usage"):
            ok, msg = _apply_ddl(
                conn,
                "llm_api_usage.provider",
                "ALTER TABLE llm_api_usage ADD COLUMN IF NOT EXISTS "
                "provider VARCHAR(32) NOT NULL DEFAULT 'anthropic'",
            )
            print(f"[MIGRATE_P17] {'✅' if ok else '❌'} llm_api_usage.provider ({msg})")

            ok, msg = _apply_ddl(
                conn,
                "idx_llm_api_usage_provider_day",
                "CREATE INDEX IF NOT EXISTS idx_llm_api_usage_provider_day "
                "ON llm_api_usage(provider, created_at DESC)",
            )
            print(f"[MIGRATE_P17] {'✅' if ok else '❌'} idx_llm_api_usage_provider_day ({msg})")

        # ── 3. Probe new tables (created by create_tables()) ──
        found_new, missing_new = [], []
        for t in _EXPECTED_NEW_TABLES:
            if _table_exists(conn, t):
                found_new.append(t)
            else:
                missing_new.append(t)
        for t in found_new:
            print(f"[MIGRATE_P17] ✅ {t} present")
        for t in missing_new:
            print(f"[MIGRATE_P17] ⚠️ {t} MISSING — ensure create_tables() ran first")

        # ── 4. ALTER TABLE receipts ADD COLUMN gemini_* (idempotent) ──
        if _table_exists(conn, "receipts"):
            for col, ddl in [
                (
                    "gemini_classification_json",
                    "ALTER TABLE receipts ADD COLUMN IF NOT EXISTS "
                    "gemini_classification_json TEXT",
                ),
                (
                    "gemini_classified_at",
                    "ALTER TABLE receipts ADD COLUMN IF NOT EXISTS "
                    "gemini_classified_at TIMESTAMP",
                ),
            ]:
                ok, msg = _apply_ddl(conn, f"receipts.{col}", ddl)
                print(f"[MIGRATE_P17] {'✅' if ok else '❌'} receipts.{col} ({msg})")

    print("-" * 60)
    print("[MIGRATE_P17] Phase 17 complete")
    print("=" * 60)


if __name__ == "__main__":
    run_phase17_migrations()
