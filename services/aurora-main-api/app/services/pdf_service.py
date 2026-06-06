"""
ASG Solutions -- PDF Service
================================
Generates professional bilingual tax invoices as PDF documents.
Supports Arabic (RTL), Hebrew (RTL), and English (LTR).

HOW IT WORKS:
  1. Load the Jinja2 HTML template (app/templates/invoice_template.html)
  2. Build a QR code that links to the invoice URL
  3. Fill the template with invoice + business data + translations
  4. Pass the rendered HTML to WeasyPrint
  5. WeasyPrint uses Cairo + Pango under the hood:
       - Pango handles Arabic/Hebrew text shaping
         (letter joining, right-to-left flow)
       - Cairo renders the shaped text to PDF
  6. Save to disk, return the URL path

TAX COMPLIANCE NOTE:
  The PDF reads numbers DIRECTLY from the Invoice database record.
  It never recalculates VAT or thresholds. The 18% VAT, allocation
  number, and all amounts are already locked at finalization time.
  This service is READ-ONLY with respect to tax data.

REAL-WORLD ANALOGY:
  Think of this as a professional print shop:
  - You hand them the invoice (data)
  - They print it on letterhead (template)
  - They stamp the official tax number on it (allocation)
  - They put a QR code on it for verification
  - You get back a ready-to-send PDF document
"""

# -----------------------------------------------------------------
# IMPORTS
# -----------------------------------------------------------------
import os
import io
import base64
import datetime

import qrcode
from jinja2 import Environment, FileSystemLoader
# weasyprint is imported lazily inside generate_invoice_pdf() so the server
# can start even on machines without the system Pango/Cairo libraries
# (development Mac). On the Alienware (Parrot OS) those libs are present.


# -----------------------------------------------------------------
# CONFIGURATION
# -----------------------------------------------------------------
# Where templates live and where PDFs are saved.
TEMPLATE_DIR = "app/templates"


def _default_pdf_dir() -> str:
    """
    Cloud Run filesystems are read-only except /tmp. The same image
    runs on Mac dev boxes (where app/static/pdfs is writable) and on
    Cloud Run (where it isn't). We honour AURORA_RUNTIME=cloud_run
    to flip the default to /tmp/aurora/pdfs.
    """
    if os.getenv("AURORA_RUNTIME", "").lower() == "cloud_run":
        return "/tmp/aurora/pdfs"
    return "app/static/pdfs"


# Explicit override wins; otherwise pick the right default for runtime.
PDF_OUTPUT_DIR = os.getenv("PDF_STORAGE_PATH") or _default_pdf_dir()

# Absolute path to fonts (used in CSS @font-face)
FONT_DIR = os.path.abspath("app/static/fonts")
FONT_ARABIC = os.path.join(FONT_DIR, "NotoSansArabic-Regular.ttf")
FONT_HEBREW = os.path.join(FONT_DIR, "NotoSansHebrew-Regular.ttf")

# Server base URL for QR codes (used to generate verify links).
# Defaults to the production Aurora domain. The Alienware dev URL
# is overridable via SERVER_BASE_URL env in the dev .env.
SERVER_BASE_URL = os.getenv("SERVER_BASE_URL", "https://aurora-ltd.co.il")


# -----------------------------------------------------------------
# TRANSLATION TABLE
# -----------------------------------------------------------------
# Every UI label in the invoice template has a translation here.
# When Phase 4 (AI Bot) adds language detection, it will call
# pdf_service with lang="ar", "he", or "en" automatically.
LABELS = {
    "ar": {
        "invoice_title":    "فاتورة ضريبية",
        "invoice_number":   "رقم الفاتورة",
        "date_label":       "التاريخ",
        "due_date_label":   "تاريخ الاستحقاق",
        "to_label":         "إلى",
        "tax_id_label":     "الرقم الضريبي",
        "item_label":       "البيان",
        "amount_label":     "المبلغ",
        "subtotal_label":   "المبلغ قبل الضريبة",
        "vat_label":        "ضريبة القيمة المضافة",
        "total_label":      "المجموع الكلي",
        "allocation_label": "رقم تخصيص ضريبة القيمة المضافة",
        "no_allocation_label": "لا يتطلب رقم تخصيص (المبلغ دون الحد الأدنى)",
        "paid_label":       "المدفوع",
        "remaining_label":  "المتبقي",
        "status_label":     "الحالة",
        "generated_label":  "تاريخ الإصدار",
    },
    "he": {
        "invoice_title":    'חשבונית מס',
        "invoice_number":   'מספר חשבונית',
        "date_label":       'תאריך',
        "due_date_label":   'תאריך פירעון',
        "to_label":         'לכבוד',
        "tax_id_label":     'ח.פ / ע.מ',
        "item_label":       'פריט',
        "amount_label":     'סכום',
        "subtotal_label":   'סכום לפני מע"מ',
        "vat_label":        'מע"מ',
        "total_label":      'סה"כ לתשלום',
        "allocation_label": 'מספר הקצאה',
        "no_allocation_label": 'אינו מחייב מספר הקצאה (מתחת לסף)',
        "paid_label":       'שולם',
        "remaining_label":  'יתרה',
        "status_label":     'סטטוס',
        "generated_label":  'הופק בתאריך',
    },
    "en": {
        "invoice_title":    "Tax Invoice",
        "invoice_number":   "Invoice #",
        "date_label":       "Date",
        "due_date_label":   "Due Date",
        "to_label":         "Bill To",
        "tax_id_label":     "Tax ID",
        "item_label":       "Description",
        "amount_label":     "Amount",
        "subtotal_label":   "Subtotal (before VAT)",
        "vat_label":        "VAT",
        "total_label":      "Total",
        "allocation_label": "ITA Allocation Number",
        "no_allocation_label": "No allocation required (below threshold)",
        "paid_label":       "Paid",
        "remaining_label":  "Remaining",
        "status_label":     "Status",
        "generated_label":  "Generated",
    },
}


