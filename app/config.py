"""Налаштування застосунку.

Читаються з файлу `.env` (якщо є) та зі змінних оточення.
Змінні оточення мають вищий пріоритет за `.env`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"


def _load_env_file(path: Path) -> None:
    """Мінімальний парсер .env — щоб не тягнути зайву залежність."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _get_str(key: str, default: str) -> str:
    return os.environ.get(key, default)


def _get_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


def _get_float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


def _get_bool(key: str, default: bool) -> bool:
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    db_path: Path
    max_concurrent_sites: int
    default_min_delay: float
    default_max_delay: float
    http_timeout: float
    http_retries: int
    http_retry_backoff: float
    store_price_only_on_change: bool
    web_host: str
    web_port: int
    log_level: str
    log_dir: Path
    log_to_file: bool
    log_retention_days: int
    telegram_bot_token: str
    telegram_chat_id: str
    discount_min_drop_percent: float
    discount_min_samples: int
    discount_bucket_percent: float
    notify_cooldown_hours: int
    notify_quiet_from: int
    notify_quiet_to: int
    notify_deeper_percent: float
    shop_request_cooldown_hours: int
    health_silent_days: int
    health_repeat_hours: int
    schedule_parse_hours: str
    schedule_notify_hours: str
    schedule_backup_hours: str

    @property
    def db_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.db_path.as_posix()}"

    @property
    def telegram_ready(self) -> bool:
        """Чи заповнено токен — щоб не падати на порожньому `.env`."""
        return bool(self.telegram_bot_token)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    _load_env_file(ENV_FILE)

    db_path = Path(_get_str("DB_PATH", "data/prices.db"))
    if not db_path.is_absolute():
        db_path = BASE_DIR / db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)

    log_dir = Path(_get_str("LOG_DIR", "logs"))
    if not log_dir.is_absolute():
        log_dir = BASE_DIR / log_dir

    return Settings(
        db_path=db_path,
        max_concurrent_sites=_get_int("MAX_CONCURRENT_SITES", 5),
        default_min_delay=_get_float("DEFAULT_MIN_DELAY", 1.0),
        default_max_delay=_get_float("DEFAULT_MAX_DELAY", 3.0),
        http_timeout=_get_float("HTTP_TIMEOUT", 20.0),
        http_retries=_get_int("HTTP_RETRIES", 3),
        http_retry_backoff=_get_float("HTTP_RETRY_BACKOFF", 2.0),
        store_price_only_on_change=_get_bool("STORE_PRICE_ONLY_ON_CHANGE", False),
        web_host=_get_str("WEB_HOST", "127.0.0.1"),
        web_port=_get_int("WEB_PORT", 8010),
        log_level=_get_str("LOG_LEVEL", "INFO").upper(),
        log_dir=log_dir,
        log_to_file=_get_bool("LOG_TO_FILE", True),
        log_retention_days=_get_int("LOG_RETENTION_DAYS", 14),
        telegram_bot_token=_get_str("TELEGRAM_BOT_TOKEN", "").strip(),
        telegram_chat_id=_get_str("TELEGRAM_CHAT_ID", "").strip(),
        discount_min_drop_percent=_get_float("DISCOUNT_MIN_DROP_PERCENT", 10.0),
        discount_min_samples=_get_int("DISCOUNT_MIN_SAMPLES", 5),
        discount_bucket_percent=_get_float("DISCOUNT_BUCKET_PERCENT", 1.0),
        notify_cooldown_hours=_get_int("NOTIFY_COOLDOWN_HOURS", 24),
        notify_quiet_from=_get_int("NOTIFY_QUIET_FROM", 22) % 24,
        notify_quiet_to=_get_int("NOTIFY_QUIET_TO", 8) % 24,
        notify_deeper_percent=_get_float("NOTIFY_DEEPER_PERCENT", 10.0),
        shop_request_cooldown_hours=_get_int("SHOP_REQUEST_COOLDOWN_HOURS", 24),
        health_silent_days=_get_int("HEALTH_SILENT_DAYS", 2),
        health_repeat_hours=_get_int("HEALTH_REPEAT_HOURS", 24),
        schedule_parse_hours=_get_str("SCHEDULE_PARSE_HOURS", "0,6,12,18"),
        schedule_notify_hours=_get_str("SCHEDULE_NOTIFY_HOURS", "7,13,19"),
        schedule_backup_hours=_get_str("SCHEDULE_BACKUP_HOURS", "3"),
    )
