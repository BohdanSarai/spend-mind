from sqlalchemy import Integer, String, UniqueConstraint

from spend_mind.models import Base, ProductType


def test_product_type_table_is_registered_in_base_metadata() -> None:
    assert Base.metadata.tables["product_types"] is ProductType.__table__


def test_product_type_columns_and_constraints() -> None:
    table = ProductType.__table__

    assert type(table.c.id.type) is Integer
    assert not table.c.id.nullable
    assert table.primary_key.name == "pk_product_types"
    assert list(table.primary_key.columns.keys()) == ["id"]

    assert isinstance(table.c.name.type, String)
    assert table.c.name.type.length == 100
    assert not table.c.name.nullable

    unique_constraints = {
        constraint.name: list(constraint.columns.keys())
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert unique_constraints == {"uq_product_types_name": ["name"]}
