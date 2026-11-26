from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from spend_mind.db import get_session
from spend_mind.models.expense import ExpenseStatus
from spend_mind.routers.dependencies import get_telegram_actor
from spend_mind.schemas.expense import (
    ExpenseCreate,
    ExpenseResponse,
    ExpenseTransitionResponse,
)
from spend_mind.services import expense

router = APIRouter(prefix="/internal/expenses")
ExpenseId = Annotated[int, Path(gt=0, le=2**31 - 1)]
Actor = Annotated[int, Depends(get_telegram_actor)]
Session = Annotated[AsyncSession, Depends(get_session)]

TRANSITION_STATUS_CODES = {
    expense.ExpenseTransitionError.USER_NOT_FOUND: status.HTTP_404_NOT_FOUND,
    expense.ExpenseTransitionError.EXPENSE_NOT_FOUND: status.HTTP_404_NOT_FOUND,
    expense.ExpenseTransitionError.STATUS_CONFLICT: status.HTTP_409_CONFLICT,
    expense.ExpenseTransitionError.EXPENSE_CHANGED: status.HTTP_409_CONFLICT,
    expense.ExpenseTransitionError.RATE_UNAVAILABLE: status.HTTP_503_SERVICE_UNAVAILABLE,
    expense.ExpenseTransitionError.CONVERSION_OUT_OF_RANGE: status.HTTP_422_UNPROCESSABLE_CONTENT,
    expense.ExpenseTransitionError.UNSUPPORTED_CURRENCY: status.HTTP_422_UNPROCESSABLE_CONTENT,
}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_expense(
    payload: ExpenseCreate,
    actor: Annotated[int, Depends(get_telegram_actor)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ExpenseResponse:
    try:
        record = await expense.create_expense(session, actor, payload)
    except expense.ExpenseCreationFailed as exc:
        status_code = (
            status.HTTP_422_UNPROCESSABLE_CONTENT
            if exc.reason is expense.ExpenseCreationError.TOTAL_MISMATCH
            else status.HTTP_404_NOT_FOUND
        )
        raise HTTPException(status_code=status_code, detail=exc.reason.value) from exc
    return ExpenseResponse.model_validate(record)


async def transition(
    session: AsyncSession, actor: int, expense_id: int, target: ExpenseStatus
) -> ExpenseTransitionResponse:
    try:
        record = await expense.transition_expense(session, actor, expense_id, target)
    except expense.ExpenseTransitionFailed as exc:
        raise HTTPException(
            status_code=TRANSITION_STATUS_CODES[exc.reason], detail=exc.reason.value
        ) from exc
    return ExpenseTransitionResponse.model_validate(record)


@router.post("/{expense_id}/confirm")
async def confirm_expense(
    expense_id: ExpenseId, actor: Actor, session: Session
) -> ExpenseTransitionResponse:
    return await transition(session, actor, expense_id, ExpenseStatus.CONFIRMED)


@router.post("/{expense_id}/reject")
async def reject_expense(
    expense_id: ExpenseId, actor: Actor, session: Session
) -> ExpenseTransitionResponse:
    return await transition(session, actor, expense_id, ExpenseStatus.REJECTED)
