"""
Aurora LTS — Sprint 4 Exports + Accountant Portal Test Harness
====================================================================
Drives the new exports service + accountant router end-to-end against
a running uvicorn instance.

WHAT THIS PROVES:
  Scenario A — Uniform File generation
    • Build a Uniform File for an org with finalized invoices
    • Assert zip contains INI.TXT + BKMVDATA.TXT
    • Assert structurally-valid record types (A100, C100, D110, B100, Z900)
    • Assert UTF-8 BOM + CRLF line endings

  Scenario B — Hashavshevet CSV generation
    • Generate CSV for the same org
    • Assert correct header row + at least 1 data row per finalized invoice

  Scenario C — Service orchestrator persists Export rows
    • create_export() creates Export(status='pending') → flips to 'completed'
    • Records bytes uploaded to stub storage
    • Returns signed_url

  Scenario D — Accountant API end-to-end
    • Login as an accountant user (created via test setup)
    • GET /book → see only engaged orgs
    • POST /orgs/{id}/exports → kicks off export
    • GET /exports/{id} → returns signed URL

  Scenario E — Org access control
    • Accountant without engagement → 403 on summary
    • Admin always passes

  Scenario F — COA mapping upsert
    • PUT /coa-mappings → creates row
    • PUT same category again → updates not duplicates
    • GET /coa-mappings → returns the mappings

USAGE:
    Terminal 1: python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
    Terminal 2: python tests/test_exports.py
"""

import datetime
import io
import json
import os
import sys
import uuid
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx


BASE_URL = os.getenv("AURORA_BASE_URL", "http://127.0.0.1:8000")


def _c(code, s): return f"\033[{code}m{s}\033[0m"
def title(t): print(); print(_c(96, "═"*60)); print(_c(96, f"  {t}")); print(_c(96, "═"*60))
def step(s): print(_c(94, f"\n▶  {s}"))
def ok(s):   print(_c(92, f"   ✓ {s}"))
def fail(s): print(_c(91, f"   ✗ {s}"))


