"""
Aurora LTS — Uniform File (מבנה אחיד / OpenFormat 1.31) Writer
==================================================================
Produces an ITA-compliant Uniform File zip:
    INI.TXT       — header: taxpayer + period + record counts
    BKMVDATA.TXT  — body: one fixed-width line per record

REFERENCES:
  Israel Tax Authority technical manual for OpenFormat 1.31 (2026)
  https://www.gov.il/he/Departments/General/openformat
  Software-house documentation provided at certification time.

THIS IS A WORKING SUBSET — it produces a structurally-valid Uniform
File for the most common record types Aurora generates today:
  A100  — opening / sender summary (one per file)
  C100  — invoice header
  D110  — invoice line items (1 per invoice for now)
  B100  — accounting transactions (a 2-leg posting per invoice)
  Z900  — file footer / totals (one per file)

We do NOT yet ship: M100/M101 inventory records, G100 stock movements,
B110 ledger details. Those are out of scope until tenants with
inventory-bearing businesses onboard (post Sprint 6).

REAL-WORLD ANALOGY:
  Imagine the ITA wants every business to send its books in a SINGLE
  agreed-upon plain-text format — like a tax-shaped envelope. The
  Uniform File is that envelope. Aurora generates one on demand.

CHARACTER ENCODING:
  ITA accepts CP-1255 (Windows Hebrew) OR UTF-8. We use UTF-8 with a
  BOM so any consumer (including Hashavshevet's import tool) reads
  Hebrew correctly.

LINE ENDING:
  CRLF (Windows-style) per ITA spec.
"""

import datetime
import io
import zipfile
from typing import List, Tuple

from sqlalchemy.orm import Session

from aurora_shared.database import (
    Organization,
    Invoice,
    Payment,
    Expense,
)


# ─────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────
LINE_TERMINATOR = "\r\n"
ENCODING = "utf-8-sig"  # UTF-8 with BOM


def _pad(value: str, length: int, *, align: str = "left") -> str:
    """Pad/truncate to fixed width. align='left' (default) or 'right'."""
    s = str(value or "")
    if len(s) > length:
        return s[:length]
    return s.rjust(length) if align == "right" else s.ljust(length)


def _digits(n, length: int) -> str:
    """Right-aligned digits, zero-padded. For numeric ITA fields."""
    return str(int(n or 0)).rjust(length, "0")[-length:]


def _money(value_minor_units: int, length: int) -> str:
    """
    ITA money fields: integer agorot, right-aligned, zero-padded, length wide.
    e.g. ₪123.45 = 12345 → "00000012345" (length=11).
    """
    if value_minor_units is None:
        value_minor_units = 0
    return str(int(value_minor_units)).rjust(length, "0")[-length:]


def _money_from_float(value: float, length: int) -> str:
    """Convert ₪ float (e.g. 123.45) → minor-unit fixed-width string."""
    if value is None:
        value = 0
    return _money(int(round(float(value) * 100)), length)


def _date(d: datetime.date) -> str:
    """ITA date format: YYYYMMDD."""
    if not d:
        d = datetime.date.today()
    return d.strftime("%Y%m%d")


def _now_hhmmss() -> str:
    return datetime.datetime.utcnow().strftime("%H%M%S")


# ─────────────────────────────────────────────────────────────
# Record builders
# ─────────────────────────────────────────────────────────────
# Each builder returns ONE LINE (no terminator) per the ITA spec.
# We concatenate with LINE_TERMINATOR at write time.
#
# For brevity each builder uses an "abbreviated" subset of the full
# OpenFormat record — fields beyond what Aurora fills today are
# left blank-padded. This is INTENTIONAL: the validator accepts
# blanks for fields that are "not applicable to the period".
# ─────────────────────────────────────────────────────────────


def _build_a100(*, org: Organization, period_start: datetime.date,
                period_end: datetime.date, software_house_id: str,
                primary_id: int) -> str:
    """
    A100 — opening record. One per file.
      pos  1– 4 : record type "A100"
      pos  5–13 : taxpayer Tax ID (right-padded)
      pos 14–18 : record sequence (zero-padded)
      pos 19–26 : period start (YYYYMMDD)
      pos 27–34 : period end   (YYYYMMDD)
      pos 35–42 : file generation date (YYYYMMDD)
      pos 43–48 : file generation time (HHMMSS)
      pos 49–63 : ASG software-house ID (15 chars)
      pos 64–73 : software registration ID (10 chars)
      pos 74–88 : software version (15 chars)
      pos 89–101: tax_id_no (the same taxpayer ID — ITA quirk)
    """
    parts = [
        "A100",
        _pad(org.tax_id, 9),
        _digits(primary_id, 5),
        _date(period_start),
        _date(period_end),
        _date(datetime.date.today()),
        _now_hhmmss(),
        _pad(software_house_id or "AURORA-LTS-001", 15),
        _pad("AURORA-001", 10),
        _pad("3.0.0-aurora", 15),
        _pad(org.tax_id, 9),
    ]
    return "".join(parts)


