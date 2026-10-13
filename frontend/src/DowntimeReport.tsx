import { useEffect, useState } from "react";
import { failureText } from "./api";
import { DataTable, type DataColumn } from "./DataTable";
import { downtimeApi, downtimeDuration, type Downtime, type DowntimeGroup, type DowntimeReport as Report, type DowntimeReportRow } from "./downtimeApi";
import { formatDay, formatMoment } from "./format";
import "./downtime.css";

/** Отчёт «Простои» за период: сумма по причинам или по сотрудникам и доля от работы по журналу времени; ниже —
 *  каждый простой периода (в период входит только его часть внутри дней периода, открытый — до «сейчас»). */

function isoDay(date: Date): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

type Preset = "today" | "week" | "month";

export function presetPeriod(preset: Preset, today = new Date()): { from: string; to: string } {
  const start = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  if (preset === "week") start.setDate(start.getDate() - ((start.getDay() + 6) % 7));
  if (preset === "month") start.setDate(1);
  return { from: isoDay(start), to: isoDay(today) };
}

function share(part: number, whole: number): string {
  if (!whole) return "—";
  return `${(Math.round((part * 1000) / whole) / 10).toString().replace(".", ",")} %`;
}

const PRESETS: Array<[Preset, string]> = [["today", "Сегодня"], ["week", "Неделя"], ["month", "Месяц"]];

export function DowntimeReport({ close }: { close: () => void }) {
  const [period, setPeriod] = useState(() => presetPeriod("month"));
  const [by, setBy] = useState<DowntimeGroup>("reason");
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!period.from || !period.to) return;
    let live = true;
    setError("");
    setReport(null);
    downtimeApi.report(period.from, period.to, by)
      .then((loaded) => { if (live) setReport(loaded); })
      .catch((reason) => { if (live) setError(failureText(reason, "Отчёт по простоям не загружен")); });
    return () => { live = false; };
  }, [period.from, period.to, by]);

  const total = report?.total_seconds ?? 0;
  const groupColumns: DataColumn<DowntimeReportRow>[] = [
    { key: "label", title: by === "reason" ? "Причина" : "Сотрудник", width: 240, value: (row) => row.label, render: (row) => <b>{row.label}</b> },
    { key: "count", title: "Случаев", numeric: true, width: 90, value: (row) => row.count, render: (row) => <>{row.count}{row.open ? <span className="dwn-open-note"> · {row.open} идёт</span> : null}</> },
    { key: "seconds", title: "Время", numeric: true, width: 120, value: (row) => row.seconds, render: (row) => downtimeDuration(row.seconds) },
    { key: "share", title: "Доля простоев", numeric: true, width: 130, value: (row) => (total ? row.seconds / total : 0), render: (row) => share(row.seconds, total) },
    ...(by === "operator" ? [{
      key: "work", title: "Работа по журналу", numeric: true, width: 160, value: (row: DowntimeReportRow) => row.work_seconds ?? 0,
      render: (row: DowntimeReportRow) => (row.work_seconds == null ? "—" : downtimeDuration(row.work_seconds)),
    }] : []),
  ];
  const entryColumns: DataColumn<Downtime>[] = [
    { key: "start", title: "Начало", width: 110, value: (row) => row.started_at, render: (row) => formatMoment(row.started_at) },
    { key: "end", title: "Конец", width: 110, value: (row) => row.ended_at ?? "", render: (row) => (row.ended_at ? formatMoment(row.ended_at) : <span className="dwn-open-note">идёт</span>) },
    { key: "time", title: "В периоде", numeric: true, width: 110, value: (row) => row.period_seconds ?? row.seconds, render: (row) => downtimeDuration(row.period_seconds ?? row.seconds) },
    { key: "reason", title: "Причина", filter: true, width: 150, value: (row) => row.reason_name, render: (row) => <b>{row.reason_name}</b> },
    { key: "who", title: "Сотрудник", filter: true, width: 170, value: (row) => (row.whole_task ? "Задание целиком" : row.operator_name) },
    { key: "task", title: "Задание", width: 220, value: (row) => `${row.task_number} ${row.project} ${row.item}`, render: (row) => <>{row.task_number} · {row.project}{row.item ? ` · ${row.item}` : ""}{row.stage ? <small className="dwn-sub">{row.stage}</small> : null}</> },
    { key: "comment", title: "Комментарий", wrap: true, grow: true, value: (row) => row.comment },
    { key: "by", title: "Отметил", hidden: true, value: (row) => [row.started_by_name, row.ended_by_name].filter(Boolean).join(" / ") },
  ];
  const label = period.from === period.to ? formatDay(period.from) : `${formatDay(period.from)} – ${formatDay(period.to)}`;

  return <section className="panel dwn-report" aria-label="Простои">
    <div className="dwn-report-head">
      <h2>Простои</h2>
      <div className="dwn-period" role="group" aria-label="Период">
        {PRESETS.map(([key, title]) => {
          const value = presetPeriod(key);
          const on = value.from === period.from && value.to === period.to;
          return <button key={key} type="button" className={on ? "secondary small on" : "secondary small"} aria-pressed={on} onClick={() => setPeriod(value)}>{title}</button>;
        })}
        <label>с<input type="date" aria-label="Начало периода" value={period.from} max={period.to} onChange={(event) => event.target.value && setPeriod({ ...period, from: event.target.value })} /></label>
        <label>по<input type="date" aria-label="Конец периода" value={period.to} min={period.from} onChange={(event) => event.target.value && setPeriod({ ...period, to: event.target.value })} /></label>
      </div>
      <div className="seg-tabs dwn-group" role="radiogroup" aria-label="Группировка">
        {([["reason", "По причинам"], ["operator", "По сотрудникам"]] as Array<[DowntimeGroup, string]>).map(([key, title]) =>
          <button key={key} type="button" role="radio" aria-checked={by === key} className={by === key ? "on" : undefined} onClick={() => setBy(key)}>{title}</button>)}
      </div>
      <button type="button" className="secondary small dwn-close" onClick={close}>Закрыть</button>
    </div>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {!report && !error && <div className="empty">Загрузка…</div>}
    {report && <>
      <dl className="dwn-summary">
        <div><dt>Период</dt><dd>{label}</dd></div>
        <div><dt>Простои</dt><dd>{downtimeDuration(report.total_seconds)} · {report.total_count} сл.</dd></div>
        <div><dt>Работа по журналу</dt><dd>{downtimeDuration(report.work_seconds)}</dd></div>
        <div><dt>Простои к работе</dt><dd>{share(report.total_seconds, report.work_seconds)}</dd></div>
      </dl>
      {!report.rows.length
        ? <div className="empty">Простоев за {label} нет — их отмечают кнопкой «Простой…» на карточке задания.</div>
        : <>
          <DataTable rows={report.rows} columns={groupColumns} rowKey={(row) => row.key} hideSearch phoneCards={false} label="Простои по группам" exportName={`Простои ${label}`} />
          <h3 className="dwn-entries-title">Все простои периода<span className="page-count">{report.entries.length}</span></h3>
          <DataTable rows={report.entries} columns={entryColumns} rowKey={(row) => row.id} label="Простои периода" exportName={`Простои ${label} — список`} pageSize={100} />
        </>}
    </>}
  </section>;
}
