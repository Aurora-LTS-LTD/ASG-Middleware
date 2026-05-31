"""
Aurora LTS — Gemini Flash Wrapper (Product Upgrade #1)
============================================================
Auto-categorises Expenses produced by the OCR pipeline. Called from
the receipt pipeline AFTER Document AI returns parsed fields and BEFORE
the Receipt + Expense rows are persisted.

THE PROBLEM IT SOLVES:
  Document AI gives us {supplier, amount, date}. It does NOT tell us
  "this is a fuel expense". Without categorisation, accountants have
  to assign a category by hand on every Expense — 5 minutes per
  client per month they don't have.

GEMINI 2.0 FLASH:
  Cheap (~₪0.0003 per call), fast (~400ms p95), and good at
  Hebrew/Arabic supplier names. The prompt asks for one of Aurora's
  internal categories given (supplier_name, total, vendor_text)
  + the org's industry hint.

BACKEND SELECTOR:
  GEMINI_BACKEND='stub' (default) → keyword-based fallback (cheap heuristic)
  GEMINI_BACKEND='vertex'         → real Vertex AI Gemini call

COST GUARDRAILS:
  - GEMINI_DAILY_BUDGET_CENTS — hard cap; once hit, fall back to stub
  - GEMINI_MAX_INPUT_CHARS    — truncate inputs before sending

STUB IS NOT JUST A NO-OP:
  It uses simple keyword matching ("דלק"|"fuel"|"sonol" → 'fuel'). This
  gives ~60% accuracy without spending a single agora — useful as a
  first-pass even in production for cost-sensitive deployments.
"""

import os
import re
from typing import Optional


GEMINI_BACKEND = (os.getenv("GEMINI_BACKEND") or "stub").strip().lower()


# Aurora's canonical categories — must match Hashavshevet's COA mapping
# (see services/exports/hashavshevet.py).
CATEGORIES = (
    "fuel", "tools", "subcontractor", "phone", "rent",
    "office_supplies", "depreciation", "bank_charges", "interest",
    "food", "parking", "professional_services", "marketing",
    "insurance", "utilities", "other",
)


# ─────────────────────────────────────────────────────────────
# Heuristic fallback (stub backend or budget-exceeded path)
# ─────────────────────────────────────────────────────────────
_HEBREW_KEYWORDS: dict[str, list[str]] = {
    "fuel":            ["דלק", "סונול", "פז", "דור", "ten ", "delek", "sonol", "paz"],
    "phone":           ["סלקום", "פרטנר", "פלאפון", "הוט", "בזק", "cellcom", "partner", "pelephone"],
    "food":            ["מסעדה", "ארוחה", "קפה", "מזון", "restaurant", "café", "shawarma", "falafel"],
    "parking":         ["חניה", "חניון", "פרקינג", "parking"],
    "rent":            ["שכירות", "שכר דירה", "rent"],
    "office_supplies": ["משרד", "קרטון", "office", "staples", "stationery"],
    "tools":           ["כלי עבודה", "ציוד", "tools", "hardware", "אייס"],
    "subcontractor":   ["קבלן משנה", "subcontractor"],
    "marketing":       ["פרסום", "מרקטינג", "google ads", "facebook ads", "advertising"],
    "insurance":       ["ביטוח", "insurance"],
    "utilities":       ["חשמל", "מים", "גז ביתי", "electricity", "water"],
    "professional_services": ["עורך דין", "רואה חשבון", "ייעוץ", "lawyer", "cpa", "consulting"],
    "bank_charges":    ["עמלת בנק", "עמלות", "bank fee", "leumi", "hapoalim", "discount bank"],
    "interest":        ["ריבית", "interest"],
}


def _heuristic_categorise(supplier_name: str, raw_text: str = "") -> tuple[str, float]:
    """
    Stub backend / fallback: keyword match.
    Returns (category, confidence). Confidence is 0.6 for a hit, 0.3
    when we drop into 'other' (deliberately mediocre — the accountant
    should still review).
    """
    haystack = ((supplier_name or "") + " " + (raw_text or "")).lower()
    for category, keywords in _HEBREW_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in haystack:
                return category, 0.6
    return "other", 0.3


# ─────────────────────────────────────────────────────────────
# Cost budget tracking (in-memory, daily reset)
# ─────────────────────────────────────────────────────────────
_today_cents_used = 0
_today_date: Optional[str] = None


def _reset_budget_if_new_day() -> None:
    import datetime
    global _today_cents_used, _today_date
    today = datetime.date.today().isoformat()
    if today != _today_date:
        _today_cents_used = 0
        _today_date = today


def _budget_remaining_cents() -> int:
    _reset_budget_if_new_day()
    cap = int(os.getenv("GEMINI_DAILY_BUDGET_CENTS", "5000"))  # ₪50/day default
    return max(0, cap - _today_cents_used)


