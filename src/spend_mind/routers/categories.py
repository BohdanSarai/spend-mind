from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from spend_mind.db import get_session
from spend_mind.routers.dependencies import get_telegram_actor
from spend_mind.schemas.category import (
    CategoryCreate,
    CategoryRename,
    CategoryResponse,
    DeleteAction,
)
from spend_mind.services import category

Actor = Annotated[int, Depends(get_telegram_actor)]
Session = Annotated[AsyncSession, Depends(get_session)]
CategoryId = Annotated[int, Path(gt=0, le=2**31 - 1)]


async def category_errors() -> AsyncIterator[None]:
    try:
        yield
    except category.CategoryNotFound as exc:
        raise HTTPException(
            status_code=404, detail="category_or_user_not_found"
        ) from exc
    except category.CategoryNameConflict as exc:
        raise HTTPException(status_code=409, detail="category_name_conflict") from exc
    except category.CategoryInUse as exc:
        raise HTTPException(status_code=409, detail="category_in_use") from exc


router = APIRouter(
    prefix="/internal/categories",
    dependencies=[Depends(category_errors, scope="function")],
)


@router.get("")
async def list_categories(actor: Actor, session: Session) -> list[CategoryResponse]:
    categories = await category.list_categories(session, actor)
    return [CategoryResponse.model_validate(item) for item in categories]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_category(
    payload: CategoryCreate, actor: Actor, session: Session
) -> CategoryResponse:
    return CategoryResponse.model_validate(
        await category.create_category(session, actor, payload.name)
    )


@router.patch("/{category_id}")
async def rename_category(
    category_id: CategoryId, payload: CategoryRename, actor: Actor, session: Session
) -> CategoryResponse:
    return CategoryResponse.model_validate(
        await category.rename_category(session, actor, category_id, payload.name)
    )


@router.delete("/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_category(
    category_id: CategoryId,
    actor: Actor,
    session: Session,
    action: DeleteAction | None = None,
    target_category_id: Annotated[int | None, Query(gt=0, le=2**31 - 1)] = None,
) -> None:
    if (action == "reassign") != (target_category_id is not None) or (
        target_category_id == category_id
    ):
        raise HTTPException(status_code=422, detail="invalid_category_delete_options")
    await category.delete_category(
        session,
        actor,
        category_id,
        update_items=action is not None,
        target_id=target_category_id,
    )
