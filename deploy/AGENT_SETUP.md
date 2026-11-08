# Інструкція для агента: розгортання парсера цін у Docker на Proxmox

Ти розгортаєш телеграм-бота, що стежить за цінами в інтернет-магазинах і пише
людям, коли ціна справді впала. Код готовий і перевірений; твоє завдання —
підняти його в Docker на цій машині, переконатися, що кожна частина працює, і
звітувати людині.

Репозиторій: `https://github.com/batskogh/parset_sells.git`, гілка `main`.
Людська інструкція — `deploy/DOCKER.md` у репозиторії; цей документ спирається
на неї, але розписаний покроково з перевірками.

Що буде запущено (`docker-compose.yml`): `migrate` (разово доводить схему
бази), `bot` (постійно), `scheduler` (обхід магазинів о 00/06/12/18, розсилка о
07/13/19, бекап о 03:00). Плюс на хості — таймер systemd для автооновлення.

## Правила, які не порушуються

1. **Не змінюй файли репозиторію на сервері** — ні код, ні `Dockerfile`, ні
   `docker-compose.yml`, ні тести. Автооновлення навмисно відмовляється
   працювати, якщо в робочій копії є незакомічені зміни, — твоя «маленька
   правка» вимкне оновлення назавжди. Якщо щось треба виправити — опиши
   проблему людині, виправлення прийде через git.
2. **Не роби `git commit` і `git push` з сервера.**
3. **Токен бота не читай, не виводь і не проси вставити в чат.** `.env`
   заповнює людина сама. Не роби `cat .env` і `docker compose config` (він
   друкує змінні з `.env`); перевіряй лише, що ключі непорожні (команда нижче).
4. **Не запускай `run_notify.py --send`** руками. Для перевірки —
   `run_notify.py` без прапорців: він лише показує, що пішло б.
5. **Не обходь антибот-захист магазинів.** 403 / Cloudflare challenge — факт
   для звіту, а не задача.
6. **Не видаляй `/opt/parset_sells/data`** і не роби `docker compose down`
   з видаленням томів — там уся історія цін.
7. **Не чіпай інші контейнери, віртуалки й Docker-проєкти** на цій машині.
8. Зупиняйся й питай людину в місцях, позначених **⏸ Питання людині**.

## Крок 0. Де ти і що питати

```bash
command -v pct && echo "ХОСТ PROXMOX" || echo "НЕ ХОСТ"
systemd-detect-virt
```

Docker має жити **не на самому хості Proxmox**, а у віртуальній машині
(рекомендовано) або в LXC-контейнері з `nesting=1,keyctl=1` (працює, але
Proxmox це офіційно не підтримує: оновлення ядра хоста іноді ламають
overlayfs усередині).

**⏸ Питання людині — перед стартом, усе разом:**

- Якщо ти на хості Proxmox: де розгортати? Варіант А — людина створює
  віртуальну машину (Debian 13 / Ubuntu 24.04, 1 vCPU, 1 ГБ RAM, 8 ГБ диска) і
  дає тобі доступ туди. Варіант Б — ти створюєш LXC-контейнер із `nesting=1`
  і `keyctl=1` (тоді спитай CTID, сховище з `pvesm status`, міст мережі).
  Без явної відповіді нічого не створюй.
- Репозиторій публічний чи приватний? Від цього залежить крок 9.
- Порожня база чи перенесення історії з іншої машини (крок 5)?
- Чи **зупинений бот на іншій машині** (Windows-ПК розробника, або служба
  `pricebot.service` від розгортання без Docker)? Telegram віддає оновлення
  лише одному процесу на токен — два боти постійно вибиватимуть один одного.

Далі всі кроки — **всередині цільової машини** (ВМ або LXC), від root.

## Крок 1. Docker, git, часовий пояс

```bash
apt update && apt install -y git ca-certificates curl
command -v docker || curl -fsSL https://get.docker.com | sh
docker compose version
timedatectl set-timezone Europe/Kyiv
timedatectl | grep 'Time zone'
```

Перевірка: `docker compose version` друкує версію v2; `Time zone:
Europe/Kyiv`. Пояс контейнерів задано в compose окремо, але таймер
автооновлення живе на хості — хай збігаються.

## Крок 2. Код

```bash
git clone https://github.com/batskogh/parset_sells.git /opt/parset_sells
cd /opt/parset_sells
git merge-base --is-ancestor 3a2f7de HEAD && echo "КОД СВІЖИЙ" || echo "КОД СТАРИЙ"
test -f docker-compose.yml && echo "COMPOSE Є" || echo "COMPOSE НЕМАЄ"
```

Якщо `КОД СТАРИЙ` або `COMPOSE НЕМАЄ` — **зупинись**: на GitHub немає останніх
комітів, людині треба зробити `git push origin main` з машини розробника.

Теки для даних — власник uid 1000, бо застосунок у контейнері працює не від
root і писати може лише туди:

```bash
mkdir -p data logs backups
chown -R 1000:1000 data logs backups
```

## Крок 3. Налаштування (`.env`)

```bash
cd /opt/parset_sells
cp .env.example .env
chmod 600 .env
```

**⏸ Питання людині:** відкрити файл (`nano /opt/parset_sells/.env`) і
вписати `TELEGRAM_BOT_TOKEN` (від @BotFather) і `TELEGRAM_CHAT_ID` (її власний
id — туди приходять службові повідомлення). Решту лишити типовим.

Перевірка без виводу секретів:

```bash
for k in TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID; do
  grep -qE "^$k=.+" .env && echo "$k: заповнено" || echo "$k: ПОРОЖНЬО"
done
```

## Крок 4. Образ і тести

```bash
docker compose build
docker compose run --rm --no-deps bot python -m pytest -q -p no:cacheprovider
```

