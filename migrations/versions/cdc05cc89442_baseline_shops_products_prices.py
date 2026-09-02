"""baseline: shops, products, prices

Стан схеми, який існував до підключення alembic — його створював
`Base.metadata.create_all`. Наявні бази вже на цьому рівні, тому їм ця
ревізія не виконується, а лише проставляється (`alembic stamp`). Нові бази
проходять її звичайним `upgrade`.

Revision ID: cdc05cc89442
Revises:
Create Date: 2026-09-02 19:25:41.866086
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "cdc05cc89442"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "shops",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("base_url", sa.String(length=512), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("shops", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_shops_slug"), ["slug"], unique=True)

    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("shop_id", sa.Integer(), nullable=False),
        sa.Column("external_id", sa.String(length=128), nullable=True),
        sa.Column("name", sa.String(length=512), nullable=False),
        sa.Column("url", sa.String(length=1024), nullable=False),
        sa.Column("image_url", sa.String(length=1024), nullable=True),
        sa.Column("category", sa.String(length=255), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("shop_id", "url", name="uq_product_shop_url"),
    )
    with op.batch_alter_table("products", schema=None) as batch_op:
        batch_op.create_index("ix_product_shop_external", ["shop_id", "external_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_products_shop_id"), ["shop_id"], unique=False)

    op.create_table(
        "prices",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("price", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("old_price", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("in_stock", sa.Boolean(), nullable=False),
        sa.Column(
            "parsed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("prices", schema=None) as batch_op:
        batch_op.create_index("ix_price_product_time", ["product_id", "parsed_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_prices_parsed_at"), ["parsed_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_prices_product_id"), ["product_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("prices", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_prices_product_id"))
        batch_op.drop_index(batch_op.f("ix_prices_parsed_at"))
        batch_op.drop_index("ix_price_product_time")
    op.drop_table("prices")

    with op.batch_alter_table("products", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_products_shop_id"))
        batch_op.drop_index("ix_product_shop_external")
    op.drop_table("products")

    with op.batch_alter_table("shops", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_shops_slug"))
    op.drop_table("shops")
