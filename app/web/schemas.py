"""Pydantic-схеми для JSON API."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ShopIn(BaseModel):
    slug: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=255)
    base_url: str = ""
    is_active: bool = True


class ShopUpdate(BaseModel):
    title: str | None = None
    base_url: str | None = None
    is_active: bool | None = None


class ShopOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    title: str
    base_url: str
    is_active: bool
    created_at: datetime


class PriceIn(BaseModel):
    price: Decimal
    old_price: Decimal | None = None
    currency: str = "UAH"
    in_stock: bool = True


class PriceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    price: Decimal
    old_price: Decimal | None
    currency: str
    in_stock: bool
    parsed_at: datetime


class ProductIn(BaseModel):
    shop_id: int
    name: str = Field(min_length=1, max_length=512)
    url: str = Field(min_length=1, max_length=1024)
    external_id: str | None = None
    image_url: str | None = None
    category: str | None = None
    notes: str | None = None
    is_active: bool = True


class ProductUpdate(BaseModel):
    shop_id: int | None = None
    name: str | None = None
    url: str | None = None
    external_id: str | None = None
    image_url: str | None = None
    category: str | None = None
    notes: str | None = None
    is_active: bool | None = None


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    shop_id: int
    name: str
    url: str
    external_id: str | None
    image_url: str | None
    category: str | None
    notes: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    last_price: Decimal | None = None
    currency: str | None = None
    # коли саме зафіксовано останню ціну
    last_price_at: datetime | None = None


class ProductPage(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[ProductOut]
