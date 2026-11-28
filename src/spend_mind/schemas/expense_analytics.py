import re
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)


class ExpenseGrouping(StrEnum):
    CATEGORY = "category"
    PRODUCT = "product"
    MERCHANT = "merchant"
    DAY = "day"
    MONTH = "month"


def validate_calendar_date(value: object) -> object:
    if type(value) is date:
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        return value
    raise ValueError("Expected a date in YYYY-MM-DD format")


CalendarDate = Annotated[date, BeforeValidator(validate_calendar_date)]
SearchText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
]
CategoryId = Annotated[int, Field(strict=True, gt=0, le=2**31 - 1)]


class ExpenseAnalyticsQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date_from: CalendarDate | None = None
    date_to: CalendarDate | None = None
    category_id: CategoryId | None = None
    uncategorized: bool = Field(
        default=False,
        strict=True,
        description="Include only positions without a category owned by the caller.",
    )
    product_name: SearchText | None = None
    merchant: SearchText | None = None
    group_by: ExpenseGrouping | None = None

    @model_validator(mode="after")
    def validate_filters(self) -> "ExpenseAnalyticsQuery":
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from must be on or before date_to")
        if self.uncategorized and self.category_id is not None:
            raise ValueError("category_id and uncategorized are mutually exclusive")
        return self


class ExpenseMetrics(BaseModel):
    total_amount_uah: Decimal
    transaction_count: int
    average_transaction_amount_uah: Decimal | None


class ExpenseAnalyticsGroup(ExpenseMetrics):
    key: str | None
    label: str


class ExpenseAnalyticsResponse(BaseModel):
    currency: Literal["UAH"] = "UAH"
    summary: ExpenseMetrics
    groups: list[ExpenseAnalyticsGroup]
