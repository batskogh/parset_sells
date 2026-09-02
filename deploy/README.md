# Розгортання на домашньому сервері (Proxmox)

Проєкт живе на власній машині: код лишається як є, SQLite на диску, бот на
polling. Publicʼної адреси не потрібно — Telegram опитується самим ботом.

## Чому LXC, а не віртуалка

Для Python-застосунку без власного ядра віртуальна машина — зайвий шар:
окреме ядро, окрема памʼять, повільніший старт. LXC-контейнер ділить ядро з
хостом і споживає стільки, скільки справді треба (тут — сотні мегабайтів).

Знадобиться: **1 vCPU, 512 МБ RAM, 4 ГБ диска**. База росте повільно — один
замір ціни це десятки байтів, тож навіть за роки щоденного стеження за
сотнею товарів рахунок іде на мегабайти.

## 1. Контейнер у Proxmox

У веб-інтерфейсі Proxmox: **Create CT**, шаблон `debian-13` або
`ubuntu-24.04`. Мережа — DHCP, решта за замовчуванням.

Перевірено на Ubuntu 24.04 з **Python 3.12** — це мінімальна версія, на якій
проєкт працює (потрібен сучасний синтаксис типів).

Усередині контейнера:

```bash
apt update && apt install -y python3-venv python3-pip git
```

## 2. Окремий користувач

Служби не мають ходити під `root` — якщо в парсері колись знайдеться діра,
вона не має відкривати весь сервер.

```bash
adduser --system --group --home /opt/parset_sells pricebot
```

## 3. Код і залежності

```bash
git clone <адреса-репозиторію> /opt/parset_sells
cd /opt/parset_sells
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
mkdir -p data logs backups
chown -R pricebot:pricebot /opt/parset_sells
```

## 4. Налаштування

```bash
cp .env.example .env
nano .env          # вписати TELEGRAM_BOT_TOKEN і TELEGRAM_CHAT_ID
chmod 600 .env     # токен читає лише власник
chown pricebot:pricebot .env
```

`.env` у git не потрапляє — токен доведеться вписати на сервері руками.

## 5. Перенесення наявної бази

Якщо історія цін уже назбирана на іншій машині, **не копіюйте `prices.db`
звичайним `cp`**: база в режимі WAL, і свіжі транзакції лежать у
`prices.db-wal`. Копія вийде старішою за оригінал, мовчки. Знімайте копію
скриптом:

```bash
python deploy/backup_db.py --dir /tmp/перенос
```

Отриманий файл покладіть у `/opt/parset_sells/data/prices.db` — міграції
самі доведуть його до поточної схеми при першому запуску.

## 6. Служби

```bash
cp deploy/systemd/*.service deploy/systemd/*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now pricebot.service
systemctl enable --now priceparse.timer pricenotify.timer pricebackup.timer
```

Що коли працює:

| Служба | Коли | Що робить |
|---|---|---|
| `pricebot.service` | постійно | відповідає в Telegram, приймає посилання |
| `priceparse.timer` | 07:00 (±30 хв) | обхід магазинів, запис цін |
| `pricenotify.timer` | 08:00 (±10 хв) | розсилка знижок підписникам |
| `pricebackup.timer` | 03:00 | копія бази, тримає 14 останніх |

Розкид часу (`RandomizedDelaySec`) навмисний: рівний ритм запитів секунда в
секунду виглядає як робот і дратує магазини.

## 7. Перевірка

```bash
systemctl status pricebot.service
systemctl list-timers 'price*'
journalctl -u pricebot.service -f
```

Разовий прогін, не чекаючи таймера:

```bash
systemctl start priceparse.service
journalctl -u priceparse.service -n 30
```

## Оновлення коду

```bash
cd /opt/parset_sells
sudo -u pricebot git pull
sudo -u pricebot .venv/bin/pip install -r requirements.txt
systemctl restart pricebot.service
```

Міграції бази виконуються самі при старті — окремої команди не треба.

## Якщо щось пішло не так

**Бот мовчить.** `systemctl status pricebot` покаже, чи живий процес.
`Restart=always` піднімає його сам, але після п'яти падінь за хвилину
systemd зупиняється й чекає на людину — це захист від нескінченного циклу
при зламаному конфізі.

**Сповіщення не приходять.** Перевірте руками, що бачить система:

```bash
sudo -u pricebot .venv/bin/python run_discounts.py --modes
sudo -u pricebot .venv/bin/python run_notify.py      # показ без відправки
```

**Магазин перестав парситись.** Швидше за все, змінив розмітку або поставив
антибот-захист. `run_parser.py --site <slug> --dry-run --log-level DEBUG`
покаже причину.
