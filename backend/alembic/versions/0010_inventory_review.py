"""Quarantine ambiguous legacy listings without deleting or renumbering them."""
from alembic import op
import sqlalchemy as sa

revision = "0010_inventory_review"
down_revision = "0009_security_hardening"
branch_labels = None
depends_on = None

def upgrade():
    conn=op.get_bind()
    if conn.dialect.name=="postgresql":
        conn.execute(sa.text("LOCK TABLE plots IN ACCESS EXCLUSIVE MODE"))
    columns={c["name"] for c in sa.inspect(conn).get_columns("plots")}
    if "listing_state" not in columns:
        op.add_column("plots",sa.Column("listing_state",sa.String(20),nullable=False,server_default="published"))
    conn.execute(sa.text("""UPDATE plots SET listing_state='conflict'
        WHERE listing_state='published' AND EXISTS
        (SELECT 1 FROM plots AS other WHERE other.id<>plots.id
         AND other.listing_state<>'archived'
         AND lower(trim(other.project))=lower(trim(plots.project))
         AND lower(trim(other.plot_number))=lower(trim(plots.plot_number)))"""))
    conn.execute(sa.text("DROP INDEX IF EXISTS uq_plots_project_number_ci"))
    if "ck_plots_listing_state" not in {c["name"] for c in sa.inspect(conn).get_check_constraints("plots")}:
        with op.batch_alter_table("plots") as batch:
            batch.create_check_constraint("ck_plots_listing_state","listing_state IN ('published','conflict','archived')")
    conn.execute(sa.text("CREATE UNIQUE INDEX uq_plots_project_number_ci ON plots (lower(trim(project)), lower(trim(plot_number))) WHERE listing_state='published'"))
    # Also reject new published identities colliding with quarantined rows.
    # PostgreSQL advisory lock serializes direct writes and the application.
    if conn.dialect.name=="postgresql":
        conn.execute(sa.text("""CREATE OR REPLACE FUNCTION metroestate_guard_plot_identity()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          PERFORM pg_advisory_xact_lock(52403182);
          IF NEW.listing_state='published' AND EXISTS
            (SELECT 1 FROM plots p WHERE p.id<>NEW.id
             AND p.listing_state<>'archived'
             AND lower(trim(p.project))=lower(trim(NEW.project))
             AND lower(trim(p.plot_number))=lower(trim(NEW.plot_number)))
          THEN RAISE EXCEPTION 'Plot identifier already exists' USING ERRCODE='23505';
          END IF;
          RETURN NEW;
        END;
        $$"""))
        conn.execute(sa.text("DROP TRIGGER IF EXISTS metroestate_guard_plot_identity ON plots"))
        conn.execute(sa.text("""CREATE TRIGGER metroestate_guard_plot_identity
            BEFORE INSERT OR UPDATE OF project,plot_number,listing_state ON plots
            FOR EACH ROW EXECUTE FUNCTION metroestate_guard_plot_identity()"""))

def downgrade():
    raise RuntimeError("Preserve archived inventory and audit data; restore a verified backup instead.")
