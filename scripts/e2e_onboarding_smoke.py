#!/usr/bin/env python3
"""
Aurora LTS — End-to-End Onboarding Smoke Test
==============================================
Drives the full self-service onboarding FSM against a LIVE aurora-api,
step by step, printing a PASS/FAIL line per stage. Every request carries
`Origin: https://app.aurora-ltd.co.il` so the real production CORS path
is exercised — not just the funnel logic.

WHY A SCRIPT (vs clicking the UI):
  Reproducible, fast, and it isolates "is the API + CORS healthy?" from
  "is the frontend glue wired?". Run this first; if it's green, any
  remaining UI failure is purely front-end.

TWO HUMAN-GATED STEPS (unavoidable against the production config):
  • Email OTP — with OTP_BACKEND=production the 6-digit code is emailed,
    not returned by the API. The script pauses and asks you to paste it.
    (If OTP_BACKEND=stub, the code comes back as `dev_only_code` and the
    script uses it automatically — fully headless.)
  • PayPlus payment method — the tokenization payload normally comes from
    the browser iframe. Provide a sandbox token via --payplus-token-file
    (JSON), or the script stops cleanly BEFORE /activate and reports how
    far it got.

USAGE
  python3 scripts/e2e_onboarding_smoke.py \
      --api-base https://api-aurora-lts.com \
      --origin   https://app.aurora-ltd.co.il \
      --email    e2e+$(date +%s)@aurora-ltd.co.il \
      --password 'Test1234!' \
      [--payplus-token-file ./payplus_sandbox_token.json]

  Stdlib only — no pip install needed. Runs on any python3.

EXIT CODE: 0 if every attempted step passed; non-zero on the first failure.
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
import time
import urllib.error
import urllib.request

# ── ANSI (no-op if not a tty) ────────────────────────────────
_TTY = sys.stdout.isatty()
def _c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if _TTY else s
def ok(s):   return _c("32", s)
def bad(s):  return _c("31", s)
def warn(s): return _c("33", s)
def dim(s):  return _c("2", s)

_STEP = 0
def step_banner(title: str) -> None:
    global _STEP
    _STEP += 1
    print(f"\n{_c('1', f'[STEP {_STEP}] {title}')}")


class SmokeError(RuntimeError):
    pass


def req(
    method: str,
    url: str,
    *,
    origin: str,
    token: str | None = None,
    json_body: dict | None = None,
    raw_body: bytes | None = None,
    content_type: str | None = None,
    timeout: int = 30,
    expect: tuple[int, ...] = (200, 201, 204),
) -> tuple[int, dict, object]:
    """One HTTP call. Returns (status, response_headers_lower, parsed_body)."""
    headers = {"Origin": origin, "Accept": "application/json"}
    data = None
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif raw_body is not None:
        data = raw_body
        if content_type:
            headers["Content-Type"] = content_type
    if token:
        headers["Authorization"] = f"Bearer {token}"

    r = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            status = resp.status
            rhdrs = {k.lower(): v for k, v in resp.headers.items()}
            body_bytes = resp.read()
    except urllib.error.HTTPError as e:
        status = e.code
        rhdrs = {k.lower(): v for k, v in (e.headers.items() if e.headers else [])}
        body_bytes = e.read() if hasattr(e, "read") else b""
    except urllib.error.URLError as e:
        raise SmokeError(f"network error calling {method} {url}: {e}") from e

    parsed: object
    txt = body_bytes.decode("utf-8", errors="replace") if body_bytes else ""
    try:
        parsed = json.loads(txt) if txt else None
    except json.JSONDecodeError:
        parsed = txt

    line = f"  {method} {url.split('/api/v1')[-1] or url} → HTTP {status}"
    if status in expect:
        print(ok(line))
    else:
        print(bad(line + f"  (expected {expect})"))
        snippet = (txt or "")[:300]
        print(dim(f"    body: {snippet}"))
        raise SmokeError(f"{method} {url} returned {status}, expected {expect}")
    return status, rhdrs, parsed


def main() -> int:
    ap = argparse.ArgumentParser(description="Aurora onboarding E2E smoke")
    ap.add_argument("--api-base", default="https://api-aurora-lts.com")
    ap.add_argument("--origin", default="https://app.aurora-ltd.co.il")
    ap.add_argument("--email", default=None, help="defaults to e2e+<ts>@aurora-ltd.co.il")
    ap.add_argument("--password", default="Test1234!")
    ap.add_argument("--legal-structure", default="osek_morshe",
                    choices=["osek_morshe", "osek_patur", "chevra_baam"])
    ap.add_argument("--plan", default="starter", choices=["starter", "pro", "enterprise"])
    ap.add_argument("--billing-cycle", default="monthly",
                    choices=["monthly", "quarterly", "annual"])
    ap.add_argument("--payplus-token-file", default=None,
                    help="JSON file with the PayPlus tokenization payload; "
                         "if omitted, the script stops cleanly before /activate")
    args = ap.parse_args()

    api = args.api_base.rstrip("/")
    origin = args.origin.rstrip("/")
    email = args.email or f"e2e+{int(time.time())}@aurora-ltd.co.il"
    base = f"{api}/api/v1/onboarding"

    print(_c("1", "═" * 64))
    print(_c("1", "  Aurora LTS — Onboarding E2E Smoke"))
    print(f"  api    : {api}")
    print(f"  origin : {origin}")
    print(f"  email  : {email}")
    print(_c("1", "═" * 64))

    # ── STEP: CORS preflight from the allowlisted origin ──
    step_banner("CORS preflight (OPTIONS /onboarding/start)")
    r = urllib.request.Request(
        f"{base}/start", method="OPTIONS",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,authorization",
        },
    )
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            acao = resp.headers.get("access-control-allow-origin")
            print(ok(f"  preflight → HTTP {resp.status} | allow-origin: {acao}"))
    except urllib.error.HTTPError as e:
        acao = e.headers.get("access-control-allow-origin") if e.headers else None
        if acao:
            print(ok(f"  preflight → HTTP {e.code} | allow-origin: {acao}"))
        else:
            print(bad(f"  preflight → HTTP {e.code} | NO access-control-allow-origin header"))
            print(warn("    The browser would BLOCK every call from this origin. "
                       "Add it to _cors_origins or check the service is healthy."))
            return 2
    if acao not in (origin, "*"):
        print(warn(f"    allow-origin={acao!r} does not match {origin!r} — browser will block."))
        return 2

    # ── STEP: health (fail fast with the ITA hint if the service is down) ──
    step_banner("Service health")
    try:
        _, _, health = req("GET", f"{base}/health", origin=origin)
    except SmokeError as e:
        print(bad(f"  {e}"))
        print(warn("    If this is 503/500: the aurora-api revision is crash-looping. "
                   "Most likely ITA_BACKEND=mock tripping the P1-09 guard — deploy the "
                   "escape-hatch build and set AURORA_ALLOW_MOCK_ITA=1. See backend_check.py."))
        return 3
    print(dim(f"    backends: kyc={health.get('kyc_backend')} "
              f"otp={health.get('otp_backend')} payplus={health.get('payplus_backend')}"))

    # ── STEP: plans (public pricing) ──
    step_banner("Plans (public pricing)")
    req("GET", f"{base}/plans", origin=origin)

    # ── STEP: start (creates user + session JWT) ──
    step_banner("Start onboarding")
    _, _, started = req("POST", f"{base}/start", origin=origin, expect=(201,), json_body={
        "email": email, "password": args.password,
        "language_pref": "he", "surface": "web",
    })
    token = started.get("access_token")
    if not token:
        print(bad("    no access_token in /start response")); return 4
    print(dim(f"    session token acquired ({token[:12]}…)"))

    # ── STEP: identity ──
    step_banner("Identity")
    req("POST", f"{base}/identity", origin=origin, token=token, json_body={
        "first_name": "E2E", "last_name": "Tester",
        "legal_structure": args.legal_structure,
        "tax_id": "123456782",            # valid 9-digit mod-11
        "display_name": "E2E Smoke Business",
        "business_address": "1 Test St", "city": "Tel Aviv", "postal_code": "6100000",
    })

    # ── STEP: email OTP (auto if dev_only_code, else prompt) ──
    step_banner("Email OTP")
    _, _, otp_resp = req("POST", f"{base}/email/send-otp", origin=origin, token=token,
                         json_body={"target": email, "purpose": "signup"})
    code = (otp_resp or {}).get("dev_only_code") if isinstance(otp_resp, dict) else None
    if code:
        print(dim(f"    OTP_BACKEND=stub → dev_only_code={code} (auto)"))
    else:
        print(warn(f"    OTP emailed to {email} (OTP_BACKEND=production)."))
        code = input("    paste the 6-digit code from the inbox: ").strip()
    req("POST", f"{base}/email/verify-otp", origin=origin, token=token,
        json_body={"target": email, "code": code})

    # ── STEP: KYC — init → PUT bytes → finalize, for each required doc ──
    step_banner("KYC documents")
    required = {
        "osek_morshe": ["israeli_id_front", "israeli_id_back", "business_certificate"],
        "osek_patur":  ["israeli_id_front", "israeli_id_back", "business_certificate"],
        "chevra_baam": ["israeli_id_front", "israeli_id_back", "company_registry_extract"],
    }[args.legal_structure]
    # Minimal valid PNG (1x1) — real bytes so sha256/size are sane.
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
        "890000000a49444154789c6360000002000154a24f5d0000000049454e44ae426082"
    )
    for dtype in required:
        _, _, init = req("POST", f"{base}/documents/init-upload", origin=origin, token=token,
                         json_body={"document_type": dtype, "mime_type": "image/png",
                                    "bytes_size": len(png)})
        doc_id = init["doc_id"]; upload_url = init["upload_url"]
        ct = (init.get("headers") or {}).get("Content-Type", "image/png")
        # PUT straight to the (GCS-signed or stub) URL — not through CORS.
        req("PUT", upload_url, origin=origin, raw_body=png, content_type=ct, expect=(200, 201, 204))
        _, _, fin = req("POST", f"{base}/documents/finalize", origin=origin, token=token,
                        json_body={"doc_id": doc_id})
        print(dim(f"    {dtype}: status={fin.get('status')} "
                  f"advanced={fin.get('advanced_to_next_step')}"))

    # ── STEP: billing plan ──
    step_banner("Billing — plan")
    req("POST", f"{base}/billing/plan", origin=origin, token=token,
        json_body={"plan": args.plan, "billing_cycle": args.billing_cycle})

    # ── STEP: payment method (needs a PayPlus token) ──
    step_banner("Billing — payment method")
    if not args.payplus_token_file:
        print(warn("    --payplus-token-file not provided. The tokenization payload "
                   "comes from the PayPlus iframe (browser). Stopping cleanly BEFORE "
                   "/review + /activate."))
        print(ok("\nSMOKE PARTIAL-PASS: funnel healthy through billing/plan. "
                 "Provide a sandbox PayPlus token to complete /activate."))
        return 0
    with open(args.payplus_token_file) as fh:
        tok_payload = json.load(fh)
    req("POST", f"{base}/billing/payment-method", origin=origin, token=token,
        json_body={"kind": "credit_card", "tokenization_payload": tok_payload})

    # ── STEP: review ──
    step_banner("Review (accept T&C + privacy)")
    req("POST", f"{base}/review", origin=origin, token=token,
        json_body={"terms_accepted": True, "privacy_accepted": True})

    # ── STEP: activate ──
    step_banner("Activate")
    _, _, act = req("POST", f"{base}/activate", origin=origin, token=token)
    print(dim(f"    redirect_to={act.get('redirect_to')} "
              f"new_token={'yes' if act.get('access_token') else 'no'}"))

    print(ok("\n" + "═" * 64))
    print(ok("  ✓ SMOKE PASS — full onboarding funnel completed end-to-end."))
    print(ok("═" * 64))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SmokeError as e:
        print(bad(f"\n✗ SMOKE FAIL: {e}"))
        sys.exit(1)
    except KeyboardInterrupt:
        print("\ninterrupted")
        sys.exit(130)
