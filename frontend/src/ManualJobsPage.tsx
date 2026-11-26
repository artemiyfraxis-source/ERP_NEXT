import { useCallback, useEffect, useMemo, useState } from "react";
import { failureText } from "./api";
import { DataTable, type DataColumn } from "./DataTable";
import { presetPeriod } from "./DowntimeReport";
import { countLabel, EMPTY, formatDay, formatWallMoment } from "./format";
import { ManualJobMaterial } from "./ManualJobMaterial";
import { ManualJobWindow } from "./ManualJobWindow";
import { manualJobsApi, type ManualJob, type ManualJobsList } from "./manualJobsApi";
import "./manualJobs.css";

/** Shared by equipment and warehouse; starting outside-task work stays on equipment cards. */
export interface WorkJournalProps {
  /** Fixed equipment UUID for a machine card. Without it the journal offers an equipment filter. */
  equipmentId?: string;
  /** Initial period (ISO dates); users can still select historical periods. Defaults to URL dates or this week. */
  period?: { from: string; to: string };
  /** Warehouse mode opens all-date pending material by default, and completed rows open their material history. */
  mode?: "journal" | "materials";
}

type Preset = "today" | "week" | "month";
const PRESETS: Array<[Preset, string]> = [["today", "Сегодня"], ["week", "Неделя"], ["month", "Месяц"]];

const dash = (value: string | number | null | undefined) => (value === null || value === undefined || value === "" ? EMPTY : value);

const COLUMNS: DataColumn<ManualJob>[] = [
  { key: "number", title: "№", width: 88, value: (row) => row.number, render: (row) => <b className="mj-number">{row.number}</b> },
  { key: "machine", title: "Станок", filter: true, width: 80, value: (row) => row.inventory, render: (row) => <span title={row.machine}>{row.inventory}</span> },
  { key: "started", title: "Начата", width: 96, value: (row) => row.started_at, render: (row) => <span className="mj-num">{formatWallMoment(row.started_at)}</span> },
  { key: "finished", title: "Завершена", hidden: true, width: 96, value: (row) => row.finished_at ?? "", render: (row) => <span className="mj-num">{row.finished_at ? formatWallMoment(row.finished_at) : EMPTY}</span> },
  { key: "status", title: "Состояние", filter: true, width: 96, value: (row) => row.status_text, render: (row) => <span className={`mj-status tone-${row.tone}`}>{row.status_text}</span> },
  { key: "purpose", title: "Зачем", grow: true, wrap: true, value: (row) => row.purpose },
  { key: "wire", title: "Провод", filter: true, width: 120, value: (row) => row.wire, render: (row) => dash(row.wire) },
  { key: "contact", title: "Контакт", filter: true, width: 120, value: (row) => row.contact, render: (row) => dash(row.contact) },
  { key: "length", title: "Длина, мм", numeric: true, width: 80, value: (row) => row.length_mm ?? "", render: (row) => dash(row.length_mm) },
  { key: "planned", title: "План, шт", numeric: true, hidden: true, width: 80, value: (row) => row.quantity_planned ?? 0, render: (row) => dash(row.quantity_planned) },
  { key: "good", title: "Годные, шт", numeric: true, width: 84, value: (row) => row.good ?? 0, render: (row) => dash(row.good) },
  { key: "defect", title: "Брак, шт", numeric: true, width: 72, value: (row) => row.defect ?? 0, render: (row) => <span title={row.defect_reason || undefined}>{dash(row.defect)}</span> },
  { key: "material", title: "Материал", filter: true, width: 156, value: (row) => row.material_text,
    render: (row) => (row.material_text ? <span className={row.material_tone !== "neutral" ? `tone-${row.material_tone}` : undefined} title={row.material_note || undefined}>{row.material_text}</span> : EMPTY) },
  { key: "operator", title: "Кто", filter: true, hidden: true, width: 140, value: (row) => row.operator },
];

