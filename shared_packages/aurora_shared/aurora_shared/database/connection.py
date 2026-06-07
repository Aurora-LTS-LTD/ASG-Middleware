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
import threading

from sqlalchemy import create_engine, event
from sqlalchemy.engine.url import make_url
from sqlalchemy.pool import StaticPool
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
        # `timeout` arms the busy handler so concurrent writers (request
        # handlers + background workers) WAIT up to 30s instead of erroring
        # with "database is locked" — only relevant to local SQLite dev;
        # production runs on Postgres.
        sqlite_engine = create_engine(
            SQLALCHEMY_DATABASE_URL,
            connect_args={"check_same_thread": False, "timeout": 30},
            # One shared connection across threads (SQLite serializes access),
            # so request handlers never deadlock against a second pooled
            # connection holding the WAL write lock. Standard SQLite-app config;
            # production runs on Postgres with a real QueuePool (this branch is
            # never taken there).
            poolclass=StaticPool,
        )

        # Eagerly enable WAL on every connection so readers (background-worker
        # SELECTs) never block the request handlers' writes. (The lazy
        # _ensure_wal_mode() hook only fires via create_tables(); this guarantees
        # it regardless.) WAL + busy_timeout = no "database is locked" locally.
        @event.listens_for(sqlite_engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _rec):  # pragma: no cover (dev-only)
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.close()

        return sqlite_engine

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
    P1-03 — Compute the worst-case Cloud SQL connection footprint and
    log a WARNING if it exceeds the configured ceiling. Never crashes —
    dev setups commonly exceed any production budget.

    Called from inside `_build_engine` so the warning fires the FIRST
    time the lazy engine is materialised, not at module import.
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


def get_engine():
    """
    Get the SQLAlchemy engine, initialising it lazily on first access.

    Double-check locking for thread-safety. The real engine is built
    exactly once per process — on the first call to get_engine() or the
    first attribute access on the `_LazyEngine` proxy. Subsequent calls
    return the cached engine.

    Returns:
        sqlalchemy.engine.Engine: The configured database engine.
    """
    global _engine

    # First check (without lock) for performance.
    if _engine is not None:
        return _engine

    # Acquire lock to ensure only one thread initialises.
    with _engine_lock:
        # Second check (with lock) to prevent race condition.
        if _engine is not None:
            return _engine

        _engine = _build_engine()
        print(
            f"[DATABASE] Engine bound: dialect={DIALECT!r}, "
            f"url={_url_obj.render_as_string(hide_password=True)}"
        )
        return _engine


class _LazyEngine:
    """
    Proxy object that transparently defers all attribute access to the
    real engine, initialised lazily on first access.

    This keeps the import-time API ("from … import engine") working,
    while moving the slow `pool_pre_ping` validation off the boot path —
    the v0.2.2 cutover stall fix. See docs/architecture/M1_STARTUP_STALL.md.

    NOTE: `sessionmaker(bind=engine)` uses this proxy, NOT the raw
    `get_engine` callable. SQLAlchemy 2.0's bind contract calls
    `bind.connect()` directly; passing the function reference raises
    `AttributeError: 'function' object has no attribute 'connect'`
    (the v0.2.3 hotfix that motivated commit edb5d60).
    """

    def __getattr__(self, name):
        real_engine = get_engine()
        return getattr(real_engine, name)

    def __repr__(self):
        real_engine = get_engine()
        return repr(real_engine)

    def __str__(self):
        real_engine = get_engine()
        return str(real_engine)


# Create the proxy object. Existing code that uses `engine` works
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
# The bind parameter is the `engine` proxy object (an instance of
# `_LazyEngine`), NOT the bare `get_engine` callable. SQLAlchemy 2.0's
# `sessionmaker(bind=...)` does NOT evaluate a callable bind — it
# treats `bind` as an Engine and invokes `bind.connect()` on it
# directly. Passing the raw function reference raises
# `AttributeError: 'function' object has no attribute 'connect'` on
# every session use (see git history for the v0.2.2 incident).
# The `_LazyEngine` proxy's __getattr__ forwards `.connect()` to
# `get_engine()` → real engine, preserving the lazy initialization
# semantics without breaking SQLAlchemy's bind contract.
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
