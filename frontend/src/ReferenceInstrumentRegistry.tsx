import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent } from "react";
import { failureText, referencesApi, type InstrumentKind, type ProductionInstrument, type ReferenceInstrumentsResult } from "./api";
import { AddColumnButton, useColumnRemoval } from "./CustomColumn";
import { compareCells, DataTable, readSaved, type DataColumn, type DataPreset } from "./DataTable";
import { formatDay, formatNumber } from "./format";
import { InstrumentLabelPrint } from "./InstrumentLabelPrint";
import { InstrumentWindow } from "./InstrumentWindow";
import { StatusChip } from "./StatusChip";

/* «База данных · Средства измерений» — единственный реестр СИ (ОТК-1, 27.11.2026): вкладка «ОТК → Средства измерения»
   влита сюда. Добавление прибора и общей принадлежности, вид «Списком / По приборам», этикетки, цвет «Поверка до». */
type InstrumentRow = ProductionInstrument & { depth: number; parentLabel: string; parentNumber: string };
const tableKey = "reference-instruments-v2";
const viewKey = "quality-instruments-view";
type View = "list" | "tree";

const isShared = (item: ProductionInstrument) => item.kind === "Принадлежность" && item.parent_id === null;

function savedView(): View {
  try { return localStorage.getItem(viewKey) === "tree" ? "tree" : "list"; }
  catch { return "list"; }
}

function treeRows(instruments: InstrumentRow[]): InstrumentRow[] {
  const children = new Map<string | null, InstrumentRow[]>();
  const ids = new Set(instruments.map((item) => item.id));
  for (const item of instruments) {
    const parent = item.parent_id && ids.has(item.parent_id) ? item.parent_id : null;
    children.set(parent, [...(children.get(parent) ?? []), item]);
  }
  const rows: InstrumentRow[] = [];
  const seen = new Set<string>();
  function visit(item: InstrumentRow, depth: number) {
    if (seen.has(item.id)) return;
    seen.add(item.id);
    rows.push({ ...item, depth });
    for (const child of children.get(item.id) ?? []) visit(child, depth + 1);
  }
  for (const item of children.get(null) ?? []) visit(item, 0);
  // Keep malformed/orphaned records visible without looping on a parent cycle.
  for (const item of instruments) visit(item, 0);
  return rows;
}

function DueDate({ item }: { item: ProductionInstrument }) {
  if (item.kind === "Принадлежность" || !item.verification_due) return <>—</>;
  const due = new Date(item.verification_due.slice(0, 10) + "T00:00:00Z");
  const today = new Date();
  const todayUtc = Date.UTC(today.getFullYear(), today.getMonth(), today.getDate());
  const remaining = (due.getTime() - todayUtc) / 86_400_000;
  const warning = remaining < 0 ? "Просрочена" : remaining <= 30 ? "До 30 дней" : "";
  return <span title={[formatDay(item.verification_due), warning].filter(Boolean).join(" · ")} className={`instrument-due ${remaining < 0 ? "is-overdue" : warning ? "is-soon" : ""}`}>
    {formatDay(item.verification_due)}{warning && <> · <small>{warning}</small></>}
  </span>;
}

