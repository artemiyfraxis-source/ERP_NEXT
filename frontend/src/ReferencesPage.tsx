import { useEffect, useLayoutEffect, useMemo, useState, type ReactNode } from "react";
import { DataTable, type DataColumn, type DataPreset } from "./DataTable";
import { hasAccess } from "./navigation";
import { EquipmentCards } from "./EquipmentCards";
import { EquipmentForm } from "./EquipmentForm";
import { applicatorTokens } from "./equipmentModel";
import { TechcardsPage } from "./TechcardsPage";
import { ConnectorGallery } from "./ConnectorGallery";
import { CrimpCell, CRIMP_COLUMN_TITLE, crimpText } from "./CrimpCell";
import { CableTable, kindSelect } from "./CableTables";
import { ColumnViewsProvider } from "./ColumnViews";
import { AddColumnButton, fieldCellEdit, filledIn, ownCellEdit, useColumnRemoval } from "./CustomColumn";
import { GaugeTable, HookupWireTable } from "./CableStructure";
import { CoveringsTable } from "./Coverings";
import { PageWindow } from "./PageWindow";
import { TerminalCard, type TerminalRow } from "./TerminalCard";
import { SortHint, firstNumber } from "./refSort";
import { NO_SERIES, makerOrder, seriesCaption, sortBySeries, terminalGroups } from "./terminalSeries";
import { COAX_CARD_ONLY, CoaxConnectors } from "./CoaxConnectors";
import { StockLinksJournal } from "./StockLinksJournal";
import { ReferenceRoundtrip } from "./ReferenceRoundtrip";
import {
  MATERIAL_TABS, isMaterialTab, materialCounts, materialRows, type MaterialTab,
} from "./materialSections";
import { operationCardCount } from "./TechcardsPage";
import { cleanHeader, fieldTitle, headerKey, headerLabel, terFieldHint, valueLabel } from "./referenceLabels";
import {
  failureText,
  openReadVisit,
  referencesApi,
  techcardsApi,
  type BasesResult,
  type CableKind,
  type CablesResult,
  type ConnectorSeries,
  type Principal,
  type ReferenceMaps,
  type ReferencePair,
  type TechOperationRecord,
  type TechOperationResult,
  type TechOperationTab,
  type TerKind,
} from "./api";
import { LUG_SORT_HINT, lugFamily, lugRows, mergeLugColumns } from "./lugList";

/* «База данных» (owner, 29.09.2026): one section with sheet-like tabs instead of «Справочник» + «Шаблоны техкарт».
   Every tab is the shared DataTable — search, column filters, sort, column chooser, Excel — so the shop's
   references read like one database. Project techcards (ЭТК) stay with the projects. */

type MapTab = "suppliers" | "countries";
type CableTab = "cab_coax" | "cab_wire" | "cab_multi" | "cab_lan";
export type DbTab = "templates" | "connectors" | "gauges" | "cov" | "lug" | TechOperationTab | MapTab | CableTab | MaterialTab;
type TabDef = { id: DbTab; label: string; base?: string };

const EMPTY_MAPS: ReferenceMaps = { suppliers: [], countries: [], gost_awg: [] };
// Owner 04.10.2026 («сортировка тут вообще не корректная»): groups by what the shop picks from — operations and
// equipment, what goes on a wire end, the wires and cables themselves, materials, the purchase maps.
const GROUPS: Array<{ title: string; tabs: TabDef[] }> = [
  // Owner 29.09.2026: «операции… лучше на карточки», «с оборудованием то же самое». БД.ОП lives inside «Операции»
  // (one card per code, template sheet + the БД.ОП row); БД.ОБ is its own card tab.
  { title: "Техкарты", tabs: [
    { id: "templates", label: "Операции" },
    { id: "ob", label: "Оборудование", base: "ob" },
  ] },
  { title: "Разъёмы и контакты", tabs: [
    { id: "connectors", label: "Разъёмы" },
    { id: "coax", label: "Коаксиальные разъёмы", base: "coax" },
    { id: "ter", label: "Терминалы", base: "ter" },
    // Owner 26.10.2026: «тип наконечник, а не терминал… в другую БД — наконечники»: строки БД.ТЕР со списком «lug».
    { id: "lug", label: "Наконечники" },
    // Owner 15.10.2026: «Материалы» расформированы — уплотнители, заглушки, гильзы и втулки идут на концы проводов.
  ] },
  // Owner 04.10.2026: «объединить провода и кабели и коаксиальные … три вкладки»; 10.10.2026: сечения — отдельной
  // таблицей, у монтажных жила из неё, многожильные — в отдельную вкладку. БД.ПРОВ, the coax D1·D2·D3 table and the coax
  // cards are joined by the server (references/cables.py, cable_structure.py). «Сечения AWG ↔ мм²» is the size table
  // the монтажные wires reference (owner 12.10.2026: it sits in «Соответствия»); «Ø изоляции по маркам» is no tab any
  // more — the generator reads it from the монтажные провода (GET /api/references/wire-dia).
  { title: "Кабели и провода", tabs: [
    { id: "cab_coax", label: "Коаксиальные кабели" },
    { id: "cab_wire", label: "Монтажные провода" },
    { id: "cab_multi", label: "Многожильные кабели" },
    // 25.10.2026 (владелец): «интернетные вынести вообще в другую вкладку — новая вкладка, новая БД», «ETHERNET».
    { id: "cab_lan", label: "Кабели Ethernet" },
  ] },
  // БД.МАТ is a working base in SUPPLYMAP (PortalRef.gs REF_BASES_ tiles, filled from purchases), not import.
  // Owner 12.10.2026: оболочки (ТУТ, ТТК, оплётки, гофра) — отдельной таблицей из строк БД.МАТ, с разбором и сортировкой;
  // 15.10.2026: вкладку «Материалы» расформировать — каждая строка БД.МАТ в одной вкладке по назначению (materialSections.ts).
  { title: "Материалы", tabs: [
    { id: "cov", label: "Оболочки" },
    { id: "mat_use", label: MATERIAL_TABS.mat_use.label },
  ] },
  { title: "Соответствия", tabs: [
    { id: "suppliers", label: "Домен → поставщик" },
    { id: "countries", label: "Изготовитель → страна" },
    { id: "gauges", label: "Сечения AWG ↔ мм²" },
  ] },
];
const CABLE_TABS: Record<CableTab, CableKind> = { cab_coax: "coax", cab_wire: "wire", cab_multi: "multi", cab_lan: "lan" };
// Keys of the tabs these replaced (saved tab, `initialTab`): «Провода и кабели» → монтажные, «Коаксиальные кабели:
// D1 · D2 · D3» → коаксиальные кабели; «Материалы» (15.10.2026) → «Оболочки», куда ушла бóльшая часть строк.
// «Прочие кабели» закрыты 20.10.2026: сохранённая вкладка ведёт в многожильные.
const TAB_ALIASES: Partial<Record<string, DbTab>> = { cab_other: "cab_multi", prov: "cab_wire", kab_coax: "cab_coax", kab_awg: "cab_wire", mat: "cov", mat_fix: "mat_use", mat_seal: "mat_use", mat_sleeve: "mat_use" };
const MAP_TABS: Record<MapTab, { title: string; keyLabel: string; valueLabel: string }> = {
  suppliers: { title: "Домен → поставщик", keyLabel: "Домен или ссылка", valueLabel: "Поставщик" },
  countries: { title: "Изготовитель → страна", keyLabel: "Изготовитель", valueLabel: "Страна" },
};
// СПР.КАБ — только просмотр (как в портале SUPPLYMAP): таблица как на листе, без правки и «+ Запись».
const VIEW_ONLY_TABS: DbTab[] = ["kab_coax"];
const TAB_STORAGE = "erp-db-tab";
// Record fields are SUPPLYMAP DB_SCHEMA keys (TcOpsDb.gs, backend technology._TECH_SCHEMA); the sheet's own header
// is what people know, so the column shows that. Unknown keys (БД.ПРОВ, БД.МАТ, СПР.КАБ) are headers already.


