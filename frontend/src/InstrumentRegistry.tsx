import { useMemo, useState, type MouseEvent } from "react";
import type { InstrumentKind, ProductionInstrument } from "./api";
import { compareCells, DataTable, readSaved, type DataColumn } from "./DataTable";
import { formatDay, formatNumber } from "./format";
import { InstrumentWindow } from "./InstrumentWindow";
import { StatusChip } from "./StatusChip";

type InstrumentRow = ProductionInstrument & { depth: number; parentLabel: string };
const tableKey = "quality-instruments-v2";
const viewKey = "quality-instruments-view";
type View = "list" | "tree";

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
  { key: "inventory_number", title: "Инв. №", value: (item) => item.inventory_number, width: 92 },
  { key: "name", title: "Наименование", value: (item) => item.name, grow: true, minWidth: 160,
    render: (item) => <span className="instrument-name" title={`${item.name}${item.kind === "Принадлежность" ? ` · ${formatNumber(item.quantity)} шт` : ""}`} style={{ paddingLeft: Math.min(item.depth, 8) * 16 }}>
      {item.depth > 0 && <span aria-hidden="true">↳ </span>}{item.name}
      {item.kind === "Принадлежность" && <small> · {formatNumber(item.quantity)} шт</small>}
    </span> },
  { key: "parentLabel", title: "Прибор", value: (item) => item.parentLabel, width: 140,
    render: (item) => item.parentLabel },
  { key: "doc_designation", title: "Обозн.", hint: "Обозначение для документов", value: (item) => item.doc_designation, width: 96 },
  { key: "kind", title: "Вид", value: (item) => item.kind, filter: true, width: 156 },
  { key: "serial_number", title: "Зав. №", value: (item) => item.serial_number, width: 80 },
  { key: "verification_due", title: "Поверка до", value: (item) => item.verification_due, render: (item) => <DueDate item={item} />, width: 104 },
  { key: "status", title: "Статус", value: (item) => item.status, filter: true, width: 192,
    render: (item) => <StatusChip status={item.status} title={item.status} /> },
  { key: "storage_location", title: "Место", value: (item) => item.storage_location, filter: true, width: 104 },
  { key: "files", title: "Файлы", value: (item) => `${item.files?.length ?? 0}${item.files?.some((file) => file.kind === "Паспорт") ? " · Паспорт" : ""}`, width: 72 },
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
  const rows = useMemo(() => {
    const byId = new Map(instruments.map((item) => [item.id, item]));
    const column = view === "tree" && treeSort && columns.find((item) => item.key === treeSort.key);
    const items = instruments.map((item) => {
      const parent = item.parent_id ? byId.get(item.parent_id) : undefined;
      return { ...item, depth: 0, parentLabel: parent ? `${parent.inventory_number} · ${parent.name}` : "" };
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
  const openNew = (kind: InstrumentKind, parentId: string | null = null) => setWindow({ record: null, kind, parentId });
  return <div className="panel instrument-registry">
    <div className="manual-stock-head"><h2>Средства измерения</h2>
      <button className="secondary small" onClick={() => openNew("Средство измерений")}>+ Средство измерений</button>
    </div>
    <div className="seg-tabs instrument-views" role="group" aria-label="Вид реестра">
      <button type="button" className={view === "list" ? "on" : ""} aria-pressed={view === "list"} onClick={() => changeView("list")}>Списком</button>
      <button type="button" className={view === "tree" ? "on" : ""} aria-pressed={view === "tree"} onClick={() => changeView("tree")}>По приборам</button>
    </div>
    <div className="instrument-legend">Поверка до: <span className="is-overdue">просрочена</span> · <span className="is-soon">осталось до 30 дней</span></div>
    <div onClickCapture={captureSort}>
      <DataTable label="Средства измерения" storageKey={tableKey} rows={rows} columns={columns} groupSort={view === "tree"}
        rowKey={(item) => item.id} rowLabel={(item) => item.inventory_number || item.name}
        onRowClick={(item) => setWindow({ record: item, kind: item.kind, parentId: item.parent_id })}
        searchLabel="Найти средство измерений" searchPlaceholder="Инв. №, наименование, заводской №…"
        searchText={(item) => [item.instrument_type, item.manufacturer, item.warehouse_grn]}
        exportName="Средства измерения" emptyText="Средств измерения нет — добавьте первое кнопкой «+ Средство измерений»." />
    </div>
    {window && <InstrumentWindow key={window.record?.id ?? `new:${window.kind}:${window.parentId}`} instrument={window.record}
      kind={window.kind} parentId={window.parentId} instruments={instruments} close={() => setWindow(null)} onChanged={onChanged}
      onAccessory={(parentId) => openNew("Принадлежность", parentId)} />}
  </div>;
}
