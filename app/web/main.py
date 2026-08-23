"""FastAPI-застосунок: локальна сторінка керування товарами."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.db.base import dispose_db, init_db
from app.web.routes_api import router as api_router
from app.web.routes_ui import router as ui_router

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await init_db()
    yield
    await dispose_db()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Парсер цін — панель керування",
        description="CRUD для товарів, магазинів та історії цін",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(ui_router)
    app.include_router(api_router)
    return app


app = create_app()
