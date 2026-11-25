from sqlalchemy import BigInteger, DateTime, Integer, UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from spend_mind.models import Base, User


def test_user_table_is_registered_in_base_metadata() -> None:
    assert Base.metadata.tables["users"] is User.__table__


def test_user_identifier_columns_and_constraints() -> None:
    table = User.__table__

    assert type(table.c.id.type) is Integer
    assert table.primary_key.name == "pk_users"
    assert list(table.primary_key.columns.keys()) == ["id"]

    assert isinstance(table.c.telegram_user_id.type, BigInteger)
    assert not table.c.telegram_user_id.nullable
    unique_constraints = {
        constraint.name: list(constraint.columns.keys())
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert unique_constraints["uq_users_telegram_user_id"] == ["telegram_user_id"]


def test_user_created_at_has_a_timezone_aware_server_default() -> None:
    table = User.__table__
    created_at = table.c.created_at

    assert isinstance(created_at.type, DateTime)
    assert created_at.type.timezone
    assert not created_at.nullable
    assert created_at.server_default is not None

    ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
    assert "created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL" in ddl
