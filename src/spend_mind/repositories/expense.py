from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, cast, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from spend_mind.models import Expense
from spend_mind.models.expense import ExpenseStatus


async def insert_expense(session: AsyncSession, expense: Expense) -> None:
    session.add(expense)
    await session.flush()


async def find_owned_expense(
    session: AsyncSession, expense_id: int, user_id: int
) -> tuple[Expense, date] | None:
    kyiv_date = cast(func.timezone("Europe/Kyiv", Expense.expense_date), Date)
    statement = (
        select(Expense, kyiv_date)
        .where(Expense.id == expense_id, Expense.user_id == user_id)
        .execution_options(populate_existing=True)
    )
    row = (await session.execute(statement)).one_or_none()
    return (row[0], row[1]) if row is not None else None


async def transition_pending_expense(
    session: AsyncSession,
    expense_id: int,
    user_id: int,
    target: ExpenseStatus,
    *,
    expected: tuple[str, Decimal, datetime] | None = None,
    rate: Decimal | None = None,
    amount_uah: Decimal | None = None,
) -> Expense | None:
    statement = update(Expense).where(
        Expense.id == expense_id,
        Expense.user_id == user_id,
        Expense.status == ExpenseStatus.PENDING,
    )
    if expected is not None:
        currency, total_amount, expense_date = expected
        statement = statement.where(
            Expense.currency == currency,
            Expense.total_amount == total_amount,
            Expense.expense_date == expense_date,
        )
    return await session.scalar(
        statement.values(status=target, exchange_rate=rate, amount_uah=amount_uah)
        .returning(Expense)
        .execution_options(populate_existing=True, synchronize_session=False)
    )
