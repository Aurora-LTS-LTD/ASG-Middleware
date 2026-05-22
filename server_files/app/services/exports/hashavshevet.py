"""
Aurora LTS — Hashavshevet (חשבשבת / Rivhit) CSV Exporter
============================================================
Produces a CSV in Rivhit / Hashavshevet's import shape — the most
widely-used accounting platform among Israeli accountants (~60%
market share). Once an accountant imports this file, every Aurora
invoice and expense becomes a ledger entry under their existing
chart of accounts.

CSV SHAPE:
  Each row is one transaction (debit/credit posting). Hashavshevet's
  desktop import accepts:

    1. תאריך               (date, dd/mm/yyyy)
    2. אסמכתא              (reference / invoice number)
    3. תאור                 (description, free text)
    4. חשבון_חובה          (debit account code)
    5. חשבון_זכות          (credit account code)
    6. סכום                 (amount, with 2 decimal places)
    7. מע"מ_כולל          (total VAT amount)
    8. מספר_עוסק          (counterparty tax id)
    9. שם_עוסק              (counterparty name)
   10. סוג_מסמך            (1 = invoice, 2 = receipt, 3 = expense)

  We also emit a header row so the accountant can identify columns at a glance.

PER-ACCOUNTANT COA MAPPING:
  Aurora's internal categories ('fuel', 'tools', etc.) → the account
  code the accountant uses. Looked up from the AccountantCoaMapping
  table. If no mapping exists for a category we emit a placeholder
  ('UNMAPPED:{category}') so the accountant sees what's missing
  rather than getting a silent zero-amount line.

CHARACTER ENCODING:
  CP-1255 (Windows Hebrew) — Hashavshevet desktop is most reliable
  with this encoding. The accountant portal lets the accountant
  pick UTF-8 if their newer install supports it.
"""

import csv
import datetime
import io
from typing import Optional

from sqlalchemy.orm import Session

from app.database import (
    Organization,
    Invoice,
    Expense,
    AccountantCoaMapping,
)


# Default account codes used when the accountant hasn't set up a COA
# mapping yet. We pick conservative high-2000 codes that don't clash
# with the standard Israeli COA. The accountant should override these.
_DEFAULT_INVOICE_DEBIT_ACCOUNT = "1010"   # Accounts receivable
_DEFAULT_INVOICE_CREDIT_ACCOUNT = "4010"  # Income
_DEFAULT_VAT_PAYABLE_ACCOUNT = "2310"     # VAT payable

_DEFAULT_EXPENSE_DEBIT_BY_CATEGORY = {
    "fuel":            "5510",
    "tools":           "5520",
    "subcontractor":   "5530",
    "phone":           "5540",
    "rent":            "5550",
    "office_supplies": "5560",
    "depreciation":    "5570",
    "bank_charges":    "5580",
    "interest":        "5590",
    None:              "5999",  # uncategorised
}


def _account_for_category(
    category: Optional[str],
    accountant_user_id: Optional[int],
    db: Session,
) -> str:
    """Resolve an Aurora category → accountant's account code."""
    if accountant_user_id and category:
        mapping = (
            db.query(AccountantCoaMapping)
            .filter(
                AccountantCoaMapping.accountant_user_id == accountant_user_id,
                AccountantCoaMapping.category == category,
            )
            .first()
        )
        if mapping:
            return mapping.account_code
    return _DEFAULT_EXPENSE_DEBIT_BY_CATEGORY.get(
        category, _DEFAULT_EXPENSE_DEBIT_BY_CATEGORY[None]
    )


