from secrets import compare_digest
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

bearer = HTTPBearer(auto_error=False)


async def require_internal_token(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> None:
    secret = request.app.state.settings.internal_api_token
    token = secret.get_secret_value() if secret is not None else ""
    if (
        token
        and credentials is not None
        and compare_digest(credentials.credentials.encode(), token.encode())
    ):
        return
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid internal API credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
