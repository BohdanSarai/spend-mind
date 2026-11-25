from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from spend_mind.models.base import Base


class ProductType(Base):
    __tablename__ = "product_types"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
