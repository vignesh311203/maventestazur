from __future__ import annotations
from datetime import datetime, timedelta, timezone
from .chatbot import respond, MENU
from .finance import router as finance_router
from enum import Enum
from typing import Generator, Literal
from math import expm1, log1p
from contextlib import asynccontextmanager
import logging
import time
import uuid
from limits import parse
from starlette.concurrency import run_in_threadpool
from .request_security import client_ip, account_key
import jwt
import hashlib
import hmac
import secrets
import re
from urllib.parse import urlsplit
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pwdlib import PasswordHash
from pydantic import BaseModel, EmailStr, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import delete, text, event, JSON, Boolean, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint, Index, CheckConstraint, create_engine, select, func
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

class Settings(BaseSettings):
    app_env: str = "development"
    app_secret_key: str = "replace-me"
    database_url: str = "sqlite:///./dev.db"
    access_token_minutes: int = Field(default=30, ge=1, le=120)
    cors_origins: str = "http://localhost:5173"
    redis_url: str = ""
    trusted_hosts: str = "localhost,127.0.0.1,testserver"
    client_ip_mode: Literal["socket", "railway"] = "socket"
    public_base_url: str = ""
    external_full_customer_reports: bool = False
    bootstrap_admin_email: str = ""
    bootstrap_admin_password: str = ""
    ai_enabled: bool = False
    openai_api_key: SecretStr = SecretStr("")
    openai_model: str = "gpt-5.6-terra"
    ai_provider: Literal["openai", "anthropic"] = "openai"
    anthropic_api_key: SecretStr = SecretStr("")
    anthropic_model: str = "claude-sonnet-4-6"
    assistant_name: str = "Riya"
    smtp_host: str = ""
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_from_email: str = ""
    google_sheets_enabled: bool = False
    google_sheet_id: str = ""
    google_service_account_json: SecretStr = SecretStr("")
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
    __table_args__ = (CheckConstraint("role IN ('admin', 'agent', 'partner', 'customer')", name="ck_users_role"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(160))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default=Role.customer.value)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class AuthSession(Base):
    __tablename__ = "auth_sessions"
    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ReportDestination(Base):
    __tablename__ = "report_destinations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), default="")
    sheet_id: Mapped[str] = mapped_column(String(200), default="")


class Lead(Base):
    __tablename__ = "leads"
    __table_args__ = (Index("ix_leads_status_created_at", "status", "created_at"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    phone: Mapped[str] = mapped_column(String(30))
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    interest: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(30), default="new")
    # Retain legacy database column for compatibility; no messaging or consent collection uses it.
    _legacy_consent: Mapped[bool] = mapped_column("consent_whatsapp", Boolean, default=False)
    contact_consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    contact_consent_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    request_type: Mapped[str] = mapped_column(String(30), default="enquiry", server_default="enquiry")
    source: Mapped[str] = mapped_column(String(30), default="website", server_default="website")
    preferred_time: Mapped[str] = mapped_column(String(80), default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class Plot(Base):
    __tablename__ = "plots"
    __table_args__ = (Index("uq_plots_project_number_ci", text("lower(trim(project))"), text("lower(trim(plot_number))"), unique=True, postgresql_where=text("listing_state = 'published'"), sqlite_where=text("listing_state = 'published'")), CheckConstraint("listing_state IN ('published', 'conflict', 'archived')", name="ck_plots_listing_state"), Index("ix_plots_status", "status"), CheckConstraint("status IN ('available', 'reserved', 'sold')", name="ck_plots_status"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project: Mapped[str] = mapped_column(String(160), index=True)
    plot_number: Mapped[str] = mapped_column(String(40))
    area_sqft: Mapped[float] = mapped_column(Float)
    rate_per_sqft: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), default="available")
    listing_state: Mapped[str] = mapped_column(String(20), default="published", server_default="published")
    x: Mapped[float] = mapped_column(Float, default=0)
    y: Mapped[float] = mapped_column(Float, default=0)
    location_label: Mapped[str] = mapped_column(String(255), default="", server_default="")
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    corner_plot: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    corner_premium_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    guidance_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    width_ft: Mapped[float | None] = mapped_column(Float, nullable=True)
    depth_ft: Mapped[float | None] = mapped_column(Float, nullable=True)
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    amenities: Mapped[str] = mapped_column(Text, default="", server_default="")
    approval_details: Mapped[str] = mapped_column(Text, default="", server_default="")
    facing: Mapped[str] = mapped_column(String(20), default="", server_default="")
    road_width_ft: Mapped[float | None] = mapped_column(Float, nullable=True)

class PlotPhoto(Base):
    __tablename__ = "plot_photos"
    plot_id: Mapped[int] = mapped_column(ForeignKey("plots.id", ondelete="CASCADE"), primary_key=True)
    jpeg: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)


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

class SupportMessage(Base):
    __tablename__ = "support_messages"
    __table_args__ = (UniqueConstraint("customer_id", "client_id", name="uq_support_customer_client"), Index("ix_support_customer_id_id", "customer_id", "id"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    sender: Mapped[str] = mapped_column(String(16))
    client_id: Mapped[str] = mapped_column(String(80))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class ChatState(Base):
    __tablename__ = "chat_states"
    customer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    quick: Mapped[list] = mapped_column(JSON, default=lambda: list(MENU))


password_hash = PasswordHash.recommended()
_dummy_password_hash = password_hash.hash(secrets.token_urlsafe(32))
bearer = HTTPBearer(auto_error=False)
logger = logging.getLogger("uvicorn.error")
def rate_key(request: Request):
    return client_ip(request, settings.client_ip_mode, settings.trusted_hosts)

limiter = Limiter(key_func=rate_key, storage_uri=settings.redis_url or "memory://")

@asynccontextmanager
async def lifespan(application):
    bootstrap_admin()
    seed_business_settings()
    start_sheets_worker()
    try:
        yield
    finally:
        stop_sheets_worker()

app = FastAPI(lifespan=lifespan, title="Real Estate Platform API", version="0.1.0", docs_url="/docs" if settings.app_env != "production" else None, redoc_url=None, openapi_url="/openapi.json" if settings.app_env != "production" else None)
app.include_router(finance_router)
app.state.limiter = limiter
if settings.app_env == "production":
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[host.strip() for host in settings.trusted_hosts.split(",") if host.strip()])
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

@app.exception_handler(RequestValidationError)
async def safe_validation_error(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"detail": [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]})
app.add_middleware(CORSMiddleware, allow_origins=[v.strip() for v in settings.cors_origins.split(",") if v.strip()], allow_credentials=True, allow_methods=["GET", "POST", "PATCH", "DELETE"], allow_headers=["Authorization", "Content-Type", "X-Session-Panel"])

@app.middleware("http")
async def request_observability(request: Request, call_next):
    request_id = uuid.uuid4().hex
    started = time.monotonic()
    response = None
    try:
        if request.url.path.startswith("/api/") and request.method != "OPTIONS":
            def permitted():
                key = rate_key(request)
                if not limiter._limiter.hit(parse("300/minute"), "api-global", key):
                    return False
                if request.url.path.startswith(("/api/finance/", "/api/calculators/")):
                    return limiter._limiter.hit(parse("60/minute"), "finance", key)
                return True
            try:
                allowed = await run_in_threadpool(permitted)
            except Exception:
                logger.error("rate_limit_unavailable request_id=%s", request_id)
                response = JSONResponse(status_code=503, content={"detail":"Service temporarily unavailable"}, headers={"Cache-Control":"no-store"})
            else:
                if not allowed:
                    response = JSONResponse(status_code=429, content={"detail":"Too many requests. Try again shortly."}, headers={"Retry-After":"60","Cache-Control":"no-store"})
        if response is None:
            response = await call_next(request)
    except Exception as exc:
        logger.error("request_failed request_id=%s error_type=%s", request_id, type(exc).__name__)
        raise
    response.headers["X-Request-ID"] = request_id
    route = request.scope.get("route")
    route_name = getattr(route, "path", "unmatched")
    logger.info("request_id=%s method=%s route=%s status=%s duration_ms=%.1f", request_id, request.method, route_name, response.status_code, (time.monotonic()-started)*1000)
    return response

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

class PlotRequestLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if scope["type"] != "http" or not ((scope["method"] == "POST" and path == "/api/plots") or (scope["method"] == "PATCH" and path.startswith("/api/admin/plots/"))):
            return await self.app(scope, receive, send)
        limit = 3_000_000
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] != "http.request":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > limit:
                return await JSONResponse(status_code=413, content={"detail": "Plot request exceeds 3 MB"}, headers={"Cache-Control":"no-store", "X-Content-Type-Options":"nosniff"})(scope, receive, send)
            body.extend(chunk)
            if not message.get("more_body", False):
                break
        delivered = False
        async def replay():
            nonlocal delivered
            if delivered:
                return await receive()
            delivered = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}
        return await self.app(scope, replay, send)


