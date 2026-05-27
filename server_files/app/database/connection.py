"""
ASG / Aurora — Database Connection
====================================
Sets up the SQLAlchemy engine + session factory used everywhere.

DUAL-DIALECT SUPPORT (Part III, Phase 2 — GCP deployment):
  The same code runs against:
    - SQLite (local dev, default fallback)        → ./asg_platform.db
    - Postgres (Cloud SQL, production)            → DATABASE_URL env var

  The engine + session config is dialect-aware:
    SQLite    → check_same_thread=False, WAL pragma, no pooling
    Postgres  → connection pooling, pre-ping, recycle stale conns

P1-03 — POOL SIZING FOR CLOUD RUN:
  Worst-case Postgres connections =
      (AURORA_DB_POOL_SIZE + AURORA_DB_MAX_OVERFLOW)
    × AURORA_GUNICORN_WORKERS
    × AURORA_CLOUD_RUN_MAX_INSTANCES

  This MUST stay below the Cloud SQL instance connection ceiling
  (db-custom-1-3840 = 100, db-custom-2-7680 = 200, etc.) minus
  ~20% headroom for migrations + manual psql + monitoring.

  At boot, we compute the worst case and log a WARNING if the
  configured values exceed the budget. We do not crash — dev
  environments often use values that exceed any production budget.

ENV VARS:
  DATABASE_URL                       (optional in dev, required in production)
                                     e.g. "postgresql+psycopg://user:pass@/db?host=/cloudsql/PROJECT:REGION:INSTANCE"
                                     defaults to "sqlite:///./asg_platform.db"

  AURORA_DB_POOL_SIZE                default 5   (Postgres only)
  AURORA_DB_MAX_OVERFLOW             default 5   (Postgres only)
  AURORA_DB_POOL_RECYCLE             default 1800 sec / 30 min (Postgres only)

  AURORA_CLOUD_SQL_MAX_CONNECTIONS   default 100  (db-custom-1-3840 floor)
  AURORA_CLOUD_RUN_MAX_INSTANCES     default 10   (must match Cloud Run cap)
  AURORA_GUNICORN_WORKERS            default 2    (must match Dockerfile -w)
"""

# ─────────────────────────────────────────────────────────────
# IMPORTS
# ─────────────────────────────────────────────────────────────
import logging
import os

from sqlalchemy import create_engine, event
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import sessionmaker, declarative_base

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# DATABASE URL
# ─────────────────────────────────────────────────────────────
# Production: set via DATABASE_URL secret (Secret Manager → Cloud Run env).
# Development: defaults to local SQLite file.
SQLALCHEMY_DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite:///./asg_platform.db",
)

# Detect dialect once so downstream code (and migrations) can branch.
_url_obj = make_url(SQLALCHEMY_DATABASE_URL)
DIALECT = _url_obj.get_backend_name()  # "sqlite" | "postgresql" | ...


# ─────────────────────────────────────────────────────────────
# CREATE THE ENGINE — dialect-aware
# ─────────────────────────────────────────────────────────────
def _build_engine():
    """Build the SQLAlchemy engine with sensible defaults per dialect."""
    if DIALECT == "sqlite":
        # SQLite needs `check_same_thread=False` because FastAPI uses
        # multiple threads. Pooling is unnecessary (single-file DB).
        return create_engine(
            SQLALCHEMY_DATABASE_URL,
            connect_args={"check_same_thread": False},
        )

    # Postgres (Cloud SQL) — production.
    # P1-03: defaults lowered from (10, 20) to (5, 5). Previous defaults
    # produced 30 conns/worker × 2 workers × 100 max-instances = 6,000
    # connections wanted at peak — far exceeds any reasonable Cloud SQL tier.
    pool_size = int(os.getenv("AURORA_DB_POOL_SIZE", "5"))
    max_overflow = int(os.getenv("AURORA_DB_MAX_OVERFLOW", "5"))
    pool_recycle = int(os.getenv("AURORA_DB_POOL_RECYCLE", "1800"))

    _emit_pool_budget_warning(pool_size, max_overflow)

    return create_engine(
        SQLALCHEMY_DATABASE_URL,
        pool_pre_ping=True,     # drop dead conns before use
        pool_size=pool_size,    # baseline conns kept open
        max_overflow=max_overflow,  # extra conns under burst
        pool_recycle=pool_recycle,  # rotate idle conns periodically
    )


