"""
ASG / Aurora Solutions — WhatsApp ONBOARDING FSM Mock Harness
================================================================
Drives the full in-WhatsApp ONBOARDING flow LOCALLY by POSTing
synthetic Meta webhook payloads to /api/v1/admin/whatsapp/mock-inbound.
No Meta credentials required — perfect for development before our
Meta Developer account is provisioned.

WHAT THIS PROVES:
  - The state machine advances correctly: FIRST_NAME → LAST_NAME →
    LEGAL_STRUCTURE → TAX_ID → BUSINESS_NAME → BUSINESS_TYPE →
    INVITE_ACCOUNTANT → ACCOUNTANT_CONTACT → CONFIRM → finalize.
  - The Tax-ID validator rejects bad checksums and the FSM re-prompts.
  - Buttons (legal_structure, invite_accountant, confirm) are accepted
    by their button_id.
  - List rows (business_type) are accepted by their list_id.
  - The "cancel" universal works at any step.
  - Final commit creates User + Organization + Membership +
    (optional) Invitation atomically.

HOW TO RUN (against a running uvicorn dev server):

  Terminal 1: start the server
      cd ~/Desktop/ASG-Middleware/server_files
      source ../venv/bin/activate
      python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

  Terminal 2: run this script
      cd ~/Desktop/ASG-Middleware/server_files
      python tests/mock_whatsapp_inbound.py

The script uses synthetic phone numbers (+97250TESTxxxx) and cleans
up the rows it creates at the end.
"""

import json
import os
import random
import sys
import time

# Make `app.*` imports resolve when this script is run as
# `python tests/mock_whatsapp_inbound.py` from server_files/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx


# ─────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────
BASE_URL = os.getenv("AURORA_BASE_URL", "http://127.0.0.1:8000")
TIMEOUT = 10.0


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────
def _color(code: int, text: str) -> str:
    return f"\033[{code}m{text}\033[0m"


def title(text: str) -> None:
    print()
    print(_color(96, "═" * 60))
    print(_color(96, f"  {text}"))
    print(_color(96, "═" * 60))


def step(text: str) -> None:
    print(_color(94, f"\n▶  {text}"))


def ok(text: str) -> None:
    print(_color(92, f"   ✓ {text}"))


def warn(text: str) -> None:
    print(_color(93, f"   ! {text}"))


def fail(text: str) -> None:
    print(_color(91, f"   ✗ {text}"))


# ─────────────────────────────────────────────────────────────
# Mock-inbound + outbox helpers
# ─────────────────────────────────────────────────────────────
def send_mock(phone: str, *, text: str = None, button_id: str = None, list_id: str = None) -> dict:
    """POST a synthetic inbound to the mock endpoint and return the response."""
    payload = {"from": phone}
    if text is not None:
        payload["text"] = text
        payload["type"] = "text"
    elif button_id is not None:
        payload["button_id"] = button_id
        payload["type"] = "interactive"
    elif list_id is not None:
        payload["list_id"] = list_id
        payload["type"] = "interactive"

    with httpx.Client(timeout=TIMEOUT) as client:
        r = client.post(f"{BASE_URL}/api/v1/admin/whatsapp/mock-inbound", json=payload)
        r.raise_for_status()
        return r.json()


def show_replies(resp: dict, max_chars: int = 220) -> None:
    """Pretty-print outbound messages from a mock-inbound response."""
    msgs = resp.get("outbound_messages", [])
    if not msgs:
        warn("No outbound replies (FSM may have stayed silent — investigate)")
        return
    for m in msgs:
        kind = m["kind"]
        if kind == "text":
            body_text = (m.get("payload") or {}).get("text", {}).get("body", "")
        elif kind == "buttons":
            inter = (m.get("payload") or {}).get("interactive", {})
            body_text = (inter.get("body") or {}).get("text", "")
            buttons = inter.get("action", {}).get("buttons", [])
            btn_titles = " | ".join(b["reply"]["title"] for b in buttons)
            body_text += f"\n   ⌬ buttons: [{btn_titles}]"
        elif kind == "list":
            inter = (m.get("payload") or {}).get("interactive", {})
            body_text = (inter.get("body") or {}).get("text", "")
            sections = inter.get("action", {}).get("sections", [])
            rows = [r["title"] for s in sections for r in s.get("rows", [])]
            body_text += f"\n   ☰ list rows: [{' | '.join(rows)}]"
        else:
            body_text = json.dumps(m.get("payload"), ensure_ascii=False)[:max_chars]

        snippet = body_text if len(body_text) <= max_chars else body_text[:max_chars] + "…"
        # Highlight whether the message left the building successfully
        marker = "📤" if m["status"] == "sent" else ("📝" if m["status"] in ("pending", "failed") else "•")
        print(f"   {marker}  {snippet}")


