from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from spend_mind.models.base import Base

if TYPE_CHECKING:
    from spend_mind.models.expense_item import ExpenseItem
    from spend_mind.models.user import User


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    __table_args__ = (
        Index(
            "uq_categories_user_id_lower_name", user_id, func.lower(name), unique=True
        ),
    )

    user: Mapped["User"] = relationship(back_populates="categories")
    expense_items: Mapped[list["ExpenseItem"]] = relationship(
        back_populates="category", passive_deletes="all"
    )
