"""
Aurora LTS — VAT Return Filing Service  (P2-22)
=================================================

Automates the Israeli bi-monthly מע"מ (VAT) return filing via the
Israeli Tax Authority's (רשות המסים) API.

BACKGROUND
──────────
In Israel, businesses registered as עוסק מורשה (osek morshe) must file
VAT returns every 2 months (bi-monthly periods):
  Period 1: January + February   → due by 15 March
  Period 2: March + April        → due by 15 May
  Period 3: May + June           → due by 15 July
  Period 4: July + August        → due by 15 September
  Period 5: September + October  → due by 15 November
  Period 6: November + December  → due by 15 January

Small businesses (< ₪1.5M annual turnover) may file quarterly.

THE FILING CONTAINS:
  • Total taxable sales (עסקאות חייבות) for the period
  • Total VAT collected from customers (מע"מ על עסקאות)
  • Total input VAT paid to suppliers (מע"מ תשומות)
  • Net VAT payable / refundable
  • Number of tax invoices issued (for cross-reference)

IMPLEMENTATION
──────────────
  VAT_FILING_BACKEND=stub         No-op, honours FORCE_VAT_FILING_ERROR=1
  VAT_FILING_BACKEND=production   Real ITA API call (same JWT auth as allocation)

API ENDPOINT (ITA מע"מ):
  The ITA provides the פורמט 878 (Form 878) API for electronic filing.
  Base URL: ITA_VAT_API_BASE (env)
  Path:     ITA_VAT_FILING_PATH (env, default: /vat/v1/submit)

AUTOMATION FLOW:
  Cloud Scheduler triggers POST /api/v1/vat/prepare-return on the 1st
  of each filing-due month, which:
    1. Calculates the period (previous 2 months)
    2. Aggregates all invoices and expenses for that period
    3. Creates a VatReturn row in status='draft'
  The admin reviews the draft and POSTs /api/v1/vat/returns/{id}/submit.
  The service then calls ITA and stores the confirmation number.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import ActionLog
from app.database.models import VatReturn, Business

log = logging.getLogger(__name__)

VAT_RATE = 0.18  # 18%
QUARTERLY_THRESHOLD_ILS = 1_500_000  # annual turnover below which quarterly is allowed


# ─────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────

def _backend() -> str:
    return (os.getenv("VAT_FILING_BACKEND") or "stub").strip().lower()


def _ita_vat_base() -> str:
    return os.getenv("ITA_VAT_API_BASE", "https://openapi.taxes.gov.il/shaam")


def _ita_vat_path() -> str:
    return os.getenv("ITA_VAT_FILING_PATH", "/taxpayers/v1/vatReturns")


# ─────────────────────────────────────────────────────────────
# Period helpers
# ─────────────────────────────────────────────────────────────

@dataclass
class VatPeriod:
    year: int
    period_number: int       # 1–6 (bi-monthly) or 1–4 (quarterly)
    frequency: str           # "bimonthly" | "quarterly"
    start_date: datetime.date
    end_date: datetime.date
    due_date: datetime.date


def current_period(reference_date: Optional[datetime.date] = None) -> VatPeriod:
    """Return the VAT period that covers `reference_date` (default: today)."""
    d = reference_date or datetime.date.today()
    return _period_for_date(d, frequency="bimonthly")


def _period_for_date(d: datetime.date, frequency: str = "bimonthly") -> VatPeriod:
    if frequency == "bimonthly":
        # Periods: Jan-Feb=1, Mar-Apr=2, May-Jun=3, Jul-Aug=4, Sep-Oct=5, Nov-Dec=6
        month_pair = (d.month - 1) // 2  # 0–5
        period_number = month_pair + 1
        start_month = month_pair * 2 + 1
        end_month = start_month + 1
        due_month = end_month + 1 if end_month < 12 else 1
        due_year = d.year if end_month < 12 else d.year + 1
        return VatPeriod(
            year=d.year,
            period_number=period_number,
            frequency="bimonthly",
            start_date=datetime.date(d.year, start_month, 1),
            end_date=_last_day(d.year, end_month),
            due_date=datetime.date(due_year, due_month, 15),
        )
    else:
        # Quarterly: Q1=Jan-Mar, Q2=Apr-Jun, Q3=Jul-Sep, Q4=Oct-Dec
        quarter = (d.month - 1) // 3
        start_month = quarter * 3 + 1
        end_month = start_month + 2
        due_month = end_month + 1 if end_month < 12 else 1
        due_year = d.year if end_month < 12 else d.year + 1
        return VatPeriod(
            year=d.year,
            period_number=quarter + 1,
            frequency="quarterly",
            start_date=datetime.date(d.year, start_month, 1),
            end_date=_last_day(d.year, end_month),
            due_date=datetime.date(due_year, due_month, 15),
        )


def _last_day(year: int, month: int) -> datetime.date:
    if month == 12:
        return datetime.date(year, 12, 31)
    return datetime.date(year, month + 1, 1) - datetime.timedelta(days=1)


# ─────────────────────────────────────────────────────────────
# VAT aggregation
# ─────────────────────────────────────────────────────────────

@dataclass
class VatAggregation:
    business_id: int
    period: VatPeriod

    # Sales side (outputs)
    total_taxable_sales_ils: float = 0.0      # net amount ex-VAT
    total_vat_collected_ils: float = 0.0      # 18% on taxable sales
    total_exempt_sales_ils: float = 0.0       # zero-rated / exempt
    invoice_count: int = 0

    # Purchase side (inputs)
    total_taxable_purchases_ils: float = 0.0
    total_input_vat_ils: float = 0.0          # VAT paid to suppliers
    expense_receipt_count: int = 0

    # Net
    @property
    def net_vat_payable_ils(self) -> float:
        return round(self.total_vat_collected_ils - self.total_input_vat_ils, 2)

    @property
    def is_refund(self) -> bool:
        return self.net_vat_payable_ils < 0


def aggregate_period(
    business_id: int,
    period: VatPeriod,
    db: Session,
) -> VatAggregation:
    """
    Aggregate invoices + expenses for the given period from the database.
    """
    agg = VatAggregation(business_id=business_id, period=period)

    # ── Sales: finalized invoices ──────────────────────────────
    inv_sql = text("""
        SELECT
            COUNT(*)                       AS cnt,
            COALESCE(SUM(amount_before_vat), 0) AS net_sales,
            COALESCE(SUM(vat_amount), 0)        AS vat_collected,
            COALESCE(SUM(CASE WHEN is_vat_exempt THEN total_amount ELSE 0 END), 0) AS exempt_sales
        FROM invoices
        WHERE business_id = :biz_id
          AND status IN ('finalized', 'sent', 'paid')
          AND issue_date BETWEEN :start AND :end
    """)
    row = db.execute(inv_sql, {
        "biz_id": business_id,
        "start": period.start_date,
        "end": period.end_date,
    }).fetchone()
    if row:
        agg.invoice_count = int(row.cnt or 0)
        agg.total_taxable_sales_ils = float(row.net_sales or 0)
        agg.total_vat_collected_ils = float(row.vat_collected or 0)
        agg.total_exempt_sales_ils = float(row.exempt_sales or 0)

    # ── Purchases: classified expenses with VAT ────────────────
    exp_sql = text("""
        SELECT
            COUNT(*)                          AS cnt,
            COALESCE(SUM(amount_ils), 0)      AS gross_purchases,
            COALESCE(SUM(vat_amount_ils), 0)  AS input_vat
        FROM expenses
        WHERE business_id = :biz_id
          AND expense_date BETWEEN :start AND :end
          AND receipt_status = 'approved'
    """)
    exp_row = db.execute(exp_sql, {
        "biz_id": business_id,
        "start": period.start_date,
        "end": period.end_date,
    }).fetchone()
    if exp_row:
        agg.expense_receipt_count = int(exp_row.cnt or 0)
        gross = float(exp_row.gross_purchases or 0)
        input_vat = float(exp_row.input_vat or 0)
        agg.total_input_vat_ils = input_vat
        agg.total_taxable_purchases_ils = gross - input_vat

    return agg


# ─────────────────────────────────────────────────────────────
# Filing
# ─────────────────────────────────────────────────────────────

@dataclass
class FilingResult:
    success: bool
    confirmation_number: Optional[str] = None
    message: str = ""
    backend: str = "stub"
    http_status: Optional[int] = None
    latency_ms: int = 0
    raw_response: Optional[str] = None


def prepare_return(
    business_id: int,
    period: Optional[VatPeriod] = None,
    db: Session = None,
) -> VatReturn:
    """
    Step 1: Aggregate the period's data and create a draft VatReturn.
    Returns the VatReturn ORM row (status='draft').
    """
    if period is None:
        period = current_period()

    agg = aggregate_period(business_id, period, db)
    biz = db.query(Business).filter_by(id=business_id).first()
    if not biz:
        raise ValueError(f"Business {business_id} not found")

    vat_return = VatReturn(
        business_id=business_id,
        tax_id=biz.tax_id,
        period_year=period.year,
        period_number=period.period_number,
        period_frequency=period.frequency,
        period_start=period.start_date,
        period_end=period.end_date,
        due_date=period.due_date,
        taxable_sales_ils=agg.total_taxable_sales_ils,
        vat_collected_ils=agg.total_vat_collected_ils,
        exempt_sales_ils=agg.total_exempt_sales_ils,
        taxable_purchases_ils=agg.total_taxable_purchases_ils,
        input_vat_ils=agg.total_input_vat_ils,
        net_vat_payable_ils=agg.net_vat_payable_ils,
        invoice_count=agg.invoice_count,
        expense_count=agg.expense_receipt_count,
        status="draft",
    )
    db.add(vat_return)
    db.add(ActionLog(
        business_id=business_id,
        status="vat.return.prepared",
        detail=(
            f"period={period.year}-{period.period_number} "
            f"net_payable={agg.net_vat_payable_ils:.2f} "
            f"invoices={agg.invoice_count}"
        ),
    ))
    db.commit()
    db.refresh(vat_return)
    return vat_return


def submit_return(
    vat_return_id: int,
    db: Session,
    submitted_by_user_id: int,
) -> FilingResult:
    """
    Step 2: Submit a draft VatReturn to the ITA.
    Updates the row to status='submitted' with the confirmation number.
    """
    vat_return = db.query(VatReturn).filter_by(id=vat_return_id).first()
    if not vat_return:
        raise ValueError(f"VatReturn {vat_return_id} not found")
    if vat_return.status not in ("draft", "rejected"):
        raise ValueError(f"VatReturn {vat_return_id} is in status '{vat_return.status}', cannot resubmit")

    backend = _backend()

    if backend == "stub":
        if os.getenv("FORCE_VAT_FILING_ERROR", "").lower() in ("1", "true"):
            result = FilingResult(
                success=False, message="FORCE_VAT_FILING_ERROR active", backend="stub"
            )
        else:
            result = FilingResult(
                success=True,
                confirmation_number=f"STUB-{vat_return_id:06d}",
                message="Stub submission (VAT_FILING_BACKEND=stub)",
                backend="stub",
            )
    elif backend == "production":
        result = _submit_to_ita(vat_return, db)
    else:
        raise ValueError(f"Unknown VAT_FILING_BACKEND='{backend}'")

    # Update VatReturn row
    if result.success:
        vat_return.status = "submitted"
        vat_return.confirmation_number = result.confirmation_number
        vat_return.submitted_at = datetime.datetime.utcnow()
        vat_return.submitted_by_user_id = submitted_by_user_id
    else:
        vat_return.status = "rejected"
        vat_return.rejection_reason = result.message

    db.add(ActionLog(
        business_id=vat_return.business_id,
        status=f"vat.return.{'submitted' if result.success else 'rejected'}",
        detail=(
            f"vat_return_id={vat_return_id} "
            f"confirmation={result.confirmation_number} "
            f"backend={result.backend} message={result.message!r}"
        ),
    ))
    db.commit()
    return result


def _submit_to_ita(vat_return: VatReturn, db: Session) -> FilingResult:
    """
    Submit the VAT return to the ITA API using JWT-signed auth
    (same JWT signing mechanism as the allocation API).
    """
    try:
        import httpx  # type: ignore
    except ImportError:
        return FilingResult(
            success=False, message="httpx not installed", backend="production"
        )

    from app.services.ita.auth import build_ita_jwt  # Reuse existing JWT builder

    url = _ita_vat_base() + _ita_vat_path()
    payload = {
        "taxId": vat_return.tax_id,
        "periodYear": vat_return.period_year,
        "periodNumber": vat_return.period_number,
        "periodType": "BI_MONTHLY" if vat_return.period_frequency == "bimonthly" else "QUARTERLY",
        "taxableSalesAmount": round(vat_return.taxable_sales_ils * 100),  # agora
        "vatOnSales": round(vat_return.vat_collected_ils * 100),
        "exemptSalesAmount": round(vat_return.exempt_sales_ils * 100),
        "taxablePurchasesAmount": round(vat_return.taxable_purchases_ils * 100),
        "inputVat": round(vat_return.input_vat_ils * 100),
        "netVat": round(vat_return.net_vat_payable_ils * 100),
        "invoiceCount": vat_return.invoice_count,
    }

    jwt_token = build_ita_jwt()
    headers = {
        "Authorization": f"Bearer {jwt_token}",
        "Content-Type": "application/json",
        "X-Aurora-Request-Id": hashlib.sha256(
            f"{vat_return.id}{time.time()}".encode()
        ).hexdigest()[:16],
    }

    t0 = time.monotonic()
    try:
        response = httpx.post(
            url, json=payload, headers=headers,
            timeout=int(os.getenv("ITA_TIMEOUT_SECONDS", "15")),
        )
        latency_ms = int((time.monotonic() - t0) * 1000)
        body = response.text

        if response.status_code in (200, 201):
            data = response.json()
            return FilingResult(
                success=True,
                confirmation_number=str(data.get("confirmationNumber") or data.get("vatReturnId", "")),
                message="Filed successfully",
                backend="production",
                http_status=response.status_code,
                latency_ms=latency_ms,
                raw_response=body[:500],
            )
        else:
            return FilingResult(
                success=False,
                message=f"ITA rejected filing: HTTP {response.status_code} — {body[:200]}",
                backend="production",
                http_status=response.status_code,
                latency_ms=latency_ms,
                raw_response=body[:500],
            )
    except Exception as e:
        return FilingResult(
            success=False,
            message=f"ITA request failed: {type(e).__name__}: {str(e)[:200]}",
            backend="production",
            latency_ms=int((time.monotonic() - t0) * 1000),
        )
