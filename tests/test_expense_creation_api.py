from collections.abc import Iterator
from functools import partial
from secrets import token_urlsafe
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from spend_mind.config import Settings
from spend_mind.main import app
from spend_mind.schemas.expense import ExpenseCreate
from spend_mind.services import expense

ENDPOINT = "/internal/expenses"


def expense_payload() -> dict:
    return {
        "merchant": "Shop",
        "total_amount": "2.00",
        "currency": "uah",
        "expense_date": "2026-11-27T18:30:00+02:00",
        "source": "manual",
        "items": [{"name": " Apples ", "amount": "2.00"}],
    }


@pytest.fixture
def api(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, dict[str, str], AsyncMock]]:
    token = token_urlsafe()
    settings = partial(
        Settings,
        _env_file=None,
        postgres_host="database.invalid",
        postgres_port=5432,
        postgres_db="test",
        postgres_user="test",
        postgres_password="unused",
        internal_api_token=token,
    )
    monkeypatch.setattr("spend_mind.main.Settings", settings)
    service = AsyncMock()
    monkeypatch.setattr(expense, "create_expense", service)
    with TestClient(app) as client:
        yield (
            client,
            {"Authorization": f"Bearer {token}", "X-Telegram-User-Id": "123"},
            service,
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"items": []},
        {"total_amount": "0"},
        {"total_amount": "1.000"},
        {"total_amount": 2.0},
        {"total_amount": "1000000000000.00"},
        {"total_amount": "NaN"},
        {"expense_date": "2026-11-27T18:30:00"},
        {"expense_date": "1760000000"},
        {"expense_date": 1760000000},
        {"status": "confirmed"},
        {"user_id": 1},
        {"exchange_rate": "1"},
        {"items": [{"name": "A", "amount": "2", "expense_id": 1}]},
        {"items": [{"name": "A", "amount": "-1"}]},
        {"items": [{"name": "A", "amount": "2", "quantity": "0"}]},
        {"items": [{"name": "A", "amount": "2", "quantity": "1000000000"}]},
        {"items": [{"name": "A", "amount": "2", "quantity": "1.0001"}]},
        {"items": [{"name": "A", "amount": "2", "category_id": True}]},
    ],
)
def test_expense_creation_rejects_invalid_payload(
    api: tuple[TestClient, dict[str, str], AsyncMock], changes: dict
) -> None:
    client, headers, service = api
    response = client.post(ENDPOINT, headers=headers, json=expense_payload() | changes)
    assert response.status_code == 422
    service.assert_not_called()


@pytest.mark.parametrize("currency", ["GBP", "PLN", "JPY", "cad", "XXX", "ZZZ"])
def test_expense_creation_rejects_unsupported_currencies(
    api: tuple[TestClient, dict[str, str], AsyncMock], currency: str
) -> None:
    client, headers, service = api
    response = client.post(
        ENDPOINT, headers=headers, json=expense_payload() | {"currency": currency}
    )
    assert response.status_code == 422
    assert [error["loc"] for error in response.json()["detail"]] == [
        ["body", "currency"]
    ]
    service.assert_not_called()


def test_expense_creation_requires_internal_authentication(
    api: tuple[TestClient, dict[str, str], AsyncMock],
) -> None:
    client, headers, service = api
    assert client.post(ENDPOINT, json=expense_payload()).status_code == 401
    assert (
        client.post(
            ENDPOINT,
            headers={"Authorization": headers["Authorization"]},
            json=expense_payload(),
        ).status_code
        == 422
    )
    service.assert_not_called()


def test_receipt_discount_distributes_remaining_cents_by_item_order() -> None:
    payload = ExpenseCreate.model_validate(
        expense_payload()
        | {
            "total_amount": "2.98",
            "receipt_discount": "0.02",
            "items": [{"name": str(index), "amount": "1.00"} for index in range(3)],
        }
    )
    assert [str(amount) for amount in expense.reconcile_item_amounts(payload)] == [
        "0.99",
        "0.99",
        "1.00",
    ]


def test_receipt_discount_uses_largest_remainders() -> None:
    payload = ExpenseCreate.model_validate(
        expense_payload()
        | {
            "total_amount": "2.99",
            "receipt_discount": "0.01",
            "items": [{"name": "A", "amount": "1"}, {"name": "B", "amount": "2"}],
        }
    )
    assert [str(amount) for amount in expense.reconcile_item_amounts(payload)] == [
        "1.00",
        "1.99",
    ]


def test_reconciled_amounts_are_preserved_without_discount() -> None:
    payload = ExpenseCreate.model_validate(expense_payload())

    assert [str(amount) for amount in expense.reconcile_item_amounts(payload)] == [
        "2.00"
    ]
