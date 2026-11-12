import type { TechOperationTab } from "./api";
import { MATERIAL_TABS, type MaterialTab } from "./materialSections";

/* The tabs of «База данных», one list: the screen (ReferencesPage) draws its tab rows from it and «Поиск по ERP» offers
   each tab as a jump. A light module of its own, so the search does not pull in the whole screen. */

export type MapTab = "suppliers" | "countries";
export type CableTab = "cab_coax" | "cab_wire" | "cab_multi" | "cab_lan";
export type DbTab = "templates" | "connectors" | "gauges" | "cov" | "lug" | TechOperationTab | MapTab | CableTab | MaterialTab;
export type TabDef = { id: DbTab; label: string; base?: string; words?: string };

// Owner 04.10.2026 («сортировка тут вообще не корректная»): groups by what the shop picks from — operations and
// equipment, what goes on a wire end, the wires and cables themselves, materials, the purchase maps.
export const REFERENCE_TAB_GROUPS: ReadonlyArray<{ title: string; tabs: TabDef[] }> = [
  // Owner 29.09.2026: «операции… лучше на карточки», «с оборудованием то же самое». БД.ОП lives inside «Операции»
  // (one card per code, template sheet + the БД.ОП row); БД.ОБ is its own card tab.
  { title: "Техкарты", tabs: [
    { id: "templates", label: "Операции", words: "шаблоны техкарт карточки операций" },
    { id: "ob", label: "Оборудование", base: "ob", words: "станки инструмент аппликаторы" },
  ] },
  { title: "Разъёмы и контакты", tabs: [
    { id: "connectors", label: "Разъёмы", words: "серии каталог разъёмов" },
    { id: "coax", label: "Коаксиальные разъёмы", base: "coax", words: "sma bnc" },
    { id: "ter", label: "Терминалы", base: "ter", words: "контакты обжим" },
    // Owner 26.10.2026: «тип наконечник, а не терминал… в другую БД — наконечники»: строки БД.ТЕР со списком «lug».
    { id: "lug", label: "Наконечники", words: "кольцевые вилочные гильзы" },
    // Owner 15.10.2026: «Материалы» расформированы — уплотнители, заглушки, гильзы и втулки идут на концы проводов.
  ] },
  // Owner 04.10.2026: «объединить провода и кабели и коаксиальные … три вкладки»; 10.10.2026: сечения — отдельной
  // таблицей, у монтажных жила из неё, многожильные — в отдельную вкладку. БД.ПРОВ, the coax D1·D2·D3 table and the coax
  // cards are joined by the server (references/cables.py, cable_structure.py). «Сечения AWG ↔ мм²» is the size table
  // the монтажные wires reference (owner 12.10.2026: it sits in «Соответствия»); «Ø изоляции по маркам» is no tab any
  // more — the generator reads it from the монтажные провода (GET /api/references/wire-dia).
  { title: "Кабели и провода", tabs: [
    { id: "cab_coax", label: "Коаксиальные кабели", words: "rg провода" },
    { id: "cab_wire", label: "Монтажные провода", words: "мс пв" },
    { id: "cab_multi", label: "Многожильные кабели" },
    // 25.10.2026 (владелец): «интернетные вынести вообще в другую вкладку — новая вкладка, новая БД», «ETHERNET».
    { id: "cab_lan", label: "Кабели Ethernet", words: "витая пара lan" },
  ] },
  // БД.МАТ is a working base in SUPPLYMAP (PortalRef.gs REF_BASES_ tiles, filled from purchases), not import.
  // Owner 12.10.2026: оболочки (ТУТ, ТТК, оплётки, гофра) — отдельной таблицей из строк БД.МАТ, с разбором и сортировкой;
  // 15.10.2026: вкладку «Материалы» расформировать — каждая строка БД.МАТ в одной вкладке по назначению (materialSections.ts).
  { title: "Материалы", tabs: [
    { id: "cov", label: "Оболочки", words: "тут ттк оплётка гофра кембрик лента" },
    { id: "mat_use", label: MATERIAL_TABS.mat_use.label, words: "припой флюс материалы" },
  ] },
  { title: "Соответствия", tabs: [
    { id: "suppliers", label: "Домен → поставщик", words: "поставщик домен соответствия" },
    { id: "countries", label: "Изготовитель → страна", words: "производитель страна соответствия" },
    { id: "gauges", label: "Сечения AWG ↔ мм²", words: "сечение гост соответствия" },
  ] },
];

/** The access key of a tab (ID-3): «Операции» and «Разъёмы» — the templates' key, the rest — the reference books'. */
export function referenceTabKey(tab: DbTab): "erptctpl" | "erpref" {
  return tab === "templates" || tab === "connectors" ? "erptctpl" : "erpref";
}