# ─────────────────────────────────────────────────────────────
# DB inspection helpers (uses local SQLite directly so we can verify
# the FSM committed Org + Membership at the end).
# ─────────────────────────────────────────────────────────────
def db_lookup_user_org(phone: str) -> dict:
    """Look up the User + paired Org (if any) for a given WhatsApp phone."""
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from app.database import SessionLocal, User, Organization, Membership, Invitation

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.whatsapp_phone_e164 == phone).first()
        if not user:
            return {"user": None, "org": None, "membership": None, "invitations": []}

        mem = (
            db.query(Membership)
            .filter(Membership.user_id == user.id)
            .first()
        )
        org = None
        if mem:
            org = db.query(Organization).filter(Organization.id == mem.organization_id).first()

        invitations = []
        if org:
            invs = db.query(Invitation).filter(Invitation.organization_id == org.id).all()
            invitations = [
                {"id": i.id, "role": i.role, "status": i.status,
                 "target_email": i.target_email, "target_phone_e164": i.target_phone_e164}
                for i in invs
            ]

        return {
            "user": {
                "id": user.id, "email": user.email,
                "full_name": user.full_name, "role": user.role,
                "whatsapp_phone_e164": user.whatsapp_phone_e164,
                "onboarding_status": user.onboarding_status,
                "phone_verified_at": user.phone_verified_at.isoformat() if user.phone_verified_at else None,
                "business_id": user.business_id,
            },
            "org": {
                "id": org.id, "display_name": org.display_name,
                "legal_structure": org.legal_structure, "tax_id": org.tax_id,
                "industry_code": org.industry_code,
                "kyc_status": org.kyc_status, "status": org.status,
                "legacy_business_id": org.legacy_business_id,
            } if org else None,
            "membership": {
                "role": mem.role, "is_primary": mem.is_primary,
            } if mem else None,
            "invitations": invitations,
        }
    finally:
        db.close()


def db_cleanup_test_user(phone: str) -> None:
    """Remove the synthetic User + its Org + Membership + Invitations + legacy Business."""
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from app.database import (
        SessionLocal, User, Organization, Membership, Invitation,
        Business, WhatsAppSession, WhatsAppOutboundLog,
    )
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.whatsapp_phone_e164 == phone).first()
        if user:
            db.query(Invitation).filter(Invitation.invited_by_user_id == user.id).delete()
            mem = db.query(Membership).filter(Membership.user_id == user.id).all()
            org_ids = [m.organization_id for m in mem]
            db.query(Membership).filter(Membership.user_id == user.id).delete()
            for oid in org_ids:
                org = db.query(Organization).filter(Organization.id == oid).first()
                if org:
                    legacy = org.legacy_business_id
                    db.query(Organization).filter(Organization.id == oid).delete()
                    if legacy:
                        db.query(Business).filter(Business.id == legacy).delete()
            db.query(WhatsAppOutboundLog).filter(WhatsAppOutboundLog.user_id == user.id).delete()
            db.query(User).filter(User.id == user.id).delete()
        # Always clean session rows + orphan outbound logs for this phone
        db.query(WhatsAppSession).filter(WhatsAppSession.whatsapp_phone_e164 == phone).delete()
        db.query(WhatsAppOutboundLog).filter(WhatsAppOutboundLog.whatsapp_phone_e164 == phone).delete()
        db.commit()
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────
# THE TEST SCENARIOS
# ─────────────────────────────────────────────────────────────
def scenario_happy_path() -> None:
    title("Scenario A — Happy path: full WhatsApp ONBOARDING")

    phone = f"+97250TEST{random.randint(1000, 9999)}"
    print(f"   synthetic phone: {phone}")

    db_cleanup_test_user(phone)  # idempotency

    step("0. Unbound user says 'hi' — expect welcome + 3-button choice")
    show_replies(send_mock(phone, text="hi"))

    step("1. Tap 'Sign up here' button — enters ONBOARDING:FIRST_NAME")
    show_replies(send_mock(phone, button_id="btn:wa_signup"))

    step("2. Send first name 'Ibrahim'")
    show_replies(send_mock(phone, text="Ibrahim"))

    step("3. Send last name 'Masarwa'")
    show_replies(send_mock(phone, text="Masarwa"))

    step("4. Tap legal_structure button = osek_morshe")
    show_replies(send_mock(phone, button_id="onb:legal:osek_morshe"))

    step("5a. Send a BAD tax_id — expect the validator to reject")
    show_replies(send_mock(phone, text="123456789"))

    step("5b. Send a VALID tax_id (123456782 — known good test value)")
    show_replies(send_mock(phone, text="123456782"))

    step("6. Send business name")
    show_replies(send_mock(phone, text="Aurora WA-Test Co."))

    step("7. Tap business_type list row = contractor")
    show_replies(send_mock(phone, list_id="onb:btype:contractor"))

    step("8. Tap 'Yes invite accountant'")
    show_replies(send_mock(phone, button_id="onb:invite:yes"))

    step("9. Provide accountant email")
    show_replies(send_mock(phone, text="cpa@example.com"))

    step("10. Tap CONFIRM — finalize")
    resp = send_mock(phone, button_id="onb:confirm")
    show_replies(resp)

    step("11. Verify persistent state in DB")
    state = db_lookup_user_org(phone)
    assert state["user"], "User should exist"
    assert state["user"]["onboarding_status"] == "active", \
        f"Expected onboarding_status='active', got {state['user']['onboarding_status']}"
    assert state["user"]["phone_verified_at"], "phone_verified_at should be stamped"
    assert state["org"], "Organization should exist"
    assert state["org"]["display_name"] == "Aurora WA-Test Co."
    assert state["org"]["legal_structure"] == "osek_morshe"
    assert state["org"]["tax_id"] == "123456782"
    assert state["org"]["industry_code"] == "contractor"
    assert state["membership"]["role"] == "owner"
    assert state["membership"]["is_primary"] is True
    assert state["user"]["business_id"], "Legacy business_id dual-write should be set"
    assert any(i["role"] == "accountant" and i["target_email"] == "cpa@example.com"
               for i in state["invitations"]), "Accountant invitation should be queued"

    ok(f"User {state['user']['email']!r} created — onboarding_status={state['user']['onboarding_status']}")
    ok(f"Org id={state['org']['id']} display={state['org']['display_name']!r} legal={state['org']['legal_structure']}")
    ok(f"Membership role={state['membership']['role']} is_primary={state['membership']['is_primary']}")
    ok(f"Invitations: {state['invitations']}")
    ok("Legacy business_id dual-write OK")

    # Cleanup
    db_cleanup_test_user(phone)
    ok("Test rows removed")


