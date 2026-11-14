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
os.environ["GOOGLE_SHEETS_ENABLED"] = "false"
os.environ["APP_SECRET_KEY"] = "test-only-secret-not-used-in-production-123456789"
os.environ["BOOTSTRAP_ADMIN_EMAIL"] = ""
os.environ["BOOTSTRAP_ADMIN_PASSWORD"] = ""

from app import main as m

@pytest.fixture
def client(monkeypatch):
    pg_url = os.environ.get("TEST_POSTGRES_URL")
    engine = create_engine(pg_url) if pg_url else create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    if pg_url:
        m.Base.metadata.drop_all(engine)
    m.Base.metadata.create_all(engine)
    monkeypatch.setattr(m, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False))
    monkeypatch.setattr(m, "engine", engine)
    monkeypatch.setattr(m.settings, "bootstrap_admin_email", "admin@example.com")
    monkeypatch.setattr(m.settings, "bootstrap_admin_password", "TestAdminPass123!")
    m.limiter.reset()
    try:
        with TestClient(m.app) as c:
            yield c
    finally:
        if pg_url:
            m.Base.metadata.drop_all(engine)
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
    response = client.post("/api/leads", headers=customer, json={"name": "Test Customer", "phone": "9999999999", "email": None, "interest": "Plot", "consent": True})
    assert response.status_code == 201, response.text
    lead_id = response.json()["id"]
    assert response.json()["status"] == "new"
    assert client.get("/api/leads", headers=customer).status_code == 403
    own = client.get("/api/customer/enquiries", headers=customer).json()
    assert [x["id"] for x in own] == [lead_id]
    assert not {"name", "phone", "email"}.intersection(own[0])
    assert client.get("/api/leads", headers=other).status_code == 403
    assert client.get("/api/customer/enquiries", headers=other).json() == []
    assert [x["id"] for x in client.get("/api/leads", headers=admin).json()] == [lead_id]
    inbox = client.get("/api/admin/lead-notifications", headers=admin)
    assert inbox.status_code == 200
    assert [x["id"] for x in inbox.json()] == [lead_id]
    assert client.patch(f"/api/leads/{lead_id}/status", headers=other, params={"new_status": "contacted"}).status_code == 403
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
    lead = client.post("/api/leads", headers=customer, json={"name": "Security Test", "phone": "9999999999", "consent": True})
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
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0009_security_hardening"
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
    assert client.post("/api/reviews/requests", headers=agent, json=request).status_code == 403
    for bad in ({"name": "  ", "phone": "9999999999"}, {"name": "Test Name", "phone": "not-a-phone"}):
        assert client.post("/api/leads", headers=admin, json=bad).status_code == 422
    assert client.post("/api/leads", headers=agent, json={"name": "Test Name", "phone": "+91 99999 99999", "email": "reviewtarget@example.com"}).status_code == 403
    assert client.post("/api/reviews/requests", headers=agent, json=request).status_code == 403
    assert client.post("/api/calculators/emi", params={"principal": 100000, "annual_rate_percent": 1e100, "months": 12}).status_code == 422


def test_public_enquiry_privacy_and_consent(client):
    admin = login(client, "admin@example.com", "TestAdminPass123!")
    _, agent = make_user(client, admin, "privateagent@example.com", "agent")
    _, partner = make_user(client, admin, "privatepartner@example.com", "partner")
    customer = register(client, "privacycustomer@example.com")
    data = {"name": "Private Enquirer", "phone": "9999999999", "email": "private@example.com", "interest": "Plot", "consent": True}
    assert client.post("/api/public/enquiries", json={**data, "consent": False}).status_code == 422
    assert client.post("/api/public/enquiries", json={**data, "website": "spam"}).status_code == 422
    response = client.post("/api/public/enquiries", json=data)
    assert response.status_code == 201, response.text
    assert set(response.json()) == {"message"}
    assert client.get("/api/leads").status_code == 401
    for headers in (agent, partner, customer):
        for path in ("/api/leads", "/api/admin/users", "/api/admin/lead-notifications", "/api/admin/referrals", "/api/admin/audit"):
            assert client.get(path, headers=headers).status_code == 403
    lead = client.get("/api/leads", headers=admin).json()[0]
    assert lead["email"] == "private@example.com"
    assert lead["contact_consent_at"]
    assert client.get("/api/customer/enquiries", headers=customer).json() == []
    assert client.post("/api/leads", headers=customer, json=data | {"consent": False}).status_code == 422
    assert client.post("/api/leads", headers=customer, json=data).status_code == 201
    own = client.get("/api/customer/enquiries", headers=customer).json()
    assert len(own) == 1
    assert set(own[0]) == {"id", "interest", "status", "created_at"}
    assert client.get("/api/customer/enquiries", headers=partner).status_code == 403
    assert client.get("/api/customer/enquiries", headers=admin).status_code == 403


def test_password_rotation_revokes_old_session(client):
    admin = login(client, "admin@example.com", "TestAdminPass123!")
    payload = {"current_password": "wrong", "new_password": "NewPassword123456!"}
    assert client.post("/api/auth/password", headers=admin, json=payload).status_code == 400
    payload["current_password"] = "TestAdminPass123!"
    assert client.post("/api/auth/password", headers=admin, json=payload).status_code == 200
    assert client.get("/api/me", headers=admin).status_code == 401
    assert client.post("/api/auth/login", json={"email": "admin@example.com", "password": "TestAdminPass123!"}).status_code == 401
    updated = login(client, "admin@example.com", payload["new_password"])
    assert client.get("/api/admin/users", headers=updated).status_code == 200


def test_validation_does_not_echo_password(client):
    response = client.post("/api/auth/register", json={"email":"valid@example.com","full_name":"Test","password":"secret"})
    assert response.status_code == 422
    assert "secret" not in response.text
    assert all("input" not in error for error in response.json()["detail"])


