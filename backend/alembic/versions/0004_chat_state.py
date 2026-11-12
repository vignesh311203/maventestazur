"""Add structured chat request data and persistent conversation state."""
from alembic import op
import sqlalchemy as sa

revision = "0004_chat_state"
down_revision = "0003_support_messages"
branch_labels = None
depends_on = None

def upgrade():
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("leads")}
    for name, size, default in (("request_type", 30, "enquiry"), ("source", 30, "website"), ("preferred_time", 80, "")):
        if name not in columns:
            op.add_column("leads", sa.Column(name, sa.String(size), nullable=False, server_default=default))
    if "chat_states" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table("chat_states", sa.Column("customer_id", sa.Integer(), sa.ForeignKey("users.id"), primary_key=True), sa.Column("state", sa.JSON(), nullable=False), sa.Column("quick", sa.JSON(), nullable=False))

def downgrade():
    raise RuntimeError("Preserve request history; restore a verified backup instead.")
