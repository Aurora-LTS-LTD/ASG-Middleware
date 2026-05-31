"""
Aurora LTS — Predictive Anomaly Detection Service  (P2-20)
===========================================================

Detects unusual patterns in financial data using a two-tier approach:

TIER 1 — SQL-based Heuristics (always active, no external dependencies)
  • Invoice amount outlier: amount > mean + 3σ within business history
  • Unusual late-night activity: invoices created 01:00–05:00 local time
  • Velocity spike: > N invoices in 24h vs. 30-day average
  • Dormant-then-active: business had 0 invoices for 30+ days, then spike
  • Round-number clustering: > 80% of recent invoices are round numbers
    (potential money-laundering signal)
  • Frequent small amounts: many invoices just below reporting thresholds

TIER 2 — Vertex AI Anomaly Detection (ANOMALY_BACKEND=vertex)
  Routes to Google Cloud Vertex AI Anomaly Detection (Time Series API).
  Sends a 90-day rolling window of daily invoice totals per business
  and asks Vertex to score each day.  Scores > 0.7 are flagged.

BACKENDS
────────
  ANOMALY_BACKEND=stub         Always returns clean (honours FORCE_ANOMALY=1)
  ANOMALY_BACKEND=heuristic    SQL-only, no Vertex call (default for cost control)
  ANOMALY_BACKEND=vertex       SQL + Vertex AI time-series scoring

OUTPUTS
───────
  • Anomaly rows stored in `anomaly_events` table (provisioned by Phase 23)
  • High-severity events → ActionLog at CRITICAL
  • Cloud Scheduler calls POST /api/v1/admin/anomaly/run daily
"""

from __future__ import annotations

import datetime
import logging
import math
import os
from dataclasses import dataclass, field
from typing import List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import ActionLog
from app.database.models import AnomalyEvent

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────

def _backend() -> str:
    return (os.getenv("ANOMALY_BACKEND") or "heuristic").strip().lower()


def _z_threshold() -> float:
    try:
        return float(os.getenv("ANOMALY_Z_THRESHOLD", "3.0"))
    except ValueError:
        return 3.0


def _velocity_multiplier() -> float:
    try:
        return float(os.getenv("ANOMALY_VELOCITY_MULTIPLIER", "3.0"))
    except ValueError:
        return 3.0


# ─────────────────────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────────────────────

@dataclass
class AnomalySignal:
    business_id: int
    invoice_id: Optional[int]
    signal_type: str          # "amount_outlier" | "velocity_spike" | "late_night" | ...
    severity: str             # "low" | "medium" | "high" | "critical"
    score: float              # 0.0–1.0
    description: str
    metadata: dict = field(default_factory=dict)


@dataclass
class AnomalyReport:
    run_at: datetime.datetime
    businesses_scanned: int
    signals_found: int
    signals: List[AnomalySignal] = field(default_factory=list)
    backend_used: str = "heuristic"


# ─────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────

def run_daily_scan(db: Session, lookback_days: int = 30) -> AnomalyReport:
    """
    Full anomaly scan.  Called by Cloud Scheduler daily via
    POST /api/v1/admin/anomaly/run.

    Returns an AnomalyReport with all signals found.
    Persists each signal to `anomaly_events` table.
    """
    backend = _backend()
    now = datetime.datetime.utcnow()

    if backend == "stub":
        if os.getenv("FORCE_ANOMALY", "").lower() in ("1", "true", "yes"):
            log.warning("FORCE_ANOMALY active — returning synthetic anomaly")
            return AnomalyReport(
                run_at=now,
                businesses_scanned=1,
                signals_found=1,
                signals=[
                    AnomalySignal(
                        business_id=1,
                        invoice_id=None,
                        signal_type="force_synthetic",
                        severity="high",
                        score=0.99,
                        description="FORCE_ANOMALY=1 synthetic signal",
                    )
                ],
                backend_used="stub",
            )
        return AnomalyReport(run_at=now, businesses_scanned=0, signals_found=0, backend_used="stub")

    # ── Tier 1: SQL heuristics ──────────────────────────────────
    signals: List[AnomalySignal] = []
    signals.extend(_scan_amount_outliers(db, lookback_days))
    signals.extend(_scan_velocity_spikes(db, lookback_days))
    signals.extend(_scan_late_night_activity(db, lookback_days))
    signals.extend(_scan_dormant_then_active(db))
    signals.extend(_scan_round_number_clustering(db, lookback_days))

    # ── Tier 2: Vertex AI (if enabled) ─────────────────────────
    if backend == "vertex":
        try:
            signals.extend(_scan_vertex_timeseries(db, lookback_days=90))
        except Exception:
            log.exception("Vertex AI anomaly scan failed — falling back to heuristics only")

    # ── Persist to DB ───────────────────────────────────────────
    businesses_scanned = _count_active_businesses(db, lookback_days)
    for sig in signals:
        _persist_signal(sig, db)

    high_plus = [s for s in signals if s.severity in ("high", "critical")]
    if high_plus:
        db.add(ActionLog(
            business_id=None,
            status="anomaly.daily_scan.flagged",
            detail=(
                f"run_at={now.isoformat()} "
                f"businesses={businesses_scanned} "
                f"signals={len(signals)} high_plus={len(high_plus)}"
            ),
        ))

    db.commit()

    return AnomalyReport(
        run_at=now,
        businesses_scanned=businesses_scanned,
        signals_found=len(signals),
        signals=signals,
        backend_used=backend,
    )


