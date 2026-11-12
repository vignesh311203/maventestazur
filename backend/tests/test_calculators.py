"""API checks using isolated SQLite and real signed login tokens."""
import os
import tempfile
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

_test_dir = tempfile.TemporaryDirectory()
os.environ["DATABASE_URL"] = "sqlite:///" + _test_dir.name + "/import.db"
os.environ["APP_ENV"] = "development"
os.environ["REDIS_URL"] = ""
os.environ["APP_SECRET_KEY"] = "test-only-secret-not-used-in-production-123456789"
os.environ["BOOTSTRAP_ADMIN_EMAIL"] = ""
os.environ["BOOTSTRAP_ADMIN_PASSWORD"] = ""

from app import main as m

@pytest.fixture
def client(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    m.Base.metadata.create_all(engine)
    monkeypatch.setattr(m, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False))
    monkeypatch.setattr(m.settings, "bootstrap_admin_email", "admin@example.com")
    monkeypatch.setattr(m.settings, "bootstrap_admin_password", "TestAdminPass123!")
    m.limiter.reset()
    try:
        with TestClient(m.app) as c:
            yield c
    finally:
        engine.dispose()

def login(client, email, password):
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": "Bearer " + response.json()["access_token"]}

def register(client, email):
    response = client.post("/api/auth/register", json={"email": email, "full_name": "Test Customer", "password": "TestCustomerPass123!"})
    assert response.status_code == 201, response.text
    return {"Authorization": "Bearer " + response.json()["access_token"]}

def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.headers["x-content-type-options"] == "nosniff"

def test_emi_zero_interest(client):
    response = client.post("/api/calculators/emi", params={"principal": 120000, "annual_rate_percent": 0, "months": 12})
    assert response.status_code == 200
    assert response.json()["monthly_emi"] == 10000

def test_growth(client):
    response = client.post("/api/calculators/growth", params={"initial_value": 100000, "annual_growth_percent": 10, "years": 2})
    assert response.status_code == 200
    assert response.json()["future_value"] == 121000

def test_plot_cost(client):
    response = client.post("/api/calculators/plot-cost", params={"area_sqft": 1000, "rate_per_sqft": 1000, "registration_percent": 5, "stamp_duty_percent": 5, "other_charges": 10000})
    assert response.status_code == 200
    assert response.json()["total"] == 1110000

def test_admin_login(client):
    assert client.post("/api/auth/login", json={"email": "admin@example.com", "password": "wrong"}).status_code == 401
    headers = login(client, "admin@example.com", "TestAdminPass123!")
    assert client.get("/api/me", headers=headers).json()["role"] == "admin"
    assert client.get("/api/admin/lead-notifications", headers=headers).status_code == 200

def test_lead_inbox_lifecycle(client):
    admin = login(client, "admin@example.com", "TestAdminPass123!")
    customer = register(client, "customer@example.com")
    other = register(client, "other@example.com")
    assert client.get("/api/admin/lead-notifications").status_code == 401
    assert client.get("/api/admin/lead-notifications", headers=customer).status_code == 403
    response = client.post("/api/leads", headers=customer, json={"name": "Test Customer", "phone": "9999999999", "email": None, "interest": "Plot"})
    assert response.status_code == 201, response.text
    lead_id = response.json()["id"]
    assert response.json()["status"] == "new"
    assert [x["id"] for x in client.get("/api/leads", headers=customer).json()] == [lead_id]
    assert client.get("/api/leads", headers=other).json() == []
    assert [x["id"] for x in client.get("/api/leads", headers=admin).json()] == [lead_id]
    inbox = client.get("/api/admin/lead-notifications", headers=admin)
    assert inbox.status_code == 200
    assert [x["id"] for x in inbox.json()] == [lead_id]
    assert client.patch(f"/api/leads/{lead_id}/status", headers=other, params={"new_status": "contacted"}).status_code == 404
    assert client.patch(f"/api/leads/{lead_id}/status", headers=admin, params={"new_status": "invalid"}).status_code == 422
    assert client.patch(f"/api/leads/{lead_id}/status", headers=admin, params={"new_status": "contacted"}).status_code == 200
    assert client.get("/api/admin/lead-notifications", headers=admin).json() == []
    assert client.get("/api/leads", headers=admin).json()[0]["status"] == "contacted"
    assert client.patch(f"/api/leads/{lead_id}/status", headers=admin, params={"new_status": "new"}).status_code == 200
    assert [x["id"] for x in client.get("/api/admin/lead-notifications", headers=admin).json()] == [lead_id]

