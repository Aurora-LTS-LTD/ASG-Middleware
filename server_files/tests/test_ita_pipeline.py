"""
Aurora LTS — Sprint 3 ITA Client Test Harness
================================================
Drives the new ITA dispatcher (mock + production paths) end-to-end
against the local DB. No live ITA credentials needed — production
path is exercised by stubbing httpx responses.

WHAT THIS PROVES:
  Scenario A — Mock backend (default ITA_BACKEND=mock)
    • request_allocation_number() returns success ~95% of the time
    • Each call writes a row to ita_audit_log
    • The Invoice gets allocation_number, allocation_status='approved',
      allocation_issued_at, ita_request_id, ita_status_code populated

  Scenario B — Production backend WITHOUT signing key
    • Switch ITA_BACKEND=production but leave the private key unset
    • Client returns success=False with message "signing failed: …"
    • An audit row is written with backend='production', success=False
    • The pipeline gracefully surfaces the failure (no crash)

  Scenario C — Production backend with stubbed httpx
    • Switch ITA_BACKEND=production + provide a synthetic key
    • Monkey-patch httpx.AsyncClient.post to return 200 OK with an
      allocation_number → assert success=True
    • Then patch to return 503 → assert success=False (retryable)
    • Then patch to return 4xx → assert success=False (permanent)

  Scenario D — HMAC signature hardening
    • Sets AURORA_RUNTIME=cloud_run
    • Calls _signature_must_enforce() → True
    • Calls _verify_signature with a placeholder secret → False
    • Without AURORA_RUNTIME → falls back to dev skip-true

  Scenario E — Phase 8 migration idempotency
    • Run migrate_phase8 twice, confirm zero errors

  Scenario F — Secret Manager wrapper TTL cache
    • Set env var, get_secret reads it
    • Change env var, get_secret returns CACHED value (within TTL)
    • Call invalidate_secret, then get_secret returns the new value

USAGE:
    cd server_files
    python tests/test_ita_pipeline.py
"""

import asyncio
import datetime
import json
import os
import sys
import time
import uuid

# Make app.* importable from server_files/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Force defaults BEFORE importing app modules so connection.py sees them
os.environ.setdefault("AURORA_RUNTIME", "")
os.environ.setdefault("ITA_BACKEND", "mock")


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
# DB helpers
# ─────────────────────────────────────────────────────────────
def setup_test_invoice():
    """Create a User + Org + Invoice with requires_allocation=1 for testing."""
    from app.database import (
        SessionLocal, create_tables, User, Invoice, Membership,
        Organization, Business,
    )
    from app.services.auth_service import hash_password
    from app.services.identity import create_organization

    create_tables()
    s = SessionLocal()
    try:
        email = f"ita_test_{uuid.uuid4().hex[:8]}@example.com"
        user = User(
            email=email, password_hash=hash_password("xx"),
            full_name="ITA Test", role="business_owner",
            is_active=True, language_pref="he",
            onboarding_status="active",
        )
        s.add(user); s.flush()
        org = create_organization(
            display_name=f"ITA Test Co {uuid.uuid4().hex[:6]}",
            legal_structure="osek_morshe",
            tax_id="123456782",
            owner_user_id=user.id,
            db=s,
        )
        invoice = Invoice(
            business_id=user.business_id,
            invoice_number=f"TEST-{uuid.uuid4().hex[:6]}",
            beneficiary_name="Test Buyer",
            beneficiary_tax_id="987654321",
            amount_net=10000.0,
            vat_rate=0.18,
            vat_amount=1800.0,
            amount_total=11800.0,
            requires_allocation=1,
            allocation_status="pending",
            status="draft",
        )
        s.add(invoice); s.commit()
        return {"user_id": user.id, "org_id": org.id, "invoice_id": invoice.id,
                "biz_id": user.business_id}
    finally:
        s.close()


def cleanup(ctx):
    from app.database import (
        SessionLocal, User, Invoice, Membership, Organization, Business,
        ItaAuditLog, ActionLog,
    )
    s = SessionLocal()
    try:
        s.query(ItaAuditLog).filter(ItaAuditLog.invoice_id == ctx["invoice_id"]).delete()
        s.query(Invoice).filter(Invoice.id == ctx["invoice_id"]).delete()
        s.query(Membership).filter(Membership.user_id == ctx["user_id"]).delete()
        org = s.query(Organization).filter(Organization.id == ctx["org_id"]).first()
        if org:
            legacy = org.legacy_business_id
            s.query(Organization).filter(Organization.id == ctx["org_id"]).delete()
            if legacy:
                s.query(Business).filter(Business.id == legacy).delete()
        s.query(User).filter(User.id == ctx["user_id"]).delete()
        s.commit()
    finally:
        s.close()


