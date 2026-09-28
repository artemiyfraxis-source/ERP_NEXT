import { useEffect, useState } from "react";
import {
  referencesApi,
  type Principal,
  type ReferenceMaps,
  type ReferencePair,
  type TechOperationResult,
  type TechOperationTab,
} from "./api";

type PageTab = "maps" | "operations";
const EMPTY_MAPS: ReferenceMaps = { suppliers: [], countries: [], gost_awg: [] };
const TECH_TABS: Array<{ id: TechOperationTab; label: string }> = [
  { id: "op", label: "Операции" },
  { id: "ob", label: "Оборудование" },
  { id: "ter", label: "Термоусадка" },
  { id: "coax", label: "Коаксиал" },
];

export function ReferencesPage({ principal }: { principal: Principal }) {
  const [pageTab, setPageTab] = useState<PageTab>("maps");
  const [maps, setMaps] = useState<ReferenceMaps>(EMPTY_MAPS);
  const [techTab, setTechTab] = useState<TechOperationTab>("op");
  const [query, setQuery] = useState("");
  const [tech, setTech] = useState<TechOperationResult>({ metadata: null, records: [] });
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const canEdit = principal.permissions.references === "edit" && principal.roles.includes("admin");

  async function loadMaps() {
    try {
      setMaps(await referencesApi.maps());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось загрузить справочники");
    } finally {
      setLoaded(true);
    }
  }

  async function loadTech(nextTab = techTab, nextQuery = query) {
    setError("");
    try {
      setTech(await referencesApi.techOperations(nextTab, nextQuery));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось загрузить базу техопераций");
    }
  }

  useEffect(() => { void loadMaps(); }, []);
  useEffect(() => { if (pageTab === "operations") void loadTech(); }, [pageTab, techTab]);

  async function saveMaps() {
    if (busy) return;
    setBusy(true); setError(""); setNotice("");
    try {
      setMaps(await referencesApi.saveMaps(maps));
      setNotice("Справочники сохранены целиком");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Справочники не сохранены");
    } finally {
      setBusy(false);
    }
  }

  return <section className="page references-page">
    <header className="page-head"><h1>Справочники</h1><span className="page-count">{pageTab === "maps" ? maps.suppliers.length + maps.countries.length + maps.gost_awg.length : tech.records.length}</span></header>
    <p className="page-sub">Редактируемые соответствия отдельно от версионированной базы технологических операций.</p>
    <div className="shipping-tabs">
      <button className={pageTab === "maps" ? "secondary active" : "secondary"} onClick={() => setPageTab("maps")}>Карты соответствий</button>
      <button className={pageTab === "operations" ? "secondary active" : "secondary"} onClick={() => setPageTab("operations")}>База техопераций</button>
    </div>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {notice && <div className="success-banner">{notice}</div>}
    {pageTab === "maps" ? <>
      <div className="reference-columns">
        <MapEditor title="Домен поставщика → поставщик" keyLabel="Домен или ссылка" valueLabel="Поставщик" rows={maps.suppliers} disabled={!canEdit} onChange={(suppliers) => setMaps({ ...maps, suppliers })} />
        <MapEditor title="Изготовитель → страна" keyLabel="Изготовитель" valueLabel="Страна" rows={maps.countries} disabled={!canEdit} onChange={(countries) => setMaps({ ...maps, countries })} />
        <MapEditor title="ГОСТ-сечение → AWG (СПР.КАБ)" keyLabel="Сечение ГОСТ, мм²" valueLabel="AWG" rows={maps.gost_awg} disabled={!canEdit} onChange={(gost_awg) => setMaps({ ...maps, gost_awg })} />
      </div>
      {canEdit && <div className="form-actions"><button className="primary" disabled={busy} onClick={() => void saveMaps()}>Сохранить справочники</button></div>}
      {loaded && !maps.suppliers.length && !maps.countries.length && !maps.gost_awg.length && !canEdit && <div className="empty">Карты соответствий пока не заполнены.</div>}
    </> : <>
      <div className="filters reference-filters">
        <div className="shipping-tabs">{TECH_TABS.map((tab) => <button key={tab.id} className={techTab === tab.id ? "secondary active" : "secondary"} onClick={() => setTechTab(tab.id)}>{tab.label}</button>)}</div>
        <input className="search" aria-label="Поиск по базе техопераций" placeholder="Код, название, инструмент…" value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void loadTech(); }} />
        <button className="secondary" onClick={() => void loadTech()}>Найти</button>
      </div>
      {tech.metadata && <div className="snapshot-meta">Схема v{tech.metadata.schema_version} · {tech.metadata.record_count} записей · импорт {new Date(tech.metadata.imported_at).toLocaleString("ru-RU")}</div>}
      <div className="table-wrap"><table><thead><tr><th>Наименование</th><th>Источник</th><th>Поля</th></tr></thead><tbody>{tech.records.map((record) => <tr key={record.id}><td className="text"><strong>{record.display_text}</strong></td><td>{record.source_sheet || "—"}</td><td className="text"><div className="reference-fields">{Object.entries(record.fields).map(([key, value]) => <span key={key}><small>{key}</small>{value}</span>)}</div></td></tr>)}{!tech.records.length && <tr><td className="empty" colSpan={3}>Записей по выбранному фильтру нет.</td></tr>}</tbody></table></div>
    </>}
  </section>;
}

function MapEditor({ title, keyLabel, valueLabel, rows, disabled, onChange }: {
  title: string;
  keyLabel: string;
  valueLabel: string;
  rows: ReferencePair[];
  disabled: boolean;
  onChange: (rows: ReferencePair[]) => void;
}) {
  function update(index: number, field: keyof ReferencePair, value: string) {
    onChange(rows.map((row, rowIndex) => rowIndex === index ? { ...row, [field]: value } : row));
  }
  return <article className="panel reference-map">
    <div className="manual-stock-head"><h2>{title}</h2>{!disabled && <button className="secondary small" onClick={() => onChange([...rows, { key: "", value: "" }])}>+ Строка</button>}</div>
    <div className="reference-map-head"><span>{keyLabel}</span><span>{valueLabel}</span><span /></div>
    {rows.map((row, index) => <div className="reference-row" key={index}>
      <input aria-label={`${keyLabel} ${index + 1}`} disabled={disabled} value={row.key} onChange={(event) => update(index, "key", event.target.value)} />
      <input aria-label={`${valueLabel} ${index + 1}`} disabled={disabled} value={row.value} onChange={(event) => update(index, "value", event.target.value)} />
      {!disabled && <button className="link-button danger-link" aria-label={`Удалить строку ${index + 1}`} onClick={() => onChange(rows.filter((_, rowIndex) => rowIndex !== index))}>Удалить</button>}
    </div>)}
    {!rows.length && <div className="empty">Нет записей</div>}
  </article>;
}
