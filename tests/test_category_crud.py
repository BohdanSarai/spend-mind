import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from secrets import randbits, token_urlsafe

import httpx
import pytest
import pytest_asyncio
from pydantic import SecretStr
from sqlalchemy import URL, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from spend_mind.config import Settings
from spend_mind.main import app
from spend_mind.models import Category, Expense, ExpenseItem
from spend_mind.models.expense import ExpenseSource
from spend_mind.repositories import category as category_repository
from spend_mind.services.user_registration import register_user

CATEGORIES = "/internal/categories"


@pytest_asyncio.fixture
async def sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    port = os.getenv("SPEND_MIND_CATEGORY_TEST_PORT")
    if not port:
        pytest.skip("Requires an isolated category_check PostgreSQL instance")
    engine = create_async_engine(
        URL.create(
            "postgresql+asyncpg",
            username="category_check",
            host="127.0.0.1",
            port=int(port),
            database="category_check",
        )
    )
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture
def token(monkeypatch: pytest.MonkeyPatch) -> str:
    token = token_urlsafe()
    settings = Settings(
        _env_file=None,
        postgres_host="database.invalid",
        postgres_db="test",
        postgres_user="test",
        postgres_password=SecretStr("unused"),
        internal_api_token=SecretStr(token),
    )
    monkeypatch.setattr(app.state, "settings", settings, raising=False)
    return token


