import os
from collections.abc import AsyncIterator
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from secrets import randbits, token_urlsafe

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import URL
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from spend_mind.config import Settings
from spend_mind.main import app
from spend_mind.models import Category, Expense, ExpenseItem, Product, ProductType
from spend_mind.models.expense import ExpenseSource, ExpenseStatus
from spend_mind.schemas.expense_analytics import ExpenseAnalyticsQuery
from spend_mind.services.expense_analytics import analyze_expenses
from spend_mind.services.user_registration import register_user

ENDPOINT = "/internal/expenses/analyze"


@pytest_asyncio.fixture
async def sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    port = os.getenv("SPEND_MIND_EXPENSE_TEST_PORT")
    if port is None:
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
    sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
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


async def make_owner(
    sessions: async_sessionmaker[AsyncSession], token: str
) -> tuple[int, dict[str, str]]:
    telegram_id = randbits(62) + 1
    async with sessions() as session:
        user, _ = await register_user(session, telegram_id)
    return user.id, {
        "Authorization": f"Bearer {token}",
        "X-Telegram-User-Id": str(telegram_id),
    }


def transaction(
    user_id: int,
    items: list[ExpenseItem],
    *,
    currency: str = "UAH",
    rate: Decimal = Decimal(1),
    status: ExpenseStatus = ExpenseStatus.CONFIRMED,
    merchant: str | None = None,
    expense_date: str = "2026-11-30T21:59:59Z",
) -> Expense:
    total = sum((item.amount for item in items), Decimal(0))
    confirmed = status == ExpenseStatus.CONFIRMED
    return Expense(
        user_id=user_id,
        items=items,
        merchant=merchant,
        total_amount=total,
        currency=currency,
        exchange_rate=rate if confirmed else None,
        amount_uah=(total * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if confirmed
        else None,
        expense_date=datetime.fromisoformat(expense_date),
        source=ExpenseSource.MANUAL,
        status=status,
    )


def metric_values(amount: str, count: int, average: str | None) -> dict:
    return {
        "total_amount_uah": amount,
        "transaction_count": count,
        "average_transaction_amount_uah": average,
    }


async def report(
    client: httpx.AsyncClient, headers: dict[str, str], **filters: object
) -> dict:
    response = await client.post(ENDPOINT, headers=headers, json=filters)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize(
    "filters",
    [
        {"category_id": 1, "uncategorized": True},
        {"group_by": "year"},
        {"date_from": "2026-12-01", "date_to": "2026-11-30"},
        {"date_from": "1760000000"},
        {"user_id": 1},
        {"status": "pending"},
        {"currency": "EUR"},
        {"product_name": " "},
    ],
)
def test_analytics_filters_reject_invalid_values(filters: dict) -> None:
    with pytest.raises(ValueError):
        ExpenseAnalyticsQuery.model_validate(filters)


@pytest.mark.asyncio
async def test_analytics_filters_groupings_and_isolation(
    api: tuple[httpx.AsyncClient, str], sessions: async_sessionmaker[AsyncSession]
) -> None:
    client, token = api
    owner, headers = await make_owner(sessions, token)
    foreign, foreign_headers = await make_owner(sessions, token)
    async with sessions.begin() as session:
        first = Category(user_id=owner, name="My groceries")
        second = Category(user_id=owner, name="My cafe")
        foreign_category = Category(user_id=foreign, name="Foreign private label")
        product = Product(
            name="Coffee catalog", product_type=ProductType(name=f"Type {owner}")
        )
        session.add_all([first, second, foreign_category, product])
        await session.flush()
        session.add_all(
            [
                transaction(
                    owner,
                    [
                        ExpenseItem(
                            name="Кава",
                            amount=Decimal(4),
                            category_id=first.id,
                            product_id=product.id,
                        ),
                        ExpenseItem(
                            name="КАВА",
                            amount=Decimal(2),
                            category_id=first.id,
                            product_id=product.id,
                        ),
                        ExpenseItem(
                            name="Bread", amount=Decimal(4), category_id=second.id
                        ),
                    ],
                    currency="USD",
                    rate=Decimal("2.5"),
                    merchant="Store%",
                ),
                transaction(
                    owner,
                    [
                        ExpenseItem(
                            name="кава", amount=Decimal(1), category_id=second.id
                        ),
                        ExpenseItem(
                            name="Milk",
                            amount=Decimal(2),
                            category_id=foreign_category.id,
                        ),
                    ],
                    currency="EUR",
                    rate=Decimal(40),
                    merchant="store%",
                    expense_date="2026-11-30T22:00:00Z",
                ),
                transaction(
                    owner,
                    [ExpenseItem(name="КАВА", amount=Decimal("2.01"))],
                    expense_date="2026-11-30T22:00:00Z",
                ),
                transaction(
                    owner,
                    [ExpenseItem(name="Pending", amount=Decimal(100))],
                    status=ExpenseStatus.PENDING,
                ),
                transaction(
                    owner,
                    [ExpenseItem(name="Rejected", amount=Decimal(100))],
                    status=ExpenseStatus.REJECTED,
                ),
                transaction(
                    foreign,
                    [
                        ExpenseItem(
                            name="Private foreign item",
                            amount=Decimal(999),
                            category_id=first.id,
                        )
                    ],
                    merchant="Private foreign merchant",
                ),
            ]
        )
    assert await report(client, headers) == {
        "currency": "UAH",
        "summary": metric_values("147.01", 3, "49.00"),
        "groups": [],
    }
    assert (await report(client, headers, merchant="STORE%"))[
        "summary"
    ] == metric_values("145.00", 2, "72.50")
    assert (await report(client, headers, merchant="store_"))[
        "summary"
    ] == metric_values("0.00", 0, None)
    assert (await report(client, headers, product_name="КАвА"))[
        "summary"
    ] == metric_values("57.01", 3, "19.00")
    assert (await report(client, headers, category_id=first.id, product_name="Bread"))[
        "summary"
    ] == metric_values("0.00", 0, None)
    assert (await report(client, headers, category_id=first.id, product_name="кава"))[
        "summary"
    ] == metric_values("15.00", 1, "15.00")
    grouped = await report(client, headers, group_by="category")
    assert grouped["summary"] == metric_values("147.01", 3, "49.00")
    owned_groups = [
        {
            "key": str(first.id),
            "label": first.name,
            **metric_values("15.00", 1, "15.00"),
        },
        {
            "key": str(second.id),
            "label": second.name,
            **metric_values("50.00", 2, "25.00"),
        },
    ]
    assert grouped["groups"] == [
        *sorted(owned_groups, key=lambda group: group["key"]),
        {"key": None, "label": "Без категорії", **metric_values("82.01", 2, "41.01")},
    ]
    assert await report(client, headers, group_by="category") == grouped
    assert (await report(client, headers, uncategorized=True, group_by="category"))[
        "groups"
    ] == [grouped["groups"][-1]]
    reverse = await report(client, foreign_headers, group_by="category")
    assert reverse["groups"] == [
        {"key": None, "label": "Без категорії", **metric_values("999.00", 1, "999.00")}
    ]
    for inaccessible in (foreign_category.id, 2**31 - 1):
        response = await client.post(
            ENDPOINT, headers=headers, json={"category_id": inaccessible}
        )
        assert (response.status_code, response.json()["detail"]) == (
            404,
            "category_not_found",
        )

    products = (await report(client, headers, group_by="product"))["groups"]
    assert [group["key"] for group in products] == [
        "name:bread",
        "name:milk",
        "name:кава",
        f"product:{product.id}",
    ]
    assert {row["key"]: row["transaction_count"] for row in products} == {
        "name:bread": 1,
        "name:milk": 1,
        "name:кава": 2,
        f"product:{product.id}": 1,
    }
    merchants = (await report(client, headers, group_by="merchant"))["groups"]
    assert [group["key"] for group in merchants] == ["store%", None]
    assert [group["total_amount_uah"] for group in merchants] == ["145.00", "2.01"]
    for grouping, keys in [
        ("day", ["2026-11-30", "2026-12-01"]),
        ("month", ["2026-11", "2026-12"]),
    ]:
        groups = (await report(client, headers, group_by=grouping))["groups"]
        assert [group["key"] for group in groups] == keys
        assert [group["total_amount_uah"] for group in groups] == ["25.00", "122.01"]
    assert (
        await report(client, headers, date_from="2026-12-01", date_to="2026-12-01")
    )["summary"] == metric_values("122.01", 2, "61.01")


@pytest.mark.asyncio
async def test_analytics_rounds_group_contributions_and_counts_single_transaction(
    api: tuple[httpx.AsyncClient, str], sessions: async_sessionmaker[AsyncSession]
) -> None:
    client, token = api
    owner, headers = await make_owner(sessions, token)
    async with sessions.begin() as session:
        category = Category(user_id=owner, name="Example")
        session.add(category)
        await session.flush()
        session.add(
            transaction(
                owner,
                [
                    ExpenseItem(
                        name="A", amount=Decimal("0.01"), category_id=category.id
                    ),
                    ExpenseItem(name="B", amount=Decimal("0.01")),
                ],
                currency="USD",
                rate=Decimal("1.5"),
            )
        )
    result = await report(client, headers, group_by="category")
    assert result["summary"] == metric_values("0.03", 1, "0.03")
    assert [group["total_amount_uah"] for group in result["groups"]] == ["0.02", "0.02"]
    assert all(group["transaction_count"] == 1 for group in result["groups"])
    async with sessions() as session:
        direct = await analyze_expenses(
            session, user_id=owner, query=ExpenseAnalyticsQuery()
        )
    assert direct.summary.total_amount_uah == Decimal("0.03")


@pytest.mark.asyncio
async def test_analytics_requires_internal_auth_and_registered_actor(
    api: tuple[httpx.AsyncClient, str], sessions: async_sessionmaker[AsyncSession]
) -> None:
    client, token = api
    _, headers = await make_owner(sessions, token)
    assert (await client.post(ENDPOINT, json={})).status_code == 401
    unknown = headers | {"X-Telegram-User-Id": str(2**63 - 1)}
    assert (await client.post(ENDPOINT, headers=unknown, json={})).status_code == 404
    assert (
        await client.post(ENDPOINT, headers=headers, json={"user_id": 1})
    ).status_code == 422
    assert await report(client, headers, group_by="day") == {
        "currency": "UAH",
        "summary": metric_values("0.00", 0, None),
        "groups": [],
    }
