"""Add public listing information and persistent, normalized cover photos."""
from alembic import op
import sqlalchemy as sa

revision = "0008_property_media"
down_revision = "0007_plot_finance"
branch_labels = None
depends_on = None

def upgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {c["name"] for c in inspector.get_columns("plots")}
    for col in (
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("amenities", sa.Text(), nullable=False, server_default=""),
        sa.Column("approvals", sa.String(500), nullable=False, server_default=""),
        sa.Column("possession", sa.String(120), nullable=False, server_default=""),
        sa.Column("facing", sa.String(20), nullable=False, server_default=""),
        sa.Column("road_width_ft", sa.Float(), nullable=True),
        sa.Column("photo_version", sa.String(64), nullable=True),
    ):
        if col.name not in columns:
            op.add_column("plots", col)
    if "plot_photos" not in inspector.get_table_names():
        op.create_table("plot_photos", sa.Column("plot_id", sa.Integer(), sa.ForeignKey("plots.id", ondelete="CASCADE"), primary_key=True), sa.Column("content", sa.LargeBinary(), nullable=False))

def downgrade():
    raise RuntimeError("Preserve property details and photos; restore a verified backup instead.")
