"""
ASG Solutions — Database Models
================================
This file defines the "shape" of our data — what information we
store about businesses, invoices, and activity logs.

REAL-WORLD ANALOGY:
Think of each class below as a blank FORM:
  - Business form: name, phone number, type of business...
  - Invoice form: who's paying, how much, tax details...
  - ActionLog form: what happened, when, was it successful...

SQLAlchemy takes these Python classes and creates actual database
tables from them. Each row in the table is one filled-out form.
"""

# ─────────────────────────────────────────────────────────────
# IMPORTS
# ─────────────────────────────────────────────────────────────
import datetime
import uuid

from sqlalchemy import (
    Column,         # Defines a column in the table
    Integer,        # Whole numbers (1, 2, 3...)
    String,         # Text ("Hello", "INV-001"...)
    Float,          # Decimal numbers (1000.50, 0.18...)
    DateTime,       # Date and time (2026-04-13 14:30:00)
    Date,           # Date only (no time component) — used for vat_registered_at
    ForeignKey,     # Links one table to another
    Boolean,        # True/False values
    UniqueConstraint, # Composite uniqueness (e.g., one membership per user+org)
    Index,          # Named index for faster lookups
    LargeBinary,    # Raw bytes (challenge_bytes for native shell handshake — Sprint 8.2)
    text as sa_text,  # Raw SQL fragment — used for partial-index WHERE clauses
    JSON,           # Cross-dialect JSON — falls back to TEXT on SQLite
    CheckConstraint, # Sprint 8.3 — 7-year retention + soft-delete invariants
)
from sqlalchemy.dialects.postgresql import JSONB  # Sprint 8.3 — JSONB on Postgres
from sqlalchemy.orm import relationship  # Defines connections between tables

from app.database.connection import Base  # The base class all models inherit from


# ═══════════════════════════════════════════════════════════════
# MODEL 1: Business
# ═══════════════════════════════════════════════════════════════
# Represents a business that uses our platform.
# Each business can have many invoices.
#
# REAL-WORLD ANALOGY:
#   This is like a client file in a filing cabinet. It stores
#   the basic info about each business we serve.
# ═══════════════════════════════════════════════════════════════
class Business(Base):
    __tablename__ = "businesses"  # The actual table name in the database

    # ── Primary Key ──
    # Every table needs a unique ID for each row.
    # "primary_key=True" means this is the unique identifier.
    # "index=True" makes lookups faster (like an index in a book).
    id = Column(Integer, primary_key=True, index=True)

    # ── Business Details ──
    name = Column(String, nullable=False)          # Business name (required)
    phone = Column(String, nullable=True)           # WhatsApp phone number
    business_type = Column(String, nullable=True)   # e.g., "restaurant", "garage"
    status = Column(String, default="active")       # "active" or "inactive"

    # ── Business Profile (for invoices/PDF) ──
    # These details appear on generated invoices and PDF documents.
    tax_id = Column(String, nullable=True)          # Business ח.פ / ע.מ (tax ID)
    logo_url = Column(String, nullable=True)        # Path to logo image file
    address = Column(String, nullable=True)         # Business street address

    # ── Portal Token ──
    # A unique token for the client portal (like a private URL key).
    # uuid.uuid4()[:12] generates a random 12-character string.
    portal_token = Column(
        String,
        unique=True,
        default=lambda: str(uuid.uuid4())[:12],
    )

    # ── Timestamps ──
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    # ── Relationships ──
    # "invoices" lets us access all invoices for this business.
    # Example: business.invoices → [Invoice1, Invoice2, ...]
    invoices = relationship("Invoice", back_populates="business")


# ═══════════════════════════════════════════════════════════════
# MODEL 2: Invoice
# ═══════════════════════════════════════════════════════════════
# Represents a tax invoice issued by a business.
# Contains all financial details + Israeli tax compliance data.
#
# REAL-WORLD ANALOGY:
#   This is the actual invoice document — who it's for, how much,
#   what tax was added, and whether the government approved it.
#
# INVOICE LIFECYCLE:
#   draft → finalized → sent → (optionally cancelled)
#   - draft: created but not yet locked
#   - finalized: locked, allocation number obtained if needed
#   - sent: delivered to the customer via WhatsApp
#   - cancelled: voided (rare)
# ═══════════════════════════════════════════════════════════════
class Invoice(Base):
    __tablename__ = "invoices"

    # ── Identifiers ──
    id = Column(Integer, primary_key=True, index=True)
    business_id = Column(Integer, ForeignKey("businesses.id"))
    invoice_number = Column(String, index=True)  # e.g., "INV-1-0001"

    # ── Beneficiary (recipient) Details ──
    # The person or company receiving the invoice.
    beneficiary_name = Column(String)                    # Their name
    beneficiary_tax_id = Column(String, nullable=True)   # Their tax ID (ח.פ / ת.ז)
    beneficiary_contact = Column(String, nullable=True)  # Phone or email

    # ── Financial Details ──
    amount_net = Column(Float)                   # Amount BEFORE tax (סכום לפני מע"מ)
    vat_rate = Column(Float, default=0.18)       # VAT rate: 18% for 2026
    vat_amount = Column(Float)                   # The tax portion (סכום המע"מ)
    amount_total = Column(Float)                 # Net + VAT (סכום כולל מע"מ)
    currency = Column(String, default="ILS")     # Israeli Shekel

    # ── Tax Authority Compliance ──
    # These fields track whether the invoice needs government approval
    # (an "allocation number" / מספר הקצאה) and the result.
    requires_allocation = Column(Integer, default=0)       # 1 = yes, 0 = no
    allocation_number = Column(String, nullable=True)      # 9-digit number from ITA
    allocation_status = Column(String, default="pending")  # pending/approved/not_required/failed/retry_pending

    # ── Allocation Retry (for Telegram bot resilience) ──
    # When the ITA service is temporarily down, the bot queues a retry
    # instead of showing an error. These fields track that queue.
    allocation_retry_count = Column(Integer, default=0)         # How many times we've retried
    allocation_next_retry_at = Column(DateTime, nullable=True)  # When to try again

    # ── Sprint 3: real-ITA tracking ──
    # When ITA_BACKEND=production these columns capture the per-attempt
    # request id (idempotency key) and the sanitised ITA response. Stays
    # NULL on mock-backend rows.
    ita_request_id = Column(String, nullable=True)
    ita_response_raw_json = Column(String, nullable=True)
    ita_status_code = Column(Integer, nullable=True)
    allocation_issued_at = Column(DateTime, nullable=True)

    # ── Document & Status ──
    pdf_url = Column(String, nullable=True)          # Link to PDF (future)
    status = Column(String, default="draft")         # draft/finalized/sent/cancelled
    description = Column(String, nullable=True)      # Optional note

    # ── P2-05: Credit note discriminator ──
    # kind="standard"     → a normal invoice (default).
    # kind="credit_note"  → a חשבונית זיכוי that REFERENCES the original
    #                        invoice via original_invoice_id. Its
    #                        amount_net/vat/total are NEGATIVE, so the
    #                        existing VAT report + ITA allocation flow
    #                        already nets them out without special-casing.
    kind = Column(String(16), default="standard", nullable=False, index=True)
    original_invoice_id = Column(
        Integer, ForeignKey("invoices.id"), nullable=True, index=True,
    )

    # ── Appendix I Sprint 2 — 7-year compliance archive ──
    # Path inside gs://aurora-pdfs-prod/ (e.g., "invoices/2026/05/INV-00042.pdf").
    # Separate from `pdf_url` (which may be a temporary signed URL or empty).
    gcs_file_path = Column(String(400), nullable=True)
    # Current storage class as managed by Cloud Storage lifecycle.
    # Values: standard | nearline | coldline | archive | deleted
    retention_class = Column(String(20), nullable=False, default="standard")
    # WhatsApp Document Bot tracking
    last_retrieval_at = Column(DateTime, nullable=True)
    retrieval_count = Column(Integer, nullable=False, default=0)
    # Legal hold flag — when true, GCS lifecycle is bypassed (moved to a
    # held/ prefix manually). Defaults False.
    legal_hold = Column(Boolean, nullable=False, default=False)

    # ── Payment Tracking ──
    # These fields are updated as payments come in.
    # They do NOT affect VAT or allocation — those are locked at creation.
    due_date = Column(DateTime, nullable=True)           # finalized_at + 30 days
    payment_status = Column(String, default="unpaid")    # unpaid / partial / paid
    amount_paid = Column(Float, default=0.0)             # Running total of payments received

    # ── Timestamps ──
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    finalized_at = Column(DateTime, nullable=True)   # When it was finalized

    # ── Relationships ──
    business = relationship("Business", back_populates="invoices")


# ═══════════════════════════════════════════════════════════════
# MODEL 3: ActionLog
# ═══════════════════════════════════════════════════════════════
# Records everything that happens in the system — messages received,
# invoices created, allocations requested, errors encountered.
#
# REAL-WORLD ANALOGY:
#   This is the security camera footage of the hotel. Every event
#   is recorded: who came in, what they did, whether it worked.
#   Useful for debugging and monitoring.
# ═══════════════════════════════════════════════════════════════
class ActionLog(Base):
    __tablename__ = "action_logs"

    id = Column(Integer, primary_key=True, index=True)

    # Which business this action is related to (optional — some
    # actions like incoming WhatsApp messages might not be linked
    # to a known business yet).
    business_id = Column(Integer, ForeignKey("businesses.id"), nullable=True)

    # ── Action Details ──
    status = Column(String)              # "received", "sent", "finalized", "error"
    detail = Column(String)              # Human-readable description of what happened
    triggered_at = Column(
        DateTime,
        default=datetime.datetime.utcnow,
    )


# ═══════════════════════════════════════════════════════════════
# MODEL 4: User
# ═══════════════════════════════════════════════════════════════
# Represents a person who can log into the admin dashboard.
# Two roles exist:
#   - "admin"          → sees everything, manages all businesses
#   - "business_owner" → sees only their own business data
#
# REAL-WORLD ANALOGY:
#   This is like a hotel keycard system. The admin has a master key
#   that opens every room. A business owner has a key that only
#   opens their own room.
# ═══════════════════════════════════════════════════════════════
class User(Base):
    __tablename__ = "users"

    # ── Primary Key ──
    id = Column(Integer, primary_key=True, index=True)

    # ── Login Credentials ──
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)  # Bcrypt hash, never plain text

    # ── Profile ──
    # full_name stays for backwards compatibility — populated from
    # first_name + " " + last_name during the expand/contract migration window.
    full_name = Column(String, nullable=False)
    first_name = Column(String, nullable=True)       # Sprint 1 addition (split from full_name)
    last_name = Column(String, nullable=True)        # Sprint 1 addition
    fax = Column(String, nullable=True)              # Optional, Israeli regulatory legacy

    role = Column(String, default="business_owner")
    # Roles:
    #   "admin"          — sees everything, manages all organizations
    #   "business_owner" — owner of one or more organizations (legacy alias for "owner" role on a Membership)
    #   "accountant"     — external CPA, sees engaged organizations via AccountantEngagement
    #   "employee"       — staff member of an organization, scoped permissions

    # ── Business Link (LEGACY — being deprecated via expand/contract) ──
    # If role is "business_owner", this links to their specific business.
    # During Sprint 1 → Sprint 5 we migrate to Membership ↔ Organization (many-to-many).
    # This column becomes a denormalized cache fed by a DB trigger in S3, dropped in S5.
    # DO NOT add new readers of this column. Use resolve_user_context() instead.
    business_id = Column(Integer, ForeignKey("businesses.id"), nullable=True)

    # ── Settings ──
    is_active = Column(Boolean, default=True)       # Can be disabled without deleting
    language_pref = Column(String, default="ar")     # "ar" | "he" | "en"

    # ── Onboarding & Verification (Sprint 1 / Onboarding Module) ──
    # Tracks the multi-step web onboarding journey. Read by both the onboarding
    # router and the WhatsApp ONBOARDING:* FSM so a user can resume across surfaces.
    onboarding_status = Column(String, default="not_started")
    # not_started | identity | phone_otp | email_otp | documents | billing | review | active | suspended
    email_verified_at = Column(DateTime, nullable=True)
    phone_verified_at = Column(DateTime, nullable=True)

    # Versioned T&C / privacy acceptance — evidence for the ITA Software House binder.
    terms_accepted_version = Column(String, nullable=True)    # e.g. "2026-04"
    terms_accepted_at = Column(DateTime, nullable=True)
    privacy_accepted_version = Column(String, nullable=True)
    privacy_accepted_at = Column(DateTime, nullable=True)

    # ── Telegram Integration ──
    # Filled in once the user links their Telegram account via the pairing flow.
    telegram_user_id = Column(String, nullable=True, index=True)   # Telegram numeric user ID (as string)
    telegram_pairing_code = Column(String, nullable=True)           # 6-digit one-time code
    telegram_pairing_expires = Column(DateTime, nullable=True)      # Code expiry timestamp
    morning_digest_enabled = Column(Boolean, default=False)         # Opt-in daily 08:30 briefing

    # ── WhatsApp Integration (Phase 5) ──
    # The phone number the user messages the bot from (E.164, e.g. +972501234567).
    # Unique across all users — one phone = one business owner identity.
    whatsapp_phone_e164 = Column(String, nullable=True, unique=True, index=True)
    whatsapp_pairing_code = Column(String, nullable=True)           # 6-digit, generalized
    whatsapp_pairing_expires = Column(DateTime, nullable=True)      # 10-minute TTL

    # ── Timestamps ──
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    # ── Relationships ──
    business = relationship("Business")


# ═══════════════════════════════════════════════════════════════
# MODEL 5: Payment
# ═══════════════════════════════════════════════════════════════
# Records every individual payment received against an invoice.
# One invoice can have many payments (partial payments are common).
#
# REAL-WORLD ANALOGY:
#   The invoice is the bill. A Payment is a receipt — proof that
#   money was handed over. If a customer pays half now and half
#   next month, that's two Payment records on one invoice.
#
# TAX COMPLIANCE NOTE:
#   Payments do NOT recalculate VAT or allocation numbers.
#   Those are locked when the invoice is created/finalized.
#   Payments only track how much of the total has been collected.
# ═══════════════════════════════════════════════════════════════
class Payment(Base):
    __tablename__ = "payments"

    # ── Primary Key ──
    id = Column(Integer, primary_key=True, index=True)

    # ── Links ──
    # Which invoice and business this payment belongs to.
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False)
    business_id = Column(Integer, ForeignKey("businesses.id"), nullable=False)

    # ── Payment Details ──
    amount = Column(Float, nullable=False)            # Amount paid in this transaction
    method = Column(String, nullable=False)            # cash / transfer / credit / check
    reference = Column(String, nullable=True)          # Check number, transfer ID, etc.
    payment_date = Column(DateTime, nullable=False)    # When the payment was made
    notes = Column(String, nullable=True)              # Optional note about this payment

    # ── Who recorded it ──
    recorded_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    # ── Timestamps ──
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    # ── Relationships ──
    invoice = relationship("Invoice")
    business = relationship("Business")


# ═══════════════════════════════════════════════════════════════
# MODEL 6: TelegramSession
# ═══════════════════════════════════════════════════════════════
# Tracks the state of a Telegram user's current bot conversation.
# There is at most ONE active session per Telegram user at a time.
#
# REAL-WORLD ANALOGY:
#   Imagine the bot is a bank teller window. Each customer (telegram_user_id)
#   has a "ticket" that says: "this person is halfway through filling out
#   a deposit form — they've entered the amount and the name, but not
#   the description yet." If the customer walks away and comes back,
#   the teller can pick up exactly where they left off.
#
# This is why we store:
#   - state:              which step they're on ("INVOICE_AMOUNT", etc.)
#   - draft_payload_json: what they've entered so far, as JSON
#   - pending_message_id: the Telegram message we'll edit when ITA responds
# ═══════════════════════════════════════════════════════════════
class TelegramSession(Base):
    __tablename__ = "telegram_sessions"

    id = Column(Integer, primary_key=True, index=True)

    # ── Who this session belongs to ──
    telegram_user_id = Column(String, index=True, nullable=False)  # Telegram numeric user ID
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)         # Linked User record
    business_id = Column(Integer, ForeignKey("businesses.id"), nullable=True) # Their business

    # ── Conversation State ──
    state = Column(String, nullable=True)               # e.g. "INVOICE_AMOUNT", "INVOICE_CONFIRM"
    draft_payload_json = Column(String, nullable=True)  # JSON string with in-progress invoice data

    # ── Allocation Pending ──
    # When an invoice is created but allocation is being retried,
    # we store the Telegram message ID so we can edit it in-place
    # when the allocation finally succeeds.
    pending_message_id = Column(Integer, nullable=True)  # The "⏳ ממתין..." message to edit
    pending_invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=True)

    # ── Timestamps ──
    updated_at = Column(DateTime, default=datetime.datetime.utcnow)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    # ── Relationships ──
    user = relationship("User")
    business = relationship("Business")


