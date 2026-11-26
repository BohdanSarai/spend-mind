import asyncio
import os
from collections.abc import AsyncIterator
from datetime import date
from decimal import Decimal
from secrets import randbits, token_urlsafe

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import URL, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from spend_mind.config import Settings
from spend_mind.integrations import nbu
from spend_mind.main import app
from spend_mind.models import Category, Expense, ExpenseItem
from spend_mind.repositories import expense as expense_repository
from spend_mind.services.user_registration import register_user

ENDPOINT = "/internal/expenses"


@pytest_asyncio.fixture
async def sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    port = os.getenv("SPEND_MIND_EXPENSE_TEST_PORT")
    if not port:
        pytest.skip("Requires an isolated expense_check PostgreSQL instance")
    engine = create_async_engine(
        URL.create(
            "postgresql+asyncpg",
            username="expense_check",
            host="127.0.0.1",
            port=int(port),
            database="expense_check",
        )
    )
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def api(
    sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[httpx.AsyncClient, str]]:
    token = token_urlsafe()
    settings = Settings(
        _env_file=None,
        postgres_db="test",
        postgres_user="test",
        postgres_password="unused",
        internal_api_token=token,
    )
    monkeypatch.setattr(app.state, "settings", settings, raising=False)
    monkeypatch.setattr(app.state, "session_factory", sessions, raising=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, token


async def add_user(
    sessions: async_sessionmaker[AsyncSession],
    token: str,
) -> tuple[dict[str, str], int, int]:
    telegram_id = randbits(62) + 1
    async with sessions() as session:
        owner, _ = await register_user(session, telegram_id)
        category = await session.scalar(
            select(Category.id)
            .where(Category.user_id == owner.id)
            .order_by(Category.id)
            .limit(1)
        )
        assert category is not None
        return (
            {
                "Authorization": f"Bearer {token}",
                "X-Telegram-User-Id": str(telegram_id),
            },
            owner.id,
            category,
        )


def payload(category_id: int | None = None) -> dict:
    return {
        "total_amount": "2.99",
        "receipt_discount": "0.01",
        "currency": "uah",
        "expense_date": "2026-11-27T18:30:00+02:00",
        "source": "image",
        "items": [
            {"name": "A", "amount": "1.00", "category_id": category_id},
            {"name": "B", "amount": "2.00", "quantity": "1.250", "unit": "kg"},
        ],
    }


async def create_pending(
    client: httpx.AsyncClient, headers: dict[str, str], currency: str = "UAH"
) -> int:
    response = await client.post(
        ENDPOINT,
        headers=headers,
        json=payload() | {"currency": currency, "expense_date": "2026-11-27T22:30:00Z"},
    )
    assert response.status_code == 201
    return response.json()["id"]


@pytest.mark.asyncio
async def test_uah_confirmation_and_rejection_are_idempotent_and_owner_scoped(
    api: tuple[httpx.AsyncClient, str],
    sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, token = api
    owner, _, _ = await add_user(sessions, token)
    outsider, _, _ = await add_user(sessions, token)
    expense_id = await create_pending(client, owner)
    path = f"{ENDPOINT}/{expense_id}"

    async def unexpected_rate(*args: object) -> Decimal:
        pytest.fail("UAH must not request an exchange rate")

    monkeypatch.setattr(nbu, "get_historical_rate", unexpected_rate)
    assert (await client.post(f"{path}/confirm")).status_code == 401
    assert (
        await client.post(f"{ENDPOINT}/0/confirm", headers=owner)
    ).status_code == 422
    for action in ("confirm", "reject"):
        response = await client.post(f"{path}/{action}", headers=outsider)
        assert (response.status_code, response.json()["detail"]) == (
            404,
            "expense_not_found",
        )
    confirmed = await client.post(f"{path}/confirm", headers=owner)
    assert confirmed.status_code == 200
    assert confirmed.json() == {
        "id": expense_id,
        "status": "confirmed",
        "exchange_rate": "1.00000000",
        "amount_uah": "2.99",
    }
    assert (
        await client.post(f"{path}/confirm", headers=owner)
    ).json() == confirmed.json()
    assert (await client.post(f"{path}/reject", headers=owner)).status_code == 409

    other_id = await create_pending(client, owner)
    reject_path = f"{ENDPOINT}/{other_id}/reject"
    for _ in range(2):
        rejected = await client.post(reject_path, headers=owner)
        assert rejected.status_code == 200
        assert rejected.json() == {
            "id": other_id,
            "status": "rejected",
            "exchange_rate": None,
            "amount_uah": None,
        }
    assert (
        await client.post(f"{ENDPOINT}/{other_id}/confirm", headers=owner)
    ).status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("currency", ["USD", "EUR"])
async def test_confirmation_uses_kyiv_date_and_reuses_stored_rate(
    api: tuple[httpx.AsyncClient, str],
    sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    currency: str,
) -> None:
    client, token = api
    owner, _, _ = await add_user(sessions, token)
    expense_id = await create_pending(client, owner, currency)
    calls: list[tuple[str, date]] = []

    async def rate(client: httpx.AsyncClient, code: str, day: date) -> Decimal:
        calls.append((code, day))
        return Decimal("1.500000004") if len(calls) == 1 else Decimal(2)

    monkeypatch.setattr(nbu, "get_historical_rate", rate)
    path = f"{ENDPOINT}/{expense_id}/confirm"
    first = await client.post(path, headers=owner)
    second = await client.post(path, headers=owner)
    assert first.status_code == second.status_code == 200
    assert (
        first.json()
        == second.json()
        == {
            "id": expense_id,
            "status": "confirmed",
            "exchange_rate": "1.50000000",
            "amount_uah": "4.49",
        }
    )
    assert calls == [(currency, date(2026, 11, 28))]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("rate_value", "status_code", "detail"),
    [
        (None, 503, "exchange_rate_unavailable"),
        (Decimal(9999999999), 422, "expense_conversion_out_of_range"),
    ],
)
async def test_failed_confirmation_leaves_expense_pending(
    api: tuple[httpx.AsyncClient, str],
    sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    rate_value: Decimal | None,
    status_code: int,
    detail: str,
) -> None:
    client, token = api
    owner, _, _ = await add_user(sessions, token)
    response = await client.post(
        ENDPOINT,
        headers=owner,
        json=payload()
        | {
            "currency": "USD",
            "total_amount": "999999999999.99",
            "receipt_discount": None,
            "items": [{"name": "A", "amount": "999999999999.99"}],
        },
    )
    assert response.status_code == 201
    expense_id = response.json()["id"]

    async def rate(*args: object) -> Decimal:
        if rate_value is None:
            raise nbu.ExchangeRateUnavailable
        return rate_value

    monkeypatch.setattr(nbu, "get_historical_rate", rate)
    response = await client.post(f"{ENDPOINT}/{expense_id}/confirm", headers=owner)
    assert (response.status_code, response.json()["detail"]) == (status_code, detail)
    async with sessions() as session:
        record = await session.get(Expense, expense_id)
        assert record is not None and record.status == "pending"
        assert record.exchange_rate is None and record.amount_uah is None


@pytest.mark.asyncio
@pytest.mark.parametrize("competing_action", ["confirm", "reject"])
@pytest.mark.parametrize("first_rate_available", [True, False])
async def test_competing_transitions_preserve_the_first_committed_result(
    api: tuple[httpx.AsyncClient, str],
    sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    competing_action: str,
    first_rate_available: bool,
) -> None:
    client, token = api
    owner, _, _ = await add_user(sessions, token)
    expense_id = await create_pending(client, owner, "USD")
    path = f"{ENDPOINT}/{expense_id}"
    waiting_for_rate = asyncio.Event()
    release = asyncio.Event()

    async def rate(*args: object) -> Decimal:
        if not waiting_for_rate.is_set():
            waiting_for_rate.set()
            await asyncio.wait_for(release.wait(), timeout=5)
            if not first_rate_available:
                raise nbu.ExchangeRateUnavailable
            return Decimal(2)
        return Decimal(3)

    monkeypatch.setattr(nbu, "get_historical_rate", rate)
    async with asyncio.TaskGroup() as tasks:
        first = tasks.create_task(client.post(f"{path}/confirm", headers=owner))
        await asyncio.wait_for(waiting_for_rate.wait(), timeout=5)
        winner = await asyncio.wait_for(
            client.post(f"{path}/{competing_action}", headers=owner), timeout=5
        )
        release.set()
        later = await first
    assert winner.status_code == 200
    if competing_action == "confirm":
        assert later.status_code == 200
        assert later.json() == winner.json()
        assert winner.json()["exchange_rate"] == "3.00000000"
    else:
        assert winner.json()["status"] == "rejected"
        assert (later.status_code, later.json()["detail"]) == (
            409,
            "expense_status_conflict",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("currency", ["UAH", "USD", "EUR"])
async def test_creates_pending_expense_and_items(
    api: tuple[httpx.AsyncClient, str],
    sessions: async_sessionmaker[AsyncSession],
    currency: str,
) -> None:
    client, token = api
    headers, user_id, category_id = await add_user(sessions, token)
    response = await client.post(
        ENDPOINT,
        headers=headers,
        json=payload(category_id) | {"currency": currency.lower()},
    )
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["status"] == "pending" and data["currency"] == currency
    assert data["total_amount"] == "2.99"
    assert data["expense_date"] == "2026-11-27T16:30:00Z"
    assert data["created_at"]
    assert [item["amount"] for item in data["items"]] == ["1.00", "1.99"]
    assert [item["category_id"] for item in data["items"]] == [category_id, None]
    assert [Decimal(item["quantity"]) for item in data["items"]] == [
        Decimal(1),
        Decimal("1.250"),
    ]
    async with sessions() as session:
        record = await session.get(Expense, data["id"])
        assert record is not None
        assert record.user_id == user_id and record.status == "pending"
        assert record.currency == currency
        assert record.exchange_rate is None and record.amount_uah is None
        amounts = await session.scalars(
            select(ExpenseItem.amount).where(ExpenseItem.expense_id == record.id)
        )
        assert sum(amounts, Decimal(0)) == record.total_amount


@pytest.mark.asyncio
async def test_invalid_owner_category_and_total_leave_no_expenses(
    api: tuple[httpx.AsyncClient, str], sessions: async_sessionmaker[AsyncSession]
) -> None:
    client, token = api
    headers, user_id, _ = await add_user(sessions, token)
    _, _, foreign_category = await add_user(sessions, token)
    unknown = headers | {"X-Telegram-User-Id": str(2**63 - 1)}
    response = await client.post(ENDPOINT, headers=unknown, json=payload())
    assert (response.status_code, response.json()["detail"]) == (404, "user_not_found")
    for category_id in (foreign_category, 2**31 - 1):
        response = await client.post(
            ENDPOINT, headers=headers, json=payload(category_id)
        )
        assert (response.status_code, response.json()["detail"]) == (
            404,
            "category_not_found",
        )
    response = await client.post(
        ENDPOINT, headers=headers, json=payload() | {"total_amount": "2.98"}
    )
    assert (response.status_code, response.json()["detail"]) == (
        422,
        "expense_total_mismatch",
    )
    async with sessions() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(Expense)
                .where(Expense.user_id == user_id)
            )
            == 0
        )


