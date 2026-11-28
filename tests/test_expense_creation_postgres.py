import asyncio
import os
from base64 import b64encode
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from decimal import Decimal
from io import BytesIO
from secrets import randbits, token_urlsafe
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
import pytest_asyncio
from PIL import Image
from sqlalchemy import URL, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from spend_mind.config import Settings
from spend_mind.integrations import nbu
from spend_mind.integrations.openai import OpenAIErrorKind, OpenAIRequestFailed
from spend_mind.main import app
from spend_mind.models import Category, Expense, ExpenseItem
from spend_mind.repositories import expense as expense_repository
from spend_mind.repositories import expense_item as item_repository
from spend_mind.routers import image_upload
from spend_mind.schemas.expense import ExpenseItemCreate
from spend_mind.schemas.expense_extraction import (
    ClarificationReason,
    ExpenseClarification,
    ExpenseRecognized,
    ExtractedExpense,
    ExtractedExpenseItem,
    ExtractedImageExpense,
)
from spend_mind.services import expense as expense_service
from spend_mind.services import expense_recognition
from spend_mind.services.user_registration import register_user

ENDPOINT = "/internal/expenses"
RECOGNITION = f"{ENDPOINT}/recognize-text"
IMAGE_RECOGNITION = f"{ENDPOINT}/recognize-image"


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


def extraction(**overrides: object) -> ExtractedExpense:
    return ExtractedExpense.model_validate(
        {
            "language": "uk",
            "clarification_reason": None,
            "merchant": None,
            "total_amount": None,
            "currency": None,
            "calendar_date": None,
            "relative_days": None,
            "local_time": None,
            "items": [
                ExtractedExpenseItem(
                    name="Кава", quantity=None, unit=None, amount="12.35"
                ),
                ExtractedExpenseItem(
                    name="Булочка", quantity="2", unit="шт", amount="2.50"
                ),
            ],
        }
        | overrides
    )


def image_bytes() -> bytes:
    buffer = BytesIO()
    with Image.new("RGB", (8, 8)) as image:
        image.save(buffer, format="PNG")
    return buffer.getvalue()


