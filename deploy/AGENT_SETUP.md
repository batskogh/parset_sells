# Інструкція для агента: розгортання парсера цін на Proxmox

Ти розгортаєш телеграм-бота, що стежить за цінами в інтернет-магазинах і пише
людям, коли ціна справді впала. Код готовий і перевірений; твоє завдання —
поставити його на цю машину, переконатися, що кожна частина працює, і
звітувати людині.

Репозиторій: `https://github.com/batskogh/parset_sells.git`, гілка `main`.
Повна людська інструкція — `deploy/README.md` у репозиторії; цей документ
спирається на неї, але розписаний покроково з перевірками.

## Правила, які не порушуються

1. **Не змінюй файли репозиторію на сервері.** Жодних правок коду, конфігів
   юнітів чи тестів у `/opt/parset_sells`. Автооновлення (`deploy/update.sh`)
   навмисно відмовляється працювати, якщо в робочій копії є незакомічені
   зміни, — твоя «маленька правка» зламає оновлення назавжди. Якщо щось
   треба виправити в коді — опиши проблему людині, виправлення прийде через git.
2. **Не роби `git commit` і `git push` з сервера.**
3. **Токен бота не читай, не виводь і не проси вставити в чат.** Файл `.env`
   заповнює людина сама. Не роби `cat .env`; перевіряй лише, що потрібні
   ключі в ньому непорожні (команда нижче).
4. **Не запускай `run_notify.py --send`.** Розсилка людям — лише через таймер
   після завершення розгортання. Для перевірки — `run_notify.py` без прапорців:
   він лише показує, що пішло б.
5. **Не обходь антибот-захист магазинів.** Якщо магазин відповідає 403 /
   Cloudflare challenge — це факт для звіту, а не задача для розв'язання.
6. **Застосунок не працює під root.** Усе, що стосується коду, — від
   користувача `pricebot` через `runuser -u pricebot -- …` (`sudo` у
   контейнерах Debian зазвичай немає).
7. **Не чіпай інші контейнери й віртуалки** на цьому хості.
8. Зупиняйся й питай людину в місцях, позначених **⏸ Питання людині**.

## Крок 0. Де ти і чи готовий код

Визнач, де запущений: на хості Proxmox чи вже всередині контейнера.

```bash
command -v pct && echo "ХОСТ PROXMOX" || echo "НЕ ХОСТ (ймовірно, контейнер)"
```

**⏸ Питання людині — перед стартом, усе разом:**

- Якщо ти на хості: створити новий LXC-контейнер? Який CTID, яке сховище
  (`pvesm status` покаже варіанти), який міст мережі (зазвичай `vmbr0`)?
  Без явного «так» контейнер не створюй.
- Репозиторій публічний чи приватний? Від цього залежить крок 9.
- Починати з порожньої бази чи переносити історію з іншої машини (крок 6)?
- Чи **зупинений бот на іншій машині** (Windows-ПК розробника)? Telegram
  віддає оновлення лише одному процесу на токен — два боти з одним токеном
  постійно вибиватимуть один одного (`TelegramConflictError`).

## Крок 1. Контейнер (лише якщо ти на хості й людина погодилась)

Шаблон — Debian 13 або Ubuntu 24.04. Ресурси: 1 vCPU, 512 МБ RAM, 4 ГБ диска.

```bash
pveam update
pveam available --section system | grep -E 'debian-13|ubuntu-24.04'
```

Створи контейнер командою `pct create` з параметрами, які назвала людина
(unprivileged, `--onboot 1`, мережа DHCP), запусти його й далі працюй
усередині через `pct enter <CTID>`.

Перевірка: усередині контейнера є мережа.

```bash
getent hosts github.com
```

## Крок 2. Система, Python, часовий пояс

```bash
apt update && apt install -y python3-venv python3-pip git openssh-client
```

Python має бути **3.12 або новіший** — на 3.11 проєкт не запуститься:

```bash
python3 -c 'import sys; assert sys.version_info >= (3, 12), sys.version; print("ok", sys.version)'
```

**Часовий пояс — обов'язково, не косметика.** Свіжий контейнер стоїть у UTC,
а розклад systemd і «тихі години» бота рахуються за місцевим часом. Без цього
розсилка о «07:00» прийде о 10:00 за Києвом, і ніхто на це не поскаржиться.

```bash
timedatectl set-timezone Europe/Kyiv
timedatectl | grep 'Time zone'
```

