"""Add revocable sessions, public plot location fields, and private reporting destination."""
from alembic import op
import sqlalchemy as sa

revision = "0006_sessions_locations"
down_revision = "0005_sheets_outbox"
branch_labels = None
depends_on = None

def upgrade():
    inspector = sa.inspect(op.get_bind())
    tables = inspector.get_table_names()
    if "auth_sessions" not in tables:
        op.create_table("auth_sessions", sa.Column("digest", sa.String(64), primary_key=True), sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False), sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False))
        op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
        op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])
    columns = {c["name"] for c in inspector.get_columns("plots")}
    for col in (sa.Column("location_label", sa.String(255), nullable=False, server_default=""), sa.Column("latitude", sa.Float(), nullable=True), sa.Column("longitude", sa.Float(), nullable=True)):
        if col.name not in columns:
            op.add_column("plots", col)
    if "report_destinations" not in tables:
        op.create_table("report_destinations", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("email", sa.String(255), nullable=False), sa.Column("sheet_id", sa.String(200), nullable=False))

def downgrade():
    raise RuntimeError("Preserve production data; restore a verified backup instead.")
