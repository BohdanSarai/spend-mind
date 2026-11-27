from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, cast, delete, func, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

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
        .options(joinedload(Expense.items))
        .execution_options(populate_existing=True)
    )
    row = (await session.execute(statement)).unique().one_or_none()
    return (row[0], row[1]) if row is not None else None


async def lock_owned_expense(
    session: AsyncSession, expense_id: int, user_id: int
) -> bool:
    statement = (
        select(Expense.id)
        .where(Expense.id == expense_id, Expense.user_id == user_id)
        .with_for_update()
    )
    return await session.scalar(statement) is not None


async def list_owned_expenses(
    session: AsyncSession,
    user_id: int,
    *,
    status: ExpenseStatus | None,
    date_from: date | None,
    date_to: date | None,
    limit: int,
    before: tuple[datetime, int] | None,
) -> list[Expense]:
    statement = select(Expense).where(Expense.user_id == user_id)
    if status is not None:
        statement = statement.where(Expense.status == status)
    if date_from is not None:
        start = func.timezone("Europe/Kyiv", cast(cast(date_from, Date), DateTime))
        statement = statement.where(Expense.expense_date >= start)
    if date_to is not None:
        end = func.timezone("Europe/Kyiv", cast(cast(date_to, Date) + 1, DateTime))
        statement = statement.where(Expense.expense_date < end)
    if before is not None:
        statement = statement.where(
            tuple_(Expense.expense_date, Expense.id) < tuple_(*before)
        )
    rows = await session.scalars(
        statement.order_by(Expense.expense_date.desc(), Expense.id.desc()).limit(limit)
    )
    return list(rows)


async def delete_owned_expense(
    session: AsyncSession, expense_id: int, user_id: int
) -> None:
    await session.execute(
        delete(Expense).where(Expense.id == expense_id, Expense.user_id == user_id)
    )


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