Перевірка: `Time zone: Europe/Kyiv`.

## Крок 3. Користувач і код

```bash
adduser --system --group --home /opt/parset_sells pricebot
runuser -u pricebot -- git clone https://github.com/batskogh/parset_sells.git /opt/parset_sells
cd /opt/parset_sells
```

**Перевір, що код свіжий.** На GitHub міг лишитися старий стан, якщо розробник
не запушив останні коміти:

```bash
runuser -u pricebot -- git merge-base --is-ancestor 3a2f7de HEAD && echo "КОД СВІЖИЙ" || echo "КОД СТАРИЙ"
```

Якщо `КОД СТАРИЙ` — **зупинись**: скажи людині, що на GitHub немає останніх
комітів і потрібно зробити `git push origin main` з машини розробника. Далі не
йди — на старому коді немає ні бота, ні таймерів.

## Крок 4. Віртуальне середовище й залежності

Від імені `pricebot`, щоб автооновлення потім могло ставити пакети само:

```bash
runuser -u pricebot -- python3 -m venv /opt/parset_sells/.venv
runuser -u pricebot -- /opt/parset_sells/.venv/bin/pip install -r /opt/parset_sells/requirements.txt
runuser -u pricebot -- mkdir -p /opt/parset_sells/data /opt/parset_sells/logs /opt/parset_sells/backups
```

## Крок 5. Налаштування (`.env`)

```bash
runuser -u pricebot -- cp /opt/parset_sells/.env.example /opt/parset_sells/.env
chmod 600 /opt/parset_sells/.env
```

**⏸ Питання людині:** попроси її відкрити файл і вписати два значення —
`TELEGRAM_BOT_TOKEN` (від @BotFather) і `TELEGRAM_CHAT_ID` (її власний id;
туди приходять службові повідомлення). Наприклад:
`nano /opt/parset_sells/.env`. Решту значень лишити типовими.

Перевірка без виводу секретів:

```bash
cd /opt/parset_sells
for k in TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID; do
  grep -qE "^$k=.+" .env && echo "$k: заповнено" || echo "$k: ПОРОЖНЬО"
done
```

## Крок 6. База даних

**Порожня база (типово).** Нічого не роби — схема підніметься сама на кроці 7.

**Перенесення історії.** Людина знімає копію на старій машині саме скриптом —
не копіюванням файлу: база в режимі WAL, і звичайна копія `prices.db` мовчки
втрачає свіжі транзакції з `prices.db-wal`.

```bash
python deploy/backup_db.py --dir <тека>
```

Отриманий файл людина передає на сервер (наприклад, `scp`). Ти кладеш його як
`/opt/parset_sells/data/prices.db`, `chown pricebot:pricebot` на нього, і
переконуєшся, що поруч **немає** старих `prices.db-wal` / `prices.db-shm`.

## Крок 7. Міграції й тести

```bash
cd /opt/parset_sells
runuser -u pricebot -- .venv/bin/python -c "from app.db.migrate import upgrade_database; upgrade_database(); print('схема ок')"
runuser -u pricebot -- .venv/bin/python -m pytest -q
```

Перевірка: тести **усі зелені** (на момент написання — 265 passed, 1 skipped;
число може зрости). Мережі вони не потребують. Якщо щось червоне — не лагодь,
збери вивід і віддай людині.

## Крок 8. Живий запит до магазинів

Перевіряє, що з цієї машини магазини взагалі відповідають. Без запису в базу:

```bash
runuser -u pricebot -- .venv/bin/python run_parser.py --site citrus --dry-run
runuser -u pricebot -- .venv/bin/python run_parser.py --site maudau --dry-run
```

Перевірка: у підсумку `[OK ] citrus: товарів 1` (так само для maudau), код
виходу 0. Якщо магазин дає 403 — запиши в звіт і йди далі (правило 5).

## Крок 9. Доступ до git без пароля (лише для приватного репозиторію)

Автооновлення робить `git fetch` щогодини без людини поруч. Для приватного
репозиторію потрібен ключ розгортання:

```bash
runuser -u pricebot -- mkdir -p /opt/parset_sells/.ssh
runuser -u pricebot -- ssh-keygen -t ed25519 -f /opt/parset_sells/.ssh/id_ed25519 -N ""
cat /opt/parset_sells/.ssh/id_ed25519.pub
```

