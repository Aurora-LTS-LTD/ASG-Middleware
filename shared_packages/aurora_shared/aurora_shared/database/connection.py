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

REAL-WORLD ANALOGY:
  This file is the "front desk" of the hotel. When a request arrives,
  the front desk (SessionLocal) hands out a room key (session). When
  the request finishes, the key is returned. In dev the hotel runs out
  of a single guest book (SQLite file); in production it's a real
  Postgres server in Tel Aviv (Cloud SQL me-west1) with pooling.

ENV VARS:
  DATABASE_URL          (optional in dev, required in production)
                        e.g. "postgresql+psycopg://user:pass@/db?host=/cloudsql/PROJECT:REGION:INSTANCE"
                        defaults to "sqlite:///./asg_platform.db"

  AURORA_DB_POOL_SIZE   default 10  (Postgres only)
  AURORA_DB_MAX_OVERFLOW default 20 (Postgres only)
  AURORA_DB_POOL_RECYCLE default 1800 sec / 30 min (Postgres only)
"""

# ─────────────────────────────────────────────────────────────
# IMPORTS
# ─────────────────────────────────────────────────────────────
import os
import threading

from sqlalchemy import create_engine, event
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import sessionmaker, declarative_base


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
# CREATE THE ENGINE — dialect-aware (Lazy Initialization)
# ─────────────────────────────────────────────────────────────
# The engine is deferred from import-time to first-use-time to avoid
# blocking container startup with pool_pre_ping validation.
# This uses double-check locking for thread-safety.
_engine = None
_engine_lock = threading.Lock()


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
    pool_size = int(os.getenv("AURORA_DB_POOL_SIZE", "10"))
    max_overflow = int(os.getenv("AURORA_DB_MAX_OVERFLOW", "20"))
    pool_recycle = int(os.getenv("AURORA_DB_POOL_RECYCLE", "1800"))
    return create_engine(
        SQLALCHEMY_DATABASE_URL,
        pool_pre_ping=True,     # drop dead conns before use
        pool_size=pool_size,    # baseline conns kept open
        max_overflow=max_overflow,  # extra conns under burst
        pool_recycle=pool_recycle,  # rotate idle conns periodically
    )


def get_engine():
    """
    Get the SQLAlchemy engine, initializing it lazily on first access.
    
    Uses double-check locking for thread-safety. The real engine is only
    created once, on the first call to get_engine() or access to the
    proxy object. Subsequent calls return the cached engine.
    
    Returns:
        sqlalchemy.engine.Engine: The configured database engine.
    """
    global _engine
    
    # First check (without lock) for performance.
    if _engine is not None:
        return _engine
    
    # Acquire lock to ensure only one thread initializes.
    with _engine_lock:
        # Second check (with lock) to prevent race condition.
        if _engine is not None:
            return _engine
        
        # Initialize the real engine.
        _engine = _build_engine()
        print(f"[DATABASE] Engine bound: dialect={DIALECT!r}, url={_url_obj.render_as_string(hide_password=True)}")
        return _engine


class _LazyEngine:
    """
    Proxy object that transparently defers all attribute access to the real
    engine, initialized lazily on first access.
    
    This allows backward-compatible code that accesses the `engine` variable
    to work seamlessly without modification, while still deferring engine
    creation from import-time to first-use-time.
    """
    
    def __getattr__(self, name):
        """Forward all attribute access to the real engine."""
        real_engine = get_engine()
        return getattr(real_engine, name)
    
    def __repr__(self):
        """Forward repr to the real engine."""
        real_engine = get_engine()
        return repr(real_engine)
    
    def __str__(self):
        """Forward str to the real engine."""
        real_engine = get_engine()
        return str(real_engine)


# Create the proxy object. Existing code that uses `engine` will work
# transparently because _LazyEngine proxies all operations.
engine = _LazyEngine()


# ─────────────────────────────────────────────────────────────
# WAL MODE (SQLite only — no-op on Postgres)
# ─────────────────────────────────────────────────────────────
# Postgres has native MVCC and never needs this. SQLite does:
# without WAL, concurrent reads + writes deadlock in the dev process.
# We defer the listener registration until first engine access.
def _setup_sqlite_wal_mode():
    """Register SQLite WAL mode listener if needed."""
    if DIALECT == "sqlite":
        real_engine = get_engine()
        @event.listens_for(real_engine, "connect")
        def set_sqlite_wal_mode(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()


# Register the listener on first engine access (lazy).
_wal_mode_initialized = False
def _ensure_wal_mode():
    """Ensure WAL mode is set up (called lazily on first engine access)."""
    global _wal_mode_initialized
    if not _wal_mode_initialized:
        _setup_sqlite_wal_mode()
        _wal_mode_initialized = True


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
#
# The bind parameter is a callable (get_engine) rather than a static
# engine. This allows SQLAlchemy to call get_engine() each time a new
# session is created, which triggers the lazy initialization logic.
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=get_engine)


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
    from aurora_shared.database import models  # noqa: F401
    
    # Ensure WAL mode is set up before creating tables (lazy init).
    _ensure_wal_mode()
    
    # Get the real engine and create all tables.
    real_engine = get_engine()
    Base.metadata.create_all(bind=real_engine)
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
