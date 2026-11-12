import hashlib
import hmac
from fastapi.testclient import TestClient
from app.main import app, settings

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


def test_whatsapp_webhook_verification(monkeypatch):
    monkeypatch.setattr(settings, "whatsapp_verify_token", "test-verify-token")
    response = client.get("/api/whatsapp/webhook", params={
        "hub.mode": "subscribe",
        "hub.verify_token": "test-verify-token",
        "hub.challenge": "challenge-value",
    })
    assert response.status_code == 200
    assert response.text == "challenge-value"


def test_whatsapp_webhook_rejects_invalid_signature(monkeypatch):
    monkeypatch.setattr(settings, "whatsapp_app_secret", "test-app-secret")
    response = client.post("/api/whatsapp/webhook", content=b'{}', headers={
        "X-Hub-Signature-256": "sha256=invalid",
        "Content-Type": "application/json",
    })
    assert response.status_code == 401


def test_whatsapp_webhook_accepts_valid_signature(monkeypatch):
    secret = "test-app-secret"
    monkeypatch.setattr(settings, "whatsapp_app_secret", secret)
    body = b'{"object":"whatsapp_business_account","entry":[]}'
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    response = client.post("/api/whatsapp/webhook", content=body, headers={
        "X-Hub-Signature-256": signature,
        "Content-Type": "application/json",
    })
    assert response.status_code == 200
    assert response.json()["processed_messages"] == 0
