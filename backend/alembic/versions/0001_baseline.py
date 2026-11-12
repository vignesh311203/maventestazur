"""Baseline MetroEstate schema, preserving pre-Alembic installations.

Revision ID: 0001_baseline
Revises: None
"""
from datetime import datetime, timezone
from alembic import op
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, inspect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None

# Frozen snapshot: do not import the changing application models here.
class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(160))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="customer")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class Lead(Base):
    __tablename__ = "leads"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    phone: Mapped[str] = mapped_column(String(30))
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    interest: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(30), default="new")
    # Retain legacy database column for compatibility; no messaging or consent collection uses it.
    _legacy_consent: Mapped[bool] = mapped_column("consent_whatsapp", Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class Plot(Base):
    __tablename__ = "plots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project: Mapped[str] = mapped_column(String(160), index=True)
    plot_number: Mapped[str] = mapped_column(String(40))
    area_sqft: Mapped[float] = mapped_column(Float)
    rate_per_sqft: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), default="available")
    x: Mapped[float] = mapped_column(Float, default=0)
    y: Mapped[float] = mapped_column(Float, default=0)

class Referral(Base):
    __tablename__ = "referrals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    referrer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    referred_name: Mapped[str] = mapped_column(String(160))
    referred_phone: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(30), default="submitted")
    reward_amount: Mapped[float] = mapped_column(Float, default=0)

class ReviewRequest(Base):
    __tablename__ = "review_requests"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    channel: Mapped[str] = mapped_column(String(30), default="manual")
    status: Mapped[str] = mapped_column(String(30), default="queued")
    consent_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class AppSetting(Base):
    __tablename__ = "app_settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    value: Mapped[str] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

class AdminAudit(Base):
    __tablename__ = "admin_audit"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(String(100))
    target: Mapped[str] = mapped_column(String(160), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

def upgrade():
    bind = op.get_bind()
    existing = inspect(bind)
    for table in Base.metadata.sorted_tables:
        if existing.has_table(table.name):
            actual = {col["name"]: col for col in existing.get_columns(table.name)}
            missing = set(table.columns.keys()) - set(actual)
            if missing:
                raise RuntimeError(f"Legacy table {table.name} is missing columns: {sorted(missing)}. Back up and repair the schema before migration.")
            for column in table.columns:
                if actual[column.name]["nullable"] != column.nullable:
                    raise RuntimeError(f"Legacy column {table.name}.{column.name} has incompatible nullability")
                actual_type = actual[column.name]["type"]
                if actual_type._type_affinity != column.type._type_affinity:
                    raise RuntimeError(f"Legacy column {table.name}.{column.name} has incompatible type")
            actual_pk = existing.get_pk_constraint(table.name).get("constrained_columns") or []
            if set(actual_pk) != {column.name for column in table.primary_key.columns}:
                raise RuntimeError(f"Legacy table {table.name} has incompatible primary key")
            actual_fks = {(tuple(fk["constrained_columns"]), fk["referred_table"], tuple(fk["referred_columns"])) for fk in existing.get_foreign_keys(table.name)}
            expected_fks = {(tuple(fk.column_keys), fk.referred_table.name, tuple(element.column.name for element in fk.elements)) for fk in table.foreign_key_constraints}
            if not expected_fks.issubset(actual_fks):
                raise RuntimeError(f"Legacy table {table.name} has missing foreign keys")
            for index in table.indexes:
                index.create(bind, checkfirst=True)
        else:
            table.create(bind)
    # Existing provider-specific tables are deliberately left intact.

def downgrade():
    raise RuntimeError("Baseline downgrade would delete business data. Restore a verified backup instead.")
