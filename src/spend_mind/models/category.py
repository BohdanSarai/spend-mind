from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from spend_mind.models.base import Base

if TYPE_CHECKING:
    from spend_mind.models.expense_item import ExpenseItem
    from spend_mind.models.user import User


class Category(Base):
    __tablename__ = "categories"
    __table_args__ = (UniqueConstraint("user_id", "name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)

    user: Mapped["User"] = relationship(back_populates="categories")
    expense_items: Mapped[list["ExpenseItem"]] = relationship(
        back_populates="category", passive_deletes="all"
    )