function mergeTerminalColumns(columns: DataColumn<TechOperationRecord>[], fields: string[]): DataColumn<TechOperationRecord>[] {
  // Колонки бывают под заголовком листа («Высота обжима проводника , мм») или под ключом схемы (terCrimpHeight).
  const find = (test: (key: string) => boolean) => fields.find((field) => test(headerKey(fieldTitle(field))) || test(field.toLowerCase()));
  const conductor = find((key) => key.startsWith("высота обжима проводника"));
  const insulation = find((key) => key.startsWith("высота обжима изоляции"));
  const pull = find((key) => key.startsWith("усилие обрыва") && !key.startsWith("усилие обрыва до"));
  const pullMax = find((key) => key.startsWith("усилие обрыва до") || key === "terpullforcemax");
  const awgFrom = find((key) => key === "от awg" || key === "awgfrom");
  const awgTo = find((key) => key === "до awg" || key === "awgto");
  const crimpFields = [conductor, insulation, pull, pullMax].filter((field): field is string => Boolean(field));
  const get = (record: TechOperationRecord, field?: string) => (field ? record.fields[field] ?? "" : "");
  const pullText = (record: TechOperationRecord) => {
    const low = get(record, pull).trim();
    const high = get(record, pullMax).trim();
    return high && low && high !== low && !low.includes(":") ? `${low}–${high}` : low || high;
  };
  const crimpValues = (record: TechOperationRecord) => ({ conductor: get(record, conductor), insulation: get(record, insulation), pull: pullText(record) });
  const awgText = (record: TechOperationRecord) => {
    const from = get(record, awgFrom).trim();
    const to = get(record, awgTo).trim();
    return from && to && from !== to ? `${from}–${to}` : from || to;
  };
  const merged: Record<string, DataColumn<TechOperationRecord>> = {};
  if (crimpFields.length > 0) merged[crimpFields[0]] = {
    // the four columns of the crimp table (`.crimp-row`: 10ch 11ch 13ch 10ch and the gaps): its width is set, not
    // measured from the text (the list in blocks measures texts, this cell draws a table)
    key: "__crimp", title: CRIMP_COLUMN_TITLE, width: 384, value: (record) => crimpText(crimpValues(record)),
    render: (record) => <CrimpCell values={crimpValues(record)} />,
  };
  if (awgFrom || awgTo) merged[(awgFrom ?? awgTo) as string] = { key: "__awg", title: "AWG", value: awgText, render: (record) => awgText(record) || "—" };
  const folded = new Set([...crimpFields, awgFrom, awgTo].filter(Boolean) as string[]);
  // Обжим и AWG стоят сразу за артикулом контакта: на планшете (~1400 px) таблица шире экрана, и всё, что правее
  // «Длины зачистки», уходит за край.
  const moved = Object.values(merged);
  const rest = columns.map((column) => (folded.has(column.key) ? { ...column, hidden: true } : column));
  const anchor = rest.findIndex((column) => headerKey(column.title).startsWith("артикул контакта"));
  if (moved.length === 0) return rest;
  if (anchor < 0) return [...moved, ...rest];
  return [...rest.slice(0, anchor + 1), ...moved, ...rest.slice(anchor + 1)];
}

// Заголовки листа, которых нет в карте: без переносов строк, русские слова вместо английских (referenceLabels.ts).
// Столбцы БД.ТЕР, нужные редко (ссылка, источник, материал, диаметры изоляции, пневма): в «Колонках», по умолчанию скрыты —
// остальные (артикулы, шаг, обжим) укладываются в ширину ноутбука (аудит БД-3).
// «Тип разъёма» («Обжимной контакт» / «Обжимная клемма») скрыт тоже: строки и так идут группами по серии, а на 16"
// (1536 px) без него «Обжим» виден без прокрутки вбок (16.10).
const TER_RARE = new Set(["тип разъема", "пневма автомат", "отр", "диаметр изоляции от, мм", "диаметр изоляции до, мм", "материал контакта", "ссылка на datasheet на сайте производителя", "источник высоты обжима"]);
// Длинные списки серий («I-DS1071-01-SCB01, I-DS1071-01-SCB02») и наименования — узкой колонкой с «…», целиком в подсказке.
const TER_NARROW = new Set(["наименование", "серия"]);
// «Наконечники»: материал («Медь / гальваническое лужение») — узкой колонкой с «…»; семейство — заголовок блока.
const LUG_NARROW = new Set(["материал контакта", "termaterial"]);
const LUG_FAMILY_KEYS = new Set(["product name", "tercomponent"]);

/** Ссылка на даташит в ячейке — «открыть» (как у кабелей); не ссылка — текст как есть. */
function DatasheetLink({ href, name }: { href: string; name: string }) {
  const link = href.trim();
  if (!link) return <span className="cell-empty">—</span>;
  return /^https?:\/\//i.test(link)
    ? <a href={link} target="_blank" rel="noreferrer" title={link} aria-label={`Открыть даташит ${name}`} onClick={(event) => event.stopPropagation()}>открыть</a>
    : <span className="db-clip" title={link}>{link}</span>;
}
// БД.КОАКС (owner 29.09.2026): Ø belong to the cable (СПР.КАБ · коаксиал), the connector row keeps only lengths;
// D1–D3 come from СПР.КАБ by «Провод», as the sheet's D1 formula does. Same key as the generator's coaxCableDiaLookup_.
const CABLE_DIAMETERS = ["D1", "D2", "D3"];
const cableKey = (value: string) => String(value || "").toLowerCase().replace(/[\s-]/g, "");
type CableDia = Record<string, Record<string, string>>;
// Таблицы записей с правкой прямо в ячейках (владелец 14.10: «как у коаксиальных кабелей, по всей базе»). Ячейка не
// правится, когда это ключ записи (по нему строка называется и ищется), выбор из списка или то, что пишет сама ERP:
// такие поля — в окне записи / карточке контакта.
const NOT_IN_CELL: Partial<Record<TechOperationTab, Set<string>>> = {
  ter: new Set(["тип разъема", "производитель", "series", "серия", "артикул контакта (reel)", "тип контакта",
    "termanufacturer", "terseries", "terarticle", "tertype", "tergender"]),
  mat: new Set(["наименование", "группа", "закупок", "цена посл., ₽ без ндс"]),
};
// Таблицы, куда владелец добавляет свои столбцы («+ Столбец»; бэкенд CUSTOM_COLUMN_TABS): «Коаксиальные разъёмы» — своим экраном.
const OWN_COLUMN_TABS: TechOperationTab[] = ["ter", "mat"];
const BOOLEAN = /^(true|false)$/i;
const natural = (a: string, b: string) => a.localeCompare(b, "ru", { numeric: true, sensitivity: "base" });
const NO_GROUP = "Без группы";
// Поиск таблиц записей — и по тому, что не колонка: полное имя записи, все поля, карточка контакта (артикул BAG).
const recordSearchText = (record: TechOperationRecord) => [record.display_text, ...Object.values(record.fields),
  ...Object.values((record as TerminalRow).card ?? {})];