const columns: DataColumn<InstrumentRow>[] = ([
  { key: "inventory_number", title: "Инв. №", value: (item) => item.inventory_number, width: 88 },
  { key: "name", title: "Наименование", value: (item) => item.name, grow: true, minWidth: 160,
    render: (item) => <span className="instrument-name" title={`${item.name}${item.kind === "Принадлежность" ? ` · ${formatNumber(item.quantity)} шт` : ""}`} style={{ paddingLeft: Math.min(item.depth, 8) * 16 }}>
      {item.depth > 0 && <span aria-hidden="true">↳ </span>}{item.name}
      {item.kind === "Принадлежность" && <small> · {formatNumber(item.quantity)} шт</small>}
    </span> },
  { key: "parentLabel", title: "Прибор", value: (item) => item.parentLabel, width: 88,
    render: (item) => item.parentNumber || "—" },
  // Полное имя прибора — в Excel всегда (и в виде «По приборам», где колонки «Прибор» нет).
  { key: "parentFull", title: "Прибор полностью", value: (item) => item.parentLabel, hidden: true, export: true },
  { key: "doc_designation", title: "Обозн.", hint: "Обозначение для документов", value: (item) => item.doc_designation, width: 96, hidden: true, export: true },
  { key: "kind", title: "Вид", value: (item) => item.kind, filter: true, width: 88,
    render: (item) => item.kind === "Средство измерений" ? "СИ" : "Принадл." },
  { key: "quantity", title: "Кол-во, шт.", value: (item) => Number(item.quantity), numeric: true, width: 80, hidden: true, export: true },
  { key: "serial_number", title: "Зав. №", value: (item) => item.serial_number, width: 88 },
  { key: "verification_due", title: "Поверка до", value: (item) => item.verification_due, render: (item) => <DueDate item={item} />, width: 132 },
  { key: "status", title: "Статус", value: (item) => item.status, filter: true, width: 144,
    render: (item) => <StatusChip status={item.status} title={item.status}>{item.status === "Поверка не подтверждена" ? "Не подтверждена" : item.status}</StatusChip> },
  { key: "storage_location", title: "Место", value: (item) => item.storage_location, filter: true, width: 96 },
  { key: "files", title: "Файлы", value: (item) => item.files?.length
    ? `${item.files.length}${item.files.some((file) => file.kind === "Паспорт") ? " · паспорт" : ""}` : "—", width: 112 },
  { key: "manufacturer", title: "Производитель", value: (item) => item.manufacturer, filter: true, hidden: true, export: true },
  { key: "article", title: "Артикул", value: (item) => item.article, filter: true, hidden: true, export: true },
  { key: "measurement_category", title: "Категория измерений", value: (item) => item.measurement_category, filter: true, hidden: true, export: true },
  { key: "notes", title: "Примечание", value: (item) => item.notes, hidden: true, export: true },
  { key: "instrument_type", title: "Тип", value: (item) => item.instrument_type, filter: true, hidden: true, export: true },
] satisfies DataColumn<InstrumentRow>[]).map((column: DataColumn<InstrumentRow>) => ({ ...column,
  render: (item: InstrumentRow) => <span className="instrument-cell" title={String(column.value(item) ?? "")}>
    {column.render ? column.render(item) : column.value(item) || "—"}
  </span>,
}));

// Only offer a status that exists in the registry; unused instruments are not written-off instruments.
const statusPresets = [
  ["Действует", "В работе"], ["На поверке", "На поверке"], ["Списан", "Списаны"],
  ["Не используется", "Не используется"], ["Поверка не подтверждена", "Поверка не подтверждена"],
] as const;

type Opened = { record: ProductionInstrument | null; kind: InstrumentKind; parentId: string | null };