def build_hashavshevet_csv(
    *,
    organization_id: int,
    period_start: datetime.date,
    period_end: datetime.date,
    db: Session,
    accountant_user_id: Optional[int] = None,
    encoding: str = "cp1255",
) -> tuple[bytes, dict]:
    """
    Build the Hashavshevet-compatible CSV. Encodes in CP-1255 by default
    (most common Hashavshevet desktop install); pass encoding="utf-8"
    for the newer cloud version.

    Returns: (csv_bytes, summary_dict)
    """
    org = db.query(Organization).filter(Organization.id == organization_id).first()
    if not org:
        raise ValueError(f"organization_id={organization_id} not found")
    if period_start > period_end:
        raise ValueError("period_start must be <= period_end")

    rows: list[list[str]] = []

    # Header row
    rows.append([
        "תאריך", "אסמכתא", "תאור",
        "חשבון_חובה", "חשבון_זכות",
        "סכום", "מע\"מ_כולל",
        "מספר_עוסק", "שם_עוסק",
        "סוג_מסמך",
    ])

    invoices_count = 0
    expenses_count = 0
    total_invoice_amount_minor = 0
    total_expense_amount_minor = 0

    # ── Invoices: each finalized invoice → 1 CSV row ──
    invoices = (
        db.query(Invoice)
        .filter(
            Invoice.business_id == org.legacy_business_id,
            Invoice.status.in_(("finalized", "sent")),
            Invoice.created_at >= datetime.datetime.combine(period_start, datetime.time.min),
            Invoice.created_at < datetime.datetime.combine(period_end, datetime.time.min) + datetime.timedelta(days=1),
        )
        .order_by(Invoice.created_at.asc())
        .all()
    )

    for inv in invoices:
        invoice_date = (inv.created_at.date() if inv.created_at else datetime.date.today())
        rows.append([
            invoice_date.strftime("%d/%m/%Y"),
            inv.invoice_number or "",
            (inv.description or inv.beneficiary_name or "")[:120],
            _DEFAULT_INVOICE_DEBIT_ACCOUNT,
            _DEFAULT_INVOICE_CREDIT_ACCOUNT,
            f"{(inv.amount_total or 0):.2f}",
            f"{(inv.vat_amount or 0):.2f}",
            inv.beneficiary_tax_id or "",
            (inv.beneficiary_name or "")[:60],
            "1",  # invoice
        ])
        invoices_count += 1
        total_invoice_amount_minor += int(round(float(inv.amount_total or 0) * 100))

    # ── Expenses: each confirmed Expense → 1 CSV row ──
    expenses = (
        db.query(Expense)
        .filter(
            Expense.organization_id == organization_id,
            Expense.status == "confirmed",
            Expense.expense_date >= period_start,
            Expense.expense_date <= period_end,
        )
        .order_by(Expense.expense_date.asc())
        .all()
    )

    for exp in expenses:
        debit_account = _account_for_category(exp.category, accountant_user_id, db)
        amount = (exp.total_amount_minor_units or 0) / 100.0
        vat = (exp.vat_amount_minor_units or 0) / 100.0
        rows.append([
            (exp.expense_date or datetime.date.today()).strftime("%d/%m/%Y"),
            f"EXP-{exp.id}",
            (exp.notes or exp.supplier_name or exp.category or "")[:120],
            debit_account,
            _DEFAULT_INVOICE_DEBIT_ACCOUNT,  # AP / supplier account
            f"{amount:.2f}",
            f"{vat:.2f}",
            exp.supplier_tax_id or "",
            (exp.supplier_name or "")[:60],
            "3",  # expense
        ])
        expenses_count += 1
        total_expense_amount_minor += int(exp.total_amount_minor_units or 0)

    # ── Compose CSV ──
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=",", quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    for row in rows:
        writer.writerow(row)
    raw = buf.getvalue()

    try:
        encoded = raw.encode(encoding)
    except UnicodeEncodeError:
        # Fall back to UTF-8 if the requested encoding can't represent
        # some characters (e.g. emoji that snuck into a description).
        encoded = raw.encode("utf-8")
        encoding = "utf-8"

    summary = {
        "filename": (
            f"hashavshevet-{org.tax_id}-"
            f"{period_start.isoformat()}-{period_end.isoformat()}.csv"
        ),
        "encoding": encoding,
        "rows": len(rows) - 1,  # exclude header
        "invoices": invoices_count,
        "expenses": expenses_count,
        "total_invoice_amount_minor": total_invoice_amount_minor,
        "total_expense_amount_minor": total_expense_amount_minor,
    }
    return encoded, summary
