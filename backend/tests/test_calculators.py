from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_health():
    response = client.get('/health')
    assert response.status_code == 200
    assert response.json()['status'] == 'ok'

def test_emi_zero_interest():
    response = client.post('/api/calculators/emi', params={'principal': 120000, 'annual_rate_percent': 0, 'months': 12})
    assert response.status_code == 200
    assert response.json()['monthly_emi'] == 10000

def test_growth():
    response = client.post('/api/calculators/growth', params={'initial_value': 100000, 'annual_growth_percent': 10, 'years': 2})
    assert response.json()['future_value'] == 121000


def test_lead_inbox_requires_authentication():
    assert client.get("/api/admin/lead-notifications").status_code == 401


def test_lead_inbox_lifecycle():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool
    from app.main import Base, User, Role, db_session, current_user
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        admin = User(email="admin@test.invalid", full_name="Admin", password_hash="unused", role=Role.admin.value)
        customer = User(email="customer@test.invalid", full_name="Customer", password_hash="unused", role=Role.customer.value)
        db.add_all([admin, customer]); db.commit()
        admin_id, customer_id = admin.id, customer.id
    actor_id = customer_id
    def session_override():
        with Session(engine) as db:
            yield db
    def user_override():
        with Session(engine) as db:
            return db.get(User, actor_id)
    app.dependency_overrides[db_session] = session_override
    app.dependency_overrides[current_user] = user_override
    try:
        response = client.post("/api/leads", json={"name":"Test Customer","phone":"9999999999","interest":"Plot"})
        assert response.status_code == 201
        lead_id = response.json()["id"]
        assert client.get("/api/admin/lead-notifications").status_code == 403
        actor_id = admin_id
        response = client.get("/api/admin/lead-notifications")
        assert response.status_code == 200
        assert [x["id"] for x in response.json()] == [lead_id]
        assert client.patch(f"/api/leads/{lead_id}/status", params={"new_status":"contacted"}).status_code == 200
        assert client.get("/api/admin/lead-notifications").json() == []
    finally:
        app.dependency_overrides.pop(db_session, None)
        app.dependency_overrides.pop(current_user, None)
        engine.dispose()
