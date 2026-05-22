"""
Aurora LTS — VAT Coach (Product Upgrade #2)
================================================
Trilingual contextual advice surfaced inside Aurora's invoice + receipt
flows. Helps a field worker (and their accountant) catch tax-rule
edge cases BEFORE submitting.

Three coaching scenarios shipped this slice:

  1. ALLOCATION_THRESHOLD_NEAR
     User is about to issue an invoice ₪200 below the threshold —
     warn about the upcoming need for an allocation number, especially
     given the 2026-06-01 threshold drop from ₪10,000 to ₪5,000.

  2. ALLOCATION_REQUIRED
     User just typed an amount that crosses the active threshold.
     Tell them ITA approval will be requested + how long it takes.

  3. UNCATEGORISED_EXPENSE
     A new Expense lacks a category. Suggest the top 3 likely
     categories (from Gemini's proposal or heuristic).

  4. VAT_RATE_AMBIGUITY
     Expense lacks a VAT amount → coach the user on whether the
     supplier is VAT-registered.

These messages are SHORT (≤140 chars) so they fit inside WhatsApp
interactive-message footers and dashboard tooltips.

NEVER GIVES ABSOLUTE TAX ADVICE — always softens with "consider…",
"your accountant should confirm…", etc. Aurora is software, not a CPA.
"""

import datetime
from typing import Optional

from app.services.tax_compliance import (
    DEFAULT_VAT_RATE,
    THRESHOLD_BEFORE_JUNE_2026,
    THRESHOLD_FROM_JUNE_2026,
    THRESHOLD_CHANGE_DATE,
)


# Trilingual templates
_STRINGS = {
    "allocation_required": {
        "he": "⚠️ הסכום ({amount} ₪) דורש אישור של רשות המיסים — המערכת תבקש מספר הקצאה אוטומטית.",
        "ar": "⚠️ المبلغ ({amount} ₪) يتطلب موافقة سلطة الضرائب — سنطلب رقم تخصيص تلقائياً.",
        "en": "⚠️ ₪{amount} requires ITA approval — we'll request an allocation number automatically.",
    },
    "allocation_near": {
        "he": "💡 הסכום ({amount} ₪) קרוב לסף ההקצאה ({threshold} ₪). שקול לבדוק עם הרוה\"ח אם לחלק לשתי חשבוניות.",
        "ar": "💡 المبلغ ({amount} ₪) قريب من حد التخصيص ({threshold} ₪). يفضّل التحقق مع المحاسب.",
        "en": "💡 ₪{amount} is near the allocation threshold (₪{threshold}). Consider checking with your accountant.",
    },
    "threshold_changing": {
        "he": "🔔 שים לב: ב-1 ביוני 2026 סף ההקצאה יורד ל-5,000 ₪ — חשבוניות שעוברות את הסף ידרשו אישור.",
        "ar": "🔔 ملاحظة: في 1 يونيو 2026 ينخفض حد التخصيص إلى 5,000 ₪.",
        "en": "🔔 Heads up: from June 1 2026 the allocation threshold drops to ₪5,000.",
    },
    "uncategorised": {
        "he": "🏷 מומלץ לסווג את ההוצאה — הצעה: {top_categories}. הסיווג מסיע למיפוי בחשבשבת.",
        "ar": "🏷 يُنصح بتصنيف المصروف — اقتراح: {top_categories}.",
        "en": "🏷 Tip: categorise this expense — suggested: {top_categories}. Helps the Hashavshevet export.",
    },
    "vat_missing": {
        "he": "ℹ️ לא נמצא סכום מע\"מ. אם הספק מע\"מ פטור הסיווג נכון; אחרת ייתכן שהקבלה חסרה פרטים.",
        "ar": "ℹ️ لم نجد مبلغ ض.ق.م. إذا كان المورد معفى من ض.ق.م، فهذا طبيعي.",
        "en": "ℹ️ No VAT amount detected. If the supplier is VAT-exempt this is normal — otherwise re-shoot the receipt.",
    },
    "trial_ending": {
        "he": "⏳ תקופת הניסיון מסתיימת בעוד {days} ימים. ללא פעולה החיוב הראשון יתבצע ב-{end_date}.",
        "ar": "⏳ تنتهي فترة التجربة خلال {days} أيام. الدفع الأول في {end_date}.",
        "en": "⏳ Your trial ends in {days} days. First charge: {end_date}.",
    },
    "monthly_vat_summary": {
        "he": "📊 לחודש {period}: מע\"מ עסקאות {output:.0f} ₪, מע\"מ תשומות {input:.0f} ₪ → לתשלום ~{due:.0f} ₪.",
        "ar": "📊 لشهر {period}: ض.ق.م المخرجات {output:.0f} ₪، المدخلات {input:.0f} ₪ → للدفع ~{due:.0f} ₪.",
        "en": "📊 {period}: output VAT ₪{output:.0f}, input VAT ₪{input:.0f} → due ~₪{due:.0f}.",
    },
}