# ═══════════════════════════════════════════════════════════════
# MODEL 7: WhatsAppSession   (Phase 5)
# ═══════════════════════════════════════════════════════════════
# Mirror of TelegramSession for the WhatsApp transport.
# One active session per phone number. Identified by the phone,
# not a Telegram ID — that's the only real difference from
# TelegramSession.
#
# REAL-WORLD ANALOGY:
#   Same "bank teller ticket" idea. The customer's phone number
#   is their ticket. As long as the ticket exists, the teller can
#   resume the conversation exactly where it left off.
#
# WHY JSON ON A STRING COLUMN (not JSONB):
#   SQLite has no JSONB. We store the draft as a JSON-encoded string
#   and parse it in Python. When we migrate to Postgres on GCP, this
#   column can be ALTERed to JSONB in-place.
# ═══════════════════════════════════════════════════════════════
class WhatsAppSession(Base):
    __tablename__ = "whatsapp_sessions"

    id = Column(Integer, primary_key=True, index=True)

    # ── Who this session belongs to ──
    whatsapp_phone_e164 = Column(String, index=True, nullable=False)     # E.164 phone identifier
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)     # Linked User (nullable until bound)
    business_id = Column(Integer, ForeignKey("businesses.id"), nullable=True)

    # ── Conversation state ──
    state = Column(String, nullable=True)               # e.g. "NEW_INVOICE:AMOUNT"
    draft_payload_json = Column(String, nullable=True)  # JSON string with in-progress invoice data
    locale = Column(String, default="he")               # "he" | "ar" | "en"

    # ── 24-hour window tracking (WhatsApp-specific) ──
    # Meta only allows freeform outbound messages for 24 hours after
    # the user's last inbound. After that, only approved templates work.
    last_client_message_at = Column(DateTime, nullable=True)

    # ── Allocation pending (mirrors TelegramSession) ──
    # The ID of the last bot message we might want to edit/supersede
    # when the ITA allocation lands asynchronously.
    pending_message_id = Column(String, nullable=True)  # Meta wamid (string, not int)
    pending_invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=True)

    # ── Timestamps ──
    updated_at = Column(DateTime, default=datetime.datetime.utcnow)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    # ── Relationships ──
    user = relationship("User")
    business = relationship("Business")


# ═══════════════════════════════════════════════════════════════
# MODEL 8: WhatsAppOutboundLog  (Phase 5)
# ═══════════════════════════════════════════════════════════════
# Every outbound message we send via Meta is logged here BEFORE the
# HTTP call. The delivery-status webhook later updates wamid/status.
# Enables:
#   - auto-resend of messages stuck in 'pending' > 5 min
#   - delivery analytics (sent / delivered / read / failed)
#   - audit trail for ITA compliance ("did we notify the client?")
#
# REAL-WORLD ANALOGY:
#   The mail clerk keeps a log of every letter handed to the postman,
#   *before* the postman walks out the door. If a letter never gets
#   a "delivered" stamp back, the clerk mails a duplicate.
# ═══════════════════════════════════════════════════════════════
class WhatsAppOutboundLog(Base):
    __tablename__ = "whatsapp_outbound_logs"

    id = Column(Integer, primary_key=True, index=True)

    # ── Who we sent it to ──
    whatsapp_phone_e164 = Column(String, index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    # ── What we sent ──
    message_kind = Column(String, nullable=False)   # "text" | "buttons" | "list" | "document" | "template" | "image"
    payload_json = Column(String, nullable=False)   # Full Meta API body (for resends)
    template_name = Column(String, nullable=True)   # Approved Meta template name, if kind == "template"

    # ── Tracking ──
    wamid = Column(String, nullable=True, index=True)  # Meta's unique message ID, returned after send
    status = Column(String, default="pending")         # pending | sent | delivered | read | failed
    attempts = Column(Integer, default=0)              # How many send attempts
    last_error = Column(String, nullable=True)

    # ── Timestamps ──
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow)


# ═══════════════════════════════════════════════════════════════
# MODEL 9: Organization   (Sprint 1 — Identity Foundation)
# ═══════════════════════════════════════════════════════════════
# An Organization is the LEGAL entity that owns invoices, expenses,
# subscriptions, and tax obligations. It is the unit of multi-tenancy
# going forward.
#
# RELATIONSHIP TO Business (LEGACY):
#   For each existing `businesses` row we create exactly one Organization
#   (1:1 backfill). Both tables coexist during the expand/contract window;
#   reads gradually migrate to Organization, and `businesses` is retired
#   in Sprint 5.
#
# ISRAELI LEGAL STRUCTURES:
#   - 'osek_morshe' (עוסק מורשה)  — Authorized Dealer, charges/reports VAT
#   - 'osek_patur'  (עוסק פטור)   — Exempt Dealer (under VAT threshold)
#   - 'chevra_baam' (חברה בע"מ)  — Limited liability company (Ltd)
#
# REAL-WORLD ANALOGY:
#   The Business row was a "client file" — a single customer.
#   The Organization is the same customer's official entity, with
#   government-recognized identity (Tax ID), legal structure, and
#   the address that appears on every tax document we generate.
# ═══════════════════════════════════════════════════════════════
class Organization(Base):
    __tablename__ = "organizations"

    # ── Identity ──
    id = Column(Integer, primary_key=True, index=True)
    display_name = Column(String, nullable=False)              # Public name (appears on invoices)
    legal_structure = Column(String, nullable=False)
    # 'osek_morshe' | 'osek_patur' | 'chevra_baam'
    tax_id = Column(String, nullable=False, index=True)        # 9-digit, Israeli mod-11 checksum
    tax_id_verified = Column(Boolean, default=False)           # True after KYC doc review
    vat_registered_at = Column(Date, nullable=True)            # Set for osek_morshe only
    industry_code = Column(String, nullable=True)              # ITA's "סיווג ענפי" code

    # ── Address ──
    business_address = Column(String, nullable=True)           # Street + number
    city = Column(String, nullable=True)
    postal_code = Column(String, nullable=True)
    country_code = Column(String, default="IL")
    website = Column(String, nullable=True)

    # ── Contact (non-personal — owner contacts live on User) ──
    business_phone = Column(String, nullable=True)
    business_email = Column(String, nullable=True)

    # ── KYC Lifecycle ──
    kyc_status = Column(String, default="pending")
    # pending | docs_uploaded | under_review | approved | rejected
    kyc_approved_at = Column(DateTime, nullable=True)
    kyc_approved_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    kyc_rejection_reason = Column(String, nullable=True)

    # ── Operational ──
    status = Column(String, default="active")                  # active | suspended | closed
    portal_token = Column(
        String,
        unique=True,
        default=lambda: str(uuid.uuid4())[:12],
    )

    # ── Migration Bridge (Expand/Contract) ──
    # Pointer to the legacy Business row this Organization was backfilled from.
    # NULL for organizations created natively post-migration.
    legacy_business_id = Column(Integer, ForeignKey("businesses.id"), nullable=True, index=True)

    # ── Appendix I Sprint 2 — Dynamic Category mapping (L3 of taxonomy) ──
    # Points at a business_categories row. NULL = uncategorized.
    # ON DELETE SET NULL: deleting a category un-assigns its orgs rather than
    # cascading destructively.
    category_id = Column(
        Integer,
        ForeignKey("business_categories.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # ── Timestamps ──
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(
        DateTime,
        default=datetime.datetime.utcnow,
        onupdate=datetime.datetime.utcnow,
    )

    # ── Relationships ──
    memberships = relationship("Membership", back_populates="organization", cascade="all, delete-orphan")
    accountant_engagements = relationship("AccountantEngagement", back_populates="organization")
    legacy_business = relationship("Business", foreign_keys=[legacy_business_id])
    category = relationship("BusinessCategory", foreign_keys=[category_id])


# ═══════════════════════════════════════════════════════════════
# MODEL 10: Membership   (Sprint 1)
# ═══════════════════════════════════════════════════════════════
# Joins a User to an Organization with a specific role.
# A user can have many memberships (e.g. owner of Org A, employee of Org B).
# An organization always has at least one member with role='owner'.
#
# REAL-WORLD ANALOGY:
#   A User is a person. An Organization is a company. A Membership is
#   the employment contract between them. One person can have multiple
#   employment contracts (works at two firms); the contract specifies
#   what they're allowed to do at each one.
#
# ROLE SEMANTICS:
#   'owner'    — full control, can invite/remove members, cancel subscription
#   'employee' — operational permissions (create invoices, record payments),
#                cannot delete or change billing/legal settings
# ═══════════════════════════════════════════════════════════════
class Membership(Base):
    __tablename__ = "memberships"

    id = Column(Integer, primary_key=True, index=True)

    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)

    role = Column(String, nullable=False, default="owner")     # 'owner' | 'employee'
    is_primary = Column(Boolean, default=False)
    # is_primary = the user's "default" org. One per user. Used when context
    # is ambiguous (e.g., generic dashboard load with no org_id query param).

    invited_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    invitation_id = Column(Integer, ForeignKey("invitations.id"), nullable=True)
    # Tracks which Invitation row this membership was accepted from (audit trail).

    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    # ── Relationships ──
    user = relationship("User", foreign_keys=[user_id])
    organization = relationship("Organization", back_populates="memberships")
    invited_by = relationship("User", foreign_keys=[invited_by_user_id])

    # ── Constraints ──
    __table_args__ = (
        UniqueConstraint("user_id", "organization_id", name="uq_membership_user_org"),
    )


# ═══════════════════════════════════════════════════════════════
# MODEL 11: AccountantEngagement   (Sprint 1)
# ═══════════════════════════════════════════════════════════════
# Connects an external CPA (User with role='accountant') to an
# Organization they advise. Distinct from Membership because:
#   - Accountants are external (not employees of the org)
#   - They can be revoked independently
#   - Revenue share is computed on this row, not on Membership
#
# REAL-WORLD ANALOGY:
#   Membership = "employment contract" between person and company.
#   AccountantEngagement = "advisory contract" between an outside CPA
#   firm and a company. Different contract type, different pay schedule,
#   different revocation process.
#
# REVENUE SHARE:
#   Each engagement carries `revenue_share_pct` (default 20.0). The
#   revenue_share_ledger (Sprint 5) joins on this row to compute payouts.
# ═══════════════════════════════════════════════════════════════
class AccountantEngagement(Base):
    __tablename__ = "accountant_engagements"

    id = Column(Integer, primary_key=True, index=True)

    accountant_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)

    status = Column(String, nullable=False, default="invited")
    # invited | active | suspended | revoked

    revenue_share_pct = Column(Float, default=20.0)            # 20% of MRR by default
    invited_at = Column(DateTime, default=datetime.datetime.utcnow)
    activated_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)
    revoked_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    revoked_reason = Column(String, nullable=True)

    # Optional scope JSON (future use): which sections of the org's data
    # the accountant can access (full read, invoices-only, exports-only, etc.).
    scope_json = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    # ── Relationships ──
    accountant_user = relationship("User", foreign_keys=[accountant_user_id])
    organization = relationship("Organization", back_populates="accountant_engagements")
    revoked_by = relationship("User", foreign_keys=[revoked_by_user_id])

    # ── Constraints ──
    # An accountant can have at most ONE active engagement per organization.
    # If revoked and re-invited, the old row stays (audit) and a new row is created.
    __table_args__ = (
        Index("ix_engagement_acct_org_status", "accountant_user_id", "organization_id", "status"),
    )


# ═══════════════════════════════════════════════════════════════
# MODEL 12: Invitation   (Sprint 1)
# ═══════════════════════════════════════════════════════════════
# A pending invite for someone to join an Organization (as employee or
# accountant). Single-use, time-limited code surfaces in URLs, emails,
# and WhatsApp templates.
#
# CODE FORMAT:
#   UUIDv4 — 36-character hex with dashes. Surfaces in URLs as a path
#   parameter: /invitations/{code}/accept
#
# TTL:
#   Default 72 hours. Configurable via environment.
#
# WHY NOT A 6-DIGIT CODE LIKE PAIRING?
#   Pairing codes secure a 1-step link with the user actively present on
#   both surfaces. Invitations may be acted on days later by someone who
#   never typed the code — they click an email or WhatsApp link. UUIDs
#   are appropriate for non-secret-but-unguessable URL tokens.
# ═══════════════════════════════════════════════════════════════
class Invitation(Base):
    __tablename__ = "invitations"

    id = Column(Integer, primary_key=True, index=True)

    code = Column(String, nullable=False, unique=True, index=True)  # UUIDv4

    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    invited_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    role = Column(String, nullable=False)                      # 'employee' | 'accountant'
    target_email = Column(String, nullable=True, index=True)
    target_phone_e164 = Column(String, nullable=True, index=True)
    display_name_hint = Column(String, nullable=True)          # For showing to the recipient

    status = Column(String, nullable=False, default="pending")
    # pending | accepted | expired | revoked

    expires_at = Column(DateTime, nullable=False)
    accepted_at = Column(DateTime, nullable=True)
    accepted_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    # ── Relationships ──
    organization = relationship("Organization")
    invited_by = relationship("User", foreign_keys=[invited_by_user_id])
    accepted_by = relationship("User", foreign_keys=[accepted_by_user_id])


# ═══════════════════════════════════════════════════════════════
# MODEL 13: OnboardingState   (Aurora Onboarding Module / Phase 6b)
# ═══════════════════════════════════════════════════════════════
# Tracks the multi-step web onboarding journey, surface-agnostic
# (web today; WhatsApp deep-link can resume the same row).
#
# WHY A SEPARATE TABLE FROM USER:
#   - User holds the persistent identity. OnboardingState holds the
#     transient drafting context — fields the user is still entering.
#   - Crashes mid-flow leave User intact and OnboardingState recoverable.
#   - Once activate() commits, the row stays as historical evidence
#     (audit) but is no longer read for live flow control.
#
# RESUMABILITY:
#   On any page load to /onboarding the frontend calls GET /onboarding/state.
#   If a state row exists for the JWT'd user, the wizard jumps to
#   `current_step` and pre-fills `draft_payload`.
# ═══════════════════════════════════════════════════════════════
class OnboardingState(Base):
    __tablename__ = "onboarding_states"

    # UUID surfaces in URLs (e.g., resume links emailed to the user)
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True, index=True)

    current_step = Column(String(20), nullable=False, default="identity")
    # identity | phone_otp | email_otp | documents | plan | payment_method
    # | first_charge | review | active | abandoned

    completed_steps = Column(String, default="[]")    # JSON array (string for SQLite portability)
    draft_payload = Column(String, default="{}")      # JSON dict (string for SQLite portability)

    surface = Column(String(12), default="web")       # web | whatsapp
    expires_at = Column(DateTime, nullable=False)     # 30-day TTL by default

    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(
        DateTime,
        default=datetime.datetime.utcnow,
        onupdate=datetime.datetime.utcnow,
    )

    user = relationship("User")


# ═══════════════════════════════════════════════════════════════
# MODEL 14: OtpVerification   (Aurora Onboarding Module / Phase 6b)
# ═══════════════════════════════════════════════════════════════
# One row per OTP issuance. Stores the BCRYPT HASH of the 6-digit code,
# never the plaintext. Even if this table leaks, OTPs cannot be replayed.
#
# LIFECYCLE:
#   pending  → consumed   (correct code submitted in time)
#   pending  → expired    (TTL reached)
#   pending  → locked     (5 wrong attempts)
#
# REAL-WORLD ANALOGY:
#   Hotel safe codes — the safe stores a one-way hash of the code,
#   not the code itself. Even the hotel manager can't recover the code,
#   they can only verify the guest's input matches.
# ═══════════════════════════════════════════════════════════════
class OtpVerification(Base):
    __tablename__ = "otp_verifications"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    channel = Column(String(8), nullable=False)        # 'phone' | 'email'
    target = Column(String(120), nullable=False)       # the phone/email being verified
    code_hash = Column(String, nullable=False)         # bcrypt hash of the 6-digit code
    purpose = Column(String(20), default="signup")
    # signup | login | step_up | change_phone | change_email

    expires_at = Column(DateTime, nullable=False)
    attempts = Column(Integer, default=0)
    status = Column(String(12), default="pending")     # pending | consumed | expired | locked

    consumed_at = Column(DateTime, nullable=True)
    last_ip = Column(String(45), nullable=True)        # IPv6-safe length

    created_at = Column(DateTime, default=datetime.datetime.utcnow)


