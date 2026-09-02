#!/usr/bin/env python3
"""Резервна копія бази.

Копіювати файл `prices.db` звичайним `cp` не можна: база працює в режимі
WAL, і свіжі транзакції лежать поруч у `prices.db-wal`. Копія самого файлу
виходить старішою за оригінал — мовчки, без жодної помилки. Тому копіюємо
через `sqlite3.Connection.backup()`: він знімає узгоджений зріз навіть тоді,
коли хтось саме зараз пише.

    python deploy/backup_db.py                # у ./backups
    python deploy/backup_db.py --keep 30      # тримати 30 останніх
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import BASE_DIR, get_settings  # noqa: E402


def make_backup(target_dir: Path, *, keep: int) -> Path:
    source_path = get_settings().db_path
    if not source_path.is_file():
        raise SystemExit(f"Бази немає: {source_path}")

    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    target_path = target_dir / f"prices-{stamp}.db"

    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    target = sqlite3.connect(target_path)
    try:
        with target:
            source.backup(target)
        # копія має відкриватись і містити дані — інакше це не копія
        rows = target.execute("select count(*) from prices").fetchone()[0]
    finally:
        source.close()
        target.close()

    print(f"Копія: {target_path.name} · цін у ній: {rows}")
    prune(target_dir, keep=keep)
    return target_path


def prune(target_dir: Path, *, keep: int) -> None:
    """Лишає лише `keep` найсвіжіших копій."""
    copies = sorted(target_dir.glob("prices-*.db"), reverse=True)
    for old in copies[keep:]:
        old.unlink()
        print(f"Прибрано стару копію: {old.name}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Резервна копія бази цін")
    parser.add_argument("--dir", default=None, help="куди складати (типово ./backups)")
    parser.add_argument("--keep", type=int, default=14, help="скільки копій тримати")
    args = parser.parse_args()

    target_dir = Path(args.dir) if args.dir else BASE_DIR / "backups"
    make_backup(target_dir, keep=args.keep)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
