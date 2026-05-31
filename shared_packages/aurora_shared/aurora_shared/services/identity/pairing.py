"""
ASG Solutions — Generalized Pairing Codes
============================================
Sprint 1 of the Tax & Document Layer.

Generalizes the pairing-code mechanism that today lives in
`app/services/whatsapp_identity.py`. This module is the canonical home
for the pairing primitive going forward; the WhatsApp module re-uses it.

WHY ONE MODULE INSTEAD OF TWO:
  We have at least two transports that need pairing:
    - WhatsApp (already built, Phase 5)
    - Telegram (already built, Phase 4)
  And likely a third:
    - SMS-link onboarding (viral signup loop, Sprint 5/6)
  Centralizing here means one rotation policy, one TTL, one audit trail.

NOTE — TRANSPORT PARALLELISM:
  This file currently delegates to `whatsapp_identity` for WhatsApp-
  specific specifics (the wa.me deep link). A future refactor will
  move that into a transport-agnostic interface. For now the duplication
  is acceptable; the goal of this file is to provide a stable import
  path (`from aurora_shared.services.identity import generate_pairing_code, ...`)
  that survives that refactor.

USAGE:
    from aurora_shared.services.identity import generate_pairing_code, verify_pairing_code

    # Web flow — generate code, show in dashboard:
    payload = generate_pairing_code(user_id=42, db=db)
    # → {"code": "482913", "expires_in_seconds": 600,
    #    "instruction": "LINK-482913",
    #    "wa_me_url": "https://wa.me/9725...?text=LINK-482913"}

    # Inbound WhatsApp message — verify code:
    user = verify_pairing_code(phone_e164="+972501234567",
                               code="482913", db=db)
    if user: ...
"""

# We re-export the existing implementations from whatsapp_identity
# rather than duplicating the code. This is intentional — Sprint 1
# is about establishing the new IMPORT PATH; the implementation move
# is a non-functional follow-up.

from aurora_shared.services.whatsapp_identity import (
    generate_pairing_code,
    verify_pairing_code,
    PAIRING_CODE_TTL_MINUTES,
    normalize_phone,
)

__all__ = [
    "generate_pairing_code",
    "verify_pairing_code",
    "PAIRING_CODE_TTL_MINUTES",
    "normalize_phone",
]
