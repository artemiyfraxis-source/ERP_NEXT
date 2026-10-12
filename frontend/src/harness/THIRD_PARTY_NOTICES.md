# Сторонний код в `frontend/src/harness`

## TECHMAP-GRAPHER

TECHMAP-GRAPHER © Василий (SaveOurSouls), used with the author's permission, 2026-10-06, commit e63befb;
logic synced to commit 0a456d2 on 2026-10-12 (see «Синхронизация с `0a456d2`» below).

- Источник: `src/Techmap.Client/src/editor`, `src/Techmap.Client/src/component-library` и общие компоненты
  (`InfoHint`, `AnchoredPopover`) на коммите `e63befb` (ветка `codex/integration-m5-22`).
- Первый коммит ветки переноса содержит эти файлы без изменений; все правки ERP — следующими коммитами.
- Не перенесено: сетевые клиенты (сеть редактора — `harnessApi` в `api.ts`, чертёж — `drawing.ts`, библиотека — `library/`), фото/HEIC, аварийные черновики в localStorage и
  журнал восстановления, маршрут изготовления (Route V2) и панели производства.

## Маршрут v3 (`route/`)

TECHMAP-GRAPHER © Василий (SaveOurSouls), used with the author's permission, коммит `0a456d2` (ветка
`codex/integration-m5-22`).

- Источник: `src/Techmap.Client/src/manufacturing-v3/route-v3-model.ts`, `route-v3-graph.ts`, `uml/uml-model.ts`
  (модель времени), `manufacturing/route-source.ts`, `manufacturing/route-drawing-copy.ts` и их тесты.
- Первый коммит переноса содержит эти файлы без изменений (только заголовок с путём источника); правки ERP — следующим.
- Правки ERP: импорты — на порт чертежа ERP; слепок в режиме «источник» хранит только скрытые объекты (чертёж —
  тот, на версии которого построен маршрут), изолированный — свою геометрию; `parseRouteV3` отклоняет слепок с копией
  всего чертежа; из Route V2 взят только тип рисунка карточки; UML принимает любой граф маршрута.
- Не перенесено: экраны маршрута (`RouteV3Panel`, `RouteV3Graph`, рисунок провода), миграция старых копий из
  localStorage (`migrateLegacyRouteDrawingCopy`), Route V2.

## Синхронизация с `0a456d2` (2026-10-12)

TECHMAP-GRAPHER © Василий (SaveOurSouls), used with the author's permission; логика редактора доведена до
коммита `0a456d2` (вершина ветки `codex/integration-m5-22` на 2026-10-12) трёхсторонним слиянием `e63befb` → `0a456d2`
поверх правок ERP.

- Раньше (коммит ERP `47f5ab59`, 2026-10-10) перенесены `7d51a67`, `5eeb02f`, `7aaaf46`, `3df8ce6`, `17914c2`,
  `723ff99`, `d971f0d`: изгибы труб в пучке, транзит провода через разъём, точки соединения выше оболочек, размеры и
  индексы материалов в верхнем слое.
- Теперь: `cf34ec5e` и `e145d2ca` — сцена участка после создания общей трубы (OP) без устаревших изгибов на её оси
  (`editor/physical-scene.ts`); `e47f99e2` — рисунок оконцовок провода в профиле C1 (`wire-blank-artwork.ts`,
  постоянные `artworkWireDiameter`, `artworkJacketAnchor` в `editor/CanvasViewport.tsx`); `e86aa33f` и `11ab6333` —
  совместимые уплотнители серии в модели шаблона v5 (`component-library/template-model-v5.ts`; черновик библиотеки
  их сохраняет, но редактора уплотнителей в ERP пока нет); из `9c159188` — только ядро поля
  (`editor/CanvasViewport.tsx`: оболочки кабелей на фоне, необязательные `dimmedObjectIds` / `onObjectsSave`).
- Добавлен файл: `wire-blank-artwork.test.ts` (проверка из `WireBlankCatalogEditor.test.ts`, перенесена на `renderWireSvg`).
- Не перенесено: оболочка редактора, рабочее место, навигация, панели и инспекторы этих коммитов (Маршрут v3 —
  `manufacturing-v3/`, режим «слепка» в `HarnessDesignEditor` / `HarnessEditorWorkspace`, редактор совместимых
  уплотнителей `CompatibleSealsEditor`, `TemplateSeriesPanelV3`, `E4ArticlePreview`, `E4ConnectorInspector`, их CSS),
  `manufacturing/`, `manufacturing-v2/`, `App.tsx`; иллюстрации `public/route-wire-illustrations/*.svg` — в ERP нет
  маршрутных иллюстраций и каталога заготовок, ссылаться на них некому.

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
