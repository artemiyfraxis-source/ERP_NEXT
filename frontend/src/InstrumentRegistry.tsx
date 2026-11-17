import { useEffect, useMemo, useState, type MouseEvent } from "react";
import type { InstrumentKind, ProductionInstrument } from "./api";
import { compareCells, DataTable, readSaved, type DataColumn } from "./DataTable";
import { formatDay, formatNumber } from "./format";
import { InstrumentWindow } from "./InstrumentWindow";
import { InstrumentLabelPrint } from "./InstrumentLabelPrint";
import { StatusChip } from "./StatusChip";

type InstrumentRow = ProductionInstrument & { depth: number; parentLabel: string; parentNumber: string };
// Reset the old column layout so its saved widths and visible designation cannot override the compact defaults.
const tableKey = "quality-instruments-v3";
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
  { key: "serial_number", title: "Зав. №", value: (item) => item.serial_number, width: 88 },
  { key: "verification_due", title: "Поверка до", value: (item) => item.verification_due, render: (item) => <DueDate item={item} />, width: 132 },
  { key: "status", title: "Статус", value: (item) => item.status, filter: true, width: 144,
    render: (item) => <StatusChip status={item.status} title={item.status}>{item.status === "Поверка не подтверждена" ? "Не подтверждена" : item.status}</StatusChip> },
  { key: "storage_location", title: "Место", value: (item) => item.storage_location, filter: true, width: 96 },
  { key: "files", title: "Файлы", value: (item) => item.files?.length
    ? `${item.files.length}${item.files.some((file) => file.kind === "Паспорт") ? " · паспорт" : ""}` : "—", width: 112 },
] satisfies DataColumn<InstrumentRow>[]).map((column: DataColumn<InstrumentRow>) => ({ ...column,
  render: (item: InstrumentRow) => <span className="instrument-cell" title={String(column.value(item) ?? "")}>
    {column.render ? column.render(item) : column.value(item) || "—"}
  </span>,
}));

export function InstrumentRegistry({ instruments, onChanged }: {
  instruments: ProductionInstrument[];
  onChanged: () => Promise<void>;
}) {
  const [view, setView] = useState<View>(savedView);
  const [treeSort, setTreeSort] = useState(() => readSaved(tableKey).sort ?? null);
  const [labels, setLabels] = useState<ProductionInstrument[] | null>(null);
  const [requestedNumber, setRequestedNumber] = useState(() => new URLSearchParams(globalThis.window.location.search).get("si") ?? "");
  const labelActions = {
    bulkActions: [{ label: "Печать выбранных этикеток", run: (items: InstrumentRow[]) => setLabels([...items]) }],
    filteredActions: [{ label: "Печать этикеток", run: (items: InstrumentRow[]) => setLabels([...items]) }],
  };
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

  const [window, setWindow] = useState<{ record: ProductionInstrument | null; kind: InstrumentKind; parentId: string | null } | null>(null);
  useEffect(() => {
    if (!requestedNumber) return;
    const record = instruments.find((item) => item.inventory_number === requestedNumber);
    if (record) {
      setWindow({ record, kind: record.kind, parentId: record.parent_id });
      setRequestedNumber("");
    }
  }, [instruments, requestedNumber]);
  function closeCard() {
    setWindow(null);
    const url = new URL(globalThis.window.location.href);
    url.searchParams.delete("si");
    globalThis.window.history.replaceState(globalThis.window.history.state, "", url);
  }
  const openNew = (kind: InstrumentKind, parentId: string | null = null) => setWindow({ record: null, kind, parentId });
  const sharedRows = view === "tree" ? rows.filter(isShared).sort((a, b) => compareCells(a.inventory_number, b.inventory_number)) : [];
  const instrumentRows = view === "tree" ? rows.filter((item) => !isShared(item)) : rows;
  return <div className="panel instrument-registry">
    {requestedNumber && !instruments.some((item) => item.inventory_number === requestedNumber) && <div className="error-banner" role="alert">
      {requestedNumber} — запись не найдена
    </div>}
    <div className="manual-stock-head"><h2>Средства измерения</h2>
      <div className="header-actions">
        <button className="secondary small" onClick={() => openNew("Средство измерений")}>+ Средство измерений</button>
        <button className="secondary small" onClick={() => openNew("Принадлежность")}>+ Общая принадлежность</button>
      </div>
    </div>
    <div className="seg-tabs instrument-views" role="group" aria-label="Вид реестра">
      <button type="button" className={view === "list" ? "on" : ""} aria-pressed={view === "list"} onClick={() => changeView("list")}>Списком</button>
      <button type="button" className={view === "tree" ? "on" : ""} aria-pressed={view === "tree"} onClick={() => changeView("tree")}>По приборам</button>
    </div>
    <div className="instrument-legend">Поверка до: <span className="is-overdue">просрочена</span> · <span className="is-soon">осталось до 30 дней</span></div>
    {(instrumentRows.length > 0 || !sharedRows.length) && <div onClickCapture={captureSort}>
      <DataTable label="Средства измерения" storageKey={tableKey} rows={instrumentRows}
        {...labelActions}
        columns={view === "tree" ? columns.filter((column) => column.key !== "parentLabel") : columns} groupSort={view === "tree"}
        rowKey={(item) => item.id} rowLabel={(item) => item.inventory_number || item.name}
        onRowClick={(item) => setWindow({ record: item, kind: item.kind, parentId: item.parent_id })}
        searchLabel="Найти средство измерений" searchPlaceholder="Инв. №, наименование, заводской №…"
        searchText={(item) => [item.instrument_type, item.manufacturer, item.warehouse_grn, item.parentLabel]}
        exportName="Средства измерения" emptyText="Средств измерения нет — добавьте первое кнопкой «+ Средство измерений»." />
    </div>}
    {sharedRows.length > 0 && <div>
      <h3>Общие принадлежности</h3>
      <DataTable label="Общие принадлежности" storageKey={`${tableKey}-shared`} rows={sharedRows}
        {...labelActions}
        columns={columns.filter((column) => column.key !== "parentLabel")}
        rowKey={(item) => item.id} rowLabel={(item) => item.inventory_number || item.name}
        onRowClick={(item) => setWindow({ record: item, kind: item.kind, parentId: null })}
        searchLabel="Найти общую принадлежность" searchPlaceholder="Инв. №, наименование, заводской №…"
        searchText={(item) => [item.instrument_type, item.manufacturer, item.warehouse_grn]}
        exportName="Общие принадлежности" emptyText="Общие принадлежности не найдены." />
    </div>}
    {window && <InstrumentWindow key={window.record?.id ?? `new:${window.kind}:${window.parentId}`} instrument={window.record}
      kind={window.kind} parentId={window.parentId} instruments={instruments} close={closeCard} onChanged={onChanged}
      onAccessory={(parentId) => openNew("Принадлежность", parentId)} />}
    {labels && <InstrumentLabelPrint instruments={labels} bulk close={() => setLabels(null)} />}
  </div>;
}
