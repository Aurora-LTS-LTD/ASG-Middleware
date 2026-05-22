# Break-Glass Emergency Access — Aurora LTS

**Track 3 / Tier-1.5 panic key.** This is the founder's last-resort
admin path when IAP, OAuth, or Google Workspace is broken and the
normal admin URL won't let you in.

## What this is

A long-lived (90-day) signed JWT that **bypasses IAP enforcement**
on every `Depends(require_admin)` endpoint. The token's `jti` is
registered in `break_glass_tokens` so individual tokens can be
revoked without rotating the JWT signing key.

## When to use

Use **only** when one or more of these is true and you cannot reach
admin via the normal path:

- Google IAP is returning errors that we've ruled out as transient (>30 min)
- The OAuth consent screen / client config is broken
- Google Workspace itself is unavailable (your account is suspended/disabled)
- A planned secret rotation has temporarily broken the IAP flow

**Do not** use as a convenience shortcut. Every use writes a CRITICAL
ActionLog entry; routine use shows up in monthly audits.

## Threat model — what this protects you against, what it doesn't

| Failure | Break-glass works? |
|---|---|
| IAP misconfigured / OAuth client broken | ✅ Yes (this is the primary use case) |
| Google Workspace outage (you can't sign in to Google) | ✅ Yes |
| Cloud Run is down | ❌ No (entire API is down regardless) |
| Postgres is down | ❌ No (require_admin checks jti in DB) |
| JWT_SECRET rotated | ❌ No (token signature breaks; rotate during a planned window) |
| Cloud Armor blocked your IP | ❌ No (request never reaches require_admin) |

## How to issue a fresh token (founder, ~5 min)

Run locally on your Mac. The Cloud SQL Auth Proxy is the bridge to
the private-IP Postgres.

```bash
# Terminal A — start the Cloud SQL Auth Proxy (leaves it running)
gcloud sql proxy aurora-pg --project=aurora-lts-prod

# Terminal B — pull secrets, then run the issuance script
cd ~/Desktop/ASG-Middleware/server_files

export JWT_SECRET=$(gcloud secrets versions access latest \
    --secret=AURORA_JWT_SECRET --project=aurora-lts-prod)

DB_PASSWORD=$(gcloud secrets versions access latest \
    --secret=AURORA_DATABASE_URL --project=aurora-lts-prod \
    | sed -n 's|.*://aurora_app:\([^@]*\)@.*|\1|p')

export DATABASE_URL="postgresql+psycopg://aurora_app:${DB_PASSWORD}@127.0.0.1:5432/aurora_prod"
unset DB_PASSWORD

../venv/bin/python scripts/issue_break_glass_token.py \
    --user-id=1 --days=90 --notes="rotation 2026-Q3"

# Then clear env vars from your shell session
unset JWT_SECRET DATABASE_URL
```

The script prints the JWT **once**. Copy it immediately to:

- **Primary**: 1Password / Bitwarden secure note labelled
  `Aurora Break-glass JWT — DO NOT COPY ELSEWHERE`
- **Backup**: paper printout in a sealed envelope, signed across
  the seal, stored in a fireproof safe or safe deposit box

The DB stores only the `jti`. The JWT itself never lives in our
infrastructure — only in your 1Password vault.

## How to USE the token (during an incident)

```bash
curl -H "Authorization: Bearer <THE_JWT>" \
     https://api-aurora.com/api/v1/admin/compliance/health
```

Note: `api-aurora.com`, NOT `admin.aurora-ltd.co.il`. IAP is the
thing that's broken, so we go through the API host which doesn't
have IAP. The break-glass JWT proves identity directly to
`require_admin()`.

On success, the endpoint returns its normal response. The use
writes a CRITICAL ActionLog entry visible in:

- Cloud Logging: search `severity=CRITICAL break_glass_used`
- ActionLog table: `SELECT * FROM action_logs WHERE status LIKE 'CRITICAL%'`
- (Future) Cloud Monitoring alert + WhatsApp notification

## How to revoke a token (compromise or expiry)

If you believe your token is compromised, or after issuing a
replacement, revoke the old one via the IAP-gated admin path:

```bash
# Sign in via admin.aurora-ltd.co.il (IAP) — get an Aurora JWT
# from the admin SPA (Track 4) or via temporary JWT issuance.
# Then:

curl -X POST -H "Authorization: Bearer <REGULAR_ADMIN_JWT>" \
     -H "Content-Type: application/json" \
     -d '{"reason": "rotation; replaced 2026-Q3"}' \
     https://admin.aurora-ltd.co.il/api/v1/admin/break-glass/revoke/<JTI>
```

**Why IAP-only**: a stolen break-glass token CANNOT revoke itself
or other tokens. The revoke endpoint uses `require_admin_iap_strict`
which rejects break-glass JWTs. If the attacker has your token,
they cannot lock you out of your own emergency access.

To list tokens (audit trail):

```bash
curl -H "Authorization: Bearer <REGULAR_ADMIN_JWT>" \
     https://admin.aurora-ltd.co.il/api/v1/admin/break-glass
```

Returns the most recent 50 tokens with jti, expiry, revocation,
last-used timestamp, use count.

## Rotation schedule

**Every 90 days.** Cloud Scheduler reminder fires 14 days before
expiry (TODO: implement reminder cron).

Rotation procedure:
1. Issue a new token (above)
2. Store the new token in 1Password + new paper backup
3. Use the new token once to confirm it works (this also produces
   a "first use" audit entry that proves the new key works)
4. Revoke the old token via the IAP-gated revoke endpoint
5. Destroy the old paper backup (shred + burn)

## Audit cadence

**Monthly**: founder reviews `action_logs WHERE status LIKE
'CRITICAL%' OR status LIKE 'break_glass%'` looking for unexpected
entries. Expected entries: rotation issuance + verification use.
Unexpected entries: investigate immediately (potential
compromise).

## Open follow-ups (not blocking this runbook)

- WhatsApp alert on every CRITICAL_break_glass_used entry
  (`notification_queue` mechanism, deferred)
- Cloud Scheduler 14-day-pre-expiry reminder
- Postgres trigger preventing UPDATE on `revoked_at` once set
  (DB-layer immutability — deferred to Phase 3 of security
  roadmap, already designed as SEC-213)
