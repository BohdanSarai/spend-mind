"""Make category names unique per user regardless of case."""

import sqlalchemy as sa
from alembic import op

revision = "3f21ca90de47"
down_revision = "22f4fa012d2b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM categories
                GROUP BY user_id, lower(name)
                HAVING count(*) > 1
            ) THEN
                RAISE EXCEPTION 'Case-insensitive category name collisions exist';
            END IF;
        END;
        $$;
    """)
    op.create_index(
        "uq_categories_user_id_lower_name",
        "categories",
        ["user_id", sa.text("lower(name)")],
        unique=True,
    )
    op.drop_constraint("uq_categories_user_id_name", "categories", type_="unique")


def downgrade() -> None:
    op.create_unique_constraint(
        "uq_categories_user_id_name", "categories", ["user_id", "name"]
    )
    op.drop_index("uq_categories_user_id_lower_name", table_name="categories")
