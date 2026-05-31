"""
Aurora LTS — Banking Router (P2-06)
======================================
Three endpoints for the reconciliation engine:

  POST /api/v1/banking/statements/upload
       Upload a CSV of bank statement rows. Headers:
       posted_at,amount,currency,counterparty_name,reference,external_id
       Inserts BankStatementEntry rows, then runs reconcile_pending
       and returns counts.

  GET  /api/v1/banking/statements/unmatched
       List unmatched + suggested entries the operator needs to review.

  POST /api/v1/banking/statements/{entry_id}/confirm-match
       Manually link a statement entry to a specific invoice OR
       mark it as 'ignored'.
"""
from __future__ import annotations

import csv
import datetime
import io
import logging
from typing import Literal, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.database.models import BankStatementEntry, Invoice, User
from app.middleware.auth_middleware import get_current_user
from app.middleware.rate_limit import limiter
from app.services.reconciliation import reconcile_pending

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/banking", tags=["banking"])


_REQUIRED_CSV_COLS = {"posted_at", "amount"}


class UploadResponse(BaseModel):
    inserted_count: int
    skipped_duplicates: int
    linked: int
    suggested: int
    unmatched: int


class ConfirmMatchRequest(BaseModel):
    invoice_id: Optional[int] = Field(None, description="Invoice to link, or omit for ignore")
    action: Literal["link", "ignore"] = "link"


class EntryOut(BaseModel):
    id: int
    posted_at: datetime.datetime
    amount: float
    counterparty_name: Optional[str]
    reference: Optional[str]
    match_status: str
    matched_invoice_id: Optional[int]
    match_confidence: Optional[float]
    match_reason: Optional[str]


@router.post(
    "/statements/upload",
    response_model=UploadResponse,
)
@limiter.limit("10/minute")
async def upload_statement(
    request: Request,
    business_id: int = Form(...),
    source_bank: str = Form("manual"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UploadResponse:
    if current_user.role != "admin" and current_user.business_id != business_id:
        raise HTTPException(status_code=403, detail="not_authorized_for_business")

    raw = (await file.read()).decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(raw))
    if reader.fieldnames is None or not _REQUIRED_CSV_COLS.issubset(reader.fieldnames):
        raise HTTPException(
            status_code=400,
            detail=f"CSV must have at minimum columns: {sorted(_REQUIRED_CSV_COLS)}. "
                   f"Got: {reader.fieldnames}",
        )

    inserted = 0
    skipped = 0

    for row in reader:
        try:
            posted_at = _parse_dt(row.get("posted_at"))
            amount = float(row.get("amount") or 0)
        except (TypeError, ValueError) as exc:
            log.warning("[banking] skipped row (bad fields): %s — %s", row, exc)
            skipped += 1
            continue

        external_id = (row.get("external_id") or "").strip() or None

        if external_id:
            existing = (
                db.query(BankStatementEntry)
                .filter(
                    BankStatementEntry.business_id == business_id,
                    BankStatementEntry.external_id == external_id,
                )
                .first()
            )
            if existing is not None:
                skipped += 1
                continue

        entry = BankStatementEntry(
            business_id=business_id,
            posted_at=posted_at,
            amount=amount,
            currency=(row.get("currency") or "ILS").strip().upper()[:3],
            counterparty_name=(row.get("counterparty_name") or "").strip() or None,
            reference=(row.get("reference") or "").strip() or None,
            source_bank=source_bank.strip()[:40] or "manual",
            external_id=external_id,
        )
        db.add(entry)
        inserted += 1

    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        log.error("[banking] CSV ingest commit failed: %s", exc)
        raise HTTPException(status_code=500, detail="statement_ingest_failed")

    # Run the matcher over all pending rows for this business.
    summary = reconcile_pending(db, business_id)

    return UploadResponse(
        inserted_count=inserted,
        skipped_duplicates=skipped,
        linked=summary["linked"],
        suggested=summary["suggested"],
        unmatched=summary["unmatched"],
    )


@router.get(
    "/statements/unmatched",
    response_model=list[EntryOut],
)
@limiter.limit("60/minute")
def list_unmatched(
    request: Request,
    business_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[EntryOut]:
    if current_user.role != "admin" and current_user.business_id != business_id:
        raise HTTPException(status_code=403, detail="not_authorized_for_business")

    rows = (
        db.query(BankStatementEntry)
        .filter(
            BankStatementEntry.business_id == business_id,
            BankStatementEntry.match_status.in_(("unmatched", "suggested")),
        )
        .order_by(BankStatementEntry.posted_at.desc())
        .all()
    )
    return [_to_out(r) for r in rows]


@router.post("/statements/{entry_id}/confirm-match")
@limiter.limit("60/minute")
def confirm_match(
    entry_id: int,
    payload: ConfirmMatchRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    entry = db.query(BankStatementEntry).filter(BankStatementEntry.id == entry_id).first()
    if entry is None:
        raise HTTPException(status_code=404, detail="entry_not_found")
    if current_user.role != "admin" and current_user.business_id != entry.business_id:
        raise HTTPException(status_code=403, detail="not_authorized_for_business")

    if payload.action == "ignore":
        entry.match_status = "ignored"
        entry.matched_invoice_id = None
        entry.matched_at = datetime.datetime.utcnow()
        db.commit()
        return {"id": entry.id, "match_status": "ignored"}

    if payload.invoice_id is None:
        raise HTTPException(status_code=400, detail="invoice_id_required_for_link")
    invoice = db.query(Invoice).filter(Invoice.id == payload.invoice_id).first()
    if invoice is None:
        raise HTTPException(status_code=404, detail="invoice_not_found")
    if invoice.business_id != entry.business_id:
        raise HTTPException(status_code=400, detail="invoice_business_mismatch")

    entry.matched_invoice_id = invoice.id
    entry.match_status = "linked"
    entry.match_confidence = 1.0
    entry.match_reason = f"manually_confirmed_by_user_{current_user.id}"
    entry.matched_at = datetime.datetime.utcnow()
    db.commit()

    # P2-07: manual confirmation creates the matching InvoicePayment.
    from app.services.payments_service import apply_bank_match
    apply_bank_match(db, bank_entry=entry, invoice=invoice)

    return {"id": entry.id, "match_status": "linked", "matched_invoice_id": invoice.id}


def _to_out(e: BankStatementEntry) -> EntryOut:
    return EntryOut(
        id=e.id,
        posted_at=e.posted_at,
        amount=e.amount,
        counterparty_name=e.counterparty_name,
        reference=e.reference,
        match_status=e.match_status,
        matched_invoice_id=e.matched_invoice_id,
        match_confidence=e.match_confidence,
        match_reason=e.match_reason,
    )


def _parse_dt(value: Optional[str]) -> datetime.datetime:
    if not value:
        raise ValueError("posted_at is required")
    # Try ISO first, then common Israeli formats.
    for fmt in (None, "%Y-%m-%d", "%d/%m/%Y", "%d.%m.%Y", "%Y-%m-%d %H:%M:%S"):
        try:
            if fmt is None:
                return datetime.datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
            return datetime.datetime.strptime(value.strip(), fmt)
        except ValueError:
            continue
    raise ValueError(f"unparseable posted_at: {value!r}")