# ═══════════════════════════════════════════════════════════════
# MODEL 15: KycDocument   (Aurora Onboarding Module / Phase 6b)
# ═══════════════════════════════════════════════════════════════
# Identity / business-certificate docs uploaded during onboarding.
# Stored in a SEPARATE GCS bucket (asg-kyc-prod) from receipts so
# the IAM, retention, and DLP policies can differ.
#
# UPLOAD FLOW:
#   1. POST /onboarding/documents/init-upload → server returns a
#      pre-signed PUT URL valid for 15 minutes + a doc_id.
#   2. Browser PUTs the bytes directly to GCS.
#   3. POST /onboarding/documents/finalize → server verifies the
#      upload landed, hashes the bytes, sets status='pending_review'.
#
# RETENTION:
#   7 years (Israeli tax retention obligation) — enforced by bucket
#   lifecycle policy in production.
#
# FIRST-50 MANUAL REVIEW:
#   Per Aurora Onboarding spec: the founder reviews the first 50
#   tenants' docs by hand, gathering ground-truth for later automation.
#   `status` flow: pending_review → approved | rejected
# ═══════════════════════════════════════════════════════════════
class KycDocument(Base):
    __tablename__ = "kyc_documents"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True, index=True)
    # nullable: docs can be uploaded BEFORE the Organization is committed
    # at activate(). The activate transaction backfills organization_id.
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    document_type = Column(String(40), nullable=False)
    # 'israeli_id_front' | 'israeli_id_back' |
    # 'business_certificate' | 'vat_certificate' |
    # 'company_registry_extract' | 'signature_card'

    # GCS coordinates
    gcs_bucket = Column(String(64), default="asg-kyc-prod")
    gcs_object_key = Column(String, nullable=False)

    sha256 = Column(String(64), nullable=True, index=True)  # populated on finalize
    mime_type = Column(String(64), nullable=True)
    bytes_size = Column(Integer, nullable=True)
    pages_count = Column(Integer, default=1)

    status = Column(String(20), default="pending_upload")
    # pending_upload | pending_review | approved | rejected | expired

    rejection_reason = Column(String, nullable=True)
    reviewed_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)

    expires_at = Column(DateTime, nullable=True)            # for time-bound certs
    retention_class = Column(String(16), default="archive_7y")

    created_at = Column(DateTime, default=datetime.datetime.utcnow)


# ═══════════════════════════════════════════════════════════════
# MODEL 16: Subscription   (Aurora Onboarding Module / Phase 6b)
# ═══════════════════════════════════════════════════════════════
# One Subscription per Organization. Tracks plan, billing cycle,
# trial state, and the active payment method.
#
# AMOUNT IN MINOR UNITS (agorot for ILS):
#   Stored as an Integer to avoid float drift on money. ₪99.00 = 9900.
#   Always convert at the edges (display layer).
#
# TRIAL POSTURE:
#   Aurora gives a 14-day free trial. Subscription is created with
#   status='trialing', trial_ends_at=now+14d. A pending
#   SubscriptionPayment is scheduled for trial_ends_at. The first
#   tax invoice is generated only when that payment SUCCEEDS — never
#   on a ₪0 trial (ITA does not require/expect zero-value invoices).
# ═══════════════════════════════════════════════════════════════
class Subscription(Base):
    __tablename__ = "subscriptions"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False,
                             unique=True, index=True)

    plan = Column(String(16), nullable=False)              # 'starter' | 'pro' | 'enterprise'
    billing_cycle = Column(String(12), nullable=False)     # 'monthly' | 'quarterly' | 'annual'

    cycle_amount_minor_units = Column(Integer, nullable=False)
    # ₪ in agorot. e.g. monthly starter = 9900 = ₪99.

    currency = Column(String(3), default="ILS")
    discount_pct = Column(Float, default=0.0)              # quarterly: 5%, annual: 15%

    status = Column(String(20), default="trialing")
    # trialing | active | past_due | cancelled | suspended

    payment_method_id = Column(Integer, ForeignKey("payment_methods.id"), nullable=True)

    trial_ends_at = Column(DateTime, nullable=True)
    started_at = Column(DateTime, nullable=True)
    current_period_start = Column(DateTime, nullable=True)
    current_period_end = Column(DateTime, nullable=True)
    cancel_at_period_end = Column(Boolean, default=False)
    cancelled_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(
        DateTime,
        default=datetime.datetime.utcnow,
        onupdate=datetime.datetime.utcnow,
    )


# ═══════════════════════════════════════════════════════════════
# MODEL 17: PaymentMethod   (Aurora Onboarding Module / Phase 6b)
# ═══════════════════════════════════════════════════════════════
# Tokenized references to a payment instrument. We NEVER store the
# Primary Account Number (PAN), CVV, or full card data — the iframe
# from PayPlus tokenizes those at the provider's vault, and we keep
# only an opaque reference plus the last-4-digits for display.
#
# PCI-DSS POSTURE:
#   This shape keeps Aurora at SAQ-A or SAQ-A-EP scope (out of full
#   Level 1 scope), saving ~₪50k/year and significant audit time.
#
# DIRECT DEBIT (הוראת קבע):
#   Israeli local payment standard. PayPlus and competitors expose
#   APIs to set up a mandate and pull periodic charges. We capture
#   bank_code/branch_code/account_last4 for display + reconciliation
#   but the actual debit authorization is held by the provider.
# ═══════════════════════════════════════════════════════════════
class PaymentMethod(Base):
    __tablename__ = "payment_methods"

    id = Column(Integer, primary_key=True, index=True)
    # NULLABLE: a payment method is captured DURING onboarding, BEFORE the
    # Organization row is committed at activate(). The activate() handler
    # back-links this column to the new org_id atomically.
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True, index=True)

    kind = Column(String(16), nullable=False)              # 'credit_card' | 'direct_debit'
    provider = Column(String(20), nullable=False)          # 'payplus' | 'tranzila' | ...
    provider_token = Column(String, nullable=False)        # opaque vault reference

    # Credit-card metadata (NOT the PAN — provider holds that)
    card_last4 = Column(String(4), nullable=True)
    card_brand = Column(String(20), nullable=True)
    card_exp_month = Column(Integer, nullable=True)
    card_exp_year = Column(Integer, nullable=True)

    # Direct-debit metadata
    bank_code = Column(String(4), nullable=True)
    branch_code = Column(String(4), nullable=True)
    account_last4 = Column(String(4), nullable=True)

    holder_name = Column(String, nullable=True)
    status = Column(String(16), default="active")          # active | expired | cancelled | failed_3ds
    is_default = Column(Boolean, default=True)

    created_at = Column(DateTime, default=datetime.datetime.utcnow)


# ═══════════════════════════════════════════════════════════════
# MODEL 18: SubscriptionPayment   (Aurora Onboarding Module / Phase 6b)
# ═══════════════════════════════════════════════════════════════
# One row per attempted charge. Idempotency key prevents double-charges
# even under retries. The invoice_id FK links to the auto-generated
# tax invoice (created via existing invoice_service.finalize_invoice
# only after status='succeeded').
#
# AUTO-INVOICING TRIGGER:
#   Per Aurora spec, "Upon activation, trigger the existing
#   invoice_service to issue a tax invoice for the subscription".
#   Implementation: activate() creates the trialing Subscription and
#   a SubscriptionPayment(status='pending') scheduled for trial_ends_at.
#   When the scheduled-charge worker (Sprint 5) lands the actual
#   payment, the SUCCESS handler calls invoice_service to mint the
#   tax invoice. ITA does not require zero-value trial invoices.
# ═══════════════════════════════════════════════════════════════
class SubscriptionPayment(Base):
    __tablename__ = "subscription_payments"

    id = Column(Integer, primary_key=True, index=True)
    subscription_id = Column(Integer, ForeignKey("subscriptions.id"), nullable=False, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)

    amount_minor_units = Column(Integer, nullable=False)
    currency = Column(String(3), default="ILS")
    status = Column(String(16), default="scheduled")
    # scheduled | pending | succeeded | failed | refunded | disputed

    provider_charge_id = Column(String, nullable=True)
    idempotency_key = Column(String(80), nullable=False, unique=True, index=True)

    period_start = Column(DateTime, nullable=False)
    period_end = Column(DateTime, nullable=False)

    attempted_at = Column(DateTime, default=datetime.datetime.utcnow)
    succeeded_at = Column(DateTime, nullable=True)
    failed_at = Column(DateTime, nullable=True)
    failure_code = Column(String(40), nullable=True)
    failure_message = Column(String, nullable=True)

    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=True)
    # FK to the existing Invoice generated post-charge by invoice_service.

    created_at = Column(DateTime, default=datetime.datetime.utcnow)


# ═══════════════════════════════════════════════════════════════
# MODEL 19: Receipt   (Sprint 2 — Document AI Receipt Pipeline)
# ═══════════════════════════════════════════════════════════════
# A Receipt is the RAW evidence — the photo / PDF a field worker sent
# us via WhatsApp (or eventually uploaded via the dashboard). It carries
# the bytes' identity (sha256, gcs_uri) and the OCR result (raw JSON +
# confidence). Every Receipt either becomes an Expense (the structured
# tax-deductible record) or gets quarantined.
#
# REAL-WORLD ANALOGY:
#   The shoebox of paper receipts under the desk. Each receipt is the
#   evidence; the bookkeeping ledger entry it produces is the Expense.
#   We keep both — the receipt for audit, the expense for accounting.
#
# WHY UUID PRIMARY KEY:
#   Receipt IDs surface in signed URLs and webhook payloads. UUIDs are
#   unguessable; sequential integer IDs would let an attacker enumerate
#   receipts across organizations.
#
# 7-YEAR RETENTION:
#   ITA requires source documents kept for 7 years. The retention is
#   enforced at the GCS bucket level (lifecycle policy); this row is
#   the metadata index into that bucket.
# ═══════════════════════════════════════════════════════════════
class Receipt(Base):
    __tablename__ = "receipts"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    # The User who uploaded — typically the org owner's WhatsApp identity,
    # or an employee, or eventually an accountant (if they upload on behalf).

    # ── Storage coordinates ──
    gcs_bucket = Column(String(64), default="asg-receipts-prod")
    gcs_object_key = Column(String, nullable=False)
    # Pattern: {organization_id}/{yyyy}/{mm}/{sha256}.{ext}
    # In stub mode, this is a path under /tmp/aurora/receipts/.

    sha256 = Column(String(64), nullable=False, index=True)
    # Dedup key: the same image uploaded twice produces ONE Receipt row.

    mime_type = Column(String(64), nullable=True)
    bytes_size = Column(Integer, nullable=True)

    # ── OCR outcome ──
    ocr_status = Column(String(20), default="pending", index=True)
    # pending          → just inserted; OCR not yet attempted
    # parsed           → Document AI returned with high confidence (auto-approved)
    # review_light     → mid confidence; user/accountant should sanity-check
    # review_heavy     → low confidence; ask user explicitly for missing fields
    # failed           → OCR call errored or returned no usable fields
    # dlp_quarantined  → Cloud DLP flagged PII (ID card / CC / passport)

    ocr_confidence_min = Column(Float, nullable=True)
    # Lowest per-field confidence across critical fields (supplier, total, date).
    # Drives the routing decision in services/receipts/confidence.py.

    ocr_raw_json = Column(String, nullable=True)
    # Full Document AI response, JSON-serialized. Audit evidence: when
    # an accountant questions a parse, we can replay the raw OCR.

    document_ai_job_id = Column(String, nullable=True)
    # Document AI returns a job/operation id; stored for trace correlation
    # with Cloud Logging.

    # ── DLP scan outcome (when DLP_BACKEND != stub) ──
    dlp_clean = Column(Boolean, default=True)
    dlp_findings_json = Column(String, nullable=True)
    # JSON list of [{infoType, likelihood, quote}] when DLP flags anything.

    # ── Source / provenance ──
    source = Column(String(20), default="whatsapp")
    # whatsapp | dashboard | accountant_portal | api

    source_message_id = Column(String, nullable=True)
    # E.g. the Meta wamid that delivered the image, or upload session id.

    # ── Timestamps ──
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    parsed_at = Column(DateTime, nullable=True)
    # When the OCR pipeline finished (pending → terminal status).

    # ── Sprint 4 Phase 17 — Vertex Gemini classification (Appendix L §4.4) ──
    # Populated by POST /api/v1/admin/exec/receipts/{id}/classify-with-gemini.
    # Stored as JSON text (matches Aurora convention; future structured-query
    # needs can JSONB-cast in SQL).
    # Shape:
    #   {category: str, confidence: float, vat_eligible: bool,
    #    rationale: str, model: str, run_id: int}
    gemini_classification_json = Column(String, nullable=True)
    gemini_classified_at = Column(DateTime, nullable=True)

    __table_args__ = (
        # Prevent the same image being uploaded twice within an org
        UniqueConstraint("organization_id", "sha256", name="uq_receipt_org_sha"),
    )


# ═══════════════════════════════════════════════════════════════
# MODEL 20: Expense   (Sprint 2 — Document AI Receipt Pipeline)
# ═══════════════════════════════════════════════════════════════
# The structured, accountant-friendly record that comes out of a
# Receipt. One Receipt → one Expense (when OCR succeeds + user confirms).
# Receipts in DLP-quarantine or that get rejected during review never
# produce an Expense.
#
# WHY SEPARATE FROM RECEIPT:
#   Receipts are immutable evidence. Expenses are working data —
#   categorisable, reclassifiable, exportable to Hashavshevet (Sprint 4).
#   Keeping them apart lets accountants edit categorization without
#   ever touching the OCR audit trail.
#
# MONEY IN MINOR UNITS (agorot for ILS):
#   Aurora's convention. Float-free arithmetic. Convert to display
#   only at the edges (PDF, dashboard, exports).
# ═══════════════════════════════════════════════════════════════
class Expense(Base):
    __tablename__ = "expenses"

    id = Column(Integer, primary_key=True, index=True)

    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    receipt_id = Column(String(36), ForeignKey("receipts.id"), nullable=True, index=True)
    # Nullable: an Expense can be created manually (typed) without a receipt.
    # When a Receipt is uploaded via OCR, this FK links them.

    # ── Parsed fields (from Document AI or manual entry) ──
    supplier_name = Column(String, nullable=True)
    supplier_tax_id = Column(String, nullable=True)
    # Israeli 9-digit ID; not validated server-side here (the supplier
    # may legitimately be foreign, or the OCR may misread). Validation
    # at Hashavshevet-export time.

    total_amount_minor_units = Column(Integer, nullable=True)
    vat_amount_minor_units = Column(Integer, nullable=True)
    currency = Column(String(3), default="ILS")

    expense_date = Column(Date, nullable=True)
    # The date PRINTED on the receipt — not when we received it.

    # ── Categorisation ──
    category = Column(String(40), nullable=True)
    # Sprint 2: NULL until admin/accountant assigns. Sprint 4 categorization
    # will use Vertex AI Gemini. Categories examples: "fuel", "office_supplies",
    # "subcontractor", "phone", "rent", "depreciation", etc.

    # ── Lifecycle ──
    status = Column(String(20), default="draft", index=True)
    # draft       → created from OCR or manual entry; not yet approved
    # confirmed   → reviewed and approved by user/accountant
    # rejected    → reviewed and rejected (kept for audit, excluded from totals)

    confirmed_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    confirmed_at = Column(DateTime, nullable=True)
    rejection_reason = Column(String, nullable=True)

    # ── Free-form notes (accountant audit comments) ──
    notes = Column(String, nullable=True)

    # ── Timestamps ──
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(
        DateTime,
        default=datetime.datetime.utcnow,
        onupdate=datetime.datetime.utcnow,
    )

    # ── Relationships ──
    receipt = relationship("Receipt", foreign_keys=[receipt_id])


