"""
Sprint 1.8 — Dual-write Audit Verification
==============================================
Exercises the two legacy mutation sites that were patched to dual-write
to the new Organization + Membership tables:

  1. POST /api/v1/businesses          (legacy admin endpoint)
     - With NO owner_user_id  → Business + Organization created;
                                   no Membership (admin assigns later).
     - With an owner_user_id  → Business + Organization + Membership(owner)
                                   created; legacy User.business_id mirrored.

  2. POST /api/v1/auth/register       (admin-only registration)
     - role='admin'                    → User only, no dual-write needed.
     - role='business_owner' + business_id → User + Membership(owner) on
                                              the Org paired with that Business.

Plus a regression check on the runtime backfill helper:
  3. A pre-existing Business with NO paired Organization → calling
     get_or_create_organization_for_business() creates the Organization
     and is idempotent on the second call.

USAGE:
  Terminal 1: python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
  Terminal 2: python tests/test_dual_write_audit.py
"""

import os
import random
import sys
import uuid
from typing import Optional

# Make `app.*` imports resolve when this script is run as
# `python tests/test_dual_write_audit.py` from server_files/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx


BASE_URL = os.getenv("AURORA_BASE_URL", "http://127.0.0.1:8000")


# ─────────────────────────────────────────────────────────────
# Pretty-print helpers
# ─────────────────────────────────────────────────────────────
def _c(code, s):
    return f"\033[{code}m{s}\033[0m"


def title(t):
    print()
    print(_c(96, "═" * 60))
    print(_c(96, f"  {t}"))
    print(_c(96, "═" * 60))


def step(s):
    print(_c(94, f"\n▶  {s}"))


def ok(s):
    print(_c(92, f"   ✓ {s}"))


def fail(s):
    print(_c(91, f"   ✗ {s}"))


# ─────────────────────────────────────────────────────────────
# DB inspection helpers (read-only — exercises the model layer)
# ─────────────────────────────────────────────────────────────
def db():
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from aurora_shared.database import SessionLocal
    return SessionLocal()


def fetch_org_for_business(business_id: int):
    from aurora_shared.database import Organization
    session = db()
    try:
        return (
            session.query(Organization)
            .filter(Organization.legacy_business_id == business_id)
            .first()
        )
    finally:
        session.close()


def fetch_membership(user_id: int, org_id: int):
    from aurora_shared.database import Membership
    session = db()
    try:
        return (
            session.query(Membership)
            .filter(Membership.user_id == user_id, Membership.organization_id == org_id)
            .first()
        )
    finally:
        session.close()


def cleanup_business_chain(business_id: int, also_user_ids: Optional[list] = None):
    """Tear down the test rows we created."""
    from aurora_shared.database import (
        SessionLocal, Business, Organization, Membership, User, ActionLog,
    )
    session = SessionLocal()
    try:
        # Memberships first (FK to org)
        org = session.query(Organization).filter(Organization.legacy_business_id == business_id).first()
        if org:
            session.query(Membership).filter(Membership.organization_id == org.id).delete()
            session.query(Organization).filter(Organization.id == org.id).delete()
        # Users (only the ones we created)
        if also_user_ids:
            session.query(Membership).filter(Membership.user_id.in_(also_user_ids)).delete()
            session.query(User).filter(User.id.in_(also_user_ids)).delete()
        # Legacy Business
        session.query(Business).filter(Business.id == business_id).delete()
        session.commit()
    finally:
        session.close()


# ─────────────────────────────────────────────────────────────
# Auth helper — log in as the seeded admin
# ─────────────────────────────────────────────────────────────
def admin_login() -> str:
    """Return a JWT for the seeded admin (admin@asg.com / admin123)."""
    with httpx.Client(timeout=10.0) as client:
        r = client.post(
            f"{BASE_URL}/api/v1/auth/login",
            json={"email": "admin@asg.com", "password": "admin123"},
        )
        r.raise_for_status()
        return r.json()["access_token"]


def H(token):
    return {"Authorization": f"Bearer {token}"}


# ─────────────────────────────────────────────────────────────
# SCENARIO A — POST /api/v1/businesses with NO owner_user_id
# ─────────────────────────────────────────────────────────────
def scenario_a_business_no_owner():
    title("A — POST /businesses (no owner_user_id) → Business + Organization, no Membership")
    token = admin_login()

    biz_name = f"DualWrite-Test-A-{uuid.uuid4().hex[:6]}"
    step(f"Creating Business name={biz_name!r} via legacy endpoint")

    with httpx.Client(timeout=10.0) as client:
        r = client.post(
            f"{BASE_URL}/api/v1/businesses",
            headers=H(token),
            json={"name": biz_name, "phone": "+972501112222", "business_type": "contractor"},
        )
        r.raise_for_status()
        body = r.json()

    biz_id = body["id"]
    org_id = body["organization_id"]
    print(f"   API returned: id={biz_id} organization_id={org_id} owner_membership_id={body.get('owner_membership_id')}")

    # Assertions
    assert body["organization_id"] is not None, "Response must include organization_id"
    assert body["owner_membership_id"] is None, "No owner specified → no Membership"

    org = fetch_org_for_business(biz_id)
    assert org is not None, f"Expected Organization paired with business_id={biz_id}"
    assert org.id == org_id, "Response organization_id must match DB"
    assert org.display_name == biz_name, "Organization display_name must mirror Business.name"
    assert org.kyc_status == "pending", "New Organization should land in pending KYC"

    ok(f"Business id={biz_id} → paired Organization id={org.id} ({org.display_name!r})")
    ok(f"No Membership created (admin will assign owner later)")

    cleanup_business_chain(biz_id)
    ok("Test rows removed")


