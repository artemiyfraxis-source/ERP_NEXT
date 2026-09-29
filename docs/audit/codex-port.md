# Аудит переноса Codex: ERP_NEXT ← SUPPLYMAP

Дата: 2026-09-28. ERP_NEXT HEAD `51cd50e`. SUPPLYMAP `main` HEAD `5cb0fc8` (2026-09-28).
Только проверка: код, тесты и `SOURCE.json` не менялись.

## Пины модулей

| Модуль | Пин | На `main` SUPPLYMAP? | Отставание от `main` (всего / код) |
|---|---|---|---|
| requests, procurement, identity, techcards (ядро) | `83b223a` (`feat/techmap-web-generator`, 2026-09-14) | **нет** — боковая ветка, 4 коммита вне `main`, merge-base `5e825a5` | 261 / 179 |
| operations, shipping | `a923c9a` (2026-09-21) | да | 163 / 107 |
| receiving, fulfillment, references | `5a039db` (2026-09-23) | да | 53 / 36 |
| warehouse, production, techcards (карточки, библиотека, grapher) | `6a636fc` (2026-09-24) | да | 17 / 8 |

## Методика

1. `supplymap-scout` (haiku) снимает правила модуля с HEAD `main`: статусы и переходы, инварианты,
   проверки, граничные случаи, права ролей — с `file:line` и отметкой, было ли правило на пине.
2. Сверщик (sonnet) сравнивает правила с кодом и тестами модуля ERP, перепроверяет происхождение через
   `git show <пин>:<файл>`.
3. Каждое C1 перепроверено вручную (Opus) по строкам SUPPLYMAP на HEAD и пине и по коду ERP.

**Класс:** пропущено — правила нет в ERP; перенесено неверно — есть, но ведёт себя иначе; придумано — в ERP
есть правило, которого нет в SUPPLYMAP и нет в ARCHITECTURE.md; без теста — правило есть, теста нет;
намеренно — отступление описано в ARCHITECTURE.md или `SOURCE.json`.

**Серьёзность:** C1 — ломает остатки или деньги; C2 — ломает процесс; C3 — косметика.

**Происхождение:** перенос — правило было уже на пине модуля (ошибка переноса); дрейф — появилось или
изменилось в SUPPLYMAP после пина. Для пина на боковой ветке сравнение идёт с деревом самого пина.

## Сквозные находки

| # | Находка | Серьёзность | Комментарий |
|---|---|---|---|
| X1 | Пин `83b223a` у requests, procurement, identity и ядра techcards лежит на боковой ветке `feat/techmap-web-generator`, а не на `main` | C2 | Хук считает отставание как `83b223a..main`, это включает коммиты `main` после merge-base `5e825a5`; 4 коммита ветки в `main` не попали. Сверка «по пину» у этих модулей идёт с состоянием, которого в `main` никогда не было |
| X2 | `npm run sync:*` есть только для `techcards`, `techcard-library`, `legacy-contracts` | C3 | CLAUDE.md велит лечить красные drift-тесты через `sync:*`, но у 8 модулей скрипта нет — пин обновляется только вручную |
| X3 | Drift-тесты архитектуры слепы для 9 из 11 модулей | C2 | `test/architecture.test.mjs` сравнивает `sourceCommit` с `rev-parse sourceRef`. Краснеют только модули с `sourceRef: "main"` (production, библиотека techcards). У requests/procurement/identity/ядра techcards `sourceRef` — замороженная боковая ветка; у shipping/operations `sourceRef` равен самому пину; у receiving/fulfillment/references — `origin/main`, который в локальном SUPPLYMAP стоит на 23.09 и отстаёт от `main` на 54 коммита; для receiving/warehouse проверка — `merge-base --is-ancestor`. Гейт зелёный при любом дрейфе этих модулей; видимость даёт только хук SessionStart |

Во время аудита `main` SUPPLYMAP сдвинулся на `cc59ca3` (правка пути в `CLAUDE.md`, код не менялся), поэтому правила
на `5cb0fc8` и `cc59ca3` совпадают.

## Модули

### requests

Пин `83b223a` (боковая ветка). `Forms.gs`, `Imports.gs`, `Pipeline.gs`, `Guards.gs`, `SystemId.gs`, `Triggers.gs`
на пине и HEAD совпадают; `Config.gs`, `OwnerTick.gs`, `Schema.gs` менялись, но не в правилах заявок. ERP:
`requests/*.py`, миграция `0001`, грань с `procurement`; тесты: `backend/tests/requests/` (5 файлов). C1 нет:
заявка не двигает склад и деньги, а сумма строки перенесена точнее источника.

| # | Правило SUPPLYMAP | ERP | Класс | Серьёзность | Происхождение | Комментарий |
|---|---|---|---|---|---|---|
| RQ-3 | Повторный перенос → новая строка закупки, новый System ID, буква ревизии = max по НАЗНАЧЕНИЮ + 1, старая остаётся (`Pipeline.gs:98-112`, `Schema.gs:134`) | `procurement/domain.py` `create_line_from_requested` всегда `revision="A"`; повторный handoff возвращает прежнее сообщение | пропущено | C2 | перенос | ARCHITECTURE.md обещает «отдельную команду» для новой ревизии — её нет, ревизия B недостижима **Исправлено 2026-09-28 (ветка `cloud/RQ-3-202609281718`).** |
| RQ-4 | Номер проекта: формат `<цифры>К…`, МФ, старая схема `П<гг><мм>-<n>`, нормализация гомоглифов, проверка в реестре (`Schema.gs:256-278`) | `domain.py`: `project_reference` — только `strip()` | пропущено | C2 | формат — перенос, МФ и сложные серии — дрейф | ARCHITECTURE.md обещает проверку через порт Projects — порт не вызывается; любая строка до 128 символов уходит в `procurement.requested` **Исправлено 2026-09-28 (ветка `cloud/RQ-4-202609281734`).** |
| RQ-5 | Импорт позиций из расчёта с обязательными полями и мягким предупреждением о неизвестном проекте (`Imports.gs:52-79`) | эндпоинта нет | пропущено | C2 | перенос | `Imports.gs` указан в `sourceFiles` **Исправлено 2026-09-28, `b19a1b3` (облачная ветка `cloud/RQ-5-202609281833`).** |
| RQ-1 | Общепром → ДОК. КАЧЕСТВА «Нет» по умолчанию (`Schema.gs:596-606`) | `domain.py:161-168` требует явное значение; тест `test_invalid_request_is_rejected` это закрепляет | перенесено неверно | C3 | перенос | Умолчание есть только во фронте (`RequestsPage.tsx:42-43`) |
| RQ-2 | Ошибка с номером позиции «Позиция N: не заполнены …» (`Forms.gs:66-67`) | `domain.py:206-223` без номера | перенесено неверно | C3 | перенос | В пачке до 100 строк не видно, какая не прошла |
| RQ-7 | У заявки только «Новый» и «В работе» (`Config.gs:13-14`) | `domain.py:51-57` `RequestStatus` ещё и APPROVAL / IN_TRANSIT / CLOSED / CANCELLED без переходов | придумано | C3 | — | Похоже на копию словаря закупки |
| RQ-6 | Ручная правка листа и колонки статуса откатывается (`Guards.gs:705-726`) | вся запись через API | намеренно | C3 | перенос | Механизм листа |
| RQ-8 | Перенос в закупку может нажать любой с правом правки листа (`Pipeline.gs:12-125`) | handoff требует buyer/admin | намеренно | — | перенос | Описано в ARCHITECTURE.md |

**Перенесено хорошо:**
- «Новый» сразу, команда → «В работе», закупка создаёт «Новый» рев. A — `test_create_batch_preserves_legacy_rules_without_using_legacy_id_as_pk`, `test_handoff_is_idempotent_and_writes_procurement_audit_and_outbox_atomically`.
- Один номер назначения на базу в пачке, `BASE.NNN` — `test_batch_uses_one_designation_per_purpose_and_separate_numbers_for_other_purposes`.
- Обязательные поля и справочники дословно; System ID `SM-ГГГГММДД-NN` только для сверки, UUID — ключ — `test_invalid_request_is_rejected`, `test_schema_exposes_supplymap_required_fields_and_fixed_purposes`.
- ОП → «Да» заблокировано, ИП → «Да» — `test_category_rules_are_applied_server_side`; пороги приоритета — `test_priority_boundaries`.
- СУММА = ЦЕНА С НДС × КОЛ-ВО до копеек на `Decimal` + `ROUND_HALF_UP` (точнее `Math.round` на float в источнике).
- Идемпотентность `creation_key` в пределах инициатора; инициатор и отдел только с сервера — `test_create_rejects_reusing_idempotency_key_for_different_payload`, `test_creation_idempotency_key_is_scoped_to_authenticated_identity`, `test_create_uses_server_actor_and_is_idempotent`.

**Вне заявленного объёма:** нет.

### procurement

Пин `83b223a` (боковая ветка). `Pipeline.gs`, `SupplierRequest.gs`, `Kp.gs`, `Invoices.gs`, `Currency.gs` на пине и
HEAD совпадают; в `Portal.gs` после пина появилась сводка по поставщикам. ERP: `procurement/*.py`, миграции `0002`,
`0004`; тесты: `backend/tests/procurement/` (4 файла). ARCHITECTURE.md называет реализованное «первым контуром
закупки», но `sourceFiles` в SOURCE.json включает `Kp.gs` и `Invoices.gs`, а `module-map.json` — ещё и `Currency.gs`.

