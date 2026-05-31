"""
Aurora LTS — AML / Sanctions Screening Service  (P2-08)
==========================================================

Screens business names, UBO names, and tax IDs against multiple
government-published sanctions lists and returns a structured risk
assessment.

LISTS SUPPORTED
───────────────
  • OFAC SDN (US Treasury) — https://ofac.treasury.gov
      XML feed refreshed weekly.  Programme examples: SDGT, IRAN, RUSSIA.
  • IL-MOF / NBCTF (Israeli National Bureau for Counter Terror Financing)
      Tab-separated CSV published by the Israeli Ministry of Finance.
  • EU Consolidated (stub — add endpoint when EU-facing clients arrive)
  • UK HMT (stub — idem)

BACKENDS
────────
  SANCTIONS_BACKEND=stub          Never fetches; always returns clean.
                                  Stub honours "FORCE_SANCTIONS_HIT=1"
                                  env flag for QA automation.
  SANCTIONS_BACKEND=ofac_only     Fetches + parses OFAC SDN only.
  SANCTIONS_BACKEND=full          OFAC SDN + IL-MOF + EU + UK (future).

LIST REFRESH
────────────
  Lists are cached in the `sanctions_list_entries` table. The Cloud
  Scheduler job hits POST /api/v1/aml/refresh-lists (admin-only) once
  per week.  The service also refreshes on the first run if the table
  is empty (cold-start safety).

MATCHING ALGORITHM
──────────────────
  1. Normalise both strings: lower-case, strip diacritics, collapse
     whitespace, remove punctuation.
  2. Exact match → score = 1.0.
  3. Token-set ratio (difflib.SequenceMatcher) on the full string.
  4. Per-token match: score = max(token_set_score, max_token_pair_score).
  5. Hit threshold: SANCTIONS_MATCH_THRESHOLD env (default 0.82).
  6. All hits above threshold written to `sanctions_screening_hits`.

RISK TIERS
──────────
  clean      No hit above threshold.
  low        Best score 0.82–0.89 (flag, allow, notify admin).
  medium     Best score 0.90–0.95 (flag, manual review required).
  high       Best score > 0.95 (block onboarding, alert admin critical).
  blocked    Exact match (score = 1.0) — zero-tolerance.
"""

from __future__ import annotations

import datetime
import difflib
import logging
import os
import re
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import httpx  # already in requirements for async HTTP
from sqlalchemy.orm import Session

from app.database import ActionLog
from app.database.models import SanctionsListEntry, SanctionsScreeningHit

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────

def _backend() -> str:
    return (os.getenv("SANCTIONS_BACKEND") or "stub").strip().lower()


def _match_threshold() -> float:
    try:
        return float(os.getenv("SANCTIONS_MATCH_THRESHOLD", "0.82"))
    except ValueError:
        return 0.82


# OFAC SDN Advanced XML — public, no auth required.
_OFAC_SDN_URL = (
    "https://ofac.treasury.gov/system/files/sdn_advanced.xml"
)
# Israeli MOF / NBCTF — public, tab-separated CSV.
_IL_MOF_URL = (
    "https://nbctf.mod.gov.il/he/Announcements/Documents/nbctf.xlsx"
)
_OFAC_HTTP_TIMEOUT = 60   # seconds — the SDN XML is ~12 MB
_IL_MOF_HTTP_TIMEOUT = 30


# ─────────────────────────────────────────────────────────────
# Normalisation helpers
# ─────────────────────────────────────────────────────────────

def _normalise(text: str) -> str:
    """
    Lower-case, strip diacritics (NFD → strip combining marks),
    collapse whitespace, remove punctuation.
    Leaves Latin, Hebrew, and Arabic base characters intact.
    """
    nfd = unicodedata.normalize("NFD", text)
    stripped = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
    lower = stripped.lower()
    no_punct = re.sub(r"[^\w\s]", " ", lower, flags=re.UNICODE)
    return re.sub(r"\s+", " ", no_punct).strip()


def _token_set_score(a: str, b: str) -> float:
    """
    Combination of full-string similarity and token-pair similarity.
    Returns float 0.0–1.0.
    """
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    full = difflib.SequenceMatcher(None, a, b).ratio()

    # Token-pair: best match of any token in `a` against any token in `b`.
    tokens_a = a.split()
    tokens_b = b.split()
    best_token = 0.0
    for ta in tokens_a:
        for tb in tokens_b:
            if len(ta) < 3 or len(tb) < 3:
                continue  # skip very short tokens (articles, etc.)
            s = difflib.SequenceMatcher(None, ta, tb).ratio()
            if s > best_token:
                best_token = s

    # Weight: 60% full-string, 40% best-token if token score is higher.
    return max(full, full * 0.6 + best_token * 0.4)


def _risk_tier(best_score: float) -> str:
    if best_score >= 1.0:
        return "blocked"
    if best_score >= 0.95:
        return "high"
    if best_score >= 0.90:
        return "medium"
    if best_score >= _match_threshold():
        return "low"
    return "clean"