function isMapTab(tab: DbTab): tab is MapTab {
  return tab in MAP_TABS;
}

function isCableTab(tab: DbTab): tab is CableTab {
  return tab in CABLE_TABS;
}

function savedTab(): string {
  try { return window.localStorage.getItem(TAB_STORAGE) ?? ""; } catch { return ""; }
}

export function ReferencesPage(props: { principal: Principal; initialTab?: DbTab }) {
  // One download of each list per visit (api.ts `visitRead`): the tab counts, the tab and the screens inside it share
  // the reads; a write forgets them. A layout effect: it runs before the tabs' own effects ask for their lists.
  useLayoutEffect(() => openReadVisit(), []);
  return <ColumnViewsProvider enabled={hasAccess(props.principal.permissions, "erpref")}><ReferencesPageBody {...props} /></ColumnViewsProvider>;
}

function ReferencesPageBody({ principal, initialTab }: { principal: Principal; initialTab?: DbTab }) {
  // Tabs by their own keys (ID-3): templates — erptctpl, the rest — erpref, as the two sections were.
  const canTemplates = hasAccess(principal.permissions, "erptctpl");
  const canRefs = hasAccess(principal.permissions, "erpref");
  const groups = useMemo(() => GROUPS
    .map((group) => ({ ...group, tabs: group.tabs.filter((tab) => tab.id === "templates" || tab.id === "connectors" ? canTemplates : canRefs) }))
    .filter((group) => group.tabs.length > 0), [canTemplates, canRefs]);
  const allowed = groups.flatMap((group) => group.tabs.map((tab) => tab.id));
  const [tab, setTab] = useState<DbTab>(() => {
    const raw = initialTab ?? savedTab();
    const wanted = TAB_ALIASES[raw] ?? raw;
    return (allowed.find((id) => id === wanted) ?? allowed[0] ?? "ter") as DbTab;
  });
  const [maps, setMaps] = useState<ReferenceMaps>(EMPTY_MAPS);
  const [savedMaps, setSavedMaps] = useState("");
  const [mapsFailed, setMapsFailed] = useState(false);
  const [tech, setTech] = useState<TechOperationResult>({ metadata: null, records: [] });
  const [cableDia, setCableDia] = useState<CableDia>({});
  const [obOps, setObOps] = useState<TechOperationRecord[]>([]);
  // БД.ТЕР «Аппликатор»: code → the БД.ОБ record that owns it (a link to its card); the card to open after the jump.
  const [toolOwners, setToolOwners] = useState<Record<string, string>>({});
  // «Терминалы» по сериям: серии каталога разъёмов с их артикулами БД.ТЕР (как «Разъёмы»); без доступа — пусто.
  const [terSeries, setTerSeries] = useState<ConnectorSeries[]>([]);
  const [focusTool, setFocusTool] = useState("");
  // «Терминалы»: the contact card open beside the list; the pick lives in `?terminal=` (a reload reopens it).
  const [terminalId, setTerminalId] = useState(() => {
    try { return new URLSearchParams(window.location.search).get("terminal") ?? ""; } catch { return ""; }
  });
  const [bases, setBases] = useState<BasesResult>({ bases: [] });
  const [cables, setCables] = useState<CablesResult | null>(null);
  // Вкладка, чьи записи уже пришли: таблица строится по готовым колонкам (иначе «скрыты по умолчанию» не сработает).
  const [techReady, setTechReady] = useState("");
  const [opCount, setOpCount] = useState<number | null>(null);
  const [connectorCount, setConnectorCount] = useState<number | null>(null);
  const [coveringCount, setCoveringCount] = useState<number | null>(null);
  // БД.МАТ split into tabs: the ids «Оболочки» holds (the server's classifier) and the counts per tab.
  const [coverIds, setCoverIds] = useState<Set<string> | null>(null);
  const [matCounts, setMatCounts] = useState<Record<MaterialTab | "cov", number> | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [stockLog, setStockLog] = useState(false);
  // `terKind` — список строки БД.ТЕР в окне правки («Терминалы» / «Наконечники»): смена переносит строку.
  const [editing, setEditing] = useState<{ id: string | null; values: Record<string, string>; terKind?: TerKind } | null>(null);
  const matTab = isMaterialTab(tab) ? tab : null;
  const techTab: TechOperationTab | null = matTab ? "mat" : tab === "lug" ? "ter"
    : !isMapTab(tab) && !isCableTab(tab) && !isMaterialTab(tab) && tab !== "templates" && tab !== "connectors" && tab !== "gauges" && tab !== "cov" ? tab : null;
  // БД.ТЕР двумя списками: «Терминалы» — обжимные контакты (contact), «Наконечники» — lug.
  const terKind: TerKind | null = tab === "lug" ? "lug" : tab === "ter" ? "contact" : null;
  const lugTab = terKind === "lug";
  const contactTab = terKind === "contact";
  // Ключ загруженного списка: вкладка БД и, у БД.ТЕР, её список («ter:lug»).
  const techKey = techTab ? `${techTab}${terKind ? `:${terKind}` : ""}` : "";
  const cableKind = isCableTab(tab) ? CABLE_TABS[tab] : null;
  const viewOnly = VIEW_ONLY_TABS.includes(tab);
  // ID-3: the `erpref` section decides (PortalRef.gs portalSectionEditGuard_), not the role.
  const canEdit = principal.permissions.erpref === "edit" && !viewOnly;
  // Maps (Portal.gs portalSaveReferences): supplier/country — the buyer's (portalCanWork_).
  const canEditSuppliers = Boolean(maps.can_edit?.suppliers);
  const canSaveMaps = canEditSuppliers;
  const mapsPayload = JSON.stringify({ suppliers: maps.suppliers, countries: maps.countries, gost_awg: maps.gost_awg });
  const mapsDirty = savedMaps !== "" && mapsPayload !== savedMaps;

  function applyMaps(next: ReferenceMaps) {
    setMaps(next);
    setSavedMaps(JSON.stringify({ suppliers: next.suppliers, countries: next.countries, gost_awg: next.gost_awg }));
  }

  async function loadTech(next: TechOperationTab, kind: TerKind | null = next === "ter" ? terKind : null) {
    setError("");
    try {
      // БД.КОАКС: the cables' D1–D3 are asked at once with the rows, not after them.
      const coaxCables = next === "coax"
        ? referencesApi.techOperations("kab_coax", "", 5000).catch(() => ({ records: [] as TechOperationRecord[] })) : null;
      // «Терминалы»: серии приходят вместе со строками — группы не перестраиваются на глазах.
      const [records, series] = await Promise.all([
        referencesApi.techOperations(next, "", 5000, kind ?? undefined),
        next === "ter" && kind !== "lug" ? techcardsApi.connectorSeries().then((result) => result.series, () => [] as ConnectorSeries[]) : Promise.resolve(null),
      ]);
      setTech(records);
      if (series) setTerSeries(series);
      if (next === "ter") {
        void referencesApi.crimpCodes().then((result) => setToolOwners(result.owners)).catch(() => setToolOwners({}));
      }
      if (next === "ob") {
        void referencesApi.techOperations("op", "", 5000).then((ops) => setObOps(ops.records)).catch(() => setObOps([]));
      }
      if (next === "mat") void loadMaterialIndex();
      if (coaxCables) {
        const cables = await coaxCables;
        setCableDia(Object.fromEntries(cables.records.map((record) => [cableKey(Object.values(record.fields)[0] ?? ""), record.fields])));
      }
    } catch (reason) {
      setTech({ metadata: null, records: [] });
      setError(reason instanceof Error ? reason.message : "Не удалось загрузить базу техопераций");
    } finally {
      setTechReady(`${next}${kind ? `:${kind}` : ""}`);
    }
  }

  /** «Оболочки» ids and the per-tab counts of БД.МАТ (a failed call leaves the tabs without numbers). */
  async function loadMaterialIndex() {
    try {
      const [covered, mat] = await Promise.all([referencesApi.coverings(), referencesApi.techOperations("mat", "", 5000)]);
      const ids = new Set(covered.rows.map((row) => row.id));
      setCoverIds(ids);
      setCoveringCount(covered.rows.length);
      setMatCounts(materialCounts(mat.records, ids));
    } catch {
      setCoverIds((current) => current ?? new Set());
      setMatCounts(null);
    }
  }

  function loadBases() {
    // Counts on the tabs are a summary; a failed call leaves the tabs without numbers.
    referencesApi.bases().then(setBases).catch(() => setBases({ bases: [] }));
  }

  async function loadCables() {
    try {
      setCables(await referencesApi.cables());
    } catch (reason) {
      setCables({ columns: [], counts: { coax: 0, wire: 0, multi: 0, lan: 0 }, rows: [], gauges: [] });
      setError(failureText(reason, "Не удалось загрузить кабели и провода"));
    }
  }

  async function cablesSaved(message: string) {
    if (message) { setError(""); setNotice(message); }
    await loadCables();
    loadBases();
  }

  useEffect(() => {
    if (canRefs) {
      referencesApi.maps().then(applyMaps).catch((reason) => { setMapsFailed(true); setError(reason instanceof Error ? reason.message : "Не удалось загрузить справочники"); });
      loadBases();
      if (!isMaterialTab(tab)) void loadMaterialIndex();
    }
    if (canTemplates) {
      // «Операции»: one card per operation code — templates plus БД.ОП codes without one (as the tab builds them).
      Promise.all([
        techcardsApi.templates(),
        canRefs ? referencesApi.techOperations("op", "", 5000) : Promise.resolve({ records: [] as TechOperationRecord[] }),
      ]).then(([library, ops]) => setOpCount(operationCardCount(library.items, ops.records))).catch(() => setOpCount(null));
      techcardsApi.connectorFamilies().then((result) => setConnectorCount(result.series_count ?? result.items.length)).catch(() => setConnectorCount(null));
    }
  }, [canRefs, canTemplates]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    try { window.localStorage.setItem(TAB_STORAGE, tab); } catch { /* per-viewer convenience only */ }
    setEditing(null);
    setNotice("");
    if (techTab) void loadTech(techTab);
    if ((cableKind || tab === "gauges" || tab === "coax") && cables === null) void loadCables();
  }, [tab]); // eslint-disable-line react-hooks/exhaustive-deps

  function count(def: TabDef): number | null {
    // no number until the maps came: «0» while loading read as an empty map
    if (isMapTab(def.id)) return canRefs && savedMaps !== "" ? maps[def.id].length : null;
    if (def.id === "templates") return opCount;
    if (def.id === "connectors") return connectorCount;
    if (def.id === "cov") return coveringCount ?? matCounts?.cov ?? null;
    if (isMaterialTab(def.id)) return matCounts?.[def.id] ?? null;
    if (isCableTab(def.id)) return (cables?.counts ?? bases.cables)?.[CABLE_TABS[def.id]] ?? null;
    if (def.id === "gauges") return cables?.gauges?.length ?? bases.gauges ?? null;
    // the open tab counts its rows once they came (before — the summary's number, not «0»)
    if (def.id === tab && techTab && techReady === techKey) return tech.records.length;
    if (def.id === "lug") return bases.ter_kinds?.lug ?? null;
    if (def.id === "ter" && bases.ter_kinds) return bases.ter_kinds.contact;
    return bases.tabs?.[def.id as TechOperationTab] ?? bases.bases.find((base) => base.id === def.base)?.count ?? null;
  }

  async function saveMaps() {
    if (busy) return;
    setBusy(true); setError(""); setNotice("");
    try {
      applyMaps(await referencesApi.saveMaps({ suppliers: maps.suppliers, countries: maps.countries, gost_awg: maps.gost_awg }));
      setNotice("Справочники сохранены целиком");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Справочники не сохранены");
    } finally {
      setBusy(false);
    }
  }

  async function saveRow() {
    if (!editing || busy || !techTab) return;
    setBusy(true); setError(""); setNotice("");
    try {
      let moved: TerKind | null = null;
      if (editing.id === null) {
        await referencesApi.createRecord(techTab, editing.values, undefined, terKind ?? undefined);
      } else {
        const original = tech.records.find((record) => record.id === editing.id);
        const edits = Object.entries(editing.values)
          .filter(([field, value]) => original && original.fields[field] !== value)
          .map(([field, value]) => ({ field, old: original?.fields[field] ?? "", value }));
        if (edits.length) await referencesApi.editRecord(editing.id, edits);
        // «Список»: строка БД.ТЕР перенесена в «Терминалы» / «Наконечники» (поля не меняются)
        if (editing.terKind && original?.ter_kind && editing.terKind !== original.ter_kind) {
          await referencesApi.moveTerminal(editing.id, editing.terKind);
          moved = editing.terKind;
        }
      }
      setEditing(null);
      setNotice(moved ? `Запись перенесена в «${TER_KIND_LABELS[moved]}»` : "Запись сохранена");
      if (moved) { openTerminal(""); loadBases(); }
      await loadTech(techTab);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Запись не сохранена");
    } finally {
      setBusy(false);
    }
  }

  const fieldColumns = tech.columns?.length ? tech.columns : Object.keys(tech.records[0]?.fields ?? {});
  const ownColumns = tech.custom_columns ?? [];
  // «Удалить столбец» своих столбцов: окно подтверждения, после удаления таблица перечитывается.
  const techRemoval = useColumnRemoval(techTab ?? "mat", canEdit, (name) => filledIn(tech.records, name), async (name) => {
    if (techTab) await loadTech(techTab);
    setError(""); setNotice(`Столбец «${name}» удалён`);
  });
  // Столбцы БД.ПРОВ у каждого списка свои: «+ Столбец» и «Удалить столбец» работают с тем списком, что открыт.
  const cableRemoval = useColumnRemoval("prov", principal.permissions.erpref === "edit",
    (name) => filledIn((cables?.rows ?? []).filter((row) => row.kind === cableKind).map((row) => row.record), name),
    (name) => cablesSaved(`Столбец «${name}» удалён`), cableKind ?? undefined);
  const columnButton = principal.permissions.erpref === "edit" && cableKind
    ? <AddColumnButton tab="prov" cableKind={cableKind} onAdded={(name) => cablesSaved(`Столбец «${name}» добавлен`)} /> : undefined;
  const coaxTab = techTab === "coax";
  // The connector's cable Ø (its row in «Коаксиальные кабели») — the connectors' default order is by it.
  const wireDiameter = useMemo(() => {
    const byKey = new Map((cables?.rows ?? []).filter((row) => row.kind === "coax")
      .map((row) => [cableKey(row.cable), firstNumber(row.record?.fields["Наружный диаметр, мм"])] as const));
    return (wire: string) => byKey.get(cableKey(wire)) ?? null;
  }, [cables]);
  const cableOf = (record: TechOperationRecord) => cableDia[cableKey(record.fields["Провод"] ?? record.fields.coaxWire ?? "")];
  // Edit form: the sheet's columns only (the server moves the schema values derived from them); on БД.КОАКС without
  // the cable's Ø — they are edited in «Коаксиальные кабели» — but with the cells removed from the table 25.10.2026
  // the row has (пол, исполнение, примечание, источник…: no column, still the row's own and editable).
  const editValues = (record: TechOperationRecord) => Object.fromEntries([...fieldColumns, ...(coaxTab ? COAX_CARD_ONLY : [])]
    .filter((key, index, all) => all.indexOf(key) === index && key in record.fields && !(coaxTab && CABLE_DIAMETERS.includes(key)))
    .map((key) => [key, record.fields[key]]));
  // «Материалы» по назначению: столбцы, пустые во всей вкладке (у стяжек — «Размер до», «Коэф. усадки»…), скрыты по
  // умолчанию — они остаются в «Колонках».
  const filledInTab = useMemo(() => new Set(matTab && coverIds ? materialRows(tech.records, coverIds, matTab)
    .flatMap((record) => Object.entries(record.fields).filter(([, value]) => String(value ?? "").trim()).map(([key]) => key)) : []),
  [tech.records, matTab, coverIds]);
  const sheetColumns: DataColumn<TechOperationRecord>[] = [
    // Short cells (Ø, AWG, мм) stay narrow so a wide sheet like СПР.КАБ fits; long text wraps; numbers sort as numbers.
    ...fieldColumns.map((field, index): DataColumn<TechOperationRecord> => {
      if (coaxTab && CABLE_DIAMETERS.includes(field)) return {
        key: field, title: `${field} кабеля`, numeric: true,
        value: (record) => cableOf(record)?.[field] ?? "",
        render: (record) => cableOf(record)
          ? <span title={`Из «Коаксиальные кабели» по кабелю «${record.fields["Провод"] ?? ""}»`}>{cableOf(record)?.[field] || "—"}</span>
          : (record.fields["Провод"] ?? record.fields.coaxWire ?? "").trim()
            ? <span className="muted" title={`У кабеля «${record.fields["Провод"] ?? record.fields.coaxWire ?? ""}» нет D1–D3 — задайте их во вкладке «Коаксиальные кабели»`}>нет у кабеля</span>
            : <span className="muted" title="У разъёма не указан кабель («Провод»)">—</span>,
      };
      if (techTab === "ter" && headerKey(fieldTitle(field)) === "аппликатор") return {
        key: field, title: fieldTitle(field), value: (record) => record.fields[field] ?? "",
        edit: canEdit ? fieldCellEdit<TechOperationRecord>(field, (record) => record) : undefined,
        // A tool code with a БД.ОБ record is a link to its card («что обжимает» lives there).
        render: (record) => {
          const text = record.fields[field] ?? "";
          const owned = applicatorTokens(text).filter((token) => toolOwners[token.code]);
          if (!owned.length) return text;
          return <span className="db-clip" title={text} onClick={(event) => event.stopPropagation()}>{text.split(/([/,;\s]+)/).map((part, index) => {
            const owner = toolOwners[part.toUpperCase()];
            return owner ? <button key={index} type="button" className="eq-link tnum" title={`Открыть инструмент ${part.toUpperCase()}`}
              onClick={() => { setFocusTool(owner); setTab("ob"); }}>{part}</button> : part;
          })}</span>;
        },
      };
      const shown = (record: TechOperationRecord) => valueLabel(field, record.fields[field] ?? "");
      const values = tech.records.map((record) => record.fields[field] ?? "").filter((value) => value && value !== "—");
      const long = values.some((value) => value.length > 40);
      const narrow = (contactTab && TER_NARROW.has(headerKey(fieldTitle(field)))) || (lugTab && LUG_NARROW.has(headerKey(field)));
      // «Наконечники»: даташит — «открыть», как у кабелей (длинная ссылка не растягивает таблицу на 16").
      if (lugTab && headerKey(field).startsWith("ссылка на datasheet")) return {
        key: field, title: fieldTitle(field), value: (record) => record.fields[field] ?? "",
        edit: canEdit ? fieldCellEdit<TechOperationRecord>(field, (record) => record) : undefined,
        render: (record) => <DatasheetLink href={record.fields[field] ?? ""} name={record.display_text} />,
      };
      const own = ownColumns.includes(field);
      // Да/нет листа (TRUE/FALSE) — выбор, а не текст: правится в окне записи. Первая колонка — ручка строки: щелчок по ней
      // открывает запись (карточку контакта), поэтому она не правится в ячейке.
      const fixed = techTab ? NOT_IN_CELL[techTab] : undefined;
      const inCell = own || (Boolean(fixed) && index > 0 && !fixed?.has(headerKey(field))
        && !(values.length > 0 && values.every((value) => BOOLEAN.test(value.trim()))));
      return {
        key: field, title: fieldTitle(field), value: shown, hint: techTab === "ter" ? terFieldHint(field) : undefined,
        // Long text (datasheet links, sources) is one line cut with «…», full text on hover: a row stays 30 px, not 66.
        render: long || narrow
          ? (record) => <span className={narrow ? "db-clip db-clip-narrow" : "db-clip"} title={record.fields[field] ?? ""}>{shown(record)}</span> : undefined,
        edit: canEdit && inCell ? (own ? ownCellEdit<TechOperationRecord>(field, (record) => record) : fieldCellEdit<TechOperationRecord>(field, (record) => record)) : undefined,
        remove: own ? techRemoval.removeOf(field) : undefined,
        // «Группа» материалов — заголовок группы строк, колонкой не повторяется (она в «Колонках»).
        // «Наконечники»: сервер присылает только заполненные у наконечников столбцы — скрытых по умолчанию нет.
        // «Наименование» наконечника (НКИ, НШВИ…) — заголовок блока, колонкой не повторяется (как «Группа» материалов).
        hidden: !own && ((contactTab && TER_RARE.has(headerKey(field))) || (lugTab && LUG_FAMILY_KEYS.has(headerKey(field))) || (techTab === "mat" && field === "Группа")
          || (Boolean(matTab) && index > 0 && !filledInTab.has(field))),
        // An article of digits (Molex 39000038) is a code, not a number: no «39 000 038», as the warehouse writes it.
        numeric: !/артикул/i.test(fieldTitle(field)) && values.length > 0 && values.every((value) => /^-?\d[\d\s]*([.,]\d+)?$/.test(value)),
      };
    }),
    { key: "__source", title: "Источник", value: (record) => record.source_sheet || "", hidden: true },
  ];
  // БД.ТЕР: обжим одной колонкой «по сечениям» и «От AWG / До AWG» одной колонкой «AWG» (владелец 12.10); исходные
  // колонки остаются в «Колонках» и в выгрузке, но скрыты.
  // «Наконечники»: «Сечение, мм²» и «AWG» от–до одной колонкой за артикулами (lugList.ts).
  const techColumns = contactTab ? mergeTerminalColumns(sheetColumns, fieldColumns)
    : lugTab ? mergeLugColumns(sheetColumns, fieldColumns) : sheetColumns;
  // Быстрые отборы «Без …» — по полям, без которых запись не годится генератору или закупке (как «Без D1–D3» у кабелей).
  const fieldOf = (...keys: string[]) => fieldColumns.find((field) => keys.some((key) => headerKey(field).startsWith(key)));
  const emptyIn = (field: string) => (record: TechOperationRecord) => !String(record.fields[field] ?? "").trim();
  const presetFields: Array<[string, string | undefined]> = contactTab
    ? [["Без высоты обжима", fieldOf("высота обжима проводника", "tercrimpheight")], ["Без аппликатора", fieldOf("аппликатор", "terapplicator")]]
    : lugTab ? [["Без сечения", fieldOf("от мм2", "mm2from")], ["Без аппликатора", fieldOf("аппликатор", "terapplicator")]]
    : techTab === "mat" ? [["Без производителя", fieldOf("производитель")], ["Без поставщика", fieldOf("поставщик")]] : [];
  // «Расходники» take every row whose «Группа» names no tab — «Без группы» finds the empty ones to sort out.
  // Порядок по умолчанию: материалы — по группам листа (ТУТ, лента, стяжка…), внутри — по названию с числами как числа;
  // терминалы — по серии разъёмов (владелец 16.10.2026: «группировку по серии, а не по производителю»; terminalSeries.ts),
  // группы по имени (производитель, серия), «Без серии» в конце, внутри — по артикулу.
  const terGroups = useMemo(() => contactTab ? terminalGroups(tech.records as TerminalRow[], terSeries) : null, [contactTab, tech.records, terSeries]);
  // «Наконечники» — блоками по семейству (НКИ, НШВИ, РПИ-М…), как «Оболочки» по группам.
  const techGroup = techTab === "mat" ? (record: TechOperationRecord) => String(record.fields["Группа"] ?? "").trim() || NO_GROUP
    : terGroups ? (record: TechOperationRecord) => terGroups.get(record.id)?.name ?? NO_SERIES
    : lugTab ? lugFamily : undefined;
  const techGroupLabel = terGroups || lugTab ? (name: string, rows: TechOperationRecord[]) => seriesCaption(name, rows as TerminalRow[]) : undefined;
  const techRows = useMemo(() => techTab !== "mat" ? (terGroups ? sortBySeries(tech.records as TerminalRow[], terGroups, makerOrder(terSeries)) : lugTab ? lugRows(tech.records) : tech.records) : (matTab && coverIds ? materialRows(tech.records, coverIds, matTab) : []).sort((a, b) => {
    const ga = String(a.fields["Группа"] ?? "").trim();
    const gb = String(b.fields["Группа"] ?? "").trim();
    return Number(!ga) - Number(!gb) || natural(ga, gb) || natural(String(a.fields["Наименование"] ?? a.display_text), String(b.fields["Наименование"] ?? b.display_text));
  }), [tech.records, techTab, lugTab, matTab, coverIds, terGroups, terSeries]);
  // The default order of the list over its table (owner 25.10.2026: «размер, затем наименование» везде).
  const sortHint = contactTab ? "серия, затем сечение провода и артикул" : lugTab ? LUG_SORT_HINT : techTab === "mat" ? "группа, затем наименование" : "";
  const techPresets: DataPreset<TechOperationRecord>[] = presetFields
    .filter((item): item is [string, string] => Boolean(item[1]))
    .map(([label, field]) => ({ label, test: emptyIn(field) }))
    .filter((preset) => techRows.some(preset.test)); // пустой отбор — шум
  useEffect(() => {
    if (techTab === "mat" && techReady === "mat" && coverIds) setMatCounts(materialCounts(tech.records, coverIds));
  }, [tech.records, coverIds]); // eslint-disable-line react-hooks/exhaustive-deps
  function openTerminal(id: string) {
    setTerminalId(id);
    try {
      const url = new URL(window.location.href);
      if (id) url.searchParams.set("terminal", id); else url.searchParams.delete("terminal");
      window.history.replaceState(window.history.state, "", url);
    } catch { /* address is not writable */ }
  }
  const terminal = contactTab && terminalId ? tech.records.find((record) => record.id === terminalId) as TerminalRow | undefined : undefined;
  function startCreate(preset: Record<string, string> = {}) {
    const blank = Object.fromEntries(fieldColumns.map((key) => [key, ""]));
    setEditing({ id: null, values: techTab === "ob" ? { ...blank, Тип: "Оборудование", ...preset } : blank });
    window.scrollTo?.({ top: 0, behavior: "smooth" });
  }
  function startEdit(record: TechOperationRecord) {
    setEditing({ id: record.id, values: editValues(record), terKind: record.ter_kind });
    window.scrollTo?.({ top: 0, behavior: "smooth" });
  }
  const current = groups.flatMap((group) => group.tabs).find((def) => def.id === tab);
  const editingRecord = editing?.id ? tech.records.find((record) => record.id === editing.id) : null;

  return <section className="page references-page">
    <header className="page-head"><h1>База данных</h1>{!stockLog && current && count(current) !== null && <span className="page-count">{count(current)}</span>}
      <div className="page-actions">
        {!stockLog && <ReferenceRoundtrip tab={tab} principal={principal} onApplied={() => { if (techTab) void loadTech(techTab); if (cables !== null) void loadCables(); referencesApi.maps().then(applyMaps).catch(() => undefined); loadBases(); }} />}
        <button type="button" className={stockLog ? "secondary small active" : "secondary small"} aria-pressed={stockLog} onClick={() => setStockLog((value) => !value)}>Журнал «Склад → база»</button>
      </div>
    </header>
    <nav className="db-tabs" aria-label="Вкладки базы данных">
      {groups.map((group) => <div className="db-tab-group" key={group.title}>
        <span className="db-tab-group-title">{group.title}</span>
        <div className="db-tab-row">{group.tabs.map((def) => {
          const n = count(def);
          const active = tab === def.id && !stockLog;
          return <button key={def.id} type="button" className={active ? "db-tab active" : "db-tab"} aria-pressed={active} onClick={() => { setTab(def.id); setStockLog(false); }}>
            {def.label}{n !== null && <span className="db-tab-count" aria-hidden="true">{n}</span>}
          </button>;
        })}</div>
      </div>)}
    </nav>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {notice && <div className="success-banner">{notice}</div>}

    {stockLog && <StockLinksJournal fieldTitle={fieldTitle} />}
    <div hidden={stockLog}>

    {tab === "templates" && <TechcardsPage principal={principal} view="templates" embedded />}
    {tab === "connectors" && <ConnectorGallery canEdit={canEdit} />}
    {tab === "cov" && <CoveringsTable canEdit={canEdit} onCount={setCoveringCount} />}

    {isMapTab(tab) && savedMaps === "" && !mapsFailed && <div className="empty">Загрузка…</div>}
    {isMapTab(tab) && (savedMaps !== "" || mapsFailed) && (() => {
      const disabled = !canEditSuppliers;
      return <MapTable
        key={tab}
        tab={tab}
        rows={maps[tab]}
        disabled={disabled}
        // Функциональное обновление: вставка столбца и Ctrl+D пишут ячейки одну за другой — каждая видит предыдущую.
        onChange={(update) => setMaps((current) => ({ ...current, [tab]: update(current[tab]) }))}
        toolbar={<>
          {!disabled && <button type="button" className="secondary small" onClick={() => setMaps((current) => ({ ...current, [tab]: [...current[tab], { key: "", value: "" }] }))}>+ Строка</button>}
          {canSaveMaps && <button type="button" className="primary small" disabled={busy || !mapsDirty} onClick={() => void saveMaps()}>Сохранить справочники</button>}
          {mapsDirty && <span className="db-dirty">Есть несохранённые изменения</span>}
        </>}
      />;
    })()}

    {techTab && <>
      {canEdit && editing && techTab !== "ob" && <PageWindow label={editing.id === null ? "Новая запись" : "Правка записи"} width={860} close={() => setEditing(null)}><div className="panel db-edit">
        <div className="manual-stock-head"><h2>{editing.id === null ? "Новая запись" : `Правка: ${(editingRecord?.display_text ?? "").replace(/^[\s/]+|[\s/]+$/g, "")}`}</h2></div>
        <RowForm values={editing.values} hint={techTab === "ter" ? terFieldHint : undefined} onChange={(values) => setEditing({ ...editing, values })} />
        {techTab === "ter" && editing.id !== null && editing.terKind && <TerKindSelect
          value={editing.terKind} record={editingRecord ?? undefined} onChange={(terKind) => setEditing({ ...editing, terKind })} />}
        <div className="form-actions"><button className="primary" disabled={busy} onClick={() => void saveRow()}>Сохранить запись</button> <button className="secondary" onClick={() => setEditing(null)}>Отмена</button></div>
        {editing.id !== null && <div className="db-edit-stock">
          <h3>Со склада</h3>
          <StockLinksJournal recordId={editing.id} fieldTitle={fieldTitle} />
        </div>}
      </div></PageWindow>}
      {techTab === "ob" ? <EquipmentCards
        records={tech.records}
        operations={obOps}
        canEdit={canEdit}
        onEdit={startEdit}
        onCreate={startCreate}
        onChanged={() => void loadTech("ob")}
        onNotice={(message) => { setError(""); setNotice(message); }}
        onError={(message) => { setNotice(""); setError(message); }}
        focusId={focusTool}
        onSelect={() => setEditing(null)}
        onCancelEdit={() => setEditing(null)}
        editor={canEdit && editing ? <>
          <div className="manual-stock-head"><h2>{editing.id === null ? "Новая запись" : `Правка: ${(editingRecord?.display_text ?? "").replace(/^[\s/]+|[\s/]+$/g, "")}`}</h2></div>
          <EquipmentForm values={editing.values} isNew={editing.id === null} records={tech.records} onChange={(values) => setEditing({ ...editing, values })} />
          <div className="form-actions"><button className="primary" disabled={busy} onClick={() => void saveRow()}>Сохранить запись</button> <button className="secondary" onClick={() => setEditing(null)}>Отмена</button></div>
        </> : undefined}
        toolbar={canEdit && !editing ? <button type="button" className="primary small" disabled={!fieldColumns.length} onClick={() => startCreate()}>+ Новая запись</button> : undefined}
      // БД.КОАКС is ordered by its cable's Ø from «Кабели и провода»: it waits for them, the rows do not jump a second later
      /> : techReady !== techKey || (matTab && !coverIds) || (coaxTab && cables === null) ? <div className="empty">Загрузка…</div> : coaxTab ? <CoaxConnectors
        records={tech.records}
        wireDiameter={wireDiameter}
        canEdit={canEdit}
        onEdit={startEdit}
        ownColumns={tech.custom_columns}
        removeColumn={techRemoval.removeOf}
        onEdited={() => loadTech("coax")}
        toolbar={canEdit && !editing ? <>
          <button type="button" className="secondary small" disabled={!fieldColumns.length} onClick={() => startCreate()}>+ Запись</button>
          <AddColumnButton tab="coax" onAdded={async (name) => { await loadTech("coax"); setError(""); setNotice(`Столбец «${name}» добавлен`); }} />
        </> : undefined}
      /> : <div className="eq-split"><div className="eq-list"><DataTable
        key={tab}
        label={current?.label ?? "База"}
        rows={techRows}
        columns={techColumns}
        presets={techPresets}
        groupBy={techGroup}
        groupLabel={techGroupLabel}
        // Группа — свой блок с заголовком и таблицей (владелец 25.10.2026: «визуал БД один», как «Оболочки»).
        blocks
        searchText={recordSearchText}
        onEdited={() => loadTech(techTab)}
        rowKey={(record) => record.id}
        storageKey={`db:${tab}`}
        sharedKey={tab}
        exportName={current?.label}
        emptyText={viewOnly ? "Таблица не загружена из зеркала SUPPLYMAP." : "Записей нет."}
        // Правка — по строке целиком (столбец одинаковых «Править» делал строки выше и шумел); «Терминалы» — строка
        // открывает карточку контакта, правка из неё.
        onRowClick={contactTab ? (record) => openTerminal(record.id) : canEdit ? startEdit : undefined}
        rowTitle={contactTab ? (record) => `Открыть карточку: ${record.display_text}` : canEdit ? (record) => `Править: ${record.display_text}` : undefined}
        rowClassName={contactTab ? (record) => (record.id === terminal?.id ? "eq-selected" : "") : undefined}
        toolbar={<>{sortHint && <SortHint text={sortHint} />}{canEdit && !editing ? <>
          <button type="button" className="secondary small" disabled={!fieldColumns.length} onClick={() => setEditing({ id: null, values: Object.fromEntries(fieldColumns.map((key) => [key, ""])) })}>+ Запись</button>
          {OWN_COLUMN_TABS.includes(techTab) && !lugTab && <AddColumnButton tab={techTab} onAdded={async (name) => { await loadTech(techTab); setError(""); setNotice(`Столбец «${name}» добавлен`); }} />}
        </> : null}</>}
      /></div>{terminal && <TerminalCard record={terminal} canEdit={canEdit} onEdit={startEdit} onClose={() => openTerminal("")} />}</div>}
      {techRemoval.dialog}
      {coaxTab && <p className="page-sub">У разъёма — подходящий кабель, разделка L1–L3 и HEX обжима; D1–D3 и Ø — у кабеля, во вкладке «Коаксиальные кабели».</p>}
    </>}

    {(cableKind || tab === "gauges") && cables === null && <div className="empty">Загрузка…</div>}
    {tab === "gauges" && cables !== null && <GaugeTable
      gauges={cables.gauges ?? []}
      canEdit={principal.permissions.erpref === "edit"}
      onSaved={cablesSaved}
      onError={(message) => { setNotice(""); setError(message); }}
    />}
    {cableKind === "wire" && cables !== null && <HookupWireTable
      data={cables}
      canEdit={principal.permissions.erpref === "edit"}
      onSaved={cablesSaved}
      onError={(message) => { setNotice(""); setError(message); }}
      kindSelect={kindSelect}
      toolbar={columnButton}
      removeColumn={cableRemoval.removeOf}
    />}
    {cableKind && cableKind !== "wire" && cables !== null && <CableTable
      kind={cableKind}
      data={cables}
      canEdit={principal.permissions.erpref === "edit"}
      onSaved={cablesSaved}
      onError={(message) => { setNotice(""); setError(message); }}
      toolbar={columnButton}
      removeColumn={cableRemoval.removeOf}
    />}
    {cableRemoval.dialog}
    </div>
  </section>;
}

