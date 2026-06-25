"""
Aurora LTS — Temporary Admin Bootstrap (forced first-login rotation)
=====================================================================
Creates (or re-arms) an admin whose password is a TEMPORARY/bootstrap
credential that MUST be rotated on first login. The user is created with
`must_change_password=True`, so the backend refuses device enrolment / native
sessions until the password is changed via POST /api/v1/auth/change-password.

WHY A SEPARATE SCRIPT (not a flag on bootstrap_admin.py):
  bootstrap_admin.py enforces a 12-char minimum on a *permanent* password and
  must stay that strong. This script intentionally allows a SHORT temp password
  (e.g. "123456") — which is only safe BECAUSE `must_change_password=True`
  makes it single-use. Keeping the two paths in separate scripts prevents a
  length-rule footgun.

INPUTS (all from environment — temp password supplied at run time, never
committed and never stored in Secret Manager):
  DATABASE_URL          required — Postgres connection string
  ADMIN_EMAIL           required — e.g. "admin@aurora-ltd.co.il"
  ADMIN_TEMP_PASSWORD   required — the one-time bootstrap password (min 6)
  ADMIN_FULL_NAME       optional — defaults to email local-part
  ADMIN_LANGUAGE_PREF   optional — "he" | "ar" | "en", default "he"
  ADMIN_FORCE_RESET     optional — "1" to re-arm an EXISTING admin: rehash to
                                   the temp password and set must_change_password
                                   =True again (use to recover a locked-out admin)

BEHAVIOR:
  - Email does not exist → create admin with must_change_password=True.
  - Email exists, ADMIN_FORCE_RESET=1 → rehash to temp + must_change_password=True.
  - Email exists, no force-reset → no-op (returns 0); never silently overwrites.
  - NEVER prints the password.

EXIT CODES:
  0  success or already-exists no-op
  2  missing required env vars
  3  password validation failed
  4  database error

USAGE — one-shot via Cloud Run Job (temp password passed at execute time):
  gcloud run jobs create aurora-bootstrap-temp-admin \\
      --image=me-west1-docker.pkg.dev/<PROJECT>/aurora/api:vX.Y.Z \\
      --region=me-west1 \\
      --service-account=aurora-run@<PROJECT>.iam.gserviceaccount.com \\
      --add-cloudsql-instances=<PROJECT>:me-west1:aurora-pg \\
      --set-secrets=DATABASE_URL=AURORA_DATABASE_URL:latest \\
      --command=python --args=scripts/bootstrap_temp_admin.py
  gcloud run jobs execute aurora-bootstrap-temp-admin --region=me-west1 \\
      --update-env-vars=ADMIN_EMAIL=admin@aurora-ltd.co.il,ADMIN_TEMP_PASSWORD=123456,ADMIN_FULL_NAME=Aurora Admin,ADMIN_LANGUAGE_PREF=he

USAGE — local (against the dev DB):
  ADMIN_EMAIL=admin@aurora-ltd.co.il ADMIN_TEMP_PASSWORD=123456 \\
  python scripts/bootstrap_temp_admin.py
"""

import os
import sys

# Minimum length for the TEMP password only. Permanent passwords are still
# governed by the 12-char rule in /auth/change-password + bootstrap_admin.py.
MIN_TEMP_PASSWORD_LENGTH = 6


def _err(msg: str, code: int) -> int:
    sys.stderr.write(f"[TEMP-BOOTSTRAP] ERROR: {msg}\n")
    return code


def main() -> int:
    # Make app.* / aurora_shared importable when run as a script.
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    email = (os.getenv("ADMIN_EMAIL") or "").strip().lower()
    pw = os.getenv("ADMIN_TEMP_PASSWORD") or ""
    full_name = (os.getenv("ADMIN_FULL_NAME") or "").strip()
    lang = (os.getenv("ADMIN_LANGUAGE_PREF") or "he").strip().lower()
    force_reset = (os.getenv("ADMIN_FORCE_RESET") or "").strip() in ("1", "true", "yes")

    if not email or "@" not in email:
        return _err("ADMIN_EMAIL is required (and must be a valid email)", 2)
    if not pw:
        return _err("ADMIN_TEMP_PASSWORD is required", 2)
    if len(pw) < MIN_TEMP_PASSWORD_LENGTH:
        return _err(
            f"ADMIN_TEMP_PASSWORD must be at least {MIN_TEMP_PASSWORD_LENGTH} characters",
            3,
        )
    if lang not in ("he", "ar", "en"):
        sys.stderr.write(
            f"[TEMP-BOOTSTRAP] WARN: unsupported lang {lang!r}, falling back to 'he'\n"
        )
        lang = "he"

    if not full_name:
        full_name = email.split("@", 1)[0].replace(".", " ").title()

    try:
        from aurora_shared.database import SessionLocal, User, create_tables
        from aurora_shared.services.auth_service import hash_password
    except Exception as e:
        return _err(f"Import failed: {e}", 4)

    # Schema must already include `must_change_password` (migration 0009).
    # create_tables() is idempotent but does NOT add columns to existing
    # tables — so the Alembic upgrade must have run first in production.
    try:
        create_tables()
    except Exception as e:
        return _err(f"create_tables() failed: {e}", 4)

    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.email == email).first()
        if existing:
            if force_reset:
                existing.password_hash = hash_password(pw)
                existing.must_change_password = True
                if existing.role != "admin":
                    sys.stderr.write(
                        f"[TEMP-BOOTSTRAP] WARN: {email!r} has role "
                        f"{existing.role!r}; leaving role unchanged.\n"
                    )
                db.commit()
                print(
                    f"[TEMP-BOOTSTRAP] ♻️  Re-armed forced reset for existing user "
                    f"id={existing.id} email={existing.email!r} "
                    f"(must_change_password=True)."
                )
                return 0
            print(
                f"[TEMP-BOOTSTRAP] User {email!r} already exists "
                f"(id={existing.id}, role={existing.role}). No-op "
                f"(set ADMIN_FORCE_RESET=1 to re-arm)."
            )
            return 0

        admin = User(
            email=email,
            password_hash=hash_password(pw),
            full_name=full_name,
            role="admin",
            is_active=True,
            language_pref=lang,
            onboarding_status="active",   # admins skip onboarding
            must_change_password=True,     # forces rotation on first login
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)

        print(
            f"[TEMP-BOOTSTRAP] ✅ Temp admin created: id={admin.id} "
            f"email={admin.email!r} full_name={admin.full_name!r}"
        )
        print(
            "[TEMP-BOOTSTRAP] Temporary password issued via run-time env. "
            "must_change_password=True — it is invalid after first login + rotation."
        )
        return 0
    except Exception as e:
        db.rollback()
        return _err(f"DB error: {e}", 4)
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