# ─────────────────────────────────────────────────────────────
# SCENARIO B — POST /api/v1/businesses WITH owner_user_id
# ─────────────────────────────────────────────────────────────
def scenario_b_business_with_owner():
    title("B — POST /businesses (with owner_user_id) → Business + Organization + Membership(owner)")
    token = admin_login()

    # Create a target user via /auth/register (without business_id) so
    # we have someone to assign as owner.
    owner_email = f"dwt-b-{uuid.uuid4().hex[:6]}@example.com"
    step(f"Creating target owner user via /auth/register: {owner_email}")
    with httpx.Client(timeout=10.0) as client:
        r = client.post(
            f"{BASE_URL}/api/v1/auth/register",
            headers=H(token),
            json={
                "email": owner_email,
                "password": "test_password_xyz",
                "full_name": "DWT Owner B",
                "role": "business_owner",  # no business_id → no dual-write yet (this validates Scenario D too)
                "language_pref": "he",
            },
        )
        r.raise_for_status()
        owner = r.json()
    owner_user_id = owner["id"]

    biz_name = f"DualWrite-Test-B-{uuid.uuid4().hex[:6]}"
    step(f"Creating Business with owner_user_id={owner_user_id}")
    with httpx.Client(timeout=10.0) as client:
        r = client.post(
            f"{BASE_URL}/api/v1/businesses",
            headers=H(token),
            json={
                "name": biz_name,
                "phone": "+972503334444",
                "business_type": "electrician",
                "owner_user_id": owner_user_id,
            },
        )
        r.raise_for_status()
        body = r.json()

    biz_id = body["id"]
    org_id = body["organization_id"]
    membership_id = body["owner_membership_id"]

    assert org_id is not None
    assert membership_id is not None, "Owner provided → Membership must be created"

    # DB-level verification
    org = fetch_org_for_business(biz_id)
    mem = fetch_membership(owner_user_id, org.id)
    assert mem is not None, "Membership row must exist in DB"
    assert mem.role == "owner", f"Expected role=owner, got {mem.role}"
    assert mem.is_primary is True, "First membership should be primary"

    ok(f"Business id={biz_id} → Org id={org.id}")
    ok(f"Membership(role=owner, is_primary=True) created for user_id={owner_user_id}")

    # Verify legacy compat: User.business_id was mirrored
    from aurora_shared.database import SessionLocal, User
    session = SessionLocal()
    try:
        u = session.query(User).filter(User.id == owner_user_id).first()
        assert u.business_id == biz_id, \
            f"User.business_id should mirror to {biz_id}, got {u.business_id}"
        ok(f"Legacy User.business_id mirrored = {u.business_id} (expand/contract bridge)")
    finally:
        session.close()

    cleanup_business_chain(biz_id, also_user_ids=[owner_user_id])
    ok("Test rows removed")


# ─────────────────────────────────────────────────────────────
# SCENARIO C — POST /auth/register with role=business_owner + business_id
# ─────────────────────────────────────────────────────────────
def scenario_c_register_with_business_id():
    title("C — POST /auth/register (role=business_owner + business_id) → User + Membership(owner)")
    token = admin_login()

    # Pre-existing Business that already has its paired Organization
    biz_name = f"DualWrite-Test-C-{uuid.uuid4().hex[:6]}"
    step(f"Creating prerequisite Business {biz_name!r} (no owner yet)")
    with httpx.Client(timeout=10.0) as client:
        r = client.post(
            f"{BASE_URL}/api/v1/businesses",
            headers=H(token),
            json={"name": biz_name, "business_type": "retail"},
        )
        r.raise_for_status()
        biz_id = r.json()["id"]
        biz_org_id = r.json()["organization_id"]

    # Now register a business_owner user pointing at that Business
    user_email = f"dwt-c-{uuid.uuid4().hex[:6]}@example.com"
    step(f"Registering {user_email} with business_id={biz_id}")
    with httpx.Client(timeout=10.0) as client:
        r = client.post(
            f"{BASE_URL}/api/v1/auth/register",
            headers=H(token),
            json={
                "email": user_email,
                "password": "test_password_xyz",
                "full_name": "DWT Owner C",
                "role": "business_owner",
                "business_id": biz_id,
                "language_pref": "he",
            },
        )
        r.raise_for_status()
        body = r.json()

    user_id = body["id"]
    assert body["organization_id"] == biz_org_id, "register response must echo paired org_id"
    assert body["membership_id"] is not None, "Membership must be created"

    # DB-level verification
    mem = fetch_membership(user_id, biz_org_id)
    assert mem is not None
    assert mem.role == "owner"

    ok(f"User id={user_id} ({user_email}) registered")
    ok(f"Membership(role=owner) auto-created on Org id={biz_org_id}")

    cleanup_business_chain(biz_id, also_user_ids=[user_id])
    ok("Test rows removed")