app.add_middleware(PlotRequestLimit)


def db_session() -> Generator[Session, None, None]:
    db = SessionLocal()
    try: yield db
    finally: db.close()

class RegisterIn(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=160)
    password: str = Field(min_length=12, max_length=128)
    @field_validator("full_name")
    @classmethod
    def validate_name(cls, value):
        return clean_name(value)
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
    consent: bool = False
    name: str = Field(min_length=2, max_length=160)
    phone: str = Field(min_length=7, max_length=30)
    email: EmailStr | None = None
    interest: str = Field(default="", max_length=255)
    _validate_name = field_validator("name")(clean_name)
    _validate_phone = field_validator("phone")(clean_phone)
class PublicEnquiryIn(LeadIn):
    website: str = Field(default="", max_length=200)

class PlotIn(BaseModel):
    project: str = Field(min_length=2, max_length=160)
    plot_number: str = Field(min_length=1, max_length=40)
    area_sqft: float = Field(gt=0, le=1000000)
    rate_per_sqft: float = Field(gt=0, le=1000000)
    status: str = Field(default="available", pattern="^(available|reserved|sold)$")
    x: float = Field(default=0, ge=0, le=10000)
    y: float = Field(default=0, ge=0, le=10000)
    location_label: str = Field(default="", max_length=255)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    corner_plot: bool | None = None
    corner_premium_percent: float | None = Field(default=None, ge=0, le=100)
    guidance_value: float | None = Field(default=None, gt=0, le=1e12)
    width_ft: float | None = Field(default=None, gt=0, le=100000)
    depth_ft: float | None = Field(default=None, gt=0, le=100000)
    description: str = Field(default="", max_length=3000)
    amenities: str = Field(default="", max_length=1500)
    approval_details: str = Field(default="", max_length=1500)
    facing: Literal["", "North", "South", "East", "West", "North-East", "North-West", "South-East", "South-West"] = ""
    road_width_ft: float | None = Field(default=None, gt=0, le=1000)
    photo_data: str | None = Field(default=None, max_length=2796204, exclude=True)
    remove_photo: bool = Field(default=False, exclude=True)

    @field_validator("project", "plot_number")
    @classmethod
    def normalize_plot_label(cls, value):
        value = value.strip()
        if not value or any(ord(c) < 32 for c in value):
            raise ValueError("Enter a valid project and plot number")
        return value

    @model_validator(mode="after")
    def validate_location(self):
        if self.photo_data and self.remove_photo:
            raise ValueError("Choose either replace or remove photo")
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("Provide both latitude and longitude, or leave both blank")
        if (self.width_ft is None) != (self.depth_ft is None):
            raise ValueError("Provide both plot dimensions, or leave both blank")
        if self.width_ft is not None and abs(self.width_ft * self.depth_ft - self.area_sqft) > self.area_sqft * 0.05:
            raise ValueError("Rectangular dimensions must agree with the listed area within 5%")
        if self.corner_plot is not True and self.corner_premium_percent not in (None, 0):
            raise ValueError("Set corner plot before specifying a premium")
        return self
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

PANEL_ROLES = {"admin", "customer", "partner"}

def browser_panel(request: Request) -> str | None:
    panel = request.headers.get("X-Session-Panel")
    if panel is None:
        return None
    if panel not in PANEL_ROLES:
        raise HTTPException(status_code=400, detail="Invalid session panel")
    origin = request.headers.get("Origin")
    if origin and request.method not in {"GET", "HEAD", "OPTIONS"}:
        allowed = {v.strip().rstrip("/") for v in settings.cors_origins.split(",") if v.strip()}
        try:
            parsed = urlsplit(origin)
        except ValueError:
            raise HTTPException(status_code=403, detail="Untrusted request origin") from None
        same_host = parsed.netloc == request.headers.get("host") and parsed.scheme in ({"https"} if settings.app_env == "production" else {"http", "https"})
        if origin.rstrip("/") not in allowed and not same_host:
            raise HTTPException(status_code=403, detail="Untrusted request origin")
    return panel

def cookie_name(panel: str) -> str:
    return "metroestate_" + panel + "_session"

def digest_session(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()

def issue_token(user: User, db: Session) -> str:
    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=settings.access_token_minutes)
    jti = secrets.token_urlsafe(32)
    db.execute(delete(AuthSession).where(AuthSession.expires_at <= now))
    db.add(AuthSession(digest=digest_session(jti), user_id=user.id, expires_at=expires))
    db.commit()
    return jwt.encode({"sub": str(user.id), "role": user.role, "pv": password_version(user), "jti": jti, "iat": now, "exp": expires}, settings.app_secret_key, algorithm="HS256")