@pytest.mark.asyncio
async def test_item_failure_rolls_back_expense_and_all_items(
    api: tuple[httpx.AsyncClient, str],
    sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, token = api
    headers, _, _ = await add_user(sessions, token)
    insert = expense_repository.insert_expense
    inserted_id = None

    async def fail_after_insert(session: AsyncSession, record: Expense) -> None:
        nonlocal inserted_id
        await insert(session, record)
        inserted_id = record.id
        await session.execute(
            update(ExpenseItem)
            .where(ExpenseItem.id == record.items[-1].id)
            .values(amount=-1)
        )

    monkeypatch.setattr(expense_repository, "insert_expense", fail_after_insert)
    with pytest.raises(IntegrityError):
        await client.post(ENDPOINT, headers=headers, json=payload())
    assert inserted_id is not None
    async with sessions() as session:
        assert await session.get(Expense, inserted_id) is None
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ExpenseItem)
                .where(ExpenseItem.expense_id == inserted_id)
            )
            == 0
        )


@pytest.mark.asyncio
async def test_expense_creation_shares_category_lock_but_blocks_deletion(
    api: tuple[httpx.AsyncClient, str],
    sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, token = api
    headers, _, category_id = await add_user(sessions, token)
    locked = asyncio.Event()
    release = asyncio.Event()
    insert = expense_repository.insert_expense

    async def pause_first_insert(session: AsyncSession, record: Expense) -> None:
        if not locked.is_set():
            locked.set()
            await asyncio.wait_for(release.wait(), timeout=10)
        await insert(session, record)

    monkeypatch.setattr(expense_repository, "insert_expense", pause_first_insert)
    async with asyncio.TaskGroup() as tasks:
        first = tasks.create_task(
            client.post(ENDPOINT, headers=headers, json=payload(category_id))
        )
        await asyncio.wait_for(locked.wait(), timeout=5)
        second = await asyncio.wait_for(
            client.post(ENDPOINT, headers=headers, json=payload(category_id)), timeout=5
        )
        assert second.status_code == 201
        deletion = tasks.create_task(
            client.delete(f"/internal/categories/{category_id}", headers=headers)
        )
        async with asyncio.timeout(5), sessions() as session:
            while not await session.scalar(
                text(
                    "SELECT EXISTS (SELECT FROM pg_locks WHERE NOT granted "
                    "AND locktype = 'transactionid')"
                )
            ):
                await asyncio.sleep(0.01)
        release.set()
        assert (await first).status_code == 201
        denied = await deletion
        assert (denied.status_code, denied.json()["detail"]) == (409, "category_in_use")
