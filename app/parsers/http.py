"""HTTP-клієнт із ретраями, таймаутами та підтримкою проксі."""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "uk-UA,uk;q=0.9,en;q=0.8",
}

RETRY_STATUSES = {408, 425, 429, 500, 502, 503, 504}


class FetchError(RuntimeError):
    """Не вдалося отримати сторінку після всіх спроб."""


@dataclass(slots=True)
class HttpConfig:
    timeout: float = 20.0
    retries: int = 3
    retry_backoff: float = 2.0
    headers: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_HEADERS))
    proxy: str | None = None
    follow_redirects: bool = True
    verify_ssl: bool = True
    http2: bool = False


class HttpClient:
    """Тонка обгортка над httpx.AsyncClient.

    Один екземпляр = одна сесія (свої cookies) = один сайт.
    """

    def __init__(self, config: HttpConfig | None = None) -> None:
        self.config = config or HttpConfig()
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "HttpClient":
        self._client = httpx.AsyncClient(
            timeout=self.config.timeout,
            headers=self.config.headers,
            follow_redirects=self.config.follow_redirects,
            verify=self.config.verify_ssl,
            proxy=self.config.proxy,
            http2=self.config.http2,
        )
        return self

    async def __aexit__(self, *exc_info) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("HttpClient використовується поза `async with`")
        return self._client

    async def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        last_error: Exception | None = None

        for attempt in range(1, self.config.retries + 1):
            try:
                response = await self.client.request(method, url, **kwargs)
                if response.status_code in RETRY_STATUSES:
                    raise httpx.HTTPStatusError(
                        f"HTTP {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()
                return response
            except (httpx.HTTPError, httpx.StreamError) as exc:
                last_error = exc
                status = getattr(getattr(exc, "response", None), "status_code", None)
                # 4xx (окрім RETRY_STATUSES) повторювати немає сенсу
                if status is not None and status not in RETRY_STATUSES:
                    break
                if attempt < self.config.retries:
                    pause = self.config.retry_backoff ** (attempt - 1)
                    pause += random.uniform(0, 0.5)
                    logger.warning(
                        "Спроба %s/%s для %s не вдалась (%s), пауза %.1fс",
                        attempt, self.config.retries, url, exc, pause,
                    )
                    await asyncio.sleep(pause)

        raise FetchError(f"Не вдалося завантажити {url}: {last_error}") from last_error

    async def get_text(self, url: str, **kwargs) -> str:
        response = await self.request("GET", url, **kwargs)
        return response.text

    async def get_json(self, url: str, **kwargs):
        response = await self.request("GET", url, **kwargs)
        return response.json()