# ─────────────────────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────────────────────

@dataclass
class ScreeningHitResult:
    entry_id: int
    list_source: str
    matched_name: str
    queried_name: str
    score: float
    entity_type: Optional[str]
    country_code: Optional[str]
    program: Optional[str]


@dataclass
class ScreeningResult:
    queried_name: str
    risk_tier: str                          # "clean" | "low" | "medium" | "high" | "blocked"
    best_score: float
    hits: List[ScreeningHitResult] = field(default_factory=list)
    lists_searched: List[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────

def screen_name(
    name: str,
    *,
    business_id: Optional[int] = None,
    invoice_id: Optional[int] = None,
    db: Session,
    auto_refresh_if_empty: bool = True,
) -> ScreeningResult:
    """
    Screen a single name against the cached sanctions lists.

    Parameters
    ----------
    name            : The name to screen (business name, UBO full name, etc.)
    business_id     : Optional — persists hits linked to this business.
    invoice_id      : Optional — persists hits linked to this invoice.
    db              : SQLAlchemy session.
    auto_refresh_if_empty : If True and the list table is empty, trigger
                      a synchronous refresh before screening.  Useful
                      for cold-start safety in staging environments.

    Returns
    -------
    ScreeningResult with risk_tier, best_score, and individual hits.
    """
    backend = _backend()
    normalised = _normalise(name)

    # ── Stub backend ──────────────────────────────────────────
    if backend == "stub":
        if os.getenv("FORCE_SANCTIONS_HIT", "").lower() in ("1", "true", "yes"):
            log.warning("FORCE_SANCTIONS_HIT active — returning synthetic high hit")
            return ScreeningResult(
                queried_name=name,
                risk_tier="high",
                best_score=0.97,
                hits=[],
                lists_searched=["stub"],
            )
        return ScreeningResult(
            queried_name=name,
            risk_tier="clean",
            best_score=0.0,
            hits=[],
            lists_searched=["stub"],
        )

    # ── Ensure lists are populated ────────────────────────────
    if auto_refresh_if_empty:
        count = db.query(SanctionsListEntry).count()
        if count == 0:
            log.info("Sanctions list table is empty — running initial sync")
            _sync_all_lists(db=db)

    threshold = _match_threshold()
    entries = db.query(SanctionsListEntry).all()
    lists_searched: List[str] = list({e.list_source for e in entries})

    hits: List[ScreeningHitResult] = []
    best_score = 0.0

    for entry in entries:
        # Build candidate names: full_name + all aliases
        candidates = [_normalise(entry.full_name)]
        if entry.aliases:
            candidates += [_normalise(a) for a in entry.aliases.split(",") if a.strip()]

        best_entry_score = max(_token_set_score(normalised, c) for c in candidates)

        if best_entry_score >= threshold:
            hits.append(ScreeningHitResult(
                entry_id=entry.id,
                list_source=entry.list_source,
                matched_name=entry.full_name,
                queried_name=name,
                score=round(best_entry_score, 4),
                entity_type=entry.entity_type,
                country_code=entry.country_code,
                program=entry.program,
            ))
            if best_entry_score > best_score:
                best_score = best_entry_score

    tier = _risk_tier(best_score)

    # ── Persist hits to DB ────────────────────────────────────
    for hit in hits:
        row = SanctionsScreeningHit(
            business_id=business_id,
            invoice_id=invoice_id,
            queried_name=name,
            matched_entry_id=hit.entry_id,
            match_score=hit.score,
            status="pending_review" if tier in ("medium", "high", "blocked") else "auto_cleared",
        )
        db.add(row)

    if hits:
        db.add(ActionLog(
            business_id=business_id,
            status=f"sanctions.screen.{tier}",
            detail=(
                f"name={name!r} best_score={best_score:.4f} "
                f"hits={len(hits)} lists={','.join(lists_searched)}"
            ),
        ))

    db.commit()

    return ScreeningResult(
        queried_name=name,
        risk_tier=tier,
        best_score=round(best_score, 4),
        hits=hits,
        lists_searched=lists_searched,
    )


def screen_multiple(
    names: List[str],
    *,
    business_id: Optional[int] = None,
    db: Session,
) -> List[ScreeningResult]:
    """
    Screen multiple names (e.g., business name + all UBO names) and
    return the worst-case aggregate result.  Still returns per-name results.
    """
    results = []
    for name in names:
        if name and name.strip():
            results.append(
                screen_name(
                    name,
                    business_id=business_id,
                    db=db,
                    auto_refresh_if_empty=(len(results) == 0),  # only on first
                )
            )
    return results


# ─────────────────────────────────────────────────────────────
# List sync  — called by Cloud Scheduler weekly
# ─────────────────────────────────────────────────────────────

def sync_lists(db: Session) -> dict:
    """
    Public entry point for the Cloud Scheduler / admin endpoint.
    Refreshes all lists depending on SANCTIONS_BACKEND.
    Returns a summary dict.
    """
    backend = _backend()
    if backend == "stub":
        return {"status": "skipped", "backend": "stub", "inserted": 0, "deleted": 0}
    return _sync_all_lists(db=db)


def _sync_all_lists(db: Session) -> dict:
    total_inserted = 0
    total_deleted = 0

    backend = _backend()
    sources: List[Tuple[str, callable]] = [("ofac_sdn", _sync_ofac_sdn)]
    if backend == "full":
        sources.append(("il_mof", _sync_il_mof))

    for source_name, sync_fn in sources:
        try:
            result = sync_fn(db=db)
            total_inserted += result.get("inserted", 0)
            total_deleted += result.get("deleted", 0)
            log.info(
                "Sanctions list synced: source=%s inserted=%d deleted=%d",
                source_name,
                result.get("inserted", 0),
                result.get("deleted", 0),
            )
        except Exception:
            log.exception("Failed to sync sanctions list: source=%s", source_name)

    return {
        "status": "ok",
        "backend": backend,
        "inserted": total_inserted,
        "deleted": total_deleted,
        "synced_at": datetime.datetime.utcnow().isoformat(),
    }


def _sync_ofac_sdn(db: Session) -> dict:
    """
    Download and parse the OFAC SDN Advanced XML.
    Upserts entries into sanctions_list_entries, deletes stale rows.
    """
    log.info("Fetching OFAC SDN XML from %s", _OFAC_SDN_URL)
    response = httpx.get(_OFAC_SDN_URL, timeout=_OFAC_HTTP_TIMEOUT, follow_redirects=True)
    response.raise_for_status()

    root = ET.fromstring(response.content)
    ns = {"ofac": "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/ADVANCED_XML"}

    now = datetime.datetime.utcnow()
    seen_external_ids: set = set()
    inserted = 0

    for entry in root.findall(".//ofac:sdnEntry", ns):
        uid_el = entry.find("ofac:uid", ns)
        if uid_el is None:
            continue
        external_id = uid_el.text or ""

        # Collect primary name
        last_el  = entry.find("ofac:lastName",  ns)
        first_el = entry.find("ofac:firstName", ns)
        last  = (last_el.text  or "").strip() if last_el  is not None else ""
        first = (first_el.text or "").strip() if first_el is not None else ""
        full_name = f"{first} {last}".strip() if first else last
        if not full_name:
            continue

        # Aliases from <akaList>
        aliases: List[str] = []
        for aka in entry.findall(".//ofac:aka", ns):
            aka_last  = aka.find("ofac:lastName",  ns)
            aka_first = aka.find("ofac:firstName", ns)
            l = (aka_last.text  or "").strip() if aka_last  is not None else ""
            f = (aka_first.text or "").strip() if aka_first is not None else ""
            alias = f"{f} {l}".strip() if f else l
            if alias and alias != full_name:
                aliases.append(alias)

        # Programs
        programs: List[str] = []
        for prog in entry.findall(".//ofac:program", ns):
            if prog.text:
                programs.append(prog.text.strip())

        # Country (first nationality or address country)
        country_el = entry.find(".//ofac:country", ns)
        country_code = (country_el.text or "").strip()[:8] if country_el is not None else None

        entity_type_el = entry.find("ofac:sdnType", ns)
        entity_type = (entity_type_el.text or "").lower()[:16] if entity_type_el is not None else None

        seen_external_ids.add(external_id)

        existing = (
            db.query(SanctionsListEntry)
            .filter_by(list_source="ofac_sdn", external_id=external_id)
            .first()
        )
        if existing:
            existing.full_name = full_name[:512]
            existing.aliases = ",".join(aliases)[:4096] if aliases else None
            existing.entity_type = entity_type
            existing.country_code = country_code
            existing.program = ",".join(programs)[:120] if programs else None
            existing.fetched_at = now
        else:
            db.add(SanctionsListEntry(
                list_source="ofac_sdn",
                external_id=external_id,
                full_name=full_name[:512],
                aliases=",".join(aliases)[:4096] if aliases else None,
                entity_type=entity_type,
                country_code=country_code,
                program=",".join(programs)[:120] if programs else None,
                fetched_at=now,
            ))
            inserted += 1

    # Delete rows that are no longer in the published list
    deleted = (
        db.query(SanctionsListEntry)
        .filter(
            SanctionsListEntry.list_source == "ofac_sdn",
            SanctionsListEntry.external_id.notin_(seen_external_ids),
        )
        .delete(synchronize_session=False)
    )

    db.commit()
    return {"inserted": inserted, "deleted": deleted}


def _sync_il_mof(db: Session) -> dict:
    """
    Stub for Israeli MOF / NBCTF sanctions list.
    The NBCTF publishes an Excel file; parsing it requires openpyxl.
    This is a documented placeholder — wire when the first IL-regulated
    financial product goes live.
    """
    log.info(
        "IL-MOF sanctions sync is a stub — "
        "wire openpyxl + %s when IL-regulated products go live",
        _IL_MOF_URL,
    )
    return {"inserted": 0, "deleted": 0}
