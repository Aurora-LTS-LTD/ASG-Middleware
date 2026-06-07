"""
Aurora LTS — Invoice lifecycle state machine test.

Exercises the central transition()/cancel_invoice() helpers directly against an
in-memory SQLite (StaticPool): allowed + denied transitions, timestamp stamping,
the pending_allocation→finalized path (no draft-hack), tax-locked cancel guard,
and audit-row writing.

USAGE: python tests/test_invoice_lifecycle.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.update({"DATABASE_URL": "sqlite://", "AURORA_RUNTIME": "", "SECRET_BACKEND": "env"})

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from aurora_shared.database.models import Base, Invoice, ActionLog  # noqa: E402
from app.services.invoice_lifecycle import (  # noqa: E402
    transition, cancel_invoice, InvoiceTransitionError,
    DRAFT, PENDING_ALLOCATION, FINALIZED, SENT, CANCELLED,
)

PASS = 0
FAIL = 0


def ok(s):
    global PASS
    PASS += 1
    print(f"   \033[92m✓ {s}\033[0m")


def bad(s):
    global FAIL
    FAIL += 1
    print(f"   \033[91m✗ {s}\033[0m")


def check(cond, s):
    ok(s) if cond else bad(s)


def raises(fn, exc):
    try:
        fn()
        return False
    except exc:
        return True
    except Exception:
        return False


eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Base.metadata.create_all(bind=eng, tables=[Invoice.__table__, ActionLog.__table__])
S = sessionmaker(bind=eng, autoflush=False)

_seq = 0


def mkinv(s, status=DRAFT, allocation_status="not_required"):
    global _seq
    _seq += 1
    inv = Invoice(
        business_id=1, invoice_number=f"INV-TEST-{_seq:04d}", beneficiary_name="Customer Ltd",
        amount_net=100.0, vat_rate=0.18, vat_amount=18.0, amount_total=118.0, currency="ILS",
        status=status, allocation_status=allocation_status,
    )
    s.add(inv)
    s.commit()
    s.refresh(inv)
    return inv


def main():
    s = S()

    # 1. draft → finalized stamps finalized_at
    inv = mkinv(s)
    transition(s, inv, FINALIZED, actor="test")
    check(inv.status == FINALIZED and inv.finalized_at is not None, "draft → finalized (+ finalized_at)")

    # 2. finalized → sent stamps sent_at
    transition(s, inv, SENT, actor="test")
    check(inv.status == SENT and inv.sent_at is not None, "finalized → sent (+ sent_at)")

    # 3. sent → draft is illegal
    check(raises(lambda: transition(s, inv, DRAFT), InvoiceTransitionError), "sent → draft denied")

    # 4. finalized → cancelled denied, with the credit-note hint
    inv2 = mkinv(s)
    transition(s, inv2, FINALIZED, actor="test")
    err = None
    try:
        transition(s, inv2, CANCELLED, actor="test")
    except InvoiceTransitionError as e:
        err = e
    check(err is not None and "credit note" in str(err).lower(), "finalized → cancelled denied (credit-note hint)")

    # 5. draft cancel ok (+ cancelled_at)
    inv3 = mkinv(s)
    cancel_invoice(s, inv3, reason="duplicate", actor="test")
    check(inv3.status == CANCELLED and inv3.cancelled_at is not None, "draft cancel (+ cancelled_at)")

    # 6. cancel a finalized invoice → tax-locked
    inv4 = mkinv(s)
    transition(s, inv4, FINALIZED, actor="test")
    check(raises(lambda: cancel_invoice(s, inv4, reason="x", actor="test"), InvoiceTransitionError),
          "cancel finalized → tax-locked (InvoiceTransitionError)")

    # 7. retry-queue path: draft → pending_allocation (+ submitted_at) → finalized (no draft-hack)
    inv5 = mkinv(s, allocation_status="pending")
    transition(s, inv5, PENDING_ALLOCATION, actor="bot")
    check(inv5.status == PENDING_ALLOCATION and inv5.submitted_at is not None,
          "draft → pending_allocation (+ submitted_at)")
    transition(s, inv5, FINALIZED, actor="queue")
    check(inv5.status == FINALIZED, "pending_allocation → finalized (no draft revert)")

    # 8. unknown target status → ValueError
    inv6 = mkinv(s)
    check(raises(lambda: transition(s, inv6, "bogus"), ValueError), "unknown status → ValueError")

    # 9. cancel is idempotent
    cancel_invoice(s, inv3, reason="again", actor="test")
    check(inv3.status == CANCELLED, "cancel idempotent (no error)")

    # 10. every transition wrote an audit row
    n = s.query(ActionLog).filter(ActionLog.status.like("invoice_%")).count()
    check(n >= 6, f"audit rows written for transitions ({n})")

    s.close()
    print()
    print(f"\033[96m{PASS} passed, {FAIL} failed\033[0m")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
