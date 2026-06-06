# Alembic Migrations (Aurora LTS — P1-02)

## Policy

- **Legacy hand-rolled phase files** (`app/migrate_phase4.py` …
  `app/migrate_phase21.py`, `app/migrations/migrate_phase21_vault.py`)
  are frozen as of the P1-02 cutover. They continue to run on every
  boot under the P1-01 advisory lock; their `ADD COLUMN IF NOT EXISTS`
  / `CREATE INDEX IF NOT EXISTS` idioms are no-ops on a fully-migrated
  database. **Do not add new DDL to these files.**
- **All NEW schema changes** go through Alembic.

## Creating a new migration

```bash
cd server_files
alembic revision --autogenerate -m "describe change in one line"
```

Edit the generated file under `alembic/versions/`, review the
`upgrade()` body (autogenerate is a starting point — never blindly
trust it on operations like column renames, type changes, or data
migrations), commit.

## Applying migrations

In production this happens automatically at FastAPI startup via
`alembic_bootstrap_or_upgrade()` called from `_run_all_phase_migrations`
inside the P1-01 advisory lock. No manual step.

Manually (e.g., for offline schema review):

```bash
cd server_files
alembic upgrade head        # apply
alembic downgrade -1        # roll back one revision
alembic history --verbose   # see the version chain
alembic current             # what revision is the DB at?
```

## The baseline

`versions/0001_baseline.py` has empty `upgrade()` and `downgrade()`
intentionally. It represents the schema state produced by the frozen
legacy phase migrations. On first deploy after P1-02 lands, the
bootstrap helper detects the missing `alembic_version` table and runs
`alembic stamp head` — no DDL, just records that the DB is at the
baseline. Subsequent boots run `alembic upgrade head` normally.

## Editing the env

`alembic/env.py` is wired to `app.database.Base.metadata`. Any model
registered in `app.database.models` (or its submodules) will be picked
up by `--autogenerate`. The `DATABASE_URL` env var drives the
connection — same value used by the FastAPI runtime.
