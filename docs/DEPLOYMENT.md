# Aurora LTS — Production Deployment Runbook

> **Audience:** the founder (Ibrahim) executing the first GCP deployment.
> **Pre-req knowledge:** comfortable opening a terminal and pasting `gcloud` commands.
> **Time budget:** ~3 hours including DNS propagation. Active hands-on work: ~1.5 hours.

---

## Phase 0 — Decisions to Lock In Before You Run a Single Command

Fill these in. Every command below uses one of them.

| Variable | Recommendation | Your value |
|---|---|---|
| `PROJECT_ID` | `aurora-lts-prod` | _____________ |
| `REGION` | `me-west1` (Tel Aviv) | _____________ |
| Cloud SQL availability | `ZONAL` for first month, upgrade to `REGIONAL` before public beta | _____________ |
| `DOMAIN` | `aurora-ltd.co.il` | _____________ |
| Telegram bot scope | **Stays on Alienware for this deploy** (decision pending) | _____________ |

Set them as shell variables for the rest of the session:

```bash
export PROJECT_ID=aurora-lts-prod
export REGION=me-west1
export DOMAIN=aurora-ltd.co.il
```

---

## Phase 1 — One-time GCP Setup (~15 min)

### 1.1 Create the project + enable APIs

```bash
gcloud projects create $PROJECT_ID --name="Aurora LTS Production"
gcloud config set project $PROJECT_ID

# Link the billing account (replace with your billing-account ID)
gcloud beta billing projects link $PROJECT_ID \
    --billing-account=XXXXXX-XXXXXX-XXXXXX

# Enable every service we use in Phases 2-3
gcloud services enable \
    sqladmin.googleapis.com \
    run.googleapis.com \
    artifactregistry.googleapis.com \
    secretmanager.googleapis.com \
    cloudbuild.googleapis.com \
    domains.googleapis.com
```

### 1.2 Create the Artifact Registry repo

```bash
gcloud artifacts repositories create aurora \
    --repository-format=docker \
    --location=$REGION \
    --description="Aurora LTS container images"
```

### 1.3 Create the Cloud Run service account (least privilege)

```bash
gcloud iam service-accounts create aurora-run \
    --display-name="Aurora LTS Cloud Run runtime"

# Grant only what's needed to RUN the service
gcloud projects add-iam-policy-binding $PROJECT_ID \
    --member=serviceAccount:aurora-run@${PROJECT_ID}.iam.gserviceaccount.com \
    --role=roles/cloudsql.client

gcloud projects add-iam-policy-binding $PROJECT_ID \
    --member=serviceAccount:aurora-run@${PROJECT_ID}.iam.gserviceaccount.com \
    --role=roles/secretmanager.secretAccessor
```

---

## Phase 2 — Cloud SQL Postgres (~30 min, mostly waiting)

### 2.1 Provision the instance

```bash
# Recommended for first month: ZONAL (₪200/mo). Upgrade to REGIONAL
# (₪450/mo) before the first paying tenant onboards.
gcloud sql instances create aurora-pg \
    --database-version=POSTGRES_15 \
    --region=$REGION \
    --tier=db-custom-2-7680 \
    --availability-type=ZONAL \
    --storage-size=20GB --storage-type=SSD --storage-auto-increase \
    --backup-start-time=03:00 \
    --backup-location=$REGION
```

The command takes ~7 minutes. Go make tea.

### 2.2 Create the application database + low-privilege user

```bash
gcloud sql databases create aurora_prod --instance=aurora-pg

# Generate a strong password — you'll never see this in plaintext again.
DB_PASSWORD=$(openssl rand -hex 24)
gcloud sql users create aurora_app --instance=aurora-pg --password="$DB_PASSWORD"
```

**Stash this password in Secret Manager IMMEDIATELY** (next step) — once you close this terminal, the password is gone.

### 2.3 Create every Secret in Secret Manager

The Cloud SQL connection string follows this format (note the Unix socket path):

```
postgresql+psycopg://aurora_app:<password>@/aurora_prod?host=/cloudsql/<PROJECT>:<REGION>:aurora-pg
```

