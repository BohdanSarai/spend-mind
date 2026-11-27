from collections.abc import AsyncIterator
from typing import Annotated, Literal

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Path,
    Query,
    Response,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from spend_mind.db import get_session
from spend_mind.models.expense import ExpenseStatus
from spend_mind.routers.dependencies import get_telegram_actor
from spend_mind.schemas.expense import (
    ExpenseCreate,
    ExpenseDetail,
    ExpenseListQuery,
    ExpensePage,
    ExpensePatch,
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
    expense.ExpenseTransitionError.STALE_ETAG: status.HTTP_412_PRECONDITION_FAILED,
    expense.ExpenseTransitionError.INVALID_CURSOR: status.HTTP_422_UNPROCESSABLE_CONTENT,
}

CREATION_STATUS_CODES = {
    expense.ExpenseCreationError.USER_NOT_FOUND: status.HTTP_404_NOT_FOUND,
    expense.ExpenseCreationError.CATEGORY_NOT_FOUND: status.HTTP_404_NOT_FOUND,
    expense.ExpenseCreationError.TOTAL_MISMATCH: status.HTTP_422_UNPROCESSABLE_CONTENT,
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
        raise HTTPException(
            status_code=CREATION_STATUS_CODES[exc.reason], detail=exc.reason.value
        ) from exc
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


async def expense_crud_errors() -> AsyncIterator[None]:
    try:
        yield
    except expense.ExpenseTransitionFailed as exc:
        raise HTTPException(
            status_code=TRANSITION_STATUS_CODES[exc.reason], detail=exc.reason.value
        ) from exc
    except expense.ExpenseCreationFailed as exc:
        raise HTTPException(
            status_code=CREATION_STATUS_CODES[exc.reason], detail=exc.reason.value
        ) from exc


CRUD_ERRORS = [Depends(expense_crud_errors, scope="function")]


def require_if_match(if_match: str | None) -> str:
    if if_match is None:
        raise HTTPException(status_code=428, detail="expense_if_match_required")
    return if_match


@router.get("", dependencies=CRUD_ERRORS)
async def list_expenses(
    query: Annotated[ExpenseListQuery, Query()], actor: Actor, session: Session
) -> ExpensePage:
    return await expense.list_expenses(session, actor, query)


@router.get("/{expense_id}", dependencies=CRUD_ERRORS)
async def get_expense(
    expense_id: ExpenseId, actor: Actor, session: Session, response: Response
) -> ExpenseDetail:
    detail = await expense.get_expense_detail(session, actor, expense_id)
    response.headers["ETag"] = expense.expense_etag(detail)
    return detail


@router.patch("/{expense_id}", dependencies=CRUD_ERRORS)
async def patch_expense(
    expense_id: ExpenseId,
    payload: ExpensePatch,
    actor: Actor,
    session: Session,
    response: Response,
    if_match: Annotated[str | None, Header()] = None,
) -> ExpenseDetail:
    detail = await expense.update_expense(
        session, actor, expense_id, payload, require_if_match(if_match)
    )
    response.headers["ETag"] = expense.expense_etag(detail)
    return detail


@router.delete(
    "/{expense_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=CRUD_ERRORS
)
async def delete_expense(
    expense_id: ExpenseId,
    actor: Actor,
    session: Session,
    confirm: Literal["true"] | None = None,
    if_match: Annotated[str | None, Header()] = None,
) -> None:
    if confirm is None:
        raise HTTPException(
            status_code=422, detail="expense_delete_confirmation_required"
        )
    await expense.delete_expense(session, actor, expense_id, require_if_match(if_match))
