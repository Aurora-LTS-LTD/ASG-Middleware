from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from bidi.algorithm import get_display

FONT_PATH = "/Library/Fonts/Arial Unicode.ttf"
pdfmetrics.registerFont(TTFont("ArialUnicode", FONT_PATH))

def rtl(text):
    return get_display(text)

OUTPUT = "/Users/ibraheem-massarwy/Desktop/ASG-Middleware/AURORA_LTS_Executive_Summary_KYC.pdf"

doc = SimpleDocTemplate(
    OUTPUT,
    pagesize=A4,
    rightMargin=2.5 * cm,
    leftMargin=2.5 * cm,
    topMargin=2.5 * cm,
    bottomMargin=2.5 * cm,
)

W = A4[0] - 5 * cm  # usable width

# ── Styles ──────────────────────────────────────────────────────────────────
def style(size, bold=False, color=colors.black, leading=None, align="RIGHT"):
    alignment_map = {"RIGHT": 2, "CENTER": 1, "LEFT": 0}
    return ParagraphStyle(
        name=f"s{size}{bold}",
        fontName="ArialUnicode",
        fontSize=size,
        textColor=color,
        leading=leading or size * 1.45,
        alignment=alignment_map.get(align, 2),
        wordWrap="RTL",
    )

TITLE_S  = style(20, bold=True, color=colors.HexColor("#1a1a2e"), align="CENTER")
SUB_S    = style(11, color=colors.HexColor("#444466"),            align="CENTER")
HEAD_S   = style(12, bold=True, color=colors.HexColor("#1a1a2e"))
BODY_S   = style(10, color=colors.HexColor("#333333"))
SMALL_S  = style(9,  color=colors.HexColor("#555555"))
TABLE_H  = style(9,  bold=True, color=colors.white,              align="CENTER")
TABLE_C  = style(9,  color=colors.HexColor("#222222"),            align="RIGHT")
SIG_S    = style(10, color=colors.HexColor("#222222"))

def p(text, s=None):
    return Paragraph(rtl(text), s or BODY_S)

def heading(text, num):
    return Paragraph(rtl(f"{num}. {text}"), HEAD_S)

DARK_BLUE = colors.HexColor("#1a1a2e")
MID_BLUE  = colors.HexColor("#16213e")
LIGHT_BG  = colors.HexColor("#f5f7ff")
ACCENT    = colors.HexColor("#0f3460")

# ── Build story ─────────────────────────────────────────────────────────────
story = []

# Header block
story.append(Spacer(1, 0.3 * cm))
story.append(Paragraph(rtl("תקציר מנהלים – לצורכי KYC"), TITLE_S))
story.append(Spacer(1, 0.25 * cm))
story.append(Paragraph(rtl("אורורה אל.טי.אס. בע\"מ  |  AURORA LTS LTD"), SUB_S))
story.append(Paragraph(rtl("ח.פ: 517335659"), SUB_S))
story.append(Paragraph(rtl("מנכ\"ל ומורשה חתימה יחיד: איבראהים מצארוה"), SUB_S))
story.append(Spacer(1, 0.3 * cm))
story.append(HRFlowable(width="100%", thickness=2, color=DARK_BLUE))
story.append(Spacer(1, 0.5 * cm))

# ── Section 1: Business Description ──────────────────────────────────────────
story.append(heading("תיאור החברה", 1))
story.append(Spacer(1, 0.2 * cm))
desc = (
    "אורורה אל.טי.אס. בע\"מ היא חברת תוכנה ישראלית המפתחת פלטפורמת SaaS "
    "לניהול עסקים קטנים ובינוניים. המוצר הוא מערכת בוטים חכמה המשולבת "
    "בערוצי הודעות מוכרים – WhatsApp ו-Telegram – ומספקת לבעלי עסקים "
    "אוטומציה של תהליכים שוטפים: ניהול לקוחות, קביעת תורים, הפקת חשבוניות "
    "תואמות רשות המסים, ומעקב פעילות עסקית. קהל היעד הראשוני הוא עסקים "
    "קטנים במגזר הערבי בישראל (צפון ומרכז)."
)
story.append(p(desc))
story.append(Spacer(1, 0.5 * cm))

# ── Section 2: Revenue Model ──────────────────────────────────────────────────
story.append(heading("מודל עסקי – מקורות הכנסה", 2))
story.append(Spacer(1, 0.2 * cm))
story.append(p("החברה פועלת על מודל מנוי חודשי חוזר (Recurring Revenue / SaaS):"))
story.append(Spacer(1, 0.2 * cm))

