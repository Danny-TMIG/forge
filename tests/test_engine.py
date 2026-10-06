from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_dxp_render():
    payload = {
        "context": {
            "user_id": "test_user",
            "session_id": "sess_123",
            "channel": "web",
            "segment": "enterprise"
        },
        "surface_id": "hero_banner"
    }
    response = client.post("/dxp/render", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["selected_variant"] == "variant_b"

def test_ax_dispatch():
    payload = {
        "agent_id": "ax_router_v1",
        "intent": "classify",
        "payload": {"text": "hello"},
        "context": {
            "user_id": "test_user",
            "session_id": "sess_123",
            "channel": "web",
            "segment": "standard"
        }
    }
    response = client.post("/ax/dispatch", json=payload)
    assert response.status_code == 200
    assert "task_id" in response.json()
