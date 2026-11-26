from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from spend_mind.db import get_session
from spend_mind.routers.dependencies import get_telegram_actor
from spend_mind.schemas.expense import ExpenseCreate, ExpenseResponse
from spend_mind.services import expense

router = APIRouter(prefix="/internal/expenses")


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