| # | Правило SUPPLYMAP | ERP | Класс | Серьёзность | Происхождение | Перепроверка Opus | Комментарий |
|---|---|---|---|---|---|---|---|
| PR-4 | В «В оплату» строка уходит только при 11 заполненных полях: ВАЛЮТА, ЦЕНА (факт), ЦЕНА С НДС (факт), КОЛ-ВО (факт), ЕД.ИЗМ (факт), СУММА (факт), № / ДАТА / ДОК. КАЧЕСТВА, НАИМЕНОВАНИЕ (Счет), ОЖИД. ДАТА; «—» = заполнено (`Config.gs:243-255`, `Invoices.gs:66-139`) | `domain.py:155-178` `record_invoice` требует только URL счёта и ожидаемую дату | пропущено | **C1** | перенос | **подтверждено** | Строка проходит «В оплату» → «В пути» без единой суммы. Полей фактической цены, НДС, суммы, валюты в `ProcurementLine` нет — есть только расчётные `calculated_*`, скопированные из заявки (`domain.py:79-81, 298-300`) **Исправлено 2026-09-28 (`53c1cfd`).** |
| PR-6 | Денежный блок: тройка цена / цена с НДС / сумма с правилом ведущего поля, СУММА = ЦЕНА С НДС × КОЛ-ВО, округление до копеек, инвалюта × курс ЦБ на дату счёта × (1 + наценка), ВАЛЮТА → RUB только при пересчёте (`Currency.gs:1-118`, `Kp.gs:142-181`) | нет ни полей, ни функций | пропущено | **C1** | перенос | **подтверждено** (вместе с PR-4) | Фактическая стоимость закупки в ERP не записывается вовсе **Исправлено 2026-09-28 (`53c1cfd`).** |
| PR-2 | Повторный перенос — новая ревизия (см. RQ-3) | ревизия всегда «A» | пропущено | C2 | перенос | — | — **Исправлено 2026-09-29 вместе с RQ-3 (ветка `rescue/procurement`).** |
| PR-1 | Offline-статусы «Согласование» / «Отменено» в любой момент (`Config.gs:58-61`) | нет | пропущено | C2 | перенос | — | Строку закупки нельзя отменить или поставить на согласование **Исправлено 2026-09-28, `c74101a` (облачная ветка `cloud/PR-1-202609281934`).** |
| PR-3 | Запрос поставщику: группировка поставщик + тип, № ЗАКАЗА `prefix-NNN` (prefix по ТИП ЗАДАЧИ, по умолчанию PO), ДАТА ЗАКАЗА (`SupplierRequest.gs:5-276`) | `domain.py:139-153` пишет присланный текст `reference` | пропущено | C2 | перенос | — | Гейт «Готов к запросу» → «Жду счет» перенесён **Исправлено 2026-09-28, `d30259b` (облачная ветка `cloud/PR-3-202609282033`).** |
| PR-5 | Замена счёта без сброса ОПЛАТЫ; № и дата счёта из файла (`Invoices.gs:34-128, 312-330`) | счёт принимается один раз, только из «Жду счет» | пропущено | C2 | перенос | — | Заменить ошибочный счёт нельзя **Исправлено 2026-09-29, `3435785` (облачная ветка `cloud/PR-5-202609282232`).** |
| PR-7 | КП: импорт файла, форма только для «Жду счет» (`Kp.gs:29-243`) | нет | пропущено | C2 | перенос | — | **Исправлено 2026-09-28 (ветка `cloud/PR-7-202609282333`).** |
| PR-8 | Открытая ЗАМЕНА без «Разрешено» блокирует запрос поставщику (`SupplierRequest.gs:235-239`) | понятия замены нет | пропущено | C2 | перенос | — | Три других блокера (поставщик, точка, факт. кол-во и ед.) входят в гейт готовности **Исправлено 2026-09-29 (☁ `cloud/PR-8-202609290033`, ветка `rescue/procurement`).** |
| PR-9 | «Раздробить» строку на 1–30 копий с новыми System ID (`Pipeline.gs:127-220`) | нет | пропущено | C2 | перенос | — | — **Исправлено 2026-09-29 (`61659c8`, ☁ `cloud/PR-9-202609290132`).** |
| PR-10 | Формулы недостачи, недовоза, излишка (`Currency.gs:65-72`) | `receiving/domain.py:36-37, 111, 136` | — | — | перенос | **отклонено** | Сверщик не нашёл их в procurement; они перенесены в receiving (`SHORT_FACT`, `SHORT_ORDER`, `backorder_quantity`) |

**Перенесено хорошо:**
- Consumer идемпотентен; «Новый» + рев. A назначает сама закупка; audit и outbox в одной транзакции; битые сообщения — backoff и dead-letter после 5 попыток — `test_requested_event_creates_procurement_owned_defaults`, `test_requested_event_is_consumed_once_and_audited_atomically`, `test_poison_outbox_is_dead_lettered_after_five_attempts`.
- Взять в работу только из «Новый», повтор тем же — без записи, другой закупщик — отказ, одинаковое ФИО не даёт владения — `test_buyer_takes_new_line_in_work_and_retry_is_idempotent`, `test_other_buyer_cannot_capture_line_already_in_work`, `test_same_display_name_does_not_grant_ownership_to_another_identity`.
- Гейт готовности (поставщик, точка отгрузки, факт. кол-во > 0, ед.) → «Готов к запросу» — `test_complete_facts_advance_line_to_ready`, `test_non_positive_actual_quantity_is_rejected`.
- Последовательность запрос → счёт → оплата → «В пути» и ровно одно `receiving.expected` — `test_buyer_advances_ready_line_through_invoice_to_transit`, `test_paid_line_emits_receiving_snapshot_once`.
- Доступ buyer/admin, правка только своих строк, `version` против гонок — `test_procurement_routes_require_authenticated_buyer`, `test_other_buyer_cannot_edit_claimed_line`, `test_take_batch_validates_every_line_before_writing`.

**Вне заявленного объёма:**

| # | Правило | Серьёзность | Происхождение | Комментарий |
|---|---|---|---|---|
| PR-11 | Сводка активных строк по поставщику для закупщика (`Portal.gs:1721-1758`) | C3 | дрейф (22.09) | — |
| PR-12 | Поставщик по ССЫЛКЕ через `SUPPLIER_BY_LINK` и пересчёт ПРИОРИТЕТА при переносе (`Pipeline.gs:78-92`) | C3 | перенос | Нет ни в requests, ни в procurement; справочник поставщиков в references есть, но не применяется |

### production — исполнение

Пин `6a636fc`. `ProdTasks.gs` и `ProdWork.gs` на пине и HEAD совпадают байт в байт; после пина менялся только
`TcKitset.gs` (d88150d). ERP: `production/{domain,application,execution,traceability,quality,genealogy,infrastructure,api}.py`;
тесты: 10 файлов в `backend/tests/production/`.

| # | Правило SUPPLYMAP | ERP | Класс | Серьёзность | Происхождение | Перепроверка Opus | Комментарий |
|---|---|---|---|---|---|---|---|
| PX-1 | Исправление возвращённой коробки: новая запись Выработки с `ИСПРАВЛЯЕТ ID`, количество коробки **заменяется**, дельта добирается из тех же входных коробок или возвращается в них, исправленная запись выпадает из сумм (`ProdWork.gs:345-391`, `prodWorkFacts_` `:93-101`, `prodPick_`/`prodGiveBack_` `:66-90`) | `execution.py:140` (`correction_of_id` принимается из API и сохраняется, но нигде не читается); `traceability.py:223-228` (`reopen`) → `submit_work` → `add_output` (`traceability.py:128-135`) только прибавляет; `infrastructure.py:769-773` `total_for_operation` суммирует все выработки, включая исправленные | пропущено | **C1** | перенос | **подтверждено** | Уменьшить количество возвращённой коробки нельзя вообще; «исправление» лишь докладывает сверху новую работу с новым входом. Завышенное количество уходит дальше по цепочке и в `FinishedGood.accepted`. Лимит «не больше задания» для первой операции считает и исправленные записи. Тест `test_returned_box_keeps_number_when_operator_reopens_and_closes_again` проверяет только номер **Исправлено 2026-09-28 (`369e70d`).** |
| PX-2 | Контрольная цифра — алгоритм Луна; номер = `100000 + seq`; ввод терпит пробелы, длинное тире и отсутствие дефиса (`ProdWork.gs:30-52`) | `traceability.py:15-23`: веса 3/1 (схема EAN), номер `f"{seq:06d}"` с нуля, regex `^(\d{6})-(\d)$` | перенесено неверно | C2 | перенос | — | Для `104233` SUPPLYMAP даёт `2`, ERP — `7`; тест `test_box_number_uses_six_digits_and_control_digit` закрепляет `7`. Номера, выданные SUPPLYMAP на время параллельной работы, ERP отвергнет. Схема 3/1 не ловит перестановку соседних цифр, отличающихся на 5 **Исправлено 2026-09-28 (`0588fba`):** Луна, терпимый ручной ввод (и в поиске родословной). База SUPPLYMAP `100000 + seq` намеренно не перенесена (решение владельца): ERP выдаёт 000001–099999, серии двух систем не пересекаются. |
| PX-3 | Нет ни одной строки компетенций по коду операции → допуск не проверяется (`ProdTasks.gs:181-192`) | `scheduling.py:83-89, 217-225`: `_skill()` → `None` трактуется как «не допущен», автоплан падает «нет допущенных операторов» | перенесено неверно | C2 | перенос | — | Новая операция без строк в матрице блокирует автоплан целиком. Теста на пустую матрицу по коду нет **Исправлено 2026-09-29 (ветка `cloud/PX-3-202609290232`).** |
| PX-4 | Ручное назначение тоже применяет коэффициент скорости и предупреждает об отсутствии допуска (`ProdTasks.gs:283-292`, общая `prodPlan_`) | `application.py:282-322` `assign_production` → `plan_operation` без коэффициента | намеренно | C3 | перенос | — | SOURCE.json сужает инвариант до автоплана (`competency-matrix-controls-auto-schedule-…`) |
| PX-5 | Статус операции «не начата / в работе / выполнена» считается из Выработки (`ProdWork.gs:104-108`) | не выводится ни в API, ни в `ProductionPage.tsx` | пропущено | C3 | перенос | — | Инвариант «статус не хранится» соблюдён, но производная метка не показана |
| PX-6 | Одна сдача = одна коробка сразу «Закрыта оператором», статуса «Открыта» нет (шапка `ProdWork.gs`) | `traceability.py:38-44` `BoxStatus.OPEN`, несколько сдач копятся до `close_box` | намеренно | C3 | перенос | — | Описано в ARCHITECTURE.md («Открыта» → «Закрыта оператором» → …) |