# ═══════════════════════════════════════════════════════════════
# MODEL 21: ItaAuditLog   (Sprint 3 — Real ITA client)
# ═══════════════════════════════════════════════════════════════
# One row per ITA API call (allocation request, status check, retry).
# Mandatory evidence for the ITA Software-House binder: when an
# auditor asks "show me every call you made to the Tax Authority on
# behalf of taxpayer X", we have an immutable, sanitised record.
#
# IMMUTABILITY:
#   In production we add a Postgres trigger that rejects UPDATE/DELETE
#   on this table once a row is committed. SQLite has no triggers,
#   so the dev environment relies on application discipline.
#
# PII HYGIENE:
#   - seller_tax_id stored masked (first-3 + last-2 digits visible)
#   - buyer_tax_id  same
#   - private signing key NEVER appears here
#   - the raw response is sanitised before persistence (see ita.client)
# ═══════════════════════════════════════════════════════════════
class ItaAuditLog(Base):
    __tablename__ = "ita_audit_log"

    id = Column(Integer, primary_key=True, index=True)

    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True, index=True)

    # Idempotency / correlation
    request_id = Column(String, nullable=False, index=True)
    # X-Request-Id we sent to ITA — also stored on Invoice.ita_request_id

    operation = Column(String(32), default="allocation_request")
    # allocation_request | allocation_check | allocation_cancel

    # Sanitised request data
    seller_tax_id_masked = Column(String(20), nullable=True)
    buyer_tax_id_masked = Column(String(20), nullable=True)
    amount_minor_units = Column(Integer, nullable=True)
    currency = Column(String(3), default="ILS")

    # Outcome
    http_status = Column(Integer, nullable=True)
    latency_ms = Column(Integer, nullable=True)
    success = Column(Boolean, default=False)
    allocation_number = Column(String, nullable=True)
    error_code = Column(String(40), nullable=True)
    error_message = Column(String, nullable=True)

    # Backend that handled the call
    backend = Column(String(20), default="mock")
    # mock | production

    # Sanitised raw response (no PII, capped length)
    response_summary = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.datetime.utcnow)


# ═══════════════════════════════════════════════════════════════
# MODEL 22: Export   (Sprint 4 — Accountant Channel)
# ═══════════════════════════════════════════════════════════════
# One row per export request. The "format" field decides which writer
# in app/services/exports/ produces the bytes:
#   uniform_file   → ITA OpenFormat 1.31 zip (INI.TXT + BKMVDATA.TXT)
#   hashavshevet   → CSV in Rivhit's accepted import shape
#   simplitax      → (future)
#
# Rows are immutable once status='completed' — accountants reference
# the exact zip they downloaded for their tax filing.
# ═══════════════════════════════════════════════════════════════
class Export(Base):
    __tablename__ = "exports"

    id = Column(Integer, primary_key=True, index=True)

    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    requested_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    format = Column(String(20), nullable=False)
    # uniform_file | hashavshevet | simplitax

    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)

    status = Column(String(16), default="pending")
    # pending | running | completed | failed

    # Storage coordinates (GCS in production; local stub in dev)
    gcs_uri = Column(String, nullable=True)
    file_size_bytes = Column(Integer, nullable=True)
    record_count = Column(Integer, nullable=True)
    sha256 = Column(String(64), nullable=True)

    error_message = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)


# ═══════════════════════════════════════════════════════════════
# MODEL 23: AccountantCoaMapping   (Sprint 4)
# ═══════════════════════════════════════════════════════════════
# Per-accountant chart-of-accounts mapping. Aurora's internal expense
# categories ('fuel', 'tools', 'subcontractor', etc.) → the account
# code the accountant uses in their book of business. Each accountant
# can have a different mapping; the Hashavshevet/SimpLiTax exporters
# join on this table to translate.
#
# WHY PER-ACCOUNTANT:
#   The same expense category ('fuel') maps to "5510" in one firm's COA
#   and "5530" in another's. Aurora doesn't impose a chart; we adapt
#   to whatever each accounting firm already uses.
# ═══════════════════════════════════════════════════════════════
class AccountantCoaMapping(Base):
    __tablename__ = "accountant_coa_mappings"

    id = Column(Integer, primary_key=True, index=True)

    accountant_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    category = Column(String(40), nullable=False)
    # Aurora's internal category — must match one used on Expense.category

    account_code = Column(String(20), nullable=False)
    # The account code in the accountant's chart of accounts.

    account_name = Column(String, nullable=True)
    # Optional human-readable label, shown in the accountant portal.

    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(
        DateTime,
        default=datetime.datetime.utcnow,
        onupdate=datetime.datetime.utcnow,
    )

    __table_args__ = (
        UniqueConstraint("accountant_user_id", "category", name="uq_coa_acct_category"),
    )


# ═══════════════════════════════════════════════════════════════
# MODEL 24: RevenueShareLedger   (Sprint 5 — Revenue Engine)
# ═══════════════════════════════════════════════════════════════
# One row per (accountant × successful subscription charge). Tracks
# the 20% lifetime rev-share that accrues to the engaging accountant.
#
# LIFECYCLE:
#   accrued        → just inserted; accountant has earned but not yet
#                    eligible for payout (fraud rules pending)
#   payable        → passed fraud rules, ready to be paid out at month-end
#   held_for_review→ flagged by fraud rules; founder reviews manually
#   paid           → included in an AccountantPayout that was disbursed
#                    (immutable from this point forward)
#   rejected       → fraud review rejected (kept for audit, excluded)
#
# IMMUTABILITY:
#   Once status='paid', the row is logically frozen. Postgres trigger
#   in Sprint 6 enforces; SQLite relies on application discipline.
#
# REAL-WORLD ANALOGY:
#   Like the commission ledger at a real-estate agency. Each property
#   sale produces a commission row — "Agent X earned ₪Y on property Z".
#   At month-end the agency cuts a single check covering all confirmed
#   commissions for that agent. Disputed commissions stay parked until
#   resolved.
# ═══════════════════════════════════════════════════════════════
class RevenueShareLedger(Base):
    __tablename__ = "revenue_share_ledger"

    id = Column(Integer, primary_key=True, index=True)

    accountant_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    subscription_payment_id = Column(
        Integer, ForeignKey("subscription_payments.id"), nullable=False, index=True
    )
    engagement_id = Column(
        Integer, ForeignKey("accountant_engagements.id"), nullable=True
    )

    # Money
    gross_amount_minor_units = Column(Integer, nullable=False)  # the payment ASG collected
    share_pct = Column(Float, nullable=False)                   # 20.0 by default
    share_amount_minor_units = Column(Integer, nullable=False)  # gross × share_pct (rounded)
    currency = Column(String(3), default="ILS")

    # Lifecycle
    status = Column(String(20), default="accrued", index=True)
    # accrued | payable | held_for_review | paid | rejected

    held_reason = Column(String, nullable=True)
    rejected_reason = Column(String, nullable=True)
    review_notes = Column(String, nullable=True)

    # Linked payout once it lands
    payout_id = Column(Integer, ForeignKey("accountant_payouts.id"), nullable=True, index=True)

    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    paid_at = Column(DateTime, nullable=True)


# ═══════════════════════════════════════════════════════════════
# MODEL 25: AccountantPayout   (Sprint 5)
# ═══════════════════════════════════════════════════════════════
# Monthly rollup. close_month() collects every payable row for an
# accountant in a given period and creates ONE AccountantPayout row
# whose total = sum of share_amount_minor_units.
#
# Founder approves → CSV export to bank → mark paid.
# Future: integrate Tranzila/PayPlus payout API.
# ═══════════════════════════════════════════════════════════════
class AccountantPayout(Base):
    __tablename__ = "accountant_payouts"

    id = Column(Integer, primary_key=True, index=True)

    accountant_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    period = Column(String(7), nullable=False, index=True)  # "YYYY-MM"
    total_amount_minor_units = Column(Integer, nullable=False, default=0)
    currency = Column(String(3), default="ILS")
    ledger_row_count = Column(Integer, default=0)

    status = Column(String(16), default="pending", index=True)
    # pending | approved | paid | failed | cancelled

    provider_ref = Column(String, nullable=True)
    # When we wire payout providers (Tranzila/PayPlus) this is their charge id

    approved_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    paid_at = Column(DateTime, nullable=True)
    failed_at = Column(DateTime, nullable=True)
    failure_message = Column(String, nullable=True)

    notes = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("accountant_user_id", "period", name="uq_payout_acct_period"),
    )


# ═══════════════════════════════════════════════════════════════
# MODEL 26: AccountantReferral   (Sprint 5)
# ═══════════════════════════════════════════════════════════════
# Audit/marketing record: which accountant brought which Org onto
# Aurora, and through what channel. Drives the leaderboard and the
# referral analytics in the founder's view.
# ═══════════════════════════════════════════════════════════════
class AccountantReferral(Base):
    __tablename__ = "accountant_referrals"

    id = Column(Integer, primary_key=True, index=True)

    accountant_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)

    source = Column(String(20), default="portal")
    # portal | csv_bulk | wa_invite | email_invite | api

    activated_at = Column(DateTime, nullable=True)
    # When the referred Org's Subscription went status='active' (first paying month).

    notes = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("accountant_user_id", "organization_id", name="uq_referral_acct_org"),
    )


# ═══════════════════════════════════════════════════════════════
# MODEL 27: AuditExportCursor   (Sprint 6 — Hardening)
# ═══════════════════════════════════════════════════════════════
# Tracks the last-exported row id per source table for the daily
# BigQuery audit pipeline. Ensures we never gap-skip a row OR
# double-export one.
#
# RECORDED PER: source_table (action_logs, ita_audit_log, etc.)
# ═══════════════════════════════════════════════════════════════
class AuditExportCursor(Base):
    __tablename__ = "audit_export_cursor"

    id = Column(Integer, primary_key=True, index=True)
    source_table = Column(String(64), nullable=False, unique=True, index=True)
    last_exported_id = Column(Integer, default=0)
    last_exported_at = Column(DateTime, nullable=True)
    rows_in_last_batch = Column(Integer, default=0)
    last_batch_hash = Column(String(64), nullable=True)
    # Hash chain for tamper-evidence: each batch hash includes the previous
    # batch's hash so any tampering with old rows breaks the chain.

    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(
        DateTime, default=datetime.datetime.utcnow,
        onupdate=datetime.datetime.utcnow,
    )


# ═══════════════════════════════════════════════════════════════
# MODEL 28: MarketingLead   (Sprint 7 — Marketing capture)
# ═══════════════════════════════════════════════════════════════
# Inbound waitlist / "founding member" signups from the marketing
# site (aurora-ltd.co.il). Org-less by design — these are people
# who have NOT yet onboarded. When they convert, a real User +
# Organization is created and we backreference here.
# ═══════════════════════════════════════════════════════════════
class MarketingLead(Base):
    __tablename__ = "marketing_leads"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), nullable=False, index=True)
    full_name = Column(String(200), nullable=True)
    phone_e164 = Column(String(32), nullable=True)

    tier_interest = Column(String(16), nullable=True)
    # courier | digital | premium | unsure

    source = Column(String(64), nullable=True)
    # marketing-home | tiers-courier | pricing | referral:<code> | ...

    locale = Column(String(8), nullable=True)
    # "he" | "en" | "ar"

    note = Column(String, nullable=True)

    consent_terms = Column(Boolean, default=False)
    consent_privacy = Column(Boolean, default=False)

    # Anti-abuse + telemetry — IP is hashed (SHA-256), never stored raw.
    ip_hash = Column(String(64), nullable=True)
    user_agent = Column(String, nullable=True)
    referer = Column(String, nullable=True)

    status = Column(String(16), default="new", index=True)
    # new | contacted | converted | discarded | unsubscribed

    converted_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    converted_org_id = Column(Integer, ForeignKey("organizations.id"), nullable=True)

    created_at = Column(DateTime, default=datetime.datetime.utcnow, index=True)
    updated_at = Column(
        DateTime, default=datetime.datetime.utcnow,
        onupdate=datetime.datetime.utcnow,
    )

    # P2-24 — Email nurture sequence tracking
    nurture_enrolled_at = Column(DateTime, nullable=True)    # when lead entered sequence
    nurture_last_step = Column(Integer, nullable=True)        # last step sent (1-5)
    nurture_unsubscribed_at = Column(DateTime, nullable=True) # unsubscribe timestamp


# ═══════════════════════════════════════════════════════════════
# MODEL 29: TaxObligation   (v2.0 — Virtual Tax Shield)
# ═══════════════════════════════════════════════════════════════
# What the user is projected to owe to a tax authority for a given
# period. Created/updated by the Tax Engine on every income/expense
# event. The Remittance Assistant generates payment links against
# these rows; payment_confirmations close them out.
# ═══════════════════════════════════════════════════════════════
class TaxObligation(Base):
    __tablename__ = "tax_obligations"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)

    kind = Column(String(32), nullable=False, index=True)
    # income_tax_advance | income_tax_annual | national_insurance_monthly |
    # vat_bimonthly | pension_monthly

    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    due_date = Column(Date, nullable=False, index=True)

    projected_amount = Column(Float, nullable=False, default=0.0)
    confirmed_paid_amount = Column(Float, nullable=False, default=0.0)

    status = Column(String(24), default="projected", index=True)
    # projected | overdue | user_confirmed_paid | reconciled | waived

    ita_form_code = Column(String(16), nullable=True)
    # e.g., "542" (מקדמה), "1301" (שנתי), "102" (ביטוח לאומי)

    notes = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(
        DateTime, default=datetime.datetime.utcnow,
        onupdate=datetime.datetime.utcnow,
    )

    __table_args__ = (
        UniqueConstraint(
            "organization_id", "kind", "period_start", "period_end",
            name="uq_obligation_org_kind_period",
        ),
        Index("ix_obligation_org_status", "organization_id", "status"),
        Index("ix_obligation_org_due", "organization_id", "due_date"),
    )


# ═══════════════════════════════════════════════════════════════
# MODEL 30: VirtualLedger   (v2.0 — Virtual Tax Shield)
# ═══════════════════════════════════════════════════════════════
# Append-only ledger of every change to a user's projected tax
# position. Replaces v1.0's "escrow_ledger" (which tracked real
# money held). Virtual ledger tracks LIABILITY accruals +
# remittance-link generation events + user-confirmation events.
# Aurora never moves money.
#
# IMMUTABILITY:
#   Phase 3 of the security roadmap installs a Postgres BEFORE
#   UPDATE / BEFORE DELETE trigger that raises on every row.
#   Until then, SQLAlchemy event-listener guards in
#   app/services/compliance/immutability.py provide the same
#   protection.
# ═══════════════════════════════════════════════════════════════
class VirtualLedger(Base):
    __tablename__ = "virtual_ledger"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)

    txn_type = Column(String(32), nullable=False)
    # liability_accrued        — Tax Engine added a projected liability
    # liability_adjusted       — expense added; liability reduced
    # remittance_link_generated — Aurora generated a payment link
    # remittance_user_confirmed — user said "I paid"
    # remittance_reconciled    — confirmed via ITA API (future)
    # period_closed            — end-of-quarter or year accounting close

    amount = Column(Float, nullable=False, default=0.0)
    running_balance = Column(Float, nullable=False, default=0.0)

    obligation_id = Column(Integer, ForeignKey("tax_obligations.id"), nullable=True, index=True)

    # Polymorphic source pointer — id of the income event / expense /
    # remittance_link / payment_confirmation that caused this row.
    source_kind = Column(String(40), nullable=True)
    source_id = Column(Integer, nullable=True)

    narrative = Column(String, nullable=True)

    created_by = Column(String(24), default="system_auto")
    # system_auto | user_confirmation | admin_override

    created_at = Column(DateTime, default=datetime.datetime.utcnow, index=True)


# ═══════════════════════════════════════════════════════════════
# MODEL 31: VirtualBalance   (v2.0 — Virtual Tax Shield)
# ═══════════════════════════════════════════════════════════════
# Denormalised per-org snapshot of the projected liability and YTD
# remittances. Faster than scanning virtual_ledger for the
# dashboard / WhatsApp daily card.
# ═══════════════════════════════════════════════════════════════
class VirtualBalance(Base):
    __tablename__ = "virtual_balance"

    organization_id = Column(Integer, ForeignKey("organizations.id"), primary_key=True)

    projected_tax_liability = Column(Float, nullable=False, default=0.0)
    ytd_remittances_confirmed = Column(Float, nullable=False, default=0.0)
    current_quarter_owed = Column(Float, nullable=False, default=0.0)
    next_due_date = Column(Date, nullable=True)

    as_of = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(
        DateTime, default=datetime.datetime.utcnow,
        onupdate=datetime.datetime.utcnow,
    )


# ═══════════════════════════════════════════════════════════════
# MODEL 32: RemittanceLink   (v2.0 — Remittance Assistant)
# ═══════════════════════════════════════════════════════════════
# A pre-filled link to the official gov.il payment portal,
# generated by the Remittance Assistant on a 7-day-before /
# 1-day-before / on-due-date schedule. Aurora never collects
# money; the user clicks through to gov.il and pays the ITA
# directly.
# ═══════════════════════════════════════════════════════════════
class RemittanceLink(Base):
    __tablename__ = "remittance_links"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    obligation_id = Column(Integer, ForeignKey("tax_obligations.id"), nullable=False, index=True)

    channel = Column(String(16), default="whatsapp")
    # whatsapp | web_push | email | sms | dashboard

    target_url = Column(String, nullable=False)
    # The full https://misim.gov.il/... URL with prefill params.

    short_code = Column(String(12), unique=True, nullable=False, index=True)
    # Used for r.aurora-ltd.co.il/<short_code> short-link service.

    delivered_at = Column(DateTime, nullable=True)
    opened_at = Column(DateTime, nullable=True)
    clicked_at = Column(DateTime, nullable=True)

    user_confirmed_payment_at = Column(DateTime, nullable=True)
    confirmed_amount = Column(Float, nullable=True)

    reconciled_at = Column(DateTime, nullable=True)
    # Phase X: when ITA exposes a payment-confirmation API,
    # this flips automatically.

    created_at = Column(DateTime, default=datetime.datetime.utcnow, index=True)


