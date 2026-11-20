import { useCallback, useEffect, useMemo, useState } from "react";
import { failureText } from "./api";
import { DataTable, type DataColumn } from "./DataTable";
import { presetPeriod } from "./DowntimeReport";
import { EMPTY, formatDay, formatWallMoment } from "./format";
import { ManualJobMaterial } from "./ManualJobMaterial";
import { ManualJobWindow } from "./ManualJobWindow";
import { manualJobsApi, type ManualJob, type ManualJobsList } from "./manualJobsApi";
import "./manualJobs.css";

/** «Ручная работа» (Производство): журнал работ на станках вне проекта за период — что делали, на каком станке,
 *  сколько годных и брака, что с материалом. Строка открывает окно работы (завершить / отменить), кнопка «Начать
 *  ручную работу» — новое окно. Видят «Производство», «Задания» и «Склад»; начинают и завершают те, у кого правка
 *  «Производства» или «Заданий». «Ждёт решения склада» — все завершённые работы, чей материал ещё не решён, за любые
 *  даты: строка открывает окно «Материал» (списать с партии / не списывать). */

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

export function ManualJobsPage() {
  const [period, setPeriod] = useState(() => presetPeriod("week"));
  const [data, setData] = useState<ManualJobsList | null>(null);
  const [error, setError] = useState("");
  const [open, setOpen] = useState<ManualJob | "new" | null>(null);
  const [material, setMaterial] = useState<ManualJob | null>(null);
  const [waiting, setWaiting] = useState(false);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    if (!period.from || !period.to) return;
    const controller = new AbortController();
    setError("");
    manualJobsApi.list(period.from, period.to, "", controller.signal, waiting)
      .then(setData)
      .catch((reason) => { if (!controller.signal.aborted) setError(failureText(reason, "Журнал ручной работы не загружен")); });
    return () => controller.abort();
  }, [period.from, period.to, waiting, reload]);

  const saved = useCallback(() => setReload((value) => value + 1), []);
  const label = period.from === period.to ? formatDay(period.from) : `${formatDay(period.from)} – ${formatDay(period.to)}`;
  const rows = useMemo(() => data?.rows ?? [], [data]);
  const running = rows.filter((row) => row.status === "running").length;
  const waitingCount = data?.waiting_count ?? 0;
  const count = (value: number) => `${value} ${value === 1 ? "работа" : value > 1 && value < 5 ? "работы" : "работ"}`;
  const decides = (row: ManualJob) => row.status === "done" && row.material === "pending" && Boolean(data?.can_decide);
  const openRow = (row: ManualJob) => (decides(row) ? setMaterial(row) : setOpen(row));

  return <section className="page mj-page">
    <header className="page-head mj-head">
      <h1>Ручная работа</h1>
      <div className="mj-period" role="group" aria-label="Период">
        {PRESETS.map(([key, title]) => {
          const value = presetPeriod(key);
          const on = !waiting && value.from === period.from && value.to === period.to;
          return <button key={key} type="button" className={on ? "secondary small on" : "secondary small"} aria-pressed={on} onClick={() => { setWaiting(false); setPeriod(value); }}>{title}</button>;
        })}
        <label>с<input type="date" name="from" autoComplete="off" aria-label="Начало периода" value={period.from} max={period.to} disabled={waiting} onChange={(event) => event.target.value && setPeriod({ ...period, from: event.target.value })} /></label>
        <label>по<input type="date" name="to" autoComplete="off" aria-label="Конец периода" value={period.to} min={period.from} disabled={waiting} onChange={(event) => event.target.value && setPeriod({ ...period, to: event.target.value })} /></label>
      </div>
      <button type="button" className={waiting ? "secondary small on mj-waiting" : "secondary small mj-waiting"} aria-pressed={waiting}
        onClick={() => setWaiting(!waiting)}>Ждёт решения склада<span className={waitingCount ? "mj-count tone-warn" : "mj-count"}>{waitingCount}</span></button>
      {data?.can_edit && <button type="button" className="primary mj-new" onClick={() => setOpen("new")}>Начать ручную работу</button>}
    </header>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {!data && !error && <p className="empty">Загрузка…</p>}
    {data && <>
      <p className="mj-summary">{waiting ? `Ждёт решения склада за все даты: ${count(rows.length)}`
        : `За ${label}: ${count(rows.length)}${running ? ` · идёт сейчас ${running}` : ""}`}</p>
      {!rows.length
        ? <p className="empty">{waiting ? "Материал всех завершённых работ решён — склад ничего не ждёт"
          : `За ${label} ручной работы не было${data.can_edit ? " — нажмите «Начать ручную работу» или кнопку «Ручная работа» на карточке станка" : ""}`}</p>
        : <DataTable rows={rows} columns={COLUMNS} rowKey={(row) => row.id} label="Ручная работа" storageKey="manual-jobs"
          exportName={waiting ? "Ручная работа — ждёт решения склада" : `Ручная работа ${label}`} onRowClick={openRow}
          rowTitle={(row) => (decides(row) ? "Решить по материалу" : "Открыть работу")} pageSize={100} />}
    </>}
    {open && <ManualJobWindow job={open === "new" ? null : open} canEdit={data?.can_edit ?? false} canIssue={data?.can_issue ?? false}
      close={() => setOpen(null)} onSaved={saved} />}
    {material && <ManualJobMaterial job={material} close={() => setMaterial(null)} onSaved={saved} />}
  </section>;
}