type MapRow = ReferencePair & { index: number };

/** Карта соответствий как лист: ячейки правятся по месту (Enter, Tab, Ctrl+V столбцом, Ctrl+D), правки копятся на экране и
    уходят разом кнопкой «Сохранить справочники» — путь сохранения карт прежний (замена целиком). Порядок — по ключу (домен,
    изготовитель), новые пустые строки внизу. */
function MapTable({ tab, rows, disabled, onChange, toolbar }: {
  tab: MapTab;
  rows: ReferencePair[];
  disabled: boolean;
  onChange: (update: (rows: ReferencePair[]) => ReferencePair[]) => void;
  toolbar: ReactNode;
}) {
  const { title, keyLabel, valueLabel } = MAP_TABS[tab];
  const indexed = useMemo(() => rows.map((row, index): MapRow => ({ ...row, index }))
    .sort((a, b) => Number(!a.key.trim()) - Number(!b.key.trim()) || natural(a.key, b.key) || a.index - b.index), [rows]);
  const cell = (field: keyof ReferencePair) => disabled ? undefined : {
    save: async (row: MapRow, value: string) => onChange((current) => current.map((item, index) => index === row.index ? { ...item, [field]: value } : item)),
  };
  const columns: DataColumn<MapRow>[] = [
    { key: "key", title: keyLabel, value: (row) => row.key, edit: cell("key") },
    { key: "value", title: valueLabel, value: (row) => row.value, edit: cell("value") },
    ...(disabled ? [] : [{
      key: "__delete", title: "", value: () => "",
      render: (row: MapRow) => <button className="link-button danger-link" aria-label={`Удалить строку ${row.index + 1}`} onClick={() => onChange((current) => current.filter((_, index) => index !== row.index))}>Удалить</button>,
    }]),
  ];
  return <>
    <DataTable
      label={title}
      rows={indexed}
      columns={columns}
      rowKey={(row) => String(row.index)}
      rowLabel={(row) => `строка ${row.index + 1}`}
      phoneCards={disabled}
      storageKey={`db:${tab}`}
      // Один блок с названием карты, как у остальных списков «Базы данных» (владелец 25.10.2026).
      blocks
      exportName={`${keyLabel} - ${valueLabel}`}
      emptyText="Нет записей"
      toolbar={<><SortHint text={keyLabel.toLocaleLowerCase("ru-RU")} />{toolbar}</>}
    />
    {!disabled && <p className="page-sub">Сохраняются все карты соответствий разом; пустые строки отбрасываются.</p>}
  </>;
}