@pytest_asyncio.fixture
async def client(
    sessions: async_sessionmaker[AsyncSession],
    token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[httpx.AsyncClient]:
    monkeypatch.setattr(app.state, "session_factory", sessions, raising=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


def headers(token: str, telegram_id: int) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-Telegram-User-Id": str(telegram_id)}


async def add_user(sessions: async_sessionmaker[AsyncSession]) -> tuple[int, int]:
    telegram_id = randbits(62) + 1
    async with sessions() as session:
        user, _ = await register_user(session, telegram_id)
        return telegram_id, user.id


async def add_item(
    sessions: async_sessionmaker[AsyncSession], owner: int, category_id: int
) -> int:
    async with sessions.begin() as session:
        expense = Expense(
            user_id=owner,
            total_amount=Decimal(1),
            currency="UAH",
            expense_date=datetime.now(UTC),
            source=ExpenseSource.MANUAL,
            items=[
                ExpenseItem(category_id=category_id, name="Test", amount=Decimal(1))
            ],
        )
        session.add(expense)
        await session.flush()
        return expense.items[0].id


@pytest.mark.asyncio
async def test_category_crud_validates_and_isolates_users(
    client: httpx.AsyncClient, token: str, sessions: async_sessionmaker[AsyncSession]
) -> None:
    actor, _ = await add_user(sessions)
    other, _ = await add_user(sessions)
    auth = headers(token, actor)
    other_auth = headers(token, other)
    assert (
        await client.get(CATEGORIES, headers={"X-Telegram-User-Id": str(actor)})
    ).status_code == 401
    assert (
        await client.get(CATEGORIES, headers={"Authorization": f"Bearer {token}"})
    ).status_code == 422
    assert (await client.get(CATEGORIES, headers=headers(token, 0))).status_code == 422
    assert (
        await client.get(CATEGORIES, headers=headers(token, 2**63 - 1))
    ).status_code == 404

    for name in (" ", "а" * 101):
        assert (
            await client.post(CATEGORIES, headers=auth, json={"name": name})
        ).status_code == 422
    created = await client.post(CATEGORIES, headers=auth, json={"name": "  Сад  "})
    assert created.status_code == 201
    category_id = created.json()["id"]
    assert created.json()["name"] == "Сад"
    assert (
        await client.post(CATEGORIES, headers=other_auth, json={"name": "САД"})
    ).status_code == 201
    conflict = await client.post(CATEGORIES, headers=auth, json={"name": "САД"})
    assert (conflict.status_code, conflict.json()["detail"]) == (
        409,
        "category_name_conflict",
    )

    path = f"{CATEGORIES}/{category_id}"
    assert (
        await client.patch(path, headers=other_auth, json={"name": "New"})
    ).status_code == 404
    assert (await client.delete(path, headers=other_auth)).status_code == 404
    assert (
        await client.patch(path, headers=auth, json={"name": "продукти"})
    ).status_code == 409
    renamed = await client.patch(path, headers=auth, json={"name": " САД "})
    assert renamed.status_code == 200 and renamed.json()["name"] == "САД"

    listed = await client.get(CATEGORIES, headers=auth)
    other_list = await client.get(CATEGORIES, headers=other_auth)
    assert listed.status_code == other_list.status_code == 200
    assert {x["id"] for x in listed.json()}.isdisjoint(
        x["id"] for x in other_list.json()
    )
    default_id = next(x["id"] for x in listed.json() if x["name"] == "Інше")
    assert (
        await client.patch(
            f"{CATEGORIES}/{default_id}", headers=auth, json={"name": "Різне"}
        )
    ).status_code == 200
    assert (
        await client.delete(f"{CATEGORIES}/{default_id}", headers=auth)
    ).status_code == 204
    assert (await client.delete(path, headers=auth)).status_code == 204


@pytest.mark.asyncio
@pytest.mark.parametrize("competitor", ["post", "patch"])
async def test_concurrent_case_insensitive_category_writes(
    competitor: str,
    client: httpx.AsyncClient,
    token: str,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    actor, _ = await add_user(sessions)
    auth = headers(token, actor)
    existing = (await client.get(CATEGORIES, headers=auth)).json()[0]["id"]
    path = CATEGORIES if competitor == "post" else f"{CATEGORIES}/{existing}"
    first, second = await asyncio.wait_for(
        asyncio.gather(
            client.post(CATEGORIES, headers=auth, json={"name": "Їжак"}),
            client.request(
                competitor.upper(), path, headers=auth, json={"name": "ЇЖАК"}
            ),
        ),
        timeout=10,
    )
    assert sum(response.status_code == 409 for response in (first, second)) == 1
    assert sum(response.status_code in (200, 201) for response in (first, second)) == 1
    listed = (await client.get(CATEGORIES, headers=auth)).json()
    assert sum(row["name"].lower() == "їжак" for row in listed) == 1


@pytest.mark.asyncio
async def test_delete_reassigns_and_uncategorizes_without_deleting_items(
    client: httpx.AsyncClient, token: str, sessions: async_sessionmaker[AsyncSession]
) -> None:
    actor, owner = await add_user(sessions)
    other, _ = await add_user(sessions)
    auth = headers(token, actor)
    source, target, *_ = (await client.get(CATEGORIES, headers=auth)).json()
    foreign = (await client.get(CATEGORIES, headers=headers(token, other))).json()[0]
    item_id = await add_item(sessions, owner, source["id"])
    path = f"{CATEGORIES}/{source['id']}"
    response = await client.delete(path, headers=auth)
    assert (response.status_code, response.json()["detail"]) == (409, "category_in_use")
    assert (
        await client.delete(path, headers=auth, params={"action": "reassign"})
    ).status_code == 422
    for destination, status in [(source, 422), (foreign, 404), (target, 204)]:
        response = await client.delete(
            path,
            headers=auth,
            params={
                "action": "reassign",
                "target_category_id": destination["id"],
            },
        )
        assert response.status_code == status
    async with sessions() as session:
        assert (await session.get(ExpenseItem, item_id)).category_id == target["id"]
    assert (
        await client.delete(
            f"{CATEGORIES}/{target['id']}",
            headers=auth,
            params={"action": "uncategorize"},
        )
    ).status_code == 204
    async with sessions() as session:
        assert (await session.get(ExpenseItem, item_id)).category_id is None


@pytest.mark.asyncio
async def test_delete_rolls_back_when_a_foreign_reference_remains(
    client: httpx.AsyncClient, token: str, sessions: async_sessionmaker[AsyncSession]
) -> None:
    actor, owner = await add_user(sessions)
    _, foreign_owner = await add_user(sessions)
    auth = headers(token, actor)
    source = (await client.get(CATEGORIES, headers=auth)).json()[0]["id"]
    own_item = await add_item(sessions, owner, source)
    foreign_item = await add_item(sessions, foreign_owner, source)
    response = await client.delete(
        f"{CATEGORIES}/{source}", headers=auth, params={"action": "uncategorize"}
    )
    assert (response.status_code, response.json()["detail"]) == (409, "category_in_use")
    async with sessions() as session:
        assert await session.get(Category, source) is not None
        assert (await session.get(ExpenseItem, own_item)).category_id == source
        assert (await session.get(ExpenseItem, foreign_item)).category_id == source


@pytest.mark.asyncio
async def test_category_delete_and_concurrent_item_insert(
    client: httpx.AsyncClient,
    token: str,
    sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor, owner = await add_user(sessions)
    auth = headers(token, actor)
    source = (await client.get(CATEGORIES, headers=auth)).json()[0]["id"]
    locked = asyncio.Event()
    release = asyncio.Event()
    lock_categories = category_repository.lock_categories

    async def hold_lock(
        session: AsyncSession, user_id: int, ids: list[int]
    ) -> dict[int, Category]:
        result = await lock_categories(session, user_id, ids)
        locked.set()
        await asyncio.wait_for(release.wait(), timeout=5)
        return result

    monkeypatch.setattr(category_repository, "lock_categories", hold_lock)

    async def insert_rejected() -> None:
        with pytest.raises(IntegrityError):
            await add_item(sessions, owner, source)

    async with asyncio.TaskGroup() as tasks:
        deletion = tasks.create_task(
            client.delete(f"{CATEGORIES}/{source}", headers=auth)
        )
        await asyncio.wait_for(locked.wait(), timeout=5)
        tasks.create_task(insert_rejected())
        # Wait until PostgreSQL reports that the insert is blocked on the row lock.
        async with asyncio.timeout(5), sessions() as session:
            while not await session.scalar(
                text(
                    "SELECT EXISTS (SELECT FROM pg_locks WHERE NOT granted "
                    "AND locktype = 'transactionid')"
                )
            ):
                await asyncio.sleep(0.01)
        release.set()
        assert (await deletion).status_code == 204