def authentication_result(user: User, db: Session, request: Request, response: Response):
    panel = browser_panel(request)
    if panel and panel != user.role:
        raise HTTPException(status_code=403, detail="This account cannot access this panel. Use the appropriate account page.")
    token = issue_token(user, db)
    profile = {"id": user.id, "email": user.email, "full_name": user.full_name, "role": user.role}
    if panel:
        prior = request.cookies.get(cookie_name(panel))
        if prior:
            try:
                payload = jwt.decode(prior, settings.app_secret_key, algorithms=["HS256"], options={"require": ["jti", "exp", "sub"]})
                db.execute(delete(AuthSession).where(AuthSession.digest == digest_session(payload["jti"]), AuthSession.user_id == int(payload["sub"])))
                db.commit()
            except (jwt.InvalidTokenError, ValueError, TypeError, KeyError):
                pass
        response.set_cookie(cookie_name(panel), token, httponly=True, secure=settings.app_env == "production", samesite="strict", path="/api", max_age=settings.access_token_minutes * 60)
        return {"user": profile, "session": "cookie"}
    return {"access_token": token, "token_type": "bearer", "user": profile}

def current_user(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer), db: Session = Depends(db_session)) -> User:
    panel = browser_panel(request)
    token = credentials.credentials if credentials else request.cookies.get(cookie_name(panel)) if panel else None
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        payload = jwt.decode(token, settings.app_secret_key, algorithms=["HS256"], options={"require": ["sub", "iat", "exp", "jti"]})
        user_id = int(payload["sub"])
        digest = digest_session(payload["jti"])
    except (jwt.InvalidTokenError, KeyError, ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    session = db.scalar(select(AuthSession).where(AuthSession.digest == digest, AuthSession.user_id == user_id, AuthSession.expires_at > datetime.now(timezone.utc)))
    user = db.get(User, user_id)
    if not session or not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Session unavailable; please sign in again")
    if not hmac.compare_digest(str(payload.get("pv", "")), password_version(user)) or payload.get("role") != user.role:
        raise HTTPException(status_code=401, detail="Session expired; please sign in again")
    if panel and panel != user.role:
        raise HTTPException(status_code=403, detail="This account cannot access this panel")
    request.state.auth_session_digest = digest
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
def register(data: RegisterIn, request: Request, response: Response, db: Session = Depends(db_session)):
    if browser_panel(request) not in {None, "customer"}:
        raise HTTPException(status_code=403, detail="Registration is for customers only")
    if db.scalar(select(User).where(User.email == data.email.lower())): raise HTTPException(status_code=409, detail="Email already registered")
    user = User(email=data.email.lower(), full_name=data.full_name.strip(), password_hash=password_hash.hash(data.password), role=Role.customer.value)
    db.add(user)
    try: db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Email already registered")
    db.refresh(user)
    return authentication_result(user, db, request, response)

@app.post("/api/auth/login")
@limiter.limit("10/minute")
def login(data: LoginIn, request: Request, response: Response, db: Session = Depends(db_session)):
    browser_panel(request)
    key = account_key(settings.app_secret_key, str(data.email))
    if not limiter._limiter.hit(parse("10/15 minutes"), "login-account", key):
        raise HTTPException(status_code=429, detail="Too many login attempts. Try again in 15 minutes.", headers={"Retry-After":"900"})
    user = db.scalar(select(User).where(User.email == data.email.lower()))
    valid = password_hash.verify(data.password, user.password_hash if user else _dummy_password_hash)
    if not user or not valid or not user.is_active: raise HTTPException(status_code=401, detail="Incorrect email or password")
    return authentication_result(user, db, request, response)

@app.post("/api/auth/password")
@limiter.limit("5/minute")
def change_password(data: PasswordChangeIn, request: Request, db: Session = Depends(db_session), user: User = Depends(current_user)):
    if not password_hash.verify(data.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    if data.new_password == data.current_password:
        raise HTTPException(status_code=422, detail="Choose a different password")
    user.password_hash = password_hash.hash(data.new_password)
    db.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
    db.commit()
    return {"message": "Password updated. Sign in again."}

@app.post("/api/auth/logout")
def logout(request: Request, response: Response, db: Session = Depends(db_session), user: User = Depends(current_user)):
    db.execute(delete(AuthSession).where(AuthSession.digest == request.state.auth_session_digest))
    db.commit()
    panel = browser_panel(request)
    if panel:
        response.delete_cookie(cookie_name(panel), path="/api", secure=settings.app_env == "production", httponly=True, samesite="strict")
    return {"message": "Signed out"}


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
    return [{"id": x.id, "name": x.name, "phone": x.phone, "email": x.email, "interest": x.interest, "status": x.status, "request_type": x.request_type, "source": x.source, "preferred_time": x.preferred_time, "contact_consent_at": x.contact_consent_at.isoformat() if x.contact_consent_at else None} for x in db.scalars(query)]

@app.post("/api/leads", status_code=201)
@limiter.limit("20/minute")
def create_lead(data: LeadIn, request: Request, db: Session = Depends(db_session), user: User = Depends(require_roles(Role.admin, Role.customer))):
    if user.role == Role.customer.value and not data.consent:
        raise HTTPException(status_code=422, detail="Confirm contact consent before submitting")
    lead = Lead(owner_id=user.id, name=user.full_name if user.role == Role.customer.value else data.name.strip(), phone=data.phone.strip(),
                email=user.email if user.role == Role.customer.value else (str(data.email).lower() if data.email else None), interest=data.interest.strip(),
                source="customer" if user.role == Role.customer.value else "admin", contact_consent_at=datetime.now(timezone.utc) if data.consent else None, contact_consent_version="2026-11-12" if data.consent else None)
    db.add(lead)
    db.flush()
    if user.role == Role.admin.value:
        write_admin_audit(db, user, "lead.created", str(lead.id), None, {"status":lead.status,"source":lead.source})
    db.commit()
    db.refresh(lead)
    return {"id": lead.id, "status": lead.status}

@app.post("/api/public/enquiries", status_code=201)
@limiter.limit("5/minute")
def public_enquiry(data: PublicEnquiryIn, request: Request, db: Session = Depends(db_session)):
    if not data.consent or data.website:
        raise HTTPException(status_code=422, detail="Please confirm consent and submit a valid enquiry")
    owner = db.scalar(select(User).where(User.role == Role.admin.value, User.is_active.is_(True)).order_by(User.id).limit(1))
    if not owner:
        raise HTTPException(status_code=503, detail="Enquiries temporarily unavailable")
    item = Lead(owner_id=owner.id, name=data.name, phone=data.phone, email=str(data.email).lower() if data.email else None, interest=data.interest.strip(), source="public", contact_consent_at=datetime.now(timezone.utc), contact_consent_version="2026-11-12")
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
    lead = db.scalar(select(Lead).where(Lead.id == lead_id).with_for_update())
    if not lead: raise HTTPException(status_code=404, detail="Lead not found")
    before = {"status": lead.status}
    lead.status = new_status
    write_admin_audit(db, user, "lead.status_changed", str(lead.id), before, {"status":new_status})
    db.commit()
    return {"id": lead.id, "status": lead.status}

def plot_view(p: Plot, has_photo: bool) -> dict:
    return {"id": p.id, "project": p.project, "plot_number": p.plot_number,
            "area_sqft": p.area_sqft, "rate_per_sqft": p.rate_per_sqft, "status": p.status, "listing_state":p.listing_state,
            "x": p.x, "y": p.y, "location_label": p.location_label, "latitude": p.latitude, "longitude": p.longitude,
            "corner_plot": p.corner_plot, "corner_premium_percent": p.corner_premium_percent,
            "guidance_value": p.guidance_value, "width_ft": p.width_ft, "depth_ft": p.depth_ft,
            "description": p.description, "amenities": p.amenities, "approval_details": p.approval_details,
            "facing": p.facing, "road_width_ft": p.road_width_ft,
            "photo_url": f"/api/plots/{p.id}/photo" if has_photo else None}


def prepare_photo(data: PlotIn):
    from .plot_media import normalize_photo
    if data.photo_data is not None:
        try:
            return normalize_photo(data.photo_data)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
    return None


def apply_plot_photo(db: Session, plot_id: int, data: PlotIn, jpeg):
    old = db.get(PlotPhoto, plot_id)
    if jpeg is not None:
        if old:
            old.jpeg = jpeg
        else:
            db.add(PlotPhoto(plot_id=plot_id, jpeg=jpeg))
        return True
    if data.remove_photo and old:
        db.delete(old)
        return False
    return old is not None


@app.get("/api/plots")
def list_plots(db: Session = Depends(db_session)):
    photo_ids = set(db.scalars(select(PlotPhoto.plot_id)))
    return [plot_view(p, p.id in photo_ids) for p in db.scalars(select(Plot).where(Plot.listing_state=="published").order_by(Plot.project, Plot.plot_number))]


@app.get("/api/admin/plots")
def admin_list_plots(db: Session = Depends(db_session), user: User = Depends(require_roles(Role.admin))):
    photo_ids = set(db.scalars(select(PlotPhoto.plot_id)))
    return [plot_view(p,p.id in photo_ids) for p in db.scalars(select(Plot).order_by(Plot.project,Plot.plot_number,Plot.id))]

def lock_plot_inventory(db):
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(52403182)"))

def assert_unique_plot_identity(db, project, number, exclude_id=None):
    query=select(Plot.id).where(Plot.listing_state!="archived",func.lower(func.trim(Plot.project))==project.strip().lower(),func.lower(func.trim(Plot.plot_number))==number.strip().lower())
    if exclude_id is not None:
        query=query.where(Plot.id!=exclude_id)
    if db.scalar(query.limit(1)) is not None:
        raise HTTPException(status_code=409,detail="Plot identifier already exists. Resolve conflicting inventory in the Layout panel first.")

@app.get("/api/plots/{plot_id}/photo")
def get_plot_photo(plot_id: int, db: Session = Depends(db_session)):
    photo = db.get(PlotPhoto, plot_id)
    if not photo:
        raise HTTPException(status_code=404, detail="Property photo unavailable")
    return Response(photo.jpeg, media_type="image/jpeg", headers={"Content-Disposition": "inline", "Cache-Control": "no-store"})


@app.post("/api/plots", status_code=201)
def add_plot(data: PlotIn, db: Session = Depends(db_session), user: User = Depends(require_roles(Role.admin))):
    jpeg = prepare_photo(data)
    lock_plot_inventory(db)
    assert_unique_plot_identity(db,data.project,data.plot_number)
    plot = Plot(**data.model_dump())
    db.add(plot)
    try:
        db.flush()
        has_photo = apply_plot_photo(db, plot.id, data, jpeg)
        write_admin_audit(db, user, "plot.created", str(plot.id), None, plot_audit_view(plot, has_photo))
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="A plot with this project and plot number already exists") from None
    db.refresh(plot)
    return plot_view(plot, has_photo)


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
    db.add(item)
    db.flush()
    write_admin_audit(db, user, "review.requested", str(item.id), None, {"customer_id":customer.id,"channel":item.channel,"consent_confirmed":True})
    db.commit()
    db.refresh(item)
    return {"id": item.id, "status": item.status, "delivery": "not_configured"}

@app.get("/api/admin/review-requests")
def admin_review_requests(db: Session = Depends(db_session), user: User = Depends(require_roles(Role.admin))):
    stmt = select(ReviewRequest, User).join(User, User.id == ReviewRequest.customer_id).order_by(ReviewRequest.id.desc()).limit(100)
    return [{"id": item.id, "customer_id": customer.id, "customer_name": customer.full_name,
             "channel": item.channel, "status": item.status, "consent_confirmed": item.consent_confirmed,
             "created_at": item.created_at.isoformat(), "delivery": "not_configured"} for item, customer in db.execute(stmt)]


@app.get("/api/customer/portal")
def customer_portal(db: Session = Depends(db_session), user: User = Depends(require_roles(Role.customer))):
    refs = list(db.scalars(select(Referral).where(Referral.referrer_id == user.id)))
    return {"profile": {"id": user.id, "name": user.full_name, "email": user.email}, "referrals": [{"id": r.id, "status": r.status} for r in refs], "message": "Booking, payment and document records require verified business-system integration"}

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
    before_values: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after_values: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

DEFAULT_SETTINGS = {"registration_percent":"2.0","stamp_duty_percent":"5.0","other_charges":"25000","referral_reward_amount":"0"}

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
class PlotUpdateIn(PlotIn):
    pass

def write_admin_audit(db: Session, actor: User, action: str, target: str = "", before=None, after=None):
    """Stage audit changes in the caller's transaction. Never store credentials or chat text."""
    db.add(AdminAudit(actor_id=actor.id, action=action, target=target[:160], before_values=before, after_values=after))

def plot_audit_view(plot, has_photo):
    return {"project":plot.project,"plot_number":plot.plot_number,"area_sqft":plot.area_sqft,"rate_per_sqft":plot.rate_per_sqft,"status":plot.status,"corner_plot":plot.corner_plot,"corner_premium_percent":plot.corner_premium_percent,"guidance_value":plot.guidance_value,"has_photo":has_photo,"listing_state":plot.listing_state}

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
    before = admin_get_settings(db, user)
    for key,value in data.model_dump().items():
        row = db.scalar(select(AppSetting).where(AppSetting.key==key).with_for_update())
        if row: row.value=str(value)
        else: db.add(AppSetting(key=key,value=str(value)))
    write_admin_audit(db,user,"settings.updated",",".join(data.model_fields_set),before,data.model_dump())
    db.commit()
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
    try:
        db.flush()
        write_admin_audit(db,user,"user.created",f"{created.id}:{created.role}",None,{"role":created.role,"is_active":created.is_active})
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Email already registered") from None
    db.refresh(created)
    return {"id":created.id,"email":created.email,"full_name":created.full_name,"role":created.role,"is_active":created.is_active}

@app.patch("/api/admin/users/{user_id}/active")
def admin_set_user_active(user_id:int, active:bool, db:Session=Depends(db_session), user:User=Depends(require_roles(Role.admin))):
    target=db.scalar(select(User).where(User.id==user_id).with_for_update())
    if not target: raise HTTPException(status_code=404,detail="User not found")
    if target.id==user.id and not active: raise HTTPException(status_code=400,detail="You cannot deactivate your own account")
    before = {"is_active":target.is_active}
    target.is_active=active
    if not active:
        db.execute(delete(AuthSession).where(AuthSession.user_id == target.id))
    write_admin_audit(db,user,"user.activation_changed",f"{target.id}:{active}",before,{"is_active":active})
    db.commit()
    return {"id":target.id,"is_active":target.is_active}

@app.get("/api/admin/referrals")
def admin_list_referrals(db:Session=Depends(db_session), user:User=Depends(require_roles(Role.admin))):
    return [{"id":x.id,"referrer_id":x.referrer_id,"referred_name":x.referred_name,"referred_phone":x.referred_phone,"status":x.status,"reward_amount":x.reward_amount} for x in db.scalars(select(Referral).order_by(Referral.id.desc()))]

@app.patch("/api/admin/referrals/{referral_id}")
def admin_decide_referral(referral_id:int, data:ReferralDecisionIn, db:Session=Depends(db_session), user:User=Depends(require_roles(Role.admin))):
    item=db.scalar(select(Referral).where(Referral.id==referral_id).with_for_update())
    if not item: raise HTTPException(status_code=404,detail="Referral not found")
    if item.status == "paid" and (data.status != "paid" or data.reward_amount != item.reward_amount):
        raise HTTPException(status_code=409, detail="Paid reward records cannot be changed")
    if data.status == "paid" and (item.status not in {"approved", "paid"} or data.reward_amount != item.reward_amount):
        raise HTTPException(status_code=409, detail="Approve the reward before recording its verified payment")
    if item.status == "paid":
        return {"id":item.id,"status":item.status,"reward_amount":item.reward_amount}
    before = {"status":item.status,"reward_amount":item.reward_amount}
    item.status=data.status
    item.reward_amount=data.reward_amount
    write_admin_audit(db,user,"referral.decision",f"{item.id}:{data.status}",before,{"status":item.status,"reward_amount":item.reward_amount})
    db.commit()
    return {"id":item.id,"status":item.status,"reward_amount":item.reward_amount}

@app.patch("/api/admin/plots/{plot_id}")
def admin_update_plot(plot_id:int, data:PlotUpdateIn, db:Session=Depends(db_session), user:User=Depends(require_roles(Role.admin))):
    jpeg = prepare_photo(data)
    lock_plot_inventory(db)
    item=db.scalar(select(Plot).where(Plot.id==plot_id).with_for_update())
    if not item: raise HTTPException(status_code=404,detail="Plot not found")
    if item.listing_state=="archived": raise HTTPException(status_code=409,detail="Archived duplicate records are retained for audit and cannot be edited")
    before = plot_audit_view(item, db.get(PlotPhoto,item.id) is not None)
    assert_unique_plot_identity(db,data.project,data.plot_number,exclude_id=item.id)
    for key,value in data.model_dump(exclude_unset=True).items(): setattr(item,key,value)
    item.listing_state="published"
    try:
        has_photo = apply_plot_photo(db, item.id, data, jpeg)
        write_admin_audit(db,user,"plot.updated",str(item.id),before,plot_audit_view(item,has_photo))
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409,detail="A plot with this project and plot number already exists") from None
    return plot_view(item, has_photo)