def test_private_support_message_lifecycle(client):
    admin = login(client, "admin@example.com", "TestAdminPass123!")
    customer = register(client, "chat-customer@example.com")
    other = register(client, "other-chat@example.com")
    _, partner = make_user(client, admin, "chat-partner@example.com", "partner")
    _, agent = make_user(client, admin, "chat-agent@example.com", "agent")
    customer_id = client.get("/api/me", headers=customer).json()["id"]
    payload = {"body": "What is the plot price?", "client_id": "test-client-message-001"}
    assert client.get("/api/customer/messages").status_code == 401
    assert client.post("/api/customer/messages", json=payload).status_code == 401
    assert client.get("/api/customer/messages", headers=customer).json() == []
    first = client.post("/api/customer/messages", headers=customer, json=payload)
    assert first.status_code == 201, first.text
    duplicate = client.post("/api/customer/messages", headers=customer, json=payload)
    assert duplicate.status_code == 201
    assert duplicate.json()["id"] == first.json()["id"]
    assert client.post("/api/customer/messages", headers=customer, json=payload | {"body": "Changed"}).status_code == 409
    messages = client.get("/api/customer/messages", headers=customer).json()
    assert [m["sender"] for m in messages] == ["customer", "assistant"]
    assert "calculators" in messages[1]["body"]
    assert client.get("/api/customer/messages", headers=other).json() == []
    for headers in (customer, other, partner, agent):
        assert client.get("/api/admin/messages", headers=headers).status_code == 403
        assert client.get(f"/api/admin/messages/{customer_id}", headers=headers).status_code == 403
        assert client.post(f"/api/admin/messages/{customer_id}", headers=headers, json=payload).status_code == 403
    assert client.get("/api/customer/messages", headers=partner).status_code == 403
    assert client.get("/api/customer/messages", headers=admin).status_code == 403
    assert client.get("/api/admin/messages", headers=admin).json()[0]["awaiting_reply"] is True
    assert client.get(f"/api/admin/messages/{customer_id}", headers=admin).json() == messages
    reply = {"body": "Please share your preferred project.", "client_id": "test-admin-message-001"}
    assert client.post(f"/api/admin/messages/{customer_id}", headers=admin, json=reply).status_code == 201
    history = client.get("/api/customer/messages", headers=customer)
    assert history.headers["cache-control"] == "no-store"
    assert [m["sender"] for m in history.json()] == ["customer", "assistant", "admin"]
    assert history.json()[-1]["body"] == reply["body"]
    assert client.get("/api/admin/messages", headers=admin).json()[0]["awaiting_reply"] is False
    page = client.get("/api/customer/messages?limit=1", headers=customer).json()
    assert len(page) == 1
    older = client.get(f"/api/customer/messages?before_id={page[0]['id']}&limit=2", headers=customer).json()
    assert len(older) == 2
    for body in (" ", "x" * 2001):
        assert client.post("/api/customer/messages", headers=customer, json={"body": body, "client_id": "test-invalid-message-001"}).status_code == 422


def test_support_reply_guidance_and_unavailable_account(client):
    admin = login(client, "admin@example.com", "TestAdminPass123!")
    customer = register(client, "chat-visit@example.com")
    customer_id = client.get("/api/me", headers=customer).json()["id"]
    assert client.get(f"/api/admin/messages/{customer_id}", headers=admin).status_code == 404
    payload = {"body": "Can I book a site visit?", "client_id": "test-visit-message-001"}
    assert client.post("/api/customer/messages", headers=customer, json=payload).status_code == 201
    assert "confirmed only" in client.get("/api/customer/messages", headers=customer).json()[-1]["body"]
    assert client.patch(f"/api/admin/users/{customer_id}/active?active=false", headers=admin).status_code == 200
    assert client.get("/api/customer/messages", headers=customer).status_code == 401
    assert client.post(f"/api/admin/messages/{customer_id}", headers=admin, json=payload).status_code == 409



def test_support_quick_actions(client):
    admin = login(client, "admin@example.com", "TestAdminPass123!")
    customer = register(client, "quick-actions@example.com")
    other = register(client, "quick-other@example.com")
    customer_id = client.get("/api/me", headers=customer).json()["id"]
    steps = [
        ("Book Site Visit", "Which project"),
        ("Test Enclave", "When would suit"),
        ("What is the plot price?", "Back to your request"),
        ("Saturday 11 am", "best phone number"),
        ("+91 99999 99999", "May our property team"),
        ("Yes, contact me", "site-visit request has been saved"),
        ("Request Brochure", "Which project"),
        ("Not sure yet", "brochure request has been saved"),
        ("Call Me Back", "When would suit"),
        ("Tomorrow evening", "best phone number"),
        ("Chat only", "callback request has been saved"),
    ]
    for index, (body, expected) in enumerate(steps):
        payload = {"body": body, "client_id": f"quick-action-test-{index:03d}"}
        response = client.post("/api/customer/messages", headers=customer, json=payload)
        assert response.status_code == 201, response.text
        # Retry the exact same final message to prove no duplicate business request.
        if index == 5:
            assert client.post("/api/customer/messages", headers=customer, json=payload).json()["id"] == response.json()["id"]
        history = client.get("/api/customer/messages", headers=customer).json()
        assert history[-2]["body"] == body
        assert expected in history[-1]["body"], history[-1]
    assert len(history) == 22
    assert client.get("/api/customer/chat-state", headers=customer).json()["in_progress"] is False
    assert client.get("/api/customer/messages", headers=other).json() == []
    assert client.get("/api/customer/chat-state", headers=other).json()["in_progress"] is False
    assert client.get(f"/api/admin/messages/{customer_id}", headers=other).status_code == 403
    assert client.get(f"/api/admin/messages/{customer_id}", headers=admin).json() == history
    leads = client.get("/api/leads", headers=admin).json()
    assert {x["request_type"] for x in leads} == {"site_visit", "callback", "brochure"}
    assert len(leads) == 3
    visit = next(x for x in leads if x["request_type"] == "site_visit")
    assert visit["source"] == "chat" and visit["preferred_time"] == "Saturday 11 am"
    assert visit["contact_consent_at"] and visit["phone"] == "+91 99999 99999"
    callback = next(x for x in leads if x["request_type"] == "callback")
    assert callback["phone"] == "" and callback["contact_consent_at"] is None
    assert len(client.get("/api/admin/lead-notifications", headers=admin).json()) == 3
    assert len(client.get("/api/customer/enquiries", headers=customer).json()) == 3