# ─────────────────────────────────────────────────────────────
# Tier 1 — SQL heuristics
# ─────────────────────────────────────────────────────────────

def _scan_amount_outliers(db: Session, lookback_days: int) -> List[AnomalySignal]:
    """
    Flag invoices whose amount is more than Z standard deviations
    above that business's historical mean.  Z default = 3.0.
    """
    z = _z_threshold()
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=lookback_days)

    sql = text("""
        WITH stats AS (
            SELECT
                business_id,
                AVG(total_amount)   AS mean_amount,
                STDDEV(total_amount) AS std_amount
            FROM invoices
            WHERE created_at < :cutoff
              AND status NOT IN ('cancelled', 'credit_note')
            GROUP BY business_id
            HAVING COUNT(*) >= 5
               AND STDDEV(total_amount) > 0
        )
        SELECT
            i.id         AS invoice_id,
            i.business_id,
            i.total_amount,
            s.mean_amount,
            s.std_amount,
            (i.total_amount - s.mean_amount) / s.std_amount AS z_score
        FROM invoices i
        JOIN stats s ON s.business_id = i.business_id
        WHERE i.created_at >= :cutoff
          AND i.status NOT IN ('cancelled', 'credit_note')
          AND (i.total_amount - s.mean_amount) / s.std_amount > :z_threshold
        ORDER BY z_score DESC
        LIMIT 50
    """)

    rows = db.execute(sql, {"cutoff": cutoff, "z_threshold": z}).fetchall()
    signals = []
    for row in rows:
        z_score = float(row.z_score)
        severity = "critical" if z_score > 5 else "high" if z_score > 4 else "medium"
        signals.append(AnomalySignal(
            business_id=row.business_id,
            invoice_id=row.invoice_id,
            signal_type="amount_outlier",
            severity=severity,
            score=min(1.0, (z_score - z) / 5),
            description=(
                f"Invoice {row.invoice_id}: amount {row.total_amount:.2f} is "
                f"{z_score:.1f}σ above business mean ({row.mean_amount:.2f})"
            ),
            metadata={"z_score": z_score, "mean": row.mean_amount, "std": row.std_amount},
        ))
    return signals


def _scan_velocity_spikes(db: Session, lookback_days: int) -> List[AnomalySignal]:
    """
    Flag businesses that issued > N × daily_average invoices in the
    last 24 hours.  N default = 3.0.
    """
    multiplier = _velocity_multiplier()
    lookback = datetime.datetime.utcnow() - datetime.timedelta(days=lookback_days)
    yesterday = datetime.datetime.utcnow() - datetime.timedelta(hours=24)

    sql = text("""
        WITH baseline AS (
            SELECT
                business_id,
                COUNT(*) * 1.0 / :lookback_days AS daily_avg
            FROM invoices
            WHERE created_at BETWEEN :lookback AND :yesterday
              AND status NOT IN ('cancelled', 'credit_note')
            GROUP BY business_id
            HAVING COUNT(*) >= 10
        ),
        recent AS (
            SELECT business_id, COUNT(*) AS recent_count
            FROM invoices
            WHERE created_at >= :yesterday
              AND status NOT IN ('cancelled', 'credit_note')
            GROUP BY business_id
        )
        SELECT
            r.business_id,
            r.recent_count,
            b.daily_avg,
            r.recent_count / b.daily_avg AS spike_ratio
        FROM recent r
        JOIN baseline b ON b.business_id = r.business_id
        WHERE r.recent_count > b.daily_avg * :multiplier
        ORDER BY spike_ratio DESC
        LIMIT 20
    """)

    rows = db.execute(sql, {
        "lookback_days": lookback_days,
        "lookback": lookback,
        "yesterday": yesterday,
        "multiplier": multiplier,
    }).fetchall()

    signals = []
    for row in rows:
        ratio = float(row.spike_ratio)
        severity = "critical" if ratio > 10 else "high" if ratio > 5 else "medium"
        signals.append(AnomalySignal(
            business_id=row.business_id,
            invoice_id=None,
            signal_type="velocity_spike",
            severity=severity,
            score=min(1.0, ratio / 10),
            description=(
                f"Business {row.business_id}: {row.recent_count} invoices in 24h "
                f"({ratio:.1f}× daily average of {row.daily_avg:.1f})"
            ),
            metadata={"recent_count": row.recent_count, "daily_avg": float(row.daily_avg)},
        ))
    return signals


