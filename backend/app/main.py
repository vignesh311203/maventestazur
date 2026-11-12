from __future__ import annotations
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Generator
from math import expm1, log1p
import jwt
import hashlib
import hmac
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pwdlib import PasswordHash
from pydantic import BaseModel, EmailStr, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

class Settings(BaseSettings):
    app_env: str = "development"
    app_secret_key: str = "replace-me"
    database_url: str = "sqlite:///./dev.db"
    access_token_minutes: int = Field(default=30, ge=1, le=120)
    cors_origins: str = "http://localhost:5173"
    redis_url: str = ""
    trusted_hosts: str = "localhost,127.0.0.1,testserver"
    bootstrap_admin_email: str = ""
    bootstrap_admin_password: str = ""
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
if settings.app_env == "production":
    if len(settings.app_secret_key) < 32 or settings.app_secret_key.startswith("replace-"):
        raise RuntimeError("Set a unique, random APP_SECRET_KEY for production")
    if not settings.database_url.startswith("postgresql"):
        raise RuntimeError("Production requires PostgreSQL")
    db_password = make_url(settings.database_url).password or ""
    if len(db_password) < 24 or db_password.startswith(("replace-", "change-")):
        raise RuntimeError("Set a unique, URL-safe production database password")
    if not settings.redis_url:
        raise RuntimeError("Production requires Redis-backed rate limiting")
    if not settings.trusted_hosts or "*" in settings.trusted_hosts:
        raise RuntimeError("Production requires explicit TRUSTED_HOSTS")
    if settings.bootstrap_admin_password and (len(settings.bootstrap_admin_password) < 16 or settings.bootstrap_admin_password.startswith("change-")):
        raise RuntimeError("Set a strong, unique bootstrap administrator password")
connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

class Base(DeclarativeBase): pass
class Role(str, Enum):
    admin = "admin"
    agent = "agent"
    partner = "partner"
    customer = "customer"

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(160))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default=Role.customer.value)
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

password_hash = PasswordHash.recommended()
bearer = HTTPBearer(auto_error=False)
limiter = Limiter(key_func=get_remote_address, storage_uri=settings.redis_url or "memory://")
app = FastAPI(title="Real Estate Platform API", version="0.1.0", docs_url="/docs" if settings.app_env != "production" else None, redoc_url=None, openapi_url="/openapi.json" if settings.app_env != "production" else None)
app.state.limiter = limiter
if settings.app_env == "production":
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[host.strip() for host in settings.trusted_hosts.split(",") if host.strip()])
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(CORSMiddleware, allow_origins=[v.strip() for v in settings.cors_origins.split(",") if v.strip()], allow_credentials=True, allow_methods=["GET", "POST", "PATCH", "DELETE"], allow_headers=["Authorization", "Content-Type"])

@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
    if settings.app_env == "production":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response

def db_session() -> Generator[Session, None, None]:
    db = SessionLocal()
    try: yield db
    finally: db.close()

class RegisterIn(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=160)
    password: str = Field(min_length=12, max_length=128)
class PasswordChangeIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)

class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)
def clean_name(value: str) -> str:
    value = value.strip()
    if len(value) < 2 or any(ord(ch) < 32 for ch in value):
        raise ValueError("Name must contain at least two visible characters")
    return value

def clean_phone(value: str) -> str:
    value = value.strip()
    if any(ch not in "0123456789+ ()-" for ch in value) or not 7 <= sum(ch.isdigit() for ch in value) <= 15:
        raise ValueError("Enter a valid phone number with 7 to 15 digits")
    return value