def test_conversation_interruptions_and_cancellation():
    from app.chatbot import respond, phone_from
    first = respond("Hi, I want to book a site visit", "Test Customer", projects=["Known Project"])
    assert first.state["flow"] == "site_visit"
    price = respond("What is the price?", "Test Customer", first.state)
    assert price.state == first.state and "Back to your request" in price.text
    greeting = respond("hello", "Test Customer", price.state)
    assert greeting.state == first.state
    project = respond("Known Project", "Test Customer", greeting.state, ["Known Project"])
    assert project.state["slots"]["project"] == "Known Project"
    human = respond("Can I speak to a real person?", "Test Customer", project.state)
    assert human.state == project.state and human.lead is None
    cancelled = respond("cancel", "Test Customer", human.state)
    assert cancelled.state == {} and cancelled.lead is None
    assert phone_from("2026-11-12") is None
    assert phone_from("My date is 12/12/2026") is None
    assert phone_from("+91 98765 43210") == "+91 98765 43210"


def test_sheet_reporting_privacy_queue_and_retry(client, monkeypatch):
    import io, zipfile
    from pydantic import SecretStr
    from sqlalchemy import select, func
    from app.sheet_sync import process_once, snapshot
    from app.sheets_mirror import TABS, SheetsError
    admin = login(client, "admin@example.com", "TestAdminPass123!")
    customer = register(client, "sheetcustomer@example.com")
    other = register(client, "sheetother@example.com")
    assert client.get("/api/admin/sheets/status").status_code == 401
    for endpoint in ("status", "export"):
        assert client.get("/api/admin/sheets/" + endpoint, headers=customer).status_code == 403
    assert client.post("/api/admin/sheets/sync", headers=other).status_code == 403
    assert client.get("/api/admin/sheets/status", headers=admin).json()["configured"] is False
    assert client.post("/api/admin/sheets/sync", headers=admin).status_code == 409
    assert client.post("/api/customer/messages", headers=customer, json={"body": "=1+1", "client_id": "sheet-message-test-001"}).status_code == 201
    assert client.post("/api/plots", headers=admin, json={"project":"Reporting Test","plot_number":"P1","area_sqft":1200,"rate_per_sqft":3500}).status_code == 201
    _, partner = make_user(client, admin, "sheetpartner@example.com", "partner")
    ref = client.post("/api/partners/referrals", headers=partner, json={"referred_name":"Sheet Referral","referred_phone":"9999999999"})
    assert ref.status_code == 201
    rid = ref.json()["id"]
    assert client.patch(f"/api/admin/referrals/{rid}", headers=admin, json={"status":"paid","reward_amount":1000}).status_code == 409
    assert client.patch(f"/api/admin/referrals/{rid}", headers=admin, json={"status":"approved","reward_amount":1000}).status_code == 200
    assert client.patch(f"/api/admin/referrals/{rid}", headers=admin, json={"status":"paid","reward_amount":1000}).status_code == 200
    assert client.patch(f"/api/admin/referrals/{rid}", headers=admin, json={"status":"rejected","reward_amount":0}).status_code == 409
    response = client.get("/api/admin/sheets/export", headers=admin)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert set(archive.namelist()) == {name + ".csv" for name in TABS}
        assert "'=1+1" in archive.read("Messages.csv").decode("utf-8-sig")
        assert "password_hash" not in archive.read("Customers.csv").decode("utf-8-sig")
        assert "sheetcustomer@example.com" in archive.read("Customers.csv").decode("utf-8-sig")
    with m.SessionLocal() as db:
        data = snapshot(db)
        assert data["Partners"][0]["rewards_paid"] == 1000
        assert data["Plots"][0]["total_price"] == 4200000
        pending = db.scalar(select(func.count(m.SheetOutbox.id)))
        assert pending > 0
        db.add(m.Plot(project="Rollback Test", plot_number="R1", area_sqft=100, rate_per_sqft=100))
        db.flush()
        db.rollback()
        assert db.scalar(select(func.count(m.SheetOutbox.id))) == pending
    monkeypatch.setattr(m.settings, "google_sheets_enabled", True)
    monkeypatch.setattr(m.settings, "google_sheet_id", "test-spreadsheet-id")
    monkeypatch.setattr(m.settings, "google_service_account_json", SecretStr("mock-only"))
    class FailedMirror:
        def __init__(self, *args): pass
        def sync(self, data): raise SheetsError("Google Sheets HTTP 503")
        def close(self): pass
    assert process_once(m, FailedMirror) == "failed"
    assert client.get("/api/admin/sheets/status", headers=admin).json()["pending_changes"] == pending
    assert client.get("/api/admin/sheets/status", headers=admin).json()["last_error"] == "Google Sheets HTTP 503"
    class GoodMirror:
        def __init__(self, *args): pass
        def sync(self, data):
            assert set(data) == set(TABS)
            assert data["Messages"][0]["body"] == ""
            assert len(data["Customers"]) == 2
        def close(self): pass
    assert process_once(m, GoodMirror) == "synced"
    status = client.get("/api/admin/sheets/status", headers=admin).json()
    assert status["pending_changes"] == 0 and status["last_success"] and not status["last_error"]
    assert process_once(m, GoodMirror) == "idle"
    assert client.post("/api/admin/sheets/sync", headers=admin).status_code == 202