export function WorkJournal({ equipmentId, period: initialPeriod, mode = "journal" }: WorkJournalProps = {}) {
  const [initialQuery] = useState(() => new URLSearchParams(window.location.search));
  const [period, setPeriod] = useState(() => {
    const fallback = initialPeriod ?? presetPeriod("week");
    return initialPeriod ?? { from: initialQuery.get("date_from") || fallback.from, to: initialQuery.get("date_to") || fallback.to };
  });
  const [selectedEquipment, setEquipment] = useState(() => initialQuery.get("equipment_id") || "");
  const selected = equipmentId || selectedEquipment;
  const [data, setData] = useState<ManualJobsList | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [open, setOpen] = useState<ManualJob | null>(null);
  const [material, setMaterial] = useState<ManualJob | null>(null);
  const [waiting, setWaiting] = useState(() => initialQuery.has("waiting") ? initialQuery.get("waiting") === "true" : mode === "materials");
  const [reload, setReload] = useState(0);

  useEffect(() => {
    if (!period.from || !period.to) return;
    const controller = new AbortController();
    setError("");
    setLoading(true);
    manualJobsApi.list(period.from, period.to, equipmentId ?? "", controller.signal, waiting)
      .then(next => { if (!controller.signal.aborted) setData(next); })
      .catch((reason) => { if (!controller.signal.aborted) setError(failureText(reason, "Журнал работ не загружен")); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [period.from, period.to, equipmentId, waiting, reload]);

  useEffect(() => {
    const url = new URL(window.location.href);
    if (!["machines", "stock"].includes(url.searchParams.get("section") ?? "")) return;
    url.searchParams.set("date_from", period.from);
    url.searchParams.set("date_to", period.to);
    url.searchParams.set("waiting", String(waiting));
    // A scoped journal must not replace the machine/source context of its host card.
    if (!equipmentId) {
      if (selectedEquipment) url.searchParams.set("equipment_id", selectedEquipment);
      else url.searchParams.delete("equipment_id");
    }
    window.history.replaceState(window.history.state, "", url);
  }, [period.from, period.to, waiting, selectedEquipment, equipmentId]);

  const saved = useCallback(() => setReload((value) => value + 1), []);
  const label = period.from === period.to ? formatDay(period.from) : `${formatDay(period.from)} – ${formatDay(period.to)}`;
  const rows = useMemo(() => (data?.rows ?? []).filter(row => !selected || row.equipment_id === selected), [data, selected]);
  const machines = useMemo(() => {
    const result = new Map<string, string>();
    for (const row of data?.rows ?? []) result.set(row.equipment_id, [row.inventory, row.machine].filter(Boolean).join(" · "));
    return [...result].sort((a, b) => a[1].localeCompare(b[1], "ru", { numeric: true }));
  }, [data]);
  const running = rows.filter((row) => row.status === "running").length;
  const waitingCount = data?.waiting_count ?? 0;
  const count = (value: number) => countLabel(value, ["работа", "работы", "работ"]);
  const decides = (row: ManualJob) => row.status === "done" && row.material === "pending" && Boolean(data?.can_decide);
  const opensMaterial = (row: ManualJob) => (mode === "materials" && row.status === "done") || decides(row);
  const openRow = (row: ManualJob) => (opensMaterial(row) ? setMaterial(row) : setOpen(row));
  const title = mode === "materials" ? "Материал работ" : "Журнал работ";

  return <section className="mj-page" aria-label={title}>
    <header className="page-head mj-head">
      <h2>{title}</h2>
      <div className="mj-period" role="group" aria-label="Период">
        {PRESETS.map(([key, title]) => {
          const value = presetPeriod(key);
          const on = !waiting && value.from === period.from && value.to === period.to;
          return <button key={key} type="button" className={on ? "secondary small on" : "secondary small"} aria-pressed={on} onClick={() => { setWaiting(false); setPeriod(value); }}>{title}</button>;
        })}
        <label>с<input type="date" name="from" autoComplete="off" aria-label="Начало периода" value={period.from} max={period.to} disabled={waiting} onChange={(event) => event.target.value && setPeriod({ ...period, from: event.target.value })} /></label>
        <label>по<input type="date" name="to" autoComplete="off" aria-label="Конец периода" value={period.to} min={period.from} disabled={waiting} onChange={(event) => event.target.value && setPeriod({ ...period, to: event.target.value })} /></label>
      </div>
      <label className="mj-machine">Станок<select name="equipment_id" autoComplete="off" value={selected} disabled={Boolean(equipmentId)} onChange={event => setEquipment(event.target.value)}>
        <option value="">Все станки</option>
        {selected && !machines.some(([id]) => id === selected) && <option value={selected}>Выбранный станок</option>}
        {machines.map(([id, name]) => <option key={id} value={id}>{name}</option>)}
      </select></label>
      <button type="button" className={waiting ? "secondary small on mj-waiting" : "secondary small mj-waiting"} aria-pressed={waiting}
        onClick={() => setWaiting(!waiting)}>Ждёт решения склада<span title="За все даты, все станки" className={waitingCount ? "mj-count tone-warn" : "mj-count"}>{waitingCount}</span></button>
    </header>
    {error && <div className="error-banner" role="alert">{error} <button type="button" className="secondary small" onClick={saved}>Повторить</button></div>}
    {data && !loading && !error && <p className="mj-summary">{waiting ? `Ждёт решения склада за все даты: ${count(rows.length)}`
      : `За ${label}: ${count(rows.length)}${running ? ` · идёт сейчас ${running}` : ""}`}</p>}
    <DataTable rows={loading || error ? [] : rows} loading={loading} columns={COLUMNS} rowKey={(row) => row.id} label={title} storageKey="manual-jobs"
      initialQuery={initialQuery.get("q") ?? undefined}
      emptyText={error ? "Журнал не загружен" : rows.length ? "Ничего не найдено — измените поиск или фильтры"
        : waiting ? (selected ? "На выбранном станке нет работ, ожидающих решения по материалу" : "Материал всех завершённых работ решён — склад ничего не ждёт")
          : `За ${label} работ нет — выберите другой период или станок`}
      exportName={waiting ? `${title} — ждёт решения склада` : `${title} ${label}`} onRowClick={openRow}
      rowTitle={(row) => (opensMaterial(row) ? "Открыть материал работы" : "Открыть работу")} pageSize={100} />
    {open && <ManualJobWindow job={open} canEdit={data?.can_edit ?? false} canIssue={data?.can_issue ?? false}
      close={() => setOpen(null)} onSaved={saved} />}
    {material && <ManualJobMaterial job={material} close={() => setMaterial(null)} onSaved={saved} />}
  </section>;
}

/** Compatibility export for existing consumers; the standalone page is no longer mounted by App. */
export const ManualJobsPage = WorkJournal;
