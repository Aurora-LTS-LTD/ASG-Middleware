"""
Aurora LTS — One-shot Production Admin Bootstrap
====================================================
Creates the FIRST admin user on a freshly-deployed Cloud Run instance.
Replaces the dev-only seed at app/main.py (which is now skipped when
SKIP_SEED_ADMIN=1 is set in production).

WHY A SEPARATE SCRIPT:
  - The hard-coded "admin@asg.com / admin123" baked into main.py was
    fine for local dev but a security liability in production.
  - On Cloud Run we run this once via a Cloud Run Job, with the
    initial password sourced from Secret Manager. The script logs
    success but never prints the password.

INPUTS (all from environment — typically Secret Manager):
  DATABASE_URL              required — Postgres connection string
  ADMIN_EMAIL               required — e.g. "ibrahim@aurora-ltd.co.il"
  ADMIN_FULL_NAME           optional — defaults to email local-part
  ADMIN_INITIAL_PASSWORD    required — at least 12 chars
  ADMIN_LANGUAGE_PREF       optional — "he" | "ar" | "en", default "he"

BEHAVIOR:
  - If an admin with the given email already exists → no-op (returns 0)
  - If a different admin already exists → still creates this one
    (Aurora supports multiple admins)
  - If no admin exists → creates the user and prints the user_id
    (NEVER the password)

EXIT CODES:
  0  success or already-exists no-op
  2  missing required env vars
  3  password validation failed
  4  database error

USAGE — one-shot via Cloud Run Job:
  gcloud run jobs create aurora-bootstrap-admin \\
      --image=me-west1-docker.pkg.dev/<PROJECT>/aurora/api:vX.Y.Z \\
      --region=me-west1 \\
      --service-account=aurora-run@<PROJECT>.iam.gserviceaccount.com \\
      --add-cloudsql-instances=<PROJECT>:me-west1:aurora-pg \\
      --set-secrets=DATABASE_URL=AURORA_DATABASE_URL:latest,\\
ADMIN_EMAIL=AURORA_ADMIN_EMAIL:latest,\\
ADMIN_INITIAL_PASSWORD=AURORA_ADMIN_INITIAL_PASSWORD:latest \\
      --command=python --args=scripts/bootstrap_admin.py
  gcloud run jobs execute aurora-bootstrap-admin --region=me-west1

USAGE — local (against the dev SQLite):
  cd server_files
  ADMIN_EMAIL=ibrahim@aurora-ltd.co.il \\
  ADMIN_INITIAL_PASSWORD='<strong-12+-char-pass>' \\
  python scripts/bootstrap_admin.py
"""

import os
import sys


def _err(msg: str, code: int) -> int:
    sys.stderr.write(f"[BOOTSTRAP] ERROR: {msg}\n")
    return code


def main() -> int:
    # Make app.* importable when run as `python scripts/bootstrap_admin.py`
    # from inside server_files/.
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    email = (os.getenv("ADMIN_EMAIL") or "").strip().lower()
    pw = os.getenv("ADMIN_INITIAL_PASSWORD") or ""
    full_name = (os.getenv("ADMIN_FULL_NAME") or "").strip()
    lang = (os.getenv("ADMIN_LANGUAGE_PREF") or "he").strip().lower()

    if not email or "@" not in email:
        return _err("ADMIN_EMAIL is required (and must be a valid email)", 2)
    if not pw:
        return _err("ADMIN_INITIAL_PASSWORD is required", 2)
    if len(pw) < 12:
        return _err("ADMIN_INITIAL_PASSWORD must be at least 12 characters", 3)
    if lang not in ("he", "ar", "en"):
        sys.stderr.write(
            f"[BOOTSTRAP] WARN: unsupported lang {lang!r}, falling back to 'he'\n"
        )
        lang = "he"

    if not full_name:
        full_name = email.split("@", 1)[0].replace(".", " ").title()

    # Late imports — must come AFTER sys.path is set.
    try:
        from app.database import SessionLocal, User, create_tables
        from app.services.auth_service import hash_password
    except Exception as e:
        return _err(f"Import failed: {e}", 4)

    # Make sure the schema exists before we try to query it. Idempotent.
    try:
        create_tables()
    except Exception as e:
        return _err(f"create_tables() failed: {e}", 4)

    db = SessionLocal()
    try:
        # Idempotency: if THIS email already exists, treat as success.
        existing = db.query(User).filter(User.email == email).first()
        if existing:
            print(
                f"[BOOTSTRAP] User {email!r} already exists "
                f"(id={existing.id}, role={existing.role}). No-op."
            )
            return 0

        admin = User(
            email=email,
            password_hash=hash_password(pw),
            full_name=full_name,
            role="admin",
            is_active=True,
            language_pref=lang,
            onboarding_status="active",  # admins skip onboarding
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)

        print(
            f"[BOOTSTRAP] ✅ Admin created: id={admin.id} email={admin.email!r} "
            f"full_name={admin.full_name!r}"
        )
        print("[BOOTSTRAP] Initial password was issued via Secret Manager. "
              "Rotate it after first login.")
        return 0
    except Exception as e:
        db.rollback()
        return _err(f"DB error: {e}", 4)
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
