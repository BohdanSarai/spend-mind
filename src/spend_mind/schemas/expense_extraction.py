from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator

from spend_mind.schemas.expense import ExpenseResponse


class ClarificationReason(StrEnum):
    MULTIPLE_TRANSACTIONS = "multiple_transactions"
    MISSING_ITEMS = "missing_items"
    MISSING_AMOUNT = "missing_amount"
    AMBIGUOUS_FINANCIAL_DATA = "ambiguous_financial_data"
    AMBIGUOUS_CURRENCY = "ambiguous_currency"
    AMBIGUOUS_DATE = "ambiguous_date"
    INVALID_FINANCIAL_DATA = "invalid_financial_data"
    TOTAL_MISMATCH = "expense_total_mismatch"


class ExtractedExpenseItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str | None
    quantity: str | None
    unit: str | None
    amount: str | None


class ExtractedExpense(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    language: Literal["uk", "en"]
    clarification_reason: ClarificationReason | None
    merchant: str | None
    total_amount: str | None
    currency: str | None
    calendar_date: str | None
    relative_days: int | None
    local_time: str | None
    items: list[ExtractedExpenseItem]


class ExpenseRecognitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: Annotated[str, StringConstraints(min_length=1, max_length=4000)]

    @field_validator("text")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Text must not be blank")
        return value


class ExpenseRecognized(BaseModel):
    outcome: Literal["created"] = "created"
    expense: ExpenseResponse


class ExpenseClarification(BaseModel):
    outcome: Literal["clarification_required"] = "clarification_required"
    reason: ClarificationReason
    question: str
