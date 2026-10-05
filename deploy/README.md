# Боевой сервер ERP

Один VPS, всё в Docker: PostgreSQL 16, API (он же отдаёт сайт), фоновый воркер outbox, Caddy (HTTPS от
Let's Encrypt), ночные копии базы. Файлы (УПД, счета) лежат в базе, поэтому копия базы — это вся ERP.

## Сервер — свой компьютер в цеху (владелец, 29.09.2026)
- Железо: любой x86-64 ПК, от 4 ГБ RAM (лучше 8), SSD от 120 ГБ, проводной Ethernet; желательно второй диск
  или USB-диск под копии базы и ИБП.
- Ubuntu Server 24.04 LTS: пользователь `erp`, имя `erp-server`, галочка OpenSSH server, Import SSH identity →
  GitHub → `artemiyfraxis-source` (на GitHub должен быть ключ `~\.ssh\erp_server.pub` с компьютера владельца).
  В BIOS — Restore on AC Power Loss → Power On. На роутере — постоянный IP для сервера.
- Один раз на консоли сервера (sudo без пароля для выкладки + Tailscale для доступа снаружи):
  `echo "erp ALL=(ALL) NOPASSWD:ALL" | sudo tee /etc/sudoers.d/erp && curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up`
  (ссылку входа открыть на телефоне тем же аккаунтом Tailscale, что на компьютере владельца).
- В цеху сайт — `http://<IP сервера>` (cookie без Secure, `ERP_COOKIE_SECURE=0`); снаружи — `https://erp-server.<tailnet>.ts.net`
  через `tailscale serve` (решено агентом: Tailscale вместо проброса портов — не нужен белый IP, трафик шифрован,
  доступ только устройствам владельца). Для публичной машины с доменом: `bootstrap.sh <домен>` — Caddy с Let's Encrypt.
- Копии: `pg_dump` в `/opt/erp/backups`. На том же диске они не спасут от его поломки — смонтировать второй
  диск в `/opt/erp/backups` (сделает агент по ssh, если диск есть).

## Раскладка на сервере (`/opt/erp`)
| Путь | Что |
|---|---|
| `app/` | код (снимок `origin/main`, коммит в `app/REVISION`); `app.prev/` — прошлый |
| `config/.env` | `ERP_DOMAIN` (`:80` — сеть цеха), `ERP_COOKIE_SECURE`, пароль базы, пути к ключам Google (создаёт `bootstrap.sh`) |
| `config/secrets/` | `google-service-account.json` (erp-reader) — только чтение Google при импорте проектов и склада |
| `config/admin.txt`, `config/staff-pins.txt` | вход администратора и PIN сотрудников (после `load-real-data.sh base`) |
| `supplymap/` | снимок SUPPLYMAP для импорта сотрудников и справочников |
| `import/` | снимки Google для импорта склада |
| `backups/daily`, `backups/weekly`, `backups/monthly` | `pg_dump` в 03:00 МСК: 14 ежедневных + 8 воскресных + 36 за 1-е число (прослеживаемость хранится 3 года) |
| `backups/pre-update` | копия перед каждым обновлением `erp_<дата_время>.dump` (+ `-final.dump` прямо перед миграцией), рядом `.sha256`; хранятся 10 последних |
| `status/` | `update-result.json` (итог последнего обновления), `update-last.log`, `integrity/` (снимки до/после миграции), `migration-approved` (отмашка на разрушающие миграции) |

## Команды
С компьютера владельца (PowerShell, из `Projects\ERP_NEXT`; рабочую копию не трогает, берёт `origin/main`):
```powershell
.\deploy\push.ps1 -Server 192.168.1.85 -GoogleKey <ключ erp-reader.json> -LoadBase   # первый раз: подготовка машины
#   (prepare-host.sh: убрать microk8s, закрепить IP, расширить /, RAID под копии), установка, база; пароли → erp-server-secrets
# Автообновление с GitHub в 2:00 и 6:00 (копия базы, откат и плашка при сбое): один раз
#   sudo bash /opt/erp/app/deploy/install-auto-update.sh; потом erp-update status | erp-update now (сразу)
# Второй проект на этом сервере — отдельный пользователь: sudo bash /opt/erp/app/deploy/add-tenant.sh <имя> <ключ.pub>
.\deploy\push.ps1 -Server erp-server                 # обновление (перед ним копия базы)
.\deploy\push.ps1 -Server erp-server -Ref <коммит>   # откат кода
```
На сервере (`ssh -i ~\.ssh\erp_server erp@erp-server`, `sudo -i`, `cd /opt/erp/app/deploy`):
```bash
./erp.sh ps                         # состояние контейнеров
./erp.sh logs -f --tail 100 api     # журнал API (worker, db, caddy, backup — так же)
./erp.sh backup-now                 # копия базы прямо сейчас
./restore.sh /opt/erp/backups/daily/erp_<дата>.dump   # восстановить (сначала сама делает свежую копию)
./load-real-data.sh base            # admin, сотрудники с PIN, справочники, библиотека техкарт (проектов нет)
./load-real-data.sh kd              # КД проектов с Google Диска в базу (Диск только читается)
./load-real-data.sh projects 708К.4 # только по запросу: один проект из Google с его ТК (уже заведённый не трогает)
./load-real-data.sh stock           # сверка склада без записи; `stock --yes` — записать (один раз, в день перехода)
./erp.sh cli python -m app.identity_admin create-user --login ... --role admin   # ещё пользователь
```
## Обновление: протокол живых данных (владелец, 05.10.2026)
Правило: **бэкап → прогон на копии → сверка количеств строк → только потом прод.** Его выполняет `deploy/update.sh`
(его зовут таймер `erp-auto-update` в 2:00/6:00, кнопка «Обновить сервер» → `erp-update now`, и `push.ps1`):
1. Свободно меньше 20% диска (копии или Docker) — отказ, ничего не начато.
2. Новая версия собирается как образ `erp-next:candidate`; сервер всё это время работает на `erp-next:latest`.
3. **Всегда** копия базы в `backups/pre-update/erp_<дата_время>.dump` + `.sha256` (проверяется `pg_restore --list`
   и контрольной суммой); если стек был выключен — база поднимается для копии.
4. Ревизия базы (`alembic_version`) сравнивается с головой миграций новой версии:
   - совпадает — миграций нет, репетиция не нужна, дальше п. 6;
   - база новее кода (ревизии нет в новой версии) — отказ;
   - есть миграция с `DESTRUCTIVE = True` (на уровне модуля миграции), а её id нет в `status/migration-approved` —
     отказ «нужна отмашка владельца» (id в поле `approve` итога);
   - иначе **репетиция** `deploy/rehearse.sh`: одноразовый проект `erp-rehearsal` (postgres:16 на анонимном томе,
     внутренняя сеть, без секретов и без worker) ← `pg_restore` свежей копии → `app.db_integrity snapshot` →
     `alembic upgrade head` → `snapshot` → `compare` → новая версия на копии: `/api/health?db=1` с ревизией головы
     и три чтения (`/api/auth/login-staff`, `/api/auth/me`, `/api/stock`) от временного админа копии. Проект
     удаляется всегда. Красная репетиция — отказ, прод не тронут, старые контейнеры работают.
5. Зелёная → код переключается (`app` → `app.prev`); с миграциями: стоп api и worker → вторая копия
   `…-final.dump` (точное состояние до миграции) → снимок → `alembic upgrade head` → снимок → сверка на проде
   (снимки в `status/integrity/`). Миграция упала, а база не изменилась — прежний образ запускается снова.
   База уже изменена, а сверка красная — **api и worker остаются остановлены** (решение за владельцем, см. ниже).
6. `erp-next:candidate` → `erp-next:latest` (прежний остаётся `erp-next:previous`), `up -d`, ответ
   `/api/health?db=1` обязан назвать голову миграций. Без миграций при красном здоровье — откат на прежний образ.

API больше не накатывает миграции при старте (`backend/docker-entrypoint.sh`): он запускается, только когда ревизия
базы равна голове миграций образа, иначе останавливается с русской ошибкой в `./erp.sh logs api`. Исключение —
первая установка: `bootstrap.sh` на машине без тома базы запускает с `ERP_AUTO_MIGRATE=1` (в compose по умолчанию 0).

**Как читать итог.** `erp-update status` или `cat /opt/erp/status/update-result.json`:
`{"ok": true|false, "step": "<шаг>", "message": "<что случилось>", "revision": "<коммит>", "migration": "<голова>",
"db_revision": "<ревизия базы до>", "approve": "<id для отмашки>", "at": "<время>"}`. Шаги: `disk`, `build`,
`backup`, `plan`, `approval`, `rehearsal`, `stop`, `final-backup`, `migrate` / `integrity-missing` /
`snapshot-before` / `snapshot-after` / `compare`, `start`, `post`, `finished`. Полный журнал —
`/opt/erp/status/update-last.log`, история — `journalctl -u erp-auto-update`. Красное обновление показывает плашку
«Обновление не установилось» (последняя строка журнала — `ОБНОВЛЕНИЕ ОСТАНОВЛЕНО (<шаг>): …`).

**Отмашка на разрушающую миграцию:** id ревизии строкой в `/opt/erp/status/migration-approved`, затем `erp-update now`.

**Ручная репетиция без обновления** (ничего не меняет на проде, проверяет копию на текущей версии):
`sudo bash /opt/erp/app/deploy/rehearse.sh /opt/erp/backups/pre-update/<копия>.dump <голова>`
(голова: `docker run --rm erp-next:latest alembic heads`; образ — `ERP_CANDIDATE_IMAGE=erp-next:latest`).

### Ручное восстановление
- Сверка или старт после миграции красные (api и worker стоят): `cd /opt/erp/app/deploy &&
  ./restore.sh /opt/erp/backups/pre-update/erp_<дата_время>-final.dump` — база ровно как до миграции (api/worker
  были остановлены, записей после копии нет). Затем код той же версии: `push.ps1 -Ref <коммит из app.prev/REVISION>`
  (автообновление само возвращает `app.prev` при сбое). `restore.sh` проверяет `.sha256`, сначала делает свежую
  копию и запускает api/worker, только если ревизия восстановленной базы равна голове образа — иначе пишет, что делать.
- Любая другая копия: `./restore.sh /opt/erp/backups/daily/erp_<дата>.dump` (то же правило запуска).

На PostgreSQL после миграций все внешние ключи переводятся в
`DEFERRABLE INITIALLY DEFERRED` (`backend/migrations/env.py`): проверка при COMMIT, порядок вставок внутри одной
транзакции не важен.

## Проверено 29.09.2026 (Docker в облаке, PostgreSQL 16)
- Все миграции с нуля до головы; сайт и `/api/health` через Caddy по HTTPS.
- Полный сквозной прогон через HTTP API (сотрудники → заявки → закупка → приёмка → склад → техкарты →
  производство → выдача → отгрузка → уведомления): 0 ошибок.
- `load-real-data.sh base/projects/stock` на реальных данных Google: 53 проекта в реестре, 161 запись журнала,
  49 проектов в плане цеха; склад — сверка «всё сошлось».
- Копия базы и восстановление из неё, вход после восстановления.
