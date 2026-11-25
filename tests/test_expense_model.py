import pytest
from sqlalchemy import DateTime, Enum, Integer, Numeric, String
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from spend_mind.models import Base, Expense, User
from spend_mind.models.expense import ExpenseSource, ExpenseStatus


def test_expense_table_is_registered_in_base_metadata() -> None:
    assert Base.metadata.tables["expenses"] is Expense.__table__


def test_expense_columns_and_keys() -> None:
    table = Expense.__table__
    columns = table.c

    assert {column.name for column in columns if column.nullable} == {
        "merchant",
        "exchange_rate",
        "amount_uah",
    }
    assert type(columns.id.type) is Integer
    assert table.primary_key.name == "pk_expenses"
    assert list(table.primary_key.columns.keys()) == ["id"]
    assert type(columns.user_id.type) is Integer
    (foreign_key,) = columns.user_id.foreign_keys
    assert foreign_key.column is User.__table__.c.id
    assert foreign_key.constraint.name == "fk_expenses_user_id_users"

    for column, length in [(columns.merchant, 255), (columns.currency, 3)]:
        assert isinstance(column.type, String)
        assert column.type.length == length

    for column, precision, scale in [
        (columns.total_amount, 14, 2),
        (columns.exchange_rate, 18, 8),
        (columns.amount_uah, 14, 2),
    ]:
        assert isinstance(column.type, Numeric)
        assert (column.type.precision, column.type.scale) == (precision, scale)
        assert column.type.asdecimal

    for column in (columns.expense_date, columns.created_at):
        assert isinstance(column.type, DateTime)
        assert column.type.timezone


@pytest.mark.parametrize(
    ("column_name", "enum_class", "values"),
    [
        ("source", ExpenseSource, ["manual", "text", "image"]),
        ("status", ExpenseStatus, ["pending", "confirmed", "rejected"]),
    ],
)
def test_expense_enum_values_round_trip(
    column_name: str,
    enum_class: type[ExpenseSource] | type[ExpenseStatus],
    values: list[str],
) -> None:
    enum_type = Expense.__table__.c[column_name].type
    assert isinstance(enum_type, Enum)
    assert enum_type.enum_class is enum_class
    assert enum_type.enums == values
    assert [member.value for member in enum_class] == values

    dialect = postgresql.dialect()
    bind = enum_type.bind_processor(dialect)
    read = enum_type.result_processor(dialect, None)
    assert bind is not None
    assert read is not None
    for member in enum_class:
        assert bind(member) == member.value
        assert read(member.value) is member
    with pytest.raises(LookupError):
        bind("invalid")


def test_expense_postgresql_ddl_contains_checks_and_defaults() -> None:
    ddl = str(CreateTable(Expense.__table__).compile(dialect=postgresql.dialect()))

    assert "created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL" in ddl
    assert "status VARCHAR(9) DEFAULT 'pending' NOT NULL" in ddl
    checks = {
        "total_amount_positive": "total_amount > 0",
        "exchange_rate_positive": "exchange_rate IS NULL OR exchange_rate > 0",
        "amount_uah_nonnegative": "amount_uah IS NULL OR amount_uah >= 0",
        "confirmed_requires_conversion": (
            "status <> 'confirmed' OR "
            "(exchange_rate IS NOT NULL AND amount_uah IS NOT NULL)"
        ),
        "source_allowed": "source IN ('manual', 'text', 'image')",
        "status_allowed": "status IN ('pending', 'confirmed', 'rejected')",
    }
    for name, condition in checks.items():
        assert f"CONSTRAINT ck_expenses_{name} CHECK ({condition})" in ddl


def test_adding_expense_to_user_sets_its_owner() -> None:
    user = User(telegram_user_id=123)
    expense = Expense()

    user.expenses.append(expense)

    assert expense.user is user


def test_assigning_expense_owner_adds_it_to_users_expenses() -> None:
    user = User(telegram_user_id=123)
    expense = Expense()

    expense.user = user

    assert user.expenses == [expense]