def test_sheets_mirror_uses_raw_deterministic_ranges():
    from urllib.parse import unquote
    from app.sheets_mirror import SheetsMirror, TABS, SheetsError
    class Resp:
        status_code = 200
        content = b"{}"
        def __init__(self, data): self.data = data
        def json(self): return self.data
    class FakeSession:
        def __init__(self):
            self.writes = []
            self.headers = {}
            self.props = {t: {"sheetId": i, "title": t, "gridProperties": {"rowCount":100, "columnCount":len(h)}} for i,(t,h) in enumerate(TABS.items(),1)}
        def request(self, method, url, timeout, **kwargs):
            path = unquote(url)
            if "fields" in kwargs.get("params", {}):
                return Resp({"sheets":[{"properties":p} for p in self.props.values()]})
            if method == "GET":
                tab = path.split("'")[1]
                return Resp({"values": [self.headers[tab]]} if tab in self.headers else {})
            self.writes.append((method, path, kwargs))
            if method == "PUT":
                tab = path.split("'")[1]
                self.headers[tab] = kwargs["json"]["values"][0]
            return Resp({})
        def close(self): pass
    data = {name: [] for name in TABS}
    data["Messages"] = [{"id":1,"body":"=1+1"}]
    fake = FakeSession()
    mirror = SheetsMirror("mock-sheet-12345", session=fake)
    mirror.sync(data)
    first = list(fake.writes)
    mirror.sync(data)
    assert fake.writes[len(first):] == first
    assert all(":append" not in call[1] for call in first)
    messages = next(call for call in first if call[0] == "PUT" and "'Messages'" in call[1])
    assert messages[2]["params"]["valueInputOption"] == "RAW"
    assert messages[2]["json"]["values"][1][-1] == "=1+1"
    fake.headers["Leads"] = ["Unexpected header"]
    with pytest.raises(SheetsError, match="Unexpected header"):
        mirror.sync(data)


def test_cookie_sessions_are_role_scoped_and_revocable(client):
    admin_panel = {"X-Session-Panel": "admin"}
    customer_panel = {"X-Session-Panel": "customer"}
    response = client.post("/api/auth/login", headers=admin_panel, json={"email": "admin@example.com", "password": "TestAdminPass123!"})
    assert response.status_code == 200, response.text
    assert "access_token" not in response.json()
    assert "httponly" in response.headers["set-cookie"].lower()
    assert "samesite=strict" in response.headers["set-cookie"].lower()
    assert client.get("/api/admin/users", headers=admin_panel).status_code == 200
    assert client.get("/api/admin/users").status_code == 401
    register_response = client.post("/api/auth/register", headers=customer_panel, json={"email":"cookiecustomer@example.com","full_name":"Cookie Customer","password":"CookiePass123456!"})
    assert register_response.status_code == 201, register_response.text
    assert client.get("/api/me", headers=customer_panel).json()["role"] == "customer"
    assert client.get("/api/admin/users", headers=customer_panel).status_code == 403
    assert client.get("/api/admin/users", headers=admin_panel).status_code == 200
    assert client.post("/api/auth/login", headers=admin_panel, json={"email":"cookiecustomer@example.com","password":"CookiePass123456!"}).status_code == 403
    assert client.post("/api/auth/logout", headers={**admin_panel,"Origin":"https://hostile.example"}).status_code == 403
    old = client.cookies.get("metroestate_admin_session")
    assert client.post("/api/auth/logout", headers=admin_panel).status_code == 200
    assert client.get("/api/admin/users", headers={"Authorization":"Bearer "+old}).status_code == 401
    assert client.get("/api/me", headers=customer_panel).status_code == 200


def test_private_report_destinations_and_email_fallback(client, monkeypatch):
    admin = login(client, "admin@example.com", "TestAdminPass123!")
    customer = register(client, "reportdest@example.com")
    assert client.get("/api/admin/reports/config", headers=customer).status_code == 403
    payload = {"email":"reports@example.com","sheet_id":"https://docs.google.com/spreadsheets/d/test-spreadsheet-123/edit","current_password":"wrong"}
    assert client.patch("/api/admin/reports/config", headers=admin, json=payload).status_code == 400
    payload["current_password"] = "TestAdminPass123!"
    assert client.patch("/api/admin/reports/config", headers=customer, json=payload).status_code == 403
    assert client.patch("/api/admin/reports/config", headers=admin, json=payload).status_code == 200
    config = client.get("/api/admin/reports/config", headers=admin).json()
    assert config["email"] == "reports@example.com"
    assert config["sheet_id"] == "test-spreadsheet-123"
    assert "current_password" not in config
    send = {"current_password":payload["current_password"],"authorized_recipient":True}
    assert client.post("/api/admin/reports/email", headers=admin, json=send).status_code == 409
    assert client.post("/api/admin/reports/email", headers=customer, json=send).status_code == 403
    from app import report_mail
    monkeypatch.setattr(report_mail, "email_ready", lambda settings: True)
    received = []
    monkeypatch.setattr(report_mail, "send_report", lambda settings, recipient, archive: received.append((recipient,archive)))
    assert client.post("/api/admin/reports/email", headers=admin, json={**send,"authorized_recipient":False}).status_code == 422
    assert client.post("/api/admin/reports/email", headers=admin, json=send).status_code == 200
    assert received[0][0] == "reports@example.com" and received[0][1].startswith(b"PK")
    payload.update(email="newreports@example.com",sheet_id="")
    assert client.patch("/api/admin/reports/config", headers=admin, json=payload).status_code == 200
    assert client.get("/api/admin/reports/config", headers=admin).json()["email"] == "newreports@example.com"


