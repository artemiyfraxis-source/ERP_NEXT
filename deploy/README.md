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

## Команды
С компьютера владельца (PowerShell, из `Projects\ERP_NEXT`; рабочую копию не трогает, берёт `origin/main`):
```powershell
.\deploy\push.ps1 -Server 192.168.1.85 -GoogleKey <ключ erp-reader.json> -LoadBase   # первый раз: подготовка машины
#   (prepare-host.sh: убрать microk8s, закрепить IP, расширить /, RAID под копии), установка, база; пароли → erp-server-secrets
# Автообновление с GitHub в 8:30 и 18:00 (копия базы, откат и плашка при сбое): один раз
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
