"""Add public listing descriptions and persistent normalized plot photos."""
from alembic import op
import sqlalchemy as sa

revision = "0008_plot_media"
down_revision = "0007_plot_finance"
branch_labels = None
depends_on = None

def upgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {c["name"] for c in inspector.get_columns("plots")}
    for col in (sa.Column("description",sa.Text(),nullable=False,server_default=""),sa.Column("amenities",sa.Text(),nullable=False,server_default=""),sa.Column("approval_details",sa.Text(),nullable=False,server_default=""),sa.Column("facing",sa.String(20),nullable=False,server_default=""),sa.Column("road_width_ft",sa.Float(),nullable=True)):
        if col.name not in columns:
            op.add_column("plots",col)
    if "plot_photos" not in inspector.get_table_names():
        op.create_table("plot_photos",sa.Column("plot_id",sa.Integer(),sa.ForeignKey("plots.id",ondelete="CASCADE"),primary_key=True),sa.Column("jpeg",sa.LargeBinary(),nullable=False))

def downgrade():
    raise RuntimeError("Preserve uploaded property photos and details; restore a verified backup instead.")