def test_plot_location_validation_and_reporting(client):
    admin = login(client, "admin@example.com", "TestAdminPass123!")
    plot = {"project":"Location Test","plot_number":"L1","area_sqft":1200,"rate_per_sqft":3000,"location_label":"Test location, Karnataka","latitude":12.9,"longitude":77.6}
    assert client.post("/api/plots", headers=admin, json={**plot,"longitude":None}).status_code == 422
    assert client.post("/api/plots", headers=admin, json={**plot,"latitude":91}).status_code == 422
    response = client.post("/api/plots", headers=admin, json=plot)
    assert response.status_code == 201, response.text
    item = next(x for x in client.get("/api/plots").json() if x["id"] == response.json()["id"])
    assert item["latitude"] == 12.9 and item["longitude"] == 77.6
    assert item["location_label"] == plot["location_label"]
    from app.sheet_sync import snapshot
    with m.SessionLocal() as db:
        assert snapshot(db)["Locations"][0]["location_label"] == plot["location_label"]


def test_optional_ai_adapter_redacts_and_handles_provider_failure():
    from app.ai_support import answer_question, safe_question, eligible_question
    assert not eligible_question("Book Site Visit")
    assert eligible_question("Explain how a home loan works")
    assert "example.com" not in safe_question("How do I update my email x@example.com?")
    assert "9999999999" not in safe_question("Can you call 9999999999?")
    calls = []
    class Response:
        status_code = 200
        def json(self):
            return {"status":"completed","output":[{"type":"message","content":[{"type":"output_text","text":"A home loan is repaid over time."}]}]}
    def post(url, **kwargs):
        calls.append(kwargs)
        return Response()
    answer = answer_question("How do loans work?", [{"project":"Public Plot"}], "test-key-not-real", "test-model", post=post)
    assert answer == "A home loan is repaid over time."
    assert calls[0]["json"]["store"] is False
    assert "Public Plot" in calls[0]["json"]["input"]
    class Failed:
        status_code = 503
    assert answer_question("How do loans work?", [], "test-key-not-real", "test-model", post=lambda *args,**kwargs:Failed()) is None


def test_ai_consent_and_structured_flow_fallback(client, monkeypatch):
    from pydantic import SecretStr
    from app import ai_support
    customer = register(client, "smart-ai@example.com")
    monkeypatch.setattr(m.settings, "ai_enabled", True)
    monkeypatch.setattr(m.settings, "openai_api_key", SecretStr("test-key-not-real"))
    calls = []
    def answer(body, inventory, key, model):
        calls.append(body)
        return "A verified general explanation."
    monkeypatch.setattr(ai_support, "answer_question", answer)
    def send(body, number, consent=False):
        return client.post("/api/customer/messages", headers=customer, json={"body":body,"client_id":f"smart-ai-request-{number:03d}","ai_consent":consent})
    assert send("How does a home loan work?", 1).status_code == 201
    assert not calls
    assert send("Explain how a home loan works", 2, True).status_code == 201
    assert calls == ["Explain how a home loan works"]
    assert client.get("/api/customer/messages", headers=customer).json()[-1]["body"] == "A verified general explanation."
    assert send("Book Site Visit", 3, True).status_code == 201
    assert len(calls) == 1
    assert send("How does RERA work", 4, True).status_code == 201
    assert len(calls) == 2
    assert client.get("/api/customer/chat-state", headers=customer).json()["in_progress"] is True
    assert send("Not sure yet", 5, True).status_code == 201
    assert len(calls) == 2


def test_finance_purchase_guidance_corner_and_government_charges(client):
    data = {"area_sqft":1200,"rate_per_sqft":3500,"corner_plot":True,"corner_premium_percent":5,"guidance_value":5000001,"govt_other":1000,"legal_charges":5000,"developer_charges":10000}
    res = client.post("/api/finance/purchase", json=data)
    assert res.status_code == 200, res.text
    x = res.json()
    assert x["base_price"] == 4200000
    assert x["corner_premium"] == 210000 and x["sale_price"] == 4410000
    assert x["assessment_basis"] == 5000001 and x["registration_basis"] == 5001000
    assert x["stamp_duty"] == 250000.05 and x["registration_fee"] == 100020
    assert x["cess"] == 25000.01 and x["surcharge"] == 5000
    assert x["government_total"] == 381020.06 and x["purchase_total"] == 4806020.06
    assert not x["provisional"]
    assert client.post("/api/finance/purchase", json={**data,"corner_plot":False}).status_code == 422
    assert client.post("/api/finance/purchase", json={"area_sqft":1200,"rate_per_sqft":3500}).json()["provisional"] is True

def test_finance_loan_reconciles_amortization_and_stage_interest(client):
    from decimal import Decimal
    body={"loan_type":"construction","project_cost":3000000,"loan_amount":2400000,"annual_rate_percent":8.5,"months":240,"disbursed_amount":600000,"upfront_fees":10000}
    res=client.post("/api/finance/loan",json=body)
    assert res.status_code==200, res.text
    x=res.json()
    assert x["own_contribution"]==600000 and x["cash_required"]==610000
    assert x["pre_emi_monthly"]==4250
    assert x["schedule"][-1]["balance"]==0 and len(x["schedule"])==240
    d=lambda value: Decimal(str(value))
    assert sum(d(row["principal"]) for row in x["schedule"])==d(x["loan_amount"])
    assert sum(d(row["interest"]) for row in x["schedule"])==d(x["total_interest"])
    assert sum(d(row["payment"]) for row in x["schedule"])==d(x["total_repayment"])
    assert client.post("/api/finance/loan",json={**body,"loan_amount":4000000}).status_code==422
    assert client.post("/api/finance/loan",json={**body,"disbursed_amount":2500000}).status_code==422
    zero=client.post("/api/finance/loan",json={"loan_type":"plot","project_cost":1000,"loan_amount":1000,"annual_rate_percent":0,"months":3}).json()
    assert zero["monthly_emi"]==333.33 and zero["final_payment"]==333.34 and zero["total_interest"]==0

