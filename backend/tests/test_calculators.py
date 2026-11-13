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
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0006_sessions_locations"
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
            assert data["Messages"][0]["body"] == "=1+1"
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
