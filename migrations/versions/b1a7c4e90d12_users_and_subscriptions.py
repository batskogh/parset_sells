"""користувачі телеграму і підписки на товари

Перша справжня зміна схеми після підключення alembic. Додає два поняття, без
яких бот неможливий: людину і те, за чим вона стежить. Наявні таблиці не
чіпає — історія цін лишається спільною для всіх підписників.

Revision ID: b1a7c4e90d12
Revises: cdc05cc89442
Create Date: 2026-09-02 19:30:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b1a7c4e90d12"
down_revision: Union[str, Sequence[str], None] = "cdc05cc89442"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "telegram_users",
        sa.Column("id", sa.Integer(), nullable=False),
        # BigInteger навмисно: telegram_id давно вийшли за межі 32 біт
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column("first_name", sa.String(length=128), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("telegram_users", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_telegram_users_chat_id"), ["chat_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_telegram_users_telegram_id"), ["telegram_id"], unique=True
        )

    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        # порожнє поле означає «поріг як налаштовано глобально»
        sa.Column("min_drop_percent", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("is_muted", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["telegram_users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "product_id", name="uq_subscription_user_product"),
    )
    with op.batch_alter_table("subscriptions", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_subscriptions_product_id"), ["product_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_subscriptions_user_id"), ["user_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("subscriptions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_subscriptions_user_id"))
        batch_op.drop_index(batch_op.f("ix_subscriptions_product_id"))
    op.drop_table("subscriptions")

    with op.batch_alter_table("telegram_users", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_telegram_users_telegram_id"))
        batch_op.drop_index(batch_op.f("ix_telegram_users_chat_id"))
    op.drop_table("telegram_users")
