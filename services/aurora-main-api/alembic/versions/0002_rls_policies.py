"""rls — row-level security on high-value tenant tables

Revision ID: 0002_rls_policies
Revises: 0001_baseline
Create Date: 2026-05-27

Adds PostgreSQL Row-Level Security to high-value tenant-scoped tables.

POLICY SEMANTICS (NULL-PERMISSIVE — fail-open for legacy code):

  Each table gets one policy keyed on its actual tenant FK
  (business_id, organization_id, or client_id):
    USING (
      <tenant_col> = NULLIF(current_setting('aurora.tenant_id', true), '')::int
      OR NULLIF(current_setting('aurora.tenant_id', true), '') IS NULL
    )

  Translation:
    - If aurora.tenant_id session var is unset / empty / missing
      → policy lets ALL rows through (legacy app-layer scoping
        continues to work; no breaking change).
    - If aurora.tenant_id IS set
      → only rows matching that tenant key are visible
        (defense in depth — even a missing WHERE clause in app
         code cannot leak cross-tenant data).

  New endpoints opt into strict scoping by calling
  set_tenant_scope(db, tenant_id) before queries.

SQLite NOTE:
  SQLite has no concept of RLS — skipped entirely when the dialect
  is sqlite. Local dev remains unaffected.

CHOICE OF TABLES:
  invoices              business_id      financial record
  receipts              organization_id  financial + KYC
  payment_methods       organization_id  payment tokens
  subscription_payments organization_id  billing history
  client_documents      client_id        KYC + vault content
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0002_rls_policies"
down_revision: Union[str, Sequence[str], None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (table_name, tenant_column) — column varies because of legacy naming.
_RLS_TABLES = (
    ("invoices",              "business_id"),
    ("receipts",              "organization_id"),
    ("payment_methods",       "organization_id"),
    ("subscription_payments", "organization_id"),
    ("client_documents",      "client_id"),
)


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _table_exists(conn, table_name: str) -> bool:
    return table_name in sa.inspect(conn).get_table_names()


def upgrade() -> None:
    if not _is_postgres():
        return  # SQLite — RLS not supported; app-layer scoping only.

    conn = op.get_bind()

    for table, tenant_col in _RLS_TABLES:
        if not _table_exists(conn, table):
            print(f"[rls] {table}: skipped (table does not exist)")
            continue

        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")

        policy = f"{table}_tenant_isolation"
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
        op.execute(
            f"""
            CREATE POLICY {policy} ON {table}
            USING (
                {tenant_col} = NULLIF(current_setting('aurora.tenant_id', true), '')::int
                OR NULLIF(current_setting('aurora.tenant_id', true), '') IS NULL
            )
            """
        )
        print(f"[rls] {table}: RLS enabled on {tenant_col} (NULL-permissive)")


def downgrade() -> None:
    if not _is_postgres():
        return
    conn = op.get_bind()
    for table, _ in _RLS_TABLES:
        if not _table_exists(conn, table):
            continue
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
