# Розгортання в Docker (Proxmox)

Альтернатива до розгортання на голій системі з systemd ([README.md](README.md)).
Код той самий; різниця в тому, хто веде розклад і як іде оновлення.

| | Голий сервер (systemd) | Docker |
|---|---|---|
| розклад | таймери systemd | контейнер `scheduler` (`run_scheduler.py`) |
| перезапуск після збою | `Restart=always` | `restart: unless-stopped` |
| оновлення | `deploy/update.sh` | `deploy/docker/update.sh` |
| дані | `/opt/parset_sells/data` | та сама тека, змонтована в контейнер |

## Де запускати Docker у Proxmox

**Рекомендовано — віртуальна машина** (Debian 13 або Ubuntu 24.04, 1 vCPU,
1 ГБ RAM, 8 ГБ диска). Docker усередині LXC-контейнера теж працює (потрібні
`nesting=1` і `keyctl=1` в опціях контейнера), але Proxmox це офіційно не
підтримує: оновлення ядра хоста час від часу ламають overlayfs у такому
контейнері, і розбиратись із цим доведеться саме тоді, коли бот мовчить.

## 1. Docker і часовий пояс

```bash
apt update && apt install -y git ca-certificates curl
curl -fsSL https://get.docker.com | sh
timedatectl set-timezone Europe/Kyiv
```

Часовий пояс контейнера задано в `docker-compose.yml` (`TZ: Europe/Kyiv`) і
від хоста не залежить, але таймер оновлення живе на хості — хай вони
збігаються.

## 2. Код

```bash
git clone https://github.com/batskogh/parset_sells.git /opt/parset_sells
cd /opt/parset_sells
mkdir -p data logs backups
chown -R 1000:1000 data logs backups
```

`chown 1000` — бо застосунок у контейнері працює не від root, а від
користувача з uid 1000, і писати він має право лише в ці три теки.

## 3. Налаштування

```bash
cp .env.example .env
chmod 600 .env
nano .env
```

Обов'язково — `TELEGRAM_BOT_TOKEN` і `TELEGRAM_CHAT_ID`. `.env` в образ не
потрапляє (`.dockerignore`): compose підставляє його при запуску.

## 4. Запуск

```bash
docker compose up -d --build
```

Стартують три служби:

| Служба | Що робить |
|---|---|
| `migrate` | доводить схему бази до поточної версії й завершується |
| `bot` | працює постійно, відповідає в Telegram |
| `scheduler` | обхід о 00/06/12/18, розсилка о 07/13/19, бекап о 03:00 |

`migrate` окремим кроком навмисно: бот і планувальник стартують одночасно, і
на свіжій базі обидва полізли б створювати ті самі таблиці.

Планувальник при першому старті одразу робить обхід — не чекає наступного
кола. Так само після будь-якого простою: усе, що пропущено, поки контейнер
лежав, він наздоганяє одразу після старту.

## 5. Перевірка

```bash
docker compose ps
docker compose logs --tail 20 bot
docker compose exec scheduler python run_scheduler.py --plan
grep "Прогін завершено" logs/parser.log | tail -5
```

`--plan` показує місцевий час контейнера й найближчі запуски кожної роботи.
Якщо час там не київський — образ зібрано без `tzdata` або `TZ` не дійшов.

Тести в тому самому образі, що працює:

```bash
docker compose run --rm --no-deps bot python -m pytest -q -p no:cacheprovider
```

## 6. Автооновлення

```bash
cp deploy/docker/priceupdate-docker.service deploy/docker/priceupdate-docker.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now priceupdate-docker.timer
```

Щогодини о :40 `deploy/docker/update.sh` дивиться в git і, якщо там нове:
збирає образ, **ганяє тести в новому образі** і лише тоді перезапускає
контейнери. Тести не пройшли — код і образ повертаються на попередню версію,
контейнери так і працюють на старій, у телеграм приходить хвіст логу.
`docker compose build` запущених контейнерів не чіпає, тож відкат нікого не
зупиняє.

Не оновлюється, якщо планувальник саме виконує роботу (обхід обірвався б), і
якщо на сервері є правки в коді, зроблені руками.

Для приватного репозиторію потрібен ключ розгортання — див. розділ
«Якщо репозиторій приватний» у [README.md](README.md); тут ключ лежить у
`/root/.ssh/`, бо оновлення йде від root.

## Пастки, специфічні для Docker

**Два боти з одним токеном.** Telegram віддає оновлення лише одному процесу.
Якщо на іншій машині ще працює `run_bot.py` — або на цьому ж сервері вже
запущена служба `pricebot.service` з розгортання без Docker, — вони постійно
вибиватимуть один одного (`TelegramConflictError` у логах).

**`Permission denied` на `data/`.** Теки на хості не віддані uid 1000 —
крок 2.

**Час у контейнері UTC.** Образ без `tzdata` мовчки ігнорує `TZ`. У нашому
`Dockerfile` він стоїть — якщо `--plan` показує UTC, перевірте, що образ
перезібрано.

**`docker stop` думає 10 секунд.** Python як процес №1 не реагує на SIGTERM
(ядро ігнорує сигнали без обробника для PID 1). Тому в compose стоїть
`init: true` — не прибирайте.

**Не кладіть `data/` на мережевий диск** (NFS, SMB). SQLite у режимі WAL
покладається на блокування файлів, які мережеві ФС реалізують ненадійно, —
база псується тихо.

**Копія бази — лише скриптом**, як і без Docker:

```bash
docker compose exec bot python deploy/backup_db.py
```
