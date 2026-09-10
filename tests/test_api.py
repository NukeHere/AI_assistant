from fastapi.testclient import TestClient

from app.config import settings
from app.main import app


def auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.api_token}"}


def test_health_works_without_auth() -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_message_requires_auth() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/v1/message",
            json={"client_id": "phone", "conversation_id": "test-auth", "text": "Привет"},
        )

    assert response.status_code == 401


def test_simple_chat_accepts_history() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/simple",
            headers=auth_headers(),
            json={
                "messages": [
                    {"role": "system", "content": "You are helpful."},
                    {"role": "user", "content": "Привет"},
                ]
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["model_mode"] in {"mock", "api"}
    assert body["text"]


def test_message_uses_default_ana() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/v1/message",
            headers=auth_headers(),
            json={"client_id": "phone", "conversation_id": "test-ana", "text": "Привет"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["persona"] == "ANA"
    assert body["switched"] is False


def test_persona_switch_persists_for_next_message() -> None:
    with TestClient(app) as client:
        switch_response = client.post(
            "/v1/message",
            headers=auth_headers(),
            json={
                "client_id": "phone",
                "conversation_id": "test-switch",
                "text": "переключись на инопланетный режим",
            },
        )
        next_response = client.post(
            "/v1/message",
            headers=auth_headers(),
            json={
                "client_id": "phone",
                "conversation_id": "test-switch",
                "text": "Объясни Docker простыми словами",
            },
        )

    assert switch_response.status_code == 200
    assert switch_response.json()["persona"] == "ALIEN"
    assert switch_response.json()["switched"] is True
    assert next_response.status_code == 200
    assert next_response.json()["persona"] == "ALIEN"
