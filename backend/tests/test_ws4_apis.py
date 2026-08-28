import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_custom_categories_api():
    resp = client.post('/api/categories', json={'name': 'Gaming', 'color': '#ff0000', 'icon': 'Gamepad'})
    assert resp.status_code == 200

    resp = client.get('/api/categories')
    assert resp.status_code == 200
    cats = resp.json()
    assert 'gaming' in cats

    resp = client.delete('/api/categories/gaming')
    assert resp.status_code == 200

    resp = client.get('/api/categories')
    assert 'gaming' not in resp.json()

def test_recurring_overrides_api():
    resp = client.patch('/api/recurring/test-series-123', json={'is_active': False, 'label': 'Netflix Sub'})
    assert resp.status_code == 200

    resp = client.delete('/api/recurring/test-series-123')
    assert resp.status_code == 200

