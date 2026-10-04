import { useEffect, useMemo, useState, type ReactNode } from "react";
import { DataTable, type DataColumn } from "./DataTable";
import { hasAccess } from "./navigation";
import { EquipmentCards } from "./EquipmentCards";
import { WireLookup } from "./WireLookup";
import { TechcardsPage } from "./TechcardsPage";
import { ConnectorGallery } from "./ConnectorGallery";
import { CableTable } from "./CableTables";
import { operationCardCount } from "./TechcardsPage";
import {
  failureText,
  referencesApi,
  techcardsApi,
  type BasesResult,
  type CableKind,
  type CablesResult,
  type Principal,
  type ReferenceMaps,
  type ReferencePair,
  type TechOperationRecord,
  type TechOperationResult,
  type TechOperationTab,
} from "./api";

/* «База данных» (owner, 29.09.2026): one section with sheet-like tabs instead of «Справочник» + «Шаблоны техкарт».
   Every tab is the shared DataTable — search, column filters, sort, column chooser, Excel — so the shop's
   references read like one database. Project techcards (ЭТК) stay with the projects. */

type MapTab = "suppliers" | "countries";
type CableTab = "cab_coax" | "cab_wire" | "cab_other";
export type DbTab = "templates" | "connectors" | TechOperationTab | MapTab | CableTab;
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
  ] },
  // Owner 04.10.2026: «объединить провода и кабели и коаксиальные … три вкладки». БД.ПРОВ and the coax D1·D2·D3 table
  // (СПР.КАБ) are joined by the server (references/cables.py). The AWG table (СПР.КАБ · AWG) is the монтажные wires'
  // own reference — Ø of insulation per mark and «ГОСТ → AWG», as SUPPLYMAP gostAwgMap_ derives it — so it sits right
  // after them as a chip of its own: its rows are AWG sizes, not wires, and it keeps its own count and Excel.
  { title: "Кабели и провода", tabs: [
    { id: "cab_coax", label: "Коаксиальные кабели" },
    { id: "cab_wire", label: "Монтажные провода" },
    { id: "kab_awg", label: "AWG ↔ сечение ↔ Ø" },
    { id: "cab_other", label: "Прочие кабели" },
  ] },
  // БД.МАТ is a working base in SUPPLYMAP (PortalRef.gs REF_BASES_ tiles, filled from purchases), not import.
  { title: "Материалы", tabs: [
    { id: "mat", label: "Материалы", base: "mat" },
  ] },
  { title: "Соответствия", tabs: [
    { id: "suppliers", label: "Домен → поставщик" },
    { id: "countries", label: "Изготовитель → страна" },
  ] },
];
const CABLE_TABS: Record<CableTab, CableKind> = { cab_coax: "coax", cab_wire: "wire", cab_other: "other" };
// Keys of the tabs these replaced (saved tab, `initialTab`): «Провода и кабели» → монтажные, «Коаксиальные кабели:
// D1 · D2 · D3» → коаксиальные кабели.
const TAB_ALIASES: Partial<Record<string, DbTab>> = { prov: "cab_wire", kab_coax: "cab_coax" };
const MAP_TABS: Record<MapTab, { keyLabel: string; valueLabel: string }> = {
  suppliers: { keyLabel: "Домен или ссылка", valueLabel: "Поставщик" },
  countries: { keyLabel: "Изготовитель", valueLabel: "Страна" },
};
// СПР.КАБ — только просмотр (как в портале SUPPLYMAP): таблица как на листе, без правки и «+ Запись».
const VIEW_ONLY_TABS: DbTab[] = ["kab_coax", "kab_awg"];
const TAB_STORAGE = "erp-db-tab";
// Record fields are SUPPLYMAP DB_SCHEMA keys (TcOpsDb.gs, backend technology._TECH_SCHEMA); the sheet's own header
// is what people know, so the column shows that. Unknown keys (БД.ПРОВ, БД.МАТ, СПР.КАБ) are headers already.
const FIELD_TITLES: Record<string, string> = {
  baseValue: "Для базы", obType: "Тип",
  opNumber: "Номер", opName: "Название", tOp: "Время операции, с", tPrep: "Время подготовки, с", tMachine: "Время машины, с/оп; с/м",
  terManufacturer: "Производитель", terSeries: "Серия", terComponent: "Product Name", connType: "Тип разъёма",
  terType: "Тип контакта", artISL: "Артикул ISL", artSAG: "Артикул SAG", terArticle: "Артикул контакта", terLPlus: "L+",
  terLMinus: "L−", terStep: "Шаг", terApplicator: "Аппликатор", terStrip: "Длина зачистки, мм",
  terCrimpHeight: "Высота обжима проводника, мм", terPullForceMin: "Усилие обрыва от, N", terPullForceMax: "Усилие обрыва до, N",
  coaxArticle: "Артикул", coaxType: "Тип/серия", coaxMfr: "Производитель", supplier: "Поставщик", coaxWire: "Провод", program: "Программа",
};
const fieldTitle = (field: string) => FIELD_TITLES[field] ?? field;
// БД.КОАКС (owner 29.09.2026): Ø belong to the cable (СПР.КАБ · коаксиал), the connector row keeps only lengths;
// D1–D3 come from СПР.КАБ by «Провод», as the sheet's D1 formula does. Same key as the generator's coaxCableDiaLookup_.
const CABLE_DIAMETERS = ["D1", "D2", "D3"];
const cableKey = (value: string) => String(value || "").toLowerCase().replace(/[\s-]/g, "");
type CableDia = Record<string, Record<string, string>>;

