import { useEffect, useState } from "react";
import { DataTable, type DataColumn } from "./DataTable";
import { employeeCardApi, failureText, type WorkTimeDay, type WorkTimeView } from "./api";
import { formatDate } from "./format";

/** Вкладка «Рабочее время»: месяц по дням (смена-норма, отработано, переработка; отсутствия и отгулы отмечены, будущие
 *  дни приглушены), итоги месяца и баланс года — переработка минус отгулы. Данные выработки; переработка справочная. */

const TONE: Record<string, string> = { debt: "tone-danger", overtime: "tone-ok", even: "tone-neutral" };

function hoursText(hours: number | null | undefined) {
  return hours === null || hours === undefined ? "—" : String(Math.round(hours * 100) / 100).replace(".", ",");
}

function shiftMonth(month: string, delta: number) {
  const [year, number] = month.split("-").map(Number);
  const moment = new Date(Date.UTC(year, number - 1 + delta, 1));
  return `${moment.getUTCFullYear()}-${String(moment.getUTCMonth() + 1).padStart(2, "0")}`;
}

function note(day: WorkTimeDay) {
  if (day.day_off) return "Отгул";
  if (day.absence) return day.absence.charAt(0).toUpperCase() + day.absence.slice(1);
  return day.workday ? "" : "Выходной";
}

const COLUMNS: DataColumn<WorkTimeDay>[] = [
  { key: "date", title: "Дата", width: 90, value: (day) => day.date, render: (day) => formatDate(day.date).slice(0, 5) },
  { key: "weekday", title: "День", width: 60, value: (day) => day.weekday },
  { key: "norm", title: "Смена, ч", width: 100, numeric: true, value: (day) => day.norm_hours, render: (day) => day.norm_hours ? hoursText(day.norm_hours) : "—" },
  {
    key: "worked", title: "Отработано, ч", width: 120, numeric: true, value: (day) => day.worked_hours,
    render: (day) => day.worked_hours === null || (day.worked_hours === 0 && !day.norm_hours) ? "—" : hoursText(day.worked_hours),
  },
  { key: "overtime", title: "Переработка, ч", width: 130, numeric: true, value: (day) => day.overtime_hours, render: (day) => day.overtime_hours ? hoursText(day.overtime_hours) : "—" },
  { key: "note", title: "Примечание", wrap: true, value: (day) => note(day), render: (day) => day.absence || day.day_off ? <strong>{note(day)}</strong> : note(day) },
];

export function WorkTime({ userId }: { userId?: string }) {
  const [month, setMonth] = useState<string | undefined>(undefined);
  const [view, setView] = useState<WorkTimeView | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let live = true;
    setError("");
    employeeCardApi.worktime(month, userId)
      .then((loaded) => { if (live) setView(loaded); })
      .catch((reason) => { if (live) setError(failureText(reason, "Рабочее время не загружено")); });
    return () => { live = false; };
  }, [month, userId]);

  const current = view?.month ?? month;
  const picker = <div className="wt-picker" role="group" aria-label="Месяц">
    <button className="secondary small" disabled={!current} aria-label="Предыдущий месяц" onClick={() => current && setMonth(shiftMonth(current, -1))}>←</button>
    <input type="month" aria-label="Месяц" value={current ?? ""} onChange={(event) => event.target.value && setMonth(event.target.value)} />
    <button className="secondary small" disabled={!current} aria-label="Следующий месяц" onClick={() => current && setMonth(shiftMonth(current, 1))}>→</button>
    {view && <strong className="wt-label">{view.label}</strong>}
  </div>;

  if (error) return <div className="lv-page">{picker}<div className="error-banner" role="alert">{error}</div></div>;
  if (!view) return <div className="lv-page">{picker}<div className="empty">Загрузка…</div></div>;

  const { balance, totals } = view;
  return <div className="lv-page">
    <section className="panel lv-summary" aria-label="Месяц">
      <h2>Рабочее время</h2>
      {picker}
      {view.available && totals && <dl className="lv-balance">
        <div><dt>Норма</dt><dd>{hoursText(totals.norm_hours)} ч</dd></div>
        <div><dt>Отработано</dt><dd>{hoursText(totals.worked_hours)} ч</dd></div>
        <div><dt>Переработка</dt><dd>{hoursText(totals.overtime_hours)} ч</dd></div>
      </dl>}
    </section>

    {!view.available && <p className="panel empty wt-nodata" role="status">{view.no_data_reason || "Нет данных."}</p>}

    {view.available && balance && <section className="panel lv-summary wt-balance" aria-label={`Баланс ${balance.year} года`}>
      <h2>Баланс {balance.year} года</h2>
      <dl className="lv-balance">
        <div><dt>Период</dt><dd>{formatDate(balance.from)} – {formatDate(balance.to)}</dd></div>
        <div><dt>Переработка</dt><dd>{hoursText(balance.overtime_hours)} ч</dd></div>
        <div><dt>Отгулы</dt><dd>{balance.day_off_days} дн. · {hoursText(balance.day_off_hours)} ч</dd></div>
        <div><dt>Баланс</dt><dd className={`wt-state ${TONE[balance.state] ?? ""}`}>{balance.balance_hours > 0 ? "+" : ""}{hoursText(balance.balance_hours)} ч · {balance.label}</dd></div>
      </dl>
      <p className="ec-note wt-source" title={view.note}>{view.source}; переработка справочная</p>
    </section>}

    {view.available && <section className="panel lv-block" aria-label="Дни месяца">
      <h2>Дни месяца<span className="page-count">{view.days.length}</span></h2>
      <DataTable rows={view.days} columns={COLUMNS} rowKey={(day) => day.date} hideSearch pageSize={40}
        exportName={`Рабочее время ${view.label}`}
        rowClassName={(day) => [day.future ? "wt-future" : "", !day.workday || day.absence ? "wt-off" : ""].join(" ").trim()} />
    </section>}
  </div>;
}
