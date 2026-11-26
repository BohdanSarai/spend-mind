from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints

CategoryName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
]
DeleteAction = Literal["reassign", "uncategorize"]


class CategoryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: CategoryName


class CategoryRename(CategoryCreate):
    pass


class CategoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
