"""
ASG Solutions — WhatsApp Bot Trilingual String Tables
========================================================
Central dictionary of every string the bot says, in Hebrew, Arabic,
and English. No i18n framework — just a flat dict keyed by language.

WHY A PLAIN DICT:
  We have three languages and ~100 strings. A heavyweight i18n
  library (gettext / Babel / ICU) adds dependency weight for no
  functional gain here. The tradeoff flips around 500+ strings or
  when pluralization rules get complex.

HOW TO USE:
  from app.services.whatsapp_strings import t
  t("hello", "he", name="Ibrahim")
  → "שלום איבראהים 👋"

  Missing keys fall back to Hebrew, then English. If neither exists,
  the raw key is returned (visible bug marker, not a crash).

RTL NOTE:
  WhatsApp infers message direction from the first *strong*
  directional character. For RTL messages we always START with a
  Hebrew/Arabic letter (never a neutral emoji), so WhatsApp renders
  the message right-to-left. Emojis can come later in the string.
"""

# ─────────────────────────────────────────────────────────────
# STRING TABLE
# ─────────────────────────────────────────────────────────────
# Each key maps to a dict of language → template string.
# Templates use Python str.format() placeholders: {name}, {total}, etc.
# ─────────────────────────────────────────────────────────────
STRINGS: dict[str, dict[str, str]] = {
    # ── Pairing & Identity ──────────────────────────────────────
    "unbound_welcome": {
        "he": (
            "שלום 👋\n"
            "נראה שמספר הטלפון הזה עדיין לא מקושר לחשבון ASG.\n\n"
            "כדי לקשר:\n"
            "1) התחבר ללוח הבקרה של ASG\n"
            "2) לחץ 'Link WhatsApp'\n"
            "3) שלח לי את הקוד שתקבל (למשל LINK-482913)"
        ),
        "ar": (
            "مرحبا 👋\n"
            "يبدو أن هذا الرقم غير مرتبط بعد بحساب ASG.\n\n"
            "للربط:\n"
            "1) ادخل إلى لوحة التحكم\n"
            "2) اضغط 'Link WhatsApp'\n"
            "3) أرسل لي الكود الذي تحصل عليه (مثلا LINK-482913)"
        ),
        "en": (
            "Hello 👋\n"
            "This phone isn't linked to an ASG account yet.\n\n"
            "To link:\n"
            "1) Log in to the ASG dashboard\n"
            "2) Click 'Link WhatsApp'\n"
            "3) Send me the code you receive (e.g. LINK-482913)"
        ),
    },
    "pair_success": {
        "he": "החשבון שלך מקושר ✅ {email}\nאיך תרצה להמשיך?",
        "ar": "تم ربط الحساب ✅ {email}\nكيف تريد المتابعة؟",
        "en": "Account linked ✅ {email}\nHow would you like to continue?",
    },
    "pair_invalid": {
        "he": "הקוד לא תקין או פג תוקפו. נסה שוב מהדשבורד.",
        "ar": "الكود غير صالح أو منتهي. أعد المحاولة من لوحة التحكم.",
        "en": "That code is invalid or expired. Please try again from the dashboard.",
    },

    # ── Main menu ──────────────────────────────────────────────
    "menu_header": {
        "he": "שלום {name} 👋\nחשבוניות החודש: {count} · יתרה פתוחה: ₪{outstanding:,.0f}",
        "ar": "مرحبا {name} 👋\nفواتير الشهر: {count} · رصيد مفتوح: ₪{outstanding:,.0f}",
        "en": "Hi {name} 👋\nInvoices this month: {count} · Outstanding: ₪{outstanding:,.0f}",
    },
    "menu_prompt": {
        "he": "מה נעשה?",
        "ar": "ماذا نفعل؟",
        "en": "What shall we do?",
    },
    "btn_new_invoice": {"he": "🧾 חשבונית", "ar": "🧾 فاتورة", "en": "🧾 Invoice"},
    "btn_balance":     {"he": "📊 מאזן",   "ar": "📊 الرصيد",  "en": "📊 Balance"},
    "btn_overdue":     {"he": "⚠️ באיחור", "ar": "⚠️ متأخرة",  "en": "⚠️ Overdue"},
    "btn_record_pay":  {"he": "💰 תשלום",  "ar": "💰 دفعة",    "en": "💰 Payment"},
    "btn_receipt_box": {"he": "📷 קבלה",   "ar": "📷 فاتورة",  "en": "📷 Receipt"},
    "btn_settings":    {"he": "⚙️ הגדרות", "ar": "⚙️ إعدادات", "en": "⚙️ Settings"},
    "btn_back":        {"he": "↩ חזרה",    "ar": "↩ رجوع",     "en": "↩ Back"},
    "btn_menu":        {"he": "🏠 תפריט",   "ar": "🏠 القائمة",  "en": "🏠 Menu"},

    # ── New invoice flow ───────────────────────────────────────
    "ask_amount": {
        "he": "סכום לפני מע״מ? (מספר בש״ח, לדוגמה 450)",
        "ar": "المبلغ قبل ض.ق.م؟ (رقم بالشيكل، مثلا 450)",
        "en": "Amount before VAT? (₪, e.g. 450)",
    },
    "bad_amount": {
        "he": "לא הבנתי את הסכום. נסה שוב — מספר בלבד, למשל 450 או 1250.50",
        "ar": "لم أفهم المبلغ. أعد — رقم فقط، مثل 450 أو 1250.50",
        "en": "I didn't understand that amount. Try again — numbers only, e.g. 450 or 1250.50",
    },
    "too_big": {
        "he": "הסכום גדול מדי. המקסימום שלנו הוא ₪10,000,000.",
        "ar": "المبلغ كبير جدا. الحد الأقصى ₪10,000,000.",
        "en": "That amount is too large. Max is ₪10,000,000.",
    },
    "vat_line": {
        "he": "💰 סכום לפני מע״מ: ₪{net:,.2f}\n📌 סכום כולל מע״מ ({rate}%): ₪{total:,.2f}",
        "ar": "💰 قبل ض.ق.م: ₪{net:,.2f}\n📌 الإجمالي مع ض.ق.م ({rate}%): ₪{total:,.2f}",
        "en": "💰 Net: ₪{net:,.2f}\n📌 Total incl. VAT ({rate}%): ₪{total:,.2f}",
    },
    "vat_green": {
        "he": "🟢 מתחת לסף — לא נדרש מספר הקצאה",
        "ar": "🟢 تحت الحد — لا حاجة لرقم تخصيص",
        "en": "🟢 Below threshold — no allocation required",
    },
    "vat_yellow": {
        "he": "🟡 מעל הסף — יידרש מספר הקצאה מרשות המסים",
        "ar": "🟡 فوق الحد — يتطلب رقم تخصيص من السلطة الضريبية",
        "en": "🟡 Above threshold — allocation number will be requested",
    },
    "ask_client": {
        "he": "לקוח:",
        "ar": "العميل:",
        "en": "Client:",
    },
    "btn_new_client":  {"he": "➕ לקוח חדש", "ar": "➕ عميل جديد", "en": "➕ New client"},
    "ask_new_client_name": {
        "he": "שם הלקוח? (2-80 תווים)",
        "ar": "اسم العميل؟ (2-80 حرف)",
        "en": "Client name? (2-80 chars)",
    },
    "bad_name": {
        "he": "השם קצר/ארוך מדי. נסה שוב.",
        "ar": "الاسم قصير/طويل جدا. أعد.",
        "en": "Name too short/long. Try again.",
    },

    # ── Confirmation ──────────────────────────────────────────
    "confirm_card": {
        "he": (
            "סכום: ₪{net:,.2f} · מע״מ: ₪{vat:,.2f} · סה״כ: ₪{total:,.2f}\n"
            "ללקוח: {client}\n"
            "{threshold_badge}"
        ),
        "ar": (
            "قبل: ₪{net:,.2f} · ض.ق.م: ₪{vat:,.2f} · الإجمالي: ₪{total:,.2f}\n"
            "للعميل: {client}\n"
            "{threshold_badge}"
        ),
        "en": (
            "Net: ₪{net:,.2f} · VAT: ₪{vat:,.2f} · Total: ₪{total:,.2f}\n"
            "To: {client}\n"
            "{threshold_badge}"
        ),
    },
    "btn_confirm_send": {"he": "✅ צור ושלח", "ar": "✅ إنشاء وإرسال", "en": "✅ Create"},
    "btn_edit":         {"he": "✏ ערוך",     "ar": "✏ تعديل",         "en": "✏ Edit"},
    "btn_cancel":       {"he": "✖ בטל",     "ar": "✖ إلغاء",          "en": "✖ Cancel"},

    "preparing_pdf": {
        "he": "⏳ מכין את ה-PDF...",
        "ar": "⏳ جاري تحضير PDF...",
        "en": "⏳ Preparing the PDF...",
    },
    "pending_allocation": {
        "he": "🕐 ממתין לאישור רשות המסים — אעדכן אותך ברגע שהאישור יגיע.",
        "ar": "🕐 بانتظار موافقة السلطة الضريبية — سأخبرك فور الموافقة.",
        "en": "🕐 Waiting for ITA allocation — I'll update you as soon as it lands.",
    },
    "allocation_arrived": {
        "he": "✅ הוקצה מספר: {allocation}. החשבונית {invoice_number} מוכנה.",
        "ar": "✅ تم تخصيص الرقم: {allocation}. الفاتورة {invoice_number} جاهزة.",
        "en": "✅ Allocated: {allocation}. Invoice {invoice_number} is ready.",
    },
    "invoice_caption": {
        "he": "חשבונית {invoice_number} — ₪{total:,.2f} — נוצרה ✅",
        "ar": "الفاتورة {invoice_number} — ₪{total:,.2f} — تم الإنشاء ✅",
        "en": "Invoice {invoice_number} — ₪{total:,.2f} — created ✅",
    },
    "cancelled": {
        "he": "בוטל. חזרת לתפריט.",
        "ar": "تم الإلغاء. عدت إلى القائمة.",
        "en": "Cancelled. Back to menu.",
    },

    # ── Balance / Overdue ─────────────────────────────────────
    "balance_summary": {
        "he": "📊 יתרה פתוחה: ₪{outstanding:,.0f}\n📄 חשבוניות פתוחות: {open_count}\n⚠️ באיחור: {overdue_count}",
        "ar": "📊 الرصيد المفتوح: ₪{outstanding:,.0f}\n📄 فواتير مفتوحة: {open_count}\n⚠️ متأخرة: {overdue_count}",
        "en": "📊 Outstanding: ₪{outstanding:,.0f}\n📄 Open invoices: {open_count}\n⚠️ Overdue: {overdue_count}",
    },
    "overdue_none": {
        "he": "🎉 אין חשבוניות באיחור. כל הכבוד!",
        "ar": "🎉 لا توجد فواتير متأخرة. عمل رائع!",
        "en": "🎉 No overdue invoices. Nice work!",
    },
    "overdue_row": {
        "he": "{invoice_number} · ₪{total:,.0f} · {days} ימים · {client}",
        "ar": "{invoice_number} · ₪{total:,.0f} · {days} أيام · {client}",
        "en": "{invoice_number} · ₪{total:,.0f} · {days} days · {client}",
    },

    # ── Settings ──────────────────────────────────────────────
    "settings_header": {
        "he": "⚙️ הגדרות",
        "ar": "⚙️ الإعدادات",
        "en": "⚙️ Settings",
    },
    "btn_lang":          {"he": "🌐 שפה",      "ar": "🌐 اللغة",     "en": "🌐 Language"},
    "btn_digest_on":     {"he": "☀️ סיכום בוקר: פעיל", "ar": "☀️ الملخص الصباحي: مفعل", "en": "☀️ Morning Pulse: ON"},
    "btn_digest_off":    {"he": "☀️ סיכום בוקר: כבוי", "ar": "☀️ الملخص الصباحي: معطل", "en": "☀️ Morning Pulse: OFF"},
    "btn_unlink":        {"he": "🚪 נתק",       "ar": "🚪 إلغاء الربط", "en": "🚪 Unlink"},
    "ask_lang":          {
        "he": "באיזו שפה להמשיך?",
        "ar": "بأي لغة نستمر؟",
        "en": "Which language?",
    },
    "btn_lang_he":       {"he": "עברית",  "ar": "عبرية",  "en": "Hebrew"},
    "btn_lang_ar":       {"he": "ערבית",  "ar": "عربية",  "en": "Arabic"},
    "btn_lang_en":       {"he": "אנגלית", "ar": "إنجليزية", "en": "English"},
    "lang_set": {
        "he": "✅ השפה נקבעה לעברית",
        "ar": "✅ تم ضبط اللغة إلى العربية",
        "en": "✅ Language set to English",
    },
    "digest_toggled": {
        "he": "✅ סיכום הבוקר עודכן.",
        "ar": "✅ تم تحديث الملخص الصباحي.",
        "en": "✅ Morning Pulse setting updated.",
    },
    "unlinked": {
        "he": "🚪 נותקת. שלח LINK-קוד כדי להתחבר שוב.",
        "ar": "🚪 تم إلغاء الربط. أرسل LINK-code للربط مجددا.",
        "en": "🚪 Unlinked. Send LINK-code to re-bind.",
    },

    # ── Receipt Box ───────────────────────────────────────────
    "receipt_received": {
        "he": "📷 קיבלתי! מזהה תוכן...",
        "ar": "📷 تم الاستلام! جار التعرف على المحتوى...",
        "en": "📷 Got it! Extracting content...",
    },
    "receipt_parsed": {
        "he": "{vendor} · ₪{amount:,.2f} · {date}\nקטגוריה: {category}",
        "ar": "{vendor} · ₪{amount:,.2f} · {date}\nالفئة: {category}",
        "en": "{vendor} · ₪{amount:,.2f} · {date}\nCategory: {category}",
    },
    "receipt_saved": {
        "he": "✅ נשמר בספר ההוצאות של החודש.",
        "ar": "✅ تم الحفظ في دفتر مصاريف الشهر.",
        "en": "✅ Saved to this month's expense book.",
    },

    # ── Sprint 2 — Document AI Receipt Pipeline strings ──
    "receipt_review_card": {
        "he": (
            "🧾 *קבלה נקלטה — נא לאשר:*\n"
            "🏪 ספק: {supplier}\n"
            "💰 סכום: ₪{total}\n"
            "📅 תאריך: {date}\n"
            "{conf_line}"
        ),
        "ar": (
            "🧾 *تم استلام الفاتورة — يرجى التأكيد:*\n"
            "🏪 المورد: {supplier}\n"
            "💰 المبلغ: ₪{total}\n"
            "📅 التاريخ: {date}\n"
            "{conf_line}"
        ),
        "en": (
            "🧾 *Receipt parsed — please confirm:*\n"
            "🏪 Supplier: {supplier}\n"
            "💰 Total: ₪{total}\n"
            "📅 Date: {date}\n"
            "{conf_line}"
        ),
    },
    "receipt_auto_approve_card": {
        "he": (
            "✅ *נשמר בהוצאות:*\n"
            "🏪 {supplier} · ₪{total} · {date}\n"
            "אם משהו לא מדויק — שלח 'תקן {receipt_id}'"
        ),
        "ar": (
            "✅ *تم الحفظ في المصاريف:*\n"
            "🏪 {supplier} · ₪{total} · {date}\n"
            "إذا لزم تعديل — أرسل 'تصحيح {receipt_id}'"
        ),
        "en": (
            "✅ *Saved to expenses:*\n"
            "🏪 {supplier} · ₪{total} · {date}\n"
            "If anything's off, send 'fix {receipt_id}'"
        ),
    },
    "receipt_review_conf_low": {
        "he": "⚠️ ביטחון נמוך — בבקשה תאשר",
        "ar": "⚠️ ثقة منخفضة — يرجى التأكيد",
        "en": "⚠️ Low confidence — please confirm",
    },
    "receipt_review_conf_mid": {
        "he": "ℹ️ נא לאמת",
        "ar": "ℹ️ يرجى التحقق",
        "en": "ℹ️ Please verify",
    },
    "receipt_review_conf_high": {
        "he": "",  # blank line on auto-approve
        "ar": "",
        "en": "",
    },
    "btn_receipt_confirm": {
        "he": "✅ אשר",
        "ar": "✅ تأكيد",
        "en": "✅ Confirm",
    },
    "btn_receipt_fix": {
        "he": "✏️ תקן",
        "ar": "✏️ تعديل",
        "en": "✏️ Fix",
    },
    "btn_receipt_reject": {
        "he": "🗑 דחה",
        "ar": "🗑 رفض",
        "en": "🗑 Reject",
    },
    "receipt_amount_guess_prompt": {
        "he": (
            "🤔 *לא הצלחתי לקרוא את הסכום בבירור.*\n"
            "מה הסכום הכולל בקבלה? (כתוב מספר, למשל 287.50)"
        ),
        "ar": (
            "🤔 *لم أستطع قراءة المبلغ بوضوح.*\n"
            "ما المبلغ الإجمالي؟ (مثلا 287.50)"
        ),
        "en": (
            "🤔 *I couldn't read the amount clearly.*\n"
            "What's the total? (e.g. 287.50)"
        ),
    },
    "receipt_filed": {
        "he": "✅ הקבלה נוספה להוצאות החודש.",
        "ar": "✅ أُضيفت الفاتورة لمصاريف الشهر.",
        "en": "✅ Receipt added to this month's expenses.",
    },
    "receipt_rejected": {
        "he": "🗑 הקבלה נדחתה ולא תיכלל בהוצאות.",
        "ar": "🗑 تم رفض الفاتورة.",
        "en": "🗑 Receipt rejected — won't appear in expenses.",
    },
    "receipt_dlp_rejected": {
        "he": (
            "⚠️ *התמונה נראית כמו תעודה אישית, לא קבלה.*\n"
            "אנחנו לא שומרים תעודות זהות / כרטיסי אשראי.\n"
            "צלם רק את הקבלה ושלח שוב 🙏"
        ),
        "ar": (
            "⚠️ *الصورة تبدو وكأنها هوية شخصية، وليست فاتورة.*\n"
            "نحن لا نحفظ الهويات / البطاقات الائتمانية.\n"
            "صور الفاتورة فقط وأرسلها مجدداً 🙏"
        ),
        "en": (
            "⚠️ *That image looks like an ID document, not a receipt.*\n"
            "We don't store ID cards / credit cards.\n"
            "Please send only the receipt 🙏"
        ),
    },
    "receipt_ocr_failed": {
        "he": (
            "⚠️ לא הצלחנו לעבד את התמונה.\n"
            "נסה תמונה ברורה יותר, או הקלד את הפרטים ידנית."
        ),
        "ar": (
            "⚠️ لم نتمكن من معالجة الصورة.\n"
            "حاول بصورة أوضح، أو أدخل التفاصيل يدوياً."
        ),
        "en": (
            "⚠️ Couldn't process that image.\n"
            "Try a clearer photo, or enter the details manually."
        ),
    },
    "receipt_duplicate": {
        "he": (
            "ℹ️ *הקבלה הזו כבר נשמרה אצלנו* (אותו תוכן בדיוק).\n"
            "לא צריך לשלוח שוב 👍"
        ),
        "ar": (
            "ℹ️ *تم حفظ هذه الفاتورة سابقاً* (نفس المحتوى).\n"
            "لا حاجة لإعادة الإرسال 👍"
        ),
        "en": (
            "ℹ️ *We already have this receipt* (exact same content).\n"
            "No need to resend 👍"
        ),
    },
    "receipt_amount_invalid": {
        "he": "לא הצלחתי לקרוא מספר. נסה: 287 או 287.50",
        "ar": "لم أفهم الرقم. مثلا: 287 أو 287.50",
        "en": "I couldn't parse that. Try: 287 or 287.50",
    },
    "receipt_unable_to_download": {
        "he": "⚠️ לא הצלחנו להוריד את התמונה. נסה לשלוח שוב.",
        "ar": "⚠️ لم نتمكن من تحميل الصورة. أعد الإرسال.",
        "en": "⚠️ Couldn't download the image. Please resend.",
    },

    # ── Misc ──────────────────────────────────────────────────
    "unknown_message": {
        "he": "לא הבנתי. אפשר לחזור לתפריט הראשי?",
        "ar": "لم أفهم. أعود للقائمة الرئيسية؟",
        "en": "I didn't understand. Return to the main menu?",
    },
    "record_payment_soon": {
        "he": "💰 רישום תשלום בדרך — הפיצ'ר הזה ייפתח בגרסה הבאה. בינתיים תוכל לרשום תשלום מהדשבורד.",
        "ar": "💰 تسجيل الدفعات يأتي قريبا — استخدم لوحة التحكم حاليا.",
        "en": "💰 Payment recording is coming soon — use the dashboard for now.",
    },

    # ═════════════════════════════════════════════════════════
    # ONBOARDING flow — Sprint 1 follow-up
    # WhatsApp-native account creation. The user starts here after
    # tapping the "📝 Quick signup" button on the unbound welcome.
    # ═════════════════════════════════════════════════════════
    "unbound_choice_prompt": {
        "he": "איך נמשיך? יש לך שתי דרכים — בחר אחת:",
        "ar": "كيف نتابع؟ لديك طريقتان — اختر واحدة:",
        "en": "How do you want to proceed? Two ways — pick one:",
    },
    "btn_wa_signup":   {"he": "📝 הרשמה ב-WhatsApp", "ar": "📝 تسجيل عبر WhatsApp", "en": "📝 Sign up here"},
    "btn_open_web":    {"he": "🌐 פתח דשבורד",        "ar": "🌐 افتح لوحة التحكم",  "en": "🌐 Open web wizard"},
    "btn_link_existing": {"he": "🔗 יש לי קוד",       "ar": "🔗 لدي كود",            "en": "🔗 I have a code"},

    "onb_intro": {
        "he": (
            "✨ *הצטרפות מהירה לאורורה*\n"
            "נשאל אותך כמה שאלות (כדקה אחת). תוכל לעצור בכל שלב על ידי "
            "שליחת המילה 'ביטול'."
        ),
        "ar": (
            "✨ *تسجيل سريع في أورورا*\n"
            "سنطرح بعض الأسئلة (دقيقة تقريبا). يمكنك التوقف بأي وقت "
            "بإرسال 'إلغاء'."
        ),
        "en": (
            "✨ *Aurora — quick signup*\n"
            "We'll ask a few questions (~1 minute). Type 'cancel' at any "
            "step to stop."
        ),
    },

    # ── Step: First name ───────────────────────────────────────
    "onb_ask_first_name": {
        "he": "מה השם הפרטי שלך? (לפחות 2 תווים)",
        "ar": "ما هو اسمك الأول؟ (٢ أحرف على الأقل)",
        "en": "What is your first name? (≥2 chars)",
    },
    "onb_bad_first_name": {
        "he": "השם קצר מדי. אפשר לכתוב את השם הפרטי שוב?",
        "ar": "الاسم قصير جدا. أعد كتابة اسمك الأول من فضلك.",
        "en": "Too short. Please type your first name again.",
    },

    # ── Step: Last name ────────────────────────────────────────
    "onb_ask_last_name": {
        "he": "מה שם המשפחה?",
        "ar": "ما هو اسم العائلة؟",
        "en": "What is your last name?",
    },
    "onb_bad_last_name": {
        "he": "שם משפחה קצר מדי. נסה שוב.",
        "ar": "اسم العائلة قصير جدا. أعد المحاولة.",
        "en": "Too short. Please try again.",
    },

    # ── Step: Legal structure ──────────────────────────────────
    "onb_ask_legal_structure": {
        "he": "*איזה סוג עסק יש לך?*\n(תוכל להשנות את זה אחר כך)",
        "ar": "*ما نوع عملك؟*\n(يمكنك تغييره لاحقا)",
        "en": "*What is your legal structure?*\n(you can change later)",
    },
    "btn_legal_osek_morshe": {
        "he": "עוסק מורשה",
        "ar": "متعامل مرخص",
        "en": "Authorized",
    },
    "btn_legal_osek_patur": {
        "he": "עוסק פטור",
        "ar": "متعامل معفى",
        "en": "Exempt",
    },
    "btn_legal_chevra_baam": {
        "he": "חברה בע\"מ",
        "ar": "شركة م.م",
        "en": "Ltd",
    },

    # ── Step: Tax ID ──────────────────────────────────────────
    "onb_ask_tax_id": {
        "he": (
            "*מה מספר ח.פ. / ת.ז. / ע.מ.?*\n"
            "9 ספרות. נבדוק מיד שזה תקין."
        ),
        "ar": (
            "*ما رقم الشركة / الهوية / المتعامل؟*\n"
            "9 أرقام. سنتحقق فورا."
        ),
        "en": (
            "*Tax ID / Company ID?*\n"
            "9 digits. We validate the checksum on the fly."
        ),
    },
    "onb_bad_tax_id": {
        "he": "ספרת הבקרה לא תקינה. אפשר לבדוק שוב? (9 ספרות, למשל 123456782)",
        "ar": "رقم التحقق غير صحيح. تأكد من الإدخال (9 أرقام، مثلا 123456782)",
        "en": "Checksum failed. Please re-check (9 digits, e.g. 123456782)",
    },

    # ── Step: Business name ───────────────────────────────────
    "onb_ask_business_name": {
        "he": "*מה שם העסק?* (איך זה יופיע על החשבונית)",
        "ar": "*ما اسم العمل؟* (كما سيظهر على الفاتورة)",
        "en": "*Business name?* (as it'll appear on invoices)",
    },
    "onb_bad_business_name": {
        "he": "השם קצר מדי (לפחות 3 תווים). נסה שוב.",
        "ar": "الاسم قصير جدا (٣ أحرف على الأقل). أعد المحاولة.",
        "en": "Too short (≥3 chars). Please try again.",
    },

    # ── Step: Business type ───────────────────────────────────
    "onb_ask_business_type": {
        "he": "*באיזה תחום העסק?* (זה עוזר לנו להציע לך את התכונות הנכונות)",
        "ar": "*ما هو مجال عملك؟* (يساعدنا في تقديم المزايا الصحيحة)",
        "en": "*What's your industry?* (helps us tailor features)",
    },
    "btn_btype_list":      {"he": "בחר תחום", "ar": "اختر",       "en": "Pick"},
    "btn_btype_contractor":{"he": "🔧 קבלן",   "ar": "🔧 مقاول",   "en": "🔧 Contractor"},
    "btn_btype_electrician":{"he": "⚡ חשמלאי", "ar": "⚡ كهربائي", "en": "⚡ Electrician"},
    "btn_btype_plumber":   {"he": "🚰 שרברב",  "ar": "🚰 سباك",    "en": "🚰 Plumber"},
    "btn_btype_hvac":      {"he": "❄️ מזגנים", "ar": "❄️ تكييف",   "en": "❄️ HVAC"},
    "btn_btype_retail":    {"he": "🛒 מסחר",   "ar": "🛒 تجارة",   "en": "🛒 Retail"},
    "btn_btype_services":  {"he": "🛠 שירותים", "ar": "🛠 خدمات",   "en": "🛠 Services"},
    "btn_btype_other":     {"he": "✏️ אחר",    "ar": "✏️ آخر",     "en": "✏️ Other"},

    # ── Step: Invite accountant ───────────────────────────────
    "onb_ask_invite_accountant": {
        "he": (
            "*יש לך רואה חשבון?*\n"
            "תוכל לצרף אותו עכשיו (או בכל שלב מאוחר יותר). הוא יראה את "
            "הדוחות שלך בלבד — לא יכול לערוך נתונים."
        ),
        "ar": (
            "*هل لديك محاسب؟*\n"
            "يمكنك إضافته الآن (أو لاحقا). سيرى تقاريرك فقط — لا يمكنه التعديل."
        ),
        "en": (
            "*Do you work with an accountant?*\n"
            "Add them now or later. They'll only see reports — they can't edit your data."
        ),
    },
    "btn_invite_yes":     {"he": "✅ כן, הזמן עכשיו", "ar": "✅ نعم، أضفه الآن", "en": "✅ Yes, invite now"},
    "btn_invite_later":   {"he": "🕐 בוא נוסיף בהמשך","ar": "🕐 لاحقا",          "en": "🕐 Maybe later"},

    # ── Step: Accountant contact ──────────────────────────────
    "onb_ask_accountant_contact": {
        "he": "מה הדוא\"ל או הטלפון של רוה\"ח? (נשלח להם הזמנה)",
        "ar": "ما هو بريد المحاسب أو هاتفه؟ (سنرسل له دعوة)",
        "en": "Accountant's email or phone? (we'll send the invite)",
    },
    "onb_bad_contact": {
        "he": "צריך לקבל דוא\"ל תקין (a@b.c) או טלפון בפורמט בינלאומי (+972...)",
        "ar": "نحتاج بريدا صحيحا (a@b.c) أو هاتفا بصيغة دولية (+972...)",
        "en": "Need a valid email (a@b.c) or international phone (+972...)",
    },
    "onb_accountant_invited": {
        "he": "✉️ ההזמנה נרשמה. אנחנו נשלח אותה כשרוה\"ח יתחבר.",
        "ar": "✉️ تم تسجيل الدعوة. سنرسلها عندما يتاح.",
        "en": "✉️ Invitation queued. We'll send it shortly.",
    },

    # ── Step: Confirm ─────────────────────────────────────────
    "onb_confirm_card": {
        "he": (
            "*סיכום הרשמה:*\n"
            "👤 {first_name} {last_name}\n"
            "🏢 {display_name}\n"
            "📋 {legal_structure_label} · ת.ז./ח.פ.: {tax_id}\n"
            "🏷 {business_type_label}\n"
            "{accountant_line}"
            "\nלחץ ✅ כדי ליצור את החשבון."
        ),
        "ar": (
            "*ملخص التسجيل:*\n"
            "👤 {first_name} {last_name}\n"
            "🏢 {display_name}\n"
            "📋 {legal_structure_label} · {tax_id}\n"
            "🏷 {business_type_label}\n"
            "{accountant_line}"
            "\nاضغط ✅ لإنشاء الحساب."
        ),
        "en": (
            "*Signup summary:*\n"
            "👤 {first_name} {last_name}\n"
            "🏢 {display_name}\n"
            "📋 {legal_structure_label} · {tax_id}\n"
            "🏷 {business_type_label}\n"
            "{accountant_line}"
            "\nTap ✅ to create the account."
        ),
    },
    "onb_accountant_line_yes": {
        "he": "🤝 רואה חשבון: {contact}\n",
        "ar": "🤝 المحاسب: {contact}\n",
        "en": "🤝 Accountant: {contact}\n",
    },
    "onb_accountant_line_none": {
        "he": "🤝 רואה חשבון: ללא\n",
        "ar": "🤝 المحاسب: غير محدد\n",
        "en": "🤝 Accountant: none\n",
    },
    "btn_onb_confirm":    {"he": "✅ צור חשבון", "ar": "✅ إنشاء",    "en": "✅ Create"},
    "btn_onb_edit":       {"he": "✏️ ערוך",       "ar": "✏️ تعديل",   "en": "✏️ Edit"},
    "btn_onb_cancel":     {"he": "✖ ביטול",       "ar": "✖ إلغاء",    "en": "✖ Cancel"},

    "onb_creating": {
        "he": "⏳ יוצר את החשבון...",
        "ar": "⏳ جار إنشاء الحساب...",
        "en": "⏳ Creating your account...",
    },
    "onb_success": {
        "he": (
            "🎉 *ברוך הבא לאורורה!*\n"
            "החשבון שלך נוצר ({display_name}). אפשר להתחיל מיד —\n"
            "✅ ליצירת חשבונית: שלח 'חשבונית'\n"
            "✅ למצב חשבון: שלח 'מאזן'"
        ),
        "ar": (
            "🎉 *أهلا في أورورا!*\n"
            "تم إنشاء حسابك ({display_name}). ابدأ الآن —\n"
            "✅ لإنشاء فاتورة: أرسل 'فاتورة'\n"
            "✅ لرصيد الحساب: أرسل 'رصيد'"
        ),
        "en": (
            "🎉 *Welcome to Aurora!*\n"
            "Your account ({display_name}) is ready. Try —\n"
            "✅ Send 'invoice' to issue one\n"
            "✅ Send 'balance' for your numbers"
        ),
    },
    "onb_cancelled": {
        "he": "ביטלת את ההרשמה. אפשר לחזור בכל עת — שלח 'הרשמה'.",
        "ar": "ألغيت التسجيل. عد متى شئت — أرسل 'تسجيل'.",
        "en": "Signup cancelled. Send 'register' to start again any time.",
    },
    "onb_failed": {
        "he": "⚠️ משהו השתבש: {error}\nנסה שוב — שלח 'הרשמה'.",
        "ar": "⚠️ حدث خطأ: {error}\nأعد المحاولة — أرسل 'تسجيل'.",
        "en": "⚠️ Something went wrong: {error}\nTry again — send 'register'.",
    },
}


