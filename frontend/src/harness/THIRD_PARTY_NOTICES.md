# Сторонний код в `frontend/src/harness`

## TECHMAP-GRAPHER

TECHMAP-GRAPHER © Василий (SaveOurSouls), used with the author's permission, 2026-10-06, commit e63befb.

- Источник: `src/Techmap.Client/src/editor`, `src/Techmap.Client/src/component-library` и общие компоненты
  (`InfoHint`, `AnchoredPopover`) на коммите `e63befb` (ветка `codex/integration-m5-22`).
- Первый коммит ветки переноса содержит эти файлы без изменений; все правки ERP — следующими коммитами.
- Не перенесено: сетевые клиенты (сеть редактора — `harnessApi` в `api.ts`, чертёж — `drawing.ts`, библиотека — `library/`), фото/HEIC, аварийные черновики в localStorage и
  журнал восстановления, маршрут изготовления (Route V2) и панели производства, UML, модель времени.

## Редактор шаблонов библиотеки компонентов (экран «Библиотека компонентов»)

TECHMAP-GRAPHER © Василий (SaveOurSouls), used with the author's permission, 2026-10-06, commit e63befb
(ветка `codex/integration-m5-22`; её вершина на 2026-10-10 — `9cf3b88`, перенос сделан с `e63befb`, как и остальной код).

- Источник: `src/Techmap.Client/src/component-library`: `ComponentLibrary.tsx`, `TemplateCanvasV2.tsx`,
  `TemplateContactsPanelV2`, `TemplateLayersPanelV2`, `TemplateParametersPanelV2`, `TemplateSeriesPanelV3`,
  `E4ConnectorTableEditor`, `E4ArticlePreview`, `DrawingArrayPanel`, `DrawingGeneratorPanel`,
  `DrawingSelectionProperties` и вспомогательные модули с тестами (`article-pattern-expansion`, `drawing-reset`,
  `drawing-selection`, `drawing-viewport`, `drawing-contact-validation`, `image-import`, `image-background`,
  `template-assets`, `template-contact-links-v2`, `library-navigation-controller`).
- Правки ERP: сеть заменена клиентом `api.ts` (`harnessApi`) через `component-library-api.ts`; серверные черновики,
  аварийные черновики в localStorage, автосохранение, удаление серии и загрузка изображений в шаблон убраны —
  правки живут на странице до «Опубликовать версию»; справочники — из снимка ERP; режим «только просмотр»;
  цвета CSS заменены токенами ERP; подписи русские.
- Не перенесено: HEIC (декодер и воркер), `TemplateCanvas` (v1), `library-draft-recovery`, `template-upgrade-v4-draft`,
  сетевой клиент `component-template-api` и его тесты.

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
