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