**Перенесено хорошо:**
- Снимок маршрута при запуске, несколько операторов, статус операции не хранится — `test_launch_keeps_sorted_route_snapshot_without_operation_status`, `test_multiple_operators_share_unit_work_but_each_has_setup_time`, `test_launch_is_idempotent_and_route_snapshot_is_not_rebuilt`.
- Отмена задания сохраняет маршрут и назначения — `test_cancel_preserves_operations_and_restore_only_changes_task_status`.
- Резерв входа при старте → расход при сдаче → освобождение при отмене (закрывает гонку, которую SUPPLYMAP ловит повторной проверкой входа) — `test_execution_converts_reservation_into_append_only_output_and_consumption`, `test_cancelling_current_work_releases_input_reservation`.
- Вход — только подтверждённая коробка того же изделия с предыдущего этапа — `test_confirmed_box_is_reserved_then_consumed_without_changing_number`.
- По брифу: первая операция берёт ГРН+пакет из выдачи; сдача последней коробки в ГП одной транзакцией и один раз; серийники при запуске; ОТК отдельной ролью; просроченное СИ запрещено; решения по браку append-only; родословная — `test_first_operation_consumes_issued_grn_and_package_into_output_box`, `test_last_confirmed_box_is_handed_to_matching_finished_good_grn_once`, `test_qc_assigns_serials_and_isolates_rejected_units_with_checked_number`, `test_qc_rejects_expired_measurement_instrument`, `test_master_decides_qc_rejection_once_without_rewriting_inspection`, `test_search_from_serial_returns_material_boxes_and_finished_goods_grn`.
- Календарь смены, перерывы, бюджет часов, первое общее свободное окно — `test_shift_uses_breaks_and_effective_hour_budget`, `test_auto_plan_finds_first_common_free_window_and_manual_plan_warns`.

**Вне заявленного объёма:** `kitUseArt_` в `TcKitset.gs:675-682` (дрейф d88150d) относится к techcards/комплектации — см. раздел techcards.

### receiving

Пин `5a039db`. `Receiving.gs`, `SupplierReturn.gs`, `DocGen.gs`, `Config.gs`, `Schema.gs` на пине и HEAD совпадают
(`git diff --stat 5a039db main` пуст) — всё ниже «перенос». ERP: `receiving/{domain,application,api,infrastructure}.py`,
миграция `0004`; тесты: `backend/tests/receiving/` (4 файла).

| # | Правило SUPPLYMAP | ERP | Класс | Серьёзность | Происхождение | Перепроверка Opus | Комментарий |
|---|---|---|---|---|---|---|---|
| RC-2 | Недовоз (УПД < заказ) ставит задание дозавоза; из него создаётся строка Закупки `-Д` в «Согласование» — копия исходной, «досыл уже оплачен — цена/ОПЛАТА/счёт сохраняются»; после ОЖИД. ДАТА → «В пути» → приёмка; отмена `-Д` → новая заявка (`Receiving.gs:80-224`) | `domain.py:136` считает `backorder_quantity`, `infrastructure.py:56` хранит, `api.py:93` отдаёт — больше его не читает никто | пропущено | **C1** | перенос | **подтверждено** | Оплаченный, но не поставленный остаток выпадает из учёта ожидаемых поставок: нет ни события в Procurement, ни ожидаемой строки в Receiving. Закрыть его можно только вручную вне ERP **Исправлено 2026-09-28 (`7a56e92`).** |
| RC-3 | «Ожидание» без решения держится ≤ 2 рабочих дней, затем автоматически «Карантин» + письмо технологу/снабжению (`Config.gs:87,227`, `OwnerTick.gs:515-545`) | нет аналога; `domain.py:118-120` выбирает Ожидание/Карантин только в момент ВК | пропущено | C2 | перенос | — | Строка без решения висит в «Ожидании» бессрочно; таймер утилизации (RC-11) не стартует **Исправлено 2026-09-29, `e7325c1` (облачная ветка `cloud/RC-3-202609290333`); письмо — за OP-1/RC-10.** |
| RC-1 | Принимаемое количество = факт, иначе УПД (`Receiving.gs:52-57`, подсказка `:301-306`) | `api.py:31,36` `Field(gt=0)` для факта, `domain.py:90,135` | перенесено неверно | C3 | перенос | — | Принять «по УПД без пересчёта» нельзя — факт обязателен |

**Перенесено хорошо:**
- Порядок пунктов ВК и вердикт (терминальный / условно годен / годен), «Ожидание» vs «Карантин» по наличию решения, акт НС только на реальные дефекты — `test_wrong_item_or_expiry_is_terminal_return`, `test_documents_wait_and_confirmed_defect_goes_to_quarantine`.
- Излишек в чистом виде принимается по факту — `test_clean_delivery_and_surplus_are_accepted_by_fact`.
- Недостача/недовоз, `backorder_quantity = заказ − УПД`, рекламация при количественном дефекте — `test_shortage_creates_backorder_and_supplier_complaint`.
- «Возврат» терминален — `test_returned_line_cannot_be_reinspected`.
- Префикс ГРН по назначению, счётчик `<prefix>.<YY>.<7 цифр>` на префикс и год, ручной выбор только CSM/PPM — `test_grn_prefix_matches_supplymap_rules`, `test_next_grn_uses_per_prefix_year_sequence_and_ignores_legacy_format`, `test_unknown_purpose_requires_narrow_manual_grn_prefix`.
- ГРН один раз на строку, склад только после «Принято + ГРН + место», одно событие `warehouse.received` — `test_expected_line_is_inspected_numbered_and_placed_before_warehouse`, `test_inspection_and_placement_emit_one_atomic_warehouse_event`.
- Серия изготовителя отдельно от ГРН — `test_manufacturer_series_is_recorded_during_inspection`; идемпотентный и устойчивый к битым событиям consumer — `test_expected_event_retry_is_read_only`, `test_invalid_decimal_event_is_quarantined_instead_of_blocking_worker`.

**Вне заявленного объёма** (`portedInvariants` их не называют):

| # | Правило | Серьёзность | Происхождение | Перепроверка Opus | Комментарий |
|---|---|---|---|---|---|
| RC-6 | Возврат поставщику уже принятой позиции: списание остатка по ГРН+ПАКЕТ, статус «Возврат» или «В пути» (ждём замену) (`SupplierReturn.gs:16-100`) | C2 | перенос | понижено с C1 | Файл указан в `sourceFiles` и `module-map.json`, но функции нет: `domain.py:229-232` не пускает принятую строку обратно. Остаток можно поправить складским списанием с причиной (`warehouse/api.py:218`), поэтому остатки не ломаются; теряется связь со строкой приёмки, претензией и ожиданием оплаченной замены |
| RC-7 | Новая ревизия ВК `-ВN`, исходник → «Архив ВК» (`Receiving.gs:479-551`) | C2 | перенос | — | Статуса «Архив ВК» нет; история прежних актов не сохраняется **Исправлено 2026-09-29 (`9ea863b`, ☁ `cloud/RC-7-202609290434`).** |
| RC-8 | Разукомплектовка на пакеты `-ПN` до раскладки, сумма = КОЛ-ВО ФАКТ (`Receiving.gs:553-613`) | C2 | перенос | — | `place()` пишет одно место на строку |
| RC-13 | Разбивка приёмки на ≥ 2 партии, каждой свой ГРН и акты (`Receiving.gs:450-473`) | C2 | перенос | — | Одна партия на `receiving.expected` |
| RC-9 | Акты ВК / НС / рекламации / разрешения (`DocGen.gs`, `Receiving.gs:757-760`) | C2 | перенос | — | Документы входного контроля не формируются |
| RC-12 | Историческая приёмка: опорная дата вердикта — дата УПД (`Receiving.gs:685-704`) | C2 | перенос | — | `api.py:148` берёт `now.date()`: ввод задним числом ложно отправит годный товар в терминальный «Возврат» (поднято с C3 — терминальный статус необратим) **Исправлено 2026-09-29 (`97d708d`, ☁ `cloud/RC-12-202609290633`).** |
| RC-10 | Уведомления о несоответствии: механический брак → технологу, остальное → снабжению (`Receiving.gs:771-788`) | C3 | перенос | — | — **Исправлено 29.09 (e746496, прогон 20260929-1050-manual).** |
| RC-11 | Карантин ≥ 90 дней → письмо об утилизации (`OwnerTick.gs:560-596`) | C3 | перенос | — | — |
| RC-14 | Масса 1 шт в граммах (`Receiving.gs:440-441`) | C3 | перенос | — | — |

### warehouse

Пин `6a636fc`. `Warehouse.gs`, `Issue.gs`, `Schema.gs`, `Config.gs` на пине и HEAD совпадают — количественные правила
сверены по пину. `Stock05.gs` и `TcHousing.gs` менялись после пина (39a5a97, d88150d). ERP: `warehouse/*.py`,
`shared/warehouse_location.py`, `workflows/warehouse_article.py`, миграции `0005`, `0022`; тесты:
`backend/tests/warehouse/` (5 файлов), `WarehousePage.test.tsx`. C1 нет.

| # | Правило SUPPLYMAP | ERP | Класс | Серьёзность | Происхождение | Перепроверка Opus | Комментарий |
|---|---|---|---|---|---|---|---|
| WH-1 | Возврат списания — строка журнала СПИСАНИЕ с основанием «Возврат …» для любой партии (`Warehouse.gs:252-286, 340, 395-398`) | `domain.py:146` `return_writeoff` вызывается только из `fulfillment/application.py:325`; в `warehouse/api.py` эндпоинта нет | пропущено | C2 | перенос | — | Ошибочное списание кладовщик сам не развернёт **Исправлено 2026-09-28, `90f6774` (SK-3).** |
| WH-2 | Списание проверяет только `ПРИХОД − СПИСАНО`, резерв не учитывается (`Warehouse.gs:276-288, 579-592`) | `domain.py:125-131`: списание не может затронуть резерв | намеренно | C3 | перенос | понижено с C1 | ERP строже SUPPLYMAP, и это описано в ARCHITECTURE.md. Но `SOURCE.json` называет правило «ported» — в источнике его нет. Практически: чтобы списать испорченную зарезервированную партию, сначала надо снять привязку в комплектации |
| WH-3 | `STOCK05_SERIES_RULES_` (Molex, JST XH/SM/PHD, Connfly, M12, GX16, QS8), коды коаксиала S-211F…, все RG через «/», .086" → RG-405, ISMA / 2.92mm / 3.5mm (`Stock05.gs:497-684`) | `workflows/warehouse_article.py` — ни одного из правил | пропущено | C3 — **исправлено 2026-09-29, `58026dc` (SK-11)** | дрейф (39a5a97) | — | Только подсказка пустых полей |
| WH-4 | DS1069 — аналог JST XH, не SM; `HOUSING_ALIAS_RULES_` (`TcHousing.gs:84-87, 139-142`) | `connector_housings.py:48` — «аналог JST SM», алиасов нет | пропущено | C3 — **исправлено 2026-09-29, `e3a12d2` (WH-4)** | дрейф (d88150d) | — | Подсказка и рисунок |
| WH-5 | Хвост «@ПОСТАВЩИК» отрезается при опознании любого артикула (`Stock05.gs:550, 561-585`) | отрезается только в ветке коаксиала (`warehouse_article.py:136`) | пропущено | C3 — **исправлено 2026-09-29, `58026dc` (SK-11)** | дрейф (39a5a97) | — | «0039012025@MOLEX» не опознаётся |