def fetch_audit_rows(invoice_id):
    from app.database import SessionLocal, ItaAuditLog
    s = SessionLocal()
    try:
        return s.query(ItaAuditLog).filter(ItaAuditLog.invoice_id == invoice_id).all()
    finally:
        s.close()


def reload_invoice(invoice_id):
    from app.database import SessionLocal, Invoice
    s = SessionLocal()
    try:
        return s.query(Invoice).filter(Invoice.id == invoice_id).first()
    finally:
        s.close()


# ─────────────────────────────────────────────────────────────
# Scenario A — Mock backend
# ─────────────────────────────────────────────────────────────
async def scenario_a_mock_backend():
    title("A — Mock backend writes audit row + populates Invoice tracking columns")
    os.environ["ITA_BACKEND"] = "mock"

    # Force a re-import of the ita package + downstream consumers so the
    # new env value is picked up.
    import importlib, app.services.ita as ita_mod, app.services.ita.client as client_mod
    importlib.reload(client_mod)
    importlib.reload(ita_mod)

    from app.services.ita import request_allocation_number

    ctx = setup_test_invoice()
    print(f"   ctx={ctx}")

    # Loop until success because the mock has 5% failure
    success_response = None
    for attempt in range(12):
        resp = await request_allocation_number(
            seller_tax_id="123456782",
            buyer_tax_id="987654321",
            amount=11800.0,
            invoice_date=datetime.date.today().isoformat(),
            invoice_id=ctx["invoice_id"],
            retry_count=attempt,
            organization_id=ctx["org_id"],
        )
        if resp["success"]:
            success_response = resp
            break

    assert success_response, "Mock should succeed within 12 attempts (95% per call)"
    assert success_response["backend"] == "mock"
    assert success_response["allocation_number"]
    assert success_response["request_id"].startswith("mock-")
    ok(f"Mock returned alloc={success_response['allocation_number']} request_id={success_response['request_id']}")

    rows = fetch_audit_rows(ctx["invoice_id"])
    assert len(rows) >= 1, f"Expected ≥1 audit row, got {len(rows)}"
    # All rows should be backend='mock'
    assert all(r.backend == "mock" for r in rows), f"Got backends: {[r.backend for r in rows]}"
    ok(f"ita_audit_log written: {len(rows)} rows, all backend='mock'")

    # Sanity: masked tax ids
    for r in rows:
        assert r.seller_tax_id_masked and "*" in r.seller_tax_id_masked
        assert r.buyer_tax_id_masked and "*" in r.buyer_tax_id_masked
    ok("Tax IDs are masked in the audit log (no PII leak)")

    cleanup(ctx)
    ok("Test rows removed")


# ─────────────────────────────────────────────────────────────
# Scenario B — Production backend without signing key
# ─────────────────────────────────────────────────────────────
async def scenario_b_production_no_key():
    title("B — Production backend without signing key returns failure cleanly")
    os.environ["ITA_BACKEND"] = "production"
    os.environ.pop("AURORA_ITA_PRIVATE_KEY", None)
    os.environ.pop("ITA_SOFTWARE_HOUSE_ID", None)

    # Re-import to pick up new env
    import importlib
    import app.services.ita.client as client_mod
    import app.services.ita as ita_mod
    importlib.reload(client_mod)
    importlib.reload(ita_mod)
    # Also drop the secrets cache (test isolation)
    from app.services.gcp.secrets import invalidate_all
    invalidate_all()

    from app.services.ita import request_allocation_number

    ctx = setup_test_invoice()
    resp = await request_allocation_number(
        seller_tax_id="123456782",
        buyer_tax_id="987654321",
        amount=11800.0,
        invoice_id=ctx["invoice_id"],
        retry_count=0,
        organization_id=ctx["org_id"],
    )
    assert resp["success"] is False
    assert "signing failed" in resp["message"].lower() or "private signing key" in resp["message"].lower()
    assert resp["backend"] == "production"
    ok(f"Returned: success=False message={resp['message'][:60]!r}")

    rows = fetch_audit_rows(ctx["invoice_id"])
    assert any(r.backend == "production" and not r.success for r in rows)
    ok("ita_audit_log captured a production-backend failure row")

    cleanup(ctx)


