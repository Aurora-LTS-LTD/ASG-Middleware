"""
sendgrid_client.py — Synchronous transactional email via SendGrid REST API.

Used by:
  • accountant_auth.py  → send_otp(), send_new_device_alert()
  • (future) scheduled reports, invoice delivery, etc.

Why httpx instead of the sendgrid SDK?
  The sendgrid SDK pulls in several MB of dependencies we don't need.
  The REST endpoint is a single POST — a thin httpx call is cleaner.

ENV VARS:
  SENDGRID_API_KEY     — required in production; absent → warning + no-op
  SENDGRID_FROM        — sender address (default: otp@api-aurora-lts.com)
  SENDGRID_FROM_NAME   — display name   (default: Aurora LTS)
"""

import logging
import os

import httpx

log = logging.getLogger(__name__)

_SENDGRID_API = "https://api.sendgrid.com/v3/mail/send"

# ──────────────────────────────────────────────────────────────────────────────
# Config helpers
# ──────────────────────────────────────────────────────────────────────────────

def _api_key() -> str:
    return (os.getenv("SENDGRID_API_KEY") or "").strip()


def _from_address() -> str:
    return (os.getenv("SENDGRID_FROM") or "otp@api-aurora-lts.com").strip()


def _from_name() -> str:
    return (os.getenv("SENDGRID_FROM_NAME") or "Aurora LTS").strip()


def is_configured() -> bool:
    return bool(_api_key())


# ──────────────────────────────────────────────────────────────────────────────
# Core send
# ──────────────────────────────────────────────────────────────────────────────

def _send(to_email: str, subject: str, html: str, plain: str) -> None:
    """
    POST one transactional email to the SendGrid REST API.
    Raises RuntimeError on HTTP error so callers can wrap in try/except → 503.
    """
    key = _api_key()
    if not key:
        log.warning("[sendgrid] SENDGRID_API_KEY not set — email NOT sent to %s", to_email)
        return

    payload = {
        "personalizations": [{"to": [{"email": to_email}]}],
        "from": {"email": _from_address(), "name": _from_name()},
        "subject": subject,
        "content": [
            {"type": "text/plain", "value": plain},
            {"type": "text/html",  "value": html},
        ],
        "mail_settings": {
            "sandbox_mode": {"enable": False},
        },
        "tracking_settings": {
            "click_tracking": {"enable": False},
            "open_tracking": {"enable": False},
        },
    }

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                _SENDGRID_API,
                json=payload,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
            )
        if resp.status_code not in (200, 202):
            log.error(
                "[sendgrid] send failed status=%s body=%s to=%s",
                resp.status_code, resp.text[:300], to_email,
            )
            raise RuntimeError(f"SendGrid returned {resp.status_code}")
        log.info("[sendgrid] email queued to=%s subject=%r", to_email, subject)
    except httpx.RequestError as exc:
        log.error("[sendgrid] network error sending to %s: %s", to_email, exc)
        raise RuntimeError(f"SendGrid network error: {exc}") from exc


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def send_otp(to_email: str, otp: str) -> None:
    """
    Send a 6-digit OTP to the accountant's work email.
    Called from accountant_auth._send_otp_to_user().
    """
    subject = "Your Aurora portal sign-in code"
    html = f"""
<!DOCTYPE html>
<html lang="en">
<body style="margin:0;padding:40px 0;background:#09090b;font-family:system-ui,sans-serif;">
  <div style="max-width:480px;margin:0 auto;background:#18181b;border:1px solid #27272a;
              border-radius:12px;padding:40px;">
    <div style="margin-bottom:28px;">
      <div style="display:inline-block;background:#4f46e5;border-radius:8px;
                  padding:10px 14px;font-size:20px;font-weight:700;color:#fff;
                  letter-spacing:-0.5px;">A</div>
    </div>
    <h1 style="margin:0 0 8px;font-size:22px;color:#f4f4f5;font-weight:600;">
      Your sign-in code
    </h1>
    <p style="margin:0 0 28px;color:#a1a1aa;font-size:14px;line-height:1.6;">
      Enter this code in the Aurora LTS Accountant Portal.
      It expires in <strong style="color:#f4f4f5;">60 seconds</strong>.
    </p>
    <div style="background:#09090b;border:1px solid #27272a;border-radius:8px;
                padding:24px;text-align:center;margin-bottom:28px;">
      <span style="font-size:36px;font-weight:700;letter-spacing:12px;
                   color:#818cf8;font-variant-numeric:tabular-nums;">{otp}</span>
    </div>
    <p style="margin:0;color:#71717a;font-size:12px;line-height:1.6;">
      If you didn't request this code, you can safely ignore this email.
      Someone may have entered your email address by mistake.
    </p>
  </div>
</body>
</html>
"""
    plain = f"Your Aurora LTS sign-in code: {otp}\n\nExpires in 60 seconds. If you didn't request this, ignore this email."
    _send(to_email, subject, html, plain)


