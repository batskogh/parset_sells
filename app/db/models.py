"""Моделі БД: магазин -> товар -> історія цін."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Shop(Base):
    """Магазин (сайт), з якого беруться дані."""

    __tablename__ = "shops"

    id: Mapped[int] = mapped_column(primary_key=True)
    # slug збігається з `name` парсера — саме за ним прив'язується парсер до магазину
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    base_url: Mapped[str] = mapped_column(String(512), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    products: Mapped[list["Product"]] = relationship(
        back_populates="shop",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Shop {self.slug}>"


class Product(Base):
    """Товар у конкретному магазині."""

    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("shop_id", "url", name="uq_product_shop_url"),
        Index("ix_product_shop_external", "shop_id", "external_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    shop_id: Mapped[int] = mapped_column(
        ForeignKey("shops.id", ondelete="CASCADE"), index=True
    )
    # ідентифікатор товару в межах магазину (артикул/SKU), якщо сайт його віддає
    external_id: Mapped[str | None] = mapped_column(String(128), default=None)
    name: Mapped[str] = mapped_column(String(512))
    url: Mapped[str] = mapped_column(String(1024))
    image_url: Mapped[str | None] = mapped_column(String(1024), default=None)
    category: Mapped[str | None] = mapped_column(String(255), default=None)
    notes: Mapped[str | None] = mapped_column(Text, default=None)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    shop: Mapped["Shop"] = relationship(back_populates="products", lazy="joined")
    prices: Mapped[list["Price"]] = relationship(
        back_populates="product",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Price.parsed_at.desc()",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Product {self.id} {self.name[:40]!r}>"


class Price(Base):
    """Зріз ціни на конкретний момент часу."""

    __tablename__ = "prices"
    __table_args__ = (Index("ix_price_product_time", "product_id", "parsed_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    old_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), default=None)
    currency: Mapped[str] = mapped_column(String(8), default="UAH")
    in_stock: Mapped[bool] = mapped_column(Boolean, default=True)
    parsed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), index=True
    )

    product: Mapped["Product"] = relationship(back_populates="prices")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Price {self.price} {self.currency} @ {self.parsed_at}>"