export function ReferenceInstrumentRegistry({ onCount }: { onCount?: (count: number, quantity: number) => void }) {
  const [data, setData] = useState<ReferenceInstrumentsResult | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [opened, setOpened] = useState<Opened | null>(null);
  const [view, setView] = useState<View>(savedView);
  const [treeSort, setTreeSort] = useState(() => readSaved(tableKey).sort ?? null);
  const [labels, setLabels] = useState<ProductionInstrument[] | null>(null);
  const countCallback = useRef(onCount);
  countCallback.current = onCount;
  const request = useRef(0);
  const [requested] = useState(() => {
    const params = new URLSearchParams(window.location.search);
    return params.get("instrument") || params.get("equipment") || "";
  });
  const [requestedNumber] = useState(() => new URLSearchParams(window.location.search).get("si") ?? "");

  const reload = useCallback(async () => {
    const stamp = ++request.current;
    setError("");
    try {
      const result = await referencesApi.instruments();
      if (stamp !== request.current) return;
      setData(result);
      countCallback.current?.(result.count, Number(result.quantity));
    } catch (reason) {
      if (stamp === request.current) {
        setError(failureText(reason, "Не удалось загрузить средства измерений"));
        throw reason;
      }
    } finally { if (stamp === request.current) setLoading(false); }
  }, []);

  const select = useCallback((item: ProductionInstrument | null) => {
    setOpened(item ? { record: item, kind: item.kind, parentId: item.parent_id } : null);
    const url = new URL(window.location.href);
    if (item) {
      url.searchParams.set("section", "references");
      url.searchParams.set("tab", "ob_measuring");
      url.searchParams.set("equipment", item.id);
    } else url.searchParams.delete("equipment");
    url.searchParams.delete("instrument");
    url.searchParams.delete("si");
    window.history.replaceState(window.history.state, "", url);
  }, []);

  useEffect(() => {
    void reload().catch(() => undefined);
    let active = true;
    if (requested) {
      void referencesApi.instrument(requested).then(({ instrument }) => {
        if (active) select(instrument);
      }).catch((reason) => { if (active) setError(failureText(reason, "СИ не найдено")); });
    }
    return () => { active = false; request.current += 1; };
  }, [reload, requested, select]);

  const numberOpened = useRef(false);
  useEffect(() => {
    if (!data || !requestedNumber || requested || numberOpened.current) return;
    numberOpened.current = true;
    const item = data.instruments.find((row) => row.inventory_number === requestedNumber);
    if (item) select(item); else setError(`${requestedNumber} — запись не найдена`);
  }, [data, requested, requestedNumber, select]);

  const instruments = data?.instruments ?? [];
  const canEditIdentity = Boolean(data?.can_edit_identity);
  const rows = useMemo(() => {
    const byId = new Map(instruments.map((item) => [item.id, item]));
    const column = view === "tree" && treeSort && columns.find((item) => item.key === treeSort.key);
    const items = instruments.map((item) => {
      const parent = item.parent_id ? byId.get(item.parent_id) : undefined;
      const sharedLabel = isShared(item) ? "общая" : "";
      return { ...item, depth: 0, parentLabel: parent ? `${parent.inventory_number} · ${parent.name}` : sharedLabel, parentNumber: parent?.inventory_number ?? sharedLabel };
    }).sort((a, b) => column && treeSort
      ? treeSort.direction * compareCells(column.value(a), column.value(b), column.numeric)
      : compareCells(a.inventory_number, b.inventory_number));
    return view === "tree" ? treeRows(items) : items;
  }, [instruments, view, treeSort]);

  function changeView(next: View) {
    setView(next);
    try { localStorage.setItem(viewKey, next); } catch { /* The current view still works when storage is unavailable. */ }
  }

  // DataTable owns the sort controls and their saved state. Mirror header clicks only to sort
  // siblings before building the tree: a global table sort would separate children from parents.
  function captureSort(event: MouseEvent<HTMLDivElement>) {
    const header = (event.target as HTMLElement).closest("button.sort-head")?.closest("th[data-col]");
    const key = header?.getAttribute("data-col");
    if (!header || !key) return;
    const current = header.getAttribute("aria-sort");
    setTreeSort(current === "descending" ? null : { key, direction: current === "ascending" ? -1 : 1 });
  }

  const openNew = (kind: InstrumentKind, parentId: string | null = null) => setOpened({ record: null, kind, parentId });
  const removal = useColumnRemoval("ob_measuring", Boolean(data?.can_edit_custom),
    (name) => instruments.filter((item) => String(item.custom_fields?.[name] ?? "").trim()).length, reload);
  const ownColumns: DataColumn<InstrumentRow>[] = (data?.custom_columns ?? []).map((name) => ({
    key: `custom:${name}`, title: name, value: (row) => row.custom_fields?.[name] ?? "",
    filter: true, remove: removal.removeOf(name),
  }));
  const presets: DataPreset<InstrumentRow>[] = statusPresets
    .filter(([status]) => instruments.some((row) => String(row.status) === status))
    .map(([status, label]) => ({ label, test: (row) => String(row.status) === status }));
  // Этикетки печатает тот, кто ведёт реестр (право правки «ОТК»), как и раньше в ОТК.
  const labelActions = canEditIdentity ? {
    bulkActions: [{ label: "Печать выбранных этикеток", run: (items: InstrumentRow[]) => setLabels([...items]) }],
    filteredActions: [{ label: "Печать этикеток", run: (items: InstrumentRow[]) => setLabels([...items]) }],
  } : {};
  const sharedRows = view === "tree" ? rows.filter(isShared).sort((a, b) => compareCells(a.inventory_number, b.inventory_number)) : [];
  const instrumentRows = view === "tree" ? rows.filter((item) => !isShared(item)) : rows;
  const toolbar = data?.can_edit_custom ? <AddColumnButton tab="ob_measuring" onAdded={reload} />
    : <span title="Нужно право правки базы данных"><button type="button" className="secondary small" disabled>+ Столбец</button></span>;
  const treeColumns = [...columns.filter((column) => column.key !== "parentLabel"), ...ownColumns];

  return <div className="panel instrument-registry">
    <div className="manual-stock-head"><h2>Средства измерений</h2>
      {data && <span className="tnum">{formatNumber(data.count)} записей · {formatNumber(data.quantity)} шт.</span>}
      {canEditIdentity && <div className="header-actions">
        <button type="button" className="secondary small" onClick={() => openNew("Средство измерений")}>+ Средство измерений</button>
        <button type="button" className="secondary small" onClick={() => openNew("Принадлежность")}>+ Общая принадлежность</button>
      </div>}
    </div>
    {error && <div className="error-banner" role="alert">{error}
      {!data && <button type="button" className="secondary small" onClick={() => { setLoading(true); void reload().catch(() => undefined); }}>Повторить</button>}
    </div>}
    <div className="seg-tabs instrument-views" role="group" aria-label="Вид реестра">
      <button type="button" className={view === "list" ? "on" : ""} aria-pressed={view === "list"} onClick={() => changeView("list")}>Списком</button>
      <button type="button" className={view === "tree" ? "on" : ""} aria-pressed={view === "tree"} onClick={() => changeView("tree")}>По приборам</button>
    </div>
    <div className="instrument-legend">Поверка до: <span className="is-overdue">просрочена</span> · <span className="is-soon">осталось до 30 дней</span></div>
    {(instrumentRows.length > 0 || !sharedRows.length) && <div onClickCapture={captureSort}>
      <DataTable label="Средства измерений" storageKey={tableKey} sharedKey="ob_measuring"
        rows={instrumentRows} {...labelActions}
        columns={view === "tree" ? treeColumns : [...columns, ...ownColumns]} groupSort={view === "tree"}
        rowKey={(item) => item.id} rowLabel={(item) => item.inventory_number || item.name}
        onRowClick={select} pageSize={200} loading={loading} presets={presets}
        searchLabel="Найти средство измерений" searchPlaceholder="Инв. №, наименование, заводской №…"
        searchText={(item) => [item.instrument_type, item.manufacturer, item.doc_designation, item.warehouse_grn, item.parentLabel]}
        exportName="Средства измерений"
        emptyText={canEditIdentity ? "Средств измерений нет — добавьте первое кнопкой «+ Средство измерений»." : "Средств измерений нет."}
        toolbar={toolbar} />
    </div>}
    {sharedRows.length > 0 && <div>
      <h3>Общие принадлежности</h3>
      <DataTable label="Общие принадлежности" storageKey={`${tableKey}-shared`} rows={sharedRows} {...labelActions}
        columns={treeColumns}
        rowKey={(item) => item.id} rowLabel={(item) => item.inventory_number || item.name}
        onRowClick={select}
        searchLabel="Найти общую принадлежность" searchPlaceholder="Инв. №, наименование, заводской №…"
        searchText={(item) => [item.instrument_type, item.manufacturer, item.warehouse_grn]}
        exportName="Общие принадлежности" emptyText="Общие принадлежности не найдены." />
    </div>}
    {removal.dialog}
    {data && !data.can_edit_custom && <p className="muted-text">Столбцы и их значения доступны для просмотра. Для изменения нужно право правки базы данных.</p>}
    {opened && data && <InstrumentWindow key={opened.record?.id ?? `new:${opened.kind}:${opened.parentId}`} instrument={opened.record}
      kind={opened.kind} parentId={opened.parentId} instruments={instruments}
      // Правка ОТК — полное окно прибора (паспорт, этикетка, ВЧ-интерфейсы, файлы); остальные читают его из базы.
      source={canEditIdentity ? "production" : "references"} referenceFields
      canEditIdentity={canEditIdentity} canEditCustom={data.can_edit_custom}
      customColumns={data.custom_columns} close={() => select(null)} onChanged={reload}
      onAccessory={(parentId) => openNew("Принадлежность", parentId)} />}
    {labels && <InstrumentLabelPrint instruments={labels} bulk close={() => setLabels(null)} />}
  </div>;
}