def test_no_external_messaging(client):
    assert client.get("/api/whatsapp/webhook").status_code == 404
    assert client.post("/api/whatsapp/webhook", json={}).status_code == 404
    assert client.get("/api/admin/whatsapp/status").status_code == 404
    assert not any(key.startswith("whatsapp_") for key in m.Settings.model_fields)
    admin = login(client, "admin@example.com", "TestAdminPass123!")
    assert client.post("/api/reviews/requests", headers=admin, json={"customer_id": 1, "channel": "whatsapp", "consent_confirmed": True}).status_code == 422


def make_user(client, admin, email, role):
    password = "FixturePassword123!"
    response = client.post("/api/admin/users", headers=admin, json={"email": email, "full_name": "Fixture User", "password": password, "role": role})
    assert response.status_code == 201, response.text
    return response.json()["id"], login(client, email, password)


def test_plot_inventory_and_permissions(client):
    admin = login(client, "admin@example.com", "TestAdminPass123!")
    customer = register(client, "plotcustomer@example.com")
    plot = {"project": "Test Enclave", "plot_number": "A1", "area_sqft": 1200, "rate_per_sqft": 3500, "status": "available", "x": 20, "y": 30}
    assert client.post("/api/plots", headers=customer, json=plot).status_code == 403
    response = client.post("/api/plots", headers=admin, json=plot)
    assert response.status_code == 201, response.text
    plot_id = response.json()["id"]
    assert client.get("/api/plots").json()[0]["plot_number"] == "A1"
    update = {**plot, "status": "reserved"}
    assert client.patch(f"/api/admin/plots/{plot_id}", headers=admin, json=update).status_code == 200
    assert client.get("/api/plots").json()[0]["status"] == "reserved"
    assert client.post("/api/plots", headers=admin, json={**plot, "area_sqft": -1}).status_code == 422


def test_partner_referral_and_approval(client):
    admin = login(client, "admin@example.com", "TestAdminPass123!")
    _, partner = make_user(client, admin, "partner@example.com", "partner")
    _, other = make_user(client, admin, "otherpartner@example.com", "partner")
    customer = register(client, "refcustomer@example.com")
    data = {"referred_name": "Referral Test", "referred_phone": "9999999999"}
    assert client.post("/api/partners/referrals", headers=customer, json=data).status_code == 403
    response = client.post("/api/partners/referrals", headers=partner, json=data)
    assert response.status_code == 201, response.text
    rid = response.json()["id"]
    assert client.get("/api/partners/referrals", headers=other).json() == []
    assert client.get("/api/partners/referrals", headers=partner).json()[0]["status"] == "submitted"
    decision = {"status": "approved", "reward_amount": 1000}
    assert client.patch(f"/api/admin/referrals/{rid}", headers=partner, json=decision).status_code == 403
    assert client.patch(f"/api/admin/referrals/{rid}", headers=admin, json=decision).status_code == 200
    assert client.get("/api/partners/referrals", headers=partner).json()[0]["reward_amount"] == 1000
    assert client.get("/api/admin/referrals", headers=admin).json()[0]["status"] == "approved"


def test_review_consent_and_customer_portal(client):
    admin = login(client, "admin@example.com", "TestAdminPass123!")
    customer = register(client, "portalcustomer@example.com")
    customer_id = client.get("/api/me", headers=customer).json()["id"]
    profile = client.get("/api/customer/portal", headers=customer)
    assert profile.status_code == 200
    assert profile.json()["profile"]["id"] == customer_id
    payload = {"customer_id": customer_id, "channel": "manual", "consent_confirmed": False}
    assert client.post("/api/reviews/requests", headers=admin, json=payload).status_code == 400
    payload["consent_confirmed"] = True
    assert client.post("/api/reviews/requests", headers=customer, json=payload).status_code == 403
    response = client.post("/api/reviews/requests", headers=admin, json=payload)
    assert response.status_code == 201, response.text
    assert response.json()["delivery"] == "not_configured"
    assert client.post("/api/reviews/requests", headers=admin, json={**payload, "customer_id": 1}).status_code == 404