def _emit_pool_budget_warning(pool_size: int, max_overflow: int) -> None:
    """
    Compute the worst-case Cloud SQL connection footprint and log a
    WARNING if it exceeds the configured ceiling. Never crashes — dev
    setups commonly exceed any production budget.
    """
    cap = int(os.getenv("AURORA_CLOUD_SQL_MAX_CONNECTIONS", "100"))
    max_instances = int(os.getenv("AURORA_CLOUD_RUN_MAX_INSTANCES", "10"))
    workers = int(os.getenv("AURORA_GUNICORN_WORKERS", "2"))
    headroom_fraction = 0.20  # reserve 20% for psql/migrations/monitoring

    per_worker = pool_size + max_overflow
    per_instance = per_worker * workers
    worst_case = per_instance * max_instances
    safe_budget = int(cap * (1 - headroom_fraction))

    log.info(
        "[DATABASE] pool budget: pool_size=%d max_overflow=%d "
        "(=%d/worker × %d workers × %d max-instances = %d worst-case) "
        "vs Cloud SQL cap=%d (safe budget %d with 20%% headroom)",
        pool_size, max_overflow, per_worker, workers,
        max_instances, worst_case, cap, safe_budget,
    )

    if worst_case > safe_budget:
        log.warning(
            "[DATABASE] ⚠️  Worst-case connection footprint (%d) exceeds the "
            "safe Cloud SQL budget (%d). At peak Cloud Run scale this will "
            "exhaust the database. Either lower AURORA_CLOUD_RUN_MAX_INSTANCES, "
            "raise AURORA_CLOUD_SQL_MAX_CONNECTIONS (requires a bigger SQL tier), "
            "or shrink AURORA_DB_POOL_SIZE / AURORA_DB_MAX_OVERFLOW.",
            worst_case, safe_budget,
        )


engine = _build_engine()
print(f"[DATABASE] Engine bound: dialect={DIALECT!r}, url={_url_obj.render_as_string(hide_password=True)}")


# ─────────────────────────────────────────────────────────────
# WAL MODE (SQLite only — no-op on Postgres)
# ─────────────────────────────────────────────────────────────
# Postgres has native MVCC and never needs this. SQLite does:
# without WAL, concurrent reads + writes deadlock in the dev process.
if DIALECT == "sqlite":
    @event.listens_for(engine, "connect")
    def set_sqlite_wal_mode(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()


# ─────────────────────────────────────────────────────────────
# SESSION FACTORY
# ─────────────────────────────────────────────────────────────
# SessionLocal is a "factory" — every time you call SessionLocal(),
# it creates a new database session (a fresh notepad).
#
# autocommit=False → changes are NOT saved automatically. You must
#   explicitly call db.commit() to save. This is safer because you
#   can roll back mistakes.
# autoflush=False → the database is NOT updated in real-time as you
#   make changes. Updates happen only when you commit.
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


# ─────────────────────────────────────────────────────────────
# BASE CLASS
# ─────────────────────────────────────────────────────────────
# All our models (Business, Invoice, ActionLog) will inherit from
# this Base. It's what makes them "known" to SQLAlchemy so it can
# create the actual database tables from our Python classes.
Base = declarative_base()


# ─────────────────────────────────────────────────────────────
# FUNCTION: create_tables
# ─────────────────────────────────────────────────────────────
# PURPOSE:
#   Look at all models that inherit from Base and create their
#   tables in the database if they don't exist yet.
#
# REAL-WORLD ANALOGY:
#   Imagine you have blueprints for 3 rooms (Business, Invoice,
#   ActionLog). This function builds those rooms in the hotel
#   if they haven't been built yet. If they already exist, it
#   does nothing (safe to call multiple times).
def create_tables():
    """Create all database tables based on the models."""
    # Import models here to ensure they're registered with Base
    # before we call create_all. Without this import, SQLAlchemy
    # wouldn't know about our models.
    from app.database import models  # noqa: F401
    Base.metadata.create_all(bind=engine)
    print("[DATABASE] All tables created successfully!")


# ─────────────────────────────────────────────────────────────
# FUNCTION: get_db
# ─────────────────────────────────────────────────────────────
# PURPOSE:
#   A FastAPI "dependency" that provides a database session to
#   each request and automatically closes it when done.
#
# REAL-WORLD ANALOGY:
#   When a guest checks into the hotel, they get a room key (session).
#   When they check out, the key is returned (session closed).
#   The "finally" block ensures the key is ALWAYS returned, even
#   if something goes wrong during the stay.
#
# HOW "yield" WORKS:
#   "yield" is like "pause here and hand over the session."
#   After the request is done, execution resumes after yield,
#   which runs the "finally" block to close the session.
def get_db():
    """FastAPI dependency: provides a database session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
