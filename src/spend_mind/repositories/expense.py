from sqlalchemy.ext.asyncio import AsyncSession

from spend_mind.models import Expense


async def insert_expense(session: AsyncSession, expense: Expense) -> None:
    session.add(expense)
    await session.flush()