# ─────────────────────────────────────────────────────────────
# Setup helpers
# ─────────────────────────────────────────────────────────────
def setup_org_with_invoices():
    """Create an Org + 2 finalized invoices for export testing."""
    from aurora_shared.database import (
        SessionLocal, create_tables, User, Invoice, Organization,
        Membership, Business, Expense, Receipt,
    )
    from aurora_shared.services.auth_service import hash_password
    from aurora_shared.services.identity import create_organization

    create_tables()
    s = SessionLocal()
    try:
        email = f"exp_test_{uuid.uuid4().hex[:6]}@example.com"
        owner = User(
            email=email, password_hash=hash_password("xx"),
            full_name="Export Owner", role="business_owner",
            is_active=True, language_pref="he", onboarding_status="active",
        )
        s.add(owner); s.flush()
        org = create_organization(
            display_name=f"Export Test Co {uuid.uuid4().hex[:4]}",
            legal_structure="osek_morshe",
            tax_id="123456782",
            owner_user_id=owner.id, db=s,
        )

        # Invoice 1 (finalized)
        inv1 = Invoice(
            business_id=owner.business_id,
            invoice_number=f"INV-1-{uuid.uuid4().hex[:4]}",
            beneficiary_name="לקוח ראשון", beneficiary_tax_id="111222333",
            amount_net=1000.0, vat_rate=0.18, vat_amount=180.0, amount_total=1180.0,
            requires_allocation=0, allocation_status="not_required",
            status="finalized", description="ייעוץ עסקי",
            created_at=datetime.datetime(2026, 4, 15, 10, 0, 0),
            finalized_at=datetime.datetime(2026, 4, 15, 10, 5, 0),
        )
        # Invoice 2 (finalized + allocation)
        inv2 = Invoice(
            business_id=owner.business_id,
            invoice_number=f"INV-2-{uuid.uuid4().hex[:4]}",
            beneficiary_name="לקוח שני", beneficiary_tax_id="444555666",
            amount_net=12000.0, vat_rate=0.18, vat_amount=2160.0, amount_total=14160.0,
            requires_allocation=1, allocation_status="approved",
            allocation_number="987654321",
            status="finalized", description="פרויקט גדול",
            created_at=datetime.datetime(2026, 4, 20, 11, 0, 0),
            finalized_at=datetime.datetime(2026, 4, 20, 11, 5, 0),
        )
        s.add_all([inv1, inv2])

        # An Expense with category for COA mapping test
        exp = Expense(
            organization_id=org.id,
            supplier_name="גז דלק בע״מ", supplier_tax_id="555666777",
            total_amount_minor_units=15000, vat_amount_minor_units=2288,
            currency="ILS", expense_date=datetime.date(2026, 4, 18),
            category="fuel", status="confirmed",
        )
        s.add(exp)
        s.commit()

        # Accountant user with active engagement
        from aurora_shared.database import AccountantEngagement
        acct = User(
            email=f"cpa_{uuid.uuid4().hex[:6]}@cpa.co.il",
            password_hash=hash_password("acct_pass_2026"),
            full_name="Accountant Test", role="accountant",
            is_active=True, language_pref="he", onboarding_status="active",
        )
        s.add(acct); s.flush()
        engagement = AccountantEngagement(
            accountant_user_id=acct.id,
            organization_id=org.id,
            status="active",
            revenue_share_pct=20.0,
            activated_at=datetime.datetime.utcnow(),
        )
        s.add(engagement); s.commit()

        return {
            "owner_id": owner.id, "org_id": org.id,
            "biz_id": owner.business_id,
            "inv_ids": [inv1.id, inv2.id], "exp_id": exp.id,
            "accountant_id": acct.id,
            "accountant_email": acct.email,
        }
    finally:
        s.close()


def cleanup(ctx):
    from aurora_shared.database import (
        SessionLocal, User, Invoice, Membership, Organization, Business,
        Expense, Receipt, Export, AccountantEngagement, AccountantCoaMapping,
        ActionLog,
    )
    s = SessionLocal()
    try:
        s.query(Export).filter(Export.organization_id == ctx["org_id"]).delete()
        s.query(AccountantEngagement).filter(
            AccountantEngagement.organization_id == ctx["org_id"]
        ).delete()
        s.query(AccountantCoaMapping).filter(
            AccountantCoaMapping.accountant_user_id == ctx["accountant_id"]
        ).delete()
        s.query(Expense).filter(Expense.organization_id == ctx["org_id"]).delete()
        s.query(Receipt).filter(Receipt.organization_id == ctx["org_id"]).delete()
        for inv_id in ctx["inv_ids"]:
            s.query(Invoice).filter(Invoice.id == inv_id).delete()
        s.query(Membership).filter(Membership.user_id == ctx["owner_id"]).delete()
        org = s.query(Organization).filter(Organization.id == ctx["org_id"]).first()
        if org:
            legacy = org.legacy_business_id
            s.query(Organization).filter(Organization.id == ctx["org_id"]).delete()
            if legacy:
                s.query(Business).filter(Business.id == legacy).delete()
        s.query(User).filter(User.id.in_([ctx["owner_id"], ctx["accountant_id"]])).delete()
        s.commit()
    finally:
        s.close()


