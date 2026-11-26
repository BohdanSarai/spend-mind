from sqlalchemy import Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import dialect
from sqlalchemy.schema import CreateIndex

from spend_mind.models import Base, Category, User


def test_category_table_is_registered_in_base_metadata() -> None:
    assert Base.metadata.tables["categories"] is Category.__table__


def test_category_columns_and_constraints() -> None:
    table = Category.__table__

    assert type(table.c.id.type) is Integer
    assert not table.c.id.nullable
    assert table.primary_key.name == "pk_categories"
    assert list(table.primary_key.columns.keys()) == ["id"]

    assert type(table.c.user_id.type) is Integer
    assert not table.c.user_id.nullable
    (foreign_key,) = table.c.user_id.foreign_keys
    assert foreign_key.column is User.__table__.c.id
    assert foreign_key.constraint.name == "fk_categories_user_id_users"

    assert isinstance(table.c.name.type, String)
    assert table.c.name.type.length == 100
    assert not table.c.name.nullable

    unique_constraints = {
        constraint.name: list(constraint.columns.keys())
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert not unique_constraints
    (index,) = table.indexes
    assert index.unique
    assert str(CreateIndex(index).compile(dialect=dialect())) == (
        "CREATE UNIQUE INDEX uq_categories_user_id_lower_name "
        "ON categories (user_id, lower(name))"
    )


def test_adding_category_to_user_sets_its_owner() -> None:
    user = User(telegram_user_id=123)
    category = Category(name="Groceries")

    user.categories.append(category)

    assert category.user is user


def test_assigning_category_owner_adds_it_to_users_categories() -> None:
    user = User(telegram_user_id=123)
    category = Category(name="Groceries")

    category.user = user

    assert user.categories == [category]
