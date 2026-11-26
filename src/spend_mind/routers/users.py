from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from spend_mind.db import get_session
from spend_mind.routers.dependencies import require_internal_token
from spend_mind.schemas.user import UserRegistrationRequest, UserResponse
from spend_mind.services import user_registration

router = APIRouter(
    prefix="/internal/users", dependencies=[Depends(require_internal_token)]
)


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    responses={200: {"model": UserResponse, "description": "User already registered"}},
)
async def register_user(
    payload: UserRegistrationRequest,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UserResponse:
    user, created = await user_registration.register_user(
        session, payload.telegram_user_id
    )
    if not created:
        response.status_code = status.HTTP_200_OK
    return UserResponse.model_validate(user)
