from datetime import date
from decimal import Decimal

from sqlalchemy import (
    Date,
    DateTime,
    String,
    and_,
    cast,
    func,
    literal,
    select,
    union_all,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ColumnElement, Select
from sqlalchemy.sql.selectable import CTE

from spend_mind.models import Category, Expense, ExpenseItem, Product
from spend_mind.models.expense import ExpenseStatus
from spend_mind.schemas.expense_analytics import ExpenseAnalyticsQuery, ExpenseGrouping


def kyiv_midnight(day: date, *, next_day: bool = False) -> ColumnElement:
    calendar_day = cast(day, Date)
    if next_day:
        calendar_day = calendar_day + 1
    return func.timezone("Europe/Kyiv", cast(calendar_day, DateTime))


def confirmed_expenses(user_id: int, query: ExpenseAnalyticsQuery) -> CTE:
    statement = select(
        Expense.id.label("expense_id"),
        Expense.user_id,
        Expense.expense_date,
        Expense.merchant,
        Expense.exchange_rate,
        Expense.amount_uah.label("contribution"),
    ).where(Expense.user_id == user_id, Expense.status == ExpenseStatus.CONFIRMED)
    if query.date_from is not None:
        statement = statement.where(
            Expense.expense_date >= kyiv_midnight(query.date_from)
        )
    if query.date_to is not None:
        statement = statement.where(
            Expense.expense_date < kyiv_midnight(query.date_to, next_day=True)
        )
    if query.merchant is not None:
        statement = statement.where(
            Expense.merchant.icontains(query.merchant, autoescape=True)
        )
    return statement.cte("confirmed_expenses")


def matching_items(expenses: CTE, query: ExpenseAnalyticsQuery) -> CTE:
    statement = (
        select(
            expenses.c.expense_id,
            expenses.c.merchant,
            expenses.c.expense_date,
            Category.id.label("category_id"),
            Category.name.label("category_name"),
            ExpenseItem.product_id,
            ExpenseItem.name.label("item_name"),
            (ExpenseItem.amount * expenses.c.exchange_rate).label("unrounded_uah"),
        )
        .select_from(expenses)
        .join(ExpenseItem, ExpenseItem.expense_id == expenses.c.expense_id)
        .outerjoin(
            Category,
            and_(
                Category.id == ExpenseItem.category_id,
                Category.user_id == expenses.c.user_id,
            ),
        )
    )
    if query.category_id is not None:
        statement = statement.where(Category.id == query.category_id)
    if query.uncategorized:
        statement = statement.where(Category.id.is_(None))
    if query.product_name is not None:
        statement = statement.where(
            ExpenseItem.name.icontains(query.product_name, autoescape=True)
        )
    return statement.cte("matching_items")


def expense_group_key(
    rows: CTE, grouping: ExpenseGrouping
) -> tuple[ColumnElement, ColumnElement]:
    if grouping == ExpenseGrouping.MERCHANT:
        name = func.nullif(func.btrim(rows.c.merchant), "")
        return func.lower(name), func.coalesce(name, "Невідомий магазин")
    local_time = func.timezone("Europe/Kyiv", rows.c.expense_date)
    pattern = "YYYY-MM-DD" if grouping == ExpenseGrouping.DAY else "YYYY-MM"
    period = func.to_char(local_time, pattern)
    return period, period


def grouped_item_contributions(items: CTE, grouping: ExpenseGrouping) -> CTE:
    if grouping == ExpenseGrouping.CATEGORY:
        key = cast(items.c.category_id, String)
        label = func.coalesce(items.c.category_name, "Без категорії")
    elif grouping == ExpenseGrouping.PRODUCT:
        key = func.coalesce(
            literal("product:") + cast(Product.id, String),
            literal("name:") + func.lower(func.btrim(items.c.item_name)),
        )
        label = func.coalesce(Product.name, items.c.item_name)
    else:
        key, label = expense_group_key(items, grouping)

    statement = select(
        key.label("key"),
        func.min(label.collate("C")).label("label"),
        func.round(func.sum(items.c.unrounded_uah), 2).label("contribution"),
    ).select_from(items)
    if grouping == ExpenseGrouping.PRODUCT:
        statement = statement.outerjoin(Product, Product.id == items.c.product_id)
    return statement.group_by(items.c.expense_id, key).cte("grouped_item_contributions")


def metrics(contributions: CTE) -> tuple[ColumnElement, ColumnElement, ColumnElement]:
    amount = contributions.c.contribution
    return (
        func.coalesce(func.round(func.sum(amount), 2), Decimal("0.00")).label(
            "total_amount_uah"
        ),
        func.count().label("transaction_count"),
        func.round(func.avg(amount), 2).label("average_transaction_amount_uah"),
    )


def analytics_statement(user_id: int, query: ExpenseAnalyticsQuery) -> Select:
    expenses = confirmed_expenses(user_id, query)
    grouping = query.group_by
    item_scope = (
        query.category_id is not None
        or query.uncategorized
        or query.product_name is not None
        or grouping in (ExpenseGrouping.CATEGORY, ExpenseGrouping.PRODUCT)
    )
    items = matching_items(expenses, query) if item_scope else None
    contributions = expenses
    if items is not None:
        # Count each expense once in the summary, even when several items match.
        contributions = (
            select(func.round(func.sum(items.c.unrounded_uah), 2).label("contribution"))
            .group_by(items.c.expense_id)
            .cte("item_contributions_by_expense")
        )
    summary = select(
        literal(0).label("scope"),
        literal(None, String).label("key"),
        literal(None, String).label("label"),
        *metrics(contributions),
    ).select_from(contributions)
    if grouping is None:
        return summary

    if items is not None:
        grouped = grouped_item_contributions(items, grouping)
    else:
        key, label = expense_group_key(expenses, grouping)
        grouped = select(
            key.label("key"), label.label("label"), expenses.c.contribution
        ).cte("grouped_expense_contributions")
    groups = select(
        literal(1).label("scope"),
        grouped.c.key,
        func.min(grouped.c.label.collate("C")).label("label"),
        *metrics(grouped),
    ).group_by(grouped.c.key)
    report = union_all(summary, groups).subquery()
    return select(report).order_by(
        report.c.scope, report.c.key.collate("C").nulls_last()
    )


async def owned_category_exists(
    session: AsyncSession, user_id: int, category_id: int
) -> bool:
    return (
        await session.scalar(
            select(Category.id).where(
                Category.id == category_id, Category.user_id == user_id
            )
        )
    ) is not None