def send_whatsapp_otp(to_phone_e164: str, otp: str) -> None:
    """
    Send a 6-digit OTP via WhatsApp Business Cloud API using the
    pre-approved OTP template (WHATSAPP_OTP_TEMPLATE_NAME env var).

    Uses a synchronous httpx.Client so this can be called from sync
    FastAPI endpoints without needing asyncio plumbing.

    Template components follow Meta's "authentication" template spec:
      - body[0].parameters[0] = the OTP code string
    """
    token = (os.getenv("WHATSAPP_ACCESS_TOKEN") or "").strip()
    phone_number_id = (os.getenv("WHATSAPP_PHONE_NUMBER_ID") or "").strip()
    template_name = (os.getenv("WHATSAPP_OTP_TEMPLATE_NAME") or "").strip()
    version = (os.getenv("WHATSAPP_API_VERSION") or "v20.0").strip()

    if not token or not phone_number_id or not template_name:
        log.warning(
            "[sendgrid/wa_otp] WhatsApp not configured — "
            "WHATSAPP_ACCESS_TOKEN / WHATSAPP_PHONE_NUMBER_ID / "
            "WHATSAPP_OTP_TEMPLATE_NAME missing. OTP NOT sent to %s",
            to_phone_e164,
        )
        return

    url = f"https://graph.facebook.com/{version}/{phone_number_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_phone_e164.lstrip("+"),
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": "en_US"},
            "components": [
                {
                    "type": "body",
                    "parameters": [{"type": "text", "text": otp}],
                }
            ],
        },
    }

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
            )
        if resp.status_code not in (200, 201):
            log.error(
                "[sendgrid/wa_otp] send failed status=%s body=%s to=%s",
                resp.status_code, resp.text[:300], to_phone_e164,
            )
            raise RuntimeError(f"WhatsApp OTP returned {resp.status_code}")
        log.info("[sendgrid/wa_otp] OTP dispatched to %s", to_phone_e164)
    except httpx.RequestError as exc:
        log.error("[sendgrid/wa_otp] network error to %s: %s", to_phone_e164, exc)
        raise RuntimeError(f"WhatsApp OTP network error: {exc}") from exc


def send_onboarding_otp_email(
    to_email: str,
    otp: str,
    ttl_minutes: int = 10,
    lang: str = "he",
) -> None:
    """Send a 6-digit OTP to a new business owner during onboarding.

    Distinct from send_otp() (which is accountant-portal-only and has a
    60-second copy). Onboarding OTPs have a 10-minute TTL and
    trilingual support (he/ar/en) matching the user's language_pref.

    Args:
        to_email:    Destination email address.
        otp:         6-digit OTP code (server-generated).
        ttl_minutes: OTP lifetime in minutes (default 10).
        lang:        Language code 'he' | 'ar' | 'en'. Default 'he'.
    """
    _COPY = {
        "he": {
            "subject": "קוד האימות שלך ב-Aurora LTS",
            "heading": "קוד האימות שלך",
            "body": f"הזן קוד זה בשלב האימות של ההרשמה ל-Aurora LTS. "
                    f"הקוד תקף ל-<strong style=\"color:#f4f4f5;\">{ttl_minutes} דקות</strong>.",
            "footer": "לא ביקשת קוד זה? אפשר להתעלם ממייל זה בבטחה.",
            "dir": "rtl",
        },
        "ar": {
            "subject": "رمز التحقق الخاص بك في Aurora LTS",
            "heading": "رمز التحقق الخاص بك",
            "body": f"أدخل هذا الرمز في خطوة التحقق من التسجيل في Aurora LTS. "
                    f"الرمز صالح لمدة <strong style=\"color:#f4f4f5;\">{ttl_minutes} دقيقة</strong>.",
            "footer": "لم تطلب هذا الرمز؟ يمكنك تجاهل هذا البريد بأمان.",
            "dir": "rtl",
        },
        "en": {
            "subject": "Your Aurora LTS verification code",
            "heading": "Your verification code",
            "body": f"Enter this code in the Aurora LTS signup flow. "
                    f"It expires in <strong style=\"color:#f4f4f5;\">{ttl_minutes} minutes</strong>.",
            "footer": "If you didn't request this code, you can safely ignore this email.",
            "dir": "ltr",
        },
    }
    copy = _COPY.get(lang, _COPY["he"])

    html = f"""<!DOCTYPE html>
<html lang="{lang}" dir="{copy['dir']}">
<body style="margin:0;padding:40px 0;background:#09090b;font-family:system-ui,sans-serif;">
  <div style="max-width:480px;margin:0 auto;background:#18181b;border:1px solid #27272a;
              border-radius:12px;padding:40px;">
    <div style="margin-bottom:28px;">
      <div style="display:inline-block;background:#4f46e5;border-radius:8px;
                  padding:10px 14px;font-size:20px;font-weight:700;color:#fff;
                  letter-spacing:-0.5px;">A</div>
    </div>
    <h1 style="margin:0 0 8px;font-size:22px;color:#f4f4f5;font-weight:600;">
      {copy['heading']}
    </h1>
    <p style="margin:0 0 28px;color:#a1a1aa;font-size:14px;line-height:1.6;">
      {copy['body']}
    </p>
    <div style="background:#09090b;border:1px solid #27272a;border-radius:8px;
                padding:24px;text-align:center;margin-bottom:28px;">
      <span style="font-size:36px;font-weight:700;letter-spacing:12px;
                   color:#818cf8;font-variant-numeric:tabular-nums;">{otp}</span>
    </div>
    <p style="margin:0;color:#71717a;font-size:12px;line-height:1.6;">
      {copy['footer']}
    </p>
  </div>
</body>
</html>"""

    plain = f"{copy['heading']}: {otp}\n\n({ttl_minutes} min expiry)\n\n{copy['footer']}"
    _send(to_email, copy["subject"], html, plain)