**Перенесено хорошо:**
- `баланс = приход − списано`, резерв отдельно; резерв не больше доступного; списание не больше баланса; возврат не больше списанного — `test_balance_is_inbound_minus_writeoff_and_reserve_is_separate`, `test_cannot_overreserve_or_write_off_more_than_balance`, `test_return_cannot_exceed_previously_written_off_quantity`.
- Выдача резерва одним действием уменьшает резерв и баланс — `test_reserved_issue_reduces_reserve_and_physical_balance_atomically`.
- Адрес «стеллаж + полка + номер + ячейка», перенос места с сохранением ячеек и пропуском нулевых — `test_storage_location_uses_rack_shelf_number_code`, `test_whole_location_move_preserves_slots_and_skips_empty_lots`.
- Артикул — идентичность партии; повторный приход суммирует и не затирает артикул; миграция name → article без изменения количеств — `test_article_identifies_stock_while_name_is_optional_legacy_description`, `test_legacy_name_becomes_article_and_repeated_receipt_updates_series`, `test_article_migration_moves_legacy_name_and_downgrade_restores_identity`.
- Правки партии требуют версию — `test_lot_can_move_and_writeoff_but_stale_update_is_rejected`; ручной ввод требует артикул или наименование (АРТИКУЛ = наименование), пропускает плохие строки с `errors[{i, error}]` и даёт ГРН `OLD.YY.NNNNNNN` (**SK-10, 2026-09-29**, `test_quick_entry.py`) — `test_manual_stock_input_gets_old_grns_and_one_audit_event_per_lot`.
- Подсказки по артикулу заполняют только пустые поля — `test_article_identity_uses_terminal_series_and_connector_family`.

**Вне заявленного объёма:** `unpackSumOk_` (`Schema.gs:551`) — зона receiving, см. RC-8.

### shipping

Пин `a923c9a`. `Shipment.gs` и ADR 0016 с пина не менялись. ERP: `shipping/*.py`, миграция `0009`; тесты:
`test_domain.py`, `test_api.py` (139 строк).

| # | Правило SUPPLYMAP | ERP | Класс | Серьёзность | Происхождение | Перепроверка Opus | Комментарий |
|---|---|---|---|---|---|---|---|
| SH-5 | Снятие набора и отмена заявки возвращают набранное на склад ГП (`Shipment.gs:787, 1201-1205`) | `domain.py:200-216, 265-274`, `application.py:217-228, 280-292` | без теста | C2 | перенос | понижено с C1 | Код верен: `put_back` симметричен `take`, заявка и регистр ГП пишутся одной транзакцией с проверкой версий. Но это единственный путь, уменьшающий «отгружено» в регистре ГП, и ни один тест его не вызывает. **Исправлено (☁ `cloud/SH-5-202609290532`, влита прогоном 20260929-0903): тесты добавлены.** |
| SH-1 | Создание заявки менеджером сразу делает набор и документы; `noDocs` → «Собрана» (`Shipment.gs:717-735`) | `application.py:173-194` создаёт только «Новая» | пропущено | C2 | перенос | — | Кладовщик вручную жмёт набор и упаковку на каждую заявку **Исправлено 29.09 (1d4ebc2, ☁ `cloud/SH-1-202609290733`).** |
| SH-2 | Очередь ГП встаёт по КБ при заведении проекта, идемпотентно; снос проекта без движения удаляет строку (`Shipment.gs:577-618`, ADR 0016 §1, §9) | `application.py:147-159` `register_good` — только ручная регистрация | пропущено | C2 | перенос | — | ADR 0016 указан источником в SOURCE.json **Исправлено 29.09 (33f065a, ☁ `cloud/SH-2-202609290833`).** |
| SH-3 | Получатель, точка, контакт наследуются из карточки проекта; своё непустое сильнее; пустое не стирает (`Shipment.gs:160-170, 318, 564-573`) | `domain.py` `Shipment.create()` требует всё в каждой заявке | пропущено | C2 | перенос | — | Снимок адреса в заявке есть, умолчаний проекта нет **Исправлено 29.09 (9f15a15, ☁ `cloud/SH-3-202609290933`).** |
| SH-4 | Правка ЗАКАЗАНО после создания, не ниже набранного (`Shipment.gs:808-816`) | нет | пропущено | C3 | перенос | — | — |

**Перенесено хорошо:**
- Статусы, условия доставки, тип УПД совпадают со списками `Config.gs`; гейты «Собрана / Упакована / Отгружена / Закрыта» с текстами недостающих полей — `test_finished_good_partial_shipment_and_close_gates`, `test_carrier_close_requires_upd_file_number_date_and_track`, `test_paper_shipment_requires_upd_file_before_dispatch`.
- Остаток ГП `max(принято − отгружено, 0)`, отбор `min(недобор, остаток)`, заказано и набрано раздельно — `test_partial_pick_keeps_requested_and_picked_separate`.
- Повторная упаковка запрещена (строже источника) — `test_packed_shipment_cannot_be_repacked_with_new_document_numbers`.
- Номер `ОТГ-ГГГГММДД-NN`; ГРН ГП выдаётся один раз на первую сдачу; цех может сдать больше тиража — `shipping/application.py:165`, `production/execution.py:442`.
- Менеджер заводит, кладовщик исполняет — `test_manager_cannot_execute_warehouse_step`.

**Вне заявленного объёма:** короба, вес, брутто (`Shipment.gs:205-242`) — C3, данные генератора PL/DN, который ARCHITECTURE.md выносит за границу модуля.

### fulfillment

Пин `5a039db`. `Issue.gs` и `Config.gs` на пине и HEAD совпадают — дрейфа нет. ERP: `fulfillment/{domain,application,api,infrastructure}.py`,
миграции `0006`, `0007`; тесты: `backend/tests/fulfillment/` (4 файла). C1 нет: резерв, доступное, списание и сторно
защищены доменом склада (`warehouse/domain.py` reserve / release_reserve / issue_reserved / return_writeoff).

| # | Правило SUPPLYMAP | ERP | Класс | Серьёзность | Происхождение | Комментарий |
|---|---|---|---|---|---|---|
| FF-2 | «Готов к выдаче» гейтится разделом `issues`, не `handout` (`Issue.gs:882`: в `give` только «КБ Выдана», «Готово», «КБ Возвращена») | `api.py:266-280` `ready()` требует `fulfillment_handout_principal` (receiver/admin) | перенесено неверно | C2 | перенос | Инженер с `issues` не может перевести собранный комплект в «Готов к выдаче» |
| FF-3 | Первая привязка всегда переводит «Новая» → «Недоукомплектован» (`Issue.gs:978-980`) | `domain.py:244-249`: сразу `COMPLETE`, если одна строка закрыта целиком | перенесено неверно | C2 | перенос | Однострочная заявка минует «Недоукомплектован»; зависимые отметки (дата В РАБОТУ) не ставятся |
| FF-1 | Заявка без дедлайна отклоняется: «Укажи дедлайн.» (`Issue.gs:121, 919`) | `api.py:60` `deadline: date \| None`, домен не проверяет | пропущено | C3 | перенос | В `FulfillmentPage.tsx` поля дедлайна нет |
| FF-4 | Даты В РАБОТУ / НАБРАНА / ВЫДАНА / ВОЗВРАЩЕНА / ЗАКРЫТА по первому попаданию в статус (`Issue.gs:431-493`) | колонок нет; частично в audit (`application.py:158-176`) | пропущено | C3 | перенос | Наружу даты переходов не выводятся |
| FF-5 | Удаление заявки только реальным админом и только без выданных позиций (`Issue.gs:1067`) | эндпоинта нет | пропущено | C3 | перенос | Частично заменено отменой |
| FF-6 | Слияние свободных строк одного артикула из разных позиций состава (`Issue.gs:1019-1041`) | `domain.py:263-292` сливает только внутри одного `requirement_id` | намеренно | C3 | перенос | ARCHITECTURE.md описывает слияние только в пределах `requirement_id` |
| FF-7 | Отказ привязки при доступном ≤ 0 и при уже выданной позиции (`Issue.gs:1101, 1135`) | `application.py:223-225`, `domain.py:99-100` | без теста | C3 | перенос | Код верен, ветки тестами не покрыты |

**Перенесено хорошо:**
- Статусный конвейер и выдача комплекта целиком одной транзакцией — `test_complete_kit_requires_handout_place_and_is_issued_whole`, `test_receiver_binds_a_human_selected_lot_and_issues_only_the_complete_kit`, `test_human_lot_binding_and_whole_kit_issue_are_one_stock_transaction`.
- Потребность и резерв раздельно, заявка без остатка создаётся, пустой состав — нет — `test_request_exists_without_stock_but_empty_bom_is_skipped`.
- Частичная привязка дробит строку, снятие сливает обратно — `test_human_binding_partially_reserves_and_splits_uncovered_need`, `test_unbinding_merges_rows_created_by_partial_lot`.
- Возврат: три части ≤ выдано, израсходовано — остаток; годное сторнирует списание и может сменить адрес — `test_return_records_good_defects_and_consumed_without_exceeding_issued`.
- Отмена освобождает резерв, выданную отменить нельзя; гейты МЕСТО ВЫДАЧИ / МЕСТО ВОЗВРАТА; раздел `handout` проверяется на сервере — `test_cancel_retry_is_read_only`, `test_incomplete_or_issued_request_cannot_bypass_gates`, `test_handout_requires_receiver_role`.
- Партию выбирает человек (`lot_id`), FIFO нет — как в SUPPLYMAP.

**Вне заявленного объёма:** нет.

### production — планирование

Пин `6a636fc`. `ProdPlan.gs` и `ProdSchedule.gs` с пина не менялись; единственный коммит после пина в этой зоне —
d88150d в `TcKitset.gs`, ручные отметки КБ (`portalKitMark`, `mark.stage`, `mark.review`) он не трогает. ERP:
`production/{plan,plan_api,plan_application,scheduling}.py`, `workflows/production_plan.py`; тесты: `test_plan`,
`test_plan_migration`, `test_scheduling`, `test_domain`. C1 нет: модуль — read model и планировщик времени.

