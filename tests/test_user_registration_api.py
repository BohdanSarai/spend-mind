from collections.abc import Iterator
from datetime import UTC, datetime
from functools import partial
from secrets import token_urlsafe
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from spend_mind.config import Settings
from spend_mind.main import app
from spend_mind.models import User
from spend_mind.services import user_registration


@pytest.fixture
def client_and_token(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, str]]:
    token = token_urlsafe()
    monkeypatch.setenv("INTERNAL_API_TOKEN", token)
    monkeypatch.setenv("POSTGRES_HOST", "database.invalid")
    monkeypatch.setenv("POSTGRES_DB", "test")
    monkeypatch.setenv("POSTGRES_USER", "test")
    monkeypatch.setenv("POSTGRES_PASSWORD", "unused")
    with TestClient(app) as client:
        yield client, token


@pytest.fixture
def registration(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    register = AsyncMock()
    monkeypatch.setattr(user_registration, "register_user", register)
    return register


@pytest.mark.parametrize(("created", "status_code"), [(True, 201), (False, 200)])
def test_registration_response(
    client_and_token: tuple[TestClient, str],
    registration: AsyncMock,
    created: bool,
    status_code: int,
) -> None:
    client, token = client_and_token
    registration.return_value = (
        User(id=1, telegram_user_id=123, created_at=datetime(2026, 1, 1, tzinfo=UTC)),
        created,
    )
    response = client.post(
        "/internal/users/register",
        json={"telegram_user_id": 123},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == status_code
    assert response.json() == {
        "id": 1,
        "telegram_user_id": 123,
        "created_at": "2026-01-01T00:00:00Z",
    }
    registration.assert_awaited_once()
    assert registration.await_args.args[1] == 123


@pytest.mark.parametrize("authorization", [None, "Bearer invalid", "Basic invalid"])
def test_registration_rejects_unauthorized_requests(
    client_and_token: tuple[TestClient, str],
    registration: AsyncMock,
    authorization: str | None,
) -> None:
    client, _ = client_and_token
    response = client.post(
        "/internal/users/register",
        json={"telegram_user_id": 123},
        headers={"Authorization": authorization} if authorization else {},
    )
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    registration.assert_not_awaited()


@pytest.mark.parametrize("configured_token", [None, ""], ids=["missing", "empty"])
def test_registration_rejects_bearer_when_backend_token_is_unconfigured(
    configured_token: str | None,
    registration: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("spend_mind.main.Settings", partial(Settings, _env_file=None))
    if configured_token is None:
        monkeypatch.delenv("INTERNAL_API_TOKEN", raising=False)
    else:
        monkeypatch.setenv("INTERNAL_API_TOKEN", configured_token)
    monkeypatch.setenv("POSTGRES_HOST", "database.invalid")
    monkeypatch.setenv("POSTGRES_PORT", "5432")
    monkeypatch.setenv("POSTGRES_DB", "test")
    monkeypatch.setenv("POSTGRES_USER", "test")
    monkeypatch.setenv("POSTGRES_PASSWORD", "unused")

    with TestClient(app) as client:
        response = client.post(
            "/internal/users/register",
            json={"telegram_user_id": 123},
            headers={"Authorization": f"Bearer {token_urlsafe()}"},
        )

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    registration.assert_not_called()


@pytest.mark.parametrize("telegram_id", [None, "123", True, 0, -1, 2**63])
def test_registration_validates_telegram_id(
    client_and_token: tuple[TestClient, str],
    registration: AsyncMock,
    telegram_id: int | str | None,
) -> None:
    client, token = client_and_token
    response = client.post(
        "/internal/users/register",
        json={"telegram_user_id": telegram_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 422
    registration.assert_not_awaited()