rev_data = [
    [rtl("מחיר חודשי"), rtl("הכנסה שנתית/לקוח"), rtl("מסלול")],
    [rtl("299 שח"), rtl("3,588 שח"), rtl("בסיסי – ערוץ תקשורת אחד (WhatsApp או Telegram)")],
    [rtl("499 שח"), rtl("5,988 שח"), rtl("מתקדם – שני ערוצים + הפקת חשבוניות")],
    [rtl("500–1,500 שח חד-פעמי"), rtl("—"), rtl("דמי הטמעה (חיבור, הגדרה, הדרכה)")],
]

col_widths = [W * 0.22, W * 0.22, W * 0.56]
rev_table = Table(rev_data, colWidths=col_widths)
rev_table.setStyle(TableStyle([
    ("BACKGROUND",  (0, 0), (-1, 0), DARK_BLUE),
    ("TEXTCOLOR",   (0, 0), (-1, 0), colors.white),
    ("FONTNAME",    (0, 0), (-1, -1), "ArialUnicode"),
    ("FONTSIZE",    (0, 0), (-1, -1), 9),
    ("ALIGN",       (0, 0), (-1, -1), "RIGHT"),
    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [LIGHT_BG, colors.white]),
    ("GRID",        (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
    ("TOPPADDING",  (0, 0), (-1, -1), 5),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
]))
story.append(rev_table)
story.append(Spacer(1, 0.5 * cm))

# ── Section 3: Year 1 Forecast ────────────────────────────────────────────────
story.append(heading("תחזית מחזור – שנה ראשונה (הערכה שמרנית)", 3))
story.append(Spacer(1, 0.15 * cm))
story.append(p("יעד: 30 עסקים פעילים עד סוף שנה 1"))
story.append(Spacer(1, 0.2 * cm))

fcst_data = [
    [rtl("הכנסה רבעונית"), rtl("הכנסה חודשית"), rtl("עסקים פעילים"), rtl("רבעון")],
    [rtl("6,000 שח"),  rtl("~2,000 שח"),  rtl("5"),  rtl("Q1")],
    [rtl("15,000 שח"), rtl("~5,000 שח"),  rtl("12"), rtl("Q2")],
    [rtl("27,000 שח"), rtl("~9,000 שח"),  rtl("22"), rtl("Q3")],
    [rtl("39,000 שח"), rtl("~13,000 שח"), rtl("30"), rtl("Q4")],
    [rtl("87,000 שח"), rtl("~13,000 שח"), rtl("30"), rtl("סה\"כ שנה 1")],
]

cw2 = [W * 0.28, W * 0.24, W * 0.22, W * 0.26]
fcst_table = Table(fcst_data, colWidths=cw2)
fcst_table.setStyle(TableStyle([
    ("BACKGROUND",   (0, 0), (-1, 0), DARK_BLUE),
    ("TEXTCOLOR",    (0, 0), (-1, 0), colors.white),
    ("BACKGROUND",   (0, -1), (-1, -1), ACCENT),
    ("TEXTCOLOR",    (0, -1), (-1, -1), colors.white),
    ("FONTNAME",     (0, 0), (-1, -1), "ArialUnicode"),
    ("FONTSIZE",     (0, 0), (-1, -1), 9),
    ("ALIGN",        (0, 0), (-1, -1), "RIGHT"),
    ("ROWBACKGROUNDS", (0, 1), (-1, -2), [LIGHT_BG, colors.white]),
    ("GRID",         (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
    ("TOPPADDING",   (0, 0), (-1, -1), 5),
    ("BOTTOMPADDING",(0, 0), (-1, -1), 5),
    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
]))
story.append(fcst_table)
story.append(Spacer(1, 0.15 * cm))
story.append(p("* בתוספת דמי הטמעה: ~100,000–110,000 שח שנה 1. התחזית אינה כוללת גידול מפה לאוזן.", SMALL_S))
story.append(Spacer(1, 0.5 * cm))

# ── Section 4: Customer Types ────────────────────────────────────────────────
story.append(heading("סוגי לקוחות (B2B – עסקים קטנים)", 4))
story.append(Spacer(1, 0.15 * cm))
customers = [
    "מסעדות ובתי קפה – ניהול הזמנות, תפריט דיגיטלי, תזכורות ללקוחות",
    "מוסכים ובתי מלאכה – ניהול תורים, הפקת חשבוניות לעבודות תיקון",
    "עסקי קמעונאות – מעקב מלאי, קשר שוטף עם לקוחות",
    "קבלנים ועסקי בנייה – הצעות מחיר, מעקב עבודות שטח",
    "עורכי דין, רואי חשבון, נותני שירותים מקצועיים",
]
for c in customers:
    story.append(p(f"• {c}"))
story.append(Spacer(1, 0.15 * cm))
story.append(p("כל הלקוחות הם עסקים (B2B). אין מכירה ישירה לצרכנים פרטיים."))
story.append(Spacer(1, 0.5 * cm))

# ── Section 5: Suppliers ──────────────────────────────────────────────────────
story.append(heading("סוגי ספקים והוצאות תפעוליות עיקריות", 5))
story.append(Spacer(1, 0.2 * cm))

sup_data = [
    [rtl("הוצאה חודשית משוערת"), rtl("שימוש"), rtl("ספק / קטגוריה")],
    [rtl("300–800 שח"), rtl("שרתים, מסד נתונים, בינה מלאכותית"), rtl("Google Cloud Platform")],
    [rtl("200–500 שח"), rtl("תשתית הודעות WhatsApp"), rtl("Meta – WhatsApp Business API")],
    [rtl("150–300 שח"), rtl("אוטומציה של תהליכים עסקיים"), rtl("Make.com")],
    [rtl("500–1,500 שח"), rtl("פרסום ממומן, רשתות חברתיות"), rtl("שיווק דיגיטלי")],
    [rtl("200–400 שח"), rtl("רישיונות תוכנה, כלים"), rtl("הוצאות תפעוליות כלליות")],
    [rtl("1,350–3,500 שח"), rtl(""), rtl("סה\"כ הוצאות חודשיות")],
]

cw3 = [W * 0.28, W * 0.42, W * 0.30]
sup_table = Table(sup_data, colWidths=cw3)
sup_table.setStyle(TableStyle([
    ("BACKGROUND",   (0, 0), (-1, 0), DARK_BLUE),
    ("TEXTCOLOR",    (0, 0), (-1, 0), colors.white),
    ("BACKGROUND",   (0, -1), (-1, -1), ACCENT),
    ("TEXTCOLOR",    (0, -1), (-1, -1), colors.white),
    ("FONTNAME",     (0, 0), (-1, -1), "ArialUnicode"),
    ("FONTSIZE",     (0, 0), (-1, -1), 9),
    ("ALIGN",        (0, 0), (-1, -1), "RIGHT"),
    ("ROWBACKGROUNDS", (0, 1), (-1, -2), [LIGHT_BG, colors.white]),
    ("GRID",         (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
    ("TOPPADDING",   (0, 0), (-1, -1), 5),
    ("BOTTOMPADDING",(0, 0), (-1, -1), 5),
    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
]))
story.append(sup_table)
story.append(Spacer(1, 0.6 * cm))

# ── Signature block ──────────────────────────────────────────────────────────
story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#aaaaaa")))
story.append(Spacer(1, 0.35 * cm))
story.append(p("הצהרה: המידע המפורט לעיל נכון ומדויק למיטב ידיעתנו ומשקף את פעילות החברה בפועל.", SMALL_S))
story.append(Spacer(1, 0.4 * cm))

sig_data = [
    [
        rtl("חותמת החברה"),
        rtl("חתימה"),
        rtl("תאריך"),
        rtl("מנכ\"ל ומורשה חתימה יחיד"),
    ],
    [
        rtl(""),
        rtl(""),
        rtl(""),
        rtl("איבראהים מצארוה"),
    ],
    [
        rtl("_______________"),
        rtl("_______________"),
        rtl("_______________"),
        rtl(""),
    ],
]

sig_table = Table(sig_data, colWidths=[W * 0.25] * 4)
sig_table.setStyle(TableStyle([
    ("FONTNAME",    (0, 0), (-1, -1), "ArialUnicode"),
    ("FONTSIZE",    (0, 0), (-1, 0), 8),
    ("FONTSIZE",    (0, 1), (-1, -1), 9),
    ("ALIGN",       (0, 0), (-1, -1), "CENTER"),
    ("TEXTCOLOR",   (0, 0), (-1, 0), colors.HexColor("#777777")),
    ("BOTTOMPADDING",(0, 0), (-1, -1), 3),
    ("TOPPADDING",  (0, 0), (-1, -1), 3),
]))
story.append(sig_table)

doc.build(story)
print(f"PDF created: {OUTPUT}")