def test_finance_construction_and_plot_metadata(client):
    c=client.post("/api/finance/construction",json={"built_area_sqft":1500,"build_rate_per_sqft":2000,"contingency_percent":10,"professional_fees":50000,"approval_fees":25000}).json()
    assert c["construction_total"]==3375000
    admin=login(client,"admin@example.com","TestAdminPass123!")
    plot={"project":"Finance Test","plot_number":"C1","area_sqft":1200,"rate_per_sqft":3500,"corner_plot":True,"corner_premium_percent":5,"guidance_value":5000000,"width_ft":30,"depth_ft":40}
    assert client.post("/api/plots",json=plot).status_code==401
    assert client.post("/api/plots",json={**plot,"depth_ft":50},headers=admin).status_code==422
    assert client.post("/api/plots",json=plot,headers=admin).status_code==201
    item=client.get("/api/plots").json()[0]
    assert item["corner_plot"] is True and item["guidance_value"]==5000000 and item["width_ft"]==30
    from app.sheet_sync import snapshot
    with m.SessionLocal() as db:
        assert snapshot(db)["PlotFinance"][0]["corner_premium_percent"]==5


def test_purchase_tds_is_a_withholding_not_an_extra_purchase_fee(client):
    values={"area_sqft":1000,"rate_per_sqft":6000,"corner_plot":False,"guidance_value":6000000,"seller_resident_india":True}
    response=client.post("/api/finance/purchase",json=values)
    assert response.status_code==200,response.text
    data=response.json()
    assert data["tds_applicable"] is True
    assert data["tds_review_required"] is False
    assert data["tds_withheld_from_seller"]==60000
    assert data["seller_payment_after_tds"]==5940000
    assert data["purchase_total"]==6456000
    pending=client.post("/api/finance/purchase",json={**values,"seller_resident_india":None}).json()
    assert pending["tds_review_required"] is True
    assert pending["indicative_tds_if_resident"]==60000
    assert pending["tds_withheld_from_seller"]==0
    assert pending["purchase_total"]==data["purchase_total"]
    exempt=client.post("/api/finance/purchase",json={**values,"rural_agricultural_land":True}).json()
    assert exempt["tds_applicable"] is False
    assert exempt["tds_withheld_from_seller"]==0
    under=client.post("/api/finance/purchase",json={**values,"area_sqft":800,"rate_per_sqft":5000,"guidance_value":4000000}).json()
    assert under["tds_applicable"] is False
    assert under["tds_review_required"] is False
    high_guidance=client.post("/api/finance/purchase",json={**values,"area_sqft":900,"rate_per_sqft":5000,"guidance_value":5000000}).json()
    assert high_guidance["tds_applicable"] is True
    assert high_guidance["tds_withheld_from_seller"]==50000
    assert high_guidance["seller_payment_after_tds"]==4450000


def test_plot_public_photo_lifecycle_and_roles(client):
    import base64
    import io
    from PIL import Image
    from app.sheet_sync import snapshot
    admin = login(client, "admin@example.com", "TestAdminPass123!")
    customer = register(client, "plot-photo-customer@example.com")
    def image_data(color):
        buf=io.BytesIO()
        Image.new("RGB",(32,24),color).save(buf,format="PNG")
        return base64.b64encode(buf.getvalue()).decode()
    values={"project":"Photo Enclave","plot_number":"P1","area_sqft":1200,"rate_per_sqft":3000,"description":"Public plot description","amenities":"Water and park","approval_details":"Verify documents","facing":"East","road_width_ft":40,"photo_data":image_data("green")}
    assert client.post("/api/plots",headers=customer,json=values).status_code==403
    assert client.post("/api/plots",headers=admin,json={**values,"photo_data":"not-a-photo"}).status_code==422
    assert client.get("/api/plots").json()==[]
    created=client.post("/api/plots",headers=admin,json=values)
    assert created.status_code==201,created.text
    plot=created.json()
    assert plot["description"]==values["description"] and "photo_data" not in plot
    path=plot["photo_url"]
    assert path==f"/api/plots/{plot['id']}/photo"
    photo=client.get(path)
    assert photo.status_code==200 and photo.headers["content-type"]=="image/jpeg"
    assert Image.open(io.BytesIO(photo.content)).size==(32,24)
    update_path=f"/api/admin/plots/{plot['id']}"
    assert client.patch(update_path,headers=customer,json={**values,"remove_photo":True,"photo_data":None}).status_code==403
    edited={**values,"photo_data":image_data("red"),"description":"Updated plot details"}
    assert client.patch(update_path,headers=admin,json=edited).status_code==200
    assert client.get(path).content!=photo.content
    assert client.patch(update_path,headers=admin,json={**edited,"photo_data":None}).status_code==200
    assert client.get(path).status_code==200
    with m.SessionLocal() as db:
        assert snapshot(db)["Plot details"][0]["has_photo"] is True
        assert snapshot(db)["Plot details"][0]["description"]=="Updated plot details"
    assert client.patch(update_path,headers=admin,json={**edited,"photo_data":None,"remove_photo":True}).status_code==200
    assert client.get(path).status_code==404
    assert client.get("/api/plots").json()[0]["photo_url"] is None
    assert client.post("/api/plots",headers=admin,json={**values,"photo_data":"A"*3000001}).status_code==413


def test_read_only_advisor_tools_and_privacy():
    from app.agent_ai import converse, build_messages, emi_amount, run_tool
    import copy
    history=[{"sender":"admin","body":"private team note"},{"sender":"customer","body":"My email is test@example.com"},{"sender":"assistant","body":"Contact test@example.com"},{"sender":"customer","body":"What fits 40 lakh?"}]
    messages=build_messages(history)
    assert "example.com" not in str(messages) and "private team note" not in str(messages)
    assert emi_amount(1000000,0,100)["monthly_emi_inr"]==10000
    assert "error" in run_tool("calculate_emi",{"principal_inr":100,"annual_rate_percent":3,"months":2.5},{})
    assert "error" in run_tool("delete_everything",{}, {})
    calls=[]
    class R:
        status_code=200
        def __init__(self,data):self.data=data
        def json(self):return self.data
    def post(url,**kw):
        calls.append(copy.deepcopy(kw["json"]))
        if len(calls)==1:
            return R({"stop_reason":"tool_use","content":[{"type":"tool_use","id":"t1","name":"search_plots","input":{"max_budget_inr":4000000}}]})
        return R({"stop_reason":"end_turn","content":[{"type":"text","text":"A plot fits your budget."}]})
    result=converse(history,{"search_plots":lambda **args:{"count":1,"args":args}},"test-key-not-real","test-model",post=post)
    assert result=="A plot fits your budget."
    assert calls[1]["messages"][-1]["content"][0]["tool_use_id"]=="t1"
    assert converse(history,{},"test-key-not-real","test-model",post=lambda *a,**k:R({"stop_reason":"max_tokens"})) is None


