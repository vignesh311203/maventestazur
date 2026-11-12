"""Record contact permission without altering existing enquiry history."""
from alembic import op
import sqlalchemy as sa

revision = "0002_contact_consent"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None

def upgrade():
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("leads")}
    if "contact_consent_at" not in columns:
        op.add_column("leads", sa.Column("contact_consent_at", sa.DateTime(timezone=True), nullable=True))
    if "contact_consent_version" not in columns:
        op.add_column("leads", sa.Column("contact_consent_version", sa.String(40), nullable=True))

def downgrade():
    raise RuntimeError("Preserve consent records; restore a verified backup instead.")
