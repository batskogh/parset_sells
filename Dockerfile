# Образ парсера цін: один на всі служби — бот, планувальник, міграції.
# Що саме запускати, вирішує docker-compose.yml через `command`.

# 3.12 — мінімальна версія, на якій проєкт перевірено на Linux
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# tzdata обов'язковий: slim-образ без нього мовчки ігнорує TZ і живе в UTC,
# а розклад і тихі години рахуються за місцевим часом
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

# не root: якщо в парсері колись знайдеться діра, вона не відкриє контейнер.
# uid 1000 — щоб теки data/logs/backups на хості було легко віддати йому
RUN useradd --create-home --uid 1000 app

WORKDIR /app

# залежності окремим шаром: зміна коду не перевстановлює пакети
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# писати застосунок може лише в ці три теки — код лишається тільки для читання
RUN mkdir -p data logs backups && chown app:app data logs backups

USER app

CMD ["python", "run_bot.py"]
