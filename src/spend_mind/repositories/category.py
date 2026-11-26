from collections.abc import Sequence

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from spend_mind.models import Category


async def list_categories(session: AsyncSession, user_id: int) -> Sequence[Category]:
    return (
        await session.scalars(
            select(Category).where(Category.user_id == user_id).order_by(Category.id)
        )
    ).all()


async def lock_categories(
    session: AsyncSession, user_id: int, ids: Sequence[int]
) -> dict[int, Category]:
    statement = (
        select(Category)
        .where(Category.user_id == user_id, Category.id.in_(ids))
        .order_by(Category.id)
        .with_for_update()
    )
    return {category.id: category for category in await session.scalars(statement)}


async def create_category(session: AsyncSession, user_id: int, name: str) -> Category:
    category = Category(user_id=user_id, name=name)
    session.add(category)
    await session.flush()
    return category


async def rename_category(session: AsyncSession, category: Category, name: str) -> None:
    category.name = name
    await session.flush()


async def delete_category(session: AsyncSession, category: Category) -> None:
    await session.delete(category)
    await session.flush()


async def insert_categories(
    session: AsyncSession, user_id: int, names: Sequence[str]
) -> None:
    await session.execute(
        insert(Category), [{"user_id": user_id, "name": name} for name in names]
    )