const TER_KIND_LABELS: Record<TerKind, string> = { contact: "Терминалы", lug: "Наконечники" };

/** «Список» строки БД.ТЕР в окне правки: перенос между «Терминалами» и «Наконечниками» (как «Вкладка» у кабелей). */
function TerKindSelect({ value, record, onChange }: { value: TerKind; record?: TechOperationRecord; onChange: (kind: TerKind) => void }) {
  const rule = record?.ter_rule;
  return <label className="cable-kind"><small>Список</small>
    <select aria-label="Список" value={value} onChange={(event) => onChange(event.target.value as TerKind)}>
      {(["contact", "lug"] as const).map((kind) => <option key={kind} value={kind}>{TER_KIND_LABELS[kind]}</option>)}
    </select>
    {record?.ter_kind && value !== record.ter_kind && <span className="muted">Строка перейдёт в «{TER_KIND_LABELS[value]}», её значения сохранятся.</span>}
    {rule && <span className="muted">{record?.ter_kind && record.ter_kind !== rule ? `перенесена вручную; по правилу — ${TER_KIND_LABELS[rule].toLowerCase()}` : `по правилу: ${TER_KIND_LABELS[rule].toLowerCase()}`}</span>}
  </label>;
}

function RowForm({ values, hint, onChange }: {
  values: Record<string, string>; hint?: (field: string) => string | undefined; onChange: (values: Record<string, string>) => void;
}) {
  return <div className="reference-fields db-form">{Object.entries(values).map(([key, value]) => {
    const meaning = hint?.(key)?.replace(/^L[+−] — /, "");
    return <label key={key}><small>{fieldTitle(key)}{meaning && <span className="muted"> · {meaning}</span>}</small>
    <input aria-label={`Поле ${key}`} value={value} onChange={(event) => onChange({ ...values, [key]: event.target.value })} />
  </label>;
  })}</div>;
}