def scenario_cancel_at_step() -> None:
    title("Scenario B — Universal cancel works mid-flow")
    phone = f"+97250TEST{random.randint(1000, 9999)}"
    db_cleanup_test_user(phone)

    step("Send 'hi' → tap Sign up → first name → cancel")
    send_mock(phone, text="hi")
    send_mock(phone, button_id="btn:wa_signup")
    send_mock(phone, text="Ibrahim")
    resp = send_mock(phone, text="cancel")
    show_replies(resp)

    state = db_lookup_user_org(phone)
    assert state["user"] is None, "Cancelled user must NOT have been created"
    ok("No User created after cancel — FSM cleanly aborted")

    db_cleanup_test_user(phone)


def scenario_keyword_trigger() -> None:
    title("Scenario C — Trigger keyword bypasses the choice menu")
    phone = f"+97250TEST{random.randint(1000, 9999)}"
    db_cleanup_test_user(phone)

    step("Send '/start' from cold state — expect FSM to enter directly")
    resp = send_mock(phone, text="/start")
    show_replies(resp)

    # We expect at least the 'onb_intro' + 'onb_ask_first_name' messages.
    msgs = resp.get("outbound_messages", [])
    bodies = [
        (m.get("payload") or {}).get("text", {}).get("body", "")
        for m in msgs if m["kind"] == "text"
    ]
    assert any("Aurora" in b or "אורורה" in b or "أورورا" in b for b in bodies), \
        "Expected the onb_intro message to mention Aurora"
    ok("Trigger keyword '/start' entered the FSM directly")

    db_cleanup_test_user(phone)


# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────
def main() -> int:
    title("Aurora WhatsApp ONBOARDING — Mock Test Harness")
    print(f"   Server: {BASE_URL}")

    # Probe the server
    try:
        with httpx.Client(timeout=5.0) as client:
            h = client.get(f"{BASE_URL}/api/v1/whatsapp/health").json()
            print(f"   /health → configured={h['configured']} signature_enforced={h['signature_enforced']}")
    except Exception as e:
        fail(f"Server not reachable at {BASE_URL}: {e}")
        print("   Start it with: python -m uvicorn app.main:app --host 0.0.0.0 --port 8000")
        return 1

    try:
        scenario_happy_path()
        scenario_cancel_at_step()
        scenario_keyword_trigger()
    except AssertionError as e:
        fail(f"Assertion failed: {e}")
        return 2
    except httpx.HTTPStatusError as e:
        fail(f"HTTP error: {e.response.status_code} {e.response.text[:200]}")
        return 3

    print()
    print(_color(92, "═" * 60))
    print(_color(92, "  ALL ONBOARDING FSM SCENARIOS PASSED ✅"))
    print(_color(92, "═" * 60))
    return 0


if __name__ == "__main__":
    sys.exit(main())