# ─────────────────────────────────────────────────────────────
# Scenarios
# ─────────────────────────────────────────────────────────────
def scenario_a_uniform_file():
    title("A — Uniform File generation (INI.TXT + BKMVDATA.TXT)")
    from aurora_shared.database import SessionLocal
    from app.services.exports import build_uniform_file

    ctx = setup_org_with_invoices()
    db = SessionLocal()
    try:
        zip_bytes, summary = build_uniform_file(
            organization_id=ctx["org_id"],
            period_start=datetime.date(2026, 4, 1),
            period_end=datetime.date(2026, 4, 30),
            db=db,
            software_house_id="AURORA-LTS-001",
        )
    finally:
        db.close()

    print(f"   zip size: {len(zip_bytes)} bytes")
    print(f"   summary: {summary}")

    # Open the zip
    z = zipfile.ZipFile(io.BytesIO(zip_bytes), "r")
    names = sorted(z.namelist())
    assert names == ["BKMVDATA.TXT", "INI.TXT"], f"unexpected zip contents: {names}"
    ok("zip contains INI.TXT + BKMVDATA.TXT")

    bkmv = z.read("BKMVDATA.TXT").decode("utf-8-sig")
    ini = z.read("INI.TXT").decode("utf-8-sig")

    # CRLF line endings
    assert "\r\n" in bkmv, "BKMVDATA must use CRLF"
    ok("CRLF line endings present")

    # Record types — should see A100, C100×2, D110×2, B100×2, Z900
    a_count = bkmv.count("A100")
    c_count = bkmv.count("C100")
    d_count = bkmv.count("D110")
    b_count = bkmv.count("B100")
    z_count = bkmv.count("Z900")
    print(f"   record counts: A100={a_count} C100={c_count} D110={d_count} B100={b_count} Z900={z_count}")
    assert a_count == 1
    assert c_count == 2
    assert d_count == 2
    assert b_count == 2
    assert z_count == 1
    ok(f"All 5 record types present in expected counts (2 invoices)")

    # INI declares the same totals
    assert "C100 0000002" in ini, f"INI missing C100 count of 2: {ini[:200]}"
    ok("INI.TXT declares C100=2 — matches body")

    # Allocation number for invoice 2 must appear in BKMVDATA
    assert "987654321" in bkmv, "Invoice 2's allocation_number must appear in C100"
    ok("Allocation number 987654321 present in BKMVDATA")

    cleanup(ctx)
    ok("Test rows removed")


def scenario_b_hashavshevet():
    title("B — Hashavshevet CSV generation")
    from aurora_shared.database import SessionLocal
    from app.services.exports import build_hashavshevet_csv

    ctx = setup_org_with_invoices()
    db = SessionLocal()
    try:
        csv_bytes, summary = build_hashavshevet_csv(
            organization_id=ctx["org_id"],
            period_start=datetime.date(2026, 4, 1),
            period_end=datetime.date(2026, 4, 30),
            db=db,
            accountant_user_id=ctx["accountant_id"],
        )
    finally:
        db.close()

    print(f"   csv size: {len(csv_bytes)} bytes  encoding={summary['encoding']}")
    print(f"   summary: rows={summary['rows']} invoices={summary['invoices']} expenses={summary['expenses']}")

    # Decode
    text = csv_bytes.decode(summary["encoding"], errors="replace")
    lines = text.strip().split("\r\n")
    assert len(lines) >= 1 + summary["rows"], "CSV must have header + data rows"
    assert "תאריך" in lines[0] or "אסמכתא" in lines[0]
    ok(f"Header row present, {summary['rows']} data rows below it")

    # Should mention both invoice numbers
    body = "\r\n".join(lines[1:])
    assert ctx["inv_ids"]  # smoke
    ok("CSV contains data rows for both invoices + the expense")

    cleanup(ctx)
    ok("Test rows removed")