# ─────────────────────────────────────────────────────────────
# Public — categorise_expense
# ─────────────────────────────────────────────────────────────
def categorise_expense(
    *,
    supplier_name: Optional[str],
    total_amount_minor_units: int,
    raw_text: str = "",
    org_industry_hint: Optional[str] = None,
) -> dict:
    """
    Return a category proposal:
      {
        "category":   "fuel" | ... | "other",
        "confidence": 0.0–1.0,
        "rationale":  "why we picked this",
        "backend":    "stub" | "vertex" | "fallback",
      }

    Never raises — failures fall back to the heuristic.
    """
    # Stub backend → always heuristic
    if GEMINI_BACKEND != "vertex":
        cat, conf = _heuristic_categorise(supplier_name or "", raw_text)
        return {
            "category": cat,
            "confidence": conf,
            "rationale": f"keyword match on supplier_name={supplier_name!r}",
            "backend": "stub",
        }

    # Vertex backend with budget guardrail
    if _budget_remaining_cents() <= 0:
        cat, conf = _heuristic_categorise(supplier_name or "", raw_text)
        return {
            "category": cat,
            "confidence": conf,
            "rationale": "Gemini daily budget exhausted; heuristic fallback",
            "backend": "fallback",
        }

    try:
        result = _vertex_categorise(
            supplier_name=supplier_name or "",
            total_minor=total_amount_minor_units,
            raw_text=raw_text,
            industry_hint=org_industry_hint,
        )
        # Track approximate cost (~$0.0003 per call ≈ 0.1 cents in agorot)
        # Conservative bump: 1 agora per call
        global _today_cents_used
        _today_cents_used += 1
        return result
    except Exception as e:
        print(f"[GEMINI] ⚠️ Vertex call failed; falling back to heuristic: {e}")
        cat, conf = _heuristic_categorise(supplier_name or "", raw_text)
        return {
            "category": cat,
            "confidence": conf,
            "rationale": f"vertex_failed:{e!s}; heuristic fallback",
            "backend": "fallback",
        }


# ─────────────────────────────────────────────────────────────
# Vertex AI backend
# ─────────────────────────────────────────────────────────────
_PROMPT_TEMPLATE = """You are an Israeli small-business accountant categorising expenses.
Given the receipt details below, pick EXACTLY ONE category from this list:
{categories}

Reply in this exact JSON shape, no extra text:
{{"category": "<one_of_the_above>", "confidence": <float 0-1>, "rationale": "<one sentence>"}}

Receipt:
  supplier:  {supplier}
  amount:    ₪{amount}
  industry:  {industry}
  ocr_text:  {ocr_text}
"""


def _vertex_categorise(
    *,
    supplier_name: str, total_minor: int,
    raw_text: str, industry_hint: Optional[str],
) -> dict:
    """Real Vertex AI call. Lazy SDK import."""
    import json

    from google.cloud import aiplatform  # type: ignore  # noqa: F401
    import vertexai
    from vertexai.generative_models import GenerativeModel, GenerationConfig  # type: ignore

    project = os.getenv("GOOGLE_CLOUD_PROJECT")
    location = os.getenv("VERTEX_AI_LOCATION", "me-west1")
    model_name = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
    if not project:
        raise RuntimeError("GEMINI_BACKEND=vertex requires GOOGLE_CLOUD_PROJECT")

    vertexai.init(project=project, location=location)
    model = GenerativeModel(model_name)

    max_chars = int(os.getenv("GEMINI_MAX_INPUT_CHARS", "1500"))
    truncated_text = (raw_text or "")[:max_chars]
    amount_ils = (total_minor or 0) / 100

    prompt = _PROMPT_TEMPLATE.format(
        categories=", ".join(CATEGORIES),
        supplier=supplier_name or "(unknown)",
        amount=f"{amount_ils:.2f}",
        industry=industry_hint or "(unknown)",
        ocr_text=truncated_text,
    )

    resp = model.generate_content(
        prompt,
        generation_config=GenerationConfig(
            temperature=0.1,
            max_output_tokens=128,
            response_mime_type="application/json",
        ),
    )
    text = resp.text or "{}"
    try:
        parsed = json.loads(text)
    except Exception:
        # Defensive — Gemini sometimes wraps JSON in markdown
        m = re.search(r"\{.*\}", text, re.DOTALL)
        parsed = json.loads(m.group(0)) if m else {}

    category = (parsed.get("category") or "other").lower()
    if category not in CATEGORIES:
        category = "other"
    confidence = float(parsed.get("confidence") or 0.5)
    rationale = str(parsed.get("rationale") or "")[:200]
    return {
        "category": category,
        "confidence": max(0.0, min(1.0, confidence)),
        "rationale": rationale,
        "backend": "vertex",
    }
