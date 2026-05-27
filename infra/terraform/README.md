# Aurora LTS — Terraform IaC (P1-25)

This directory describes Aurora's GCP production infra as code. Today
the infra exists, having been created via `gcloud` CLI commands. This
foundation lets you bring those resources under Terraform management
without recreating them.

## Initial setup (one-time, before first `terraform plan`)

```bash
# 1. Make sure you have gcloud authenticated locally.
gcloud auth application-default login

# 2. Create the state bucket if it doesn't exist (idempotent).
gcloud storage buckets create gs://aurora-tf-state-prod \
  --location=me-west1 \
  --uniform-bucket-level-access \
  --public-access-prevention \
  --versioning || true

# 3. Init Terraform (downloads providers, configures backend).
cd infra/terraform
terraform init

# 4. Import the existing resources — see main.tf header comment for
#    the full import command list.
terraform import google_sql_database_instance.postgres \
  projects/aurora-lts-prod/instances/aurora-postgres-prod
# … (repeat for Cloud Run, GCS buckets, secrets)

# 5. terraform plan → expect minimal diffs (where state matches code).
#    Resolve diffs by editing the .tf files to match the existing
#    state, or accept the Terraform-normalised state by running apply.
```

## Day-to-day workflow

```bash
# Edit .tf files
vim main.tf

# See what changes
terraform plan

# Apply (writes to state bucket; locks acquired automatically)
terraform apply
```

## What is + isn't in here

In:
  - Cloud Run service (aurora-api) — env, scaling, image, network
  - Cloud SQL Postgres instance (aurora-postgres-prod) + backups
  - GCS buckets (vault + KYC) with retention policies
  - Secret Manager secret CONTAINERS (values are managed out-of-band)
  - State backend in GCS (versioned, ME-WEST1 for residency)

Out (intentionally — these live elsewhere or are P2 work):
  - Cloud Run service ACCOUNT and IAM (existing manual setup is fine)
  - VPC, default network (project-level resources, not app-specific)
  - DNS records (Firebase Hosting / GoDaddy — not GCP)
  - Marketing site (`aurora-website` is its own Firebase project)
  - The accountant-portal Tauri app (no GCP infra of its own)

## Rotating a secret

Terraform doesn't own secret VALUES, only the container. Rotation:

```bash
# Add a new version (the value).
gcloud secrets versions add aurora-pii-encryption-key \
  --data-file=<(python -m app.db.encrypted_types --gen-key | head -1)

# Cloud Run picks up the latest version automatically (the env binding
# uses `--latest` semantics in the existing Cloud Run YAML). No
# terraform apply needed.
```

## What if state drift breaks `plan`?

If `terraform plan` shows changes you didn't intend (someone made a
change in the GCP Console), either:
  - `terraform apply` to flip the resource back to the .tf-described state
  - Edit the .tf to match the console reality

Always commit the resulting `.tf` change so the codebase stays the
source of truth.
