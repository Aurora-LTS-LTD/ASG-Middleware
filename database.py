from sqlalchemy import create_engine, Column, Integer, String, Text, Float, ForeignKey, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker
import datetime
import uuid   # for generating unique portal tokens

# ─────────────────────────────────────────────────────────────
# DATABASE URL
# "sqlite" = the database engine we are using (file-based, no server needed)
# "./ags_dashboard.db" = the file will be created in the same folder as this script
# ─────────────────────────────────────────────────────────────
SQLALCHEMY_DATABASE_URL = "sqlite:///./ags_dashboard.db"

# create_engine → opens the connection to the database file
# check_same_thread=False → required for SQLite when used with FastAPI (async requests)
engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)

# sessionmaker → a factory that creates "sessions" (database conversations)
# Each request gets its own session, uses it, then closes it
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base → the parent class all our database models (tables) will inherit from
Base = declarative_base()


# ─────────────────────────────────────────────────────────────
# TABLE: categories
# A Category is like a "franchise brand" — it defines what TYPE
# of business this is (Garage, Law Office, Restaurant, etc.)
# Each category has its own set of TemplateBlueprints (the ops manual).
# ─────────────────────────────────────────────────────────────
class Category(Base):
    __tablename__ = "categories"

    id          = Column(Integer, primary_key=True, index=True)
    name        = Column(String, unique=True, index=True)     # e.g. "HVAC", "Legal", "Logistics"
    description = Column(String, nullable=True)               # what this category is about
    created_at  = Column(DateTime, default=datetime.datetime.utcnow)

    # One category can have many businesses and many blueprints
    businesses = relationship("Business", back_populates="category")
    blueprints = relationship("TemplateBlueprint", back_populates="category")


# ─────────────────────────────────────────────────────────────
# TABLE: businesses
# Each instance of this class = one row in the businesses table
# NOW linked to a Category — when you add a business, you pick its category.
# ─────────────────────────────────────────────────────────────
class Business(Base):
    __tablename__ = "businesses"

    id               = Column(Integer, primary_key=True, index=True)
    name             = Column(String, index=True)
    business_type    = Column(String, default="Other")       # kept for backward compat
    channel          = Column(String, default="WhatsApp")    # WhatsApp / Telegram / Both
    status           = Column(String, default="active")      # active / inactive
    category_id      = Column(Integer, ForeignKey("categories.id"), nullable=True)  # which category
    portal_token     = Column(String, unique=True, index=True,
                              default=lambda: str(uuid.uuid4())[:12])  # unique "key card" for client portal
    telegram_api_key = Column(String, nullable=True)
    created_at       = Column(DateTime, default=datetime.datetime.utcnow)

    # Relationships
    category  = relationship("Category", back_populates="businesses")
    templates = relationship("Template", back_populates="owner")


# ─────────────────────────────────────────────────────────────
# TABLE: templates
# Each template belongs to one business (via business_id foreign key)
# ─────────────────────────────────────────────────────────────
class Template(Base):
    __tablename__ = "templates"

    id               = Column(Integer, primary_key=True, index=True)
    name             = Column(String, index=True)
    description      = Column(String, nullable=True)
    make_webhook_url = Column(String)     # The Make.com webhook URL to trigger
    business_id      = Column(Integer, ForeignKey("businesses.id"))
    created_at       = Column(DateTime, default=datetime.datetime.utcnow)

    # Link back to the parent business
    owner = relationship("Business", back_populates="templates")

    # One template can have many action logs
    logs = relationship("ActionLog", back_populates="template")


# ─────────────────────────────────────────────────────────────
# TABLE: template_blueprints
# A Blueprint is the "operations manual" for a category.
# Think of it like a franchise manual — every Garage business
# gets the same set of automations (schedule technician, send
# invoice, etc.) automatically when they join the Garage category.
#
# Blueprints belong to a CATEGORY, not a specific business.
# When a business joins a category, it inherits all blueprints.
# ─────────────────────────────────────────────────────────────
class TemplateBlueprint(Base):
    __tablename__ = "template_blueprints"

    id               = Column(Integer, primary_key=True, index=True)
    name             = Column(String, index=True)                     # e.g. "Schedule Technician"
    description      = Column(String, nullable=True)                  # what this blueprint does
    make_webhook_url = Column(String, nullable=True)                  # webhook URL for Make.com
    category_id      = Column(Integer, ForeignKey("categories.id"))   # belongs to which category
    actions_config   = Column(Text, nullable=True)                    # JSON string: actions, fields, etc.
    created_at       = Column(DateTime, default=datetime.datetime.utcnow)

    # Link back to the parent category
    category = relationship("Category", back_populates="blueprints")