class LeadIn(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    phone: str = Field(min_length=7, max_length=30)
    email: EmailStr | None = None
    interest: str = Field(default="", max_length=255)
    _validate_name = field_validator("name")(clean_name)
    _validate_phone = field_validator("phone")(clean_phone)
class PublicEnquiryIn(LeadIn):
    consent: bool = Field(default=False)
    website: str = Field(default="", max_length=200)

class PlotIn(BaseModel):
    project: str = Field(min_length=2, max_length=160)
    plot_number: str = Field(min_length=1, max_length=40)
    area_sqft: float = Field(gt=0, le=1000000)
    rate_per_sqft: float = Field(gt=0, le=1000000)
    status: str = Field(default="available", pattern="^(available|reserved|sold)$")
    x: float = Field(default=0, ge=0, le=10000)
    y: float = Field(default=0, ge=0, le=10000)
class ReferralIn(BaseModel):
    referred_name: str = Field(min_length=2, max_length=160)
    referred_phone: str = Field(min_length=7, max_length=30)
    _validate_name = field_validator("referred_name")(clean_name)
    _validate_phone = field_validator("referred_phone")(clean_phone)
class ReviewIn(BaseModel):
    customer_id: int = Field(gt=0)
    channel: str = Field(default="manual", pattern="^(manual|email)$")
    consent_confirmed: bool

def password_version(user: User) -> str:
    return hmac.new(settings.app_secret_key.encode(), user.password_hash.encode(), hashlib.sha256).hexdigest()

def issue_token(user: User) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": str(user.id), "role": user.role, "pv": password_version(user), "iat": now, "exp": now + timedelta(minutes=settings.access_token_minutes)}, settings.app_secret_key, algorithm="HS256")

def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer), db: Session = Depends(db_session)) -> User:
    if not credentials: raise HTTPException(status_code=401, detail="Authentication required")
    try:
        payload = jwt.decode(credentials.credentials, settings.app_secret_key, algorithms=["HS256"], options={"require": ["sub", "iat", "exp"]})
        user_id = int(payload["sub"])
    except (jwt.InvalidTokenError, KeyError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    user = db.get(User, user_id)
    if not user or not user.is_active: raise HTTPException(status_code=401, detail="Account unavailable")
    if not hmac.compare_digest(str(payload.get("pv", "")), password_version(user)):
        raise HTTPException(status_code=401, detail="Session expired; please sign in again")
    return user

def require_roles(*roles: Role):
    def dependency(user: User = Depends(current_user)) -> User:
        if user.role not in [r.value for r in roles]: raise HTTPException(status_code=403, detail="Insufficient permissions")
        return user
    return dependency

@app.get("/health")
def health(): return {"status": "ok", "environment": settings.app_env}

@app.get("/ready")
def ready(db: Session = Depends(db_session)):
    try:
        db.execute(select(User.id).limit(1))
    except Exception:
        raise HTTPException(status_code=503, detail="Database unavailable")
    return {"status": "ready"}

@app.post("/api/auth/register", status_code=201)
@limiter.limit("5/minute")
def register(data: RegisterIn, request: Request, db: Session = Depends(db_session)):
    if db.scalar(select(User).where(User.email == data.email.lower())): raise HTTPException(status_code=409, detail="Email already registered")
    user = User(email=data.email.lower(), full_name=data.full_name.strip(), password_hash=password_hash.hash(data.password), role=Role.customer.value)
    db.add(user)
    try: db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Email already registered")
    db.refresh(user)
    return {"access_token": issue_token(user), "token_type": "bearer", "user": {"id": user.id, "email": user.email, "full_name": user.full_name, "role": user.role}}

@app.post("/api/auth/login")
@limiter.limit("10/minute")
def login(data: LoginIn, request: Request, db: Session = Depends(db_session)):
    user = db.scalar(select(User).where(User.email == data.email.lower()))
    if not user or not password_hash.verify(data.password, user.password_hash) or not user.is_active: raise HTTPException(status_code=401, detail="Incorrect email or password")
    return {"access_token": issue_token(user), "token_type": "bearer", "user": {"id": user.id, "email": user.email, "full_name": user.full_name, "role": user.role}}

@app.post("/api/auth/password")
@limiter.limit("5/minute")
def change_password(data: PasswordChangeIn, request: Request, db: Session = Depends(db_session), user: User = Depends(current_user)):
    if not password_hash.verify(data.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    if data.new_password == data.current_password:
        raise HTTPException(status_code=422, detail="Choose a different password")
    user.password_hash = password_hash.hash(data.new_password)
    db.commit()
    return {"message": "Password updated. Sign in again."}

@app.get("/api/me")
def me(user: User = Depends(current_user)): return {"id": user.id, "email": user.email, "full_name": user.full_name, "role": user.role}

@app.post("/api/calculators/emi")
def emi(principal: float = Query(gt=0, le=1e12), annual_rate_percent: float = Query(ge=0, le=100), months: int = Query(gt=0, le=600)):
    r = annual_rate_percent / 1200
    payment = principal / months if r == 0 else principal * r / (-expm1(-months * log1p(r)))
    return {"monthly_emi": round(payment, 2), "total_payment": round(payment * months, 2), "total_interest": round(payment * months - principal, 2)}

@app.post("/api/calculators/plot-cost")
def plot_cost(area_sqft: float = Query(gt=0, le=1e6), rate_per_sqft: float = Query(gt=0, le=1e6), registration_percent: float = Query(ge=0, le=100), stamp_duty_percent: float = Query(ge=0, le=100), other_charges: float = Query(ge=0, le=1e9)):
    base = area_sqft * rate_per_sqft
    reg = base * registration_percent / 100
    stamp = base * stamp_duty_percent / 100
    return {"base_cost": round(base, 2), "registration": round(reg, 2), "stamp_duty": round(stamp, 2), "other_charges": round(other_charges, 2), "total": round(base + reg + stamp + other_charges, 2)}

@app.post("/api/calculators/growth")
def growth(initial_value: float = Query(gt=0, le=1e12), annual_growth_percent: float = Query(ge=-99, le=100), years: int = Query(gt=0, le=100)):
    future = initial_value * (1 + annual_growth_percent / 100) ** years
    return {"future_value": round(future, 2), "gain": round(future - initial_value, 2)}

@app.get("/api/leads")
def list_leads(db: Session = Depends(db_session), user: User = Depends(require_roles(Role.admin))):
    query = select(Lead).order_by(Lead.created_at.desc()).limit(500)
    return [{"id": x.id, "name": x.name, "phone": x.phone, "email": x.email, "interest": x.interest, "status": x.status} for x in db.scalars(query)]

@app.post("/api/leads", status_code=201)
def create_lead(data: LeadIn, db: Session = Depends(db_session), user: User = Depends(require_roles(Role.admin, Role.customer))):
    lead = Lead(owner_id=user.id, name=data.name.strip(), phone=data.phone.strip(),
                email=user.email if user.role == Role.customer.value else (str(data.email).lower() if data.email else None), interest=data.interest.strip())
    db.add(lead); db.commit(); db.refresh(lead)
    return {"id": lead.id, "status": lead.status}

@app.post("/api/public/enquiries", status_code=201)
@limiter.limit("5/minute")
def public_enquiry(data: PublicEnquiryIn, request: Request, db: Session = Depends(db_session)):
    if not data.consent or data.website:
        raise HTTPException(status_code=422, detail="Please confirm consent and submit a valid enquiry")
    owner = db.scalar(select(User).where(User.role == Role.admin.value, User.is_active.is_(True)).order_by(User.id).limit(1))
    if not owner:
        raise HTTPException(status_code=503, detail="Enquiries temporarily unavailable")
    item = Lead(owner_id=owner.id, name=data.name, phone=data.phone, email=str(data.email).lower() if data.email else None, interest=data.interest.strip())
    db.add(item)
    db.commit()
    return {"message": "Your enquiry has been received."}

@app.get("/api/customer/enquiries")
def customer_enquiries(db: Session = Depends(db_session), user: User = Depends(require_roles(Role.customer))):
    return [{"id": item.id, "interest": item.interest, "status": item.status, "created_at": item.created_at.isoformat()} for item in db.scalars(select(Lead).where(Lead.owner_id == user.id).order_by(Lead.id.desc()).limit(100))]

@app.get("/api/admin/lead-notifications")
def lead_notifications(limit: int = Query(default=50, ge=1, le=200),
                       db: Session = Depends(db_session),
                       user: User = Depends(require_roles(Role.admin))):
    # New leads are durable notifications; changing lead status resolves the alert.
    query = select(Lead).where(Lead.status == "new").order_by(Lead.created_at.desc(), Lead.id.desc()).limit(limit)
    return [{"id": lead.id, "name": lead.name, "interest": lead.interest,
             "created_at": lead.created_at.isoformat()} for lead in db.scalars(query)]

@app.patch("/api/leads/{lead_id}/status")
def update_lead(lead_id: int, new_status: str, db: Session = Depends(db_session), user: User = Depends(require_roles(Role.admin))):
    if new_status not in {"new", "contacted", "qualified", "visit_booked", "won", "lost"}: raise HTTPException(status_code=422, detail="Invalid lead status")
    lead = db.get(Lead, lead_id)
    if not lead: raise HTTPException(status_code=404, detail="Lead not found")
    lead.status = new_status; db.commit()
    return {"id": lead.id, "status": lead.status}

@app.get("/api/plots")
def list_plots(db: Session = Depends(db_session)):
    return [{"id": p.id, "project": p.project, "plot_number": p.plot_number, "area_sqft": p.area_sqft, "rate_per_sqft": p.rate_per_sqft, "status": p.status, "x": p.x, "y": p.y} for p in db.scalars(select(Plot).order_by(Plot.project, Plot.plot_number))]

@app.post("/api/plots", status_code=201)
def add_plot(data: PlotIn, db: Session = Depends(db_session), user: User = Depends(require_roles(Role.admin))):
    plot = Plot(**data.model_dump()); db.add(plot); db.commit(); db.refresh(plot)
    return {"id": plot.id, **data.model_dump()}

@app.post("/api/partners/referrals", status_code=201)
def create_referral(data: ReferralIn, db: Session = Depends(db_session), user: User = Depends(require_roles(Role.partner, Role.admin))):
    item = Referral(referrer_id=user.id, referred_name=data.referred_name.strip(), referred_phone=data.referred_phone.strip())
    db.add(item); db.commit(); db.refresh(item)
    return {"id": item.id, "status": item.status, "reward_amount": item.reward_amount}

@app.get("/api/partners/referrals")
def list_referrals(db: Session = Depends(db_session), user: User = Depends(require_roles(Role.admin, Role.partner))):
    query = select(Referral)
    if user.role != Role.admin.value: query = query.where(Referral.referrer_id == user.id)
    return [{"id": r.id, "referred_name": r.referred_name if user.role == Role.admin.value else "Referral #" + str(r.id), "status": r.status, "reward_amount": r.reward_amount} for r in db.scalars(query)]

@app.post("/api/reviews/requests", status_code=201)
def request_review(data: ReviewIn, db: Session = Depends(db_session), user: User = Depends(require_roles(Role.admin))):
    if not data.consent_confirmed: raise HTTPException(status_code=400, detail="Explicit customer consent is required before review outreach")
    customer = db.get(User, data.customer_id)
    if not customer or customer.role != Role.customer.value: raise HTTPException(status_code=404, detail="Customer not found")
    item = ReviewRequest(customer_id=customer.id, channel=data.channel, consent_confirmed=True)
    db.add(item); db.commit(); db.refresh(item)
    return {"id": item.id, "status": item.status, "delivery": "not_configured"}

@app.get("/api/customer/portal")
def customer_portal(db: Session = Depends(db_session), user: User = Depends(require_roles(Role.customer))):
    refs = list(db.scalars(select(Referral).where(Referral.referrer_id == user.id)))
    return {"profile": {"id": user.id, "name": user.full_name, "email": user.email}, "referrals": [{"id": r.id, "status": r.status} for r in refs], "message": "Booking, payment and document records require verified business-system integration"}

@app.on_event("startup")
def bootstrap_admin():
    if not settings.bootstrap_admin_email or not settings.bootstrap_admin_password: return
    with SessionLocal() as db:
        exists = db.scalar(select(User).where(User.email == settings.bootstrap_admin_email.lower()))
        if not exists:
            db.add(User(email=settings.bootstrap_admin_email.lower(), full_name="Platform Admin", password_hash=password_hash.hash(settings.bootstrap_admin_password), role=Role.admin.value))
            db.commit()


# --- Admin operations and editable business settings ---
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

DEFAULT_SETTINGS = {"registration_percent":"5.0","stamp_duty_percent":"5.0","other_charges":"25000","referral_reward_amount":"0"}

class AdminUserIn(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=160)
    password: str = Field(min_length=12, max_length=128)
    role: Role
class AdminSettingsIn(BaseModel):
    registration_percent: float = Field(ge=0, le=100)
    stamp_duty_percent: float = Field(ge=0, le=100)
    other_charges: float = Field(ge=0, le=100000000)
    referral_reward_amount: float = Field(ge=0, le=100000000)
class ReferralDecisionIn(BaseModel):
    status: str = Field(pattern="^(approved|rejected|paid)$")
    reward_amount: float = Field(ge=0, le=100000000)
class PlotUpdateIn(BaseModel):
    project: str = Field(min_length=2, max_length=160)
    plot_number: str = Field(min_length=1, max_length=40)
    area_sqft: float = Field(gt=0, le=1000000)
    rate_per_sqft: float = Field(gt=0, le=1000000)
    status: str = Field(pattern="^(available|reserved|sold)$")
    x: float = Field(ge=0, le=10000)
    y: float = Field(ge=0, le=10000)

def write_admin_audit(db: Session, actor: User, action: str, target: str = ""):
    db.add(AdminAudit(actor_id=actor.id, action=action, target=target[:160]))
    db.commit()

@app.get("/api/settings/calculator")
def calculator_settings(db: Session = Depends(db_session)):
    rows = {x.key:x.value for x in db.scalars(select(AppSetting))}
    return {k:float(rows.get(k,v)) for k,v in DEFAULT_SETTINGS.items() if k in {"registration_percent","stamp_duty_percent","other_charges"}}

@app.get("/api/admin/settings")
def admin_get_settings(db: Session = Depends(db_session), user: User = Depends(require_roles(Role.admin))):
    rows = {x.key:x.value for x in db.scalars(select(AppSetting))}
    return {k:float(rows.get(k,v)) for k,v in DEFAULT_SETTINGS.items()}

@app.patch("/api/admin/settings")
def admin_update_settings(data: AdminSettingsIn, db: Session = Depends(db_session), user: User = Depends(require_roles(Role.admin))):
    for key,value in data.model_dump().items():
        row = db.scalar(select(AppSetting).where(AppSetting.key==key))
        if row: row.value=str(value)
        else: db.add(AppSetting(key=key,value=str(value)))
    db.commit()
    write_admin_audit(db,user,"settings.updated",",".join(data.model_fields_set))
    return data.model_dump()

@app.get("/api/admin/users")
def admin_list_users(db: Session = Depends(db_session), user: User = Depends(require_roles(Role.admin))):
    return [{"id":x.id,"email":x.email,"full_name":x.full_name,"role":x.role,"is_active":x.is_active,"created_at":x.created_at.isoformat()} for x in db.scalars(select(User).order_by(User.id.desc()))]

@app.post("/api/admin/users", status_code=201)
def admin_create_user(data: AdminUserIn, db: Session = Depends(db_session), user: User = Depends(require_roles(Role.admin))):
    email=data.email.lower()
    if db.scalar(select(User).where(User.email==email)): raise HTTPException(status_code=409,detail="Email already registered")
    created=User(email=email,full_name=data.full_name.strip(),password_hash=password_hash.hash(data.password),role=data.role.value)
    db.add(created)
    try: db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Email already registered")
    db.refresh(created)
    write_admin_audit(db,user,"user.created",f"{created.id}:{created.role}")
    return {"id":created.id,"email":created.email,"full_name":created.full_name,"role":created.role,"is_active":created.is_active}

@app.patch("/api/admin/users/{user_id}/active")
def admin_set_user_active(user_id:int, active:bool, db:Session=Depends(db_session), user:User=Depends(require_roles(Role.admin))):
    target=db.get(User,user_id)
    if not target: raise HTTPException(status_code=404,detail="User not found")
    if target.id==user.id and not active: raise HTTPException(status_code=400,detail="You cannot deactivate your own account")
    target.is_active=active; db.commit()
    write_admin_audit(db,user,"user.activation_changed",f"{target.id}:{active}")
    return {"id":target.id,"is_active":target.is_active}

@app.get("/api/admin/referrals")
def admin_list_referrals(db:Session=Depends(db_session), user:User=Depends(require_roles(Role.admin))):
    return [{"id":x.id,"referrer_id":x.referrer_id,"referred_name":x.referred_name,"referred_phone":x.referred_phone,"status":x.status,"reward_amount":x.reward_amount} for x in db.scalars(select(Referral).order_by(Referral.id.desc()))]

@app.patch("/api/admin/referrals/{referral_id}")
def admin_decide_referral(referral_id:int, data:ReferralDecisionIn, db:Session=Depends(db_session), user:User=Depends(require_roles(Role.admin))):
    item=db.get(Referral,referral_id)
    if not item: raise HTTPException(status_code=404,detail="Referral not found")
    item.status=data.status; item.reward_amount=data.reward_amount; db.commit()
    write_admin_audit(db,user,"referral.decision",f"{item.id}:{data.status}")
    return {"id":item.id,"status":item.status,"reward_amount":item.reward_amount}

@app.patch("/api/admin/plots/{plot_id}")
def admin_update_plot(plot_id:int, data:PlotUpdateIn, db:Session=Depends(db_session), user:User=Depends(require_roles(Role.admin))):
    item=db.get(Plot,plot_id)
    if not item: raise HTTPException(status_code=404,detail="Plot not found")
    for key,value in data.model_dump().items(): setattr(item,key,value)
    db.commit()
    write_admin_audit(db,user,"plot.updated",str(item.id))
    return {"id":item.id,**data.model_dump()}

@app.get("/api/admin/audit")
def admin_audit_log(limit:int=Query(default=100,ge=1,le=500), db:Session=Depends(db_session), user:User=Depends(require_roles(Role.admin))):
    return [{"id":x.id,"actor_id":x.actor_id,"action":x.action,"target":x.target,"created_at":x.created_at.isoformat()} for x in db.scalars(select(AdminAudit).order_by(AdminAudit.id.desc()).limit(limit))]

@app.on_event("startup")
def seed_business_settings():
    with SessionLocal() as db:
        for key,value in DEFAULT_SETTINGS.items():
            if not db.scalar(select(AppSetting).where(AppSetting.key==key)): db.add(AppSetting(key=key,value=value))
        db.commit()
