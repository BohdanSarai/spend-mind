import pytest
from sqlalchemy import Integer, Numeric, String, inspect
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from spend_mind.models import Base, Category, Expense, ExpenseItem, Product


def test_expense_items_table_is_registered_in_base_metadata() -> None:
    assert Base.metadata.tables["expense_items"] is ExpenseItem.__table__


def test_expense_item_columns_and_keys() -> None:
    table = ExpenseItem.__table__
    columns = table.c
    assert {column.name for column in columns if column.nullable} == {
        "category_id",
        "product_id",
        "unit",
    }
    assert type(columns.id.type) is Integer
    assert table.primary_key.name == "pk_expense_items"
    assert list(table.primary_key.columns.keys()) == ["id"]

    for column, parent, ondelete in [
        (columns.expense_id, Expense, "CASCADE"),
        (columns.category_id, Category, "RESTRICT"),
        (columns.product_id, Product, "RESTRICT"),
    ]:
        assert type(column.type) is Integer
        (foreign_key,) = column.foreign_keys
        assert foreign_key.column is parent.__table__.c.id
        assert foreign_key.ondelete == ondelete
        assert foreign_key.constraint.name == (
            f"fk_expense_items_{column.name}_{parent.__tablename__}"
        )

    for column, length in [(columns.name, 255), (columns.unit, 30)]:
        assert isinstance(column.type, String)
        assert column.type.length == length

    for column, precision, scale in [
        (columns.quantity, 12, 3),
        (columns.amount, 14, 2),
    ]:
        assert isinstance(column.type, Numeric)
        assert (column.type.precision, column.type.scale) == (precision, scale)
        assert column.type.asdecimal


def test_expense_item_postgresql_ddl_contains_checks_and_default() -> None:
    ddl = str(CreateTable(ExpenseItem.__table__).compile(dialect=postgresql.dialect()))

    assert "quantity NUMERIC(12, 3) DEFAULT 1 NOT NULL" in ddl
    assert "CONSTRAINT ck_expense_items_quantity_positive CHECK (quantity > 0)" in ddl
    assert "CONSTRAINT ck_expense_items_amount_nonnegative CHECK (amount >= 0)" in ddl


@pytest.mark.parametrize(
    ("parent_model", "collection_name", "item_attribute", "passive_deletes", "cascade"),
    [
        (
            Expense,
            "items",
            "expense",
            True,
            {"save-update", "merge", "delete", "delete-orphan"},
        ),
        (Category, "expense_items", "category", "all", {"save-update", "merge"}),
        (Product, "expense_items", "product", "all", {"save-update", "merge"}),
    ],
)
def test_expense_item_relationships(
    parent_model: type[Expense] | type[Category] | type[Product],
    collection_name: str,
    item_attribute: str,
    passive_deletes: bool | str,
    cascade: set[str],
) -> None:
    relationship = inspect(parent_model).relationships[collection_name]
    assert relationship.passive_deletes == passive_deletes
    assert relationship.cascade == cascade

    parent = parent_model()
    appended_item = ExpenseItem()
    collection = getattr(parent, collection_name)
    collection.append(appended_item)
    assert getattr(appended_item, item_attribute) is parent

    assigned_item = ExpenseItem()
    setattr(assigned_item, item_attribute, parent)
    assert collection == [appended_item, assigned_item]