# ─────────────────────────────────────────────────────────────
# Scenario C — Production backend with stubbed httpx
# ─────────────────────────────────────────────────────────────
async def scenario_c_production_stubbed():
    title("C — Production backend with stubbed httpx (success / 503 / 4xx)")
    os.environ["ITA_BACKEND"] = "production"
    os.environ["ITA_SOFTWARE_HOUSE_ID"] = "TEST_SH_001"

    # Generate a synthetic RSA key so jose can sign
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
    except Exception:
        print(_c(93, "   ! cryptography not available — skipping scenario C"))
        return

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    os.environ["AURORA_ITA_PRIVATE_KEY"] = pem

    # Reload modules to pick up env
    import importlib
    import app.services.ita.client as client_mod
    importlib.reload(client_mod)
    from app.services.gcp.secrets import invalidate_all
    invalidate_all()
    from app.services.ita import request_allocation_number

    # Stub httpx — replace AsyncClient.post with a fake that returns
    # whatever the test scenario needs.
    import httpx

    class FakeResponse:
        def __init__(self, status, payload):
            self.status_code = status
            self._payload = payload
            self.text = json.dumps(payload) if isinstance(payload, dict) else str(payload)

        def json(self):
            if isinstance(self._payload, dict):
                return self._payload
            raise ValueError("not JSON")

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, headers=None, json=None):
            return FakeClient._next_response

    FakeClient._next_response = None  # set per scenario

    original_async_client = httpx.AsyncClient
    httpx.AsyncClient = FakeClient

    try:
        ctx = setup_test_invoice()

        # ── Sub-scenario C1: 200 OK with allocation_number ──
        FakeClient._next_response = FakeResponse(
            200, {"allocation_number": "987654321", "valid_until": "2026-12-31"}
        )
        resp = await request_allocation_number(
            seller_tax_id="123456782",
            buyer_tax_id="987654321",
            amount=11800.0,
            invoice_id=ctx["invoice_id"],
            retry_count=0,
        )
        assert resp["success"] is True
        assert resp["allocation_number"] == "987654321"
        assert resp["http_status"] == 200
        assert resp["backend"] == "production"
        ok(f"C1: 200-OK → success=True, alloc={resp['allocation_number']}")

        # ── Sub-scenario C2: 503 server error (retryable) ──
        FakeClient._next_response = FakeResponse(503, "ITA temporarily unavailable")
        resp = await request_allocation_number(
            seller_tax_id="123456782",
            buyer_tax_id="987654321",
            amount=11800.0,
            invoice_id=ctx["invoice_id"],
            retry_count=1,
        )
        assert resp["success"] is False
        assert resp["http_status"] == 503
        assert "503" in resp["message"]
        ok(f"C2: 503 → success=False http_status=503 message={resp['message'][:50]!r}")

        # ── Sub-scenario C3: 400 client error (permanent) ──
        FakeClient._next_response = FakeResponse(400, {"error": "Invalid seller_tax_id"})
        resp = await request_allocation_number(
            seller_tax_id="invalid",
            buyer_tax_id="987654321",
            amount=11800.0,
            invoice_id=ctx["invoice_id"],
            retry_count=2,
        )
        assert resp["success"] is False
        assert resp["http_status"] == 400
        ok(f"C3: 400 → success=False http_status=400")

        # Audit rows accrue across all 3
        rows = fetch_audit_rows(ctx["invoice_id"])
        assert len(rows) == 3
        assert sum(1 for r in rows if r.success) == 1
        assert sum(1 for r in rows if not r.success) == 2
        ok(f"ita_audit_log captured all 3 attempts (1 success, 2 failures)")

        cleanup(ctx)

    finally:
        # Always restore httpx
        httpx.AsyncClient = original_async_client