# -----------------------------------------------------------------
# FUNCTION: _make_qr_base64
# -----------------------------------------------------------------
# Generate a QR code image for the invoice verify URL and return
# it as a base64-encoded PNG string, ready to embed in HTML.
#
# REAL-WORLD ANALOGY:
#   Like the barcode on a product — scan it and you jump straight
#   to the invoice page to verify it's authentic.
# -----------------------------------------------------------------
def _make_qr_base64(invoice_id: int) -> str:
    """Generate a QR code for the invoice URL, returned as base64 PNG."""
    url = f"{SERVER_BASE_URL}/api/v1/invoices/{invoice_id}"

    qr = qrcode.QRCode(version=2, box_size=6, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#0f172a", back_color="white")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


# -----------------------------------------------------------------
# FUNCTION: generate_invoice_pdf
# -----------------------------------------------------------------
# PURPOSE:
#   The main function. Takes invoice data (dict) and business data
#   (dict), renders the HTML template, and converts it to a PDF.
#
# PARAMETERS:
#   invoice_data (dict)  -- from invoice_to_dict() in invoices.py
#   business_data (dict) -- {name, tax_id, address, logo_url}
#   lang (str)           -- "ar" | "he" | "en" (default: "ar")
#
# RETURNS:
#   str -- relative URL path to the PDF (e.g. "/static/pdfs/INV-1-0001.pdf")
#
# RAISES:
#   Exception -- if template not found or rendering fails
# -----------------------------------------------------------------
def generate_invoice_pdf(
    invoice_data: dict,
    business_data: dict,
    lang: str = "ar",
) -> str:
    """Render invoice as a bilingual PDF and return its URL path."""

    # ── Step 1: Ensure output directory exists ──
    os.makedirs(PDF_OUTPUT_DIR, exist_ok=True)

    # ── Step 2: Get labels for the chosen language ──
    labels = LABELS.get(lang, LABELS["en"])

    # ── Step 3: Determine text direction ──
    direction = "rtl" if lang in ("ar", "he") else "ltr"

    # ── Step 4: Format dates ──
    now_str = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    invoice_date = ""
    if invoice_data.get("created_at"):
        try:
            d = datetime.datetime.fromisoformat(invoice_data["created_at"])
            invoice_date = d.strftime("%Y-%m-%d")
        except Exception:
            invoice_date = str(invoice_data["created_at"])[:10]

    due_date = ""
    if invoice_data.get("due_date"):
        try:
            d = datetime.datetime.fromisoformat(invoice_data["due_date"])
            due_date = d.strftime("%Y-%m-%d")
        except Exception:
            due_date = str(invoice_data["due_date"])[:10]

    # ── Step 5: Generate QR code ──
    qr_b64 = _make_qr_base64(invoice_data["id"])

    # ── Step 6: Build font file:// URIs for CSS ──
    # WeasyPrint needs absolute file paths for @font-face src
    font_arabic = f"file://{FONT_ARABIC}"
    font_hebrew = f"file://{FONT_HEBREW}"

    # ── Step 7: Load and render the Jinja2 template ──
    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR))
    template = env.get_template("invoice_template.html")

    # Build a simple dict-like object that the template can access
    # via invoice.field_name notation
    class InvoiceObj:
        pass

    inv = InvoiceObj()
    for k, v in invoice_data.items():
        setattr(inv, k, v)

    html_string = template.render(
        invoice=inv,
        business=type("Biz", (), business_data)(),
        lang=lang,
        direction=direction,
        font_arabic=font_arabic,
        font_hebrew=font_hebrew,
        qr_base64=qr_b64,
        invoice_date=invoice_date,
        due_date=due_date,
        now=now_str,
        **labels,
    )

    # ── Step 8: Render HTML → PDF via WeasyPrint ──
    # WeasyPrint calls Pango for text shaping (Arabic letter joining)
    # and Cairo for actual PDF rendering. Both are system libraries
    # already installed on the Alienware (Parrot OS).
    # Imported here (not at module level) so the server starts on
    # dev machines that lack the system Pango/Cairo libraries.
    import weasyprint
    pdf_bytes = weasyprint.HTML(
        string=html_string,
        base_url=os.path.abspath("."),  # Resolves relative paths
    ).write_pdf()

    # ── Step 9: Save to disk ──
    filename = f"{invoice_data['invoice_number']}.pdf"
    output_path = os.path.join(PDF_OUTPUT_DIR, filename)
    with open(output_path, "wb") as f:
        f.write(pdf_bytes)

    pdf_url = f"/static/pdfs/{filename}"
    print(
        f"[PDF] Generated {filename} ({len(pdf_bytes):,} bytes)"
        f" lang={lang} dir={direction}"
    )
    return pdf_url


# -----------------------------------------------------------------
# FUNCTION: get_invoice_pdf_path
# -----------------------------------------------------------------
# Check if a PDF already exists on disk for a given invoice number.
# Returns the URL path if it exists, None if it hasn't been
# generated yet.
# -----------------------------------------------------------------
def get_invoice_pdf_path(invoice_number: str) -> str | None:
    """Return the PDF URL if it exists on disk, else None."""
    filename = f"{invoice_number}.pdf"
    full_path = os.path.join(PDF_OUTPUT_DIR, filename)
    if os.path.exists(full_path):
        return f"/static/pdfs/{filename}"
    return None
