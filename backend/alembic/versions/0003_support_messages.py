"""Add private support conversations without altering existing customer records."""
from alembic import op
import sqlalchemy as sa

revision = "0003_support_messages"
down_revision = "0002_contact_consent"
branch_labels = None
depends_on = None

def upgrade():
    if "support_messages" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "support_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("sender", sa.String(16), nullable=False),
        sa.Column("client_id", sa.String(80), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("customer_id", "client_id", name="uq_support_customer_client"),
    )
    op.create_index("ix_support_messages_customer_id", "support_messages", ["customer_id"])

def downgrade():
    raise RuntimeError("Preserve customer conversations; restore a verified backup instead.")