**⏸ Питання людині:** додати цей публічний ключ у GitHub → репозиторій →
Settings → Deploy keys (лише читання). Після підтвердження:

```bash
runuser -u pricebot -- git -C /opt/parset_sells remote set-url origin git@github.com:batskogh/parset_sells.git
runuser -u pricebot -- ssh -o StrictHostKeyChecking=accept-new -T git@github.com
runuser -u pricebot -- git -C /opt/parset_sells fetch --dry-run && echo "FETCH ОК"
```

Домашній каталог `pricebot` — це сама робоча копія, тож ключ лягає в
`/opt/parset_sells/.ssh/`. Git його ігнорує (`.ssh/` є в `.gitignore`), і
`git status --porcelain` має лишитись **порожнім** — перевір.

## Крок 10. Служби systemd

Спершу перевір юніти — `systemd-analyze` ловить помилки, на які systemd при
запуску мовчить:

```bash
cd /opt/parset_sells
systemd-analyze verify deploy/systemd/*.service deploy/systemd/*.timer
```

Попередження «marked executable» не страшне; будь-що інше — у звіт.

```bash
cp deploy/systemd/*.service deploy/systemd/*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now pricebot.service
systemctl enable --now priceparse.timer pricenotify.timer pricebackup.timer priceupdate.timer
```

## Крок 11. Перевірка, що все живе

**Бот:**

```bash
systemctl is-active pricebot.service
tail -n 5 /opt/parset_sells/logs/bot.log
```

Очікується `active` і рядок `Start polling`. Якщо в журналі
`TelegramConflictError` — десь ще працює бот із тим самим токеном (див. крок 0).

**⏸ Питання людині:** написати боту `/start` у Telegram і підтвердити, що він
відповів.

**Таймери:**

```bash
systemctl list-timers 'price*'
```

Очікуються чотири таймери з найближчим часом спрацювання в **місцевому** часі:
обхід (00/06/12/18), розсилка (07/13/19), бекап (03:00), оновлення (щогодини о :40).

**Перший обхід — не чекаючи таймера:**

```bash
systemctl start priceparse.service
journalctl -u priceparse.service -n 20 --no-pager
grep "Прогін завершено" /opt/parset_sells/logs/parser.log | tail -1
```

`systemctl start` для цієї служби чекає завершення обходу — це нормально, до
кількох хвилин.

**Розсилка — лише показ:**

```bash
runuser -u pricebot -- .venv/bin/python run_notify.py
```

На свіжій базі очікується «нових знижок немає»: звичну ціну система рахує
щонайменше за 5 днів спостережень.

**Бекап:**

```bash
systemctl start pricebackup.service
ls -la /opt/parset_sells/backups/
```

**Автооновлення:**

```bash
systemctl start priceupdate.service
journalctl -u priceupdate.service -n 10 --no-pager
```

Очікується `Нічого нового: main на <коміт>.` Якщо `незбережені зміни в коді` —
у робочій копії щось змінено (правило 1): `git status` покаже що, і це треба
віддати людині, а не скидати самому.

## Крок 12. Звіт людині

Коротко, по пунктах, з фактичним виводом там, де щось не так:

- де розгорнуто (CTID / хост), ОС і версія Python, часовий пояс;
- тести: скільки пройшло;
- магазини: які відповіли, які дали помилку і яку;
- бот: `active`, чи відповів на `/start`;
- таймери: вивід `systemctl list-timers 'price*'`;
- перший обхід: рядок «Прогін завершено …»;
- бекап і автооновлення: результат;
- усе, що пішло не за інструкцією, і що ти **свідомо не робив** (з причиною).

## Відомі пастки (щоб не шукати заново)

| Симптом | Причина |
|---|---|
| Розклад зсунутий на 2–3 години | контейнер у UTC — крок 2 |
| `TelegramConflictError` у журналі бота | інший процес опитує той самий токен |
| Автооновлення «нічого не робить» | приватний репозиторій без deploy key, або незакомічені правки на сервері |
| `Permission denied` на `update.sh` | файл без біта виконання — у git він стоїть, перевір `ls -l deploy/update.sh` після клону |
| Копія бази «старіша» за оригінал | копіювали `cp` замість `deploy/backup_db.py` |
| `priceparse.service` → `failed` | не зібралося **нічого** з жодного магазину; один недоступний магазин на це не впливає |
| Сповіщень немає вночі | так задумано: тихі години 22:00–08:00, розсилка лише о 07/13/19 |
