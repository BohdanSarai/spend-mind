from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from spend_mind.models.base import Base

if TYPE_CHECKING:
    from spend_mind.models.user import User


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint(
            "CASE WHEN tool_calls IS NULL THEN TRUE "
            "WHEN role = 'assistant' AND jsonb_typeof(tool_calls) = 'array' "
            "THEN jsonb_array_length(tool_calls) > 0 ELSE FALSE END",
            name="tool_calls_allowed",
        ),
        CheckConstraint(
            "CASE WHEN role = 'tool' THEN "
            "tool_call_id IS NOT NULL AND tool_call_id ~ '[^[:space:]]' "
            "ELSE tool_call_id IS NULL END",
            name="tool_call_id_required_for_tool",
        ),
        CheckConstraint(
            "content IS NOT NULL OR (role = 'assistant' AND tool_calls IS NOT NULL)",
            name="content_required",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False
    )
    role: Mapped[MessageRole] = mapped_column(
        Enum(
            MessageRole,
            name="role_allowed",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            values_callable=lambda members: [member.value for member in members],
        ),
        nullable=False,
    )
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    tool_calls: Mapped[list[Any] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    tool_call_id: Mapped[str | None] = mapped_column(String, nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="messages")
