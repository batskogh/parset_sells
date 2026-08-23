"""HTML-інтерфейс: CRUD для товарів, магазинів і цін."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import repository as repo
from app.db.base import get_session

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

PAGE_SIZE = 25


def _redirect(url: str, **params) -> RedirectResponse:
    if params:
        url = f"{url}?{urlencode({k: v for k, v in params.items() if v not in (None, '')})}"
    return RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)


def _decimal(raw: str | None) -> Decimal | None:
    if raw is None or not str(raw).strip():
        return None
    try:
        return Decimal(str(raw).replace(",", ".").strip())
    except InvalidOperation:
        return None


# ------------------------------------------------------------------ товари


@router.get("/", include_in_schema=False)
async def index() -> RedirectResponse:
    return _redirect("/products")


@router.get("/products", response_class=HTMLResponse)
async def products_page(
    request: Request,
    search: str | None = None,
    shop_id: int | None = None,
    only_active: bool = False,
    page: int = 1,
    msg: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    page = max(1, page)
    flt = repo.ProductFilter(
        search=search,
        shop_id=shop_id,
        only_active=only_active,
        limit=PAGE_SIZE,
        offset=(page - 1) * PAGE_SIZE,
    )
    products = await repo.list_products(session, flt)
    total = await repo.count_products(session, flt)

    return templates.TemplateResponse(
        request,
        "products.html",
        {
            "products": products,
            "shops": await repo.list_shops(session),
            "total": total,
            "page": page,
            "pages": max(1, -(-total // PAGE_SIZE)),
            "search": search or "",
            "shop_id": shop_id,
            "only_active": only_active,
            "msg": msg,
        },
    )


@router.get("/products/new", response_class=HTMLResponse)
async def product_new_form(request: Request, session: AsyncSession = Depends(get_session)):
    return templates.TemplateResponse(
        request,
        "product_form.html",
        {"product": None, "shops": await repo.list_shops(session)},
    )


@router.post("/products/new")
async def product_create(
    shop_id: int = Form(...),
    name: str = Form(...),
    url: str = Form(...),
    external_id: str = Form(""),
    category: str = Form(""),
    image_url: str = Form(""),
    notes: str = Form(""),
    is_active: bool = Form(False),
    price: str = Form(""),
    currency: str = Form("UAH"),
    session: AsyncSession = Depends(get_session),
):
    if await repo.get_product_by_url(session, shop_id, url):
        return _redirect("/products", msg="Товар з таким URL у цьому магазині вже існує")

    product = await repo.create_product(
        session,
        shop_id=shop_id,
        name=name.strip(),
        url=url.strip(),
        external_id=external_id.strip() or None,
        category=category.strip() or None,
        image_url=image_url.strip() or None,
        notes=notes.strip() or None,
        is_active=is_active,
    )

    start_price = _decimal(price)
    if start_price is not None:
        await repo.add_price(
            session, product_id=product.id, price=start_price, currency=currency or "UAH"
        )
    return _redirect(f"/products/{product.id}", msg="Товар створено")


@router.get("/products/{product_id}", response_class=HTMLResponse)
async def product_detail(
    request: Request,
    product_id: int,
    msg: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    product = await repo.get_product(session, product_id)
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Товар не знайдено")
    return templates.TemplateResponse(
        request,
        "product_detail.html",
        {
            "product": product,
            "prices": await repo.list_prices(session, product_id, limit=200),
            "msg": msg,
        },
    )


@router.get("/products/{product_id}/edit", response_class=HTMLResponse)
async def product_edit_form(
    request: Request, product_id: int, session: AsyncSession = Depends(get_session)
):
    product = await repo.get_product(session, product_id)
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Товар не знайдено")
    return templates.TemplateResponse(
        request,
        "product_form.html",
        {"product": product, "shops": await repo.list_shops(session)},
    )


@router.post("/products/{product_id}/edit")
async def product_update(
    product_id: int,
    shop_id: int = Form(...),
    name: str = Form(...),
    url: str = Form(...),
    external_id: str = Form(""),
    category: str = Form(""),
    image_url: str = Form(""),
    notes: str = Form(""),
    is_active: bool = Form(False),
    session: AsyncSession = Depends(get_session),
):
    product = await repo.update_product(
        session,
        product_id,
        shop_id=shop_id,
        name=name.strip(),
        url=url.strip(),
        external_id=external_id.strip() or None,
        category=category.strip() or None,
        image_url=image_url.strip() or None,
        notes=notes.strip() or None,
        is_active=is_active,
    )
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Товар не знайдено")
    return _redirect(f"/products/{product_id}", msg="Зміни збережено")


@router.post("/products/{product_id}/delete")
async def product_delete(product_id: int, session: AsyncSession = Depends(get_session)):
    deleted = await repo.delete_product(session, product_id)
    return _redirect("/products", msg="Товар видалено" if deleted else "Товар не знайдено")


# -------------------------------------------------------------------- ціни


@router.post("/products/{product_id}/prices")
async def price_add(
    product_id: int,
    price: str = Form(...),
    old_price: str = Form(""),
    currency: str = Form("UAH"),
    in_stock: bool = Form(False),
    session: AsyncSession = Depends(get_session),
):
    value = _decimal(price)
    if value is None:
        return _redirect(f"/products/{product_id}", msg="Некоректна ціна")
    await repo.add_price(
        session,
        product_id=product_id,
        price=value,
        old_price=_decimal(old_price),
        currency=currency or "UAH",
        in_stock=in_stock,
    )
    return _redirect(f"/products/{product_id}", msg="Ціну додано")


@router.post("/prices/{price_id}/delete")
async def price_delete(
    price_id: int, product_id: int = Form(...), session: AsyncSession = Depends(get_session)
):
    await repo.delete_price(session, price_id)
    return _redirect(f"/products/{product_id}", msg="Запис ціни видалено")


# ---------------------------------------------------------------- магазини


@router.get("/shops", response_class=HTMLResponse)
async def shops_page(
    request: Request, msg: str | None = None, session: AsyncSession = Depends(get_session)
):
    shops = await repo.list_shops(session)
    counts = {
        shop.id: await repo.count_products(session, repo.ProductFilter(shop_id=shop.id))
        for shop in shops
    }
    return templates.TemplateResponse(
        request, "shops.html", {"shops": shops, "counts": counts, "msg": msg}
    )


@router.post("/shops/new")
async def shop_create(
    slug: str = Form(...),
    title: str = Form(...),
    base_url: str = Form(""),
    is_active: bool = Form(False),
    session: AsyncSession = Depends(get_session),
):
    slug = slug.strip()
    if await repo.get_shop_by_slug(session, slug):
        return _redirect("/shops", msg=f"Магазин '{slug}' вже існує")
    await repo.create_shop(
        session, slug=slug, title=title.strip(), base_url=base_url.strip(), is_active=is_active
    )
    return _redirect("/shops", msg="Магазин створено")


@router.post("/shops/{shop_id}/edit")
async def shop_update(
    shop_id: int,
    title: str = Form(...),
    base_url: str = Form(""),
    is_active: bool = Form(False),
    session: AsyncSession = Depends(get_session),
):
    shop = await repo.get_shop(session, shop_id)
    if shop is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Магазин не знайдено")
    shop.title = title.strip()
    shop.base_url = base_url.strip()
    shop.is_active = is_active
    return _redirect("/shops", msg="Зміни збережено")


@router.post("/shops/{shop_id}/delete")
async def shop_delete(shop_id: int, session: AsyncSession = Depends(get_session)):
    deleted = await repo.delete_shop(session, shop_id)
    return _redirect(
        "/shops",
        msg="Магазин і його товари видалено" if deleted else "Магазин не знайдено",
    )
