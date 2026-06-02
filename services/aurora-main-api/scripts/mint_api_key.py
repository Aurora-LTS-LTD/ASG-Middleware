#!/usr/bin/env python3
"""
Aurora LTS — Mint API Key (P1-22)
===================================
One-shot CLI that creates a new api_keys row + prints the plaintext
key ONCE (it cannot be recovered after this script exits).

USAGE:
    cd server_files
    python -m scripts.mint_api_key --name make-webhook --scope make-webhook

OUTPUT:
    [ok] Created api_key id=N name=make-webhook scope=make-webhook
    plaintext: aurora-key_<48-random-url-safe-chars>

    Hand this plaintext to the caller via Secret Manager / 1Password.
    The DB only stores SHA-256(plaintext); we cannot retrieve it.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import secrets
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Mint an Aurora API key")
    parser.add_argument(
        "--name",
        required=True,
        help="Human-readable label (must be unique). e.g. 'make-webhook'.",
    )
    parser.add_argument(
        "--scope",
        default=None,
        help="Optional scope. e.g. 'make-webhook'. None = global scope.",
    )
    args = parser.parse_args()

    # Plaintext key: `aurora-key_` + 48 url-safe chars (~36 bytes random).
    # The prefix is so support can recognise an Aurora key in logs at a
    # glance without the value being leaked (still useless without the
    # rest).
    plaintext = f"aurora-key_{secrets.token_urlsafe(36)}"
    key_hash = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()

    # Insert.
    from aurora_shared.database.connection import SessionLocal
    from aurora_shared.database.models import ApiKey

    db = SessionLocal()
    try:
        row = ApiKey(
            name=args.name,
            key_hash=key_hash,
            scope=args.scope,
            created_at=datetime.datetime.utcnow(),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
    except Exception as exc:
        db.rollback()
        print(f"[err] failed to insert: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()

    print(
        f"[ok] Created api_key id={row.id} name={row.name} scope={row.scope}"
    )
    print()
    print(f"plaintext: {plaintext}")
    print()
    print(
        "Hand this plaintext to the caller via Secret Manager / 1Password.\n"
        "The DB only stores SHA-256(plaintext); we cannot retrieve it later."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