function isMapTab(tab: DbTab): tab is MapTab {
  return tab in MAP_TABS;
}

function isCableTab(tab: DbTab): tab is CableTab {
  return tab in CABLE_TABS;
}

function savedTab(): string {
  try { return window.localStorage.getItem(TAB_STORAGE) ?? ""; } catch { return ""; }
}

export function ReferencesPage({ principal, initialTab }: { principal: Principal; initialTab?: DbTab }) {
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
  const [tech, setTech] = useState<TechOperationResult>({ metadata: null, records: [] });
  const [cableDia, setCableDia] = useState<CableDia>({});
  const [obOps, setObOps] = useState<TechOperationRecord[]>([]);
  const [bases, setBases] = useState<BasesResult>({ bases: [] });
  const [cables, setCables] = useState<CablesResult | null>(null);
  const [awgRecords, setAwgRecords] = useState<TechOperationRecord[] | null>(null);
  const [opCount, setOpCount] = useState<number | null>(null);
  const [connectorCount, setConnectorCount] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [editing, setEditing] = useState<{ id: string | null; values: Record<string, string> } | null>(null);
  const techTab = !isMapTab(tab) && !isCableTab(tab) && tab !== "templates" && tab !== "connectors" ? tab : null;
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

  async function loadTech(next: TechOperationTab) {
    setError("");
    try {
      setTech(await referencesApi.techOperations(next, "", 5000));
      if (next === "ob") {
        void referencesApi.techOperations("op", "", 5000).then((ops) => setObOps(ops.records)).catch(() => setObOps([]));
      }
      if (next === "coax") {
        const cables = await referencesApi.techOperations("kab_coax", "", 5000).catch(() => ({ records: [] as TechOperationRecord[] }));
        setCableDia(Object.fromEntries(cables.records.map((record) => [cableKey(Object.values(record.fields)[0] ?? ""), record.fields])));
      }
    } catch (reason) {
      setTech({ metadata: null, records: [] });
      setError(reason instanceof Error ? reason.message : "Не удалось загрузить базу техопераций");
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
      setCables({ columns: [], counts: { coax: 0, wire: 0, other: 0 }, rows: [] });
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
      referencesApi.maps().then(applyMaps).catch((reason) => setError(reason instanceof Error ? reason.message : "Не удалось загрузить справочники"));
      loadBases();
    }
    if (canTemplates) {
      // «Операции»: one card per operation code — templates plus БД.ОП codes without one (as the tab builds them).
      Promise.all([
        techcardsApi.templates(),
        canRefs ? referencesApi.techOperations("op", "", 5000) : Promise.resolve({ records: [] as TechOperationRecord[] }),
      ]).then(([library, ops]) => setOpCount(operationCardCount(library.items, ops.records))).catch(() => setOpCount(null));
      techcardsApi.connectorFamilies().then((result) => setConnectorCount(result.items.length)).catch(() => setConnectorCount(null));
    }
  }, [canRefs, canTemplates]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    try { window.localStorage.setItem(TAB_STORAGE, tab); } catch { /* per-viewer convenience only */ }
    setEditing(null);
    setNotice("");
    if (techTab) void loadTech(techTab);
    if (cableKind && cables === null) void loadCables();
    if (cableKind === "wire" && awgRecords === null) {
      referencesApi.techOperations("kab_awg", "", 5000).then((result) => setAwgRecords(result.records)).catch(() => setAwgRecords([]));
    }
  }, [tab]); // eslint-disable-line react-hooks/exhaustive-deps

  function count(def: TabDef): number | null {
    if (isMapTab(def.id)) return canRefs ? maps[def.id].length : null;
    if (def.id === "templates") return opCount;
    if (def.id === "connectors") return connectorCount;
    if (isCableTab(def.id)) return (cables?.counts ?? bases.cables)?.[CABLE_TABS[def.id]] ?? null;
    if (def.id === tab && techTab) return tech.records.length;
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
      if (editing.id === null) {
        await referencesApi.createRecord(techTab, editing.values);
      } else {
        const original = tech.records.find((record) => record.id === editing.id);
        const edits = Object.entries(editing.values)
          .filter(([field, value]) => original && original.fields[field] !== value)
          .map(([field, value]) => ({ field, old: original?.fields[field] ?? "", value }));
        if (edits.length) await referencesApi.editRecord(editing.id, edits);
      }
      setEditing(null);
      setNotice("Запись сохранена");
      await loadTech(techTab);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Запись не сохранена");
    } finally {
      setBusy(false);
    }
  }

  const fieldColumns = tech.columns?.length ? tech.columns : Object.keys(tech.records[0]?.fields ?? {});
  const coaxTab = techTab === "coax";
  const cableOf = (record: TechOperationRecord) => cableDia[cableKey(record.fields["Провод"] ?? record.fields.coaxWire ?? "")];
  // Edit form: the sheet's columns only (the server moves the schema values derived from them); on БД.КОАКС without
  // the cable's Ø — they are edited in «Коаксиальные кабели».
  const editValues = (record: TechOperationRecord) => Object.fromEntries(fieldColumns
    .filter((key) => key in record.fields && !(coaxTab && CABLE_DIAMETERS.includes(key)))
    .map((key) => [key, record.fields[key]]));
  const techColumns: DataColumn<TechOperationRecord>[] = [
    // Short cells (Ø, AWG, мм) stay narrow so a wide sheet like СПР.КАБ fits; long text wraps; numbers sort as numbers.
    ...fieldColumns.map((field): DataColumn<TechOperationRecord> => {
      if (coaxTab && CABLE_DIAMETERS.includes(field)) return {
        key: field, title: `${field} кабеля`, numeric: true,
        value: (record) => cableOf(record)?.[field] ?? "",
        render: (record) => cableOf(record)
          ? <span title={`Из «Коаксиальные кабели» по кабелю «${record.fields["Провод"] ?? ""}»`}>{cableOf(record)?.[field] || "—"}</span>
          : (record.fields["Провод"] ?? record.fields.coaxWire ?? "").trim()
            ? <span className="muted" title={`У кабеля «${record.fields["Провод"] ?? record.fields.coaxWire ?? ""}» нет D1–D3 — задайте их во вкладке «Коаксиальные кабели»`}>нет у кабеля</span>
            : <span className="muted" title="У разъёма не указан кабель («Провод»)">—</span>,
      };
      const values = tech.records.map((record) => record.fields[field] ?? "").filter((value) => value && value !== "—");
      const long = values.some((value) => value.length > 40);
      return {
        key: field, title: fieldTitle(field), value: (record) => record.fields[field] ?? "",
        // Long text (datasheet links, sources) is cut to three lines so one cell does not stretch the row; full text on hover.
        render: long ? (record) => <span className="db-clamp" title={record.fields[field] ?? ""}>{record.fields[field] ?? ""}</span> : undefined,
        wrap: long,
        numeric: values.length > 0 && values.every((value) => /^-?\d[\d\s]*([.,]\d+)?$/.test(value)),
      };
    }),
    { key: "__source", title: "Источник", value: (record) => record.source_sheet || "", hidden: true },
    ...(canEdit ? [{
      key: "__edit", title: "", value: () => "",
      render: (record: TechOperationRecord) => <button className="secondary small" onClick={() => setEditing({ id: record.id, values: editValues(record) })}>Править</button>,
    }] : []),
  ];
  const current = groups.flatMap((group) => group.tabs).find((def) => def.id === tab);
  const editingRecord = editing?.id ? tech.records.find((record) => record.id === editing.id) : null;

  return <section className="page references-page">
    <header className="page-head"><h1>База данных</h1>{current && count(current) !== null && <span className="page-count">{count(current)}</span>}</header>
    <nav className="db-tabs" aria-label="Вкладки базы данных">
      {groups.map((group) => <div className="db-tab-group" key={group.title}>
        <span className="db-tab-group-title">{group.title}</span>
        <div className="db-tab-row">{group.tabs.map((def) => {
          const n = count(def);
          return <button key={def.id} type="button" className={tab === def.id ? "db-tab active" : "db-tab"} aria-pressed={tab === def.id} onClick={() => setTab(def.id)}>
            {def.label}{n !== null && <span className="db-tab-count" aria-hidden="true">{n}</span>}
          </button>;
        })}</div>
      </div>)}
    </nav>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {notice && <div className="success-banner">{notice}</div>}

    {tab === "templates" && <TechcardsPage principal={principal} view="templates" embedded />}
    {tab === "connectors" && <ConnectorGallery />}

    {isMapTab(tab) && (() => {
      const disabled = !canEditSuppliers;
      return <MapTable
        key={tab}
        tab={tab}
        rows={maps[tab]}
        disabled={disabled}
        onChange={(rows) => setMaps({ ...maps, [tab]: rows })}
        toolbar={<>
          {!disabled && <button type="button" className="secondary small" onClick={() => setMaps({ ...maps, [tab]: [...maps[tab], { key: "", value: "" }] })}>+ Строка</button>}
          {canSaveMaps && <button type="button" className="primary small" disabled={busy || !mapsDirty} onClick={() => void saveMaps()}>Сохранить справочники</button>}
          {mapsDirty && <span className="db-dirty">Есть несохранённые изменения</span>}
        </>}
      />;
    })()}

    {techTab && <>
      {canEdit && editing && <div className="panel db-edit">
        <div className="manual-stock-head"><h2>{editing.id === null ? "Новая запись" : `Правка: ${editingRecord?.display_text ?? ""}`}</h2></div>
        <RowForm values={editing.values} onChange={(values) => setEditing({ ...editing, values })} />
        <div className="form-actions"><button className="primary" disabled={busy} onClick={() => void saveRow()}>Сохранить запись</button> <button className="secondary" onClick={() => setEditing(null)}>Отмена</button></div>
      </div>}
      {techTab === "kab_awg" && <WireLookup records={tech.records} />}
      {techTab === "ob" ? <EquipmentCards
        records={tech.records}
        operations={obOps}
        canEdit={canEdit}
        onEdit={(record) => { setEditing({ id: record.id, values: editValues(record) }); window.scrollTo?.({ top: 0, behavior: "smooth" }); }}
        toolbar={canEdit && !editing ? <button type="button" className="secondary small" disabled={!fieldColumns.length} onClick={() => setEditing({ id: null, values: Object.fromEntries(fieldColumns.map((key) => [key, ""])) })}>+ Запись</button> : undefined}
      /> : <DataTable
        key={techTab}
        label={current?.label ?? "База"}
        rows={tech.records}
        columns={techColumns}
        rowKey={(record) => record.id}
        storageKey={`db:${techTab}`}
        exportName={current?.label}
        emptyText={viewOnly ? "Таблица не загружена из зеркала SUPPLYMAP." : "Записей нет."}
        toolbar={canEdit && !editing ? <button type="button" className="secondary small" disabled={!fieldColumns.length} onClick={() => setEditing({ id: null, values: Object.fromEntries(fieldColumns.map((key) => [key, ""])) })}>+ Запись</button> : undefined}
      />}
      {coaxTab && <p className="page-sub">D1–D3 — диаметры кабеля из колонки «Провод»; у разъёма только длины разделки. Диаметры правятся во вкладке «Коаксиальные кабели».</p>}
    </>}

    {cableKind && (cables === null
      ? <div className="empty">Загрузка…</div>
      : <CableTable
        kind={cableKind}
        data={cables}
        awgRecords={awgRecords ?? []}
        canEdit={principal.permissions.erpref === "edit"}
        onSaved={cablesSaved}
        onError={(message) => { setNotice(""); setError(message); }}
      />)}
  </section>;
}

