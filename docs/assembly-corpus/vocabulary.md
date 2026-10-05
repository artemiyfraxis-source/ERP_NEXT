# Словарь операций для ручной сборки

Какие карточки из библиотеки операций можно ставить в `ops[]` ручной сборки (`type: "libraryOp"`, `templateId` —
id карточки, `opNum` — код) и как они соотносятся с типами генератора. Таблица собрана из библиотеки, которую
загружает `backend/app/modules/techcards/seed_library.py`: снимок `backend/data/techcard-library.json` плюс карточки
ERP из `backend/data/techcard-library-erp.json` (всего 108 кодов; карточка с сторонами А/В — одна строка).

## Как читать

- **Код** — код карточки (`opNum`). По нему и по `templateId` движок находит карточку; `templateId` — id вида
  `cut-wire-hand-резка`, он берётся из библиотеки, не придумывается.
- **Название** — русское название операции карточки; если мастер не задал `label`, в ЭТК пойдёт оно.
- **Тип генератора** — `type` операции в конфиге типовых генераторов (`cutWireStrip`, `insTerm`, …), которая
  выбирает эту карточку. Соответствие взято из подсказок `tmplHint` генератора (`frontend/src/tcGenLogic.ts`);
  отдельного справочника «код → тип» в коде нет, поэтому это ориентир, а не контракт. «—» — генератор эту карточку
  сам не ставит (ручные и редкие варианты): её берут только как `libraryOp`.
- **Параметры** — что карточка ждёт сверх общих полей. В текстах библиотеки подстановок `{{…}}` нет, кроме длины
  `[L КД]` у карточек резки трубок; сторона — поле `side` («A» / «B»).

Общие поля любой операции ручной сборки (см. `build-config.schema.json`): `qty` (на изделие), `tPrep` (подготовка
на партию, секунды), `tOp` (секунды на штуку), `params`, `note`, `result` (что операция отдаёт дальше),
`components` (комплектующие на изделие).

Нетиповая операция, которой нет в библиотеке, — `type: "customOp"` с ходом (`steps`) и оснасткой (`tooling`);
карточка для неё не нужна.

Если нужной карточки нет — не выдумывать код: записать нетиповой операцией (`customOp`) и отметить в `lesson.md`,
что нужна карточка.

