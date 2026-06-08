"""
Aurora LTS — ITA integration hardening test (pure logic, no network / no keys).

Covers the WS2 changes: deterministic idempotency keys, 9-digit tax-id
validation, sign_request guard ordering, the retryable/error_code failure
contract, and the safety default that ITA_BACKEND is 'mock' unless explicitly
flipped to production.

USAGE: python tests/test_ita_integration.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Ensure no accidental production posture + env-backed secrets (no GCP calls).
os.environ.pop("ITA_BACKEND", None)
os.environ.pop("AURORA_ITA_PRIVATE_KEY", None)
os.environ["SECRET_BACKEND"] = "env"
os.environ.setdefault("DATABASE_URL", "sqlite://")

from app.services.ita import auth, client  # noqa: E402

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


def main():
    # 1. Safety: production is never the default.
    check(client.ITA_BACKEND == "mock", f"ITA_BACKEND defaults to 'mock' (got {client.ITA_BACKEND!r})")

    # 2. Idempotency key is deterministic per (invoice, retry).
    check(auth.build_request_id(5, 0) == "5:0", "build_request_id format = '<invoice>:<retry>'")
    check(auth.build_request_id(5, 0) == auth.build_request_id(5, 0), "build_request_id deterministic (same invoice+retry)")
    check(auth.build_request_id(5, 0) != auth.build_request_id(5, 1), "build_request_id differs by retry")
    check(auth.build_request_id(5, 0) != auth.build_request_id(6, 0), "build_request_id differs by invoice")

    # 3. Tax-id validation (9-digit Israeli format).
    check(auth._validate_seller_tax_id("512345678") == "512345678", "valid 9-digit tax id accepted")
    for bad in ["12345", "abcdefghi", "", "1234567890", "51234567", "51234567x"]:
        check(raises(lambda b=bad: auth._validate_seller_tax_id(b), ValueError), f"reject tax id {bad!r}")

    # 4. sign_request validates the tax id BEFORE touching the signing key.
    check(raises(lambda: auth.sign_request(seller_tax_id="not-a-taxid", request_id="1:0"), ValueError),
          "sign_request raises ValueError on a malformed tax id")

    # 5. With a valid id but no key configured → RuntimeError (config error, not a crash).
    check(raises(lambda: auth.sign_request(seller_tax_id="512345678", request_id="1:0"), RuntimeError),
          "sign_request raises RuntimeError when the private key is unset")

    # 6. Failure result carries the retryability verdict + a granular error code.
    fr = client._failure_result(
        message="HTTP 400: bad tax id", request_id="1:0", http_status=400,
        latency_ms=5, backend="production", retryable=False, error_code="http_400",
    )
    check(fr["success"] is False and fr["retryable"] is False and fr["error_code"] == "http_400",
          "_failure_result carries retryable=False + error_code")
    fr2 = client._failure_result(message="x", request_id="1:0", http_status=503, latency_ms=5, backend="production")
    check(fr2["retryable"] is True and fr2["error_code"] == "ita_error", "_failure_result defaults retryable=True")

    print()
    print(f"\033[96m{PASS} passed, {FAIL} failed\033[0m")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