```bash
# 1. The DB URL — assembled from the password we just generated
DATABASE_URL="postgresql+psycopg://aurora_app:${DB_PASSWORD}@/aurora_prod?host=/cloudsql/${PROJECT_ID}:${REGION}:aurora-pg"

# 2. Strong JWT secret (32 bytes, hex)
JWT_SECRET=$(openssl rand -hex 32)

# 3. Bootstrap admin password (12+ chars, you'll use this once for first login)
ADMIN_INITIAL_PASSWORD=$(openssl rand -base64 18 | tr -d '/+' | head -c 16)

# 4. Create + populate every secret
create_secret() {
    local name=$1 value=$2
    gcloud secrets create "$name" --replication-policy=automatic 2>/dev/null || true
    echo -n "$value" | gcloud secrets versions add "$name" --data-file=-
    gcloud secrets add-iam-policy-binding "$name" \
        --member=serviceAccount:aurora-run@${PROJECT_ID}.iam.gserviceaccount.com \
        --role=roles/secretmanager.secretAccessor
}

create_secret AURORA_DATABASE_URL              "$DATABASE_URL"
create_secret AURORA_JWT_SECRET                "$JWT_SECRET"
create_secret AURORA_ADMIN_EMAIL               "ibrahim@aurora-ltd.co.il"   # adjust
create_secret AURORA_ADMIN_INITIAL_PASSWORD    "$ADMIN_INITIAL_PASSWORD"

# WhatsApp — paste from your Meta Developer Dashboard
create_secret AURORA_WHATSAPP_VERIFY_TOKEN     "<paste from Meta>"
create_secret AURORA_WHATSAPP_ACCESS_TOKEN     "<paste from Meta>"
create_secret AURORA_WHATSAPP_PHONE_NUMBER_ID  "<paste from Meta>"
create_secret AURORA_WHATSAPP_APP_SECRET       "<paste from Meta>"
create_secret AURORA_WHATSAPP_WEBHOOK_SECRET   "$(openssl rand -hex 24)"
create_secret AURORA_WHATSAPP_BOT_PHONE_E164   "<your bot's E.164 number, e.g. +9725...>"

# IMPORTANT — write the admin password down NOW. You'll use it on first login.
echo "FIRST-LOGIN admin password: $ADMIN_INITIAL_PASSWORD"
```

> **Security:** the only places these secrets exist are (a) Secret Manager, (b) the running container (as env vars). Never commit them to git, never put them in `.env` files that ship in the image.

---

## Phase 3 — Build + Push the Image (~10 min)

### 3.1 Build locally

```bash
cd ~/Desktop/ASG-Middleware
docker build -t aurora-api:v1.0.0 .
```

### 3.2 Tag + push to Artifact Registry

```bash
gcloud auth configure-docker ${REGION}-docker.pkg.dev

docker tag aurora-api:v1.0.0 \
    ${REGION}-docker.pkg.dev/${PROJECT_ID}/aurora/api:v1.0.0
docker tag aurora-api:v1.0.0 \
    ${REGION}-docker.pkg.dev/${PROJECT_ID}/aurora/api:latest

docker push ${REGION}-docker.pkg.dev/${PROJECT_ID}/aurora/api:v1.0.0
docker push ${REGION}-docker.pkg.dev/${PROJECT_ID}/aurora/api:latest
```

**Alternative — Cloud Build (slower first time, but reproducible):**

```bash
gcloud builds submit --config cloudbuild.yaml \
    --substitutions=_PROJECT=$PROJECT_ID,_REGION=$REGION,_VERSION=v1.0.0
```

---

## Phase 4 — Deploy the Cloud Run Service (~5 min)