# ─────────────────────────────────────────────────────────────
# PUBLIC: t() — look up a string by key and language
# ─────────────────────────────────────────────────────────────
def t(key: str, lang: str = "he", **kwargs) -> str:
    """
    Fetch a localized string and interpolate any {placeholders}.

    Fallback order: requested lang → Hebrew → English → raw key.

    Example:
        t("pair_success", "ar", email="ibrahim@asg.com")
        → "تم ربط الحساب ✅ ibrahim@asg.com\\nكيف تريد المتابعة؟"
    """
    entry = STRINGS.get(key)
    if not entry:
        return key  # visible bug marker, not a crash

    template = entry.get(lang) or entry.get("he") or entry.get("en") or key

    try:
        return template.format(**kwargs) if kwargs else template
    except (KeyError, IndexError):
        # Missing placeholder — return the raw template so the mistake
        # is obvious in logs and the UI, instead of raising.
        return template


# ─────────────────────────────────────────────────────────────
# PUBLIC: locale helpers
# ─────────────────────────────────────────────────────────────
SUPPORTED_LANGS = ("he", "ar", "en")


def normalize_lang(lang: str | None) -> str:
    """Clamp to a supported language, default 'he'."""
    if not lang:
        return "he"
    code = lang.strip().lower()[:2]
    return code if code in SUPPORTED_LANGS else "he"


def is_rtl(lang: str) -> bool:
    """True if the given language is written right-to-left."""
    return lang in ("he", "ar")


def detect_lang_switch(text: str) -> str | None:
    """
    Detect if the user typed a language-switch shortcut.
    Returns 'he' / 'ar' / 'en' or None.
    """
    if not text:
        return None
    s = text.strip().lower()
    if s in ("עברית", "hebrew", "he"):
        return "he"
    if s in ("عربي", "عربية", "arabic", "ar"):
        return "ar"
    if s in ("english", "en", "אנגלית", "إنجليزية"):
        return "en"
    return None