| # | Правило SUPPLYMAP | ERP | Класс | Серьёзность | Происхождение | Комментарий |
|---|---|---|---|---|---|---|
| PP-1 | В план попадает только проект из реестра «Проекты», у которого есть карточка с составом; «голая строка реестра» — нет (`ProdPlan.gs:203-205`) | `production_plan.py:177`: `project_snapshots.keys() \| cards_by_project.keys()` — объединение вместо пересечения | перенесено неверно | C2 | перенос | В план попадают проекты без карточки и карточки без строки реестра. Теста нет |
| PP-2 | Допуск не проверяется, если по коду операции в матрице нет ни одной строки (`ProdTasks.gs:186-189`) | `scheduling.py:217-225` | перенесено неверно | C2 | перенос | То же, что PX-3: новый код операции блокирует автоплан |
| PP-6 | `replan=false`: назначенные этапы сразу идут в занятость, расчёт заполняет только пустые (`ProdSchedule.gs:87-94`) | `scheduling.py:178-189` — код верен | без теста | C2 | перенос | Заявленный инвариант `auto-schedule-keeps-fixed-assignments-unless-replan-is-requested` ни одним тестом не исполняется |
| PP-3 | ETA снабжения: ОЖИД. либо ПЛАН строки Закупки, и только если пусто — из заявки (`ProdPlan.gs:163`) | `production_plan.py:73-89` не подставляет ПЛАН закупки | перенесено неверно | C3 | перенос | — |
| PP-4 | Предупреждение о ручном назначении раньше готовности деталей с датой «можно с …» (`ProdSchedule.gs:103-104`) | `scheduling.py:211-215` без дат | перенесено неверно | C3 | перенос | — |
| PP-5 | Ошибка одного источника не валит план, колонка пустая (`ProdPlan.gs:174`) | `plan_api.py:92-96` ловит только «модуль не подключён» | перенесено неверно | C3 | перенос | Исключение в подключённом модуле роняет весь `/api/production/plan` |

**Перенесено хорошо:**
- Журнал: виды записей, блокеры, append-only, тексты ошибок — `test_journal_keeps_append_only_history_and_current_blocker`, `test_deadline_and_note_validation_match_current_supplymap`.
- Ручной этап КБ и «авто», проверка ТК и её инвалидация новой ревизией — `test_manual_kit_stage_and_latest_techcard_review_override_facts`, `test_newer_techcard_invalidates_aware_review_when_card_time_is_naive`.
- Корзины снабжения stock / transit / buying / request — `test_supply_buckets_and_summary_match_current_supplymap`.
- Ручные техкарты 714К.1 / 35К.М.34 (`KIT_MANUAL_TC_` есть в GAS на пине) — `test_manual_techcard_project_starts_at_ready_without_fake_card_rows`.
- Проект из НАЗНАЧЕНИЯ, нормализация номера с гомоглифами — `test_project_from_purpose_matches_current_supplymap_examples`.
- Передаточная партия, варианты 1/5/10/25/100 %, Парето, «целиком» всегда — `test_schedule_variants_keep_whole_batch_and_pareto_options`; коэффициент делит всё время этапа — `test_auto_schedule_uses_skills_coefficients_and_transfer_batch`.

**Вне заявленного объёма:** нет.

### techcards

Пины: ядро `83b223a` (боковая ветка; sha256 `coax.py`, `harness.py` совпадают с пином, каталога `techmap/` на `main`
нет); карточки, библиотека и grapher — `6a636fc`. `tc-library.json` на пине и `main` одинаков. После `6a636fc` в
зоне модуля — d88150d (28.09) и 2815c46 (24.09). ERP: `techcards/**`, `workflows/techcard_build.py`; тесты:
`backend/tests/techcards/**`.

| # | Правило SUPPLYMAP | ERP | Класс | Серьёзность | Происхождение | Перепроверка Opus | Комментарий |
|---|---|---|---|---|---|---|---|
| TC-1 | Строка комплектующего ЭТК = артикул к применению + «по КД» + наименование по правилам склада + ГРН только от привязки партии; `kitUseArt_`: код в НАИМЕНОВАНИИ набора, отличный от АРТИКУЛА, — артикул к применению; генератор ищет контакт в БД.ТЕР по нему; КД-артикул заменяется в заголовках операций и на схеме (`TcBuild.gs:27-64`, `TcKitset.gs:675-682`) | `etk_builder.py:191-221` `_components` — только `art`, `name`, `norm`, `unit` | пропущено | C2 | дрейф (d88150d, 28.09) | понижено с C1 | Компоненты ЭТК не питают состав комплектации, остатки не затрагиваются. Но в карте окажется аналог по КД вместо оригинала к применению, а норма обжима будет искаться по артикулу аналога (533К.11: BL2018TS вместо SHF-001T-0.8BS) |
| TC-2 | `HOUSING_ALIAS_RULES_`: BL2018P-nn рисуется как JST SMP-nnV-BC (`TcHousing.gs:139-140`) | `connector_housings.py:12` — только словарь `ALIASES` | пропущено | C3 | дрейф (d88150d) | — | Только рисунок раскладки |
| TC-4 | Запись операции: пустое название, чужой тип шага, неизвестный раздел оснастки отклоняются (`TcRecord.gs:217-232`) | `operation_cards.py` проверяет код и ≥ 1 шаг | пропущено | C3 | перенос | — | Библиотека — снимок, поштучной правки нет; пустое название всё же пропускается |
| TC-3 | Два шаблона с одним кодом — только со сторонами (A)/(B) или выбором владельца (`TcRecord.gs:158-166`) | матчинг по `templateId` | намеренно | C3 | перенос | — | Проблема в архитектуре ERP не возникает — `test_build_uses_template_id_not_ambiguous_side_title_and_tracks_pinout` |
| TC-5 | Заглушка ‹рев.›, проверка «render == input» (`TcRecordDb.gs:29-30, 61`) | нет | намеренно | C3 | перенос | — | Относится к портальной правке записи, которой в ERP нет |

**Перенесено хорошо:**
- Двойной обжим (`perTerm = max(2, perTerm)`), топология «максимум по операции, не сумма», узел «Сростки ×N» — `test_cut_and_crimp_operation_tracks_terminal_material_strip_and_applicator`, `test_splice_topology_and_strip_cable_do_not_advance_main_chain`.
- Аппликатор в «№ Прог.» поверх «-»/«—» только у `cutWireCrimp` — тот же тест.
- Резка без длины блокирует выдачу; ручные правки переживают пересборку — `test_issue_rejects_cut_operations_without_wire_length`, `test_project_techcard_revision_lifecycle_and_manual_carryover`.
- Grapher: материал, округление длины вверх до шага, разная зачистка → предупреждение и max — `test_cut_rounds_only_final_length_up_to_step`, `test_material_match_requires_unique_numeric_token_match`, `test_zip_snapshot_groups_equal_wires_and_matches_component`.
- Нет нормы терминала → предупреждение, не блокирует — `test_etk_surfaces_terminal_sections_missing_from_current_database`; замена библиотеки только админом — `test_non_admin_cannot_replace_operation_library`.

**Вне заявленного объёма:** `tcRecordsSync` / `diagTc*` (правка записей владельцем через clasp) — C3, в ERP заменено ролями API.

### references

Пин `5a039db`. `References.gs`, `TcOpsDb.gs`, `TcConfig.gs`, `Schema.gs` с пина не менялись; в `TcGenerator.gs` и
`TcBuild.gs` после пина правки только в `cutWireCrimp`, комплектующих ЭТК и сростках — функции подбора сечения,
нормы терминала и формула времени не тронуты. ERP: `references/{domain,technology,application,infrastructure,api}.py`,
миграция `0010`, потребитель `workflows/techcard_build.py`; тесты: `backend/tests/references/` (3 файла),
`test_etk_builder.py`. C1 нет: нормы идут в производственную инструкцию, не в склад и не в деньги.

| # | Правило SUPPLYMAP | ERP | Класс | Серьёзность | Происхождение | Комментарий |
|---|---|---|---|---|---|---|
| RF-3 | Сложенная под контакт жила удваивает сечение перед подбором высоты обжима (`TcGenerator.gs:772`) | `technology.py:93-98` `fold_wire_section` есть и протестирована, но `workflows/techcard_build.py:32` вызывает `wire_section` без учёта `ep.fold` | перенесено неверно | C2 | перенос | При `fold: true` в карте будет высота обжима под неудвоенное сечение. В фикстурах нет ни одного `fold: true` **Исправлено 2026-09-28 (`0174d6a`).** |
| RF-1 | Ключ поставщика: URL → домен без www, иначе литерал как есть (Склад, Фраксис, Вода…); при пустом хранилище — сид из `SUPPLIER_BY_LINK` (`Schema.gs:221-227`, `References.gs:31-37`) | `domain.py:27-35` всегда парсит как URL и отклоняет хост без точки; сида нет | перенесено неверно | C2 | перенос | Реальные литеральные ключи из SUPPLYMAP сохранить нельзя (422) **Исправлено 29.09 (прогон 20260929-refs-identity): Ключ поставщика как supplierKey_: домен без www либо литерал в нижнем регистре; пустое хранилище показывает сид SUPPLIER_BY_LINK.** |
| RF-2 | Пустые строки справочника молча отбрасываются при сохранении (`References.gs:41-48`) | `api.py:32-34` `min_length=1` — пустая строка роняет весь запрос 422 | перенесено неверно | C2 | перенос | Кнопка «+ Строка» в `ReferencesPage.tsx:107` без заполнения ломает сохранение всей карты **Исправлено 29.09 (прогон 20260929-refs-identity): Пустые строки карт отбрасываются при сохранении.** |
| RF-4 | Цеховые переопределения AWG из СПР.КАБ передаются в подбор сечения (`TcGenerator.gs:762`) | `techcard_build.py` вызывает `wire_section` без карты — работает только встроенное 1,0 мм² → 18 AWG | без теста | C3 | перенос | Сам справочник СПР.КАБ в источники модуля не входит **Исправлено 2026-09-28 (`72e9e71`):** карта «ГОСТ-сечение → AWG» — третий справочник в references, сборка ЭТК передаёт её в подбор сечения. Сид значениями СПР.КАБ (0,35→22, 0,75→18, 1,0→17) — миграция `0025_references_gost_awg_seed` (`87cfbe4`). |
| RF-5 | Сечения нет ни в одной строке терминала в БД.ТЕР → берётся первая строка терминала: её общая высота обжима (если ячейка не список по AWG), аппликатор и усилие «от - до»; предупреждение «нет строки под сечение» остаётся (`TcGenerator.gs:786-796`, `TcBuild.gs:356`) | `technology.py` `resolve_terminal` без запасной строки: пустая высота, «нет данных» вместо аппликатора, пустое усилие | перенесено неверно | C2 | перенос (на пинах `5a039db` и `6a636fc` уже было, a923c9a, 21.09) | Норма из общей строки терминала в карту не попадала **Исправлено 2026-09-28 (`677288a`).** |

