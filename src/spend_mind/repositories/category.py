from collections.abc import Sequence

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from spend_mind.models import Category


async def insert_categories(
    session: AsyncSession, user_id: int, names: Sequence[str]
) -> None:
    await session.execute(
        insert(Category), [{"user_id": user_id, "name": name} for name in names]
    )