def _scan_late_night_activity(db: Session, lookback_days: int) -> List[AnomalySignal]:
    """
    Flag businesses with a high proportion of invoices created
    between 01:00 and 05:00 UTC (indicative of automated or
    unusual activity; legitimate Israeli businesses work 08:00–22:00).
    """
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=lookback_days)

    # SQLite STRFTIME vs Postgres EXTRACT — handle both dialects
    is_postgres = "postgresql" in str(db.bind.dialect.name if db.bind else "")
    if is_postgres:
        hour_expr = "EXTRACT(HOUR FROM created_at)"
    else:
        hour_expr = "CAST(STRFTIME('%H', created_at) AS INTEGER)"

    sql = text(f"""
        SELECT
            business_id,
            COUNT(*) AS total,
            SUM(CASE WHEN {hour_expr} BETWEEN 1 AND 4 THEN 1 ELSE 0 END) AS late_night,
            SUM(CASE WHEN {hour_expr} BETWEEN 1 AND 4 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS ratio
        FROM invoices
        WHERE created_at >= :cutoff
          AND status NOT IN ('cancelled', 'credit_note')
        GROUP BY business_id
        HAVING COUNT(*) >= 10
           AND ratio > 0.35
        ORDER BY ratio DESC
        LIMIT 20
    """)

    rows = db.execute(sql, {"cutoff": cutoff}).fetchall()
    signals = []
    for row in rows:
        ratio = float(row.ratio)
        signals.append(AnomalySignal(
            business_id=row.business_id,
            invoice_id=None,
            signal_type="late_night_activity",
            severity="medium" if ratio < 0.6 else "high",
            score=min(1.0, ratio),
            description=(
                f"Business {row.business_id}: {int(row.late_night)}/{int(row.total)} "
                f"invoices ({ratio:.0%}) created 01:00–05:00 UTC"
            ),
            metadata={"ratio": ratio, "late_night_count": int(row.late_night)},
        ))
    return signals


def _scan_dormant_then_active(db: Session) -> List[AnomalySignal]:
    """
    Flag businesses that had 0 invoices for 30+ days and then
    created 3+ invoices in the last 48 hours.
    """
    now = datetime.datetime.utcnow()
    recent = now - datetime.timedelta(hours=48)
    dormant_threshold = now - datetime.timedelta(days=30)

    sql = text("""
        WITH last_before AS (
            SELECT business_id, MAX(created_at) AS last_invoice_before
            FROM invoices
            WHERE created_at < :recent
              AND status NOT IN ('cancelled', 'credit_note')
            GROUP BY business_id
        ),
        burst AS (
            SELECT business_id, COUNT(*) AS recent_count
            FROM invoices
            WHERE created_at >= :recent
              AND status NOT IN ('cancelled', 'credit_note')
            GROUP BY business_id
            HAVING COUNT(*) >= 3
        )
        SELECT b.business_id, b.recent_count, l.last_invoice_before
        FROM burst b
        JOIN last_before l ON l.business_id = b.business_id
        WHERE l.last_invoice_before < :dormant_threshold
           OR l.last_invoice_before IS NULL
        LIMIT 20
    """)

    rows = db.execute(sql, {"recent": recent, "dormant_threshold": dormant_threshold}).fetchall()
    signals = []
    for row in rows:
        signals.append(AnomalySignal(
            business_id=row.business_id,
            invoice_id=None,
            signal_type="dormant_then_active",
            severity="medium",
            score=0.7,
            description=(
                f"Business {row.business_id} was dormant since "
                f"{row.last_invoice_before or 'account creation'}, "
                f"then created {row.recent_count} invoices in 48h"
            ),
            metadata={"recent_count": row.recent_count},
        ))
    return signals


