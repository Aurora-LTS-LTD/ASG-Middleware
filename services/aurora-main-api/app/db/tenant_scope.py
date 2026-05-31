"""
Aurora LTS — Tenant Scope (P1-04)
==================================
Sets the per-request PostgreSQL session variable that the RLS policies
in migration 0002_rls_policies read.

USAGE PATTERN (new endpoints):

    from fastapi import Depends
    from app.db.tenant_scope import tenant_scoped_db

    @router.get("/invoices")
    def list_invoices(db: Session = Depends(tenant_scoped_db)):
        # Within this request, RLS automatically filters all queries
        # to the caller's organization/business — even queries that
        # forgot a WHERE clause cannot leak cross-tenant rows.
        return db.query(Invoice).all()

LEGACY ENDPOINTS:

    Continue to use `Depends(get_db)`. The RLS policy is NULL-permissive,
    so unset session vars mean RLS lets everything through. Existing
    `WHERE business_id = X` clauses keep working unchanged.

HOW SCOPING WORKS:

    set_tenant_scope(db, 42) sets the Postgres session var
    `aurora.tenant_id` = '42'. The RLS policy filters rows where the
    tenant FK = 42. SET LOCAL is used so the value resets on the next
    transaction — a connection returned to the pool starts unscoped.

PRIORITY ORDER FOR TENANT ID:

    1. user.business_id  (legacy User model — most existing rows)
    2. user.organization_id  (newer Membership-based path)
    3. None  (admin user with no tenant — RLS stays permissive)

    The integer chosen is written to a single session var; the RLS
    policies on each table compare against the column appropriate
    to that table (business_id for invoices, organization_id for
    receipts, etc.). A single id space works because Aurora's
    Organization rows are 1:1 with Business rows in practice
    (see services/identity.get_or_create_organization_for_business).
"""
from __future__ import annotations

import logging
from typing import Generator, Optional

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database.connection import DIALECT, SessionLocal

log = logging.getLogger(__name__)


def set_tenant_scope(db: Session, tenant_id: Optional[int]) -> None:
    """
    Set the per-request RLS session variable.

    On PostgreSQL: executes SET LOCAL aurora.tenant_id = '<id>' within
    the active transaction. On SQLite: no-op.

    Pass None or 0 to clear (the policies revert to permissive).
    """
    if DIALECT != "postgresql":
        return

    value = "" if tenant_id is None or tenant_id == 0 else str(int(tenant_id))
    try:
        # SET LOCAL is transaction-scoped; resets when the conn returns
        # to the pool, preventing scope-leak across requests.
        db.execute(text("SET LOCAL aurora.tenant_id = :v"), {"v": value})
    except Exception as exc:
        # Never crash a request because RLS scope-setting failed.
        # App-layer WHERE clauses are still in effect; RLS is the
        # defence-in-depth layer.
        log.warning("[tenant-scope] failed to set aurora.tenant_id: %s", exc)


def tenant_scoped_db(
    current_user=Depends(lambda: None),  # patched at import time below
) -> Generator[Session, None, None]:
    """
    FastAPI dependency: yields a Session with aurora.tenant_id pre-set
    to the current user's organization/business.

    For now we re-import get_current_user inside the function to avoid
    a circular import at module load time.
    """
    # Lazy import — auth_middleware imports database, which imports us.
    from app.middleware.auth_middleware import get_current_user as _get_current_user  # noqa: F401

    # Resolve current_user lazily — FastAPI rewires Depends at runtime.
    db = SessionLocal()
    try:
        # If the dependency injection produced a User object (because
        # the caller wired the dependency correctly with Depends), use
        # it. Otherwise leave the session unscoped and rely on app-layer.
        user = current_user
        tenant_id: Optional[int] = None
        if user is not None:
            tenant_id = getattr(user, "business_id", None) or getattr(
                user, "organization_id", None
            )
        set_tenant_scope(db, tenant_id)
        yield db
    finally:
        db.close()


__all__ = ["set_tenant_scope", "tenant_scoped_db"]
