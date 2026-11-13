"""Atomic audit values, safe inventory uniqueness and query indexes."""
from alembic import op
import sqlalchemy as sa

revision = "0009_security_hardening"
down_revision = "0008_plot_media"
branch_labels = None
depends_on = None

def upgrade():
    conn = op.get_bind()
    duplicate = conn.execute(sa.text("SELECT 1 FROM plots GROUP BY lower(trim(project)), lower(trim(plot_number)) HAVING count(*) > 1 LIMIT 1")).first()
    if duplicate:
        raise RuntimeError("Duplicate plot identifiers: reconcile existing records before migration")
    columns = {c["name"] for c in sa.inspect(conn).get_columns("admin_audit")}
    for name in ("before_values","after_values"):
        if name not in columns:
            op.add_column("admin_audit", sa.Column(name,sa.JSON(),nullable=True))
    for table,name,condition in (
        ("plots","ck_plots_status","status IN ('available','reserved','sold')"),
        ("users","ck_users_role","role IN ('admin','agent','partner','customer')"),
    ):
        if name not in {x["name"] for x in sa.inspect(conn).get_check_constraints(table)}:
            with op.batch_alter_table(table) as batch:
                batch.create_check_constraint(name,condition)
    for ddl in (
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_plots_project_number_ci ON plots (lower(trim(project)), lower(trim(plot_number)))",
        "CREATE INDEX IF NOT EXISTS ix_plots_status ON plots (status)",
        "CREATE INDEX IF NOT EXISTS ix_leads_status_created_at ON leads (status, created_at)",
        "CREATE INDEX IF NOT EXISTS ix_support_customer_id_id ON support_messages (customer_id, id)",
    ):
        conn.execute(sa.text(ddl))

def downgrade():
    raise RuntimeError("Preserve audit data; restore a verified backup instead.")
