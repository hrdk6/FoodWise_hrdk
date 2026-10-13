"""Row-level security, append-only audit log, geospatial index

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Kept literal (not imported) so this migration stays stable if the model list changes later.
TENANT_TABLES = [
    "users", "campuses", "canteens", "dishes", "menu_entries", "calendar_days", "weather_days",
    "service_logs", "live_sessions", "import_jobs", "model_versions", "forecasts", "feedback",
    "votes", "rsvps", "tenant_partner_links", "surplus_incidents", "incident_events", "offers",
    "handoffs", "jobs", "audit_logs", "outbox_messages", "webhooks", "webhook_deliveries",
]

BYPASS = "current_setting('app.bypass_rls', true) = 'on'"
CURRENT_TENANT = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS cube")
    op.execute("CREATE EXTENSION IF NOT EXISTS earthdistance")

    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING ({BYPASS} OR tenant_id = {CURRENT_TENANT}) "
            f"WITH CHECK ({BYPASS} OR tenant_id = {CURRENT_TENANT})"
        )

    op.execute("ALTER TABLE tenants ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenants FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_self ON tenants USING ({BYPASS} OR id = {CURRENT_TENANT}) "
        f"WITH CHECK ({BYPASS} OR id = {CURRENT_TENANT})"
    )

    op.execute(
        """
        CREATE FUNCTION audit_logs_append_only() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'audit_logs is append-only';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER audit_logs_no_update BEFORE UPDATE OR DELETE ON audit_logs "
        "FOR EACH ROW EXECUTE FUNCTION audit_logs_append_only()"
    )

    op.execute("CREATE INDEX ix_partner_orgs_earth ON partner_orgs USING gist (ll_to_earth(lat, lon))")
    op.execute("CREATE INDEX ix_canteens_earth ON canteens USING gist (ll_to_earth(lat, lon))")
    op.execute(
        "CREATE UNIQUE INDEX uq_jobs_dedupe_active ON jobs (dedupe_key) "
        "WHERE dedupe_key IS NOT NULL AND status IN ('queued', 'running')"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_jobs_dedupe_active")
    op.execute("DROP INDEX IF EXISTS ix_canteens_earth")
    op.execute("DROP INDEX IF EXISTS ix_partner_orgs_earth")
    op.execute("DROP TRIGGER IF EXISTS audit_logs_no_update ON audit_logs")
    op.execute("DROP FUNCTION IF EXISTS audit_logs_append_only()")
    op.execute("DROP POLICY IF EXISTS tenant_self ON tenants")
    op.execute("ALTER TABLE tenants DISABLE ROW LEVEL SECURITY")
    for table in TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
