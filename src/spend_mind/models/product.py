from typing import TYPE_CHECKING

from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from spend_mind.models.base import Base

if TYPE_CHECKING:
    from spend_mind.models.product_type import ProductType


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    product_type_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("product_types.id"), nullable=False
    )
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536), nullable=True)

    product_type: Mapped["ProductType"] = relationship(back_populates="products")