# ═══════════════════════════════════════════════════════════════
# MODEL 33: PaymentConfirmation   (v2.0 — Remittance Assistant)
# ═══════════════════════════════════════════════════════════════
# The user-side acknowledgement that they paid (via the link, or
# manually). Append-only audit of "user said paid" events.
# Critical for both UX (mark obligation as paid) and audit
# (binder evidence).
# ═══════════════════════════════════════════════════════════════
class PaymentConfirmation(Base):
    __tablename__ = "payment_confirmations"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    remittance_link_id = Column(Integer, ForeignKey("remittance_links.id"), nullable=True, index=True)
    obligation_id = Column(Integer, ForeignKey("tax_obligations.id"), nullable=False, index=True)

    confirmed_via = Column(String(24), nullable=False)
    # whatsapp_button | wa_text_payment | dashboard_button | admin_manual

    stated_amount = Column(Float, nullable=False, default=0.0)

    # Optional proof — receipt image / PDF user uploaded.
    # Plain Integer FK to avoid a circular import with Receipt.
    proof_doc_id = Column(Integer, nullable=True)

    user_agent = Column(String, nullable=True)
    ip_hash = Column(String(64), nullable=True)

    confirmed_at = Column(DateTime, default=datetime.datetime.utcnow, index=True)


# ═══════════════════════════════════════════════════════════════
# MODEL 34: BreakGlassToken   (Track 3 — Tier-1.5 emergency JWT)
# ═══════════════════════════════════════════════════════════════
# Long-lived JWT for emergency admin access when IAP / Workspace /
# OAuth client is down. Stored in 1Password + sealed-envelope paper
# backup. Every use is CRITICAL-audited via ActionLog.
#
# SECURITY MODEL:
#   - JWT is signed with normal JWT_SECRET (HS256), distinguished
#     from regular tokens ONLY by the custom claim
#     `is_emergency_break_glass=true`.
#   - The token's `jti` is registered in this table at issue time.
#     require_admin() bypasses IAP enforcement only if jti is
#     present in this table AND not revoked AND not expired.
#   - Revocation is a one-way flip; `revoked_at` once set cannot be
#     unset (defense via Postgres trigger added in Phase 6).
#
# THREAT MODEL COVERED:
#   - Workspace outage (founder can't sign in to Google)
#   - IAP / OAuth client misconfig (recent Error 11 incident)
#   - GCP zone failure that downs IAP-related infra
#
# THREAT MODEL NOT COVERED:
#   - Postgres failure (this token still requires DB lookup)
#   - JWT_SECRET rotation (invalidates this token too — but that's
#     a planned-rotation event we'd coordinate around)
# ═══════════════════════════════════════════════════════════════
class BreakGlassToken(Base):
    __tablename__ = "break_glass_tokens"

    id = Column(Integer, primary_key=True, index=True)
    jti = Column(String(64), nullable=False, unique=True, index=True)
    # UUID4 (or any unique string) that the JWT carries in its `jti` claim.
    # Database lookup gate: only registered jtis are accepted.

    issued_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=False, index=True)

    revoked_at = Column(DateTime, nullable=True)
    revoked_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    revoke_reason = Column(String, nullable=True)

    last_used_at = Column(DateTime, nullable=True)
    last_used_ip_hash = Column(String(64), nullable=True)
    use_count = Column(Integer, default=0, nullable=False)

    issued_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    # Who pressed the button (the admin running the CLI).
    issued_for_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    # The user this token impersonates (the `sub` claim).

    notes = Column(String, nullable=True)
    # Free-text reminder. e.g., "initial issue 2026-05-14".

    created_at = Column(DateTime, default=datetime.datetime.utcnow)


# ═══════════════════════════════════════════════════════════════
# MODEL: VerticalTemplate   (Track Appendix H — Tier 1 CEO Dashboard)
# ═══════════════════════════════════════════════════════════════
# A reusable "playbook" for a specific business vertical. Each
# template bundles:
#   • a WhatsApp opening flow (welcome + menu structure)
#   • an invoice preset (default line items, VAT defaults)
#   • receipt categorization rules (regex/keyword → category)
#   • a VAT advisory note shown in the CEO dashboard
#
# Read-only in Tier 1. CRUD via the CEO Dashboard arrives with
# Tier 2 (native SwiftUI) and Tier 1.5 (template CRUD UI).
#
# WHY:
#   Every new SMB the platform onboards belongs to ONE vertical
#   (restaurant, garage, retail, contractor, services, …). Each
#   vertical has known patterns — VAT exposure, expense categories,
#   day-to-day conversational triggers. Storing them as DB rows
#   instead of hard-coding lets the CEO iterate without redeploys.
# ═══════════════════════════════════════════════════════════════
class VerticalTemplate(Base):
    __tablename__ = "vertical_templates"

    id = Column(Integer, primary_key=True, index=True)

    name = Column(String(120), nullable=False)
    business_type = Column(String(40), nullable=False, index=True)
    # e.g., "restaurant", "garage", "retail", "contractor", "services"

    locale = Column(String(8), nullable=False, default="he")
    # "he" | "ar" | "en"

    # JSON-encoded blobs stored as text (matches the Aurora convention
    # used by WhatsAppSession.draft_payload_json + WhatsAppOutboundLog.payload_json).
    whatsapp_opening_flow_json = Column(String, nullable=False, default="{}")
    invoice_preset_json = Column(String, nullable=False, default="{}")
    receipt_categorization_rules_json = Column(String, nullable=False, default="{}")

    vat_advisory_text = Column(String, nullable=True)

    is_active = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("idx_vertical_templates_type_locale", "business_type", "locale"),
    )


# ═══════════════════════════════════════════════════════════════
# MODEL: ExecEvent   (Track Appendix H — Tier 1 CEO Dashboard)
# ═══════════════════════════════════════════════════════════════
# Append-only stream of CEO-visible events. The Alert Stream
# (right rail of the executive dashboard) renders these in
# reverse chronological order.
#
# Distinct from ActionLog (which is durable, hash-chained, and
# ITA-audit-bound) — ExecEvent is operator-UX data, optimized for
# "what happened in the last hour that I should glance at."
#
# A Cloud Scheduler cron prunes rows older than 30 days; the
# durable record lives in ActionLog / ItaAuditLog.
#
# Published by invoice_service.py, whatsapp_engine.py,
# accountant payouts, break-glass middleware, etc.
# Consumed by /api/v1/admin/exec/events and the SSE stream.
# ═══════════════════════════════════════════════════════════════
class ExecEvent(Base):
    __tablename__ = "exec_events"

    id = Column(Integer, primary_key=True, index=True)

    kind = Column(String(50), nullable=False, index=True)
    # invoice_finalized | wa_send_failed | payout_marked_paid |
    # break_glass_used | allocation_succeeded | kyc_pending_review | …

    severity = Column(String(20), nullable=False, default="info")
    # "info" | "warning" | "critical"

    title = Column(String(200), nullable=False)
    # Short headline shown in the feed. e.g. "Invoice INV-00042 finalized (₪5,200)"

    detail = Column(String, nullable=True)
    # Optional longer body. Free text.

    related_entity_type = Column(String(40), nullable=True)
    # "invoice" | "receipt" | "organization" | "user" | "payout" | …

    related_entity_id = Column(Integer, nullable=True)

    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False, index=True)


# ═══════════════════════════════════════════════════════════════
# MODEL: BusinessCategory   (Appendix I Sprint 2 — Custom Option B)
# ═══════════════════════════════════════════════════════════════
# Self-referencing two-level taxonomy for grouping registered
# businesses by sector + profession. Replaces the static
# `vertical_templates` table from Sprint 1 with a CEO-managed
# dynamic hierarchy.
#
# Level 1 (sector / branch):
#   • parent_id IS NULL
#   • Examples: "Construction" / "ענף הבנייה", "Food & Beverage"
#
# Level 2 (profession / sub-category):
#   • parent_id points at an L1 row
#   • Examples: "Electricity" / "חשמל", "Plumbing" / "אינסטלציה"
#
# Level 3 (business mapping):
#   • organizations.category_id FK to this table
#   • One category per org for Tier 1 (extend to M:N later if needed)
#
# CHECK constraints enforce that L1 has no parent and L2 has a parent —
# the DB itself refuses to store a malformed tree.
# ═══════════════════════════════════════════════════════════════
class BusinessCategory(Base):
    __tablename__ = "business_categories"

    id = Column(Integer, primary_key=True, index=True)

    parent_id = Column(
        Integer,
        ForeignKey("business_categories.id", ondelete="RESTRICT"),
        nullable=True,
    )

    name = Column(String(120), nullable=False)         # canonical (Latin) name
    name_he = Column(String(120), nullable=True)       # Hebrew label
    name_ar = Column(String(120), nullable=True)       # Arabic label

    slug = Column(String(140), nullable=False, unique=True, index=True)
    # e.g., "construction" (L1), "construction/electricity" (L2)
    # Built deterministically: parent.slug + "/" + slugify(name)

    level = Column(Integer, nullable=False, default=1)
    # 1 = sector (branch), 2 = profession (sub-category)

    description = Column(String, nullable=True)
    icon_emoji = Column(String(8), nullable=True)  # 🔨 ⚡ 🚰 etc.
    sort_order = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)

    # Self-referencing relationship for tree navigation
    parent = relationship("BusinessCategory", remote_side=[id], backref="children")


# ═══════════════════════════════════════════════════════════════
# MODEL: CeoSessionSnapshot   (Appendix I Sprint 2 — Option E)
# ═══════════════════════════════════════════════════════════════
# Persisted snapshot of the Mission Control KPIs at the end of each
# CEO session. Used by /admin/exec/dashboard-summary to compute a
# "what changed since you last looked" diff on the next visit.
#
# Snapshot is stored as a JSON-encoded text blob for forward-compat
# with new KPI fields without ALTER TABLE thrash.
#
# Pruned to last 30 snapshots per user_id by a Cloud Scheduler cron
# (TBD — wired in Sprint 2 alongside prune-exec-events).
# ═══════════════════════════════════════════════════════════════
class CeoSessionSnapshot(Base):
    __tablename__ = "ceo_session_snapshots"

    id = Column(Integer, primary_key=True, index=True)

    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    snapshot_json = Column(String, nullable=False)
    # JSON-encoded dict of dashboard-summary KPIs at this moment

    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False, index=True)


# ═══════════════════════════════════════════════════════════════
# MODEL: WebauthnCredential   (Appendix I Sprint 2 — Option H)
# ═══════════════════════════════════════════════════════════════
# One row per registered passkey (Touch ID / Face ID / hardware key).
# Used to gate sensitive admin actions (DSAR-erase, payout approve,
# break-glass revoke) behind a biometric step-up.
#
# Multi-credential support: a single user can register multiple
# devices (e.g., one Mac + one iPad). Each credential has its own
# `sign_count` that the server tracks to detect clones.
#
# Sensitive fields stored as base64url-encoded strings (the WebAuthn
# spec's standard wire format) — no binary BLOBs needed.
# ═══════════════════════════════════════════════════════════════
class WebauthnCredential(Base):
    __tablename__ = "webauthn_credentials"

    id = Column(Integer, primary_key=True, index=True)

    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    credential_id = Column(String(512), nullable=False, unique=True, index=True)
    # base64url credential ID returned during registration

    public_key = Column(String, nullable=False)
    # COSE-encoded public key (base64)

    sign_count = Column(Integer, nullable=False, default=0)
    # Monotonic counter from the authenticator — server rejects
    # assertions where sign_count < stored value (clone detection).

    device_label = Column(String(120), nullable=True)
    # Human-friendly: "MacBook (Touch ID)", "iPhone (Face ID)", "YubiKey"

    aaguid = Column(String(64), nullable=True)
    # Authenticator model identifier (for fleet visibility)

    transports = Column(String(120), nullable=True)
    # JSON-encoded list of allowed transports: ["internal","hybrid","usb",...]

    last_used_at = Column(DateTime, nullable=True)
    last_used_ip_hash = Column(String(64), nullable=True)

    revoked_at = Column(DateTime, nullable=True)
    revoke_reason = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)


# ═══════════════════════════════════════════════════════════════
# MODEL: CopilotConversation   (Appendix J Sprint 3 — Headline K)
# ═══════════════════════════════════════════════════════════════
# One row per chat thread between the CEO and the Aurora AI Copilot.
# The Copilot is provisioning-focused — it proposes structured
# blueprints (via Claude tool_use), and the CEO approves + executes
# them through `/api/v1/admin/exec/copilot/approve`.
#
# Title is auto-generated from the first user message (truncated to
# 120 chars) so the conversation list is browsable without opening.
# ═══════════════════════════════════════════════════════════════
class CopilotConversation(Base):
    __tablename__ = "copilot_conversations"

    id = Column(Integer, primary_key=True, index=True)

    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    title = Column(String(200), nullable=True)
    # Auto-generated from the first user message; null until first send

    status = Column(String(20), nullable=False, default="active")
    # 'active' | 'archived' | 'errored'

    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)


# ═══════════════════════════════════════════════════════════════
# MODEL: CopilotMessage   (Appendix J Sprint 3)
# ═══════════════════════════════════════════════════════════════
# Full transcript of a Copilot conversation. Each row is one
# Anthropic-style message (user / assistant / tool_result / system).
#
# `content_json` stores Anthropic's content-blocks array verbatim:
#   • user      → [{"type": "text", "text": "..."}]
#   • assistant → [{"type": "text", "text": "..."},
#                  {"type": "tool_use", "id": "...", "name": "...",
#                   "input": {...}}]
#   • tool_result → [{"type": "tool_result", "tool_use_id": "...",
#                     "content": "..."}]
#
# Tokens are tracked per row so we can compute per-conversation cost
# without a separate aggregation step.
#
# DB-persisted (founder pivot 2026-05-20): full history survives
# page refreshes and feeds the audit narrative for the binder.
# ═══════════════════════════════════════════════════════════════
class CopilotMessage(Base):
    __tablename__ = "copilot_messages"

    id = Column(Integer, primary_key=True, index=True)

    conversation_id = Column(
        Integer,
        ForeignKey("copilot_conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    role = Column(String(20), nullable=False)
    # 'user' | 'assistant' | 'tool_result' | 'system'

    content_json = Column(String, nullable=False)
    # JSON-encoded array of Anthropic content blocks (stored as text for
    # forward-compat with new block kinds; matches the Aurora convention
    # used elsewhere for JSON payloads).

    tokens_input = Column(Integer, nullable=True)
    tokens_output = Column(Integer, nullable=True)
    tokens_cache_creation = Column(Integer, nullable=True)
    tokens_cache_read = Column(Integer, nullable=True)

    model = Column(String(60), nullable=True)
    stop_reason = Column(String(40), nullable=True)
    # 'end_turn' | 'tool_use' | 'max_tokens' | 'stop_sequence' | 'error'

    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)


# ═══════════════════════════════════════════════════════════════
# MODEL: CopilotProvisioningRun   (Appendix J Sprint 3)
# ═══════════════════════════════════════════════════════════════
# Records what blueprints actually executed against the DB after the
# CEO clicked Approve & Build.
#
# Each row links back to:
#   • the conversation it came from
#   • the specific Anthropic tool_use_id that proposed it
#   • the WebAuthn credential that approved it (step-up audit)
#   • the User who pressed approve (always the founder for v1)
#
# `outcome_json` captures per-item success/failure so partial
# provisioning runs (sector created but L2 failed) are forensically
# reconstructable.
# ═══════════════════════════════════════════════════════════════
class CopilotProvisioningRun(Base):
    __tablename__ = "copilot_provisioning_runs"

    id = Column(Integer, primary_key=True, index=True)

    conversation_id = Column(
        Integer,
        ForeignKey("copilot_conversations.id"),
        nullable=False,
        index=True,
    )

    tool_use_id = Column(String(120), nullable=False, index=True)
    # Anthropic's tool_use block id (e.g., "toolu_01ABC...")

    tool_name = Column(String(60), nullable=False)
    # e.g., "propose_provisioning_blueprint", "update_category", ...

    input_json = Column(String, nullable=False)
    # JSON-encoded args Claude proposed (verbatim from tool_use.input)

    outcome_json = Column(String, nullable=True)
    # JSON-encoded result: created entity IDs, per-item success/failure

    step_up_credential_id = Column(
        Integer,
        ForeignKey("webauthn_credentials.id"),
        nullable=True,
    )
    # Which passkey approved this. NULL only when
    # AURORA_EXEC_REQUIRE_STEP_UP=0 (escape hatch).

    executed_by_user_id = Column(
        Integer,
        ForeignKey("users.id"),
        nullable=False,
    )

    executed_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False, index=True)

    status = Column(String(20), nullable=False, default="pending")
    # 'pending' | 'success' | 'partial' | 'failed'


