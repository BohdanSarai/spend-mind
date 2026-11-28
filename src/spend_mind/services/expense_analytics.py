from sqlalchemy.ext.asyncio import AsyncSession

from spend_mind.repositories import expense_analytics as repository
from spend_mind.schemas.expense_analytics import (
    ExpenseAnalyticsGroup,
    ExpenseAnalyticsQuery,
    ExpenseAnalyticsResponse,
    ExpenseMetrics,
)
from spend_mind.services.expense import get_expense_owner_id


class AnalyticsCategoryNotFound(Exception):
    pass


async def analyze_expenses(
    session: AsyncSession, *, user_id: int, query: ExpenseAnalyticsQuery
) -> ExpenseAnalyticsResponse:
    """Analyze confirmed expenses for an owner resolved by the caller."""
    if query.category_id is not None and not await repository.owned_category_exists(
        session, user_id, query.category_id
    ):
        raise AnalyticsCategoryNotFound

    result = await session.execute(repository.analytics_statement(user_id, query))
    summary, *groups = result.mappings().all()
    return ExpenseAnalyticsResponse(
        summary=ExpenseMetrics.model_validate(summary),
        groups=[ExpenseAnalyticsGroup.model_validate(row) for row in groups],
    )


async def analyze_expenses_for_actor(
    session: AsyncSession, telegram_user_id: int, query: ExpenseAnalyticsQuery
) -> ExpenseAnalyticsResponse:
    async with session.begin():
        user_id = await get_expense_owner_id(session, telegram_user_id)
        return await analyze_expenses(session, user_id=user_id, query=query)
