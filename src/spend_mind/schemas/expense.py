import re
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationInfo,
    field_validator,
    model_validator,
)

from spend_mind.models.expense import ExpenseSource, ExpenseStatus
from spend_mind.schemas.currency import SUPPORTED_CURRENCY_CODES

Money = Annotated[Decimal, Field(max_digits=14, decimal_places=2, ge=0)]
PositiveMoney = Annotated[Money, Field(gt=0)]
Quantity = Annotated[Decimal, Field(max_digits=12, decimal_places=3, gt=0)]
Merchant = Annotated[str, Field(max_length=255)]
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


def parse_money(value: object) -> str:
    return validate_decimal_string(value, 2)


def normalize_currency(value: str) -> str:
    code = value.upper()
    if not value.isascii() or code not in SUPPORTED_CURRENCY_CODES:
        raise ValueError("Unsupported ISO 4217 currency code")
    return code


def parse_expense_date(value: object) -> datetime:
    if not isinstance(value, str):
        # Pydantic converts ValueError into an HTTP validation error.
        raise ValueError("Expected an ISO 8601 datetime string")  # noqa: TRY004
    return datetime.fromisoformat(value)


def normalize_expense_date(value: datetime) -> datetime:
    try:
        return value.astimezone(UTC)
    except OverflowError as exc:
        raise ValueError("Expense date is outside the supported UTC range") from exc


MoneyInput = Annotated[Money, BeforeValidator(parse_money, json_schema_input_type=str)]
TotalInput = Annotated[
    PositiveMoney, BeforeValidator(parse_money, json_schema_input_type=str)
]
CurrencyInput = Annotated[str, AfterValidator(normalize_currency)]
ExpenseDateInput = Annotated[
    AwareDatetime,
    BeforeValidator(parse_expense_date),
    AfterValidator(normalize_expense_date),
]


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


ExpenseItemsInput = Annotated[list[ExpenseItemCreate], Field(min_length=1)]


class ExpenseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    merchant: Merchant | None = None
    total_amount: TotalInput
    currency: CurrencyInput
    expense_date: ExpenseDateInput
    source: ExpenseSource
    items: ExpenseItemsInput
    receipt_discount: MoneyInput | None = None


class ExpensePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    merchant: Merchant | None = None
    total_amount: TotalInput | None = None
    currency: CurrencyInput | None = None
    expense_date: ExpenseDateInput | None = None
    items: ExpenseItemsInput | None = None
    receipt_discount: MoneyInput | None = None

    @model_validator(mode="after")
    def validate_changes(self) -> "ExpensePatch":
        if not self.model_fields_set:
            raise ValueError("At least one field is required")
        if any(
            getattr(self, field) is None
            for field in self.model_fields_set - {"merchant"}
        ):
            raise ValueError("Only merchant can be set to null")
        if self.receipt_discount is not None and self.items is None:
            raise ValueError("Receipt discount requires replacement items")
        return self


class ExpenseListQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ExpenseStatus | Literal["all"] = ExpenseStatus.CONFIRMED
    date_from: date | None = None
    date_to: date | None = None
    limit: Annotated[int, Field(ge=1, le=100)] = 20
    cursor: Annotated[str, Field(min_length=1, max_length=128)] | None = None

    @field_validator("date_from", "date_to", mode="before")
    @classmethod
    def validate_calendar_date(cls, value: object) -> object:
        if isinstance(value, str) and not re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value
        ):
            raise ValueError("Expected a date in YYYY-MM-DD format")
        return value

    @model_validator(mode="after")
    def validate_date_range(self) -> "ExpenseListQuery":
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from must be on or before date_to")
        return self


class ExpenseCursor(BaseModel):
    expense_date: AwareDatetime
    id: Annotated[int, Field(strict=True, gt=0, le=2**31 - 1)]


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


class ExpenseTransitionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: ExpenseStatus
    exchange_rate: Decimal | None
    amount_uah: Decimal | None


class ExpenseDetail(ExpenseResponse):
    exchange_rate: Decimal | None
    amount_uah: Decimal | None


class ExpenseListItem(ExpenseTransitionResponse):
    merchant: str | None
    total_amount: Decimal
    currency: str
    expense_date: datetime
    created_at: datetime
    source: ExpenseSource


class ExpensePage(BaseModel):
    items: list[ExpenseListItem]
    next_cursor: str | None
