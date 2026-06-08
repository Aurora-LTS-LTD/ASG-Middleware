"""
Aurora LTS — Immutability guards test.

Verifies the append-only SQLAlchemy guards actually fire: ActionLog + ItaAuditLog
reject UPDATE/DELETE after insert, the status-based blocker rejects only at the
terminal status, and AURORA_AUDIT_ALLOW_OVERRIDE=1 bypasses (rare admin path).

USAGE: python tests/test_immutability_guards.py
"""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.pop("AURORA_AUDIT_ALLOW_OVERRIDE", None)
os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("SECRET_BACKEND", "env")

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from aurora_shared.database.models import Base, ActionLog, ItaAuditLog  # noqa: E402
from app.services.compliance.immutability import (  # noqa: E402
    install_immutability_guards, ImmutableRowError, _make_status_blocker,
)

PASS = 0
FAIL = 0


def check(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"   \033[92m✓ {msg}\033[0m")
    else:
        FAIL += 1
        print(f"   \033[91m✗ {msg}\033[0m")


def raises(fn, exc):
    try:
        fn()
        return False
    except exc:
        return True
    except Exception:
        return False


eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Base.metadata.create_all(bind=eng, tables=[ActionLog.__table__, ItaAuditLog.__table__])
S = sessionmaker(bind=eng, autoflush=False)

install_immutability_guards()


def main():
    s = S()

    # ── ActionLog: insert ok, update + delete rejected ──
    log = ActionLog(status="created", detail="invoice INV-1 created")
    s.add(log)
    s.commit()
    check(log.id is not None, "ActionLog insert allowed")

    def _upd():
        log.detail = "tampered"
        s.commit()
    check(raises(_upd, ImmutableRowError), "ActionLog UPDATE rejected (append-only)")
    s.rollback()

    def _del():
        s.delete(log)
        s.commit()
    check(raises(_del, ImmutableRowError), "ActionLog DELETE rejected (append-only)")
    s.rollback()

    # ── ItaAuditLog: insert ok, update rejected ──
    audit = ItaAuditLog(request_id="1:0", operation="allocation_request", success=True, backend="mock")
    s.add(audit)
    s.commit()
    check(audit.id is not None, "ItaAuditLog insert allowed")

    def _upd2():
        audit.success = False
        s.commit()
    check(raises(_upd2, ImmutableRowError), "ItaAuditLog UPDATE rejected (append-only)")
    s.rollback()

    # ── Override bypass ──
    os.environ["AURORA_AUDIT_ALLOW_OVERRIDE"] = "1"
    try:
        log.detail = "admin correction"
        s.commit()
        overridden = True
    except ImmutableRowError:
        overridden = False
    finally:
        os.environ.pop("AURORA_AUDIT_ALLOW_OVERRIDE", None)
        s.rollback()
    check(overridden, "AURORA_AUDIT_ALLOW_OVERRIDE=1 bypasses the guard")

    # ── Status-based blocker (factory unit) ──
    blk = _make_status_blocker("RevenueShareLedger", "paid")
    check(raises(lambda: blk(None, None, SimpleNamespace(status="paid")), ImmutableRowError),
          "status blocker rejects UPDATE at terminal status ('paid')")
    # Non-terminal status → no raise (returns None)
    blk(None, None, SimpleNamespace(status="accrued"))
    check(True, "status blocker allows UPDATE at non-terminal status ('accrued')")

    s.close()
    print()
    print(f"\033[96m{PASS} passed, {FAIL} failed\033[0m")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