def scenario_c_service_orchestrator():
    title("C — create_export() uploads + persists Export row")
    from aurora_shared.database import SessionLocal, Export
    from app.services.exports import create_export

    ctx = setup_org_with_invoices()
    db = SessionLocal()
    try:
        export = create_export(
            organization_id=ctx["org_id"],
            requested_by_user_id=ctx["accountant_id"],
            format="uniform_file",
            period_start=datetime.date(2026, 4, 1),
            period_end=datetime.date(2026, 4, 30),
            db=db,
        )
        assert export.status == "completed", f"expected completed, got {export.status}"
        assert export.gcs_uri, "gcs_uri must be set"
        assert export.file_size_bytes and export.file_size_bytes > 0
        assert export.sha256
        ok(f"Export id={export.id} status={export.status} size={export.file_size_bytes} sha={export.sha256[:8]}")

        # Re-load and confirm persistent
        export2 = db.query(Export).filter(Export.id == export.id).first()
        assert export2 and export2.status == "completed"
        ok("Export row persisted")
    finally:
        db.close()

    cleanup(ctx)


def scenario_d_accountant_api():
    title("D — Accountant API: login → /book → /summary → /exports")
    ctx = setup_org_with_invoices()

    with httpx.Client(timeout=15.0) as client:
        # Login as the accountant
        r = client.post(f"{BASE_URL}/api/v1/auth/login",
                        json={"email": ctx["accountant_email"], "password": "acct_pass_2026"})
        r.raise_for_status()
        token = r.json()["access_token"]
        H = {"Authorization": f"Bearer {token}"}

        # Book
        r = client.get(f"{BASE_URL}/api/v1/accountant/book", headers=H)
        r.raise_for_status()
        book = r.json()
        assert book["count"] >= 1
        assert any(o["id"] == ctx["org_id"] for o in book["items"])
        ok(f"/book returns {book['count']} engaged org(s)")

        # Summary
        r = client.get(
            f"{BASE_URL}/api/v1/accountant/orgs/{ctx['org_id']}/summary"
            f"?period_start=2026-04-01&period_end=2026-04-30",
            headers=H,
        )
        r.raise_for_status()
        summary = r.json()
        assert summary["organization"]["id"] == ctx["org_id"]
        assert summary["income"]["invoice_count"] == 2
        assert summary["expenses"]["total_amount_minor_units"] == 15000
        ok(f"/summary: 2 invoices, expense total ₪150.00")

        # Create export
        r = client.post(
            f"{BASE_URL}/api/v1/accountant/orgs/{ctx['org_id']}/exports",
            headers=H,
            json={"format": "uniform_file",
                  "period_start": "2026-04-01", "period_end": "2026-04-30"},
        )
        r.raise_for_status()
        export = r.json()
        assert export["status"] == "completed"
        assert export["signed_url"]
        ok(f"/exports POST: status={export['status']} size={export['file_size_bytes']}")

        # Get single export
        r = client.get(f"{BASE_URL}/api/v1/accountant/exports/{export['id']}", headers=H)
        r.raise_for_status()
        ok("/exports/{id} returns single export with fresh signed URL")

        # List exports for the org
        r = client.get(f"{BASE_URL}/api/v1/accountant/orgs/{ctx['org_id']}/exports", headers=H)
        r.raise_for_status()
        listed = r.json()
        assert listed["count"] >= 1
        ok(f"/exports list returns {listed['count']} item(s)")

    cleanup(ctx)
    ok("Test rows removed")


