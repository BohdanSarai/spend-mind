import asyncio
import os
from collections.abc import AsyncIterator
from secrets import randbits

import pytest
import pytest_asyncio
from sqlalchemy import URL, delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from spend_mind.models import Category, User
from spend_mind.services import user_registration

EXPECTED_NAMES = {
    "Продукти",
    "Кафе та ресторани",
    "Транспорт",
    "Автомобіль",
    "Житло та комунальні послуги",
    "Здоров'я",
    "Одяг та взуття",
    "Розваги",
    "Освіта",
    "Підписки",
    "Побут",
    "Подарунки",
    "Інше",
}


@pytest_asyncio.fixture
async def sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    port = os.getenv("SPEND_MIND_REGISTRATION_TEST_PORT")
    if not port:
        pytest.skip("Requires an isolated registration_check PostgreSQL instance")
    engine = create_async_engine(
        URL.create(
            "postgresql+asyncpg",
            username="registration_check",
            host="127.0.0.1",
            port=int(port),
            database="registration_check",
        )
    )
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def category_names(session: AsyncSession, user_id: int) -> list[str]:
    return list(
        await session.scalars(select(Category.name).where(Category.user_id == user_id))
    )


@pytest.mark.asyncio
async def test_registration_seeds_once_and_isolates_categories(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    first_id, second_id = randbits(62) + 1, randbits(62) + 1
    async with sessions() as session:
        first, created = await user_registration.register_user(session, first_id)
        assert created
        second, created = await user_registration.register_user(session, second_id)
        assert created and first.id != second.id
        async with session.begin():
            for user_id in (first.id, second.id):
                assert sorted(await category_names(session, user_id)) == sorted(
                    EXPECTED_NAMES
                )
            await session.execute(
                delete(Category).where(
                    Category.user_id == first.id, Category.name == "Інше"
                )
            )
        existing, created = await user_registration.register_user(session, first_id)
        assert not created and existing.id == first.id
        assert sorted(await category_names(session, first.id)) == sorted(
            EXPECTED_NAMES - {"Інше"}
        )
        assert sorted(await category_names(session, second.id)) == sorted(
            EXPECTED_NAMES
        )


@pytest.mark.asyncio
async def test_concurrent_registration_creates_one_user_and_category_set(
    sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    telegram_id = randbits(62) + 1
    lookup = user_registration.user.get_by_telegram_id
    barrier = asyncio.Barrier(2)

    async def synchronized_lookup(
        session: AsyncSession, telegram_user_id: int
    ) -> User | None:
        found = await lookup(session, telegram_user_id)
        if found is None:
            await asyncio.wait_for(barrier.wait(), timeout=5)
        return found

    monkeypatch.setattr(
        user_registration.user, "get_by_telegram_id", synchronized_lookup
    )
    async with sessions() as left, sessions() as right:
        first, second = await asyncio.wait_for(
            asyncio.gather(
                user_registration.register_user(left, telegram_id),
                user_registration.register_user(right, telegram_id),
            ),
            timeout=10,
        )
        assert first[0].id == second[0].id
        assert {first[1], second[1]} == {True, False}
        assert sorted(await category_names(left, first[0].id)) == sorted(EXPECTED_NAMES)


@pytest.mark.asyncio
async def test_category_insert_failure_rolls_back_user(
    sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    telegram_id = randbits(62) + 1
    monkeypatch.setattr(user_registration, "DEFAULT_CATEGORY_NAMES", ("Інше", "Інше"))
    async with sessions() as session:
        with pytest.raises(IntegrityError):
            await user_registration.register_user(session, telegram_id)
        assert (
            await user_registration.user.get_by_telegram_id(session, telegram_id)
            is None
        )