@app.post("/api/admin/plots/{plot_id}/archive")
def archive_duplicate_plot(plot_id:int, db:Session=Depends(db_session), user:User=Depends(require_roles(Role.admin))):
    lock_plot_inventory(db)
    item=db.scalar(select(Plot).where(Plot.id==plot_id).with_for_update())
    if not item:
        raise HTTPException(status_code=404,detail="Plot not found")
    has_photo=db.get(PlotPhoto,item.id) is not None
    if item.listing_state=="archived":
        return plot_view(item,has_photo)
    if item.listing_state!="conflict":
        raise HTTPException(status_code=409,detail="Only conflicting legacy records can be archived here")
    before=plot_audit_view(item,has_photo)
    item.listing_state="archived"
    write_admin_audit(db,user,"plot.conflict_archived",str(item.id),before,plot_audit_view(item,has_photo))
    db.commit()
    return plot_view(item,has_photo)

@app.get("/api/admin/audit")
def admin_audit_log(limit:int=Query(default=100,ge=1,le=500), db:Session=Depends(db_session), user:User=Depends(require_roles(Role.admin))):
    return [{"id":x.id,"actor_id":x.actor_id,"action":x.action,"target":x.target,"before":x.before_values,"after":x.after_values,"created_at":x.created_at.isoformat()} for x in db.scalars(select(AdminAudit).order_by(AdminAudit.id.desc()).limit(limit))]

