import pytest
from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Enum, Integer, String, Text
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.schema import CreateTable

from spend_mind.models import Base, Message, User
from spend_mind.models.message import MessageRole


def test_message_table_is_registered_in_base_metadata() -> None:
    assert Base.metadata.tables["messages"] is Message.__table__


def test_message_columns_and_keys() -> None:
    table = Message.__table__
    columns = table.c
    assert {column.name for column in columns if column.nullable} == {
        "content",
        "tool_calls",
        "tool_call_id",
        "embedding",
    }
    assert type(columns.id.type) is Integer
    assert table.primary_key.name == "pk_messages"
    assert list(table.primary_key.columns.keys()) == ["id"]
    assert type(columns.user_id.type) is Integer
    (foreign_key,) = columns.user_id.foreign_keys
    assert foreign_key.column is User.__table__.c.id
    assert foreign_key.constraint.name == "fk_messages_user_id_users"

    assert isinstance(columns.content.type, Text)
    assert isinstance(columns.tool_calls.type, JSONB)
    assert columns.tool_calls.type.none_as_null
    assert type(columns.tool_call_id.type) is String
    assert columns.tool_call_id.type.length is None
    assert isinstance(columns.embedding.type, Vector)
    assert columns.embedding.type.dim == 1536
    assert isinstance(columns.created_at.type, DateTime)
    assert columns.created_at.type.timezone


def test_message_role_values_round_trip() -> None:
    role_type = Message.__table__.c.role.type
    assert isinstance(role_type, Enum)
    assert role_type.enum_class is MessageRole
    assert role_type.enums == ["user", "assistant", "tool"]
    assert not role_type.native_enum

    dialect = postgresql.dialect()
    bind = role_type.bind_processor(dialect)
    read = role_type.result_processor(dialect, None)
    assert bind is not None
    assert read is not None
    for member in MessageRole:
        assert bind(member) == member.value
        assert read(member.value) is member
    with pytest.raises(LookupError):
        bind("invalid")


def test_message_postgresql_ddl_contains_checks_and_default() -> None:
    ddl = str(CreateTable(Message.__table__).compile(dialect=postgresql.dialect()))

    assert "created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL" in ddl
    checks = {
        "role_allowed": "role IN ('user', 'assistant', 'tool')",
        "tool_calls_allowed": (
            "CASE WHEN tool_calls IS NULL THEN TRUE "
            "WHEN role = 'assistant' AND jsonb_typeof(tool_calls) = 'array' "
            "THEN jsonb_array_length(tool_calls) > 0 ELSE FALSE END"
        ),
        "tool_call_id_required_for_tool": (
            "CASE WHEN role = 'tool' THEN "
            "tool_call_id IS NOT NULL AND tool_call_id ~ '[^[:space:]]' "
            "ELSE tool_call_id IS NULL END"
        ),
        "content_required": (
            "content IS NOT NULL OR (role = 'assistant' AND tool_calls IS NOT NULL)"
        ),
    }
    for name, condition in checks.items():
        assert f"CONSTRAINT ck_messages_{name} CHECK ({condition})" in ddl


def test_message_relationship_synchronizes_in_both_directions() -> None:
    user = User(telegram_user_id=123)
    appended_message = Message()
    assigned_message = Message()

    user.messages.append(appended_message)
    assert appended_message.user is user

    assigned_message.user = user
    assert user.messages == [appended_message, assigned_message]
