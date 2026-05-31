"""
Aurora LTS — Issue a Break-Glass JWT (Track 3, Tier-1.5)
=========================================================
Generates a long-lived JWT for emergency admin access. The token's
jti is registered in the `break_glass_tokens` table; the JWT itself
is signed with `JWT_SECRET` (HS256). The token is printed ONCE and
must be copied immediately to 1Password / Bitwarden + paper backup.

This token bypasses IAP enforcement on every `Depends(require_admin)`
endpoint (see app/middleware/auth_middleware.py). Treat it as the
root credential.

USAGE — runs against the production DB via cloud-sql-proxy.

  # Terminal A — start the Cloud SQL Auth Proxy:
  gcloud sql proxy aurora-pg --project=aurora-lts-prod

  # Terminal B — pull secrets + run:
  cd ~/Desktop/ASG-Middleware/server_files
  export JWT_SECRET=$(gcloud secrets versions access latest \\
      --secret=AURORA_JWT_SECRET --project=aurora-lts-prod)
  # Derive the DB password from AURORA_DATABASE_URL:
  export DB_PASSWORD=$(gcloud secrets versions access latest \\
      --secret=AURORA_DATABASE_URL --project=aurora-lts-prod \\
      | sed -n 's|.*://aurora_app:\\([^@]*\\)@.*|\\1|p')
  export DATABASE_URL="postgresql+psycopg://aurora_app:${DB_PASSWORD}@127.0.0.1:5432/aurora_prod"
  unset DB_PASSWORD
  ../venv/bin/python scripts/issue_break_glass_token.py --days=90 --notes="initial issue"

OUTPUT: the JWT is printed to stdout exactly once. Store it
immediately. The script does NOT log the JWT to any file or remote
sink. The DB stores only the jti — not the JWT itself.

ROTATION:
  Default validity = 90 days. Issue a new one before expiry,
  store the new one, then revoke the old via the admin API.
"""

from __future__ import annotations

import argparse
import datetime
import os
import sys
import uuid

# Ensure parent app/ package is importable when run from server_files/.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def main() -> None:
    parser = argparse.ArgumentParser(description="Issue an Aurora break-glass JWT")
    parser.add_argument(
        "--user-id", type=int, default=1,
        help="Admin user_id this token impersonates (default: 1, bootstrap admin)",
    )
    parser.add_argument(
        "--days", type=int, default=90,
        help="Validity in days (default: 90)",
    )
    parser.add_argument(
        "--notes", type=str, default="",
        help="Free-text notes stored alongside the jti (audit only, not in JWT)",
    )
    args = parser.parse_args()

    if args.days < 1 or args.days > 365:
        print(f"ERROR: --days must be 1..365 (got {args.days})", file=sys.stderr)
        sys.exit(2)

    jwt_secret = os.environ.get("JWT_SECRET", "").strip()
    if not jwt_secret:
        print(
            "ERROR: JWT_SECRET environment variable is required.\n"
            "Pull from Secret Manager:\n"
            "    export JWT_SECRET=$(gcloud secrets versions access latest "
            "--secret=AURORA_JWT_SECRET --project=aurora-lts-prod)",
            file=sys.stderr,
        )
        sys.exit(2)

    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        print(
            "ERROR: DATABASE_URL environment variable is required.\n"
            "Run a Cloud SQL Auth Proxy locally first and set DATABASE_URL to point at 127.0.0.1:5432.",
            file=sys.stderr,
        )
        sys.exit(2)

    # Lazy imports so the help text works without DB access.
    from jose import jwt
    from aurora_shared.database import SessionLocal, BreakGlassToken, User

    db = SessionLocal()
    try:
        target_user = db.query(User).filter(User.id == args.user_id).first()
        if target_user is None:
            print(f"ERROR: user_id={args.user_id} not found", file=sys.stderr)
            sys.exit(3)
        if target_user.role != "admin":
            print(
                f"ERROR: user_id={args.user_id} is not admin (role={target_user.role!r}). "
                "Break-glass tokens can only be issued for admin users.",
                file=sys.stderr,
            )
            sys.exit(3)

        # Find an issuer user. If JWT_ISSUED_BY env is set, use that;
        # otherwise default to the same user.
        issuer_id = int(os.environ.get("ISSUED_BY_USER_ID", str(args.user_id)))

        now = datetime.datetime.utcnow()
        expires_at = now + datetime.timedelta(days=args.days)
        jti = str(uuid.uuid4())

        # Insert the DB record FIRST so the jti is registered before
        # the JWT is valid. (require_admin checks the jti.)
        row = BreakGlassToken(
            jti=jti,
            issued_at=now,
            expires_at=expires_at,
            issued_by_user_id=issuer_id,
            issued_for_user_id=args.user_id,
            notes=args.notes or None,
        )
        db.add(row)
        db.commit()

        # Build the JWT claims.
        claims = {
            "sub": str(args.user_id),
            "role": "admin",
            "is_emergency_break_glass": True,
            "iat": int(now.timestamp()),
            "exp": int(expires_at.timestamp()),
            "iss": "aurora-break-glass",
            "jti": jti,
        }

        token = jwt.encode(claims, jwt_secret, algorithm="HS256")

        # Print exactly once. Stored nowhere else in our infrastructure.
        bar = "=" * 70
        print(bar)
        print("  BREAK-GLASS JWT ISSUED")
        print("  Copy NOW — this token will not be shown again")
        print(bar)
        print(f"  jti:         {jti}")
        print(f"  user_id:     {args.user_id} ({target_user.email})")
        print(f"  issued_at:   {now.isoformat()}Z")
        print(f"  expires_at:  {expires_at.isoformat()}Z  ({args.days} days)")
        print(f"  notes:       {args.notes!r}")
        print(bar)
        print()
        print("  JWT:")
        print()
        print(f"  {token}")
        print()
        print(bar)
        print("  STORE NOW:")
        print("    Primary: 1Password / Bitwarden secure note labelled")
        print("             'Aurora Break-glass JWT — DO NOT COPY ELSEWHERE'")
        print("    Backup:  printed paper in sealed envelope, signed across")
        print("             the seal, in fireproof safe")
        print()
        print("  This token bypasses IAP. Treat it as the root credential.")
        print("  Use only during a documented IAP / Workspace incident.")
        print("  Every use writes a CRITICAL ActionLog entry.")
        print(bar)

    finally:
        db.close()


if __name__ == "__main__":
    main()