# ═══════════════════════════════════════════════════════════════
# MODEL: ClaudeApiUsage   (Appendix J Sprint 3)
# ═══════════════════════════════════════════════════════════════
# Token usage tracking for cost guardrails. Every Anthropic API
# call writes one row with the token counts from the response.
#
# Aggregation queries power:
#   • Per-user rate limiting (30 chat turns/hr default)
#   • Daily budget cap with audit (default ~$5/day)
#   • Monthly cost report for the binder
#
# This is INDEPENDENT of CopilotMessage.tokens_* fields — those
# are per-message, this is per-API-call (a single Claude call can
# fire multiple times in one turn if there's tool_use back-and-forth).
# ═══════════════════════════════════════════════════════════════
class ClaudeApiUsage(Base):
    # Phase 17 (Sprint 4 — Appendix L): table renamed claude_api_usage →
    # llm_api_usage. The class name is kept as ClaudeApiUsage for backward
    # compatibility with all existing imports; a future cleanup pass can
    # rename it to LlmApiUsage once we're confident no service references
    # the old name. The `provider` column was added in-place.
    __tablename__ = "llm_api_usage"

    id = Column(Integer, primary_key=True, index=True)

    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    conversation_id = Column(
        Integer,
        ForeignKey("copilot_conversations.id"),
        nullable=True,
    )

    model = Column(String(60), nullable=False)

    tokens_input = Column(Integer, nullable=False, default=0)
    tokens_output = Column(Integer, nullable=False, default=0)
    tokens_cache_creation = Column(Integer, nullable=False, default=0)
    tokens_cache_read = Column(Integer, nullable=False, default=0)

    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False, index=True)

    # Sprint 4 Phase 17 — provider column added in-place (idempotent ALTER).
    # Default 'anthropic' for historical rows. New Gemini rows set 'vertex_gemini'.
    # Column is added via migrate_phase17.py ALTER TABLE; the model field stays
    # NULL-safe so SQLAlchemy reads back default-populated rows correctly.
    provider = Column(String(32), nullable=False, default="anthropic", index=True)


# ═══════════════════════════════════════════════════════════════
# MODEL: GeminiRun   (Sprint 4 — Phase 17, Appendix L §4.4)
# ═══════════════════════════════════════════════════════════════
# One row per Vertex AI Gemini one-shot call (receipts classify,
# WhatsApp template draft, DSAR summarize, daily insights brief).
#
# Distinct from llm_api_usage which is a thin token-accounting log:
# GeminiRun stores the FULL input + output text so we can audit what
# the model produced and re-execute / re-analyze later.
#
# Purpose enum:
#   receipt_classify        → Vertex Flash, OCR text → expense category
#   whatsapp_template_draft → Vertex Flash, use_case → Meta-compliant body
#   dsar_summarize          → Vertex Pro, DSAR bundle → narrative summary
#   daily_insights_brief    → Vertex Pro, 24h activity → 3 observations
# ═══════════════════════════════════════════════════════════════
class GeminiRun(Base):
    __tablename__ = "gemini_runs"

    id = Column(Integer, primary_key=True, index=True)

    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)

    purpose = Column(String(50), nullable=False, index=True)
    # See enum in docstring above

    related_entity_type = Column(String(40), nullable=True)
    related_entity_id = Column(Integer, nullable=True)
    # e.g., type='receipt' id=<Receipt.id>

    model = Column(String(60), nullable=False)
    # e.g., "gemini-1.5-flash-002"

    input_text = Column(String, nullable=True)
    # Input prompt / context

    output_text = Column(String, nullable=True)
    # Generated text response (for text-mode tasks)

    output_json = Column(String, nullable=True)
    # Structured JSON response when response_mime_type=application/json
    # was used (stored as text for forward-compat with new field shapes)

    tokens_input = Column(Integer, nullable=False, default=0)
    tokens_output = Column(Integer, nullable=False, default=0)
    cost_usd = Column(Float, nullable=False, default=0.0)
    # Computed via pricing.py at call time. Note: against Google for
    # Startups credits this is a *projected* charge — Cloud Billing
    # is the actual source of truth (reconciled by Sprint 5 cron).

    status = Column(String(20), nullable=False, default="success")
    # 'success' | 'failed' | 'partial'
    error = Column(String, nullable=True)
    duration_ms = Column(Integer, nullable=True)

    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False, index=True)


# ═══════════════════════════════════════════════════════════════
# MODEL: DailyBriefCard   (Sprint 4 — Phase 17, Appendix L §4.4)
# ═══════════════════════════════════════════════════════════════
# Claude-narrated (no — Gemini-narrated) daily operations brief.
# Distinct from the WhatsApp EOD brief in /internal/eod-brief which
# is template-based and pushed to the CEO's phone.
#
# A Cloud Scheduler cron at 07:00 IL runs:
#   POST /api/v1/internal/daily-insights-generate
# which:
#   1. Aggregates last 24h of ExecEvent + invoice / WA / payout deltas
#   2. Feeds the rollup to Gemini 1.5 Pro with a "surface 3 operator-
#      actionable observations" prompt
#   3. Persists the response here
#
# Mission Control renders the latest non-dismissed row at the top of
# the KPI grid. Founder can dismiss; next morning's cron creates a
# fresh row.
# ═══════════════════════════════════════════════════════════════
class DailyBriefCard(Base):
    __tablename__ = "daily_brief_cards"

    id = Column(Integer, primary_key=True, index=True)

    generated_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False, index=True)
    period_start = Column(DateTime, nullable=False)
    period_end = Column(DateTime, nullable=False)

    content_json = Column(String, nullable=False)
    # JSON-encoded:
    #   {observations: [{title, severity: 'info'|'warning'|'critical',
    #                    detail, related_entity_type?, related_entity_id?}],
    #    cost_usd: <float>, tokens_input: <int>, tokens_output: <int>,
    #    model: <str>}

    dismissed_at = Column(DateTime, nullable=True)

    gemini_run_id = Column(
        Integer,
        ForeignKey("gemini_runs.id"),
        nullable=True,
    )
    # Pointer to the GeminiRun that produced this card; lets us
    # forensically audit the prompt + raw output that drove the brief.


# ═══════════════════════════════════════════════════════════════
# Sprint 5 / Appendix M — Pre-Armed Autonomous Architecture
# ═══════════════════════════════════════════════════════════════
# Five new tables for the H-CARL Ecosystem Orchestrator + PredictiveSite
# + Causal Insights Graph + Federated Learning Sync, plus the
# CEO-facing growth_milestones table.
#
# Design principle: all rows are append-mostly, FAIL-CLOSED. A row that
# can't be written is logged + ignored — never propagates back to the
# user-facing API path. Autonomous services that fail to record their
# state become invisible to operators (logged as warnings); they NEVER
# cause user-facing failures.
# ═══════════════════════════════════════════════════════════════


# ─────────────────────────────────────────────────────────────
# MODEL: ProjectConstraint   (H-CARL hard-constraint layer)
# ─────────────────────────────────────────────────────────────
# Hard constraints on a project that H-CARL is REQUIRED to respect
# during reward-function evaluation. These are the non-negotiable
# rules: safety regs, building codes, union work hours, critical-path
# deadlines, environmental compliance, etc.
#
# Fail-closed: if the H-CARL agent cannot evaluate a constraint (e.g.,
# missing reference data), it MUST treat the proposed action as
# violating the constraint. Never silently skip.
#
# constraint_kind:
#   safety        — non-negotiable safety regulations
#   building_code — local/national building code requirements
#   deadline      — hard date constraints (e.g., critical path)
#   environmental — compliance with environmental regs
#   union         — workforce rules (hours, certifications)
#   custom        — client-specific contractual constraints
#
# violated_count + last_violation_at track how often the H-CARL agent
# proposed an action that violated this constraint during training
# rollouts — feeds into the explainability layer.

