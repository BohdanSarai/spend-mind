from pgvector.sqlalchemy import Vector
from sqlalchemy import Integer, String

from spend_mind.models import Base, Product, ProductType


def test_product_table_is_registered_in_base_metadata() -> None:
    assert Base.metadata.tables["products"] is Product.__table__


def test_product_columns_and_constraints() -> None:
    table = Product.__table__

    assert type(table.c.id.type) is Integer
    assert not table.c.id.nullable
    assert table.primary_key.name == "pk_products"
    assert list(table.primary_key.columns.keys()) == ["id"]

    assert isinstance(table.c.name.type, String)
    assert table.c.name.type.length == 255
    assert not table.c.name.nullable

    assert type(table.c.product_type_id.type) is Integer
    assert not table.c.product_type_id.nullable
    (foreign_key,) = table.c.product_type_id.foreign_keys
    assert foreign_key.column is ProductType.__table__.c.id
    assert foreign_key.constraint.name == "fk_products_product_type_id_product_types"


def test_product_embedding_is_a_nullable_1536_dimensional_vector() -> None:
    embedding = Product.__table__.c.embedding

    assert isinstance(embedding.type, Vector)
    assert embedding.type.dim == 1536
    assert embedding.nullable


def test_adding_product_to_product_type_sets_its_type() -> None:
    product_type = ProductType(name="Fruit")
    product = Product(name="Apple")

    product_type.products.append(product)

    assert product.product_type is product_type


def test_assigning_product_type_adds_product_to_its_collection() -> None:
    product_type = ProductType(name="Fruit")
    product = Product(name="Apple")

    product.product_type = product_type

    assert product_type.products == [product]