**Перенесено хорошо:**
- Ключ изготовителя: схлопнуть пробелы + casefold — `test_manufacturer_key_is_case_and_spacing_insensitive`.
- Атомарная замена карт и запрет дубля нормализованного ключа (намеренно вместо «learn once») — `test_duplicate_normalized_keys_are_rejected`, `test_replaces_both_editable_maps_atomically`.
- Снимок БД.Технология только v14 — `test_rejects_outdated_technology_snapshot_schema`.
- Поиск терминала в три прохода; AWG-диапазон → мм² ±10 %, точное сечение важнее вилки; список высот «N AWG: значение (усилие Н)» — `test_terminal_resolution_matches_manufacturer_prefix_and_reports_each_section`, `test_terminal_row_prefers_exact_section_over_compatible_range`, `test_crimp_profile_prefers_exact_awg_over_range_and_keeps_pull_force`.
- ГОСТ 1,0 мм² → 18 AWG — `test_wire_section_uses_explicit_awg_gost_map_and_owner_override`.
- Время операции `tOp × units × batch + tPrep`, до 2 знаков, частные случаи CUT/PRS — `test_operation_time_is_per_unit_and_setup_is_once_per_batch`, `test_terminal_time_multiplies_multicore_wire_and_batch`, `test_cut_time_splits_setup_once_between_wire_rows`, `test_splice_and_coax_time_multiply_operation_units`.
- Допуск длины `len / 1000 × 7`, вверх, минимум 1 (`etk_builder.py:269-274`); запись справочников только с правом edit (усиление против SUPPLYMAP) — `test_viewer_cannot_replace_maps`.

**Вне заявленного объёма:** миграции старых схем БД.ТЕР (`terMergePullColumns_`, `terCollapsePlan_`) — C3, неприменимо: ERP принимает только v14.

### identity

Пин `83b223a` (боковая ветка). ERP: `identity/*.py` (включая `middleware.py`), `identity_admin.py`,
`shared/http_auth.py`, `legacy_access/*.py`, миграция `0003`; тесты: `backend/tests/identity/`,
`backend/tests/legacy_access/`, `test_techcards_auth.py`. C1 нет: все записывающие ручки склада, закупки,
производства и отгрузки требуют роль и режим edit одновременно.

Оговорка по методике: поролевые наборы разделов в `Portal.gs:593-638` на HEAD перекрыты `PORTAL_FULL_ACCESS_ = true`
(`Portal.gs:446-448, 654`) — база для всех ролей полная, а реальная матрица живёт в ScriptProperties, вне git.
Поэтому матрица сверялась по функциональным инвариантам, а не построчно.

| # | Правило SUPPLYMAP | ERP | Класс | Серьёзность | Происхождение | Перепроверка Opus | Комментарий |
|---|---|---|---|---|---|---|---|
| ID-6 | Режим «view» открывает раздел на чтение (`Portal.gs:529-540`, `portalStock05Guard_` `:790-801`) | у warehouse, procurement, receiving нет `*_view_principal`: все GET требуют edit (`warehouse/api.py`, `procurement/api.py`, `receiving/api.py`) | перенесено неверно | C2 | перенос | проверено | Директор, мастер, закупщик (склад у него view) не видят остатков и приёмки **Отложено** до конца работ другой сессии по receiving/procurement. Решение владельца 2026-09-28: view-гарды на GET и читалка в UI; дефолты — `ROLE_DEFAULTS` + миграция с `ON CONFLICT DO NOTHING`, чтобы явные настройки админа не перетирались. Ролевые наборы SUPPLYMAP (`Portal.gs:605-647`): buyer → склад; receiver → закупка; prodhead, management → склад, закупка, приёмка; master, staff → приёмка и склад. **Исправлено 29.09 (94f95f2, прогон 20260929-1050-manual).** |
| ID-2 | Раздел заявок («Новая заявка», «Мои заявки») открыт всем ролям (`Portal.gs:446-448`) | `identity_admin.py:39-58` `ROLE_DEFAULTS`: у receiver, estimator, prodhead, master, management, pm, qc, engineer нет `requests` | перенесено неверно | C2 | перенос | проверено | Кладовщик, мастер, инженер не могут подать заявку без ручной настройки |
| ID-4 | Лимит входа: 10 неудач на почту и 30 **глобально** за 10 минут, счётчики в ScriptProperties (`Portal.gs:255-306`) | `identity/application.py:156`: 10 на учётку и 300 на IP клиента, глобального лимита нет | перенесено неверно | C2 | перенос | проверено | ARCHITECTURE.md говорит «по учётной записи и адресу клиента», но глобальный порог SUPPLYMAP не упомянут; распределённый перебор не тормозится **Исправлено 29.09 (прогон 20260929-refs-identity): Глобальный счётчик 30/10 мин, 10 на учётку, номер попытки в журнале, уведомление роли admin на пороге.** |
| ID-3 | Доступ к Техкартам — только через матрицу, без зашитых списков ролей («второй источник правды», `Portal.gs:661-664`) | `shared/http_auth.py:48-138` почти в каждом `*_principal` поверх раздела проверяет ещё и зашитый список ролей | придумано | C2 | перенос | — | Персональное «edit» не работает, если роли нет в списке; engineer разрешён для techcards в коде, но не получает раздел по умолчанию **Исправлено 29.09 (прогон 20260929-refs-identity): Ключи erptctpl, tcgen, erpref, staffdir; техкарты и справочник без зашитых ролей; «Доступы» — всегда администратору; миграция 0045 переносит сохранённые права.** |
| ID-5 | Админу без явной настройки раздел открыт на edit (`portalAdminAccess_`, `Portal.gs:673-679`) | нет | пропущено | C2 | дрейф (14.09, после пина) | — | Новые разделы у админа закрыты, пока их не выдать вручную **Исправлено 29.09 (прогон 20260929-refs-identity): ROLE_DEFAULTS[admin] = edit на все разделы SECTION_LABELS.** |
| ID-7 | «Реальный админ» — только роль (`Portal.gs:374`) | роль + `permissions["identity"] == "edit"`, защиты от самоблокировки нет | придумано | C3 | перенос | — | — |
| ID-1 | — | сверщик заявил, что GET `/api/techcards/templates*` и `/api/techcards/projects` открыты без сессии | — | — | — | **отклонено** | Роутер подключён с `dependencies=[Depends(techcards_view_principal)]` (`main.py:46-49`) |

**Намеренно** (SOURCE.json `intentionalSecurityUpgrades`): Argon2id вместо SHA-256(email:PIN), непрозрачный
HttpOnly-токен вместо localStorage, CSRF, абсолютный срок сессии.

**Перенесено хорошо:**
- Роль + персональное перекрытие, перекрытие сильнее роли и не меняет общую политику — `test_user_override_can_remove_role_access_or_grant_another_section`, `test_cli_permission_is_per_user_override`, `test_user_override_does_not_mutate_shared_role_policy`.
- Одинаковый ответ на неизвестного сотрудника и неверный пароль; 10 неудач на учётку — `test_login_error_is_generic`, `test_wrong_password_is_generic_and_rate_limited_by_account`.
- Стабильный UUID отдельно от ФИО — `test_identity_user_has_stable_uuid_separate_from_display_name`.
- Деактивация отзывает все сессии, скользящий срок не превышает абсолютный — `test_deactivation_revokes_all_user_sessions_atomically`, `test_sliding_expiry_never_exceeds_absolute_lifetime`, `test_sliding_refresh_cannot_resurrect_revoked_session`.

### operations

Пин `a923c9a`. `Trace.gs`, `OwnerTick.gs` (кроме бэкапов), `Triggers.gs`, `Notify.gs`, `Master.gs` по правилам не
изменились. ERP: `operations/*.py`, `outbox_worker.py`, `shared/{events,persistence,clock}.py`, миграция `0011`;
тесты: `backend/tests/operations/`, `test_outbox_worker.py`.

| # | Правило SUPPLYMAP | ERP | Класс | Серьёзность | Происхождение | Комментарий |
|---|---|---|---|---|---|---|
| OP-1 | Уведомления реально уходят инициатору, технологу, снабжению (`Notify.gs:1-68`, вызовы из `OwnerTick.gs`, `Receiving.gs:771-788`, `Shipment.gs:113-122`) | `application.py:59-95` — consumer `notification.requested` есть, **продюсеров нет** ни в одном модуле (grep по `backend/app`: единственное упоминание — сам consumer) | перенесено неверно | C2 | перенос | Лента уведомлений из ARCHITECTURE.md всегда пуста; сообщение создаётся только в тесте **Исправлено 29.09 (e746496, прогон 20260929-1050-manual).** |
| OP-4 | Ежедневный дайджест просроченных заявок и зависшего карантина (`OwnerTick.gs:340-396`) | нет | пропущено | C2 | перенос | Просрочки никому не видны без захода в модуль |
| OP-5 | Алерт об ошибках фоновых шагов с dedup и cooldown 6 ч (`OwnerTick.gs:106-172`) | retry + dead-letter (`persistence.py:117-135`), `/api/operations/outbox` только на чтение | пропущено | C3 | перенос | О dead-letter никто не узнаёт |
| OP-6 | Кольцевой буфер 40 уведомлений на человека (`Notify.gs:41-65`) | хранится всё, `LIMIT` при чтении | намеренно | C3 | перенос | Разумная замена в БД |
| OP-7 | Дубль уведомлений на почту по флагу `notify_email` (выкл.) | канала почты нет | намеренно | C3 | перенос | По умолчанию поведение совпадает |
| OP-8 | — | нет теста poison/dead-letter для уведомлений | без теста | C3 | перенос | У receiving/warehouse/procurement такие тесты есть |

**Судьба обязанностей OwnerTick и триггеров:**