def _t(key: str, lang: str, **kwargs) -> str:
    """Lookup with fallback to Hebrew → English → key."""
    entry = _STRINGS.get(key, {})
    template = entry.get(lang) or entry.get("he") or entry.get("en") or key
    try:
        return template.format(**kwargs)
    except (KeyError, IndexError):
        return template


def _active_threshold(when: Optional[datetime.date] = None) -> int:
    when = when or datetime.date.today()
    return (
        THRESHOLD_FROM_JUNE_2026
        if when >= THRESHOLD_CHANGE_DATE
        else THRESHOLD_BEFORE_JUNE_2026
    )


# ─────────────────────────────────────────────────────────────
# Public — coach_invoice_amount
# ─────────────────────────────────────────────────────────────
def coach_invoice_amount(
    *,
    amount_total_ils: float,
    when: Optional[datetime.date] = None,
    lang: str = "he",
) -> Optional[str]:
    """
    Return the coaching message for an invoice amount, or None when
    nothing useful to say.
    """
    when = when or datetime.date.today()
    threshold = _active_threshold(when)

    if amount_total_ils >= threshold:
        return _t("allocation_required", lang, amount=f"{amount_total_ils:,.0f}")

    near_window = max(threshold * 0.10, 200)  # 10% or ₪200, whichever is larger
    if (threshold - amount_total_ils) <= near_window:
        return _t("allocation_near", lang,
                  amount=f"{amount_total_ils:,.0f}",
                  threshold=f"{threshold:,.0f}")

    # Special: pre-June-2026 invoices flag the upcoming change
    if when < THRESHOLD_CHANGE_DATE and amount_total_ils >= THRESHOLD_FROM_JUNE_2026:
        return _t("threshold_changing", lang)

    return None


# ─────────────────────────────────────────────────────────────
# Public — coach_expense_categorisation
# ─────────────────────────────────────────────────────────────
def coach_expense_categorisation(
    *,
    category: Optional[str],
    suggested_categories: list[str],
    lang: str = "he",
) -> Optional[str]:
    if category:
        return None
    top = ", ".join(suggested_categories[:3]) or "—"
    return _t("uncategorised", lang, top_categories=top)


# ─────────────────────────────────────────────────────────────
# Public — coach_receipt_vat
# ─────────────────────────────────────────────────────────────
def coach_receipt_vat(
    *,
    vat_amount_minor_units: Optional[int],
    total_amount_minor_units: Optional[int],
    lang: str = "he",
) -> Optional[str]:
    if vat_amount_minor_units and vat_amount_minor_units > 0:
        return None
    if not total_amount_minor_units:
        return None
    return _t("vat_missing", lang)


# ─────────────────────────────────────────────────────────────
# Public — coach_trial_ending
# ─────────────────────────────────────────────────────────────
def coach_trial_ending(
    *,
    trial_ends_at: datetime.datetime,
    lang: str = "he",
) -> Optional[str]:
    """If the trial ends in 1-3 days, surface the warning."""
    if not trial_ends_at:
        return None
    days = (trial_ends_at.date() - datetime.date.today()).days
    if not 0 < days <= 3:
        return None
    return _t("trial_ending", lang, days=days, end_date=trial_ends_at.date().isoformat())


# ─────────────────────────────────────────────────────────────
# Public — monthly_vat_summary
# ─────────────────────────────────────────────────────────────
def monthly_vat_summary(
    *,
    output_vat_ils: float,
    input_vat_ils: float,
    period: str,
    lang: str = "he",
) -> str:
    due = output_vat_ils - input_vat_ils
    return _t(
        "monthly_vat_summary", lang,
        period=period, output=output_vat_ils, input=input_vat_ils, due=due,
    )
