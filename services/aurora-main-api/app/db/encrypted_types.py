"""
Aurora LTS — Column-Level PII Encryption (P1-23)
==================================================
A SQLAlchemy TypeDecorator that transparently encrypts on write and
decrypts on read, backed by `cryptography.fernet.Fernet` (AES-128-CBC
+ HMAC-SHA256, authenticated). Used for the highest-sensitivity PII
fields — Israeli tax IDs (ת.ז / ע.מ / ח.פ), phone numbers, and any
other column where a DB dump shouldn't expose plaintext.

USAGE (on new columns going forward):

    from app.db.encrypted_types import EncryptedString

    class Customer(Base):
        __tablename__ = "customers"
        tax_id = Column(EncryptedString(120), nullable=True)
        phone  = Column(EncryptedString(60),  nullable=True)

The TypeDecorator wraps a regular VARCHAR. The underlying storage
column needs to be wider than the plaintext because Fernet tokens are
URL-safe-base64 and ~1.5× plaintext size + ~57 bytes overhead.

KEY MANAGEMENT:

    AURORA_PII_ENCRYPTION_KEY env var (Fernet key — 32 url-safe bytes).
    Generate:
        python -m app.db.encrypted_types --gen-key

    Production: stored in Secret Manager (gcloud secrets create
    aurora-pii-key --data-file=-) and mounted as the env var on
    Cloud Run.

KEY ROTATION:

    Set AURORA_PII_ENCRYPTION_KEY=new-key,old-key (comma-separated).
    The first key is used for new writes; all keys are tried on read.
    Run a re-encryption migration to flip all existing rows to the
    new key, then drop the old one from the env.

EXISTING COLUMN MIGRATION:

    Converting an existing plaintext column to EncryptedString is
    NOT automatic — it needs a one-shot batch migration that:
      1. Reads every row's plaintext value.
      2. Encrypts it.
      3. Writes the ciphertext back to a temporary _enc column.
      4. ALTER TABLE drops plaintext, renames _enc → plaintext name.
    Do this carefully + with backups. The encryption infrastructure
    is shipped first; per-column conversion is incremental work.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from sqlalchemy.types import String, TypeDecorator

log = logging.getLogger(__name__)


def _load_fernet_keys():
    """Lazy: only import cryptography at first use, only crash if needed."""
    from cryptography.fernet import Fernet, InvalidToken, MultiFernet

    raw = (os.getenv("AURORA_PII_ENCRYPTION_KEY") or "").strip()
    if not raw:
        # Dev-mode fallback: generate an in-memory ephemeral key so the
        # app doesn't crash. Decryption of existing ciphertext will
        # fail, which is the correct dev signal. WARN loudly.
        log.warning(
            "[encrypted_types] AURORA_PII_ENCRYPTION_KEY not set — "
            "using ephemeral in-memory key. Existing ciphertext will "
            "FAIL to decrypt. Set the env var in production."
        )
        return MultiFernet([Fernet(Fernet.generate_key())]), Fernet, InvalidToken

    parts = [p.strip() for p in raw.split(",") if p.strip()]
    fernets = []
    for p in parts:
        try:
            fernets.append(Fernet(p.encode("ascii")))
        except Exception as exc:
            raise RuntimeError(
                f"AURORA_PII_ENCRYPTION_KEY contains an invalid Fernet key: {exc}"
            ) from exc
    return MultiFernet(fernets), Fernet, InvalidToken


_cached_multifernet = None
_cached_invalidtoken = None


def _get_multifernet():
    global _cached_multifernet, _cached_invalidtoken
    if _cached_multifernet is None:
        _cached_multifernet, _, _cached_invalidtoken = _load_fernet_keys()
    return _cached_multifernet


class EncryptedString(TypeDecorator):
    """
    A VARCHAR column that's transparently encrypted with Fernet.
    On Python-side: str <-> str (plaintext).
    On SQL-side:  str (Fernet token).

    Pass `length` as the maximum CIPHERTEXT size for the underlying
    VARCHAR. Allocate ~1.6× plaintext + 60 bytes to leave headroom.
    """

    impl = String
    cache_ok = True

    def __init__(self, length: int = 255, *args, **kwargs):
        # Internal column is VARCHAR(length) holding the Fernet token.
        super().__init__(*args, **kwargs)
        self.length = length

    def load_dialect_impl(self, dialect):
        return dialect.type_descriptor(String(self.length))

    def process_bind_param(self, value: Optional[str], dialect) -> Optional[str]:
        if value is None:
            return None
        token = _get_multifernet().encrypt(str(value).encode("utf-8"))
        return token.decode("ascii")

    def process_result_value(self, value: Optional[str], dialect) -> Optional[str]:
        if value is None:
            return None
        try:
            plaintext = _get_multifernet().decrypt(value.encode("ascii"))
        except Exception as exc:
            # Either the key changed and the row is stale, or the
            # column is already plaintext from before the migration.
            # Return the raw value rather than crashing the read —
            # caller can detect/fix.
            log.warning(
                "[encrypted_types] decrypt failed (len=%d): %s — returning raw value",
                len(value), exc,
            )
            return value
        return plaintext.decode("utf-8")


def _generate_key_cli() -> int:
    """python -m app.db.encrypted_types --gen-key → prints a fresh Fernet key."""
    from cryptography.fernet import Fernet
    print(Fernet.generate_key().decode("ascii"))
    print(
        "\nAdd this to .env (or your Secret Manager secret) as:\n"
        "  AURORA_PII_ENCRYPTION_KEY=<the key above>\n"
    )
    return 0


if __name__ == "__main__":
    import sys
    if "--gen-key" in sys.argv:
        sys.exit(_generate_key_cli())
    print("usage: python -m app.db.encrypted_types --gen-key", file=sys.stderr)
    sys.exit(2)


__all__ = ["EncryptedString"]
