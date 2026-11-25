from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Integer, Numeric, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from spend_mind.models.base import Base

if TYPE_CHECKING:
    from spend_mind.models.category import Category
    from spend_mind.models.expense import Expense
    from spend_mind.models.product import Product


class ExpenseItem(Base):
    __tablename__ = "expense_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("amount >= 0", name="amount_nonnegative"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    expense_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("expenses.id", ondelete="CASCADE"), nullable=False
    )
    category_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("categories.id", ondelete="RESTRICT"), nullable=True
    )
    product_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("products.id", ondelete="RESTRICT"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(
        Numeric(12, 3), nullable=False, server_default=text("1")
    )
    unit: Mapped[str | None] = mapped_column(String(30), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    expense: Mapped["Expense"] = relationship(back_populates="items")
    category: Mapped["Category | None"] = relationship(back_populates="expense_items")
    product: Mapped["Product | None"] = relationship(back_populates="expense_items")
