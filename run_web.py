"""Запуск локальної сторінки: python run_web.py [--port 8010] [--host 127.0.0.1]"""

from __future__ import annotations

import argparse
import socket

import uvicorn

from app.config import get_settings


def is_port_busy(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def main() -> None:
    settings = get_settings()

    arg_parser = argparse.ArgumentParser(description="Локальна панель керування товарами")
    arg_parser.add_argument("--host", default=settings.web_host)
    arg_parser.add_argument("--port", type=int, default=settings.web_port)
    arg_parser.add_argument("--reload", action="store_true", help="автоперезапуск при змінах коду")
    args = arg_parser.parse_args()

    if is_port_busy(args.host, args.port):
        raise SystemExit(
            f"Порт {args.port} уже зайнятий. Вкажіть інший: "
            f"`python run_web.py --port {args.port + 1}` "
            f"або змініть WEB_PORT у .env"
        )

    print(f"Панель керування: http://{args.host}:{args.port}")
    uvicorn.run(
        "app.web.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