def _build_c100(*, invoice: Invoice, org: Organization, sequence: int) -> str:
    """
    C100 — invoice header.
      pos  1– 4  : "C100"
      pos  5–13  : taxpayer tax id
      pos 14–18  : sequence
      pos 19–28  : invoice number (10 chars)
      pos 29–29  : document type (1 = tax invoice)
      pos 30–37  : invoice date YYYYMMDD
      pos 38–45  : value date YYYYMMDD (= due_date or invoice_date)
      pos 46–55  : counterparty tax id
      pos 56–105 : counterparty name (50 chars)
      pos 106–127: address line (22 chars, padded)
      pos 128–139: invoice subtotal (12 digits, agorot)
      pos 140–151: VAT amount (12 digits, agorot)
      pos 152–163: invoice total (12 digits, agorot)
      pos 164–173: allocation_number (10 chars; blank if not required)
    """
    invoice_date = invoice.created_at.date() if invoice.created_at else datetime.date.today()
    value_date = invoice.due_date.date() if invoice.due_date else invoice_date

    parts = [
        "C100",
        _pad(org.tax_id, 9),
        _digits(sequence, 5),
        _pad(invoice.invoice_number, 10),
        "1",  # document type 1 = tax invoice
        _date(invoice_date),
        _date(value_date),
        _pad(invoice.beneficiary_tax_id or "", 10),
        _pad(invoice.beneficiary_name or "", 50),
        _pad("", 22),  # address line — out of scope for first export
        _money_from_float(invoice.amount_net or 0, 12),
        _money_from_float(invoice.vat_amount or 0, 12),
        _money_from_float(invoice.amount_total or 0, 12),
        _pad(invoice.allocation_number or "", 10),
    ]
    return "".join(parts)


def _build_d110(*, invoice: Invoice, org: Organization, sequence: int) -> str:
    """
    D110 — invoice line item. One row per item; Aurora's invoices today
    are single-line, so we emit one D110 per C100.
      pos  1– 4  : "D110"
      pos  5–13  : taxpayer tax id
      pos 14–18  : sequence (continues from C100 line)
      pos 19–28  : invoice number
      pos 29–32  : line number (0001)
      pos 33–82  : item description (50 chars)
      pos 83– 92 : quantity (10 chars; we use 1)
      pos 93–104 : unit price (12 digits agorot — the net amount)
      pos 105–116: line total (12 digits agorot)
      pos 117–119: VAT rate × 100 (e.g. 1800 for 18.00%)
    """
    description = (invoice.description or invoice.beneficiary_name or "Service")[:50]
    parts = [
        "D110",
        _pad(org.tax_id, 9),
        _digits(sequence, 5),
        _pad(invoice.invoice_number, 10),
        "0001",
        _pad(description, 50),
        _digits(1, 10),  # quantity
        _money_from_float(invoice.amount_net or 0, 12),
        _money_from_float(invoice.amount_total or 0, 12),
        _digits(int((invoice.vat_rate or 0.18) * 10000), 3),
    ]
    return "".join(parts)


def _build_b100(*, invoice: Invoice, org: Organization, sequence: int) -> str:
    """
    B100 — accounting transaction (debit/credit posting).
      pos  1– 4  : "B100"
      pos  5–13  : taxpayer tax id
      pos 14–18  : sequence
      pos 19–28  : transaction reference (= invoice number)
      pos 29–36  : transaction date
      pos 37–48  : amount (12 digits agorot)
      pos 49     : sign (+/-)
      pos 50–58  : counterparty tax id
      pos 59–98  : counterparty name (40 chars)
    """
    invoice_date = invoice.created_at.date() if invoice.created_at else datetime.date.today()
    parts = [
        "B100",
        _pad(org.tax_id, 9),
        _digits(sequence, 5),
        _pad(invoice.invoice_number, 10),
        _date(invoice_date),
        _money_from_float(invoice.amount_total or 0, 12),
        "+",  # accounts-receivable: positive
        _pad(invoice.beneficiary_tax_id or "", 9),
        _pad(invoice.beneficiary_name or "", 40),
    ]
    return "".join(parts)


def _build_z900(*, org: Organization, totals: dict, primary_id: int) -> str:
    """
    Z900 — closing record / file totals. Last line in BKMVDATA.TXT.
      pos  1– 4  : "Z900"
      pos  5–13  : taxpayer tax id
      pos 14–18  : sequence (== last sequence)
      pos 19–33  : total record count (15 digits)
      pos 34–48  : total invoice amount (15 digits agorot)
      pos 49–63  : total VAT amount (15 digits agorot)
      pos 64–73  : ASG software-house ID (10 chars padded)
    """
    parts = [
        "Z900",
        _pad(org.tax_id, 9),
        _digits(primary_id, 5),
        _digits(totals["records"], 15),
        _money(totals["total_amount_minor"], 15),
        _money(totals["total_vat_minor"], 15),
        _pad("AURORA-001", 10),
    ]
    return "".join(parts)


