# Печать маркировки Godex — контракт T1/T2/T3

База: `/api/production/marking`. Все размеры — мм, PNG монохромный, 300 dpi.
Печатать можно с правом `print:edit` и просмотром «Задания» или «Производство», только доступное задание/группу.
Настройка и калибровка требуют `erpref:edit` (то же право, что редактирование оборудования). UUID оборудования — `equipment_units.id`.

- `GET /machines` → `{machines: [{equipment_id, inventory_number, name, profiles: [...], binding: null|{version, machine_id, printer}}], printers: [{machine_id, pc, name, driver, port, kind, present, online}]}`.
- Профиль: `{id, equipment_id, name, version, calibrated, geometry}`. Пять предустановок ОБ.101/ОБ.102 связываются с UUID при первом чтении справочника; неоднозначные/отсутствующие номера не связываются. Начальное состояние — «Не откалиброван».
- `PUT /profiles/{id}`: `{version, calibrated, geometry}` → профиль. Геометрия: `{width_mm,height_mm,rotation:0|90|180|270,feed_direction:"height"|"width",zones:[{x,y,width,height}],blank_zones:[...],offset_x_mm,offset_y_mm,min_font_mm:1.6,max_font_mm:5,max_lines:3,alignment:"center"|"left"|"right"}`. Зоны заданы до поворота; смещение не должно выводить текст за разрешённые зоны. Две зоны получают одинаковый текст. Изменение геометрии снимает калибровку; подтверждение калибровки — отдельное сохранение неизменной геометрии.
- `PUT /machines/{equipment_id}/binding`: `{version:0|N,machine_id,printer}` → привязка. Имя очереди точное, принтер должен присутствовать в heartbeat.
- `POST /profiles/{id}/calibration`: `{version,text:"АБВ 123",print:false,idempotency_key?:string}` → `{png_b64,sha256,width_mm,height_mm,...}`; `print:true` ставит одну пробную этикетку, возвращает также `batch_id`. Номера изделий не расходуются. Проба разрешена до подтверждения калибровки.

В PNG ось Y соответствует подаче. `feed_direction:"width"` добавляет поворот на 90° по часовой стрелке
к `rotation`; итоговые `width_mm`/`height_mm` и `orientation` уже учитывают оба параметра. Агент повторно растр не поворачивает.

## Предпросмотр и партия

`POST /preview`:

```json
{"profile_id":"UUID","profile_version":1,"source":{"task_id":"UUID","operation_id":"UUID","version":3},"selected":[]}
```

Вместо задания: `source:{group_id:"UUID",version:2}`. Пустой `selected` выбирает все надписи;
непустой — ключи надписей из предыдущего preview, без изменения текста и количества.
Ответ: `{manifest_hash,profile_id,profile_version,binding_version,source_snapshot,labels:[{key,task_id,text,material,quantity,serial,png_b64,sha256}],total,width_mm,height_mm,orientation,dpi}`.
Одна строка с quantity=N превращается в N последовательных заданий с copies=1. Максимум 500 этикеток.
Неодинаковые материалы требуют выбора совместимых надписей отдельно; термоусадочные трубки не печатаются на этикетках.

`POST /batches`: тот же запрос + `{manifest_hash,idempotency_key,reprint_of?:"UUID",reason?:"Причина повторной печати"}`.
Сервер повторно проверяет профиль, источник, диапазон номеров, привязку и PNG. Повтор ключа с тем же запросом возвращает ту же партию; с другим — 409.
Повторная печать создаёт отдельную партию с причиной и ссылкой на исходную; номера изделий сохраняются.
`GET /batches/{id}` → `{id,status,status_label,total,jobs:[{id,sequence,status,status_label,message,spool_job_id}],reprint_of}`.
Статусы: `pending` «В очереди», `taken` «Печатается», `spooled` «Передано принтеру», `failed` «Ошибка печати», `unknown` «Результат неизвестен».
`spooled` подтверждает только передачу драйверу.
`POST /batches/{id}/jobs/{job_id}/resolve`: `{outcome:"spooled"|"failed",reason:"Результат проверки оператором"}` — правка «Производство», снимает паузу после проверки неизвестного результата. Повторная печать выполняется отдельно.

## Агент 2.9.0

Heartbeat `/api/stock/scan/agent/heartbeat` добавляет `printers:[{name,driver,port,kind:"tspl"|"gdi_png",present}]`.
`POST /api/stock/labels/jobs/claim`: `{machine_id,capabilities:["tspl","gdi_png"],printer?,host?}`.
Ответ `{job:null}` либо `{job:{id,attempt,format:"gdi_png",printer,width_mm,height_mm,orientation,dpi:300,copies:1,png_b64,sha256}}`.
`attempt` — непрозрачная строка, обязательно вернуть её без изменений.
`POST /api/stock/labels/jobs/{id}/result`: `{attempt,outcome:"spooled"|"failed"|"unknown",message,spool_job_id?,machine_id?}`.
При отсутствии machine_id сервер определяет ПК по привязанному токену; токен должен принадлежать владельцу claim.
Ответ `{ok:true,status}`: повтор идентичного результата — 200 с тем же статусом. Чужой ПК/токен — 403; другая попытка — 409.
Просроченная попытка маркировки становится unknown; пришедшая позже квитанция сохраняется на задании (статус остаётся
unknown до решения оператора «Проверка» → spooled/failed) и отвечает 200 со статусом. Агент держит паузу только
маркировки (TSPL печатает дальше), пока сервер не вернёт решение оператора; 403/404/409 агент считает окончательными.
Автоматической перепечати нет. Claim привязан к токену/ПК, имя принтера и ПК обновляются при каждом claim.
Старые `/labels/agent/claim` и `/labels/agent/jobs/{id}/result` и TSPL-ответы сохраняются. Они никогда не выдают PNG.

Ошибки: 403 «Операция не назначена сотруднику»/«Задание другой площадки»; 404 «… не найдено»;
409 «Источник или профиль изменился — обновите предпросмотр», «Принтер не на связи», «Профиль не откалиброван»,
«Ключ запроса уже использован»; 422 «Надпись не помещается: …»/«Выберите совместимый материал».
Проводные markTexts пока не являются структурированными fields.marks в выданном документе; их адаптер требует отдельного изменения etk_builder.py.