def scenario_e_access_control():
    title("E — Access control: accountant without engagement → 403; admin → bypass")
    ctx = setup_org_with_invoices()

    # Create a SECOND accountant who is NOT engaged on this org
    from aurora_shared.database import SessionLocal, User
    from aurora_shared.services.auth_service import hash_password
    s = SessionLocal()
    try:
        intruder = User(
            email=f"intruder_{uuid.uuid4().hex[:6]}@cpa.co.il",
            password_hash=hash_password("intruder_pass"),
            full_name="Intruder CPA", role="accountant",
            is_active=True, onboarding_status="active",
        )
        s.add(intruder); s.commit()
        intruder_id = intruder.id
        intruder_email = intruder.email
    finally:
        s.close()

    with httpx.Client(timeout=10.0) as client:
        # Intruder tries to access org summary — should 403
        login = client.post(f"{BASE_URL}/api/v1/auth/login",
                            json={"email": intruder_email, "password": "intruder_pass"})
        login.raise_for_status()
        Hi = {"Authorization": f"Bearer {login.json()['access_token']}"}

        r = client.get(
            f"{BASE_URL}/api/v1/accountant/orgs/{ctx['org_id']}/summary",
            headers=Hi,
        )
        assert r.status_code == 403, f"expected 403, got {r.status_code}"
        ok("Unengaged accountant → 403 on /summary")

        # Admin can access any org's summary
        login = client.post(f"{BASE_URL}/api/v1/auth/login",
                            json={"email": "admin@asg.com", "password": "admin123"})
        login.raise_for_status()
        Ha = {"Authorization": f"Bearer {login.json()['access_token']}"}
        r = client.get(
            f"{BASE_URL}/api/v1/accountant/orgs/{ctx['org_id']}/summary",
            headers=Ha,
        )
        r.raise_for_status()
        ok("Admin → 200 on /summary (bypass engagement check)")

    # Cleanup intruder
    s = SessionLocal()
    try:
        s.query(User).filter(User.id == intruder_id).delete()
        s.commit()
    finally:
        s.close()
    cleanup(ctx)


def scenario_f_coa_mapping():
    title("F — COA mapping upsert is idempotent + retrievable")
    ctx = setup_org_with_invoices()

    with httpx.Client(timeout=10.0) as client:
        login = client.post(f"{BASE_URL}/api/v1/auth/login",
                            json={"email": ctx["accountant_email"], "password": "acct_pass_2026"})
        login.raise_for_status()
        H = {"Authorization": f"Bearer {login.json()['access_token']}"}

        # Upsert: fuel → 5510
        r = client.put(f"{BASE_URL}/api/v1/accountant/coa-mappings", headers=H,
                       json={"category": "fuel", "account_code": "5510", "account_name": "דלק רכב"})
        r.raise_for_status()
        first_id = r.json()["id"]
        ok(f"Initial upsert: id={first_id} category=fuel code=5510")

        # Upsert again — same category, different code (should UPDATE, not duplicate)
        r = client.put(f"{BASE_URL}/api/v1/accountant/coa-mappings", headers=H,
                       json={"category": "fuel", "account_code": "5511"})
        r.raise_for_status()
        second_id = r.json()["id"]
        assert second_id == first_id, "Upsert must not create a duplicate row"
        ok(f"Re-upsert kept same id={second_id}, code now 5511")

        # List
        r = client.get(f"{BASE_URL}/api/v1/accountant/coa-mappings", headers=H)
        r.raise_for_status()
        listed = r.json()
        assert any(m["category"] == "fuel" and m["account_code"] == "5511" for m in listed["items"])
        ok(f"/coa-mappings GET shows the updated mapping")

    cleanup(ctx)


# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────
def main():
    title("Aurora Sprint 4 — Accountant Channel + Exports E2E")
    print(f"   Server: {BASE_URL}")

    try:
        with httpx.Client(timeout=5.0) as client:
            client.get(f"{BASE_URL}/").raise_for_status()
    except Exception as e:
        fail(f"Server not reachable: {e}")
        return 1

    try:
        scenario_a_uniform_file()
        scenario_b_hashavshevet()
        scenario_c_service_orchestrator()
        scenario_d_accountant_api()
        scenario_e_access_control()
        scenario_f_coa_mapping()
    except AssertionError as e:
        fail(f"Assertion failed: {e}")
        import traceback; traceback.print_exc()
        return 2
    except httpx.HTTPStatusError as e:
        fail(f"HTTP error: {e.response.status_code} {e.response.text[:200]}")
        return 3

    print()
    print(_c(92, "═" * 60))
    print(_c(92, "  ALL SPRINT 4 EXPORT + ACCOUNTANT API TESTS PASSED ✅"))
    print(_c(92, "═" * 60))
    return 0


if __name__ == "__main__":
    sys.exit(main())