def test_advisor_consent_and_customer_isolation(client,monkeypatch):
    from pydantic import SecretStr
    from app import agent_ai
    admin=login(client,"admin@example.com","TestAdminPass123!")
    customer=register(client,"advisor-test@example.com")
    other=register(client,"advisor-other@example.com")
    client.post("/api/customer/messages",headers=other,json={"body":"Private other customer","client_id":"other-advisor-test-001"})
    client.post("/api/plots",headers=admin,json={"project":"Available Enclave","plot_number":"1","area_sqft":1000,"rate_per_sqft":3000})
    client.post("/api/plots",headers=admin,json={"project":"Sold Enclave","plot_number":"2","area_sqft":1000,"rate_per_sqft":3000,"status":"sold"})
    monkeypatch.setattr(m.settings,"ai_enabled",True)
    monkeypatch.setattr(m.settings,"ai_provider","anthropic")
    monkeypatch.setattr(m.settings,"anthropic_api_key",SecretStr("test-key-not-real"))
    seen=[]
    def fake(history,handlers,key,model,name):
        seen.append(history)
        assert handlers["search_plots"](max_budget_inr=4000000)["count"]==1
        assert "error" in handlers["plot_base_price"]("Sold Enclave","2")
        return "A plot fits your budget."
    monkeypatch.setattr(agent_ai,"converse",fake)
    assert client.get("/api/customer/chat-state",headers=customer).json()["ai_available"]
    def send(text,n,consent):
        return client.post("/api/customer/messages",headers=customer,json={"body":text,"client_id":f"advisor-test-message-{n:03d}","ai_consent":consent})
    assert send("I have a 40 lakh budget",1,False).status_code==201
    assert not seen
    assert send("Which plot is available?",2,True).status_code==201
    assert len(seen)==1 and "Private other customer" not in str(seen[0])
    assert client.get("/api/customer/messages",headers=customer).json()[-1]["body"]=="A plot fits your budget."
    assert send("Book Site Visit",3,True).status_code==201
    assert len(seen)==1


def test_duplicate_plot_and_atomic_audit(client, monkeypatch):
    admin = login(client,"admin@example.com","TestAdminPass123!")
    data={"project":"Unique Enclave","plot_number":"P1","area_sqft":1200,"rate_per_sqft":3500}
    result=client.post("/api/plots",headers=admin,json=data)
    assert result.status_code==201,result.text
    pid=result.json()["id"]
    assert client.post("/api/plots",headers=admin,json={**data,"project":" unique enclave ","plot_number":"p1"}).status_code==409
    assert client.patch(f"/api/admin/plots/{pid}",headers=admin,json={**data,"rate_per_sqft":3600}).status_code==200
    audit=client.get("/api/admin/audit",headers=admin).json()
    change=next(x for x in audit if x["action"]=="plot.updated")
    assert change["before"]["rate_per_sqft"]==3500 and change["after"]["rate_per_sqft"]==3600
    original=m.write_admin_audit
    def fail_audit(*args,**kwargs):
        raise RuntimeError("Simulated audit failure")
    monkeypatch.setattr(m,"write_admin_audit",fail_audit)
    with pytest.raises(RuntimeError):
        client.post("/api/plots",headers=admin,json={**data,"plot_number":"P2"})
    monkeypatch.setattr(m,"write_admin_audit",original)
    assert len(client.get("/api/plots").json())==1


def test_referral_paid_idempotent_and_audited(client):
    admin=login(client,"admin@example.com","TestAdminPass123!")
    _,partner=make_user(client,admin,"referral-lock@example.com","partner")
    rid=client.post("/api/partners/referrals",headers=partner,json={"referred_name":"Test Referral","referred_phone":"9999999999"}).json()["id"]
    url=f"/api/admin/referrals/{rid}"
    assert client.patch(url,headers=admin,json={"status":"approved","reward_amount":1000}).status_code==200
    for _ in range(2):
        assert client.patch(url,headers=admin,json={"status":"paid","reward_amount":1000}).status_code==200
    records=[x for x in client.get("/api/admin/audit",headers=admin).json() if x["action"]=="referral.decision"]
    assert len(records)==2 and records[0]["before"]["status"]=="approved" and records[0]["after"]["status"]=="paid"
    assert client.patch(url,headers=admin,json={"status":"paid","reward_amount":2000}).status_code==409


def test_proxy_account_throttle_and_request_ids(client,monkeypatch):
    from starlette.requests import Request
    from app.request_security import client_ip
    scope={"type":"http","client":("127.0.0.1",5000),"headers":[(b"host",b"testserver"),(b"x-real-ip",b"203.0.113.10"),(b"x-forwarded-for",b"1.2.3.4")]}
    assert client_ip(Request(scope),"socket","testserver")=="127.0.0.1"
    assert client_ip(Request(scope),"railway","testserver")=="203.0.113.10"
    monkeypatch.setattr(m.settings,"client_ip_mode","railway")
    for i in range(10):
        res=client.post("/api/auth/login",headers={"X-Real-IP":f"203.0.113.{i+1}"},json={"email":"unknown@example.com","password":"wrong"})
        assert res.status_code==401
    res=client.post("/api/auth/login",headers={"X-Real-IP":"203.0.113.50"},json={"email":"unknown@example.com","password":"wrong"})
    assert res.status_code==429 and res.headers["retry-after"]=="900"
    assert len(res.headers["x-request-id"])==32
    assert client.post("/api/auth/login",headers={"X-Real-IP":"203.0.113.50"},json={"email":"admin@example.com","password":"TestAdminPass123!"}).status_code==200