def send_new_device_alert(
    to_email: str,
    accountant_name: str,
    platform: str,
    device_label: str,
    enrolled_at_iso: str,
    revoke_url: str,
) -> None:
    """
    Notify the accountant that a new device signed into their portal.
    Called from accountant_auth._send_new_device_alert() when is_new_device=True.
    """
    platform_label = {"macos": "Mac", "windows": "Windows", "linux": "Linux"}.get(
        platform.lower(), platform
    )
    subject = f"New device signed in to your Aurora portal"
    html = f"""
<!DOCTYPE html>
<html lang="en">
<body style="margin:0;padding:40px 0;background:#09090b;font-family:system-ui,sans-serif;">
  <div style="max-width:480px;margin:0 auto;background:#18181b;border:1px solid #27272a;
              border-radius:12px;padding:40px;">
    <div style="margin-bottom:28px;">
      <div style="display:inline-block;background:#4f46e5;border-radius:8px;
                  padding:10px 14px;font-size:20px;font-weight:700;color:#fff;">A</div>
    </div>
    <h1 style="margin:0 0 8px;font-size:22px;color:#f4f4f5;font-weight:600;">
      New device detected
    </h1>
    <p style="margin:0 0 24px;color:#a1a1aa;font-size:14px;line-height:1.6;">
      Hi {accountant_name}, a new device just signed in to your Aurora LTS portal.
    </p>
    <table style="width:100%;border-collapse:collapse;margin-bottom:28px;">
      <tr>
        <td style="padding:8px 0;color:#71717a;font-size:13px;">Device</td>
        <td style="padding:8px 0;color:#f4f4f5;font-size:13px;text-align:right;">
          {device_label}
        </td>
      </tr>
      <tr style="border-top:1px solid #27272a;">
        <td style="padding:8px 0;color:#71717a;font-size:13px;">Platform</td>
        <td style="padding:8px 0;color:#f4f4f5;font-size:13px;text-align:right;">
          {platform_label}
        </td>
      </tr>
      <tr style="border-top:1px solid #27272a;">
        <td style="padding:8px 0;color:#71717a;font-size:13px;">Time</td>
        <td style="padding:8px 0;color:#f4f4f5;font-size:13px;text-align:right;">
          {enrolled_at_iso}
        </td>
      </tr>
    </table>
    <p style="margin:0 0 20px;color:#a1a1aa;font-size:14px;line-height:1.6;">
      Was this you? If so, no action needed. If not, revoke this device immediately:
    </p>
    <a href="{revoke_url}"
       style="display:inline-block;background:#dc2626;color:#fff;font-size:14px;
              font-weight:600;padding:12px 24px;border-radius:8px;text-decoration:none;">
      Revoke this device
    </a>
    <p style="margin:24px 0 0;color:#52525b;font-size:12px;line-height:1.6;">
      Aurora LTS · Secure Accountant Portal
    </p>
  </div>
</body>
</html>
"""
    plain = (
        f"New device signed in to your Aurora portal.\n\n"
        f"Device: {device_label}\nPlatform: {platform_label}\nTime: {enrolled_at_iso}\n\n"
        f"Not you? Revoke: {revoke_url}"
    )
    _send(to_email, subject, html, plain)