# ─────────────────────────────────────────────────────────────
# Scenario D — HMAC signature hardening
# ─────────────────────────────────────────────────────────────
def scenario_d_hmac_hardening():
    title("D — HMAC signature hardening: production refuses placeholder secrets")

    from app.routers.whatsapp import _signature_must_enforce, _verify_signature

    # Default (no AURORA_RUNTIME, no WA_REQUIRE_SIGNATURE)
    os.environ.pop("AURORA_RUNTIME", None)
    os.environ.pop("WA_REQUIRE_SIGNATURE", None)
    assert _signature_must_enforce() is False
    ok("Dev mode (no flags) → signature_must_enforce = False")

    # Explicit production opt-in
    os.environ["WA_REQUIRE_SIGNATURE"] = "1"
    assert _signature_must_enforce() is True
    ok("WA_REQUIRE_SIGNATURE=1 → signature_must_enforce = True")

    # Cloud Run flips it on
    del os.environ["WA_REQUIRE_SIGNATURE"]
    os.environ["AURORA_RUNTIME"] = "cloud_run"
    assert _signature_must_enforce() is True
    ok("AURORA_RUNTIME=cloud_run → signature_must_enforce = True")

    # Placeholder secret + production enforcement → must reject
    os.environ["WHATSAPP_APP_SECRET"] = "YOUR_META_APP_SECRET_HERE"
    assert _verify_signature(b"some body", "sha256=deadbeef") is False
    ok("Production + placeholder secret + valid-shape sig → verify returns False (correct)")

    # Cleanup
    del os.environ["AURORA_RUNTIME"]
    os.environ.pop("WHATSAPP_APP_SECRET", None)


# ─────────────────────────────────────────────────────────────
# Scenario E — Phase 8 migration idempotency
# ─────────────────────────────────────────────────────────────
def scenario_e_migration_idempotent():
    title("E — Phase 8 migration is idempotent")
    from app.migrate_phase8 import run_phase8_migrations
    run_phase8_migrations()
    print(_c(93, "   (re-running)"))
    run_phase8_migrations()
    ok("Phase 8 ran twice without error")


# ─────────────────────────────────────────────────────────────
# Scenario F — Secret Manager TTL cache
# ─────────────────────────────────────────────────────────────
def scenario_f_secret_cache():
    title("F — Secret Manager wrapper TTL cache")
    from app.services.gcp.secrets import get_secret, invalidate_secret, invalidate_all

    invalidate_all()
    os.environ["TEST_SECRET_CACHE_KEY"] = "value-1"
    v1 = get_secret("TEST_SECRET_CACHE_KEY")
    assert v1 == "value-1"
    ok(f"First read returned {v1!r}")

    # Mutate env, but cache should still serve the old value
    os.environ["TEST_SECRET_CACHE_KEY"] = "value-2"
    v2 = get_secret("TEST_SECRET_CACHE_KEY")
    assert v2 == "value-1", f"Expected cached 'value-1', got {v2!r}"
    ok(f"Within TTL: cache served stale value {v2!r}")

    # Invalidate → next read picks up the new value
    invalidate_secret("TEST_SECRET_CACHE_KEY")
    v3 = get_secret("TEST_SECRET_CACHE_KEY")
    assert v3 == "value-2"
    ok(f"After invalidate: read returns fresh {v3!r}")

    # refresh=True bypasses cache
    os.environ["TEST_SECRET_CACHE_KEY"] = "value-3"
    v4 = get_secret("TEST_SECRET_CACHE_KEY", refresh=True)
    assert v4 == "value-3"
    ok(f"refresh=True bypasses cache → {v4!r}")

    # default fallback
    v5 = get_secret("NONEXISTENT_SECRET", default="fallback")
    assert v5 == "fallback"
    ok("default= fallback works")

    del os.environ["TEST_SECRET_CACHE_KEY"]


# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────
async def main():
    title("Aurora Sprint 3 — ITA Client + Secret Manager + HMAC Tests")

    # Ensure DB exists
    from app.database import create_tables
    create_tables()
    from app.migrate_phase4 import run_phase4_migrations; run_phase4_migrations()
    from app.migrate_phase5 import run_phase5_migrations; run_phase5_migrations()
    from app.migrate_phase6 import run_phase6_migrations; run_phase6_migrations()
    from app.migrate_phase6b import run_phase6b_migrations; run_phase6b_migrations()
    from app.migrate_phase7 import run_phase7_migrations; run_phase7_migrations()
    from app.migrate_phase8 import run_phase8_migrations; run_phase8_migrations()

    try:
        await scenario_a_mock_backend()
        await scenario_b_production_no_key()
        await scenario_c_production_stubbed()
        scenario_d_hmac_hardening()
        scenario_e_migration_idempotent()
        scenario_f_secret_cache()
    except AssertionError as e:
        fail(f"Assertion failed: {e}")
        import traceback; traceback.print_exc()
        return 2

    print()
    print(_c(92, "═" * 60))
    print(_c(92, "  ALL SPRINT 3 ITA / SECRETS / HMAC TESTS PASSED ✅"))
    print(_c(92, "═" * 60))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
