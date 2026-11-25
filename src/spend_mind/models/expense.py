from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from spend_mind.models.base import Base

if TYPE_CHECKING:
    from spend_mind.models.user import User


class ExpenseSource(StrEnum):
    MANUAL = "manual"
    TEXT = "text"
    IMAGE = "image"


class ExpenseStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class Expense(Base):
    __tablename__ = "expenses"
    __table_args__ = (
        CheckConstraint("total_amount > 0", name="total_amount_positive"),
        CheckConstraint(
            "exchange_rate IS NULL OR exchange_rate > 0",
            name="exchange_rate_positive",
        ),
        CheckConstraint(
            "amount_uah IS NULL OR amount_uah >= 0", name="amount_uah_nonnegative"
        ),
        CheckConstraint(
            "status <> 'confirmed' OR "
            "(exchange_rate IS NOT NULL AND amount_uah IS NOT NULL)",
            name="confirmed_requires_conversion",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False
    )
    merchant: Mapped[str | None] = mapped_column(String(255), nullable=True)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    exchange_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    amount_uah: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    expense_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    source: Mapped[ExpenseSource] = mapped_column(
        Enum(
            ExpenseSource,
            name="source_allowed",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            values_callable=lambda members: [member.value for member in members],
        ),
        nullable=False,
    )
    status: Mapped[ExpenseStatus] = mapped_column(
        Enum(
            ExpenseStatus,
            name="status_allowed",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            values_callable=lambda members: [member.value for member in members],
        ),
        nullable=False,
        server_default=ExpenseStatus.PENDING.value,
    )

    user: Mapped["User"] = relationship(back_populates="expenses")
