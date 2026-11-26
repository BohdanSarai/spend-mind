from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from spend_mind.models import Category
from spend_mind.repositories import category, expense_item, user


class CategoryNotFound(Exception):
    pass


class CategoryNameConflict(Exception):
    pass


class CategoryInUse(Exception):
    pass


def violates(exc: IntegrityError, sqlstate: str, constraint: str) -> bool:
    # The asyncpg dialect wraps the native exception, which carries the constraint name.
    original = exc.orig
    return (
        getattr(original, "sqlstate", None) == sqlstate
        and getattr(original.__cause__, "constraint_name", None) == constraint
    )


@asynccontextmanager
async def category_transaction(
    session: AsyncSession, telegram_user_id: int
) -> AsyncIterator[int]:
    try:
        async with session.begin():
            owner = await user.get_by_telegram_id(session, telegram_user_id)
            if owner is None:
                raise CategoryNotFound
            yield owner.id
    except IntegrityError as exc:
        if violates(exc, "23505", "uq_categories_user_id_lower_name"):
            raise CategoryNameConflict from exc
        if violates(exc, "23503", "fk_expense_items_category_id_categories"):
            raise CategoryInUse from exc
        raise


async def list_categories(
    session: AsyncSession, telegram_user_id: int
) -> Sequence[Category]:
    async with category_transaction(session, telegram_user_id) as user_id:
        return await category.list_categories(session, user_id)


async def create_category(
    session: AsyncSession, telegram_user_id: int, name: str
) -> Category:
    async with category_transaction(session, telegram_user_id) as user_id:
        return await category.create_category(session, user_id, name)


async def rename_category(
    session: AsyncSession, telegram_user_id: int, category_id: int, name: str
) -> Category:
    async with category_transaction(session, telegram_user_id) as user_id:
        locked = await category.lock_categories(session, user_id, [category_id])
        current = locked.get(category_id)
        if current is None:
            raise CategoryNotFound
        await category.rename_category(session, current, name)
        return current


async def delete_category(
    session: AsyncSession,
    telegram_user_id: int,
    category_id: int,
    *,
    update_items: bool,
    target_id: int | None,
) -> None:
    async with category_transaction(session, telegram_user_id) as user_id:
        ids = [category_id] if target_id is None else [category_id, target_id]
        locked = await category.lock_categories(session, user_id, ids)
        if any(item_id not in locked for item_id in ids):
            raise CategoryNotFound

        if update_items:
            await expense_item.move_category_items(
                session, user_id, category_id, target_id
            )
        elif await expense_item.category_is_in_use(session, category_id):
            raise CategoryInUse

        await category.delete_category(session, locked[category_id])
