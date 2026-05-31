"""Aurora LTS — Payment Links Router  (P2-23)"""
from __future__ import annotations
import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.database import get_db
from app.middleware.auth_middleware import get_current_user, get_business_filter
from app.services.payment_links import (
    create_payment_link, resolve_payment_link,
    create_payplus_checkout, handle_payplus_ipn,
)

log = logging.getLogger(__name__)
router = APIRouter(tags=["payment_links"])


class CreateLinkRequest(BaseModel):
    ttl_hours: Optional[int] = None


@router.post("/api/v1/invoices/{invoice_id}/payment-link")
async def create_link(
    invoice_id: int,
    req: CreateLinkRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict:
    biz_filter = get_business_filter(current_user)
    from app.database.models import Invoice
    invoice = db.query(Invoice).filter_by(id=invoice_id).first()
    if not invoice:
        raise HTTPException(404, "Invoice not found")
    if biz_filter is not None and invoice.business_id != biz_filter:
        raise HTTPException(403, "Access denied")
    try:
        return create_payment_link(invoice_id, db, current_user.id, req.ttl_hours)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/pay/{token}", response_class=HTMLResponse)
async def checkout_page(token: str, db: Session = Depends(get_db)) -> HTMLResponse:
    try:
        link = resolve_payment_link(token, db)
    except ValueError as e:
        return HTMLResponse(
            content=_error_html(str(e)),
            status_code=400,
        )
    try:
        checkout = create_payplus_checkout(link, db)
    except Exception as e:
        log.exception("PayPlus checkout creation failed for token=%s", token[:8])
        return HTMLResponse(content=_error_html("Payment processor unavailable"), status_code=503)

    return HTMLResponse(content=_checkout_html(
        invoice_id=link.invoice_id,
        amount_ils=link.amount_ils,
        iframe_url=checkout.get("iframe_url", ""),
        expires_at=link.expires_at.strftime("%d/%m/%Y %H:%M"),
    ))


@router.get("/pay/{token}/success", response_class=HTMLResponse)
async def payment_success(token: str) -> HTMLResponse:
    return HTMLResponse(content=_success_html())


@router.get("/pay/{token}/cancel", response_class=HTMLResponse)
async def payment_cancel(token: str) -> HTMLResponse:
    return HTMLResponse(content=_error_html("התשלום בוטל"))


@router.post("/api/v1/webhooks/payplus-ipn")
async def payplus_ipn(request: Request, db: Session = Depends(get_db)) -> dict:
    payload = await request.json()
    return handle_payplus_ipn(payload, db)


# ─── Minimal Hebrew checkout HTML ────────────────────────────────────────────

def _checkout_html(invoice_id: int, amount_ils: float, iframe_url: str, expires_at: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>תשלום חשבונית #{invoice_id} — Aurora LTS</title>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:system-ui,sans-serif;background:#070912;color:#f4f4f5;min-height:100vh;
          display:flex;align-items:center;justify-content:center;padding:24px}}
    .card{{background:#111;border:1px solid #27272a;border-radius:16px;padding:32px;
           max-width:480px;width:100%}}
    h1{{font-size:1.125rem;font-weight:600;margin-bottom:4px}}
    .amount{{font-size:2rem;font-weight:800;color:#a78bfa;margin:16px 0}}
    .notice{{font-size:.75rem;color:#71717a;margin-top:16px}}
    iframe{{width:100%;min-height:420px;border:0;border-radius:8px;margin-top:20px}}
  </style>
</head>
<body>
  <div class="card">
    <h1>תשלום חשבונית #{invoice_id}</h1>
    <div class="amount">₪{amount_ils:,.2f}</div>
    <p style="font-size:.875rem;color:#a1a1aa">קישור בתוקף עד {expires_at}</p>
    <iframe src="{iframe_url}" title="טופס תשלום מאובטח PayPlus" allow="payment"></iframe>
    <p class="notice">🔒 התשלום מאובטח ומעובד ישירות על-ידי PayPlus. Aurora לא שומרת פרטי כרטיס.</p>
  </div>
</body>
</html>"""


def _success_html() -> str:
    return """<!DOCTYPE html>
<html lang="he" dir="rtl">
<head><meta charset="utf-8"><title>תשלום התקבל</title>
<style>body{font-family:system-ui,sans-serif;background:#070912;color:#f4f4f5;
display:flex;align-items:center;justify-content:center;height:100vh;text-align:center}
h1{font-size:1.5rem;margin-bottom:8px}p{color:#71717a}</style></head>
<body><div><h1>✅ התשלום התקבל בהצלחה</h1><p>תודה! תקבל/י אישור בהודעת WhatsApp.</p></div></body>
</html>"""


def _error_html(message: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="he" dir="rtl">
<head><meta charset="utf-8"><title>שגיאה</title>
<style>body{{font-family:system-ui,sans-serif;background:#070912;color:#f4f4f5;
display:flex;align-items:center;justify-content:center;height:100vh;text-align:center}}
h1{{font-size:1.25rem;margin-bottom:8px;color:#f87171}}p{{color:#71717a}}</style></head>
<body><div><h1>⚠️ {message}</h1><p>אם יש שאלות, פנה/י ישירות לעסק.</p></div></body>
</html>"""
