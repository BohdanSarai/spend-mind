from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from spend_mind.main import app


def test_health_is_available_without_database(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_HOST", "database.invalid")
    monkeypatch.setenv("POSTGRES_DB", "spend_mind")
    monkeypatch.setenv("POSTGRES_USER", "spend_mind")
    monkeypatch.setenv("POSTGRES_PASSWORD", "test-password")

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
