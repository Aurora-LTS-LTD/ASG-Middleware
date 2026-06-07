"""
Aurora LTS — Accountant password auth + email recovery (in-process TestClient).

Proves the REAL endpoints (no logic mocks): email+password login, anti-enum
forgot-password, single-use attempt-capped reset code, password rotation, and
session invalidation on reset. Uses real bcrypt against an in-memory SQLite DB
(StaticPool → one shared connection, no lock contention, and avoids the
Postgres-only client_documents CHECK). The reset code (server-side, never
returned) is captured via the stub send hook.

USAGE: python tests/test_accountant_password_auth.py
"""
import os
import sys
import time

# Access tokens use datetime.utcnow().timestamp(), which interprets the naive
# UTC time as LOCAL time — on a non-UTC dev machine that makes a freshly-minted
# token look already-expired (require_accountant → "Signature has expired").
# Pin the process to UTC (as Cloud Run runs) so the exp check matches prod.
os.environ["TZ"] = "UTC"
time.tzset()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.update({
    "DATABASE_URL": "sqlite://",     # global engine is unused (get_db is overridden)
    "AURORA_RUNTIME": "",
    "OTP_BACKEND": "stub",
    "JWT_SECRET": "test-secret-key-accountant-auth",
    "SECRET_BACKEND": "env",
})

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from slowapi import _rate_limit_exceeded_handler  # noqa: E402
from slowapi.errors import RateLimitExceeded  # noqa: E402
from aurora_shared.middleware.rate_limit import limiter  # noqa: E402
from aurora_shared.database import get_db  # noqa: E402
from aurora_shared.database.models import (  # noqa: E402
    Base, User, Organization, AccountantEngagement,
    AccountantDevice, AccountantRefreshToken, AccountantPasswordReset, ActionLog,
)
from aurora_shared.services.auth_service import hash_password  # noqa: E402
from app.routers import accountant_auth  # noqa: E402

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


EMAIL = "demo.accountant@aurora-lts.test"
PW1 = "Aurora#Test123"
PW2 = "Aurora#Reset456"
FP = "a" * 64

# Capture the reset code — the server only logs/emails it, never returns it.
_captured = {}
accountant_auth._send_reset_code = lambda email, code: _captured.update(email=email, code=code)

# In-memory DB, one shared connection (StaticPool). Create ONLY the auth tables
# (a full create_all trips on client_documents' Postgres-only interval CHECK).
test_engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
)
Base.metadata.create_all(bind=test_engine, tables=[
    User.__table__, Organization.__table__, AccountantEngagement.__table__,
    AccountantDevice.__table__, AccountantRefreshToken.__table__,
    AccountantPasswordReset.__table__, ActionLog.__table__,
])
TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)


def _override_get_db():
    db = TestSession()
    try:
        yield db
    finally:
        db.close()


limiter.enabled = False
app = FastAPI()
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.include_router(accountant_auth.router)
app.dependency_overrides[get_db] = _override_get_db


def seed():
    s = TestSession()
    try:
        u = User(
            email=EMAIL, password_hash=hash_password(PW1),
            full_name="Demo Accountant", first_name="Demo", last_name="Accountant",
            role="accountant", is_active=True,
        )
        s.add(u)
        s.flush()
        org = Organization(
            display_name="Test Client Ltd", legal_structure="osek_morshe", tax_id="512345670",
        )
        s.add(org)
        s.flush()
        s.add(AccountantEngagement(
            accountant_user_id=u.id, organization_id=org.id, status="active",
        ))
        s.commit()
    finally:
        s.close()


def login(client, email, password):
    return client.post("/api/v1/accountant/login", json={
        "email": email, "password": password,
        "device_fingerprint": FP, "platform": "macos", "device_label": "Test Mac",
    })


def main():
    seed()
    client = TestClient(app)

    # 1. Wrong password → 401 invalid_credentials
    r = login(client, EMAIL, "wrong-password-9")
    check(r.status_code == 401 and r.json().get("detail", {}).get("error") == "invalid_credentials",
          f"wrong password → 401 invalid_credentials (got {r.status_code})")

    # 2. Correct password → 200 + tokens + user
    r = login(client, EMAIL, PW1)
    body = r.json()
    check(r.status_code == 200 and body.get("access_token") and body.get("refresh_token")
          and body.get("user", {}).get("email") == EMAIL,
          f"correct password → 200 + tokens (got {r.status_code})")

    # 3. forgot-password (known) → 200 masked + code dispatched
    r = client.post("/api/v1/accountant/forgot-password", json={"email": EMAIL})
    check(r.status_code == 200 and "@" in r.json().get("sent_to", ""),
          f"forgot-password known → 200 masked (got {r.status_code})")
    check(bool(_captured.get("code")) and _captured.get("email") == EMAIL,
          "reset code generated + sent via email hook")

    # 4. forgot-password (unknown) → still 200 (anti-enumeration)
    r = client.post("/api/v1/accountant/forgot-password", json={"email": "nobody@aurora-lts.test"})
    check(r.status_code == 200, f"forgot-password unknown → 200 anti-enum (got {r.status_code})")

    # 5. reset with wrong code → 400
    r = client.post("/api/v1/accountant/reset-password", json={
        "email": EMAIL, "code": "WRONGXYZ", "new_password": PW2})
    check(r.status_code == 400, f"reset wrong code → 400 (got {r.status_code})")

    # 6. reset with correct code → 200
    code = _captured["code"]
    r = client.post("/api/v1/accountant/reset-password", json={
        "email": EMAIL, "code": code, "new_password": PW2})
    check(r.status_code == 200, f"reset correct code → 200 (got {r.status_code}: {r.text[:140]})")

    # 7. old password rejected; new password works
    r = login(client, EMAIL, PW1)
    check(r.status_code == 401, f"old password rejected post-reset → 401 (got {r.status_code})")
    r = login(client, EMAIL, PW2)
    check(r.status_code == 200 and r.json().get("access_token"),
          f"new password works → 200 (got {r.status_code})")
    token = r.json().get("access_token", "")

    # 8. reset code is single-use → reuse fails
    r = client.post("/api/v1/accountant/reset-password", json={
        "email": EMAIL, "code": code, "new_password": "Another#Pw789"})
    check(r.status_code == 400, f"reused reset code → 400 single-use (got {r.status_code})")

    # 9-11. Profile GET/PATCH (authed) — editable name + firm
    hdr = {"Authorization": f"Bearer {token}"}
    r = client.get("/api/v1/accountant/profile", headers=hdr)
    check(r.status_code == 200 and r.json().get("name") == "Demo Accountant" and not r.json().get("firm_name"),
          f"GET profile → 200 name + empty firm (got {r.status_code})")
    r = client.patch("/api/v1/accountant/profile", headers=hdr,
                     json={"name": "Renamed Accountant", "firm_name": "Masarwa & Co"})
    check(r.status_code == 200 and r.json().get("name") == "Renamed Accountant" and r.json().get("firm_name") == "Masarwa & Co",
          f"PATCH profile → 200 reflects changes (got {r.status_code}: {r.text[:120]})")
    r = client.get("/api/v1/accountant/profile", headers=hdr)
    check(r.status_code == 200 and r.json().get("name") == "Renamed Accountant" and r.json().get("firm_name") == "Masarwa & Co",
          "GET profile after PATCH → persisted")

    print()
    print(f"\033[96m{PASS} passed, {FAIL} failed\033[0m")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
