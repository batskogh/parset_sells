#!/usr/bin/env bash
#
# Оновлення коду на сервері: git pull, залежності, міграції, тести, перезапуск.
#
# Потрібне тому, що машина стоїть на горищі й ходити до неї не хочеться.
# Достатньо `git push` — решту зробить таймер (priceupdate.timer) або ця
# команда, запущена руками по SSH:
#
#     sudo /opt/parset_sells/deploy/update.sh
#
# Головне правило: зламаний коміт не має лишати людей без бота. Тому перед
# перезапуском ганяються тести, і якщо вони не проходять, код повертається
# на попередній коміт, а служби так і працюють на старій робочій версії.
#
# Увесь скрипт загорнутий у фігурні дужки навмисно: bash читає файл шматками
# по ходу виконання, а ми цей самий файл посеред роботи оновлюємо через git.
# Дужки змушують прочитати й розібрати все одразу, до першої дії.
{
set -uo pipefail

APP_DIR="${APP_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
APP_USER="${APP_USER:-pricebot}"
PYTHON="$APP_DIR/.venv/bin/python"
PIP="$APP_DIR/.venv/bin/pip"
BOT_SERVICE="pricebot.service"
PARSE_SERVICE="priceparse.service"

cd "$APP_DIR" || { echo "Немає каталогу $APP_DIR"; exit 1; }

# Файли належать APP_USER, і root не має лишати по собі чужих власників:
# один git pull від root — і наступний від pricebot уже не пройде
as_app() {
    if [ "$(id -un)" = "$APP_USER" ]; then
        "$@"
    else
        runuser -u "$APP_USER" -- "$@"
    fi
}

# Звіт власнику. Не вдалося написати — не привід валити оновлення:
# воно або вже відбулося, або вже відкотилось
report() {
    as_app "$PYTHON" deploy/notify_owner.py "$@" || true
}

fail() {
    echo "ЗБІЙ: $1"
    report --status failed --branch "${BRANCH:-}" --details "$1"
    exit 1
}

# --- чи час зараз оновлюватись ------------------------------------------------

# Обхід магазинів пише в базу й читає код із диска. Міняти файли під ним —
# найкоротший шлях до збою, який потім не відтвориш. Наступне коло за годину
if systemctl is-active --quiet "$PARSE_SERVICE" 2>/dev/null; then
    echo "Зараз іде обхід магазинів — оновлення відкладено."
    exit 0
fi

BRANCH="$(as_app git rev-parse --abbrev-ref HEAD 2>/dev/null)"
if [ -z "$BRANCH" ] || [ "$BRANCH" = "HEAD" ]; then
    fail "Не вдалося визначити гілку (detached HEAD?)"
fi

if ! as_app git fetch --quiet origin "$BRANCH"; then
    fail "Не вдалося зв'язатися з origin — мережа або доступ до репозиторію"
fi

BEFORE="$(as_app git rev-parse HEAD)"
AFTER="$(as_app git rev-parse "origin/$BRANCH")"

if [ "$BEFORE" = "$AFTER" ]; then
    echo "Нічого нового: $BRANCH на $(as_app git rev-parse --short HEAD)."
    exit 0
fi

# Незакомічені правки на сервері означають, що хтось міняв код руками. Перезаписати
# їх мовчки не можна — це єдина копія тієї роботи
if ! as_app git diff --quiet || ! as_app git diff --cached --quiet; then
    fail "На сервері є незбережені зміни в коді — оновлення зупинено, щоб їх не втратити"
fi

echo "Оновлення $BRANCH: $(as_app git rev-parse --short HEAD) -> $(as_app git rev-parse --short "origin/$BRANCH")"

# Заголовки нових комітів — щоб у телеграмі було видно, за що оновились
mapfile -t COMMITS < <(as_app git log --format=%s "$BEFORE..$AFTER" | head -10)
COMMIT_ARGS=()
for заголовок in "${COMMITS[@]}"; do
    COMMIT_ARGS+=(--commit "$заголовок")
done

# --- власне оновлення ---------------------------------------------------------

if ! as_app git merge --ff-only "origin/$BRANCH"; then
    fail "Гілка розійшлася з origin — швидке перемотування неможливе"
fi

# Залежності ставимо лише коли список змінився: pip навіть без роботи
# витрачає час і мережу
if ! as_app git diff --quiet "$BEFORE" "$AFTER" -- requirements.txt; then
    echo "Список залежностей змінився — оновлюю."
    if ! as_app "$PIP" install --quiet -r requirements.txt; then
        as_app git reset --hard --quiet "$BEFORE"
        fail "Не стали залежності; код повернуто на $(as_app git rev-parse --short HEAD)"
    fi
fi

# Міграції явно, до перезапуску: краще впасти тут і лишитись на старому коді,
# ніж підняти бота на схемі, якої він не розуміє
if ! as_app "$PYTHON" -c "from app.db.migrate import upgrade_database; upgrade_database()"; then
    as_app git reset --hard --quiet "$BEFORE"
    fail "Міграція не пройшла; код повернуто на $(as_app git rev-parse --short HEAD)"
fi

# --- перевірка перед перезапуском ---------------------------------------------

echo "Ганяю тести..."
TESTS_LOG="$(as_app "$PYTHON" -m pytest -q 2>&1)"
TESTS_OK=$?

if [ $TESTS_OK -ne 0 ]; then
    echo "$TESTS_LOG" | tail -30
    as_app git reset --hard --quiet "$BEFORE"
    # залежності лишаємо як є: зайвий пакет шкоди не робить, а зняти рівно
    # ті, що доставились, надійно все одно не вийде
    report --status rolled_back --branch "$BRANCH" \
        --revision "$(as_app git rev-parse --short HEAD)" \
        --details "$(echo "$TESTS_LOG" | tail -20)"
    echo "Тести не пройшли — код повернуто на $(as_app git rev-parse --short HEAD)."
    exit 1
fi

# --- перезапуск ---------------------------------------------------------------

if ! systemctl restart "$BOT_SERVICE"; then
    fail "Код оновлено, але бот не перезапустився — потрібні очі"
fi

REVISION="$(as_app git rev-parse --short HEAD)"
echo "Готово: $BRANCH на $REVISION, бот перезапущений."
report --status updated --branch "$BRANCH" --revision "$REVISION" "${COMMIT_ARGS[@]}"
exit 0
}