def image_extraction(**overrides: object) -> ExtractedImageExpense:
    return ExtractedImageExpense.model_validate(
        extraction(
            language="en",
            merchant="Market",
            total_amount="15.00",
            currency="UAH",
            calendar_date="2026-11-28",
            local_time="12:30",
            items=[
                ExtractedExpenseItem(
                    name="Coffee", quantity="1", unit=None, amount="10.00"
                ),
                ExtractedExpenseItem(
                    name="Scone", quantity="2", unit=None, amount="10.00"
                ),
            ],
        ).model_dump()
        | {"receipt_discount": "5.00", "discount_basis": "before_discount"}
        | overrides
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("basis", ["before_discount", "after_discount"])
async def test_image_recognition_persists_owner_and_applies_discount_once(
    api: tuple[httpx.AsyncClient, str],
    sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    basis: str,
) -> None:
    client, token = api
    owner, user_id, _ = await add_user(sessions, token)
    raw_image = image_bytes()
    items = image_extraction().items
    if basis == "after_discount":
        items = [item.model_copy(update={"amount": "7.50"}) for item in items]
    result = image_extraction(items=items, discount_basis=basis)
    active_session = None
    original = expense_recognition.recognize_image

    async def observe_session(
        session: AsyncSession, *args: object
    ) -> ExpenseRecognized | ExpenseClarification:
        nonlocal active_session
        active_session = session
        return await original(session, *args)

    async def extract(
        inputs: list[dict], *, instructions: str, schema: type[ExtractedImageExpense]
    ) -> ExtractedImageExpense:
        assert active_session is not None and not active_session.in_transaction()
        assert schema is ExtractedImageExpense
        assert "untrusted data" in instructions
        assert "Never round, truncate, or drop" in instructions
        assert "without requesting clarification solely for its absence" in instructions
        part = inputs[0]["content"][0]
        assert part["type"] == "input_image"
        assert part["image_url"].startswith("data:image/png;base64,")
        return result

    monkeypatch.setattr(expense_recognition, "recognize_image", observe_session)
    adapter = Mock(create_structured_response=AsyncMock(side_effect=extract))
    monkeypatch.setattr(app.state, "openai_responses", adapter, raising=False)
    response = await client.post(
        IMAGE_RECOGNITION,
        headers=owner,
        files={"image": ("receipt.txt", raw_image, "text/plain")},
    )
    assert response.status_code == 201, response.text
    record = response.json()["expense"]
    assert record["status"] == "pending" and record["source"] == "image"
    assert record["expense_date"] == "2026-11-28T10:30:00Z"
    assert record["total_amount"] == "15.00"
    assert [item["amount"] for item in record["items"]] == ["7.50", "7.50"]
    async with sessions() as session:
        saved = await session.get(Expense, record["id"])
        assert saved is not None and saved.user_id == user_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("calendar_date", "local_time", "expected_time"),
    [
        (None, None, "2026-11-27T22:30:00Z"),
        ("2026-11-26", None, "2026-11-25T22:30:00Z"),
        (None, "12:30", "2026-11-28T10:30:00Z"),
    ],
)
async def test_image_recognition_uses_existing_defaults_for_missing_fields(
    api: tuple[httpx.AsyncClient, str],
    sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    calendar_date: str | None,
    local_time: str | None,
    expected_time: str,
) -> None:
    client, token = api
    owner, _, _ = await add_user(sessions, token)
    fixed_clock = (
        datetime(2026, 11, 27, 22, 30, tzinfo=UTC),
        datetime(2026, 11, 28, 0, 30),
    )
    monkeypatch.setattr(
        expense_repository, "get_kyiv_reference", AsyncMock(return_value=fixed_clock)
    )
    candidate = image_extraction(
        currency=None,
        calendar_date=calendar_date,
        local_time=local_time,
        items=[
            item.model_copy(update={"quantity": None})
            for item in image_extraction().items
        ],
    )
    adapter = Mock(create_structured_response=AsyncMock(return_value=candidate))
    monkeypatch.setattr(app.state, "openai_responses", adapter, raising=False)
    response = await client.post(
        IMAGE_RECOGNITION,
        headers=owner,
        files={"image": ("receipt.png", image_bytes())},
    )
    assert response.status_code == 201, response.text
    record = response.json()["expense"]
    assert record["expense_date"] == expected_time
    assert record["currency"] == "UAH"
    assert [item["quantity"] for item in record["items"]] == ["1", "1"]
    assert [item["amount"] for item in record["items"]] == ["7.50", "7.50"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("overrides", "reason", "stage", "diagnostic"),
    [
        ({"items": []}, "missing_items", "item_validation", "-"),
        (
            {
                "items": [
                    ExtractedExpenseItem(
                        name="Private item", quantity=None, unit=None, amount=None
                    )
                ]
            },
            "missing_amount",
            "item_validation",
            "-",
        ),
        ({"discount_basis": None}, "ambiguous_financial_data", "discount_basis", "-"),
        (
            {"receipt_discount": "5.005"},
            "invalid_financial_data",
            "discount_validation",
            "receipt_discount:value_error",
        ),
        ({"local_time": "25:30"}, "ambiguous_date", "date_validation", "-"),
        ({"total_amount": "15.01"}, "expense_total_mismatch", "reconciliation", "-"),
        (
            {"total_amount": "15,00"},
            "invalid_financial_data",
            "expense_validation",
            "total_amount:value_error",
        ),
        (
            {
                "items": [
                    ExtractedExpenseItem(
                        name="Private item",
                        quantity="1.2345",
                        unit=None,
                        amount="10.001",
                    )
                ]
            },
            "invalid_financial_data",
            "expense_validation",
            "items[].quantity:value_error,items[].amount:value_error",
        ),
        (
            {"clarification_reason": ClarificationReason.AMBIGUOUS_FINANCIAL_DATA},
            "ambiguous_financial_data",
            "ai",
            "-",
        ),
    ],
)
async def test_image_recognition_requests_clarification_without_writing(
    api: tuple[httpx.AsyncClient, str],
    sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    overrides: dict,
    reason: str,
    stage: str,
    diagnostic: str,
) -> None:
    client, token = api
    owner, user_id, _ = await add_user(sessions, token)
    adapter = Mock(
        create_structured_response=AsyncMock(
            return_value=image_extraction(merchant="Private merchant", **overrides)
        )
    )
    monkeypatch.setattr(app.state, "openai_responses", adapter, raising=False)
    raw_image = image_bytes()
    response = await client.post(
        IMAGE_RECOGNITION,
        headers=owner,
        files={"image": ("private-payment.png", raw_image)},
    )
    assert response.status_code == 200
    assert response.json()["outcome"] == "clarification_required"
    assert response.json()["reason"] == reason
    assert (
        f"stage={stage} reason={reason} validation_errors={diagnostic}" in caplog.text
    )
    for secret in (
        "Private merchant",
        "Private item",
        "private-payment.png",
        "5.005",
        "1.2345",
        "10.001",
        "15,00",
        "15.01",
        "25:30",
        b64encode(raw_image).decode("ascii"),
        owner["X-Telegram-User-Id"],
        token,
    ):
        assert secret not in caplog.text
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
async def test_image_recognition_checks_owner_before_decode_and_blocks_invalid_files(
    api: tuple[httpx.AsyncClient, str],
    sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, token = api
    owner, _, _ = await add_user(sessions, token)
    adapter = Mock(create_structured_response=AsyncMock())
    monkeypatch.setattr(app.state, "openai_responses", adapter, raising=False)
    validate = Mock(wraps=image_upload.validate_image)
    monkeypatch.setattr(image_upload, "validate_image", validate)
    files = {"image": ("receipt.png", image_bytes())}
    assert (await client.post(IMAGE_RECOGNITION, files=files)).status_code == 401
    unknown = owner | {"X-Telegram-User-Id": str(2**63 - 1)}
    assert (
        await client.post(IMAGE_RECOGNITION, headers=unknown, files=files)
    ).status_code == 404
    validate.assert_not_called()

    for content, status in [(b"private document", 415), (b"\xff\xd8\xffprivate", 422)]:
        response = await client.post(
            IMAGE_RECOGNITION,
            headers=owner,
            files={"image": ("private.png", content, "image/png")},
        )
        assert response.status_code == status
        assert "private" not in response.text
    adapter.create_structured_response.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "candidate", "expected_date", "expected_currency"),
    [
        (
            "Кава 12,35 і дві булочки разом за 2,50",
            extraction(),
            "2026-11-27T22:30:00Z",
            "UAH",
        ),
        (
            "Yesterday at 09:15, coffee and buns at Market, total EUR 14.85.",
            extraction(
                language="en",
                merchant="Market",
                total_amount="14.85",
                currency="EUR",
                relative_days=-1,
                local_time="09:15",
            ),
            "2026-11-27T07:15:00Z",
            "EUR",
        ),
        (
            "Учора кава й булочки",
            extraction(relative_days=-1),
            "2026-11-26T22:30:00Z",
            "UAH",
        ),
    ],
)
async def test_recognition_creates_pending_expense_outside_openai_transaction(
    api: tuple[httpx.AsyncClient, str],
    sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    message: str,
    candidate: ExtractedExpense,
    expected_date: str,
    expected_currency: str,
) -> None:
    client, token = api
    owner, user_id, _ = await add_user(sessions, token)
    service_call = expense_recognition.recognize_text
    active_session = None

    async def observe_session(
        session: AsyncSession, telegram_user_id: int, adapter: Mock, text: str
    ) -> ExpenseRecognized | ExpenseClarification:
        nonlocal active_session
        active_session = session
        return await service_call(session, telegram_user_id, adapter, text)

    async def extract(
        text: str, *, instructions: str, schema: type[ExtractedExpense]
    ) -> ExtractedExpense:
        assert active_session is not None and not active_session.in_transaction()
        assert text == message
        assert "untrusted data" in instructions and message not in instructions
        assert "2026-11-28T00:30:00" in instructions
        assert schema is ExtractedExpense
        return candidate

    fixed_clock = AsyncMock(
        return_value=(
            datetime(2026, 11, 27, 22, 30, tzinfo=UTC),
            datetime(2026, 11, 28, 0, 30),
        )
    )
    monkeypatch.setattr(expense_repository, "get_kyiv_reference", fixed_clock)
    monkeypatch.setattr(expense_recognition, "recognize_text", observe_session)
    adapter = Mock(create_structured_response=AsyncMock(side_effect=extract))
    monkeypatch.setattr(app.state, "openai_responses", adapter, raising=False)
    response = await client.post(RECOGNITION, headers=owner, json={"text": message})
    assert response.status_code == 201, response.text
    assert response.json()["outcome"] == "created"
    record = response.json()["expense"]
    assert record["status"] == "pending" and record["source"] == "text"
    assert record["currency"] == expected_currency
    assert record["created_at"] and record["expense_date"] == expected_date
    assert (
        record["total_amount"] == "14.85" and record["merchant"] == candidate.merchant
    )
    assert [item["quantity"] for item in record["items"]] == ["1", "2"]
    async with sessions() as session:
        saved = await session.get(Expense, record["id"])
        assert saved is not None and saved.user_id == user_id
        assert saved.total_amount == Decimal("14.85")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("candidate", "expected"),
    [
        (
            extraction(clarification_reason=ClarificationReason.MULTIPLE_TRANSACTIONS),
            "multiple_transactions",
        ),
        (
            extraction(clarification_reason=ClarificationReason.AMBIGUOUS_CURRENCY),
            "ambiguous_currency",
        ),
        (extraction(total_amount="14.84"), "expense_total_mismatch"),
        (extraction(currency="GBP"), "invalid_financial_data"),
        (extraction(items=[]), "missing_items"),
        (
            extraction(
                items=[
                    ExtractedExpenseItem(
                        name="A", quantity=None, unit=None, amount=None
                    )
                ]
            ),
            "missing_amount",
        ),
        (extraction(calendar_date="2026-03-29", local_time="03:30"), "ambiguous_date"),
    ],
)
async def test_recognition_clarification_leaves_no_expense(
    api: tuple[httpx.AsyncClient, str],
    sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    candidate: ExtractedExpense,
    expected: str,
) -> None:
    client, token = api
    owner, user_id, _ = await add_user(sessions, token)
    adapter = Mock(create_structured_response=AsyncMock(return_value=candidate))
    monkeypatch.setattr(app.state, "openai_responses", adapter, raising=False)
    response = await client.post(RECOGNITION, headers=owner, json={"text": "Кава"})
    assert response.status_code == 200
    assert response.json()["outcome"] == "clarification_required"
    assert response.json()["reason"] == expected
    assert response.json()["question"]
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
async def test_recognition_validates_request_without_echoing_text(
    api: tuple[httpx.AsyncClient, str],
    sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, token = api
    owner, _, _ = await add_user(sessions, token)
    adapter = Mock(create_structured_response=AsyncMock())
    monkeypatch.setattr(app.state, "openai_responses", adapter, raising=False)
    assert (await client.post(RECOGNITION, json={"text": "Кава"})).status_code == 401
    for invalid in (
        {"text": " "},
        {"text": "а" * 4001},
        {"text": "Кава", "user_id": 10},
    ):
        response = await client.post(RECOGNITION, headers=owner, json=invalid)
        assert response.status_code == 422
        assert response.json() == {"detail": "invalid_expense_recognition_request"}
    unknown = owner | {"X-Telegram-User-Id": str(2**63 - 1)}
    assert (
        await client.post(RECOGNITION, headers=unknown, json={"text": "Кава"})
    ).status_code == 404
    adapter.create_structured_response.assert_not_called()

    app.state.openai_responses = None
    response = await client.post(RECOGNITION, headers=owner, json={"text": "а" * 4000})
    assert (response.status_code, response.json()["detail"]) == (
        503,
        "openai_not_configured",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "status_code"),
    [
        (OpenAIErrorKind.REFUSAL, 422),
        (OpenAIErrorKind.INCOMPLETE, 502),
        (OpenAIErrorKind.FAILED, 502),
        (OpenAIErrorKind.INVALID_OUTPUT, 502),
        (OpenAIErrorKind.TIMEOUT, 503),
    ],
)
async def test_recognition_maps_openai_failures(
    api: tuple[httpx.AsyncClient, str],
    sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    kind: OpenAIErrorKind,
    status_code: int,
) -> None:
    client, token = api
    owner, user_id, _ = await add_user(sessions, token)
    adapter = Mock(
        create_structured_response=AsyncMock(side_effect=OpenAIRequestFailed(kind))
    )
    monkeypatch.setattr(app.state, "openai_responses", adapter, raising=False)
    response = await client.post(RECOGNITION, headers=owner, json={"text": "Кава"})
    assert response.status_code == status_code
    assert response.json() == {"detail": kind.value}
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
async def test_expense_list_filters_by_owner_status_date_and_cursor(
    api: tuple[httpx.AsyncClient, str], sessions: async_sessionmaker[AsyncSession]
) -> None:
    client, token = api
    owner, _, _ = await add_user(sessions, token)
    other, _, _ = await add_user(sessions, token)
    first = await create_pending(client, owner)
    second = await create_pending(client, owner)
    rejected = await create_pending(client, owner)
    pending = await create_pending(client, owner)
    foreign = await create_pending(client, other)
    for headers, expense_id in [(owner, first), (owner, second), (other, foreign)]:
        assert (
            await client.post(f"{ENDPOINT}/{expense_id}/confirm", headers=headers)
        ).status_code == 200
    assert (
        await client.post(f"{ENDPOINT}/{rejected}/reject", headers=owner)
    ).status_code == 200
    params = {"limit": 1, "date_from": "2026-11-28", "date_to": "2026-11-28"}
    page = await client.get(ENDPOINT, headers=owner, params=params)
    assert page.status_code == 200, page.text
    assert [item["id"] for item in page.json()["items"]] == [second]
    next_page = await client.get(
        ENDPOINT, headers=owner, params=params | {"cursor": page.json()["next_cursor"]}
    )
    assert [item["id"] for item in next_page.json()["items"]] == [first]
    assert next_page.json()["next_cursor"] is None
    for state, expected in [
        ("pending", [pending]),
        ("rejected", [rejected]),
        ("all", [pending, rejected, second, first]),
    ]:
        response = await client.get(ENDPOINT, headers=owner, params={"status": state})
        assert [item["id"] for item in response.json()["items"]] == expected
    response = await client.get(
        ENDPOINT, headers=owner, params={"date_to": "2026-11-27"}
    )
    assert response.json() == {"items": [], "next_cursor": None}
    for filters in [
        {"cursor": "!"},
        {"limit": 101},
        {"status": "invalid"},
        {"date_from": "1760000000"},
        {"user_id": 123},
        {"date_from": "2026-11-29", "date_to": "2026-11-28"},
    ]:
        assert (
            await client.get(ENDPOINT, headers=owner, params=filters)
        ).status_code == 422