class ProjectConstraint(Base):
    __tablename__ = "project_constraints"

    id = Column(Integer, primary_key=True, index=True)

    organization_id = Column(
        Integer,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Tenant isolation — every constraint belongs to exactly one org.

    project_external_id = Column(String(120), nullable=True, index=True)
    # Client-provided project identifier (e.g., "TLV-2026-tower-A").
    # Multi-project tenants will have multiple constraints per org.

    constraint_kind = Column(String(40), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    description = Column(String, nullable=True)

    # JSON-encoded constraint expression. Schema is constraint-kind-specific;
    # consumers parse based on `constraint_kind`. Example for kind=deadline:
    #   {"task_id": "concrete_pour_3F", "deadline_iso": "2026-12-31T23:59:00Z",
    #    "buffer_hours": 24}
    expression_json = Column(String, nullable=False, default="{}")

    severity = Column(String(20), nullable=False, default="hard")
    # 'hard' (never violate) | 'soft' (penalize in reward, can violate)
    # The H-CARL agent uses this discriminator to decide whether to
    # multiply the reward by -infinity vs by a finite penalty.

    is_active = Column(Boolean, nullable=False, default=True)
    # Allows constraint deactivation without delete — preserves history.

    violated_count = Column(Integer, nullable=False, default=0)
    last_violation_at = Column(DateTime, nullable=True)
    # Updated by the H-CARL training loop when a proposed action would
    # have violated this constraint (training never executes; this
    # captures near-misses).

    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)

    organization = relationship("Organization", foreign_keys=[organization_id])


# ─────────────────────────────────────────────────────────────
# MODEL: HcarlPolicyState   (H-CARL state vectors + actions + rewards)
# ─────────────────────────────────────────────────────────────
# One row per training step / decision point in the H-CARL agent's
# rollout. Stores the (state, action, reward, next_state) tuple plus
# rich metadata for explainability.
#
# `level`:
#   strategic   — long-horizon portfolio allocation (months → quarter)
#   tactical    — weekly scheduling / workforce / materials
#   operational — daily task sequencing, dispatch, safety interventions
#
# state_json:    JSON-encoded observation vector (project KPIs, twin state)
# action_json:   JSON-encoded proposed action (which crew, when, where)
# reward_metrics: JSON object splitting reward into cost/time/quality/safety
# constraint_violations: list of ProjectConstraint.id this action would
#                        have violated (empty in production — H-CARL is
#                        constrained to never propose violators)

class HcarlPolicyState(Base):
    __tablename__ = "hcarl_policy_states"

    id = Column(Integer, primary_key=True, index=True)

    organization_id = Column(
        Integer,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    project_external_id = Column(String(120), nullable=True, index=True)

    rollout_id = Column(String(64), nullable=False, index=True)
    # Groups all states that belong to one training / inference episode.
    # UUID4 generated at episode start by hcarl_orchestrator service.

    step_index = Column(Integer, nullable=False)
    # 0-based index within the rollout (rollout, step_index) is unique.

    level = Column(String(20), nullable=False, index=True)
    # 'strategic' | 'tactical' | 'operational'

    state_json = Column(String, nullable=False, default="{}")
    action_json = Column(String, nullable=False, default="{}")

    reward_metrics_json = Column(String, nullable=False, default="{}")
    # {cost: float, time: float, quality: float, safety: float,
    #  total: float (weighted sum), explanation: str}

    constraint_violations_json = Column(String, nullable=True)
    # JSON list of ProjectConstraint.id values this action would violate.
    # Should be EMPTY in production rollouts (the agent is constrained
    # to skip violating actions). Populated during exploration training.

    is_human_overridden = Column(Boolean, nullable=False, default=False)
    # True iff a human operator approved a different action than what
    # H-CARL proposed — feeds the gamified-feedback loop in the PDF.

    model_version = Column(String(60), nullable=True)
    # Tag of the H-CARL checkpoint that produced this state.

    created_at = Column(
        DateTime, default=datetime.datetime.utcnow, nullable=False, index=True,
    )

    organization = relationship("Organization", foreign_keys=[organization_id])

    __table_args__ = (
        UniqueConstraint(
            "rollout_id", "step_index",
            name="uq_hcarl_rollout_step",
        ),
    )


# ─────────────────────────────────────────────────────────────
# MODEL: CausalInsight   (Causal Insights Graph nodes)
# ─────────────────────────────────────────────────────────────
# One row per causal hypothesis the system tracks. Used to power
# the explainability layer: when H-CARL recommends action X, the
# Causal Insights Graph contributes "X causes Y under conditions Z
# with probability P (95% CI)" narratives.
#
# Stores BOTH the structural causal model (graph node) AND a denormalized
# narrative for fast rendering. The graph topology (edges between nodes)
# is implicit via `parent_insight_id` — each insight may have one parent
# (the cause it amplifies) or none (root cause).
#
# evidence_json: list of supporting observations + their weights.
# probability + confidence_interval: posterior estimates from Bayesian
#   inference over the project data.

class CausalInsight(Base):
    __tablename__ = "causal_insights"

    id = Column(Integer, primary_key=True, index=True)

    organization_id = Column(
        Integer,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    project_external_id = Column(String(120), nullable=True, index=True)

    parent_insight_id = Column(
        Integer,
        ForeignKey("causal_insights.id", ondelete="SET NULL"),
        nullable=True,
    )
    # NULL = root cause hypothesis. Self-referencing FK forms the graph.

    insight_kind = Column(String(40), nullable=False, index=True)
    # 'root_cause' | 'mediator' | 'effect' | 'confounder'

    summary = Column(String(400), nullable=False)
    narrative = Column(String, nullable=True)
    # Natural-language explanation; generated by Gemini Pro via the
    # CausalInsights service (PDF §4: "Decision Transparency").

    probability = Column(Float, nullable=False, default=0.0)
    confidence_low = Column(Float, nullable=False, default=0.0)
    confidence_high = Column(Float, nullable=False, default=0.0)
    # Posterior probability with 95% Bayesian credible interval.

    evidence_json = Column(String, nullable=False, default="[]")
    # JSON list of supporting observations.

    related_constraint_id = Column(
        Integer,
        ForeignKey("project_constraints.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Some insights are tied to a hard constraint (e.g., "this insight
    # explains a safety-rule near-miss"). NULL when not constraint-tied.

    is_validated = Column(Boolean, nullable=False, default=False)
    # Set True when a human operator confirms the insight is accurate;
    # contributes to model retraining priority.

    created_at = Column(
        DateTime, default=datetime.datetime.utcnow, nullable=False, index=True,
    )

    parent = relationship("CausalInsight", remote_side=[id], backref="children")
    related_constraint = relationship(
        "ProjectConstraint", foreign_keys=[related_constraint_id]
    )


# ─────────────────────────────────────────────────────────────
# MODEL: FederatedSyncLog   (Federated Learning training-round audit)
# ─────────────────────────────────────────────────────────────
# One row per federated training round. Captures the round-level
# metadata: which orgs participated, total aggregated samples, model
# version produced, accuracy delta vs prior round.
#
# Critically, NO RAW DATA is stored here — only aggregated weights
# pointers (`weights_uri`) and tenant participation metadata. This is
# the privacy invariant: even an admin reading this table directly
# learns nothing about any individual org's data.

class FederatedSyncLog(Base):
    __tablename__ = "federated_sync_logs"

    id = Column(Integer, primary_key=True, index=True)

    round_id = Column(String(64), nullable=False, unique=True, index=True)
    # UUID4 generated at round start by federated_sync service.

    model_name = Column(String(80), nullable=False, index=True)
    # Which model is being trained (e.g., "hcarl_strategic_v1",
    # "predictive_site_anomaly_v3").

    started_at = Column(DateTime, nullable=False)
    finished_at = Column(DateTime, nullable=True)
    duration_ms = Column(Integer, nullable=True)

    participating_org_count = Column(Integer, nullable=False, default=0)
    # k-anonymity floor: we only run rounds where this is >= 5 (set by
    # FEDERATED_MIN_PARTICIPANTS env var; defaults to 5). Smaller rounds
    # could allow re-identification via differential analysis.

    total_aggregated_samples = Column(Integer, nullable=False, default=0)
    # Sum of per-org sample counts. Counts only, not raw data.

    aggregated_weights_uri = Column(String(500), nullable=True)
    # gs:// URI of the aggregated model weights blob (Cloud Storage).
    # NULL if the round failed before weight aggregation.

    accuracy_metric = Column(Float, nullable=True)
    accuracy_delta_vs_prev = Column(Float, nullable=True)
    # Held-out validation accuracy. Drift detection alerts if delta < 0
    # for N consecutive rounds.

    status = Column(String(20), nullable=False, default="started")
    # 'started' | 'aggregating' | 'success' | 'failed' | 'rejected_k_anon'

    error = Column(String, nullable=True)

    # Differential privacy guarantees applied:
    dp_epsilon = Column(Float, nullable=True)
    dp_delta = Column(Float, nullable=True)
    # NULL when DP wasn't applied (e.g., research rounds); populated
    # for production rounds.

    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)


# ─────────────────────────────────────────────────────────────
# MODEL: GrowthMilestone   (CEO-facing Growth Engine target tracking)
# ─────────────────────────────────────────────────────────────
# One row per (feature, milestone) pair. Tracks the current value of
# the gating metric, the configured threshold, and whether the CEO has
# formally activated the feature (after the threshold was crossed).
#
# Auto-seeded on first access — `growth_summary` endpoint creates the
# four canonical rows (one per AutonomousFeature) on first call.
# `current_value` is recomputed on every `/growth/summary` call from
# the live system metrics; threshold is read from feature_flags config.
#
# `is_unlocked` flips True ONLY via the WebAuthn-gated activate endpoint.
# Once flipped, it stays True (CEO has explicitly opted in — turning off
# requires manual SQL or a future deactivate endpoint).

class GrowthMilestone(Base):
    __tablename__ = "growth_milestones"

    id = Column(Integer, primary_key=True, index=True)

    feature_name = Column(String(60), nullable=False, unique=True, index=True)
    # One of AutonomousFeature values: 'hcarl_orchestrator',
    # 'predictive_site', 'causal_insights', 'federated_learning'

    threshold_metric = Column(String(40), nullable=False)
    # System-scale metric that gates this feature (see feature_flags).

    threshold_value = Column(Integer, nullable=False)
    # Required value of `threshold_metric` to unlock activation.

    current_value = Column(Integer, nullable=False, default=0)
    # Last computed value. Refreshed on every `/growth/summary` call.

    is_unlocked = Column(Boolean, nullable=False, default=False)
    # True ONLY after the CEO clicks "Activate Service" + completes
    # WebAuthn step-up. NEVER flipped automatically by the threshold
    # crossing alone — explicit human approval is required.

    unlocked_at = Column(DateTime, nullable=True)
    unlocked_by_user_id = Column(
        Integer, ForeignKey("users.id"), nullable=True
    )
    # Audit: who activated, when. NULL while is_unlocked=False.

    last_updated_at = Column(
        DateTime, default=datetime.datetime.utcnow, nullable=False,
    )
    # Bumped on every refresh of current_value.

    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)


# ═══════════════════════════════════════════════════════════════
# MODEL: NativeDeviceKey   (Sprint 8.2 — Aurora Mac Shell)
# ═══════════════════════════════════════════════════════════════
# Each row represents a MacBook (or future iPad) whose Secure
# Enclave–generated ECDSA P-256 public key has been registered with
# Aurora via the handshake protocol in app/routers/native_shell.py.
#
# The PRIVATE key never leaves the founder's Secure Enclave silicon —
# we only ever see the public key (X.963 / PEM-encoded) and a SHA-256
# fingerprint of it ("device_id"). To prove possession, the device
# must sign a server-issued challenge with its private key; that
# signature is verified against the stored public key here.
#
# Multi-row semantics:
#   • Same device_id may have many revoked rows + at most one active.
#   • A "Reset Binding" in the shell creates a fresh SE key → new
#     device_id → new row. The old row stays for audit trail.
#   • Revocation is a soft delete (revoked_at + revoked_reason) so
#     the audit chain is preserved.
# ═══════════════════════════════════════════════════════════════
class NativeDeviceKey(Base):
    __tablename__ = "native_device_keys"

    id = Column(Integer, primary_key=True, index=True)

    user_id = Column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    # The founder (or future operator) who owns this binding.

    device_id = Column(String(64), nullable=False, index=True)
    # SHA-256 hex of the X.963-encoded public key. Stable across
    # launches on the same MacBook. Treated as an opaque
    # device-fingerprint everywhere downstream.

    public_key_pem = Column(String, nullable=False)
    # SubjectPublicKeyInfo PEM — human-readable form for audit /
    # debugging. Used by `cryptography.hazmat` for verification.

    public_key_b64 = Column(String, nullable=False)
    # X.963 uncompressed point (65 bytes), base64. This is what the
    # shell's `SecKeyCopyExternalRepresentation` produces; we keep
    # it so cross-verification with the shell is byte-exact.

    device_label = Column(String(120), nullable=True)
    # Human-friendly: "MacBook Pro 16'' (Ibraheem)". User-supplied.

    aaguid = Column(String(64), nullable=True)
    # Reserved for future device-attestation (e.g., Apple's
    # PlatformProvisioner credentials). NULL in v1.

    enrolled_at = Column(
        DateTime, default=datetime.datetime.utcnow, nullable=False
    )
    last_used_at = Column(DateTime, nullable=True)
    # Touched on every successful _resolve_native_session() call.

    use_count = Column(Integer, nullable=False, default=0)
    # Audit signal — how many requests this device has authorized.

    revoked_at = Column(DateTime, nullable=True)
    revoked_reason = Column(String, nullable=True)
    # Soft-delete. Once set, the device cannot satisfy
    # require_native_shell(...) — middleware filters revoked_at IS NULL
    # on every request.

    __table_args__ = (
        # At most one ACTIVE row per device_id (partial unique index).
        # Revoked rows pile up freely — that's the audit chain.
        Index(
            "ix_native_device_keys_active_unique",
            "device_id",
            unique=True,
            postgresql_where=sa_text("revoked_at IS NULL"),
        ),
        # Fast "list active devices for user X" query path.
        Index(
            "ix_native_device_keys_user_active",
            "user_id",
            "enrolled_at",
            postgresql_where=sa_text("revoked_at IS NULL"),
        ),
    )


# ═══════════════════════════════════════════════════════════════
# MODEL: NativeHandshakeChallenge   (Sprint 8.2 — Aurora Mac Shell)
# ═══════════════════════════════════════════════════════════════
# Ephemeral storage for the single-shot challenges issued by
# /api/v1/admin/exec/native/handshake/start and consumed by
# /handshake/finish.
#
# Lifecycle:
#   1. Created by /start with `expires_at = now + 60s`
#   2. Consumed by /finish (sets `consumed_at`, sig verified)
#   3. Phase 20 migration sweeps rows older than 24h on each boot
#      (keeps audit visibility for ~1 day, then GC)
#
# Cross-user attack guard: the row's user_id is set from the
# authenticated admin's user_id at /start. /finish requires the
# challenge_id be looked up WITH a user_id filter, so user A can
# never consume user B's challenge even if they steal challenge_id.
# ═══════════════════════════════════════════════════════════════
class NativeHandshakeChallenge(Base):
    __tablename__ = "native_handshake_challenges"

    id = Column(Integer, primary_key=True, index=True)

    challenge_id = Column(
        String(64), nullable=False, unique=True, index=True
    )
    # UUID4 — the public token the client returns at /finish.

    user_id = Column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    # Bound to the authenticated user at /start — prevents cross-user
    # replay even if challenge_id leaks.

    device_id_hint = Column(String(64), nullable=True)
    # What the client CLAIMED at /start. Untrusted until /finish
    # proves possession via commitment + signature. We re-check at
    # /finish that the client didn't switch device_id mid-handshake.

    challenge_bytes = Column(LargeBinary, nullable=False)
    # 32 cryptographically random bytes the client must sign with
    # its SE private key. Raw bytes — base64 encode/decode at the
    # router boundary only.

    issued_at = Column(
        DateTime, default=datetime.datetime.utcnow, nullable=False
    )
    expires_at = Column(DateTime, nullable=False)
    # 60-second TTL by default (see CHALLENGE_TTL_SECONDS in router).

    consumed_at = Column(DateTime, nullable=True)
    # Single-shot. Once set, a replay attempt at /finish raises
    # `challenge_already_consumed`.

    __table_args__ = (
        Index(
            "ix_native_handshake_challenges_active_lookup",
            "challenge_id",
            "expires_at",
            postgresql_where=sa_text("consumed_at IS NULL"),
        ),
    )


# ═══════════════════════════════════════════════════════════════
# MODEL: AccountantDevice   (Sprint 8.2 sibling — Accountant Portal)
# ═══════════════════════════════════════════════════════════════
# Multi-active device-fingerprint registry for external accountants
# using the Tauri + Next.js portal at ~/Desktop/.../accountant-portal.
#
# DIFFERENT from NativeDeviceKey (Mac shell, CEO-only):
#   • Multi-active per user (up to 5 devices simultaneously)
#   • Advisory only — no cryptographic possession proof. The
#     fingerprint is just a stable identifier (SHA-256 of
#     machine UID), used as audit signal + "new device detected"
#     alert trigger, NOT as a hard authentication factor.
#   • Cross-platform (macos / windows / linux), not Apple-only.
#   • The accountant access token + refresh token (separate table)
#     are the actual auth gates; the device row is metadata.
# ═══════════════════════════════════════════════════════════════
class AccountantDevice(Base):
    __tablename__ = "accountant_devices"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    device_fingerprint = Column(String(128), nullable=False)
    # SHA-256 hex (64 chars) of (machine UID + bundle id). Stable
    # on the same physical machine; different across machines. NOT
    # cryptographically possession-provable (no SE key behind it).

    platform = Column(String(20), nullable=False)
    # "macos" | "windows" | "linux"

    device_label = Column(String(120), nullable=True)
    # User-supplied: "MacBook Pro (Office)", "Lenovo ThinkPad", etc.

    ip_hash_first = Column(String(64), nullable=False)
    # SHA-256 of (IP + random per-user salt). First-seen IP at enrollment.
    last_seen_at = Column(DateTime, nullable=False)
    last_seen_ip_hash = Column(String(64), nullable=False)
    use_count = Column(Integer, nullable=False, default=0)

    enrolled_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    revoked_at = Column(DateTime, nullable=True)
    revoked_reason = Column(String, nullable=True)

    new_device_alert_sent_at = Column(DateTime, nullable=True)
    # When the "new sign-in detected" email was sent for this device.

    __table_args__ = (
        # At most one ACTIVE row per (user_id, device_fingerprint).
        Index(
            "ix_accountant_devices_user_active",
            "user_id", "device_fingerprint",
            unique=True,
            postgresql_where=sa_text("revoked_at IS NULL"),
        ),
        Index(
            "ix_accountant_devices_user_recent",
            "user_id", "last_seen_at",
            postgresql_where=sa_text("revoked_at IS NULL"),
        ),
    )


# ═══════════════════════════════════════════════════════════════
# MODEL: AccountantRefreshToken   (Sprint 8.2 sibling)
# ═══════════════════════════════════════════════════════════════
# Long-lived (30d) refresh tokens for the accountant portal.
# Stored as SHA-256 hash — never plaintext. Rotation tracking via
# (used_at, replaced_by_id) chain so we can detect replay attacks.
#
# Rotation semantics:
#   • Each successful /refresh consumes the current token
#     (sets used_at + replaced_by_id) and issues a new one
#   • Reuse of a consumed token is a security event: revoke the
#     whole chain + alert
# ═══════════════════════════════════════════════════════════════
class AccountantRefreshToken(Base):
    __tablename__ = "accountant_refresh_tokens"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    device_id = Column(
        Integer, ForeignKey("accountant_devices.id"), nullable=False, index=True
    )

    token_hash = Column(String(64), nullable=False, unique=True, index=True)
    # SHA-256 hex of the opaque token. Plaintext NEVER persisted.

    issued_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=False)

    used_at = Column(DateTime, nullable=True)
    # Set on rotation. Once non-null, token is dead; reuse = security event.

    replaced_by_id = Column(
        Integer, ForeignKey("accountant_refresh_tokens.id"), nullable=True
    )
    # Chain: previous → next rotation. Lets us trace the lineage.

    revoked_at = Column(DateTime, nullable=True)
    revoked_reason = Column(String, nullable=True)
    # Explicit revocation (e.g., admin revoke, device revoke,
    # detected replay attack).

    last_used_ip_hash = Column(String(64), nullable=True)


# ═══════════════════════════════════════════════════════════════
# MODEL: AccountantOtpAttempt   (Sprint 8.2 sibling)
# ═══════════════════════════════════════════════════════════════
# Email-OTP rows for accountant sign-in. Separate from the
# existing OtpVerification (which is for SMB-owner onboarding)
# to keep the auth flows independent — they have different TTLs,
# different attempt limits, different lockout semantics.
#
# Lifecycle:
#   1. /otp/send creates a row with 60s TTL, attempts_count=0
#   2. /otp/verify increments attempts_count on each wrong guess
#   3. After 3 wrong attempts → locked_until = now + 15min
#   4. Success → consumed_at = now (one-shot)
#   5. Phase 21 sweeps rows older than 1h
# ═══════════════════════════════════════════════════════════════
class AccountantOtpAttempt(Base):
    __tablename__ = "accountant_otp_attempts"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(120), nullable=False, index=True)

    otp_hash = Column(String(64), nullable=False)
    # SHA-256 of the 6-digit code. We never store the plaintext OTP.

    issued_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=False)

    attempts_count = Column(Integer, nullable=False, default=0)
    locked_until = Column(DateTime, nullable=True)
    consumed_at = Column(DateTime, nullable=True)

    ip_hash = Column(String(64), nullable=False)
    # SHA-256 of caller IP. Used by rate-limiting + audit.

    delivery_method = Column(String(20), nullable=False, default="email")
    # "email" | "whatsapp"

    __table_args__ = (
        Index(
            "ix_accountant_otp_attempts_email_recent",
            "email", "issued_at",
            postgresql_where=sa_text("consumed_at IS NULL"),
        ),
    )


# ═══════════════════════════════════════════════════════════════
# MODEL: ClientDocument   (Sprint 8.3 — Document Vault DB Layer)
# ═══════════════════════════════════════════════════════════════
# Every file that lands in the Document Vault — whether forwarded by
# the client via WhatsApp, emailed to their personal vault alias, or
# uploaded manually by their accountant — becomes a row in this table.
#
# The row carries:
#   - Routing metadata     (agency_id, client_id, uploaded_by_vector)
#   - Object-store pointer (s3_bucket / s3_key — the file itself never
#                            lives in Postgres, only its checksum + ref)
#   - Content fingerprint  (sha256, mime_type, size_bytes) for dedup
#                            and forensic chain-of-custody
#   - Classification slot  (document_type, tax_year, extracted_metadata)
#                            populated asynchronously by the OCR + ML
#                            classifier worker
#   - Compliance lifecycle (created_at, archived_until, deleted_at)
#
# COMPLIANCE INVARIANTS (DB-level, not application-level):
#
#   1. 7-year retention   — Israeli Tax Ordinance §134B requires
#      taxpayers to retain books and records for 7 calendar years.
#      `archived_until` MUST be at least 7 years after `created_at`.
#      A CHECK constraint enforces this at write time — the application
#      cannot accidentally set a shorter retention window.
#
#   2. Retention lock     — `deleted_at` may only be populated AFTER
#      `archived_until` has passed. A CHECK constraint blocks
#      premature soft-deletes regardless of code path. The audit
#      trail is therefore tamper-evident at the database level.
#
# These constraints intentionally use PostgreSQL interval syntax. The
# table is provisioned in production against Postgres; local SQLite
# dev runs the same model but skips the temporal CHECK (SQLite has no
# `interval` keyword — the constraint is added in the Postgres-only
# migration `migrate_phase21_vault.py`).
# ═══════════════════════════════════════════════════════════════
class ClientDocument(Base):
    __tablename__ = "client_documents"

    id = Column(Integer, primary_key=True, index=True)

    # ── Routing ──
    # `agency_id` = the accountant's firm (an Organization with the
    # "agency" sub-type). `client_id` = the SMB whose books the agency
    # is keeping. Both are foreign keys to organizations.
    agency_id = Column(
        Integer,
        ForeignKey("organizations.id"),
        index=True,
        nullable=False,
    )
    client_id = Column(
        Integer,
        ForeignKey("organizations.id"),
        index=True,
        nullable=False,
    )

    # How the document entered the vault — drives the upstream
    # classification pipeline and the audit narrative.
    uploaded_by_vector = Column(String(16), nullable=False)
    # "whatsapp" | "email" | "manual"

    # ── Object-store pointer (file itself lives in S3 / GCS) ──
    s3_key = Column(String(512), unique=True, nullable=False)
    s3_bucket = Column(String(120), nullable=False)

    # ── Classification slot (populated by async classifier worker) ──
    document_type = Column(String(24), default="unclassified", nullable=False)
    # "invoice" | "receipt" | "bank_statement" | "tax_form" | ...

    # ── File metadata + content fingerprint ──
    file_name = Column(String(255), nullable=False)
    mime_type = Column(String(80), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    sha256 = Column(String(64), index=True, nullable=False)

    # ── Provenance ──
    sender_phone_e164 = Column(String(20), nullable=True)
    sender_email = Column(String(255), nullable=True)
    extracted_metadata = Column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=True,
    )

    # ── Tax-period + lifecycle ──
    tax_year = Column(Integer, index=True, nullable=False)
    status = Column(String(16), default="received", nullable=False)
    # "received" | "classified" | "exported" | "archived" | "error"
    error_reason = Column(String, nullable=True)

    # ── Compliance timestamps ──
    created_at = Column(
        DateTime,
        default=datetime.datetime.utcnow,
        nullable=False,
    )
    archived_until = Column(DateTime, index=True, nullable=False)
    deleted_at = Column(DateTime, nullable=True)

    # ── Compliance constraints (Postgres-enforced) ──
    __table_args__ = (
        CheckConstraint(
            "archived_until >= created_at + interval '7 years'",
            name="compliance_7_year_retention_check",
        ),
        CheckConstraint(
            "deleted_at IS NULL OR deleted_at > archived_until",
            name="retention_lock_prevent_premature_delete",
        ),
        Index(
            "ix_client_doc_agency_client",
            "agency_id", "client_id",
        ),
        Index(
            "ix_client_doc_taxyear_status",
            "tax_year", "status",
        ),
        Index(
            "ix_client_doc_client_created",
            "client_id", "created_at",
        ),
    )