def seed_business_settings():
    with SessionLocal() as db:
        for key,value in DEFAULT_SETTINGS.items():
            if not db.scalar(select(AppSetting).where(AppSetting.key==key)): db.add(AppSetting(key=key,value=value))
        db.commit()


# Private messaging. Optional AI receives consented chat context and public inventory.
def ai_configured() -> bool:
    key = settings.anthropic_api_key if settings.ai_provider == "anthropic" else settings.openai_api_key
    return bool(settings.ai_enabled and key.get_secret_value())


def plot_tool_handlers() -> dict:
    def row(p):
        return {"project": p.project, "plot_number": p.plot_number, "area_sqft": p.area_sqft,
                "rate_per_sqft": p.rate_per_sqft, "base_price_inr": round(p.area_sqft * p.rate_per_sqft, 2),
                "location": p.location_label, "corner_plot": p.corner_plot, "corner_premium_percent": p.corner_premium_percent}
    def search_plots(project=None, max_budget_inr=None, min_area_sqft=None, max_area_sqft=None):
        from math import isfinite
        stmt = select(Plot).where(Plot.status == "available", Plot.listing_state == "published")
        if project:
            stmt = stmt.where(Plot.project.ilike("%" + str(project)[:80].replace("%", "").replace("_", "") + "%"))
        for column, raw, lower in ((Plot.area_sqft, min_area_sqft, True), (Plot.area_sqft, max_area_sqft, False), (Plot.area_sqft * Plot.rate_per_sqft, max_budget_inr, False)):
            if raw is not None:
                n = float(raw)
                if not isfinite(n) or n < 0:
                    raise ValueError("Use finite nonnegative search bounds")
                stmt = stmt.where(column >= n if lower else column <= n)
        with SessionLocal() as tool_db:
            rows = list(tool_db.scalars(stmt.order_by(Plot.area_sqft * Plot.rate_per_sqft, Plot.id).limit(8)))
            return {"count": len(rows), "plots": [row(p) for p in rows], "note": "Base prices exclude corner premiums, taxes and registration."}
    def plot_base_price(project, plot_number):
        with SessionLocal() as tool_db:
            p = tool_db.scalar(select(Plot).where(Plot.status == "available", Plot.listing_state == "published", func.lower(Plot.project) == str(project).strip().lower(), func.lower(Plot.plot_number) == str(plot_number).strip().lower()).order_by(Plot.id).limit(1))
            return row(p) if p else {"error": "No available matching plot."}
    return {"search_plots": search_plots, "plot_base_price": plot_base_price}