def test_budget_redaction_preserves_money():
    from app.ai_support import safe_question
    assert "50000000" in safe_question("budget 50000000")
    assert "45,00,000" in safe_question("price 45,00,000")
    assert "9999999999" not in safe_question("call me at 9999999999")
    assert "+91 9876543210" not in safe_question("phone +91 9876543210")


def test_external_reporting_minimization(client):
    from app.sheet_sync import snapshot
    customer=register(client,"private-report@example.com")
    client.post("/api/customer/messages",headers=customer,json={"body":"private customer message","client_id":"private-report-12345"})
    with m.SessionLocal() as db:
        full=snapshot(db)
        external=snapshot(db,external=True)
        assert full["Customers"][0]["email"]=="private-report@example.com"
        assert external["Customers"][0]["email"]==""
        assert external["Messages"][0]["body"]==""
        assert snapshot(db,external=True,full_customer_data=True)["Messages"][0]["body"]=="private customer message"


def test_ai_fallback_committed_before_provider(client,monkeypatch):
    from pydantic import SecretStr
    from sqlalchemy import select
    from app import ai_support
    customer=register(client,"ai-commit@example.com")
    monkeypatch.setattr(m.settings,"ai_enabled",True)
    monkeypatch.setattr(m.settings,"openai_api_key",SecretStr("test-key-not-real"))
    calls=[]
    def answer(*args):
        with m.SessionLocal() as independent:
            rows=list(independent.scalars(select(m.SupportMessage).order_by(m.SupportMessage.id)))
            assert len(rows)==2 and rows[0].body=="How do loans work?"
        calls.append(True)
        return "Loan explanation."
    monkeypatch.setattr(ai_support,"answer_question",answer)
    body={"body":"How do loans work?","client_id":"ai-commit-test-1234","ai_consent":True}
    assert client.post("/api/customer/messages",headers=customer,json=body).status_code==201
    assert client.post("/api/customer/messages",headers=customer,json=body).status_code==201
    assert len(calls)==1
    assert client.get("/api/customer/messages",headers=customer).json()[-1]["body"]=="Loan explanation."


def test_human_support_handoff_notifications_and_resume(client):
    admin = login(client, "admin@example.com", "TestAdminPass123!")
    customer = register(client, "handoff-customer@example.com")
    other = register(client, "handoff-other@example.com")
    customer_id = client.get("/api/me", headers=customer).json()["id"]
    inbox = "/api/admin/message-notifications"
    assert client.get(inbox, headers=customer).status_code == 403
    assert client.get(inbox, headers=admin).json()["pending_count"] == 0
    assert client.get("/api/customer/chat-state", headers=customer).json()["mode"] == "assistant"
    assert client.post("/api/customer/chat-mode", headers=customer, json={"mode": "team"}).status_code == 200
    assert client.get("/api/customer/chat-state", headers=customer).json()["quick"] == []
    payload = {"body": "Can a staff member help me?", "client_id": "handoff-message-0001", "ai_consent": True}
    first = client.post("/api/customer/messages", headers=customer, json=payload)
    assert first.status_code == 201, first.text
    assert client.post("/api/customer/messages", headers=customer, json=payload).json()["id"] == first.json()["id"]
    assert [x["sender"] for x in client.get("/api/customer/messages", headers=customer).json()] == ["customer"]
    result = client.get(inbox, headers=admin).json()
    assert result["pending_count"] == 1
    assert result["items"][0]["customer_id"] == customer_id
    assert result["items"][0]["message_id"] == first.json()["id"]
    assert client.get("/api/customer/messages", headers=other).json() == []
    reply = {"body": "Our property team is here to help.", "client_id": "handoff-admin-000001"}
    assert client.post(f"/api/admin/messages/{customer_id}", headers=admin, json=reply).status_code == 201
    assert client.get(inbox, headers=admin).json()["pending_count"] == 0
    assert client.post("/api/customer/messages", headers=customer, json=payload | {"client_id": "handoff-message-0002"}).status_code == 201
    assert [x["sender"] for x in client.get("/api/customer/messages", headers=customer).json()] == ["customer", "admin", "customer"]
    assert client.get(inbox, headers=admin).json()["pending_count"] == 1
    assert client.post("/api/customer/chat-mode", headers=customer, json={"mode": "assistant"}).status_code == 200
    assert client.get(inbox, headers=admin).json()["pending_count"] == 0
    assert client.post("/api/customer/messages", headers=customer, json=payload | {"body": "What is the plot price?", "client_id": "handoff-message-0003", "ai_consent": False}).status_code == 201
    assert client.get("/api/customer/messages", headers=customer).json()[-1]["sender"] == "assistant"
    assert client.post(f"/api/admin/messages/{customer_id}", headers=admin, json=reply | {"client_id": "handoff-admin-000002"}).status_code == 201
    assert client.get("/api/customer/chat-state", headers=customer).json()["mode"] == "team"


def test_admin_review_history_access(client):
    admin = login(client, "admin@example.com", "TestAdminPass123!")
    customer = register(client, "review-history@example.com")
    customer_id = client.get("/api/me", headers=customer).json()["id"]
    assert client.get("/api/admin/review-requests", headers=customer).status_code == 403
    assert client.post("/api/reviews/requests", headers=admin, json={"customer_id": customer_id, "channel": "manual", "consent_confirmed": True}).status_code == 201
    rows = client.get("/api/admin/review-requests", headers=admin).json()
    assert len(rows) == 1 and rows[0]["customer_id"] == customer_id
    assert rows[0]["delivery"] == "not_configured" and rows[0]["consent_confirmed"] is True
