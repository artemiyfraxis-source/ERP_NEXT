# Боевой сервер ERP

Один VPS, всё в Docker: PostgreSQL 16, API (он же отдаёт сайт), фоновый воркер outbox, Caddy (HTTPS от
Let's Encrypt), ночные копии базы. Файлы (УПД, счета) лежат в базе, поэтому копия базы — это вся ERP.

## Сервер
Timeweb Cloud (решено агентом 29.09.2026, в claude-brain о сервере владельца данных нет): российский хостинг,
оплата картой РФ, данные сотрудников остаются в РФ, есть зеркало Docker Hub и ежедневные снимки диска.
Ubuntu 24.04, Москва, 2 vCPU / 4 ГБ / 50 ГБ NVMe, опция «Автоматические бэкапы». SSH-ключ — `~\.ssh\erp_server.pub`
с компьютера владельца. Без своего домена адрес будет `<ip-через-дефисы>.sslip.io` (HTTPS работает и так);
свой домен — A-запись на IP и `ERP_DOMAIN` в `/opt/erp/config/.env`, затем `deploy/erp.sh up -d`.

## Раскладка на сервере (`/opt/erp`)
| Путь | Что |
|---|---|
| `app/` | код (снимок `origin/main`, коммит в `app/REVISION`); `app.prev/` — прошлый |
| `config/.env` | `ERP_DOMAIN`, пароль базы, пути к ключам Google (создаёт `bootstrap.sh`) |
| `config/secrets/` | `google-service-account.json` (erp-reader), позже `kd-drive-oauth.json` |
| `config/admin.txt`, `config/staff-pins.txt` | вход администратора и PIN сотрудников (после `load-real-data.sh base`) |
| `supplymap/` | снимок SUPPLYMAP для импорта сотрудников и справочников |
| `import/` | снимки Google для импорта склада и проектов |
| `backups/daily`, `backups/weekly` | `pg_dump` в 03:00 МСК: 14 ежедневных + 8 воскресных |

## Команды
С компьютера владельца (PowerShell, из `Projects\ERP_NEXT`; рабочую копию не трогает, берёт `origin/main`):
```powershell
.\deploy\push.ps1 -Server <IP>                 # первый раз — установка, дальше — обновление с копией базы перед ним
.\deploy\push.ps1 -Server <IP> -Ref <коммит>   # откат кода
```
На сервере (`ssh -i ~\.ssh\erp_server root@<IP>`, затем `cd /opt/erp/app/deploy`):
```bash
./erp.sh ps                         # состояние контейнеров
./erp.sh logs -f --tail 100 api     # журнал API (worker, db, caddy, backup — так же)
./erp.sh backup-now                 # копия базы прямо сейчас
./restore.sh /opt/erp/backups/daily/erp_<дата>.dump   # восстановить (сначала сама делает свежую копию)
./load-real-data.sh base            # admin, сотрудники с PIN, справочники, библиотека техкарт, проекты
./load-real-data.sh projects        # перечитать проекты из Google (повтор ничего не дублирует)
./load-real-data.sh stock           # сверка склада без записи; `stock --yes` — записать (один раз, в день перехода)
./erp.sh cli python -m app.identity_admin create-user --login ... --role admin   # ещё пользователь
```
Миграции накатываются сами при старте API. На PostgreSQL после миграций все внешние ключи переводятся в
`DEFERRABLE INITIALLY DEFERRED` (`backend/migrations/env.py`): проверка при COMMIT, порядок вставок внутри одной
транзакции не важен.

## Проверено 29.09.2026 (Docker в облаке, PostgreSQL 16)
- Все миграции с нуля до головы; сайт и `/api/health` через Caddy по HTTPS.
- Полный сквозной прогон через HTTP API (сотрудники → заявки → закупка → приёмка → склад → техкарты →
  производство → выдача → отгрузка → уведомления): 0 ошибок.
- `load-real-data.sh base/projects/stock` на реальных данных Google: 53 проекта в реестре, 161 запись журнала,
  49 проектов в плане цеха; склад — сверка «всё сошлось».
- Копия базы и восстановление из неё, вход после восстановления.