@pytest.mark.asyncio
async def test_patch_rolls_back_expense_and_existing_items_on_insert_failure(
    api: tuple[httpx.AsyncClient, str],
    sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, token = api
    owner, _, _ = await add_user(sessions, token)
    expense_id = await create_pending(client, owner)
    path = f"{ENDPOINT}/{expense_id}"
    before = await client.get(path, headers=owner)
    build_items = expense_service.build_expense_items

    def build_invalid_items(
        items: list[ExpenseItemCreate], amounts: list[Decimal]
    ) -> list[ExpenseItem]:
        result = build_items(items, amounts)
        result[-1].amount = Decimal(-1)
        return result

    monkeypatch.setattr(expense_service, "build_expense_items", build_invalid_items)
    with pytest.raises(IntegrityError):
        await client.patch(
            path,
            headers=owner | {"If-Match": before.headers["etag"]},
            json={
                "merchant": "Changed",
                "items": [{"name": "Replacement", "amount": "2.99"}],
            },
        )
    after = await client.get(path, headers=owner)
    assert after.json() == before.json()
    assert after.headers["etag"] == before.headers["etag"]


@pytest.mark.asyncio
async def test_patch_reuses_validation_and_preserves_already_discounted_items(
    api: tuple[httpx.AsyncClient, str], sessions: async_sessionmaker[AsyncSession]
) -> None:
    client, token = api
    owner, _, _ = await add_user(sessions, token)
    other, _, foreign_category = await add_user(sessions, token)
    expense_id = await create_pending(client, owner)
    path = f"{ENDPOINT}/{expense_id}"
    detail = await client.get(path, headers=owner)
    assert detail.status_code == 200
    assert (await client.get(path, headers=other)).status_code == 404
    assert (
        await client.patch(path, headers=owner, json={"merchant": "Shop"})
    ).status_code == 428
    tag = detail.headers["etag"]
    tagged = owner | {"If-Match": tag}
    assert (
        await client.patch(
            path, headers=other | {"If-Match": tag}, json={"merchant": "X"}
        )
    ).status_code == 404
    for changes in (
        {},
        {"status": "confirmed"},
        {"items": []},
        {"currency": "GBP"},
        {"receipt_discount": "0.01"},
        {"total_amount": "1.10"},
        {"currency": None},
    ):
        assert (
            await client.patch(path, headers=tagged, json=changes)
        ).status_code == 422
    replacement = [{"name": "C", "amount": "2.99", "category_id": foreign_category}]
    assert (
        await client.patch(path, headers=tagged, json={"items": replacement})
    ).status_code == 404
    assert (await client.get(path, headers=owner)).headers["etag"] == tag

    updated = await client.patch(path, headers=tagged, json={"merchant": "Shop"})
    assert updated.status_code == 200
    assert updated.json()["items"] == detail.json()["items"]
    assert [item["amount"] for item in updated.json()["items"]] == ["1.00", "1.99"]
    assert updated.headers["etag"] != tag
    assert (
        await client.patch(path, headers=tagged, json={"merchant": None})
    ).status_code == 412

    replaced = await client.patch(
        path,
        headers=owner | {"If-Match": updated.headers["etag"]},
        json={
            "merchant": None,
            "total_amount": "2",
            "receipt_discount": "1",
            "items": [{"name": "New", "amount": "3"}],
        },
    )
    assert replaced.status_code == 200
    assert replaced.json()["merchant"] is None
    assert [item["amount"] for item in replaced.json()["items"]] == ["2.00"]
    assert (await client.get(path, headers=owner)).headers["etag"] == replaced.headers[
        "etag"
    ]
    async with sessions() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ExpenseItem)
                .where(ExpenseItem.expense_id == expense_id)
            )
            == 1
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["pending", "confirmed", "rejected"])
async def test_delete_any_status_requires_confirmation_and_current_etag(
    api: tuple[httpx.AsyncClient, str],
    sessions: async_sessionmaker[AsyncSession],
    state: str,
) -> None:
    client, token = api
    owner, _, _ = await add_user(sessions, token)
    other, _, _ = await add_user(sessions, token)
    expense_id = await create_pending(client, owner)
    path = f"{ENDPOINT}/{expense_id}"
    actions = {"confirmed": "confirm", "rejected": "reject"}
    if state in actions:
        assert (
            await client.post(f"{path}/{actions[state]}", headers=owner)
        ).status_code == 200
    detail = await client.get(path, headers=owner)
    assert detail.json()["status"] == state
    tag = detail.headers["etag"]
    if state != "pending":
        assert (
            await client.patch(
                path, headers=owner | {"If-Match": tag}, json={"merchant": "X"}
            )
        ).status_code == 409
    assert (await client.delete(path, headers=owner)).status_code == 422
    confirmed_path = f"{path}?confirm=true"
    assert (await client.delete(confirmed_path, headers=owner)).status_code == 428
    assert (
        await client.delete(confirmed_path, headers=other | {"If-Match": tag})
    ).status_code == 404
    assert (
        await client.delete(confirmed_path, headers=owner | {"If-Match": '"old"'})
    ).status_code == 412
    deleted = await client.delete(confirmed_path, headers=owner | {"If-Match": tag})
    assert deleted.status_code == 204 and not deleted.content
    assert (await client.get(path, headers=owner)).status_code == 404
    async with sessions() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ExpenseItem)
                .where(ExpenseItem.expense_id == expense_id)
            )
            == 0
        )


