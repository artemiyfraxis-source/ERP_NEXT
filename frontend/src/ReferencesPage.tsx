import { useEffect, useMemo, useState, type ReactNode } from "react";
import { DataTable, type DataColumn } from "./DataTable";
import { hasAccess } from "./navigation";
import { TechcardsPage } from "./TechcardsPage";
import {
  referencesApi,
  type Principal,
  type ReferenceBase,
  type ReferenceMaps,
  type ReferencePair,
  type TechOperationRecord,
  type TechOperationResult,
  type TechOperationTab,
} from "./api";

/* «База данных» (owner, 29.09.2026): one section with sheet-like tabs instead of «Справочник» + «Шаблоны техкарт».
   Every tab is the shared DataTable — search, column filters, sort, column chooser, Excel — so the shop's
   references read like one database. Project techcards (ЭТК) stay with the projects. */

type MapTab = "suppliers" | "countries" | "gost_awg";
export type DbTab = "templates" | TechOperationTab | MapTab;
type TabDef = { id: DbTab; label: string; base?: string };

const EMPTY_MAPS: ReferenceMaps = { suppliers: [], countries: [], gost_awg: [] };
const GROUPS: Array<{ title: string; tabs: TabDef[] }> = [
  { title: "Техкарты", tabs: [{ id: "templates", label: "Шаблоны техкарт", base: "tk_templates" }] },
  { title: "Техоперации", tabs: [
    { id: "op", label: "Операции", base: "op" },
    { id: "ob", label: "Оборудование", base: "ob" },
    { id: "ter", label: "Терминалы", base: "ter" },
    { id: "coax", label: "Коаксиал", base: "coax" },
    { id: "prov", label: "Провода", base: "prov" },
    { id: "mat", label: "Материалы", base: "mat" },
  ] },
  { title: "СПР.КАБ", tabs: [{ id: "kab_coax", label: "СПР.КАБ · коаксиал" }, { id: "kab_awg", label: "СПР.КАБ · AWG" }] },
  { title: "Соответствия", tabs: [
    { id: "suppliers", label: "Домен → поставщик" },
    { id: "countries", label: "Изготовитель → страна" },
    { id: "gost_awg", label: "ГОСТ → AWG" },
  ] },
];
const MAP_TABS: Record<MapTab, { keyLabel: string; valueLabel: string }> = {
  suppliers: { keyLabel: "Домен или ссылка", valueLabel: "Поставщик" },
  countries: { keyLabel: "Изготовитель", valueLabel: "Страна" },
  gost_awg: { keyLabel: "Сечение ГОСТ, мм²", valueLabel: "AWG" },
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

function isMapTab(tab: DbTab): tab is MapTab {
  return tab in MAP_TABS;
}

function savedTab(): string {
  try { return window.localStorage.getItem(TAB_STORAGE) ?? ""; } catch { return ""; }
}

export function ReferencesPage({ principal, initialTab }: { principal: Principal; initialTab?: DbTab }) {
  // Tabs by their own keys (ID-3): templates — erptctpl, the rest — erpref, as the two sections were.
  const canTemplates = hasAccess(principal.permissions, "erptctpl");
  const canRefs = hasAccess(principal.permissions, "erpref");
  const groups = useMemo(() => GROUPS
    .map((group) => ({ ...group, tabs: group.tabs.filter((tab) => tab.id === "templates" ? canTemplates : canRefs) }))
    .filter((group) => group.tabs.length > 0), [canTemplates, canRefs]);
  const allowed = groups.flatMap((group) => group.tabs.map((tab) => tab.id));
  const [tab, setTab] = useState<DbTab>(() => {
    const wanted = initialTab ?? savedTab();
    return (allowed.find((id) => id === wanted) ?? allowed[0] ?? "op") as DbTab;
  });
  const [maps, setMaps] = useState<ReferenceMaps>(EMPTY_MAPS);
  const [savedMaps, setSavedMaps] = useState("");
  const [tech, setTech] = useState<TechOperationResult>({ metadata: null, records: [] });
  const [bases, setBases] = useState<ReferenceBase[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [editing, setEditing] = useState<{ id: string | null; values: Record<string, string> } | null>(null);
  const techTab = !isMapTab(tab) && tab !== "templates" ? tab : null;
  const viewOnly = VIEW_ONLY_TABS.includes(tab);
  // ID-3: the `erpref` section decides (PortalRef.gs portalSectionEditGuard_), not the role.
  const canEdit = principal.permissions.erpref === "edit" && !viewOnly;
  // Maps (Portal.gs portalSaveReferences): supplier/country — the buyer's (portalCanWork_), ГОСТ→AWG — «Справочник».
  const canEditSuppliers = Boolean(maps.can_edit?.suppliers);
  const canEditGost = Boolean(maps.can_edit?.gost_awg);
  const canSaveMaps = canEditSuppliers || canEditGost;
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
    } catch (reason) {
      setTech({ metadata: null, records: [] });
      setError(reason instanceof Error ? reason.message : "Не удалось загрузить базу техопераций");
    }
  }

  useEffect(() => {
    if (!canRefs) return;
    referencesApi.maps().then(applyMaps).catch((reason) => setError(reason instanceof Error ? reason.message : "Не удалось загрузить справочники"));
    // Counts on the tabs are a summary; a failed call leaves the tabs without numbers.
    referencesApi.bases().then((result) => setBases(result.bases)).catch(() => setBases([]));
  }, [canRefs]);

  useEffect(() => {
    try { window.localStorage.setItem(TAB_STORAGE, tab); } catch { /* per-viewer convenience only */ }
    setEditing(null);
    setNotice("");
    if (techTab) void loadTech(techTab);
  }, [tab]); // eslint-disable-line react-hooks/exhaustive-deps

  function count(def: TabDef): number | null {
    if (isMapTab(def.id)) return canRefs ? maps[def.id].length : null;
    if (def.id === tab && techTab) return tech.records.length;
    return bases.find((base) => base.id === def.base)?.count ?? null;
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
  const techColumns: DataColumn<TechOperationRecord>[] = [
    ...fieldColumns.map((field): DataColumn<TechOperationRecord> => ({ key: field, title: fieldTitle(field), value: (record) => record.fields[field] ?? "", wrap: true })),
    { key: "__source", title: "Источник", value: (record) => record.source_sheet || "", hidden: true },
    ...(canEdit ? [{
      key: "__edit", title: "", value: () => "",
      render: (record: TechOperationRecord) => <button className="secondary small" onClick={() => setEditing({ id: record.id, values: { ...record.fields } })}>Править</button>,
    }] : []),
  ];
  const current = groups.flatMap((group) => group.tabs).find((def) => def.id === tab);
  const editingRecord = editing?.id ? tech.records.find((record) => record.id === editing.id) : null;

  return <section className="page references-page">
    <header className="page-head"><h1>База данных</h1>{current && count(current) !== null && <span className="page-count">{count(current)}</span>}</header>
    <p className="page-sub">Шаблоны техкарт, техоперации, СПР.КАБ и карты соответствий — одна база, каждая вкладка как лист. Техкарты конкретных проектов открываются из «Проектов».</p>
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

    {isMapTab(tab) && (() => {
      const disabled = tab === "gost_awg" ? !canEditGost : !canEditSuppliers;
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
      {tech.metadata && <div className="snapshot-meta">Схема v{tech.metadata.schema_version} · {tech.metadata.record_count} записей · импорт {new Date(tech.metadata.imported_at).toLocaleString("ru-RU")}</div>}
      {canEdit && editing && <div className="panel db-edit">
        <div className="manual-stock-head"><h2>{editing.id === null ? "Новая запись" : `Правка: ${editingRecord?.display_text ?? ""}`}</h2></div>
        <RowForm values={editing.values} onChange={(values) => setEditing({ ...editing, values })} />
        <div className="form-actions"><button className="primary" disabled={busy} onClick={() => void saveRow()}>Сохранить запись</button> <button className="secondary" onClick={() => setEditing(null)}>Отмена</button></div>
      </div>}
      <DataTable
        key={techTab}
        label={current?.label ?? "База"}
        rows={tech.records}
        columns={techColumns}
        rowKey={(record) => record.id}
        storageKey={`db:${techTab}`}
        exportName={current?.label}
        emptyText={viewOnly ? "Таблица СПР.КАБ не загружена из зеркала." : "Записей нет."}
        toolbar={canEdit && !editing ? <button type="button" className="secondary small" disabled={!fieldColumns.length} onClick={() => setEditing({ id: null, values: Object.fromEntries(fieldColumns.map((key) => [key, ""])) })}>+ Запись</button> : undefined}
      />
      <p className="page-sub">{viewOnly ? "СПР.КАБ — только просмотр; карта «ГОСТ → AWG» правится на вкладке соответствий." : "Правки строк живут до следующего импорта снимка."}</p>
    </>}
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
