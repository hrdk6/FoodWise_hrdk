"""One organisation login and an admin console; nearby places instead of partner accounts;
dish pictures; operations telemetry.

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BYPASS = "current_setting('app.bypass_rls', true) = 'on'"
CURRENT_TENANT = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"

STATUS_MAP = {
    # old incident status -> new
    "detected": "open",
    "offered": "open",
    "claimed": "arranged",
    "picked_up": "arranged",
    "delivered": "closed",
    "diverted": "closed",
}


def upgrade() -> None:
    # ------------------------------------------------------------------ accounts
    op.execute("DELETE FROM users WHERE role = 'partner_coordinator'")
    op.execute("UPDATE users SET role = 'admin' WHERE role = 'platform_admin'")
    op.execute("UPDATE users SET role = 'organization' WHERE role <> 'admin'")
    op.drop_constraint("fk_users_partner_id_partner_orgs", "users", type_="foreignkey")
    op.drop_column("users", "partner_id")

    # ------------------------------------------------------------------ rescue: places, not partners
    op.create_table(
        "donations",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("incident_id", sa.UUID(), sa.ForeignKey("surplus_incidents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("place_ref", sa.String(80), nullable=False),
        sa.Column("place_name", sa.String(200), nullable=False),
        sa.Column("place_kind", sa.String(30), nullable=False),
        sa.Column("place_lat", sa.Float()),
        sa.Column("place_lon", sa.Float()),
        sa.Column("place_phone", sa.String(40)),
        sa.Column("place_address", sa.Text()),
        sa.Column("distance_km", sa.Float()),
        sa.Column("portions", sa.Float(), nullable=False),
        sa.Column("weight_kg", sa.Float()),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("contact_method", sa.String(16), nullable=False),
        sa.Column("contact_name", sa.String(120)),
        sa.Column("notes", sa.Text()),
        sa.Column("handed_over_at", sa.DateTime(timezone=True)),
        sa.Column("created_by", sa.UUID()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_donations_tenant_id", "donations", ["tenant_id"])
    op.create_index("ix_donations_incident_id", "donations", ["incident_id"])
    op.create_index("ix_donations_place_ref", "donations", ["place_ref"])
    op.execute("ALTER TABLE donations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE donations FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON donations USING ({BYPASS} OR tenant_id = {CURRENT_TENANT}) "
        f"WITH CHECK ({BYPASS} OR tenant_id = {CURRENT_TENANT})"
    )

    # Past handoffs become donations so impact history survives the change.
    op.execute(
        """
        INSERT INTO donations (id, tenant_id, incident_id, place_ref, place_name, place_kind, place_lat, place_lon,
                               distance_km, portions, weight_kg, status, contact_method, handed_over_at, created_at)
        SELECT h.id, h.tenant_id, h.incident_id, 'legacy:' || p.id, p.name, p.kind, p.lat, p.lon, o.distance_km,
               o.qty_portions, h.weight_kg, 'handed_over', 'other', h.created_at, h.created_at
        FROM handoffs h JOIN offers o ON o.id = h.offer_id JOIN partner_orgs p ON p.id = o.partner_id
        """
    )
    op.drop_table("handoffs")
    op.drop_table("offers")
    op.drop_table("tenant_partner_links")
    op.execute("DROP INDEX IF EXISTS ix_partner_orgs_earth")
    op.drop_table("partner_orgs")

    op.alter_column("surplus_incidents", "first_accepted_at", new_column_name="first_agreed_at")
    op.drop_column("surplus_incidents", "wave")
    op.drop_column("surplus_incidents", "split_plan")
    for old, new in STATUS_MAP.items():
        op.execute(f"UPDATE surplus_incidents SET status = '{new}' WHERE status = '{old}'")

    op.create_table(
        "place_cache",
        sa.Column("key", sa.String(120), primary_key=True),
        sa.Column("results", postgresql.JSONB(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # Settings keys that described partner alert waves no longer apply.
    op.execute("UPDATE tenants SET settings = settings - 'waves' - 'wave_timeout' - 'matching'")

    # ------------------------------------------------------------------ dish pictures
    op.add_column("dishes", sa.Column("image_key", sa.String(400)))
    op.add_column("dishes", sa.Column("image_source", sa.String(20)))
    op.add_column("dishes", sa.Column("image_status", sa.String(12), server_default="pending", nullable=False))
    op.add_column("dishes", sa.Column("image_credit", sa.Text()))
    op.add_column("dishes", sa.Column("image_license", sa.String(80)))
    op.add_column("dishes", sa.Column("image_source_url", sa.Text()))
    op.add_column("dishes", sa.Column("image_updated_at", sa.DateTime(timezone=True)))

    # ------------------------------------------------------------------ operations telemetry (global)
    op.create_table(
        "service_checks",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("service", sa.String(40), nullable=False),
        sa.Column("hour", sa.DateTime(timezone=True), nullable=False),
        sa.Column("calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_ms", sa.Float(), nullable=False, server_default="0"),
        sa.Column("max_ms", sa.Float(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text()),
        sa.Column("last_error_at", sa.DateTime(timezone=True)),
        sa.Column("last_ok_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("service", "hour"),
    )
    op.create_table(
        "worker_heartbeats",
        sa.Column("worker_id", sa.String(80), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("jobs_done", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("jobs_failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("current_job", sa.String(80)),
    )
    op.create_table(
        "web_vitals",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("metric", sa.String(8), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("rating", sa.String(20), nullable=False),
        sa.Column("route", sa.String(120), nullable=False),
        sa.Column("connection", sa.String(12)),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_web_vitals_at", "web_vitals", ["at"])


def downgrade() -> None:
    raise NotImplementedError("0003 removes partner accounts; restore from a backup to go back.")
