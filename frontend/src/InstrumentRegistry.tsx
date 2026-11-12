import { useState } from "react";
import type { InstrumentKind, ProductionInstrument } from "./api";
import { DataTable, type DataColumn } from "./DataTable";
import { formatDay, formatNumber } from "./format";
import { InstrumentWindow } from "./InstrumentWindow";
import { StatusChip } from "./StatusChip";

type InstrumentRow = ProductionInstrument & { depth: number };

function treeRows(instruments: ProductionInstrument[]): InstrumentRow[] {
  const children = new Map<string | null, ProductionInstrument[]>();
  const ids = new Set(instruments.map((item) => item.id));
  for (const item of instruments) {
    const parent = item.parent_id && ids.has(item.parent_id) ? item.parent_id : null;
    children.set(parent, [...(children.get(parent) ?? []), item]);
  }
  const rows: InstrumentRow[] = [];
  const seen = new Set<string>();
  function visit(item: ProductionInstrument, depth: number) {
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
  return <span className={`instrument-due ${remaining < 0 ? "is-overdue" : warning ? "is-soon" : ""}`}>
    {formatDay(item.verification_due)}{warning && <small>{warning}</small>}
  </span>;
}

const columns: DataColumn<InstrumentRow>[] = [
  { key: "inventory_number", title: "Инв. №", value: (item) => item.inventory_number, width: 92 },
  { key: "name", title: "Наименование", value: (item) => item.name, grow: true, wrap: true, minWidth: 160,
    render: (item) => <span className="instrument-name" style={{ paddingLeft: Math.min(item.depth, 8) * 16 }}>
      {item.depth > 0 && <span aria-hidden="true">↳ </span>}{item.name}
      {item.kind === "Принадлежность" && <small> · {formatNumber(item.quantity)} шт</small>}
    </span> },
  { key: "doc_designation", title: "Обозначение для документов", value: (item) => item.doc_designation, wrap: true, minWidth: 120, maxWidth: 170 },
  { key: "kind", title: "Вид", value: (item) => item.kind, filter: true, wrap: true, width: 120 },
  { key: "serial_number", title: "Зав. №", value: (item) => item.serial_number, width: 88 },
  { key: "verification_due", title: "Поверка до", value: (item) => item.verification_due, render: (item) => <DueDate item={item} />, width: 104 },
  { key: "status", title: "Статус", value: (item) => item.status, filter: true, wrap: true, minWidth: 126,
    render: (item) => <StatusChip status={item.status} /> },
  { key: "storage_location", title: "Место", value: (item) => item.storage_location, filter: true, width: 80 },
  { key: "files", title: "Файлы", value: (item) => `${item.files?.length ?? 0}${item.files?.some((file) => file.kind === "Паспорт") ? " · Паспорт" : ""}`, width: 100 },
];

export function InstrumentRegistry({ instruments, onChanged }: {
  instruments: ProductionInstrument[];
  onChanged: () => Promise<void>;
}) {
  const [window, setWindow] = useState<{ record: ProductionInstrument | null; kind: InstrumentKind; parentId: string | null } | null>(null);
  const openNew = (kind: InstrumentKind, parentId: string | null = null) => setWindow({ record: null, kind, parentId });
  return <div className="panel instrument-registry">
    <div className="manual-stock-head"><h2>Средства измерения</h2>
      <button className="secondary small" onClick={() => openNew("Средство измерений")}>+ Средство измерений</button>
    </div>
    <div className="instrument-legend">Поверка до: <span className="is-overdue">просрочена</span> · <span className="is-soon">осталось до 30 дней</span></div>
    {/* Fixed tree order: DataTable still provides search, filters, columns and export. */}
    <DataTable label="Средства измерения" storageKey="quality-instruments-v2" rows={treeRows(instruments)} columns={columns} groupSort
      rowKey={(item) => item.id} rowLabel={(item) => item.inventory_number || item.name}
      onRowClick={(item) => setWindow({ record: item, kind: item.kind, parentId: item.parent_id })}
      searchLabel="Найти средство измерений" searchPlaceholder="Инв. №, наименование, заводской №…"
      searchText={(item) => [item.instrument_type, item.manufacturer, item.warehouse_grn]}
      exportName="Средства измерения" emptyText="Средств измерения нет — добавьте первое кнопкой «+ Средство измерений»." />
    {window && <InstrumentWindow key={window.record?.id ?? `new:${window.kind}:${window.parentId}`} instrument={window.record}
      kind={window.kind} parentId={window.parentId} instruments={instruments} close={() => setWindow(null)} onChanged={onChanged}
      onAccessory={(parentId) => openNew("Принадлежность", parentId)} />}
  </div>;
}
