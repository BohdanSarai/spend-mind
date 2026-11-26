from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class UserRegistrationRequest(BaseModel):
    telegram_user_id: int = Field(strict=True, gt=0, le=2**63 - 1)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    telegram_user_id: int
    created_at: datetime
