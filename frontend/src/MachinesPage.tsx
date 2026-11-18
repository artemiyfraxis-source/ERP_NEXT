import { useCallback, useMemo, useState } from "react";
import { DataTable, type DataColumn } from "./DataTable";
import { EMPTY, formatMoment } from "./format";
import { machinesApi, usePolled, type MachineRow, type Tone } from "./machinesApi";
import { Ob019Card } from "./Ob019Card";
import "./machines.css";

/** «Станки» (Производство): всё, что подключено к ERP, — ОБ с мостом на своём ПК, принтеры агентов, сканеры и
 *  анализаторы цепей. Одна строка — одно устройство, одно значение в колонке. Строка станка открывает живую карточку. */

const KIND_ORDER: Record<MachineRow["kind"], string> = { machine: "Станки", printer: "Принтеры", device: "Приборы и сканеры" };

function toned(text: string, tone: Tone) {
  return <span className={tone === "neutral" ? "machine-muted" : `tone-${tone}`}>{text || EMPTY}</span>;
}

const text = (value: string) => value || EMPTY;

const COLUMNS: DataColumn<MachineRow>[] = [
  { key: "inventory", title: "ОБ", value: (row) => row.inventory, render: (row) => <strong>{text(row.inventory)}</strong> },
  { key: "name", title: "Название", value: (row) => row.name, grow: true, maxWidth: 320 },
  { key: "pc", title: "ПК", value: (row) => row.pc, render: (row) => text(row.pc) },
  { key: "ip", title: "IP", value: (row) => row.ip, render: (row) => <span className="machine-mono">{text(row.ip)}</span> },
  { key: "service", title: "Служба", value: (row) => row.service, filter: true },
  { key: "version", title: "Версия", value: (row) => row.version, render: (row) => <span className="machine-mono">{text(row.version)}</span> },
  { key: "link", title: "Связь", value: (row) => row.link.text, filter: true, render: (row) => toned(row.link.text, row.link.tone) },
  { key: "last_seen", title: "Последний сигнал", value: (row) => row.last_seen ?? "", render: (row) => <span className="machine-mono">{row.last_seen ? formatMoment(row.last_seen) : EMPTY}</span> },
  { key: "device", title: "Устройство", value: (row) => row.device, render: (row) => text(row.device) },
  { key: "state", title: "Состояние", value: (row) => row.state.text, filter: true, render: (row) => toned(row.state.text, row.state.tone) },
];

export function MachinesPage() {
  const load = useCallback((signal: AbortSignal) => machinesApi.list(signal), []);
  const { data, error } = usePolled(load);
  const [open, setOpen] = useState<MachineRow | null>(null);
  const rows = useMemo(() => data?.rows ?? [], [data]);
  return <section className="page machines-page">
    <header className="page-head"><h1>Станки</h1></header>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {!data && !error
      ? <p className="empty">Загрузка…</p>
      : <DataTable label="Подключённое оборудование" rows={rows} columns={COLUMNS} rowKey={(row) => row.key}
        groupBy={(row) => KIND_ORDER[row.kind]} storageKey="machines-list"
        onRowClick={(row) => { if (row.card && row.equipment_id) setOpen(row); }}
        rowClassName={(row) => (row.card ? "machine-row-open" : "")}
        rowTitle={(row) => (row.card ? "Открыть живую карточку станка" : "")}
        emptyText="Ничего не подключено — установите мост на ПК станка или агент печати на ПК склада" />}
    {open?.equipment_id && <Ob019Card equipmentId={open.equipment_id}
      title={[open.inventory, open.name].filter(Boolean).join(" · ")} close={() => setOpen(null)} />}
  </section>;
}
