"""Add optional verified plot pricing and dimensions; preserve existing inventory."""
from alembic import op
import sqlalchemy as sa

revision = "0007_plot_finance"
down_revision = "0006_sessions_locations"
branch_labels = None
depends_on = None

def upgrade():
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("plots")}
    for name, kind in (("corner_plot", sa.Boolean), ("corner_premium_percent", sa.Float), ("guidance_value", sa.Float), ("width_ft", sa.Float), ("depth_ft", sa.Float)):
        if name not in columns:
            op.add_column("plots", sa.Column(name, kind(), nullable=True))

def downgrade():
    raise RuntimeError("Preserve plot pricing and dimensions; restore a verified backup instead.")