class SupportModeIn(BaseModel):
    mode: Literal["assistant", "team"]


def support_mode(db: Session, customer_id: int, state: ChatState | None = None) -> str:
    state = state if state is not None else db.get(ChatState, customer_id)
    explicit = (state.state or {}).get("support_mode") if state else None
    if explicit in {"assistant", "team"}:
        return explicit
    # Existing conversations with a human reply remain human-owned after upgrade.
    previous = db.scalar(select(SupportMessage.id).where(
        SupportMessage.customer_id == customer_id, SupportMessage.sender == "admin"
    ).limit(1))
    return "team" if previous is not None else "assistant"


def set_support_mode(db: Session, customer_id: int, mode: str) -> ChatState:
    state = db.get(ChatState, customer_id)
    if state is None:
        state = ChatState(customer_id=customer_id, state={}, quick=[])
        db.add(state)
    if (state.state or {}).get("support_mode") != mode:
        boundary = db.scalar(select(func.max(SupportMessage.id)).where(SupportMessage.customer_id == customer_id)) or 0
        state.state = {"support_mode": mode, "assistant_context_after_id": boundary}
        state.quick = list(MENU) if mode == "assistant" else []
    return state


class SupportMessageIn(BaseModel):
    body: str = Field(min_length=1, max_length=2000)
    client_id: str = Field(min_length=16, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    ai_consent: bool = False

    @field_validator("body")
    @classmethod
    def validate_body(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(ch) < 32 and ch not in "\n\t\r" for ch in value):
            raise ValueError("Enter a valid message")
        return value


def support_view(message: SupportMessage) -> dict:
    return {"id": message.id, "sender": message.sender, "body": message.body, "created_at": message.created_at.isoformat()}


def support_history(db: Session, customer_id: int, before_id: int | None, limit: int) -> list[dict]:
    stmt = select(SupportMessage).where(SupportMessage.customer_id == customer_id)
    if before_id is not None:
        stmt = stmt.where(SupportMessage.id < before_id)
    rows = list(db.scalars(stmt.order_by(SupportMessage.id.desc()).limit(limit)))
    return [support_view(row) for row in reversed(rows)]


def save_support(db: Session, customer_id: int, sender: str, data: SupportMessageIn) -> dict:
    # Serialize per-customer changes in PostgreSQL, including retries across replicas.
    customer = db.scalar(select(User).where(User.id == customer_id).with_for_update())
    if not customer or not customer.is_active:
        raise HTTPException(status_code=409, detail="Customer account is inactive")
    key = sender + ":" + data.client_id
    existing = db.scalar(select(SupportMessage).where(SupportMessage.customer_id == customer_id, SupportMessage.client_id == key))
    if existing:
        if existing.body != data.body:
            raise HTTPException(status_code=409, detail="Message request ID already used")
        return support_view(existing)
    item = SupportMessage(customer_id=customer_id, sender=sender, client_id=key, body=data.body)
    db.add(item)
    ai_job = None
    assistant = None
    try:
        db.flush()
        if sender == "admin":
            set_support_mode(db, customer_id, "team")
        if sender == "customer" and support_mode(db, customer_id) == "assistant":
            state = db.get(ChatState, customer_id)
            if state is None:
                state = ChatState(customer_id=customer_id, state={}, quick=list(MENU))
                db.add(state)
            projects = list(db.scalars(select(Plot.project).where(Plot.status == "available", Plot.listing_state == "published").distinct().order_by(Plot.project).limit(20)))
            available = db.scalar(select(func.count(Plot.id)).where(Plot.status == "available", Plot.listing_state == "published")) or 0
            context_boundary = (state.state or {}).get("assistant_context_after_id", 0)
            flow_state = {k: v for k, v in (state.state or {}).items() if k not in {"support_mode", "assistant_context_after_id"}}
            reply = respond(data.body, customer.full_name, flow_state, projects, available)
            # Collect bounded public data and consented context; persist a fallback first.
            if data.ai_consent and ai_configured() and not reply.lead and reply.state == flow_state:
                if settings.ai_provider == "anthropic":
                    from .agent_ai import converse, agent_eligible
                    if agent_eligible(data.body):
                        recent = list(db.scalars(select(SupportMessage).where(SupportMessage.customer_id == customer_id, SupportMessage.id > context_boundary, SupportMessage.sender.in_(["customer", "assistant"])).order_by(SupportMessage.id.desc()).limit(14)))
                        history = [{"sender": x.sender, "body": x.body} for x in reversed(recent)]
                        ai_job = (converse, (history, plot_tool_handlers(), settings.anthropic_api_key.get_secret_value(), settings.anthropic_model, settings.assistant_name))
                else:
                    from .ai_support import answer_question, eligible_question
                    if eligible_question(data.body):
                        inventory = [{"project": p.project, "plot": p.plot_number, "area_sqft": p.area_sqft, "rate_per_sqft": p.rate_per_sqft, "location": p.location_label} for p in db.scalars(select(Plot).where(Plot.status == "available", Plot.listing_state == "published").order_by(Plot.id).limit(40))]
                        ai_job = (answer_question, (data.body, inventory, settings.openai_api_key.get_secret_value(), settings.openai_model))
            state.state, state.quick = {**reply.state, "support_mode": "assistant", "assistant_context_after_id": context_boundary}, reply.quick
            assistant = SupportMessage(customer_id=customer_id, sender="assistant", client_id="assistant:" + data.client_id, body=reply.text)
            db.add(assistant)
            if reply.lead:
                data_lead = reply.lead
                db.add(Lead(owner_id=customer_id, name=customer.full_name, email=customer.email,
                            phone=data_lead["phone"], interest=data_lead["interest"][:255], request_type=data_lead["request_type"],
                            preferred_time=data_lead["preferred_time"], source="chat",
                            contact_consent_at=datetime.now(timezone.utc) if data_lead["consent"] else None,
                            contact_consent_version="2026-11-12" if data_lead["consent"] else None))

        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(SupportMessage).where(SupportMessage.customer_id == customer_id, SupportMessage.client_id == key))
        if not existing or existing.body != data.body:
            raise HTTPException(status_code=409, detail="Message request conflict")
        return support_view(existing)
    result = support_view(item)
    if ai_job and assistant is not None:
        assistant_id = assistant.id
        # Release the user lock and pool connection before provider network I/O.
        db.close()
        try:
            fn, args = ai_job
            ai_text = fn(*args)
        except Exception as exc:
            logger.warning("ai_advisor_failed error_type=%s", type(exc).__name__)
            ai_text = None
        if ai_text:
            if reply.state.get("flow"):
                ai_text += "\n\nBack to your request: " + reply.text.split("Back to your request: ")[-1].split("Let's continue. ")[-1]
            with SessionLocal() as update_db:
                active = update_db.scalar(select(User).where(User.id == customer_id).with_for_update())
                latest = update_db.scalar(select(func.max(SupportMessage.id)).where(SupportMessage.customer_id == customer_id))
                saved = update_db.get(SupportMessage, assistant_id)
                if active and active.is_active and latest == assistant_id and saved and saved.body == reply.text and support_mode(update_db, customer_id) == "assistant":
                    saved.body = ai_text[:2000]
                    update_db.commit()
    return result