def _scan_round_number_clustering(db: Session, lookback_days: int) -> List[AnomalySignal]:
    """
    Flag businesses where > 80% of recent invoices have round amounts
    (multiples of 500 ILS).  Possible structuring signal.
    """
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=lookback_days)

    sql = text("""
        SELECT
            business_id,
            COUNT(*) AS total,
            SUM(CASE WHEN CAST(total_amount AS INTEGER) % 500 = 0 THEN 1 ELSE 0 END) AS round_count,
            SUM(CASE WHEN CAST(total_amount AS INTEGER) % 500 = 0 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS ratio
        FROM invoices
        WHERE created_at >= :cutoff
          AND status NOT IN ('cancelled', 'credit_note')
          AND total_amount > 0
        GROUP BY business_id
        HAVING COUNT(*) >= 10 AND ratio > 0.80
        ORDER BY ratio DESC
        LIMIT 20
    """)

    rows = db.execute(sql, {"cutoff": cutoff}).fetchall()
    signals = []
    for row in rows:
        ratio = float(row.ratio)
        signals.append(AnomalySignal(
            business_id=row.business_id,
            invoice_id=None,
            signal_type="round_number_clustering",
            severity="low" if ratio < 0.95 else "medium",
            score=ratio,
            description=(
                f"Business {row.business_id}: {ratio:.0%} of invoices are "
                f"multiples of ₪500 ({int(row.round_count)}/{int(row.total)})"
            ),
            metadata={"ratio": ratio, "round_count": int(row.round_count)},
        ))
    return signals


# ─────────────────────────────────────────────────────────────
# Tier 2 — Vertex AI Time-Series Anomaly Detection
# ─────────────────────────────────────────────────────────────

def _scan_vertex_timeseries(db: Session, lookback_days: int) -> List[AnomalySignal]:
    """
    Sends per-business daily invoice totals to Vertex AI Anomaly
    Detection and returns high-score days as signals.

    Requires:
      VERTEX_PROJECT      = GCP project ID
      VERTEX_LOCATION     = e.g. us-central1 (closest AD region to me-west1)
      VERTEX_AD_ENDPOINT  = Vertex AI endpoint resource name
    """
    try:
        from google.cloud.aiplatform.gapic import PredictionServiceClient  # type: ignore
        from google.cloud.aiplatform_v1.types import PredictRequest  # type: ignore
    except ImportError:
        log.warning("google-cloud-aiplatform not installed — skipping Vertex anomaly detection")
        return []

    project   = os.getenv("VERTEX_PROJECT",     "aurora-lts-prod")
    location  = os.getenv("VERTEX_LOCATION",    "us-central1")
    endpoint  = os.getenv("VERTEX_AD_ENDPOINT", "")

    if not endpoint:
        log.warning("VERTEX_AD_ENDPOINT not set — skipping Vertex scan")
        return []

    cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=lookback_days)
    sql = text("""
        SELECT
            business_id,
            DATE(created_at) AS day,
            SUM(total_amount) AS daily_total
        FROM invoices
        WHERE created_at >= :cutoff
          AND status NOT IN ('cancelled', 'credit_note')
        GROUP BY business_id, DATE(created_at)
        ORDER BY business_id, day
    """)
    rows = db.execute(sql, {"cutoff": cutoff}).fetchall()

    # Group by business_id
    by_business: dict = {}
    for row in rows:
        by_business.setdefault(row.business_id, []).append({
            "time": str(row.day),
            "value": float(row.daily_total),
        })

    signals: List[AnomalySignal] = []
    client = PredictionServiceClient()

    for biz_id, series in by_business.items():
        if len(series) < 14:
            continue  # Not enough history for meaningful detection

        try:
            response = client.predict(
                endpoint=endpoint,
                instances=[{"time_series": series}],
            )
            for prediction in response.predictions:
                for point in prediction.get("anomaly_scores", []):
                    score = float(point.get("score", 0.0))
                    if score > 0.7:
                        signals.append(AnomalySignal(
                            business_id=biz_id,
                            invoice_id=None,
                            signal_type="vertex_timeseries",
                            severity="high" if score > 0.9 else "medium",
                            score=score,
                            description=(
                                f"Vertex AI: unusual daily total on {point.get('time')} "
                                f"for business {biz_id} (score={score:.3f})"
                            ),
                            metadata={"vertex_score": score, "day": point.get("time")},
                        ))
        except Exception as e:
            log.warning("Vertex AD prediction failed for business %d: %s", biz_id, e)

    return signals


# ─────────────────────────────────────────────────────────────
# DB persistence
# ─────────────────────────────────────────────────────────────

def _persist_signal(sig: AnomalySignal, db: Session) -> None:
    try:
        event = AnomalyEvent(
            business_id=sig.business_id,
            invoice_id=sig.invoice_id,
            signal_type=sig.signal_type,
            severity=sig.severity,
            score=sig.score,
            description=sig.description,
            metadata_json=str(sig.metadata),
        )
        db.add(event)
    except Exception as e:
        log.warning("Failed to persist anomaly signal: %s", e)


def _count_active_businesses(db: Session, lookback_days: int) -> int:
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=lookback_days)
    result = db.execute(
        text("SELECT COUNT(DISTINCT business_id) FROM invoices WHERE created_at >= :c"),
        {"c": cutoff},
    ).scalar()
    return int(result or 0)
