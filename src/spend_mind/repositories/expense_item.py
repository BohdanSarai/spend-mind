from sqlalchemy import exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from spend_mind.models import Expense, ExpenseItem


async def category_is_in_use(session: AsyncSession, category_id: int) -> bool:
    statement = select(exists().where(ExpenseItem.category_id == category_id))
    return bool(await session.scalar(statement))


async def move_category_items(
    session: AsyncSession, user_id: int, category_id: int, target_id: int | None
) -> None:
    statement = (
        update(ExpenseItem)
        .where(
            ExpenseItem.category_id == category_id,
            ExpenseItem.expense_id.in_(
                select(Expense.id).where(Expense.user_id == user_id)
            ),
        )
        .values(category_id=target_id)
    )
    await session.execute(statement)
