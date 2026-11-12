"""Add reporting queue and sync status without copying private values."""
from alembic import op
import sqlalchemy as sa

revision = "0005_sheets_outbox"
down_revision = "0004_chat_state"
branch_labels = None
depends_on = None

def upgrade():
    tables = sa.inspect(op.get_bind()).get_table_names()
    if "sheet_outbox" not in tables:
        op.create_table("sheet_outbox", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    if "sheet_sync_status" not in tables:
        op.create_table("sheet_sync_status", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("sheet_id", sa.String(200), nullable=False), sa.Column("last_success", sa.DateTime(timezone=True), nullable=True), sa.Column("last_error", sa.String(200), nullable=False))

def downgrade():
    raise RuntimeError("Preserve reporting state; restore a verified backup instead.")
