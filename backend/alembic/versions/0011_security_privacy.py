"""Administrator TOTP/recovery and account privacy workflows."""
from alembic import op
import sqlalchemy as sa

revision = "0011_security_privacy"
down_revision = "0010_inventory_review"
branch_labels = None
depends_on = None

def upgrade():
    conn=op.get_bind()
    for table,columns in (
        ("users",[sa.Column("privacy_notice_version",sa.String(40)),sa.Column("privacy_notice_ack_at",sa.DateTime(timezone=True)),sa.Column("erased_at",sa.DateTime(timezone=True))]),
        ("leads",[sa.Column("contact_consent_text",sa.Text()),sa.Column("contact_consent_withdrawn_at",sa.DateTime(timezone=True))]),
        ("support_messages",[sa.Column("ai_consent_at",sa.DateTime(timezone=True)),sa.Column("ai_consent_version",sa.String(40)),sa.Column("ai_consent_text",sa.Text())]),
    ):
        existing={c["name"] for c in sa.inspect(conn).get_columns(table)}
        for column in columns:
            if column.name not in existing:
                op.add_column(table,column)
    if not sa.inspect(conn).has_table("admin_mfa"):
        op.create_table("admin_mfa",
            sa.Column("user_id",sa.Integer(),sa.ForeignKey("users.id"),primary_key=True),
            sa.Column("secret_ciphertext",sa.Text(),nullable=False),
            sa.Column("enabled",sa.Boolean(),nullable=False),
            sa.Column("expires_at",sa.DateTime(timezone=True),nullable=True),
            sa.Column("last_step",sa.Integer(),nullable=False),
            sa.Column("recovery_hashes",sa.JSON(),nullable=False))
    if not sa.inspect(conn).has_table("privacy_requests"):
        op.create_table("privacy_requests",
            sa.Column("id",sa.Integer(),primary_key=True),
            sa.Column("customer_id",sa.Integer(),sa.ForeignKey("users.id"),nullable=False),
            sa.Column("kind",sa.String(30),nullable=False),
            sa.Column("status",sa.String(20),nullable=False),
            sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),
            sa.Column("resolved_at",sa.DateTime(timezone=True),nullable=True),
            sa.Column("resolution_code",sa.String(40),server_default="",nullable=False),
            sa.CheckConstraint("kind IN ('erasure','correction','withdraw_contact')",name="ck_privacy_kind"),
            sa.CheckConstraint("status IN ('pending','in_review','resolved','declined','erased')",name="ck_privacy_status"))
        op.create_index("ix_privacy_requests_customer_id","privacy_requests",["customer_id"])
        op.create_index("ix_privacy_status_created","privacy_requests",["status","created_at"])

def downgrade():
    raise RuntimeError("Preserve consent and privacy audit history; restore a verified backup instead.")