@pytest.mark.asyncio
async def test_parallel_edits_with_same_tag_only_apply_once(
    api: tuple[httpx.AsyncClient, str], sessions: async_sessionmaker[AsyncSession]
) -> None:
    client, token = api
    owner, _, _ = await add_user(sessions, token)
    expense_id = await create_pending(client, owner)
    path = f"{ENDPOINT}/{expense_id}"
    tag = (await client.get(path, headers=owner)).headers["etag"]
    results = await asyncio.wait_for(
        asyncio.gather(
            *(
                client.patch(
                    path, headers=owner | {"If-Match": tag}, json={"merchant": name}
                )
                for name in ("Shop A", "Shop B")
            )
        ),
        timeout=5,
    )
    assert sorted(response.status_code for response in results) == [200, 412]


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["edit", "delete"])
async def test_pending_confirmation_detects_concurrent_mutation(
    api: tuple[httpx.AsyncClient, str],
    sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    client, token = api
    owner, _, _ = await add_user(sessions, token)
    expense_id = await create_pending(client, owner, "USD")
    path = f"{ENDPOINT}/{expense_id}"
    tag = (await client.get(path, headers=owner)).headers["etag"]
    waiting = asyncio.Event()
    release = asyncio.Event()

    async def rate(*args: object) -> Decimal:
        waiting.set()
        await asyncio.wait_for(release.wait(), timeout=5)
        return Decimal(2)

    monkeypatch.setattr(nbu, "get_historical_rate", rate)
    async with asyncio.TaskGroup() as tasks:
        confirm = tasks.create_task(client.post(f"{path}/confirm", headers=owner))
        await asyncio.wait_for(waiting.wait(), timeout=5)
        tagged = owner | {"If-Match": tag}
        if mutation == "edit":
            response = await client.patch(
                path,
                headers=tagged,
                json={"items": [{"name": "Different", "amount": "2.99"}]},
            )
            assert response.status_code == 200
        else:
            response = await client.delete(f"{path}?confirm=true", headers=tagged)
            assert response.status_code == 204
        release.set()
        outcome = await confirm
    assert (outcome.status_code, outcome.json()["detail"]) == (
        (409, "expense_changed") if mutation == "edit" else (404, "expense_not_found")
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["edit", "delete"])
@pytest.mark.parametrize("action", ["reassign", "uncategorize"])
async def test_category_mutation_changes_tag_without_deadlock(
    api: tuple[httpx.AsyncClient, str],
    sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
    action: str,
) -> None:
    client, token = api
    owner, _, source = await add_user(sessions, token)
    categories = await client.get("/internal/categories", headers=owner)
    destination = next(row["id"] for row in categories.json() if row["id"] != source)
    created = await client.post(ENDPOINT, headers=owner, json=payload(source))
    path = f"{ENDPOINT}/{created.json()['id']}"
    detail = await client.get(path, headers=owner)
    tag = detail.headers["etag"]
    paused = asyncio.Event()
    release = asyncio.Event()
    move = item_repository.move_category_items

    async def pause_move(
        session: AsyncSession, user_id: int, source_id: int, target_id: int | None
    ) -> None:
        paused.set()
        await asyncio.wait_for(release.wait(), timeout=5)
        await move(session, user_id, source_id, target_id)

    monkeypatch.setattr(item_repository, "move_category_items", pause_move)
    params = {"action": action}
    if action == "reassign":
        params["target_category_id"] = str(destination)
    async with asyncio.TaskGroup() as tasks:
        category_delete = tasks.create_task(
            client.delete(
                f"/internal/categories/{source}", headers=owner, params=params
            )
        )
        await asyncio.wait_for(paused.wait(), timeout=5)
        tagged = owner | {"If-Match": tag}
        request = (
            client.patch(path, headers=tagged, json={"merchant": "Stale"})
            if mutation == "edit"
            else client.delete(f"{path}?confirm=true", headers=tagged)
        )
        expense_mutation = tasks.create_task(request)
        async with asyncio.timeout(5), sessions() as session:
            while not await session.scalar(
                text(
                    "SELECT EXISTS (SELECT FROM pg_locks WHERE NOT granted AND locktype = 'transactionid')"
                )
            ):
                await asyncio.sleep(0.01)
        release.set()
        assert (await category_delete).status_code == 204
        assert (await expense_mutation).status_code == 412
    current = await client.get(path, headers=owner)
    assert current.headers["etag"] != tag
    assert current.json()["items"][0]["category_id"] == (
        destination if action == "reassign" else None
    )


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