@app.get("/api/customer/chat-state")
def customer_chat_state(db: Session = Depends(db_session), user: User = Depends(require_roles(Role.customer))):
    state = db.get(ChatState, user.id)
    mode = support_mode(db, user.id, state)
    return {"mode": mode, "quick": (state.quick if state else MENU) if mode == "assistant" else [],
            "in_progress": mode == "assistant" and bool(state and (state.state or {}).get("flow")),
            "ai_available": mode == "assistant" and ai_configured()}


@app.post("/api/customer/chat-mode")
@limiter.limit("20/minute")
def customer_chat_mode(request: Request, data: SupportModeIn, db: Session = Depends(db_session), user: User = Depends(require_roles(Role.customer))):
    customer = db.scalar(select(User).where(User.id == user.id).with_for_update())
    if not customer or not customer.is_active:
        raise HTTPException(status_code=409, detail="Customer account is inactive")
    set_support_mode(db, user.id, data.mode)
    db.commit()
    return {"mode": data.mode}


@app.get("/api/customer/messages")
def customer_messages(before_id: int | None = Query(default=None, gt=0), limit: int = Query(default=50, ge=1, le=100), db: Session = Depends(db_session), user: User = Depends(require_roles(Role.customer))):
    return support_history(db, user.id, before_id, limit)


@app.post("/api/customer/messages", status_code=201)
@limiter.limit("20/minute")
def send_customer_message(request: Request, data: SupportMessageIn, db: Session = Depends(db_session), user: User = Depends(require_roles(Role.customer))):
    return save_support(db, user.id, "customer", data)


@app.get("/api/admin/messages")
def admin_message_threads(limit: int = Query(default=100, ge=1, le=200), db: Session = Depends(db_session), user: User = Depends(require_roles(Role.admin))):
    latest = select(SupportMessage.customer_id, func.max(SupportMessage.id).label("latest_id")).where(SupportMessage.sender != "assistant").group_by(SupportMessage.customer_id).subquery()
    stmt = select(User, SupportMessage).join(latest, latest.c.customer_id == User.id).join(SupportMessage, SupportMessage.id == latest.c.latest_id).where(User.role == Role.customer.value).order_by(SupportMessage.id.desc()).limit(limit)
    result = []
    for customer, message in db.execute(stmt):
        mode = support_mode(db, customer.id)
        result.append({"customer_id": customer.id, "name": customer.full_name, "email": customer.email,
                       "last_message": message.body, "updated_at": message.created_at.isoformat(),
                       "last_message_id": message.id, "mode": mode,
                       "awaiting_reply": message.sender == "customer",
                       "team_pending": mode == "team" and message.sender == "customer"})
    return result


@app.get("/api/admin/message-notifications")
def admin_message_notifications(db: Session = Depends(db_session), user: User = Depends(require_roles(Role.admin))):
    latest = select(SupportMessage.customer_id, func.max(SupportMessage.id).label("latest_id")).where(
        SupportMessage.sender != "assistant").group_by(SupportMessage.customer_id).subquery()
    explicit = ChatState.state["support_mode"].as_string()
    previous_admin = select(func.count(SupportMessage.id)).where(
        SupportMessage.customer_id == User.id, SupportMessage.sender == "admin").correlate(User).scalar_subquery()
    pending = (select(User.id.label("customer_id"), User.full_name.label("name"),
                      SupportMessage.id.label("message_id"), SupportMessage.created_at)
               .select_from(User).join(latest, latest.c.customer_id == User.id)
               .join(SupportMessage, SupportMessage.id == latest.c.latest_id)
               .outerjoin(ChatState, ChatState.customer_id == User.id)
               .where(User.role == Role.customer.value, User.is_active.is_(True),
                      SupportMessage.sender == "customer",
                      (explicit == "team") | (explicit.is_(None) & (previous_admin > 0))))
    count = db.scalar(select(func.count()).select_from(pending.subquery())) or 0
    rows = db.execute(pending.order_by(SupportMessage.id.desc()).limit(50))
    return {"pending_count": count, "items": [{"customer_id": row.customer_id, "name": row.name,
             "message_id": row.message_id, "received_at": row.created_at.isoformat()} for row in rows]}


def support_customer(db: Session, customer_id: int):
    customer = db.get(User, customer_id)
    exists = db.scalar(select(SupportMessage.id).where(SupportMessage.customer_id == customer_id).limit(1))
    if not customer or customer.role != Role.customer.value or not exists:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return customer


@app.get("/api/admin/messages/{customer_id}")
def admin_message_history(customer_id: int, before_id: int | None = Query(default=None, gt=0), limit: int = Query(default=50, ge=1, le=100), db: Session = Depends(db_session), user: User = Depends(require_roles(Role.admin))):
    support_customer(db, customer_id)
    return support_history(db, customer_id, before_id, limit)


