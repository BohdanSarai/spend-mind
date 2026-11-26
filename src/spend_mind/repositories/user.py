from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from spend_mind.models import User


async def get_by_telegram_id(
    session: AsyncSession, telegram_user_id: int
) -> User | None:
    return await session.scalar(
        select(User).where(User.telegram_user_id == telegram_user_id)
    )


async def insert_if_absent(session: AsyncSession, telegram_user_id: int) -> User | None:
    statement = (
        insert(User)
        .values(telegram_user_id=telegram_user_id)
        .on_conflict_do_nothing(index_elements=[User.telegram_user_id])
        .returning(User)
    )
    return await session.scalar(statement)