| Обязанность | Дом в ERP | Класс | Серьёзность |
|---|---|---|---|
| Статус по умолчанию для новой заявки | статус ставится при создании | намеренно | — |
| Закупка «В пути» → строка ожидания приёмки, без дублей | `procurement/application.py:363-395` → `receiving.expected` с уникальным ключом | перенесено | — |
| Приход на склад из ожидания | `receiving/application.py:161-187` → `warehouse.received` | перенесено | — |
| Проведение списаний | `warehouse/application.py:243-260` — синхронно, инвариант сохранён | перенесено | — |
| Откат «Закрыта», поставленной мимо гейтов | статус не меняется мимо домена (`shipping/domain.py:250-251`) | намеренно | — |
| SVOD pull/push, зеркала Ожидание ↔ Закупка ↔ Запросы, буфер журнала, бэкапы листов | одна БД, дублей нет | намеренно | — |
| Дозавоз `-Д` и его продвижение / отмена | нет | пропущено | **C1** — см. RC-2 — **исправлено (`7a56e92`)** |
| Ожидание → Карантин через 2 рабочих дня | нет | пропущено | C2 — см. RC-3 |
| Ревизии ВК `-ВN`, разукомплектовка `-ПN` | нет | пропущено | C2 — см. RC-7, RC-8 |
| Письмо об утилизации после 90 дней карантина | нет | пропущено | C3 — см. RC-11 |
| Дайджест просрочек | нет | пропущено | C2 — OP-4 |
| Блокировка строк закупки по исполнителю | владение строкой в Procurement (только владелец правит факты) | намеренно | — |
| `tcKitNamesTick_` — подтяжка наименований наборов | не оценивалось | — | — |

**Перенесено хорошо:**
- Audit пишется тем же commit, что и агрегат, во всех модулях; API только читает и доступен management/admin — `test_audit_is_restricted_to_management_and_admin`.
- Идемпотентная доставка уведомления по `message.id:recipient`, получатель из сессии — `test_notification_outbox_delivery_is_idempotent`.
- Сбой фоновой задачи не откатывает бизнес-данные; retry с backoff и dead-letter — `test_outbox_worker.py`.

**Вне заявленного объёма:** нет.

## Проверка (`npm run check`)

Прогон через `erp-verifier` 2026-09-28, код ERP на `51cd50e` не менялся. **FAIL.**
`npm run check` — одна цепочка `&&`, она остановилась на архитектурных тестах; остальные наборы прогнаны отдельно.

| Набор | Итог | Классификация |
|---|---|---|
| Архитектура (`npm test`) | 18 из 20, 2 падения | дрейф: `techcards import records the exact source commit` (`test/architecture.test.mjs:133`, пин `operationLibrary` `6a636fc` ≠ `main`) → `npm run sync:techcard-library`; `production records its implemented SUPPLYMAP execution stage` (`:279`, пин `6a636fc` ≠ `main`) — скрипта `sync:production` нет |
| Backend (`npm run test:backend`) | 327 из 327 тестов прошли, но код выхода 1 | окружение: `pytest_sessionfinish` → `cleanup_dead_symlinks` падает с `PermissionError [WinError 5]` на `%TEMP%\pytest-of-Артемий Васильев\pytest-current` (Windows, не-ASCII имя пользователя). Воспроизводится дважды. Гейт красный без единого упавшего теста **Исправлено (`a7a1bec`)**: причина не кириллица, а `pytest-current` — файловый симлинк на каталог; `backend/tests/conftest.py` задаёт `--basetemp` на процесс |
| Frontend (`npm run test:frontend`) | 51 тест в 12 файлах, `tsc -b && vite build` — ок | — |

Гейт не доказывает: поведение на PostgreSQL (тесты на SQLite), соответствие правилам SUPPLYMAP (архитектурные тесты
сверяют только коммиты и sha256), вёрстку UI.

## Перепроверка C1 (Opus)

| Кандидат | Вердикт | Основание |
|---|---|---|
| PR-4, PR-6 — закупка без фактической цены, суммы, валюты | подтверждено | `procurement/domain.py:155-178` принимает счёт по URL и дате; полей факта нет, есть только `calculated_*` из заявки |
| PX-1 — исправление возвращённой коробки | подтверждено | `correction_of_id` нигде не читается; `add_output` только прибавляет; `total_for_operation` суммирует все выработки |
| RC-2 — дозавоз оплаченного остатка | подтверждено | `backorder_quantity` только хранится и отдаётся в API |
| RC-6 — возврат поставщику | понижено до C2 | остаток правится складским списанием с причиной |
| TC-1 — артикул к применению / по КД / ГРН | понижено до C2 | компоненты ЭТК не питают состав комплектации |
| SH-5 — снятие набора и отмена без тестов | понижено до C2 | код симметричен и транзакционен, не хватает только тестов |
| WH-2 — списание не трогает резерв | понижено до C3, «намеренно» | ERP строже SUPPLYMAP, описано в ARCHITECTURE.md; SOURCE.json ошибочно зовёт правило перенесённым |
| PR-10 — недостача / недовоз / излишек | отклонено | формулы есть в `receiving/domain.py:36-37, 111, 136` |
| ID-1 — ручки техкарт без сессии | отклонено | `main.py:46-49` вешает `techcards_view_principal` на весь роутер |

## Итог

C1 — 4 строки (3 проблемы), C2 — 42 строки, C3 — 37 (с повторами между модулями и сквозными).
Происхождение почти всех расхождений — перенос: у большинства модулей исходные файлы на пине и HEAD совпадают.
Дрейф сосредоточен в d88150d и 39a5a97 (комплектующие ЭТК, подсказки склада), `portalAdminAccess_` и формате номера
проекта.

Самые серьёзные расхождения:

1. **PR-4 + PR-6 (C1, перенос)** — закупка проходит «В оплату» → «В пути» без цены, НДС, суммы и валюты; фактическая стоимость покупки в ERP не хранится, курс ЦБ и округление не перенесены. *Исправлено (`53c1cfd`).*
2. **PX-1 (C1, перенос)** — количество возвращённой мастером коробки нельзя уменьшить: «исправление» только докладывает сверху, `correction_of_id` мёртвое, завышение уходит в готовую продукцию. *Исправлено (`369e70d`).*
3. **RC-2 (C1, перенос)** — оплаченный недовоз не превращается в строку дозавоза; остаток виден числом на строке приёмки и больше нигде не ждётся. *Исправлено (`7a56e92`).*
4. **OP-1 (C2, перенос)** — у ленты уведомлений есть consumer, но ни одного продюсера: ни приёмка, ни отгрузка, ни заявки никого не уведомляют.
5. **ID-6 (C2, перенос)** — режим «view» для склада, закупки и приёмки не работает: их GET требуют edit. *Отложено, решение по дефолтам записано в строке ID-6.*
6. **RF-3 (C2, перенос)** — для сложенной жилы высота обжима подбирается по неудвоенному сечению: неверная производственная инструкция. *Исправлено (`0174d6a`).*
7. **PX-2 (C2, перенос)** — контрольная цифра номера коробки считается по схеме 3/1, а не Луна: номера SUPPLYMAP при параллельной работе ERP отвергнет. *Исправлено (`0588fba`).*

Отдельно: X3 — архитектурные drift-тесты не краснеют для 9 из 11 модулей, и `npm run check` красный в backend на
Windows при 327 зелёных тестах.

## Исправления после аудита

