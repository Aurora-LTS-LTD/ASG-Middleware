"""Aurora LTS — Phase 33: Support / Tickets (v3.1).

Creates `tickets` + `ticket_messages` for the CEO Dashboard Support module.
Idempotent (create_all checkfirst). Prod applies via app.db_setup; the Alembic
revision 0011 covers Alembic environments. Mirrors migrate_phase32.
"""
from __future__ import annotations
import logging
from aurora_shared.database.connection import get_engine
from aurora_shared.database import models

log = logging.getLogger(__name__)


def run() -> None:
    try:
        eng = get_engine()
        models.Base.metadata.create_all(
            bind=eng,
            tables=[models.Ticket.__table__, models.TicketMessage.__table__],
            checkfirst=True,
        )
        log.info("[phase33] ensured tickets + ticket_messages exist")
    except Exception as e:
        log.warning("[phase33] could not create ticket tables: %s", e)
