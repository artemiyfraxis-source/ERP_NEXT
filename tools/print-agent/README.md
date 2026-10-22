# Агент печати этикеток (ПК склада, TSC MB341)

Маленькая программа на PowerShell для ПК склада. Каждые 3 секунды спрашивает ERP, нет ли этикеток на печать, и отправляет их на принтер TSC через очередь Windows. Python не нужен, ничего не нужно открывать: агент работает скрыто и стартует сам при входе в Windows.

## Что нужно заранее
- Принтер TSC MB341 подключён по USB, драйвер TSC установлен, в «Принтеры и сканеры» он виден (очередь печати).
- Токен ERP со scope `labels:print`. Его создаёт администратор в ERP. Токен показывается один раз, скопируйте его.

## Установка (одна команда)
1. Скопируйте папку `tools\print-agent` на ПК склада.
2. Откройте PowerShell в этой папке (в Проводнике: адресная строка, набрать `powershell`, Enter) и выполните:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Token "ТОКЕН_ИЗ_ERP"
```

Сервер по умолчанию `https://erp-server.tail811f20.ts.net`. Другой сервер: `-Server "https://..."`.
Принтер выбирается сам: первая очередь, в названии которой есть «TSC». Если такой нет, установка остановится и покажет список очередей; тогда укажите нужную: `-Printer "имя очереди"`.

Установщик копирует агент в `C:\ProgramData\FraxisPrintAgent`, сохраняет настройки (токен зашифрован средствами Windows и читается только вашим пользователем на этом ПК), создаёт задачу «Fraxis — печать этикеток» (запуск при входе, перезапуск при сбое) и сразу запускает её.

## Как узнать имя очереди принтера
```powershell
Get-Printer | Select-Object Name, DriverName, PortName
```
Или: Параметры, Устройства, Принтеры и сканеры, имя принтера в списке. Это имя и нужно в `-Printer`.

## Как проверить, что работает
1. Журнал: `Get-Content C:\ProgramData\FraxisPrintAgent\agent.log -Tail 20`. Должна быть строка «Агент запущен», без повторяющихся ошибок «Сеть/сервер».
2. Задача: `Get-ScheduledTask -TaskName "Fraxis — печать этикеток"` (состояние Running).
3. В ERP отправьте этикетку на печать. Она выйдет на принтере, в журнале появится «Напечатано: задание ...».
4. Проверка без бумаги: остановите задачу и запустите один опрос в режиме «понарошку» (задание из очереди ERP будет взято и помечено как ошибка «dry run», этикетка не напечатается):
```powershell
Stop-ScheduledTask -TaskName "Fraxis — печать этикеток"
powershell -ExecutionPolicy Bypass -File C:\ProgramData\FraxisPrintAgent\agent.ps1 -DryRun -Once
Start-ScheduledTask -TaskName "Fraxis — печать этикеток"
```

## Если что-то не так
- Нет связи с сервером: агент пишет в журнал, ждёт 3, 6, 12, 24, 30 секунд и пробует снова; ничего перезапускать не нужно.
- Принтер выключен или очередь названа иначе: задание вернётся в ERP с ошибкой (код Windows в тексте), этикетку можно отправить повторно.
- Сменили принтер или токен: запустите `install.ps1` ещё раз с новыми параметрами.
- Журнал `agent.log` сам переименовывается в `agent.log.1` при размере около 1 МБ. Токен в журнал не пишется.

## Удаление
```powershell
powershell -ExecutionPolicy Bypass -File .\uninstall.ps1
```
(то же самое: `install.ps1 -Uninstall`). Удаляет задачу и папку `C:\ProgramData\FraxisPrintAgent`. Токен в ERP после этого лучше отозвать.

## Для разработчика
- Контракт с сервером: `POST /api/stock/labels/agent/claim` `{printer, host}` -> `{job: {id, copies, tspl_b64} | null}`; `POST /api/stock/labels/agent/jobs/<id>/result` `{ok, error}`; заголовок `Authorization: Bearer <токен>`. Копии уже внутри TSPL (`PRINT n`), агент отправляет задание один раз.
- Тесты: `Invoke-Pester tools\print-agent\tests\agent.Tests.ps1` (Pester 3+, печать подменена). Ручной сквозной тест с мок-сервером: `powershell -NoProfile -ExecutionPolicy Bypass -File tools\print-agent\tests\mock-server.ps1`.
- Файлы `.ps1` хранятся в UTF-8 с BOM: Windows PowerShell 5.1 иначе портит кириллицу.