# ─────────────────────────────────────────────────────────────
# TABLE: action_logs
# Every time someone clicks "Trigger" on a template, a new row
# is added here. Think of it as a doctor's journal — every
# treatment (trigger) gets recorded so nothing is forgotten.
# ─────────────────────────────────────────────────────────────
class ActionLog(Base):
    __tablename__ = "action_logs"

    id            = Column(Integer, primary_key=True, index=True)
    template_id   = Column(Integer, ForeignKey("templates.id"))   # which template was triggered
    business_id   = Column(Integer, ForeignKey("businesses.id"))  # which business it belongs to
    status        = Column(String)           # "triggered" / "simulated" / "failed"
    detail        = Column(String, nullable=True)  # extra info (error message, Make.com response, etc.)
    triggered_at  = Column(DateTime, default=datetime.datetime.utcnow)  # when it happened

    # Relationships — link back to the parent template and business
    template = relationship("Template", back_populates="logs")
    business = relationship("Business")


# ─────────────────────────────────────────────────────────────
# TABLE: invoices
# The financial brain of the system. Each row = one invoice.
#
# Think of it like a receipt book:
# - Who is it for? (beneficiary)
# - How much? (amount + VAT)
# - Did the government approve it? (allocation number)
# - Where's the PDF? (pdf_url)
#
# Israeli tax rules (2026):
# - VAT rate: 17%
# - Allocation number required for invoices >= 10,000 NIS (until June 2026)
# - From June 1, 2026: threshold drops to 5,000 NIS
# ─────────────────────────────────────────────────────────────
class Invoice(Base):
    __tablename__ = "invoices"

    id                  = Column(Integer, primary_key=True, index=True)
    business_id         = Column(Integer, ForeignKey("businesses.id"))  # who is issuing this invoice
    invoice_number      = Column(String, index=True)        # unique per business, e.g. "INV-001"

    # ── Beneficiary (the person/company receiving the invoice) ──
    beneficiary_name    = Column(String)                     # שם המוטב
    beneficiary_tax_id  = Column(String, nullable=True)      # ח.פ / ת.ז של המוטב
    beneficiary_contact = Column(String, nullable=True)      # email or phone for delivery

    # ── Financial details ──
    amount_net          = Column(Float)                      # סכום לפני מע"מ
    vat_rate            = Column(Float, default=0.17)        # שיעור מע"מ (17%)
    vat_amount          = Column(Float)                      # סכום המע"מ
    amount_total        = Column(Float)                      # סכום כולל מע"מ
    currency            = Column(String, default="ILS")      # מטבע

    # ── Tax authority compliance ──
    requires_allocation = Column(Integer, default=0)         # 1 if amount >= threshold, else 0
    allocation_number   = Column(String, nullable=True)      # מספר הקצאה מרשות המיסים (9 digits)
    allocation_status   = Column(String, default="pending")  # pending / approved / not_required / failed

    # ── Document ──
    pdf_url             = Column(String, nullable=True)      # link to the generated PDF
    status              = Column(String, default="draft")    # draft / finalized / sent / cancelled
    description         = Column(String, nullable=True)      # optional note

    created_at          = Column(DateTime, default=datetime.datetime.utcnow)
    finalized_at        = Column(DateTime, nullable=True)    # when it was locked and sent

    # Relationship back to the business
    business = relationship("Business")


# ─────────────────────────────────────────────────────────────
# CREATE TABLES
# This line reads all the classes above and creates the actual
# .db file + tables if they don't exist yet. Safe to run multiple times.
# ─────────────────────────────────────────────────────────────
Base.metadata.create_all(bind=engine)