| Код | Название | Тип генератора | Параметры |
|---|---|---|---|
| `SDR_tin` | Лужение | tin | — |
| `INS_SLEEVE_coax` | Монтаж | coaxInsSleeve | — |
| `INS_TUT` | Монтаж | insTut, coaxInsTut | — |
| `INS_housing_COAX` | Монтаж корпуса | coaxHousing | сторона A/B |
| `INS_terminal_1.00` | Монтаж терминалов | insTerm | сторона A/B |
| `INS_terminal_1.25` | Монтаж терминалов | insTerm | сторона A/B |
| `INS_terminal_1.5` | Монтаж терминалов | insTerm | сторона A/B |
| `INS_terminal_2` | Монтаж терминалов | insTerm | сторона A/B |
| `INS_terminal_2,54-6,3` | Монтаж терминалов | insTerm | сторона A/B |
| `PRS_SLEEVE_coax` | Опрессовка | coaxShield | — |
| `PRS_TERMINAL_auto` | Опрессовка терминалов | prsTerm | сторона A/B |
| `SDR_COAX_pin` | Пайка | coaxPin | сторона A/B |
| `SDR_COAX_pin_R/A` | Пайка | coaxPin | сторона A/B |
| `SDR_COAX_housing` | Пайка экрана | coaxSolderShield | сторона A/B |
| `STRIP_COAX_auto` | Разделка | coaxStrip | сторона A/B |
| `CUT_TUT_short` | Резка | cutTut, coaxCutTut | длина по КД (`[L КД]`) |
| `CUT_WIRE_auto` | Резка | cutWire, coaxCut | — |
| `CUT_WIRE+1strip` | Резка с разделкой | cutWireStrip | — |
| `CUT_WIRE+2strip` | Резка с разделкой | cutWireStrip | — |
| `TWIST` | Свивка | twist | — |
| `HEAT_TYT_short` | Усадка | heatTut, coaxHeat | — |
| `HEAT_TYT_long` | Усадка | heatTut | — |
| `HEAT_TTK_short` | Усадка | heatTut, coaxHeat | — |
| `HEAT_TTK_long` | Усадка | heatTut | — |
| `SDR_DB` | Пайка | solderConn | — |
| `SDR_DH` | Пайка | solderConn | — |
| `SDR_MR10` | Пайка | solderConn | — |
| `SDR_XT30` | Пайка | solderConn | — |
| `SDR_XT60` | Пайка | solderConn | — |
| `SDR_XT90` | Пайка | solderConn | — |
| `SDR_GX12` | Пайка | solderConn | — |
| `SDR_GX16` | Пайка | solderConn | — |
| `SDR_LEMO` | Пайка | solderConn | — |
| `SDR_W_W` | Пайка | splice | — |
| `SDR_AUX` | Пайка | solderConn | — |
| `SDR_RCA` | Пайка | solderConn | — |
| `SDR_крокодилы` | Пайка | — | — |
| `SDR_USB_A_2.0` | Пайка | solderConn | — |
| `SDR_USB_A_3.2` | Пайка | solderConn | — |
| `SDR_USB_C_2.0` | Пайка | solderConn | — |
| `SDR_USB_C_3.2` | Пайка | solderConn | — |
| `PRS_TERMINAL_hand` | Опрессовка терминалов | — | — |
| `PRS_TERMINAL_double` | Опрессовка терминалов | — | — |
| `PRS_TERMINAL_multi` | Опрессовка терминалов | — | — |
| `PRS_HKИ/НВИ/РПИ` | Опрессовка наконечников | prsTerm | — |
| `PRS_НШВИ` | Опрессовка наконечников | prsTerm | — |
| `PRS_ТМЛ` | Опрессовка наконечников | prsTerm | — |
| `PRS_MHF` | Опрессовка | — | — |
| `PRS_MHF_LK` | Опрессовка | — | — |
| `PRS_IDC` | Опрессовка | — | — |
| `PRS_CAP` | Опрессовка | — | — |
| `PRS_COPPER` | Ленточный обжим | — | — |
| `STRIP_COAX_hand` | Разделка | — | — |
| `STRIP_U/UTP_ethernet` | Разделка | stripCable | — |
| `STRIP_F/UTP_ethernet` | Разделка | stripCable | — |
| `STRIP_S/UTP_ethernet` | Разделка | stripCable | — |
| `STRIP_SF/UTP_ethernet` | Разделка | stripCable | — |
| `STRIP_U/FTP_ethernet` | Разделка | stripCable | — |
| `STRIP_F/FTP_ethernet` | Разделка | stripCable | — |
| `STRIP_S/FTP_ethernet` | Разделка | stripCable | — |
| `STRIP_SF/FTP_ethernet` | Разделка | stripCable | — |
| `STRIP_WIRE` | Разделка | stripCable | — |
| `STRIP_CABLE_usb_2.0` | Разделка | stripCable | — |
| `STRIP_CABLE_usb_3.2` | Разделка | stripCable | — |
| `STRIP_CABLE` | Разделка | stripCable | — |
| `STRIP_CABLE_sheld` | Разделка | stripCable | — |
| `CUT_WIRE_hand` | Резка | — | — |
| `CUT_TUT_long` | Резка линейных материалов | — | длина по КД (`[L КД]`) |
| `CUT_COAX+1strip` | Резка с разделкой | cutWireStrip | — |
| `CUT_COAX+2strip` | Резка с разделкой | cutWireStrip | — |
| `CUT_WIRE+1strip+1mark` | Резка с разделкой+ маркировка | — | — |
| `CUT_WIRE+1strip+2mark` | Резка с разделкой+ маркировка | — | — |
| `CUT_WIRE+2strip+1mark` | Резка с разделкой+ маркировка | — | — |
| `CUT_WIRE+2strip+2mark` | Резка с разделкой+ маркировка | — | — |
| `CUT_WIRE+1crimp` | Автомат резка + опрессовка | cutWireCrimp | — |
| `CUT_WIRE+2crimp` | Автомат резка + опрессовка | cutWireCrimp | — |
| `CUT_NEYLON` | Резка линейных материалов | — | длина по КД (`[L КД]`) |
| `CUT_ПМЛ` | Резка линейных материалов | — | длина по КД (`[L КД]`) |
| `CUT_гофра` | Резка линейных материалов | — | длина по КД (`[L КД]`) |
| `INS_RJ_cat5` | Монтаж RJ | insTerm | — |
| `INS_RJ_cat6/7` | Монтаж RJ | insTerm | — |
| `INS_housing_DB/DH/СНП` | Монтаж корпуса | insHousing | — |
| `INS_housing_GX` | Монтаж корпуса | insHousing | — |
| `INS_housing_USB` | Монтаж корпуса | insHousing | — |
| `INS_NEYLON` | Монтаж | — | — |
| `INS_ПМЛ` | Монтаж | — | — |
| `INS_MARK` | Монтаж | — | — |
| `INS_CAP` | Монтаж | coaxCapScrew | — |
| `INS_lock` | Монтаж | — | — |
| `INS_гофра_разрез` | Монтаж | — | — |
| `INS_гофра_неразрез` | Монтаж | — | — |
| `INS_стяжки` | Монтаж | — | — |
| `INS_tape` | Монтаж | — | — |
| `US_WELD` | УЗ сварка | — | — |
| `MOLD_hand` | Литье пластика | — | — |
| `MOLD_auto` | Литье пластика | — | — |
| `PLASM` | Литье пластика | — | — |
| `INS_WRAP` | Монтаж | insWrap | — |
| `INS_MARK_HARNESS` | Монтаж маркировки | insMark | — |
| `PRS_CAP_COAX` | Опрессовка крышки коакс | coaxCapCrimp | — |
| `INS_BRAID` | Монтаж оплётки | insWrap | — |
| `SEAL` | Герметизация | seal | — |
| `INS_END_CAP` | Заделка свободных концов | endCap | — |
| `PREP_FOLD` | Складывание жилы | foldCore | — |
| `SDR_BANANA` | Пайка | — | — |
| `SDR_PCB` | Пайка на плату | — | — |
| `SDR_НК` | Пайка | — | — |
| `CUT_WIRE+1crimp+tin` | Автомат резка + опрессовка + лужение | cutWireCrimp | — |
