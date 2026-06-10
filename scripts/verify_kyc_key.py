#!/usr/bin/env python3
"""
Aurora LTS — KYC keyless-signing smoke-test (Workload Identity).
=================================================================
KYC signing is KEYLESS now: no exported SA key (the org policy
iam.disableServiceAccountKeyCreation forbids them). Signed URLs are produced
via the IAM signBlob API using the Cloud Run runtime SA's ambient credentials.

This verifies the runtime SA can actually do all three things finalize/init need:
  1. signBlob          — generate a v4 signed URL (needs roles/iam.serviceAccountTokenCreator on itself)
  2. objects.create    — write to the bucket (roles/storage.objectCreator)
  3. objects.get       — read it back to hash (roles/storage.objectViewer)

It complements the startup gate (backend_check.py), which can't assert IAM
bindings — only this live check can.

RUN IT AS THE RUNTIME SA (aurora-run@), not as yourself:
  • On Cloud Run: deploy/run it there and ambient creds ARE the runtime SA.
  • Locally via impersonation (you need tokenCreator on aurora-run@):
      export IMPERSONATE_SA="aurora-run@aurora-lts-prod.iam.gserviceaccount.com"
      python3 scripts/verify_kyc_key.py aurora-lts-prod-secure-storage
  • The script PRINTS the identity it resolved — confirm it's the runtime SA.

Exit code: 0 = sign + create + view all OK; 1 = a required capability is missing.
"""
import datetime
import os
import sys

GREEN, RED, YEL, DIM, RST = "\033[92m", "\033[91m", "\033[93m", "\033[2m", "\033[0m"


def resolve_credentials():
    """Ambient creds, optionally impersonated to IMPERSONATE_SA, refreshed."""
    import google.auth
    from google.auth.transport.requests import Request

    base, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    impersonate = (os.getenv("IMPERSONATE_SA") or "").strip()
    if impersonate:
        from google.auth import impersonated_credentials
        base = impersonated_credentials.Credentials(
            source_credentials=base,
            target_principal=impersonate,
            target_scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
    base.refresh(Request())
    sa_email = (
        os.getenv("GCS_SIGNING_SA_EMAIL", "").strip()
        or impersonate
        or getattr(base, "service_account_email", "")
        or "?"
    )
    return base, sa_email


def main() -> None:
    bucket_name = sys.argv[1] if len(sys.argv) > 1 else (os.getenv("GCS_BUCKET_KYC") or "").strip()
    if not bucket_name:
        print(f"{RED}✗ No bucket. Pass it as arg 1 or set GCS_BUCKET_KYC.{RST}")
        sys.exit(2)

    try:
        from google.cloud import storage
        from google.api_core import exceptions as gcs_exc
    except ImportError as e:
        print(f"{RED}✗ google-cloud-storage not installed in this env: {e}{RST}")
        sys.exit(2)

    creds, sa_email = resolve_credentials()

    print(f"\n{DIM}── Keyless KYC signing identity (must be the Cloud Run runtime SA) ──{RST}")
    print(f"  signing as      : {YEL}{sa_email}{RST}")
    print(f"  target bucket   : {YEL}{bucket_name}{RST}")
    print(f"  mode            : {'impersonated (local)' if os.getenv('IMPERSONATE_SA') else 'ambient (Workload Identity)'}\n")

    client = storage.Client(credentials=creds)
    bucket = client.bucket(bucket_name)
    test_key = f"_kyc_keycheck/smoketest-{datetime.datetime.utcnow().strftime('%Y%m%d%H%M%S')}.txt"
    blob = bucket.blob(test_key)

    sign_ok = creator_ok = viewer_ok = False

    # 1) signBlob — generate a v4 signed URL (the keyless signing dependency)
    try:
        blob.generate_signed_url(
            version="v4",
            expiration=datetime.timedelta(seconds=300),
            method="PUT",
            content_type="text/plain",
            service_account_email=sa_email,
            access_token=creds.token,
        )
        sign_ok = True
        print(f"{GREEN}✓ signBlob — generated a v4 signed URL (roles/iam.serviceAccountTokenCreator OK){RST}")
    except gcs_exc.Forbidden as e:
        print(f"{RED}✗ signBlob FORBIDDEN (403) — grant the runtime SA roles/iam.serviceAccountTokenCreator "
              f"on ITSELF: {e.message}{RST}")
    except Exception as e:  # noqa: BLE001
        print(f"{RED}✗ signBlob check failed ({type(e).__name__}): {e}{RST}")

    # 2) objectCreator — write
    try:
        blob.upload_from_string(b"aurora-kyc-keycheck", content_type="text/plain")
        creator_ok = True
        print(f"{GREEN}✓ objectCreator — wrote {test_key}{RST}")
    except gcs_exc.Forbidden as e:
        print(f"{RED}✗ objectCreator MISSING (403) — grant roles/storage.objectCreator: {e.message}{RST}")
    except Exception as e:  # noqa: BLE001
        print(f"{RED}✗ objectCreator check failed ({type(e).__name__}): {e}{RST}")

    # 3) objectViewer — read back (finalize downloads to hash)
    if creator_ok:
        try:
            data = blob.download_as_bytes()
            viewer_ok = data == b"aurora-kyc-keycheck"
            print(f"{GREEN}✓ objectViewer — read object back ({len(data)} bytes){RST}" if viewer_ok
                  else f"{RED}✗ objectViewer — read mismatch{RST}")
        except gcs_exc.Forbidden as e:
            print(f"{RED}✗ objectViewer MISSING (403) — grant roles/storage.objectViewer "
                  f"(objectCreator ALONE makes finalize 403): {e.message}{RST}")
        except Exception as e:  # noqa: BLE001
            print(f"{RED}✗ objectViewer check failed ({type(e).__name__}): {e}{RST}")

    # cleanup (best-effort — objects.delete is NOT required by the app)
    try:
        blob.delete()
        print(f"{DIM}  cleaned up test object{RST}")
    except Exception:  # noqa: BLE001
        print(f"{YEL}  note: could not delete test object (objects.delete not granted — fine; "
              f"remove {test_key} manually).{RST}")

    ok = sign_ok and creator_ok and viewer_ok
    print(f"\n{'%s✓ KEYLESS KYC READY — sign + create + view all work%s' % (GREEN, RST) if ok else '%s✗ NOT READY — fix the IAM above before flipping KYC_BACKEND=gcs%s' % (RED, RST)}\n")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