```bash
gcloud run deploy aurora-api \
    --image=${REGION}-docker.pkg.dev/${PROJECT_ID}/aurora/api:v1.0.0 \
    --region=$REGION \
    --platform=managed \
    --service-account=aurora-run@${PROJECT_ID}.iam.gserviceaccount.com \
    --add-cloudsql-instances=${PROJECT_ID}:${REGION}:aurora-pg \
    --min-instances=1 \
    --max-instances=10 \
    --cpu=2 --memory=1Gi \
    --concurrency=80 \
    --timeout=120s \
    --allow-unauthenticated \
    --set-env-vars="\
AURORA_RUNTIME=cloud_run,\
SKIP_SEED_ADMIN=1,\
COMPANY_NAME_HE=אורורה אל.טי.אס. בע\"מ,\
COMPANY_NAME_EN=AURORA LTS LTD,\
PUBLIC_DOMAIN=$DOMAIN,\
SERVER_BASE_URL=https://$DOMAIN,\
ONBOARDING_PUBLIC_URL=https://$DOMAIN/onboarding,\
KYC_BACKEND=stub,PAYPLUS_BACKEND=stub,OTP_BACKEND=stub,\
ONBOARDING_TRIAL_DAYS=14,KYC_MANUAL_REVIEW_FIRST_N=50,\
INVITATION_TTL_HOURS=72,WHATSAPP_API_VERSION=v20.0,\
JWT_EXPIRATION_HOURS=24" \
    --set-secrets="\
DATABASE_URL=AURORA_DATABASE_URL:latest,\
JWT_SECRET=AURORA_JWT_SECRET:latest,\
WHATSAPP_VERIFY_TOKEN=AURORA_WHATSAPP_VERIFY_TOKEN:latest,\
WHATSAPP_ACCESS_TOKEN=AURORA_WHATSAPP_ACCESS_TOKEN:latest,\
WHATSAPP_PHONE_NUMBER_ID=AURORA_WHATSAPP_PHONE_NUMBER_ID:latest,\
WHATSAPP_APP_SECRET=AURORA_WHATSAPP_APP_SECRET:latest,\
WHATSAPP_WEBHOOK_SECRET=AURORA_WHATSAPP_WEBHOOK_SECRET:latest,\
WHATSAPP_BOT_PHONE_E164=AURORA_WHATSAPP_BOT_PHONE_E164:latest"
```

The first deploy logs every Phase 4 / 5 / 6 / 6b migration as the container boots — schema creation runs automatically. Total boot time on first deploy: ~30 seconds. After that, ~5 seconds per deploy.

When it succeeds, gcloud prints a temporary URL like `https://aurora-api-xxxxx-zf.a.run.app`. **Smoke-test it before doing anything else:**

```bash
SERVICE_URL=$(gcloud run services describe aurora-api --region=$REGION --format='value(status.url)')
curl -fsS "${SERVICE_URL}/api/v1/onboarding/health"
```

Expected: `{"ok":true,"module":"aurora-onboarding","trial_days":14,...}`

---

## Phase 5 — Bootstrap the First Admin User

```bash
gcloud run jobs create aurora-bootstrap-admin \
    --image=${REGION}-docker.pkg.dev/${PROJECT_ID}/aurora/api:v1.0.0 \
    --region=$REGION \
    --service-account=aurora-run@${PROJECT_ID}.iam.gserviceaccount.com \
    --add-cloudsql-instances=${PROJECT_ID}:${REGION}:aurora-pg \
    --set-secrets="\
DATABASE_URL=AURORA_DATABASE_URL:latest,\
ADMIN_EMAIL=AURORA_ADMIN_EMAIL:latest,\
ADMIN_INITIAL_PASSWORD=AURORA_ADMIN_INITIAL_PASSWORD:latest" \
    --command=python --args=scripts/bootstrap_admin.py

gcloud run jobs execute aurora-bootstrap-admin --region=$REGION --wait
```

Then log in at `${SERVICE_URL}/dashboard` with `ibrahim@aurora-ltd.co.il` + the `ADMIN_INITIAL_PASSWORD` you stashed earlier. **Rotate the password immediately on first login.**

---

## Phase 6 — Custom Domain Mapping (~30 min, mostly DNS propagation)

### 6.1 Verify domain ownership in Search Console (one-time, manual)

1. Open https://search.google.com/search-console
2. Add property → `aurora-ltd.co.il` (use the "Domain" type, not "URL prefix")
3. Add the TXT record Google provides at your registrar (Israeli registrars like NSi / Domain The Net / israeli-domains.co.il)
4. Wait for verification (typically <5 min)

### 6.2 Create the domain mapping