Перевірка: тести **усі зелені** (на момент написання близько 290; число може
зрости). Мережі вони не потребують. Червоне — не лагодь, збери вивід для
людини.

Часовий пояс і план запусків:

```bash
docker compose run --rm --no-deps scheduler python run_scheduler.py --plan
```

Перший рядок має показати київський час (`EET`/`EEST`), не UTC.

## Крок 5. База даних

**Порожня база (типово)** — нічого не роби, схему створить служба `migrate`.

**Перенесення історії.** Людина знімає копію на старій машині скриптом — не
копіюванням файлу: база в режимі WAL, і звичайна копія `prices.db` мовчки
втрачає свіжі транзакції.

```bash
python deploy/backup_db.py --dir <тека>
```

Файл передається на сервер (наприклад, `scp`). Ти кладеш його як
`/opt/parset_sells/data/prices.db`, робиш `chown 1000:1000` і перевіряєш, що
поруч **немає** старих `prices.db-wal` / `prices.db-shm`.

## Крок 6. Живий запит до магазинів

Чи відповідають магазини саме з цієї машини. Без запису в базу:

```bash
docker compose run --rm --no-deps bot python run_parser.py --site citrus --dry-run
docker compose run --rm --no-deps bot python run_parser.py --site maudau --dry-run
```

Перевірка: у підсумку `[OK ] citrus: товарів 1` (так само maudau). 403 —
у звіт і далі (правило 5).

## Крок 7. Запуск

```bash
docker compose up -d
docker compose ps
```

Перевірка: `migrate` — `Exited (0)`, `bot` і `scheduler` — `Up`.

**Бот:**

```bash
docker compose logs --tail 10 bot
```

Має бути `Start polling`. `TelegramConflictError` — десь ще працює бот із тим
самим токеном (крок 0).

**⏸ Питання людині:** написати боту `/start` у Telegram і підтвердити, що він
відповів.

**Планувальник** при першому старті одразу робить обхід (так задумано —
наздоганяння пропущеного). Зачекай, доки він закінчиться (до кількох хвилин):

```bash
docker compose logs --tail 20 scheduler
grep "Прогін завершено" logs/parser.log | tail -1
```

**Розсилка — лише показ:**

```bash
docker compose run --rm --no-deps bot python run_notify.py
```

На свіжій базі — «нових знижок немає»: звичну ціну система рахує щонайменше
за 5 днів спостережень.

**Бекап:**

```bash
docker compose exec bot python deploy/backup_db.py
ls -la backups/
```

## Крок 8. Перезапуск переживається

```bash
docker compose restart
sleep 10
docker compose ps
docker compose logs --tail 5 scheduler
```

Перевірка: обидва знову `Up`; планувальник **не** запускає обхід повторно
(він щойно був), а пише «Наступний parse: …». Зупинка не має тривати 10 секунд
на контейнер — якщо триває, у звіт.

## Крок 9. Автооновлення

**Лише для приватного репозиторію** — ключ розгортання для root:

```bash
test -f /root/.ssh/id_ed25519 || ssh-keygen -t ed25519 -f /root/.ssh/id_ed25519 -N ""
cat /root/.ssh/id_ed25519.pub
```

**⏸ Питання людині:** додати цей публічний ключ у GitHub → репозиторій →
Settings → Deploy keys (лише читання). Після підтвердження:

```bash
cd /opt/parset_sells
git remote set-url origin git@github.com:batskogh/parset_sells.git
ssh -o StrictHostKeyChecking=accept-new -T git@github.com
git fetch --dry-run && echo "FETCH ОК"
```

**Для всіх:**

```bash
cd /opt/parset_sells
systemd-analyze verify deploy/docker/priceupdate-docker.service deploy/docker/priceupdate-docker.timer
cp deploy/docker/priceupdate-docker.service deploy/docker/priceupdate-docker.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now priceupdate-docker.timer
systemctl start priceupdate-docker.service
journalctl -u priceupdate-docker.service -n 10 --no-pager
```

Перевірка: `Нічого нового: main на <коміт>.` Якщо «незбережені зміни в коді» —
`git status` покаже що; віддай людині, сам не скидай (правило 1).
`git status --porcelain` загалом має бути **порожнім**.

## Крок 10. Звіт людині

Коротко, по пунктах, з фактичним виводом там, де щось не так:

- де розгорнуто (ВМ / LXC, ОС), версія Docker, часовий пояс хоста й контейнера;
- тести: скільки пройшло;
- магазини: які відповіли, які дали помилку і яку;
- `docker compose ps`; бот відповів на `/start`?
- перший обхід: рядок «Прогін завершено …»;
- вивід `run_scheduler.py --plan`;
- бекап, перезапуск, автооновлення — результат кожного;
- усе, що пішло не за інструкцією, і що ти **свідомо не робив** (з причиною).

## Відомі пастки

| Симптом | Причина |
|---|---|
| `Permission denied` на `data/` чи `logs/` | теки не віддані uid 1000 — крок 2 |
| `--plan` показує UTC | образ без `tzdata` або `TZ` не дійшов; перезібрати |
| `TelegramConflictError` у логах бота | інший процес опитує той самий токен |
| `docker stop` триває 10 с | прибрано `init: true` з compose — його не можна прибирати |
| Автооновлення «нічого не робить» | приватний репозиторій без deploy key, або правки в коді на сервері |
| Оновлення «відкладено» щоразу | планувальник саме виконує роботу; якщо завжди — обхід зависає, дивись `logs/parser.log` |
| Сповіщень немає вночі | так задумано: тихі години 22:00–08:00, розсилка лише о 07/13/19 |
| База «зіпсувалась» на мережевому диску | SQLite у WAL не можна на NFS/SMB — лише локальний диск |