# ═══════════════════════════════════════════════════════════════
# MODEL: VaultIngestionAddress   (Sprint 8.3 — Document Vault Router)
# ═══════════════════════════════════════════════════════════════
# Each client organization gets exactly ONE row that maps:
#
#   {email_alias_token, whatsapp_e164}  →  client_id
#
# Clients forward documents to:
#
#   vault+<email_alias_token>@api-aurora-lts.com
#       OR
#   WhatsApp DID associated with the agency, which routes by
#   sender phone number against `whatsapp_e164`.
#
# `email_alias_token` is a high-entropy 16-character hex string. It's
# unguessable enough that an attacker who knows a client's name still
# cannot guess the alias. Tokens are stable for life — once printed
# in onboarding material, they do not rotate without explicit admin
# action (rotation would invalidate the printed handouts).
#
# `whatsapp_e164` is optional because not every client links a phone;
# email-only ingestion is supported.
#
# `active=False` disables ingestion without deleting the row, so the
# audit chain `email_alias_token → historical_client_id` survives.
# ═══════════════════════════════════════════════════════════════
class VaultIngestionAddress(Base):
    __tablename__ = "vault_ingestion_addresses"

    id = Column(Integer, primary_key=True, index=True)

    client_id = Column(
        Integer,
        ForeignKey("organizations.id"),
        unique=True,
        nullable=False,
    )

    email_alias_token = Column(String(48), unique=True, nullable=False)
    # 16-hex-char by default (8 bytes from secrets.token_hex). Width 48
    # leaves headroom for future longer tokens.

    whatsapp_e164 = Column(String(20), index=True, nullable=True)
    active = Column(Boolean, default=True, nullable=False)
    created_at = Column(
        DateTime,
        default=datetime.datetime.utcnow,
        nullable=False,
    )


# ═══════════════════════════════════════════════════════════════
# P1-22 — API KEYS (service-to-service authentication)
# ═══════════════════════════════════════════════════════════════
# Used by Make.com webhook relays + future integration partners.
# The plaintext key is hashed (SHA-256) before persist — even a DB
# leak does not expose usable credentials. Lookups are by hash, so
# the plaintext is only known at mint time + held by the caller.
class ApiKey(Base):
    __tablename__ = "api_keys"

    id = Column(Integer, primary_key=True, index=True)

    # Human label — what / who this key is for. NOT a secret.
    name = Column(String(120), nullable=False, unique=True, index=True)

    # SHA-256 hex digest of the plaintext key. The plaintext is shown
    # to the operator once at mint time, then discarded.
    key_hash = Column(String(64), nullable=False, unique=True, index=True)

    # Optional scope string (free-form for now; e.g. 'make-webhook').
    scope = Column(String(80), nullable=True)

    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    last_used_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)



# ═══════════════════════════════════════════════════════════════
# P2-01 — RECURRING INVOICE SCHEDULES
# ═══════════════════════════════════════════════════════════════
# A template for an invoice that should be issued at a fixed
# cadence (e.g. "10,000 ILS from Customer X on the 1st of every
# month"). The tick worker queries `next_due_at <= now() AND active`,
# creates a draft invoice via services/invoice_service.create_draft_invoice,
# and advances next_due_at by `cadence`.
class RecurringInvoiceSchedule(Base):
    __tablename__ = "recurring_invoice_schedules"

    id = Column(Integer, primary_key=True, index=True)

    business_id = Column(
        Integer, ForeignKey("businesses.id"), nullable=False, index=True
    )

    # Invoice template fields — mirror create_draft_invoice args.
    beneficiary_name = Column(String(200), nullable=False)
    beneficiary_tax_id = Column(String(32), nullable=True)
    beneficiary_contact = Column(String(255), nullable=True)
    amount_net = Column(Float, nullable=False)
    description = Column(String, nullable=True)

    # Cadence — one of: "weekly" "monthly" "quarterly" "yearly".
    cadence = Column(String(16), nullable=False)

    # Driver state — when the NEXT invoice should be minted.
    next_due_at = Column(DateTime, nullable=False, index=True)
    # When the LAST invoice was minted (null on a brand-new schedule).
    last_run_at = Column(DateTime, nullable=True)

    # Soft delete — flip to False to stop generating without losing
    # the audit trail of past runs.
    active = Column(Boolean, default=True, nullable=False, index=True)

    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    __table_args__ = (
        Index(
            "ix_recurring_due_active",
            "next_due_at", "active",
        ),
    )


# ═══════════════════════════════════════════════════════════════
# P2-02 — FX RATES (Bank of Israel daily feed)
# ═══════════════════════════════════════════════════════════════
# Israeli Tax Authority requires invoice reporting in ILS even when
# the customer is billed in a foreign currency. We cache the daily
# Bank of Israel rate per (currency, observed_date) so that:
#   - Conversions are deterministic + auditable (same rate used by
#     the regulator).
#   - We don't hit BoI's public API on every invoice render.
#
# Unique on (currency, observed_date) — at most one rate per day.
class FxRate(Base):
    __tablename__ = "fx_rates"

    id = Column(Integer, primary_key=True, index=True)

    # ISO-4217: "USD", "EUR", "GBP", etc.
    currency = Column(String(3), nullable=False, index=True)

    # How many ILS one unit of `currency` is worth on observed_date.
    # e.g. currency="USD", rate_to_ils=3.65 → 1 USD = 3.65 ILS.
    rate_to_ils = Column(Float, nullable=False)

    # The date the rate was OBSERVED by BoI (typically the previous
    # business day). NOT when we fetched it.
    observed_date = Column(DateTime, nullable=False, index=True)

    # When we ingested this row.
    fetched_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)

    # Provenance — which feed did the rate come from.
    # "boi" = bank of israel (default). Future: "manual_override".
    source = Column(String(16), nullable=False, default="boi")

    __table_args__ = (
        UniqueConstraint("currency", "observed_date", name="uq_fx_currency_date"),
        Index("ix_fx_currency_date", "currency", "observed_date"),
    )


# ═══════════════════════════════════════════════════════════════
# P2-06 — BANK STATEMENT ENTRIES (reconciliation engine)
# ═══════════════════════════════════════════════════════════════
# Rows ingested from bank statements (CSV upload today; Open Banking
# AISP feed in the future when per-bank credentials are provisioned).
# The reconciliation matcher tries to link each entry to an Invoice
# by amount + date + counterparty fuzzy match.
class BankStatementEntry(Base):
    __tablename__ = "bank_statement_entries"

    id = Column(Integer, primary_key=True, index=True)
    business_id = Column(Integer, ForeignKey("businesses.id"), nullable=False, index=True)

    # ── Transaction details from the bank ──
    posted_at = Column(DateTime, nullable=False, index=True)
    amount = Column(Float, nullable=False)         # positive = credit, negative = debit
    currency = Column(String(3), nullable=False, default="ILS")
    counterparty_name = Column(String(255), nullable=True)
    reference = Column(String(120), nullable=True)  # bank's free-text memo
    source_bank = Column(String(40), nullable=True)  # "leumi", "discount", ...
    external_id = Column(String(120), nullable=True, index=True)  # idempotency

    # ── Reconciliation state ──
    # unmatched | suggested | linked | ignored
    match_status = Column(String(16), nullable=False, default="unmatched", index=True)
    matched_invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=True, index=True)
    match_confidence = Column(Float, nullable=True)   # 0.0–1.0 when matched
    match_reason = Column(String(255), nullable=True)

    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    matched_at = Column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("business_id", "external_id", name="uq_bse_biz_extid"),
        Index("ix_bse_business_status_date", "business_id", "match_status", "posted_at"),
    )


# ═══════════════════════════════════════════════════════════════
# P2-07 — INVOICE PAYMENTS (partial payment support)
# ═══════════════════════════════════════════════════════════════
# One InvoicePayment row per payment applied to an Invoice. The sum
# of rows for an invoice = total paid. balance_due = amount_total - sum.
# Auto-created when a BankStatementEntry links to an Invoice (P2-06).
class InvoicePayment(Base):
    __tablename__ = "invoice_payments"

    id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(
        Integer, ForeignKey("invoices.id"), nullable=False, index=True,
    )

    amount = Column(Float, nullable=False)
    currency = Column(String(3), nullable=False, default="ILS")
    paid_at = Column(DateTime, nullable=False)

    # Where did the payment come from?
    source = Column(String(40), nullable=False, default="manual")
    # "manual" | "bank_statement" | "payplus" | "remittance_link"

    # Backreference to the bank statement entry, if reconciliation
    # generated the row. NULL for manual entries.
    bank_entry_id = Column(
        Integer, ForeignKey("bank_statement_entries.id"),
        nullable=True, index=True,
    )

    note = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    __table_args__ = (
        UniqueConstraint("invoice_id", "bank_entry_id", name="uq_payment_invoice_bank"),
        Index("ix_invoice_payments_invoice_id", "invoice_id"),
    )


# ═══════════════════════════════════════════════════════════════
# P2-08 — AML / SANCTIONS SCREENING
# ═══════════════════════════════════════════════════════════════
# Cached entries from public sanctions lists. Refreshed weekly via
# Cloud Scheduler hitting POST /api/v1/aml/refresh-lists.
class SanctionsListEntry(Base):
    __tablename__ = "sanctions_list_entries"

    id = Column(Integer, primary_key=True, index=True)

    # Source: "ofac_sdn" | "il_mof" | "eu_consolidated" | "uk_hmt"
    list_source = Column(String(32), nullable=False, index=True)
    # External ID within that list (OFAC uses an integer; we store as string).
    external_id = Column(String(64), nullable=False, index=True)

    full_name = Column(String(512), nullable=False, index=True)
    # Other names / aliases stored as a single comma-separated string —
    # avoids JSON storage on SQLite. Fine for fuzzy matching.
    aliases = Column(String, nullable=True)

    entity_type = Column(String(16), nullable=True)  # "individual" | "entity"
    country_code = Column(String(8), nullable=True)
    program = Column(String(120), nullable=True)     # e.g. "SDGT", "IRAN-EO13902"

    last_updated_at = Column(DateTime, nullable=True)  # publisher date
    fetched_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("list_source", "external_id", name="uq_sanctions_src_extid"),
        Index("ix_sanctions_full_name", "full_name"),
    )


# ═══════════════════════════════════════════════════════════════
# P2-08 — Sanctions screening hit log
# ═══════════════════════════════════════════════════════════════
# Every screening call writes a row — high-score hits get human review.
class SanctionsScreeningHit(Base):
    __tablename__ = "sanctions_screening_hits"

    id = Column(Integer, primary_key=True, index=True)
    business_id = Column(Integer, ForeignKey("businesses.id"), nullable=True, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=True, index=True)

    queried_name = Column(String(512), nullable=False)
    matched_entry_id = Column(
        Integer, ForeignKey("sanctions_list_entries.id"), nullable=False,
    )
    match_score = Column(Float, nullable=False)
    # "pending_review" | "false_positive" | "confirmed" | "ignored"
    status = Column(String(24), nullable=False, default="pending_review", index=True)

    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    reviewed_at = Column(DateTime, nullable=True)
    reviewed_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    review_note = Column(String(500), nullable=True)


# ═══════════════════════════════════════════════════════════════
# P2-20 — Predictive Anomaly Detection events
# ═══════════════════════════════════════════════════════════════
class AnomalyEvent(Base):
    __tablename__ = "anomaly_events"

    id = Column(Integer, primary_key=True, index=True)
    business_id = Column(Integer, ForeignKey("businesses.id"), nullable=True, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=True, index=True)

    signal_type = Column(String(48), nullable=False, index=True)
    # "low" | "medium" | "high" | "critical"
    severity = Column(String(16), nullable=False, index=True)
    score = Column(Float, nullable=False)
    description = Column(String(1000), nullable=False)
    metadata_json = Column(String, nullable=True)   # serialised dict

    # Lifecycle: "open" → "acknowledged" | "false_positive" | "escalated"
    status = Column(String(24), nullable=False, default="open", index=True)

    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    resolved_at = Column(DateTime, nullable=True)
    resolved_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    resolution_note = Column(String(500), nullable=True)

    __table_args__ = (
        Index("ix_anomaly_events_business_signal", "business_id", "signal_type"),
        Index("ix_anomaly_events_created_at", "created_at"),
    )


# ═══════════════════════════════════════════════════════════════
# P2-22 — VAT Return Filing
# ═══════════════════════════════════════════════════════════════
class VatReturn(Base):
    __tablename__ = "vat_returns"

    id = Column(Integer, primary_key=True, index=True)
    business_id = Column(Integer, ForeignKey("businesses.id"), nullable=False, index=True)
    tax_id = Column(String(20), nullable=True)

    # Period identification
    period_year = Column(Integer, nullable=False)
    period_number = Column(Integer, nullable=False)        # 1–6 (bi-monthly) or 1–4 (quarterly)
    period_frequency = Column(String(16), nullable=False)  # "bimonthly" | "quarterly"
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    due_date = Column(Date, nullable=False)

    # Sales (outputs)
    taxable_sales_ils = Column(Float, nullable=False, default=0.0)
    vat_collected_ils = Column(Float, nullable=False, default=0.0)
    exempt_sales_ils = Column(Float, nullable=False, default=0.0)
    invoice_count = Column(Integer, nullable=False, default=0)

    # Purchases (inputs)
    taxable_purchases_ils = Column(Float, nullable=False, default=0.0)
    input_vat_ils = Column(Float, nullable=False, default=0.0)
    expense_count = Column(Integer, nullable=False, default=0)

    # Net
    net_vat_payable_ils = Column(Float, nullable=False, default=0.0)

    # Lifecycle: draft → submitted | rejected
    status = Column(String(16), nullable=False, default="draft", index=True)
    confirmation_number = Column(String(64), nullable=True)
    rejection_reason = Column(String(500), nullable=True)

    submitted_at = Column(DateTime, nullable=True)
    submitted_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "business_id", "period_year", "period_number", "period_frequency",
            name="uq_vat_return_period",
        ),
        Index("ix_vat_returns_due_date", "due_date"),
    )


# ═══════════════════════════════════════════════════════════════
# P2-23 — Payment Links
# ═══════════════════════════════════════════════════════════════
class PaymentLink(Base):
    __tablename__ = "payment_links"

    id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False, index=True)
    business_id = Column(Integer, ForeignKey("businesses.id"), nullable=False, index=True)

    # Cryptographic material
    token = Column(String(64), unique=True, nullable=False, index=True)
    nonce = Column(String(32), nullable=False)

    # Financial metadata (denormalised for fast checkout rendering)
    amount_ils = Column(Float, nullable=False)
    currency = Column(String(3), nullable=False, default="ILS")

    # Lifecycle: open → paid | expired | cancelled | failed
    status = Column(String(16), nullable=False, default="open", index=True)

    expires_at = Column(DateTime, nullable=False)
    paid_at = Column(DateTime, nullable=True)
    payplus_transaction_id = Column(String(128), nullable=True)

    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    __table_args__ = (
        Index("ix_payment_links_invoice_status", "invoice_id", "status"),
    )