```bash
gcloud beta run domain-mappings create \
    --service=aurora-api \
    --domain=$DOMAIN \
    --region=$REGION

gcloud beta run domain-mappings create \
    --service=aurora-api \
    --domain=www.$DOMAIN \
    --region=$REGION
```

The command prints DNS records (typically 4 × A or 1 × CNAME). Copy them and add them at your registrar. **Wait 5-30 minutes** for DNS propagation. You can poll:

```bash
dig +short $DOMAIN
# Should return Google's IPs once propagation is done.
```

### 6.3 Verify HTTPS works

```bash
curl -fsS https://$DOMAIN/api/v1/onboarding/health
```

Expected: same JSON as the `${SERVICE_URL}` test in Phase 4. If you get a TLS error, the cert is still being issued — wait ~10 minutes and retry.

---

## Phase 7 — Cut Over the Meta Webhook (CHOOSE YOUR MOMENT)

> **The cut-over moment** — when you update Meta Dashboard, the Alienware stops receiving messages and Cloud Run starts. Do this at a low-traffic time (recommendation: 02:00 IL time).

1. Open Meta Developer Dashboard → your WhatsApp app → **Configuration → Webhook**
2. Click **Edit**
3. Set:
   - **Callback URL:** `https://aurora-ltd.co.il/webhook/whatsapp/<value-of-AURORA_WHATSAPP_WEBHOOK_SECRET>`
   - **Verify Token:** value of `AURORA_WHATSAPP_VERIFY_TOKEN`
4. Click **Verify and Save**
5. If Meta returns "Webhook verified" — you're live. If not, check the Cloud Run logs for the verify GET hit and inspect the verify_token mismatch.
6. Re-subscribe to fields: `messages`, `message_deliveries`, `message_reads`

Test by messaging the bot's WhatsApp number with `/start` from a phone the bot hasn't seen before. The Aurora ONBOARDING FSM should reply within ~2 seconds.

---

## Phase 8 — Post-deploy Verification Checklist

- [ ] `curl https://aurora-ltd.co.il/api/v1/onboarding/health` returns 200
- [ ] `https://aurora-ltd.co.il/onboarding` loads the Alpine.js wizard
- [ ] `https://aurora-ltd.co.il/dashboard` loads the login screen with Aurora branding
- [ ] Admin login works with the bootstrap credentials
- [ ] WhatsApp `/start` from a phone enters the Aurora ONBOARDING FSM
- [ ] Cloud Run logs show no startup errors and all 4 migrations log "Done"
- [ ] `gcloud sql connect aurora-pg --user=aurora_app` shows ~17 tables under `\dt`
- [ ] First-login password rotated

---

## Rollback

If anything goes wrong, revert traffic to the previous revision:

```bash
gcloud run services update-traffic aurora-api \
    --region=$REGION \
    --to-revisions=aurora-api-00001-abc=100
```

(Replace `00001-abc` with the prior revision listed by `gcloud run revisions list`.)

To roll the WhatsApp webhook back to the Alienware, just re-paste the old URL into Meta Dashboard.

---

## Operational Notes

- **Logs:** `gcloud run services logs read aurora-api --region=$REGION --limit=200`
- **Database access:** `gcloud sql connect aurora-pg --user=aurora_app` (then enter the password from Secret Manager)
- **Re-deploy a new image version:** repeat Phase 3 with a new tag, then run the same Phase 4 deploy command with the new tag
- **Add a secret post-deploy:** `gcloud run services update aurora-api --region=$REGION --set-secrets=NEW_KEY=NEW_SECRET:latest` (preserves existing secrets)
- **Cost monitoring:** set a budget alert in Cloud Billing → expected ~₪465-605/month at the recommended sizing

---

## When to Move to Sprint 2

Sprint 2 (per Part II of the plan) ships:
- GCS migration of PDFs and KYC uploads
- Document AI pipeline for receipt OCR
- Cloud Tasks refactor of the three long-poll asyncio loops (which currently rely on `--min-instances=1`)

Once Sprint 2 lands, you can safely lower `--min-instances=0` and save ~₪70/mo. Until then, leave `--min-instances=1` so the three asyncio tasks (`whatsapp_resend_loop`, `allocation_retry_loop`, `morning_digest_loop`) keep running.
