# Сторонний код в `frontend/src/harness`

## TECHMAP-GRAPHER

TECHMAP-GRAPHER © Василий (SaveOurSouls), used with the author's permission, 2026-10-06, commit e63befb.

- Источник: `src/Techmap.Client/src/editor`, `src/Techmap.Client/src/component-library` и общие компоненты
  (`InfoHint`, `AnchoredPopover`) на коммите `e63befb` (ветка `codex/integration-m5-22`).
- Первый коммит ветки переноса содержит эти файлы без изменений; все правки ERP — следующими коммитами.
- Не перенесено: сетевые клиенты (заменены адаптерами), фото/HEIC, аварийные черновики в localStorage и
  журнал восстановления, маршрут изготовления (Route V2) и панели производства, UML, авторинг шаблонов
  библиотеки компонентов, модель времени.

## Правила хранения чертежа на сервере ERP

`backend/app/modules/techcards/harness/validate.py` переписывает на Python проверки сервера и редактора
TECHMAP-GRAPHER (коммит `9cf3b88`, та же ветка, то же разрешение автора): `ElectricalGraphValidator`,
`ValidateCableInstances`, `HarnessStripProfileValidator`, `TemplateContactNumberingValidator`,
`HarnessPhysicalTopologyValidator` (только ссылки и непрерывность трасс) и проверки `parseHarnessDesignDocument`.

## Исключено: каталог штриховок QCAD (GPL-3.0)

`hatching-catalog.json` TECHMAP-GRAPHER собран из шаблонов QCAD под GPL-3.0 и сюда **не перенесён**.
`hatching.ts` содержит собственный набор штриховок ERP (коды «Ш1»…«Ш8»); ничего, производного от QCAD,
в этом каталоге нет.

## Текстуры оболочек (ambientCG, CC0 1.0)

`editor/covering-textures/Rubber002.jpg`, `Fabric061.jpg`, `Metal049A.jpg` — ambientCG
(https://ambientcg.com/a/Rubber002, /Fabric061, /Metal049A), лицензия CC0 1.0 Universal; перенесены из
TECHMAP-GRAPHER вместе с редактором.
