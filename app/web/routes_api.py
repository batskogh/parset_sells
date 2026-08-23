"""JSON API (CRUD) — зручно для скриптів і зовнішніх інтеграцій."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import repository as repo
from app.db.base import get_session
from app.db.models import Product
from app.web.schemas import (
    PriceIn,
    PriceOut,
    ProductIn,
    ProductOut,
    ProductPage,
    ProductUpdate,
    ShopIn,
    ShopOut,
    ShopUpdate,
)

router = APIRouter(prefix="/api", tags=["api"])


def _to_product_out(product: Product) -> ProductOut:
    data = ProductOut.model_validate(product)
    # `prices` чіпаємо лише якщо вони вже завантажені: ліниве довантаження
    # в асинхронній сесії кидає MissingGreenlet
    if "prices" not in inspect(product).unloaded and product.prices:
        last = product.prices[0]
        data.last_price = last.price
        data.currency = last.currency
        data.last_price_at = last.parsed_at
    return data


# --------------------------------------------------------------- магазини


@router.get("/shops", response_model=list[ShopOut])
async def api_list_shops(session: AsyncSession = Depends(get_session)):
    return await repo.list_shops(session)


@router.post("/shops", response_model=ShopOut, status_code=status.HTTP_201_CREATED)
async def api_create_shop(payload: ShopIn, session: AsyncSession = Depends(get_session)):
    if await repo.get_shop_by_slug(session, payload.slug):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Магазин '{payload.slug}' вже існує")
    return await repo.create_shop(session, **payload.model_dump())


@router.patch("/shops/{shop_id}", response_model=ShopOut)
async def api_update_shop(
    shop_id: int, payload: ShopUpdate, session: AsyncSession = Depends(get_session)
):
    shop = await repo.update_shop(session, shop_id, **payload.model_dump(exclude_unset=True))
    if shop is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Магазин не знайдено")
    return shop


@router.delete("/shops/{shop_id}", status_code=status.HTTP_204_NO_CONTENT)
async def api_delete_shop(shop_id: int, session: AsyncSession = Depends(get_session)):
    if not await repo.delete_shop(session, shop_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Магазин не знайдено")


# ----------------------------------------------------------------- товари


@router.get("/products", response_model=ProductPage)
async def api_list_products(
    search: str | None = None,
    shop_id: int | None = None,
    only_active: bool = False,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
):
    flt = repo.ProductFilter(
        search=search, shop_id=shop_id, only_active=only_active, limit=limit, offset=offset
    )
    items = await repo.list_products(session, flt)
    total = await repo.count_products(session, flt)
    return ProductPage(
        total=total, limit=limit, offset=offset, items=[_to_product_out(p) for p in items]
    )


@router.get("/products/{product_id}", response_model=ProductOut)
async def api_get_product(product_id: int, session: AsyncSession = Depends(get_session)):
    product = await repo.get_product(session, product_id)
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Товар не знайдено")
    return _to_product_out(product)


@router.post("/products", response_model=ProductOut, status_code=status.HTTP_201_CREATED)
async def api_create_product(payload: ProductIn, session: AsyncSession = Depends(get_session)):
    if await repo.get_shop(session, payload.shop_id) is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Такого магазину немає")
    if await repo.get_product_by_url(session, payload.shop_id, payload.url):
        raise HTTPException(status.HTTP_409_CONFLICT, "Товар з таким URL у цьому магазині вже є")
    product = await repo.create_product(session, **payload.model_dump())
    return _to_product_out(await repo.get_product(session, product.id))


@router.patch("/products/{product_id}", response_model=ProductOut)
async def api_update_product(
    product_id: int, payload: ProductUpdate, session: AsyncSession = Depends(get_session)
):
    product = await repo.update_product(
        session, product_id, **payload.model_dump(exclude_unset=True)
    )
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Товар не знайдено")
    return _to_product_out(await repo.get_product(session, product_id))


@router.delete("/products/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def api_delete_product(product_id: int, session: AsyncSession = Depends(get_session)):
    if not await repo.delete_product(session, product_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Товар не знайдено")


# ------------------------------------------------------------------- ціни


@router.get("/products/{product_id}/prices", response_model=list[PriceOut])
async def api_list_prices(
    product_id: int,
    limit: int = Query(100, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
):
    return await repo.list_prices(session, product_id, limit=limit)


@router.post(
    "/products/{product_id}/prices",
    response_model=PriceOut,
    status_code=status.HTTP_201_CREATED,
)
async def api_add_price(
    product_id: int, payload: PriceIn, session: AsyncSession = Depends(get_session)
):
    if await repo.get_product(session, product_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Товар не знайдено")
    price = await repo.add_price(session, product_id=product_id, **payload.model_dump())
    return price


@router.delete("/prices/{price_id}", status_code=status.HTTP_204_NO_CONTENT)
async def api_delete_price(price_id: int, session: AsyncSession = Depends(get_session)):
    if not await repo.delete_price(session, price_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Запис ціни не знайдено")