| Строка | Коммит | Что сделано |
|---|---|---|
| RF-3 | `0174d6a` | `ep.fold` удваивает сечение перед подбором нормы терминала; подпись сечения без хвостовых нулей («0,24 мм²») |
| PX-2 | `0588fba` | Контрольная цифра Луна, терпимый ручной ввод; серия ERP 000001–099999 (`issue_box_number`), SUPPLYMAP — от 100000 |
| Гейт pytest | `a7a1bec` | `backend/tests/conftest.py`: `--basetemp` на процесс, без `pytest-current`; зелёный прогон выходит с кодом 0 |
| PR-4, PR-6 | `53c1cfd` | Факт-цены, НДС, сумма, валюта, № и дата счёта; ведущее поле и копейки по `computeFact_`; инвалюта × курс ЦБ × 1,065; гейт `PUR_PAY_REQUIRED` на счёте и на оплате. Отступления (дата курса, ручной курс при недоступном ЦБ, Decimal) — в ARCHITECTURE.md, «проверить у бухгалтера». Миграция `0024_procurement_money` |
| RC-2 | `7a56e92` | Первый ВК с недовозом → `procurement.backorder_requested` → строка `-Д` в «Согласование» с ценой, оплатой, счётом и курсом исходной; сумма на остаток, точное дробное количество (решение владельца). Подтверждение досыла → «В пути» → своя строка приёмки; отмена → новая заявка сразу в закупке |
| RF-4 | `72e9e71` | Справочник «ГОСТ-сечение → AWG» (kind `gost_awg`), передаётся в сборку ЭТК; поправка 1,0 мм² → 18 AWG в коде сильнее карты |
| RF-4 (сид) | `87cfbe4` | Миграция `0025_references_gost_awg_seed`: 0,35→22, 0,75→18, 1,0→17, `ON CONFLICT DO NOTHING` (строки админа сильнее), откат удаляет только `gost_awg` |
| RF-5 | `677288a` | Нет строки под сечение → общая высота, аппликатор и усилие с первой строки терминала, как `buildTerData_`; `found=False` оставляет предупреждение |
| PX-1 | `369e70d` | `correct_box`: запись-исправление без сессии с `correction_of_id` заменяет одну запись возвращённой коробки; количество коробки заменяется, коробка сразу «Закрыта оператором»; разница добирается из тех же входных коробок или возвращается в них с последней (отрицательное ребро расхода, родословная — нетто); заменённая запись выпадает из `total_for_operation`, лимита задания и ГП. Брак: новая Б- при изменении, старая помечается `replaced_by_output_id` (рамки владельца). Прочие решения — в ARCHITECTURE.md, «решено агентом — проверить». Миграция `0026_production_output_corrections` (поверх `0025_references_gost_awg_seed`) |
| RQ-5 | `b19a1b3` | `POST /api/requests/import/parse` (`parse_zpk`, ЕД.ИЗМ по типу, проект из ЗКЗ!B2 или названия) и `POST /api/requests/import` (гейты `importRequests_` дословно, одно `<проект>.NNN` на пачку, АРТИКУЛ КД = Обозначение, аудит `source: import`). Решения — в ARCHITECTURE.md, «решено агентом — проверить» |
| PR-1 | `c74101a` | Команды владельца `approval` / `approval/resume` / `cancel` на любом этапе до оплаты; выход из «Согласование» — на этап по данным строки; «Отменено» окончательно; неразобранную строку забирает тот, кто её паркует или отменяет; у дозавоза общих команд нет. Кнопки в карточке закупки. Решения — в ARCHITECTURE.md, «решено агентом — проверить» |
| WH-1 | `90f6774` | `POST /api/stock/batches/{id}/return` (кладовщик/админ): количество > 0 и не больше СПИСАНО, обязательное «откуда» → `details.purpose` аудита `warehouse.writeoff_returned`, основание «Возврат» / «Возврат: …»; кнопка «Вернуть на склад» у партии со списанным (SK-3) |
| PR-3 | `d30259b` (☁ `cloud/PR-3-202609282033`) | `POST /api/procurement/supplier-requests`: свои строки «Готов к запросу» группой ПОСТАВЩИК + ТИП ЗАДАЧИ, № ЗАКАЗА `PO-NNN` сквозной по таблице (`nextOrderNumber_`), ДАТА ЗАКАЗА — московский день, все строки группы → «Жду счет» одной транзакцией; текст письма (`supplierRef_`, `positionParagraphs_`) в `procurement_supplier_orders`; свободный `reference` удалён. Отступления — в ARCHITECTURE.md, «решено агентом — проверить». Миграция `0029_procurement_supplier_orders` |
| PR-5 | `3435785` (☁ `cloud/PR-5-202609282232`) | `POST /api/procurement/{id}/invoice/replace`: новый счёт вместо старого без сброса ОПЛАТЫ и статуса, № и дата счёта из формы (пустое не трогает), прежний счёт — в audit `procurement.invoice_replaced`; работает и на дозавозе `-Д`. OCR и флаг для вывода недовоза из карантина не перенесены (ARCHITECTURE.md) |
| PR-9 | `61659c8` (☁ `cloud/PR-9-202609290132`) | `POST /api/procurement/{id}/split`: 1–30 копий «Новый», Рев.A, новые System ID `SM-<день>-NN`, данные заявки остаются, всё заполненное закупщиком — пустое; исходная строка +1 к версии (повтор со старой формы → 409); кнопка «Раздробить» в карточке. Решения — в ARCHITECTURE.md, «решено агентом — проверить» |
| PX-3, PP-2 | `c7faa39` (☁ `cloud/PX-3-202609290232`) | Нет ни одной строки матрицы по коду операции → допуск не проверяется, в автоплан идёт вся бригада с коэффициентом 1 (`prodSkillOf_`); заведена хоть одна строка → нужен «допущен». Текст ошибки — «нет допущенных операторов — заведите компетенции». Решения — в ARCHITECTURE.md, «решено агентом — проверить» |
| RC-3 | `e7325c1` (☁ `cloud/RC-3-202609290333`) | `inspected_on` (дата акта ВК, миграция 0031 с заполнением из аудита); `quarantine_overdue_waiting` в тике `outbox_worker` переводит «Ожидание» старше 2 рабочих дней в «Карантин», аудит `receiving.auto_quarantined` с адресатами «Технолог»/«Снабжение»; письмо — OP-1/RC-10 |
| WH-3, WH-5 | `58026dc` | `stock05ArtInfo_` по `013a986`: хвост «@ПОСТАВЩИК» отрезается до любого опознания, ведущие нули `^0+` у 10-значного артикула; `STOCK05_SERIES_RULES_` до семейства корпуса; 9-значный номер в описании; вилка/розетка из названия семейства; коаксиал — коды поставщика S/N/T/B/U-1xx, ISMA→SMA, 2.92mm, 3.5mm, B2/B3 = RG-405/402, «… male/female» в описании, все RG через «/», .086 → RG-405. Пин warehouse `6a636fc` → `013a986`. Решения — в ARCHITECTURE.md, «решено агентом — проверить» (SK-11) |
| RC-7 | `9ea863b` (☁ `cloud/RC-7-202609290434`) | «Провести новый ВК»: строка с актом → «Архив ВК» (история, закрыта для ВК и склада), ревизия `<ID>-ВN` «В пути» с контекстом заказа и УПД; одна живая строка приёмки на строку закупки; дозавоз один на цепочку. Решения — в ARCHITECTURE.md, «решено агентом — проверить». Миграция `0032_receiving_vk_revisions` (поверх `0031_receiving_inspected_on`; в ветке была 0028) |
| SH-5 | ☁ `cloud/SH-5-202609290532` | Только тесты, код не менялся: `unpick` и `cancel` в домене (вся строка, статус «Новая»/«Собрана», пустая/чужая строка, запрет после «Упакована»/«Отгружена», повторная отмена) и `put_back` против `take`; через API и SQLite — снятие строки и отмена упакованной заявки возвращают набранное в регистр ГП, устаревшая версия (409) и отказ (422) регистр не трогают. Мутация «не писать `put_back`» ловится двумя тестами. Отступления от `shipUnpick_` — в ARCHITECTURE.md, «решено агентом — проверить» |
| RC-12 | `97d708d` (☁ `cloud/RC-12-202609290633`) | `historical: true` + `document_date` (запасная `arrival_date`) — опорная дата вердикта ВК = дата УПД; год ГРН — от сегодня, как `nextGrn_`; флаг и дата УПД в форме приёмки |
| RQ-3 | ☁ `cloud/RQ-3-202609281718` | `POST /api/requests/handoff` с `retransfer: true` (кнопка «Повторно в закупку» у заявки «В работе») — новая версия заявки и новое событие; consumer закупки видит, что у заявки уже есть строка, и добавляет новую: System ID `SM-<день>-NN` по строкам закупки, ревизия = max буква по НАЗНАЧЕНИЮ + 1, «Новый»; прежняя строка и данные закупщика не трогаются. Решения — в ARCHITECTURE.md, «решено агентом — проверить» |
| PR-2 | ветка `rescue/procurement` | Поверх RQ-3: ревизии группируются по НАЗНАЧЕНИЮ как `normHeader_` (пробелы, регистр — `designation_key`); первый перенос заявки, чей System ID уже занят строкой повторного переноса или дробления, получает свежий `SM-YYYYMMDD-NN` и остаётся Рев.A — дублей System ID в закупке нет. Решения — в ARCHITECTURE.md, «решено агентом — проверить» |
| PR-7 | ☁ `cloud/PR-7-202609282333` (ветка `rescue/procurement`) | Форма «Внести КП»: все строки заказа «Жду счет» одной формой, импорт .xlsx/.csv по шапке «Наименование \| Кол-во \| Цена \| НДС», сопоставление по имени, необязательный счёт на весь заказ. При переносе на main заказ ищется по № ЗАКАЗА `order_number` (PR-3, `PO-NNN`), как `kpOrderRows_`, а не по тексту `supplier_request_reference`. Решения — в ARCHITECTURE.md, «решено агентом — проверить» |
| RQ-4 | ☁ `cloud/RQ-4-202609281734` | `project_reference_valid` / `normalize_project_reference` (перенос `projNoValid_` / `projNoNorm_`): формат по сырой строке, латинская K отклоняется, хранится каноническая форма («2000к» → «2000К.1»). Порт `ProjectRegistry` в `create_requests`, адаптер `workflows/project_registry.py` над снимком реестра плана цеха: номер вне открытых проектов → 422 «Позиция N: проект … не найден», вся пачка отклоняется. Решения — в ARCHITECTURE.md, «решено агентом — проверить» |
| PR-8 | ☁ `cloud/PR-8-202609290033` (ветка `rescue/procurement`) + DOC-1 | ЗАМЕНА / РЕШ. ЗАМЕНЫ на строке закупки (`PATCH …/replacement`, селектор «Замена» в карточке). ЗАМЕНА = Да без «Разрешено» хотя бы у одной строки — блокер всей группы в `supplier_request_blockers` (`build_supplier_requests` кладёт её в `skipped`, остальные заказы формируются), подсказка в карточке. Документ разрешения — DOC-1: «РАЗРЕШЕНИЕ НА ЗАМЕНУ» через общий `PrintSheet` (FF-8/WH-6), `GET .../replacement-permission`, номер/дата (`0037_procurement_replacement_permission`) присваиваются и обнуляются доменным правилом (`next_system_id`, префикс «РЗ»). ИЗГОТОВИТЕЛЬ/СТРАНА не перенесены (поля склада, не закупки). Миграции `0035_procurement_replacement` (поверх `0034_warehouse_item_types`; в ветке была 0028) и `0037_procurement_replacement_permission` (поверх `0036_identity_departments`). Решения — в ARCHITECTURE.md, «решено агентом — проверить» |
| SH-1 | `1d4ebc2` (☁ `cloud/SH-1-202609290733`) | `create_shipment` в той же транзакции набирает со склада ГП и присваивает PL/DN → «Упакована»; `no_docs` → «Собрана»; без остатка — «Новая». Флажок «Без отгрузочных документов» в форме. Решения — в ARCHITECTURE.md, «решено агентом — проверить» |
| SH-2 | `33f065a` (☁ `cloud/SH-2-202609290833`) | `POST /api/shipping/project-queue`: строка ГП на каждую КБ карточки проекта (`shipQueuePlan_`), повтор ничего не добавляет, тираж следует за карточкой, пока по КБ ничего не отгружено; КБ без тиража возвращается в `skipped`. `POST /api/shipping/project-queue/drop`: снос карточки удаляет строки без движения, принятые/отгруженные и названные в заявке остаются. Решения — в ARCHITECTURE.md, «решено агентом — проверить» |
| SH-3 | `9f15a15` (☁ `cloud/SH-3-202609290933`) | Адресат проекта (`shipping_project_addressees`, миграция `0042_shipping_project_addressees` (поверх `0041_identity_own_sections`; в ветке была 0028)): получатель, точка, контакт, телефон, условия, ТК, тип УПД; `GET/PUT /api/shipping/project-addressee` — присланное заменяет, пустое стирает. Пустые поля заявки наследуют карточку проекта первой позиции, своё непустое сильнее; набранные точка/контакт/телефон запоминаются за каждым проектом заявки, пустое не стирает (`shipProjPatch_`). Решения — в ARCHITECTURE.md, «решено агентом — проверить» |

Отложены: ID-6 (см. решение в строке), OP-1 (уведомления — следующая сессия).