@app.post("/api/admin/messages/{customer_id}", status_code=201)
@limiter.limit("30/minute")
def send_admin_message(request: Request, customer_id: int, data: SupportMessageIn, db: Session = Depends(db_session), user: User = Depends(require_roles(Role.admin))):
    customer = support_customer(db, customer_id)
    if not customer.is_active:
        raise HTTPException(status_code=409, detail="Customer account is inactive")
    return save_support(db, customer_id, "admin", data)


# Durable reporting change markers. These contain no customer data or credentials.
class SheetOutbox(Base):
    __tablename__ = "sheet_outbox"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class SheetSyncStatus(Base):
    __tablename__ = "sheet_sync_status"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sheet_id: Mapped[str] = mapped_column(String(200), default="")
    last_success: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str] = mapped_column(String(200), default="")


@event.listens_for(Session, "before_flush")
def queue_reporting_changes(db, flush_context, instances):
    watched = (Lead, SupportMessage, Plot, PlotPhoto, Referral, User)
    if any(isinstance(obj, watched) for obj in db.new.union(db.deleted)) or any(isinstance(obj, watched) and db.is_modified(obj, include_collections=False) for obj in db.dirty):
        db.add(SheetOutbox())


class ReportConfigIn(BaseModel):
    email: EmailStr | None = None
    sheet_id: str = Field(default="", max_length=300)
    current_password: str = Field(min_length=1, max_length=128)

    @field_validator("sheet_id")
    @classmethod
    def validate_sheet(cls, value):
        value = value.strip()
        if value.startswith("https://docs.google.com/spreadsheets/d/"):
            value = value.split("/d/", 1)[1].split("/", 1)[0]
        if value and not re.fullmatch(r"[A-Za-z0-9_-]{10,200}", value):
            raise ValueError("Use a valid Google spreadsheet ID or URL")
        return value

class ReportSendIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    authorized_recipient: bool = False

def reporting_config(db: Session):
    destination = db.get(ReportDestination, 1)
    return {"email": destination.email if destination else "", "sheet_id": destination.sheet_id if destination else settings.google_sheet_id}

@app.get("/api/admin/reports/config")
def get_report_config(db: Session = Depends(db_session), user: User = Depends(require_roles(Role.admin))):
    from .report_mail import email_ready
    return {**reporting_config(db), "email_ready": email_ready(settings), "ai_available": ai_configured()}

@app.patch("/api/admin/reports/config")
@limiter.limit("5/minute")
def update_report_config(request: Request, data: ReportConfigIn, db: Session = Depends(db_session), user: User = Depends(require_roles(Role.admin))):
    if not password_hash.verify(data.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    # Serialize destination changes with the reporting worker to avoid writes
    # to a former destination after the change has been acknowledged.
    if db.bind.dialect.name == "postgresql" and not db.execute(text("SELECT pg_try_advisory_xact_lock(52403181)")).scalar():
        raise HTTPException(status_code=409, detail="Synchronization is in progress. Try again shortly.")
    destination = db.get(ReportDestination, 1)
    if destination is None:
        destination = ReportDestination(id=1, email="", sheet_id="")
        db.add(destination)
    destination.email = str(data.email) if data.email else ""
    destination.sheet_id = data.sheet_id
    db.add(SheetOutbox())
    db.add(AdminAudit(actor_id=user.id, action="reporting.destination_changed", target="private reporting destinations"))
    db.commit()
    return reporting_config(db)

@app.post("/api/admin/reports/email")
@limiter.limit("3/minute")
def email_report(request: Request, data: ReportSendIn, db: Session = Depends(db_session), user: User = Depends(require_roles(Role.admin))):
    from .report_mail import email_ready, send_report
    from .sheet_sync import snapshot, export_zip
    if not password_hash.verify(data.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    if not data.authorized_recipient:
        raise HTTPException(status_code=422, detail="Confirm the recipient is authorized to receive private records")
    destination = reporting_config(db)
    if not destination["email"] or not email_ready(settings):
        raise HTTPException(status_code=409, detail="Configure the reporting email and SMTP provider first")
    archive = export_zip(snapshot(db, external=True, full_customer_data=settings.external_full_customer_reports))
    if len(archive) > 10_000_000:
        raise HTTPException(status_code=413, detail="Report is too large for email. Download it privately instead.")
    try:
        send_report(settings, destination["email"], archive)
    except Exception:
        raise HTTPException(status_code=503, detail="Email provider unavailable. Download the private report instead.") from None
    db.add(AdminAudit(actor_id=user.id, action="reporting.email_sent", target="configured recipient"))
    db.commit()
    return {"message": "Report accepted by email provider. Check the recipient inbox."}

@app.get("/api/admin/sheets/status")
def sheets_status(db: Session = Depends(db_session), user: User = Depends(require_roles(Role.admin))):
    from .sheet_sync import configured
    sheet_id = reporting_config(db)["sheet_id"]
    state = db.get(SheetSyncStatus, 1)
    same = bool(state and state.sheet_id == sheet_id)
    enabled = configured(settings, sheet_id)
    return {"configured": enabled, "pending_changes": db.scalar(select(func.count(SheetOutbox.id))) or 0,
            "last_success": state.last_success.isoformat() if same and state.last_success else None,
            "last_error": state.last_error if same else "", "sheet_url": "https://docs.google.com/spreadsheets/d/" + sheet_id if enabled else None}

@app.post("/api/admin/sheets/sync", status_code=202)
@limiter.limit("5/minute")
def request_sheet_sync(request: Request, db: Session = Depends(db_session), user: User = Depends(require_roles(Role.admin))):
    from .sheet_sync import configured
    if not configured(settings, reporting_config(db)["sheet_id"]):
        raise HTTPException(status_code=409, detail="Google Sheets is not configured")
    db.add(SheetOutbox())
    db.commit()
    return {"message": "Sync queued. Check status for completion."}


@app.get("/api/admin/sheets/export")
@limiter.limit("5/minute")
def export_reporting_data(request: Request, db: Session = Depends(db_session), user: User = Depends(require_roles(Role.admin))):
    from .sheet_sync import snapshot, export_zip
    return Response(export_zip(snapshot(db)), media_type="application/zip", headers={"Content-Disposition": 'attachment; filename="metroestate-private-report.zip"'})


def start_sheets_worker():
    from . import main
    from .sheet_sync import start_worker
    app.state.sheet_stop, app.state.sheet_worker = start_worker(main)


def stop_sheets_worker():
    stop = getattr(app.state, "sheet_stop", None)
    worker = getattr(app.state, "sheet_worker", None)
    if stop:
        stop.set()
    if worker:
        worker.join(timeout=2)
