import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationInfo,
    field_validator,
)

from spend_mind.models.expense import ExpenseSource, ExpenseStatus
from spend_mind.schemas.currency import SUPPORTED_CURRENCY_CODES

Money = Annotated[Decimal, Field(max_digits=14, decimal_places=2, ge=0)]
PositiveMoney = Annotated[Money, Field(gt=0)]
Quantity = Annotated[Decimal, Field(max_digits=12, decimal_places=3, gt=0)]
ItemName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
]


def validate_decimal_string(value: object, places: int) -> str:
    if not isinstance(value, str) or not re.fullmatch(
        rf"-?[0-9]+(?:\.[0-9]{{1,{places}}})?", value
    ):
        raise ValueError(
            f"Expected a decimal string with up to {places} decimal places"
        )
    return value


class ExpenseItemCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: ItemName
    quantity: Quantity = Decimal(1)
    unit: Annotated[str, Field(max_length=30)] | None = None
    amount: Money
    category_id: Annotated[int, Field(strict=True, gt=0, le=2**31 - 1)] | None = None

    @field_validator("quantity", "amount", mode="before", json_schema_input_type=str)
    @classmethod
    def validate_amount_or_quantity(cls, value: object, info: ValidationInfo) -> str:
        return validate_decimal_string(value, 3 if info.field_name == "quantity" else 2)


class ExpenseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    merchant: Annotated[str, Field(max_length=255)] | None = None
    total_amount: PositiveMoney
    currency: str
    expense_date: AwareDatetime
    source: ExpenseSource
    items: Annotated[list[ExpenseItemCreate], Field(min_length=1)]
    receipt_discount: Money | None = None

    @field_validator("total_amount", mode="before", json_schema_input_type=str)
    @classmethod
    def validate_total_amount(cls, value: object) -> str:
        return validate_decimal_string(value, 2)

    @field_validator(
        "receipt_discount", mode="before", json_schema_input_type=str | None
    )
    @classmethod
    def validate_receipt_discount(cls, value: object) -> str | None:
        return None if value is None else validate_decimal_string(value, 2)

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        code = value.upper()
        if not value.isascii() or code not in SUPPORTED_CURRENCY_CODES:
            raise ValueError("Unsupported ISO 4217 currency code")
        return code

    @field_validator("expense_date", mode="before")
    @classmethod
    def parse_expense_date(cls, value: object) -> datetime:
        if not isinstance(value, str):
            # Pydantic converts ValueError into an HTTP validation error.
            raise ValueError("Expected an ISO 8601 datetime string")  # noqa: TRY004
        return datetime.fromisoformat(value)

    @field_validator("expense_date")
    @classmethod
    def normalize_expense_date(cls, value: datetime) -> datetime:
        try:
            return value.astimezone(UTC)
        except OverflowError as exc:
            raise ValueError("Expense date is outside the supported UTC range") from exc


class ExpenseItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    quantity: Decimal
    unit: str | None
    amount: Decimal
    category_id: int | None


class ExpenseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    merchant: str | None
    total_amount: Decimal
    currency: str
    expense_date: datetime
    created_at: datetime
    source: ExpenseSource
    status: ExpenseStatus
    items: list[ExpenseItemResponse]
