"""
Aurora LTS — Immutability Guards (Sprint 6)
================================================
Application-level guards that reject UPDATE / DELETE on terminal-state
rows, mirroring the Postgres triggers we install in production.

PROTECTED ROWS:
  ItaAuditLog                 : every row is immutable from creation
  ActionLog                   : every row is immutable from creation
  RevenueShareLedger(status='paid')      : immutable once paid
  AccountantPayout(status='paid')        : immutable once paid
  SubscriptionPayment(status='succeeded'): immutable once succeeded

WHY APPLICATION-LEVEL TOO:
  - Dev / local SQLite has no triggers → without these guards, a stray
    db.commit() can rewrite an audit row in dev and pass tests, only
    to fail in production.
  - Mirroring the Postgres trigger logic here lets us catch tampering
    BEFORE the round-trip to the DB.

INSTALLED ONCE AT STARTUP:
  install_immutability_guards() registers the SQLAlchemy event
  listeners. Idempotent — running it twice is a no-op (we track
  installation in a module-global flag).
"""

from sqlalchemy import event
from sqlalchemy.orm.session import Session as SqlaSession


_INSTALLED = False


class ImmutableRowError(Exception):
    """Raised when application-level immutability rejects a write."""


def install_immutability_guards() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    # Lazy imports to avoid circular dep at app boot
    from aurora_shared.database import (
        ItaAuditLog, ActionLog,
        RevenueShareLedger, AccountantPayout, SubscriptionPayment,
    )

    # ── ALWAYS immutable: any UPDATE/DELETE rejected ──
    for cls in (ItaAuditLog, ActionLog):
        event.listen(cls, "before_update",
                     _make_blanket_blocker(cls.__name__, "UPDATE"))
        event.listen(cls, "before_delete",
                     _make_blanket_blocker(cls.__name__, "DELETE"))

    # ── Conditional: immutable once status reaches a terminal value ──
    event.listen(RevenueShareLedger, "before_update",
                 _make_status_blocker(RevenueShareLedger.__name__, "paid"))
    event.listen(RevenueShareLedger, "before_delete",
                 _make_status_blocker(RevenueShareLedger.__name__, "paid", on_delete=True))

    event.listen(AccountantPayout, "before_update",
                 _make_status_blocker(AccountantPayout.__name__, "paid"))
    event.listen(AccountantPayout, "before_delete",
                 _make_status_blocker(AccountantPayout.__name__, "paid", on_delete=True))

    event.listen(SubscriptionPayment, "before_update",
                 _make_status_blocker(SubscriptionPayment.__name__, "succeeded"))
    event.listen(SubscriptionPayment, "before_delete",
                 _make_status_blocker(SubscriptionPayment.__name__, "succeeded", on_delete=True))

    _INSTALLED = True
    print("[COMPLIANCE] ✅ Immutability guards installed on 5 protected models")


# ─────────────────────────────────────────────────────────────
# Internal — blocker factories
# ─────────────────────────────────────────────────────────────
def _make_blanket_blocker(model_name: str, op: str):
    """Reject EVERY update/delete on a model (audit-log style)."""
    def _blocker(mapper, connection, target):
        # Allow when AURORA_AUDIT_ALLOW_OVERRIDE=1 (rare admin operations)
        import os
        if os.getenv("AURORA_AUDIT_ALLOW_OVERRIDE") == "1":
            return
        raise ImmutableRowError(
            f"{model_name} is append-only; {op} is forbidden"
        )
    return _blocker


def _make_status_blocker(model_name: str, terminal_status: str, *, on_delete: bool = False):
    """Reject update/delete iff the row's status is the terminal value."""
    def _blocker(mapper, connection, target):
        current_status = getattr(target, "status", None)
        if current_status == terminal_status:
            import os
            if os.getenv("AURORA_AUDIT_ALLOW_OVERRIDE") == "1":
                return
            op = "DELETE" if on_delete else "UPDATE"
            raise ImmutableRowError(
                f"{model_name} with status={terminal_status!r} is immutable; {op} forbidden"
            )
    return _blocker