def test_admin_settings_audit_and_deactivation(client):
    admin = login(client, "admin@example.com", "TestAdminPass123!")
    uid, agent = make_user(client, admin, "agent@example.com", "agent")
    payload = {"registration_percent": 4, "stamp_duty_percent": 5, "other_charges": 20000, "referral_reward_amount": 500}
    assert client.patch("/api/admin/settings", headers=agent, json=payload).status_code == 403
    assert client.patch("/api/admin/settings", headers=admin, json=payload).status_code == 200
    assert client.get("/api/settings/calculator").json()["registration_percent"] == 4
    assert any(a["action"] == "settings.updated" for a in client.get("/api/admin/audit", headers=admin).json())
    assert client.get("/api/admin/audit", headers=agent).status_code == 403
    assert client.patch(f"/api/admin/users/{uid}/active", headers=admin, params={"active": False}).status_code == 200
    assert client.get("/api/me", headers=agent).status_code == 401


def test_jwt_validation_and_lead_staff_only(client):
    import jwt
    from datetime import datetime, timedelta, timezone
    customer = register(client, "securitycustomer@example.com")
    me = client.get("/api/me", headers=customer)
    uid = me.json()["id"]
    assert me.headers["cache-control"] == "no-store"
    now = datetime.now(timezone.utc)
    forged = jwt.encode({"sub": str(uid), "iat": now, "exp": now + timedelta(minutes=5)}, "wrong-secret-not-the-server-key-123456789", algorithm="HS256")
    expired = jwt.encode({"sub": str(uid), "iat": now - timedelta(hours=1), "exp": now - timedelta(minutes=1)}, m.settings.app_secret_key, algorithm="HS256")
    missing_exp = jwt.encode({"sub": str(uid), "iat": now}, m.settings.app_secret_key, algorithm="HS256")
    for token in (forged, expired, missing_exp):
        assert client.get("/api/me", headers={"Authorization": "Bearer " + token}).status_code == 401
    lead = client.post("/api/leads", headers=customer, json={"name": "Security Test", "phone": "9999999999"})
    assert lead.status_code == 201
    assert client.patch(f"/api/leads/{lead.json()['id']}/status", headers=customer, params={"new_status": "won"}).status_code == 403
    assert client.get("/ready").json()["status"] == "ready"


def test_login_rate_limit(client):
    for _ in range(10):
        assert client.post("/api/auth/login", json={"email": "unknown@example.com", "password": "incorrect"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": "unknown@example.com", "password": "incorrect"}).status_code == 429


@pytest.mark.parametrize("legacy", [False, True])
def test_versioned_migration(tmp_path, monkeypatch, legacy):
    from alembic import command
    from alembic.config import Config
    from pathlib import Path
    from sqlalchemy import inspect, text
    url = "sqlite:///" + str(tmp_path / "migration.db")
    monkeypatch.setenv("DATABASE_URL", url)
    engine = create_engine(url)
    try:
        if legacy:
            m.Base.metadata.create_all(engine)
            with engine.begin() as connection:
                connection.execute(text("CREATE TABLE legacy_provider_history (id INTEGER PRIMARY KEY, message TEXT)"))
                connection.execute(text("INSERT INTO legacy_provider_history VALUES (1, 'preserve')"))
                connection.execute(text("INSERT INTO app_settings (key, value, updated_at) VALUES ('registration_percent', '7', CURRENT_TIMESTAMP)"))
        cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        command.upgrade(cfg, "head")
        command.upgrade(cfg, "head")
        assert set(m.Base.metadata.tables).issubset(inspect(engine).get_table_names())
        with engine.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0001_baseline"
            if legacy:
                assert connection.execute(text("SELECT message FROM legacy_provider_history")).scalar_one() == "preserve"
                assert connection.execute(text("SELECT value FROM app_settings WHERE key='registration_percent'")).scalar_one() == "7"
    finally:
        engine.dispose()


def test_invalid_input_and_agent_review_scope(client):
    admin = login(client, "admin@example.com", "TestAdminPass123!")
    _, agent = make_user(client, admin, "reviewagent@example.com", "agent")
    customer = register(client, "reviewtarget@example.com")
    customer_id = client.get("/api/me", headers=customer).json()["id"]
    request = {"customer_id": customer_id, "channel": "manual", "consent_confirmed": True}
    assert client.post("/api/reviews/requests", headers=agent, json=request).status_code == 404
    for bad in ({"name": "  ", "phone": "9999999999"}, {"name": "Test Name", "phone": "not-a-phone"}):
        assert client.post("/api/leads", headers=agent, json=bad).status_code == 422
    assert client.post("/api/leads", headers=agent, json={"name": "Test Name", "phone": "+91 99999 99999", "email": "reviewtarget@example.com"}).status_code == 201
    assert client.post("/api/reviews/requests", headers=agent, json=request).status_code == 201
    assert client.post("/api/calculators/emi", params={"principal": 100000, "annual_rate_percent": 1e100, "months": 12}).status_code == 422
