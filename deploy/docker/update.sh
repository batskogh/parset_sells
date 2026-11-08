#!/usr/bin/env bash
#
# Оновлення коду для Docker-розгортання: git pull, збірка образу, тести в
# новому образі, перезапуск контейнерів.
#
# Запускається на хості Docker (не в контейнері) — таймером
# priceupdate-docker.timer або руками:
#
#     sudo /opt/parset_sells/deploy/docker/update.sh
#
# Те саме головне правило, що й у deploy/update.sh для голого сервера:
# зламаний коміт не має лишати людей без бота. Тести ганяються в щойно
# зібраному образі **до** перезапуску; якщо вони не пройшли, код повертається
# на попередній коміт, образ перезбирається зі старого коду, а контейнери так
# і працюють на старій версії — `docker compose build` не чіпає запущених.
#
# Тіло загорнуте у фігурні дужки, бо git оновлює цей самий файл посеред його
# виконання, а bash читає скрипт шматками.
{
set -uo pipefail

APP_DIR="${APP_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
cd "$APP_DIR" || { echo "Немає каталогу $APP_DIR"; exit 1; }

compose() { docker compose "$@"; }

# Звіт власнику — з контейнера, бо токен і залежності живуть там.
# Не вийшло написати — не привід валити оновлення
report() {
    compose run --rm --no-deps -T bot python deploy/notify_owner.py "$@" || true
}

fail() {
    echo "ЗБІЙ: $1"
    report --status failed --branch "${BRANCH:-}" --details "$1"
    exit 1
}

# --- чи час зараз оновлюватись ------------------------------------------------

# Перезапуск посеред обходу обірвав би його — наступне коло за годину
if compose exec -T scheduler python run_scheduler.py --busy >/dev/null 2>&1; then
    echo "Планувальник саме виконує роботу — оновлення відкладено."
    exit 0
fi

BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null)"
if [ -z "$BRANCH" ] || [ "$BRANCH" = "HEAD" ]; then
    fail "Не вдалося визначити гілку (detached HEAD?)"
fi

if ! git fetch --quiet origin "$BRANCH"; then
    fail "Не вдалося зв'язатися з origin — мережа або доступ до репозиторію"
fi

BEFORE="$(git rev-parse HEAD)"
AFTER="$(git rev-parse "origin/$BRANCH")"

if [ "$BEFORE" = "$AFTER" ]; then
    echo "Нічого нового: $BRANCH на $(git rev-parse --short HEAD)."
    exit 0
fi

# правки руками на сервері — єдина копія тієї роботи, затирати не можна
if ! git diff --quiet || ! git diff --cached --quiet; then
    fail "На сервері є незбережені зміни в коді — оновлення зупинено, щоб їх не втратити"
fi

echo "Оновлення $BRANCH: $(git rev-parse --short HEAD) -> $(git rev-parse --short "origin/$BRANCH")"

mapfile -t COMMITS < <(git log --format=%s "$BEFORE..$AFTER" | head -10)
COMMIT_ARGS=()
# ім'я змінної латиницею навмисно: кирилиця в іменах — синтаксична помилка bash
for subject in "${COMMITS[@]}"; do
    COMMIT_ARGS+=(--commit "$subject")
done

# --- новий код і новий образ --------------------------------------------------

if ! git merge --ff-only --quiet "origin/$BRANCH"; then
    fail "Гілка розійшлася з origin — швидке перемотування неможливе"
fi

# повертає і код, і образ: інакше наступний перезапуск хоста підняв би
# контейнери з образу, зібраного зі зламаного коду
rollback() {
    git reset --hard --quiet "$BEFORE"
    compose build --quiet
}

if ! compose build --quiet; then
    rollback
    fail "Образ не зібрався; код повернуто на $(git rev-parse --short HEAD)"
fi

# --- перевірка перед перезапуском ---------------------------------------------

echo "Ганяю тести в новому образі..."
# -p no:cacheprovider: код у контейнері лише для читання, кешу pytest нема куди писати
TESTS_LOG="$(compose run --rm --no-deps -T bot python -m pytest -q -p no:cacheprovider 2>&1)"
TESTS_OK=$?

if [ $TESTS_OK -ne 0 ]; then
    echo "$TESTS_LOG" | tail -30
    rollback
    report --status rolled_back --branch "$BRANCH" \
        --revision "$(git rev-parse --short HEAD)" \
        --details "$(echo "$TESTS_LOG" | tail -20)"
    echo "Тести не пройшли — код повернуто на $(git rev-parse --short HEAD)."
    exit 1
fi

# --- перезапуск ---------------------------------------------------------------

# up -d пересоздає лише змінені контейнери; migrate відпрацює першим
if ! compose up -d; then
    fail "Образ оновлено, але контейнери не піднялися — потрібні очі"
fi

sleep 5
if [ -z "$(compose ps --status running -q bot)" ]; then
    fail "Після оновлення бот не працює — дивіться docker compose logs bot"
fi

# старі образи без тегу інакше накопичуються з кожним оновленням
docker image prune -f >/dev/null 2>&1 || true

REVISION="$(git rev-parse --short HEAD)"
echo "Готово: $BRANCH на $REVISION, контейнери перезапущені."
report --status updated --branch "$BRANCH" --revision "$REVISION" "${COMMIT_ARGS[@]}"
exit 0
}