# ─────────────────────────────────────────────────────────────
# SCENARIO D — POST /auth/register with role=admin → no dual-write
# ─────────────────────────────────────────────────────────────
def scenario_d_register_admin():
    title("D — POST /auth/register (role=admin) → User only, no Org/Membership")
    token = admin_login()

    user_email = f"dwt-d-admin-{uuid.uuid4().hex[:6]}@example.com"
    step(f"Registering admin user {user_email}")
    with httpx.Client(timeout=10.0) as client:
        r = client.post(
            f"{BASE_URL}/api/v1/auth/register",
            headers=H(token),
            json={
                "email": user_email,
                "password": "test_password_xyz",
                "full_name": "DWT Admin D",
                "role": "admin",
                "language_pref": "en",
            },
        )
        r.raise_for_status()
        body = r.json()

    assert body["organization_id"] is None, "Admin role must NOT create an Organization"
    assert body["membership_id"] is None, "Admin role must NOT create a Membership"

    ok("Admin user created with no Organization or Membership (correct)")

    # Cleanup
    from aurora_shared.database import SessionLocal, User
    session = SessionLocal()
    try:
        session.query(User).filter(User.email == user_email).delete()
        session.commit()
    finally:
        session.close()
    ok("Test row removed")


# ─────────────────────────────────────────────────────────────
# SCENARIO E — Runtime backfill on a "naked" Business
# ─────────────────────────────────────────────────────────────
def scenario_e_runtime_backfill():
    title("E — Runtime backfill: orphan Business → get_or_create_organization_for_business")
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    from aurora_shared.database import SessionLocal, Business, Organization
    from aurora_shared.services.identity import get_or_create_organization_for_business

    session = SessionLocal()
    try:
        # Manually create a Business WITHOUT going through the patched
        # endpoint. Simulates a row left over from before Sprint 1.8.
        biz = Business(
            name=f"Orphan-Test-E-{uuid.uuid4().hex[:6]}",
            phone="+972505556666",
            tax_id="123456782",   # valid checksum, starts with "1" → osek_morshe
            status="active",
        )
        session.add(biz)
        session.commit()
        session.refresh(biz)
        biz_id = biz.id

        step(f"Created naked Business id={biz_id} (no paired Org yet)")

        # Confirm there's no Organization yet
        before = session.query(Organization).filter(
            Organization.legacy_business_id == biz_id
        ).first()
        assert before is None, "Orphan Business must not have an Org yet"

        # First call: should create the Org
        step("Calling get_or_create_organization_for_business() — first time")
        org_first = get_or_create_organization_for_business(biz_id, session)
        session.commit()
        assert org_first.legacy_business_id == biz_id
        assert org_first.legal_structure == "osek_morshe"
        assert org_first.tax_id == "123456782"
        ok(f"Created Organization id={org_first.id} legal={org_first.legal_structure}")

        # Second call: must be idempotent (same row)
        step("Calling again — must be idempotent")
        org_second = get_or_create_organization_for_business(biz_id, session)
        assert org_second.id == org_first.id, \
            "Second call must return SAME Organization (no duplicate)"
        ok(f"Idempotent — returned same Organization id={org_second.id}")

        # Cleanup
        cleanup_business_chain(biz_id)
        ok("Test rows removed")

    finally:
        session.close()


# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────
def main():
    title("Sprint 1.8 — Dual-write Audit Verification")
    print(f"   Server: {BASE_URL}")

    try:
        with httpx.Client(timeout=5.0) as client:
            r = client.get(f"{BASE_URL}/")
            r.raise_for_status()
    except Exception as e:
        fail(f"Server not reachable at {BASE_URL}: {e}")
        return 1

    try:
        scenario_a_business_no_owner()
        scenario_b_business_with_owner()
        scenario_c_register_with_business_id()
        scenario_d_register_admin()
        scenario_e_runtime_backfill()
    except AssertionError as e:
        fail(f"Assertion failed: {e}")
        return 2
    except httpx.HTTPStatusError as e:
        fail(f"HTTP error: {e.response.status_code} {e.response.text[:200]}")
        return 3

    print()
    print(_c(92, "═" * 60))
    print(_c(92, "  ALL DUAL-WRITE AUDIT SCENARIOS PASSED ✅"))
    print(_c(92, "═" * 60))
    return 0


if __name__ == "__main__":
    sys.exit(main())
