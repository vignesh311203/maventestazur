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