type MapRow = ReferencePair & { index: number };

function MapTable({ tab, rows, disabled, onChange, toolbar }: {
  tab: MapTab;
  rows: ReferencePair[];
  disabled: boolean;
  onChange: (rows: ReferencePair[]) => void;
  toolbar: ReactNode;
}) {
  const { keyLabel, valueLabel } = MAP_TABS[tab];
  const indexed = rows.map((row, index): MapRow => ({ ...row, index }));
  function update(index: number, field: keyof ReferencePair, value: string) {
    onChange(rows.map((row, rowIndex) => rowIndex === index ? { ...row, [field]: value } : row));
  }
  const cell = (field: keyof ReferencePair, label: string) => (row: MapRow) => disabled
    ? row[field]
    : <input className="db-cell-input" aria-label={`${label} ${row.index + 1}`} value={row[field]} onChange={(event) => update(row.index, field, event.target.value)} />;
  const columns: DataColumn<MapRow>[] = [
    { key: "key", title: keyLabel, value: (row) => row.key, render: cell("key", keyLabel) },
    { key: "value", title: valueLabel, value: (row) => row.value, render: cell("value", valueLabel) },
    ...(disabled ? [] : [{
      key: "__delete", title: "", value: () => "",
      render: (row: MapRow) => <button className="link-button danger-link" aria-label={`Удалить строку ${row.index + 1}`} onClick={() => onChange(rows.filter((_, rowIndex) => rowIndex !== row.index))}>Удалить</button>,
    }]),
  ];
  return <>
    <DataTable
      label={`${keyLabel} → ${valueLabel}`}
      rows={indexed}
      columns={columns}
      rowKey={(row) => String(row.index)}
      storageKey={`db:${tab}`}
      exportName={`${keyLabel} - ${valueLabel}`}
      emptyText="Нет записей"
      toolbar={toolbar}
    />
    {!disabled && <p className="page-sub">Сохраняются все карты соответствий разом; пустые строки отбрасываются.</p>}
  </>;
}

function RowForm({ values, onChange }: { values: Record<string, string>; onChange: (values: Record<string, string>) => void }) {
  return <div className="reference-fields db-form">{Object.entries(values).map(([key, value]) => <label key={key}><small>{fieldTitle(key)}</small>
    <input aria-label={`Поле ${key}`} value={value} onChange={(event) => onChange({ ...values, [key]: event.target.value })} />
  </label>)}</div>;
}