def _build_ini(*, org: Organization, period_start: datetime.date,
               period_end: datetime.date, totals: dict,
               software_house_id: str) -> str:
    """
    INI.TXT — header file.

    Format: each line is "<record_type><record_count>\r\n" with the
    overall summary on its own. The first line is the file's tax_id
    + name + period + total record count.
    """
    lines = []
    # Line 1: tax_id + name + period start + period end + total count + generation date
    summary = (
        f"INI {_pad(org.tax_id, 9)} "
        f"{_pad((org.display_name or '')[:30], 30)} "
        f"{_date(period_start)} {_date(period_end)} "
        f"{_digits(totals['records'], 7)} "
        f"{_date(datetime.date.today())} {_pad(software_house_id or 'AURORA-LTS-001', 15)}"
    )
    lines.append(summary)

    # One line per record-type with its count
    for rtype, count in [
        ("A100", totals["A100"]),
        ("C100", totals["C100"]),
        ("D110", totals["D110"]),
        ("B100", totals["B100"]),
        ("Z900", totals["Z900"]),
    ]:
        lines.append(f"{rtype} {_digits(count, 7)}")

    return LINE_TERMINATOR.join(lines) + LINE_TERMINATOR


# ─────────────────────────────────────────────────────────────
# Public API — build_uniform_file
# ─────────────────────────────────────────────────────────────
def build_uniform_file(
    *,
    organization_id: int,
    period_start: datetime.date,
    period_end: datetime.date,
    db: Session,
    software_house_id: str = "",
) -> Tuple[bytes, dict]:
    """
    Build the Uniform File for an organization × period.

    Returns:
        (zip_bytes, summary_dict)

        summary_dict = {
          "records": int,           # total records (excluding INI header)
          "invoices": int,
          "total_amount_minor": int,
          "total_vat_minor": int,
          "filename": "uniform-file-{tax_id}-{period}.zip",
          "encoding": "utf-8-sig",
        }

    Raises ValueError on missing org or empty period.
    """
    org = db.query(Organization).filter(Organization.id == organization_id).first()
    if not org:
        raise ValueError(f"organization_id={organization_id} not found")
    if period_start > period_end:
        raise ValueError("period_start must be <= period_end")

    # ── Pull invoices + map to per-invoice C100/D110/B100 lines ──
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

    bkmvdata_lines: List[str] = []
    sequence = 1

    # A100 first
    bkmvdata_lines.append(_build_a100(
        org=org, period_start=period_start, period_end=period_end,
        software_house_id=software_house_id, primary_id=sequence,
    ))
    counts = {"A100": 1, "C100": 0, "D110": 0, "B100": 0, "Z900": 0}
    total_amount_minor = 0
    total_vat_minor = 0

    for invoice in invoices:
        sequence += 1
        bkmvdata_lines.append(_build_c100(invoice=invoice, org=org, sequence=sequence))
        counts["C100"] += 1
        sequence += 1
        bkmvdata_lines.append(_build_d110(invoice=invoice, org=org, sequence=sequence))
        counts["D110"] += 1
        sequence += 1
        bkmvdata_lines.append(_build_b100(invoice=invoice, org=org, sequence=sequence))
        counts["B100"] += 1

        total_amount_minor += int(round(float(invoice.amount_total or 0) * 100))
        total_vat_minor += int(round(float(invoice.vat_amount or 0) * 100))

    # Z900 last
    sequence += 1
    counts["Z900"] = 1
    totals = {
        "records": sum(counts.values()),
        "A100": counts["A100"], "C100": counts["C100"],
        "D110": counts["D110"], "B100": counts["B100"], "Z900": counts["Z900"],
        "total_amount_minor": total_amount_minor,
        "total_vat_minor": total_vat_minor,
    }
    bkmvdata_lines.append(_build_z900(org=org, totals=totals, primary_id=sequence))

    # ── Compose INI.TXT + BKMVDATA.TXT into a zip ──
    bkmvdata = LINE_TERMINATOR.join(bkmvdata_lines) + LINE_TERMINATOR
    ini = _build_ini(
        org=org,
        period_start=period_start, period_end=period_end,
        totals=totals,
        software_house_id=software_house_id,
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("INI.TXT", ini.encode(ENCODING))
        zf.writestr("BKMVDATA.TXT", bkmvdata.encode(ENCODING))
    zip_bytes = buf.getvalue()

    summary = {
        **totals,
        "invoices": counts["C100"],
        "filename": (
            f"uniform-file-{org.tax_id}-"
            f"{period_start.isoformat()}-{period_end.isoformat()}.zip"
        ),
        "encoding": ENCODING,
        "ini_size_bytes": len(ini.encode(ENCODING)),
        "bkmvdata_size_bytes": len(bkmvdata.encode(ENCODING)),
    }
    return zip_bytes, summary
