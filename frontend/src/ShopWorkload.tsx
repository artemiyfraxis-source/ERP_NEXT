import { useEffect, useMemo, useState } from "react";
import { failureText, productionApi, type ShopWorkload, type WorkloadKb, type WorkloadProject, type WorkloadWeekSite } from "./api";
import { DataTable, type DataColumn } from "./DataTable";
import { StatusChip } from "./StatusChip";
import type { Tone } from "./statusTone";
import { formatDay, formatDayMonth, formatNumber, plural } from "./format";
import "./workload.css";

/* «Загрузка» (владелец 29.10.2026, идея №1): загрузка цеха и прогноз готовности без заданий — по выданным ЭТК, фактам
   «сделано», календарю цеха и монтажникам площадок. Только чтение: расчёт целиком на сервере (production/workload.py). */

const ALL = "";
const STATUS_TONE: Record<string, Tone> = { "в срок": "success", "риск": "warning", "не успевает": "danger", "без срока": "neutral" };
const SOURCE_TEXT: Record<string, string> = {
  "сдано цехом": "сдано цехом по заданиям",
  "принято складом": "принято складом готовой продукции",
  "отгружено": "отгружено с завода",
};

function hours(value: number | undefined): string {
  return value ? formatNumber(value, { digits: 1 }) : "—";
}

function kindsText(kinds: Record<string, number>, order: string[], top = 3): string {
  return order.filter((kind) => kinds[kind] > 0).sort((a, b) => kinds[b] - kinds[a]).slice(0, top)
    .map((kind) => `${kind} ${formatNumber(kinds[kind], { digits: 1 })}`).join(" · ");
}

function weekText(start: string, end: string): string {
  return `${formatDayMonth(start)}–${formatDayMonth(end)}`;
}

function statusTitle(row: WorkloadProject, horizon: string): string {
  if (row.status === "без срока") return row.ready ? "Срока нет — в прогнозе после проектов со сроком" : `Срока нет, не помещается до ${formatDay(horizon)}`;
  if (!row.ready) return `Не помещается до ${formatDay(horizon)}: не хватает мощности площадки`;
  const slack = row.slack_days ?? 0;
  if (slack < 0) return `Опоздание ${-slack} ${plural(-slack, ["день", "дня", "дней"])} к сроку`;
  return `Запас ${slack} ${plural(slack, ["день", "дня", "дней"])} до срока`;
}

function doneTitle(kb: WorkloadKb): string {
  if (!kb.done_source) return "Сделанного нет: нет сдач цеха, приёмки склада и отгрузки";
  return `Сделано ${formatNumber(kb.done)} шт — ${SOURCE_TEXT[kb.done_source] ?? kb.done_source}. Берётся наибольший факт: сдано цехом, принято складом, отгружено`;
}

export function ShopWorkloadView() {
  const [data, setData] = useState<ShopWorkload | null>(null);
  const [error, setError] = useState("");
  const [site, setSite] = useState(ALL);

  useEffect(() => {
    let live = true;
    productionApi.workload()
      .then((result) => { if (live) setData(result); })
      .catch((reason) => { if (live) setError(failureText(reason, "Загрузка цеха не посчитана")); });
    return () => { live = false; };
  }, []);

  const rows = useMemo(() => (data?.projects ?? []).filter((row) => site === ALL || (row.by_site[site] ?? 0) > 0), [data, site]);

  if (error) return <div className="error-banner" role="alert">{error}</div>;
  if (!data) return <div className="empty">Загрузка…</div>;

  const unassigned = data.unassigned;
  const columns: DataColumn<WorkloadProject>[] = [
    { key: "project", title: "Проект", value: (row) => row.project, render: (row) => <b>{row.project}</b> },
    { key: "customer", title: "Заказчик", value: (row) => row.customer, render: (row) => row.customer || <span className="muted">—</span>, filter: true },
    { key: "deadline", title: "Срок", value: (row) => row.deadline ?? "", render: (row) => row.deadline ? <span className="num">{formatDay(row.deadline)}</span> : <span className="muted">—</span> },
    { key: "hours", title: "Осталось, ч", numeric: true, value: (row) => row.hours, render: (row) => <b className="num">{hours(row.hours)}</b>,
      hint: "Нормочасы ЭТК × (тираж КБ − сделано)" },
    ...data.sites.map((name): DataColumn<WorkloadProject> => ({
      key: `site:${name}`, title: `${name}, ч`, numeric: true, value: (row) => row.by_site[name] ?? 0,
      render: (row) => <span className="num">{hours(row.by_site[name])}</span>,
    })),
    { key: "unassigned", title: "Не распр., ч", numeric: true, value: (row) => row.by_site[unassigned] ?? 0,
      render: (row) => <span className="num">{hours(row.by_site[unassigned])}</span>,
      hint: "Нет заданий с исполнителями — площадка не определена; в прогнозе берёт свободную мощность любой площадки" },
    { key: "kinds", title: "Виды операций, ч", value: (row) => kindsText(row.by_kind, data.kinds), wrap: true,
      render: (row) => <span className="wl-kinds">{kindsText(row.by_kind, data.kinds) || "—"}</span> },
    { key: "ready", title: "Расчётная готовность", value: (row) => row.ready ?? "9999",
      render: (row) => row.ready ? <span className="num">{formatDay(row.ready)}</span> : <span className="danger-text">позже {formatDay(data.horizon_to)}</span> },
    { key: "status", title: "Статус", value: (row) => row.status, filter: true,
      render: (row) => <StatusChip tone={STATUS_TONE[row.status] ?? "neutral"} title={statusTitle(row, data.horizon_to)}>{row.status}</StatusChip> },
  ];
  const late = data.projects.filter((row) => row.status === "не успевает").length;
  const risk = data.projects.filter((row) => row.status === "риск").length;
  const total = data.projects.reduce((sum, row) => sum + row.hours, 0);
  const people = data.sites.map((name) => `${name} ${data.people[name] ?? 0} чел.`).join(", ");
  const stripSites = site === ALL ? [data.shop, ...data.sites] : site === unassigned ? [data.shop] : [site];

  return <div className="wl">
    <div className="wl-head">
      <div className="seg-tabs wl-sites" role="radiogroup" aria-label="Участок">
        {[ALL, ...data.sites, unassigned].map((name) => <button type="button" role="radio" key={name || "all"} aria-checked={site === name}
          className={site === name ? "on" : undefined} onClick={() => setSite(name)}>{name === ALL ? "Все участки" : name === unassigned ? "Не распределено" : name}</button>)}
      </div>
      <span className="wl-summary">
        на {formatDay(data.today)} · осталось <b className="num">{formatNumber(total, { digits: 0 })} ч</b> в {data.projects.length} {plural(data.projects.length, ["проекте", "проектах", "проектах"])}
        {late > 0 && <> · <span className="danger-text">не успевают: {late}</span></>}
        {risk > 0 && <> · <span className="warning-text">риск: {risk}</span></>}
        {" "}· смена {formatNumber(data.shift_hours, { digits: 1 })} ч чистого времени · {people}
      </span>
    </div>

    <WeekStrip data={data} sites={stripSites} />

    {data.no_run.length > 0 && <div className="warning-inline wl-note">Без тиража КБ — часы не посчитаны: {data.no_run.join(", ")}. Задайте тираж в наборе проекта.</div>}

    <DataTable
      label="Загрузка по проектам"
      rows={rows}
      columns={columns}
      rowKey={(row) => row.project}
      storageKey="production-workload"
      presets={[
        { label: "Не успевают", test: (row) => row.status === "не успевает" },
        { label: "Риск", test: (row) => row.status === "риск" },
        { label: "В срок", test: (row) => row.status === "в срок" },
        { label: "Без срока", test: (row) => row.status === "без срока" },
      ]}
      searchPlaceholder="№ проекта или заказчик"
      emptyText={site === ALL ? "Работы по выданным ЭТК не осталось — всё сделано или ЭТК ещё не выданы." : "На этом участке работы нет."}
      exportName="Загрузка цеха"
      renderDetail={(row) => <KbTable row={row} kinds={data.kinds} />}
      rowTitle={() => "КБ проекта — по щелчку"}
    />

    <details className="wl-how">
      <summary>Как считается</summary>
      <ul>
        <li>Осталось часов по КБ = нормочасы выданной ЭТК на изделие (как у операций задания, без подготовки) × (тираж КБ − сделано).</li>
        <li>Сделано — наибольший из фактов: сдано цехом по заданиям, принято складом, отгружено; источник — в подсказке.</li>
        <li>Участок КБ — по заданию: отдано в ЖУК или назначено монтажникам площадки; иначе «не распределено».</li>
        <li>Мощность недели = монтажники участка без отсутствующих × чистое время смены × рабочие дни (праздники и отпуска — из «Календаря цеха»).</li>
        <li>Прогноз: проекты по сроку, затем по дате открытия, занимают свободные часы с сегодняшнего дня; «не распределено» — часы любого участка. Готовность позже срока — «не успевает», ближе 3 дней — «риск».</li>
        <li>Полоса недели — «нужно к срокам» (часы проектов со сроком поровну по рабочим дням до срока) против мощности; больше мощности — перегруз.</li>
      </ul>
    </details>
  </div>;
}

function KbTable({ row, kinds }: { row: WorkloadProject; kinds: string[] }) {
  return <table className="grid wl-kbs" aria-label={`КБ проекта ${row.project}`}>
    <thead><tr><th>КБ</th><th>Наименование</th><th className="num">Тираж</th><th className="num">Сделано</th><th className="num">Осталось, шт</th><th className="num">Норма на изд., ч</th><th className="num">Часов</th><th>Участок</th><th>Виды операций, ч</th></tr></thead>
    <tbody>{row.kbs.map((kb) => <tr key={kb.index}>
      <td><b>{kb.index}</b></td>
      <td>{kb.name || <span className="muted">—</span>}</td>
      <td className="num">{kb.run ?? <span className="danger-text">нет тиража</span>}</td>
      <td className="num" title={doneTitle(kb)}>{kb.done ? formatNumber(kb.done) : "—"}{kb.done_source && <small className="wl-source"> {kb.done_source}</small>}</td>
      <td className="num">{formatNumber(kb.remaining)}</td>
      <td className="num">{formatNumber(kb.unit_hours, { digits: 3 })}</td>
      <td className="num"><b>{hours(kb.hours)}</b></td>
      <td title={kb.site_note}>{kb.site}</td>
      <td className="wl-kinds">{kindsText(kb.by_kind, kinds, 4) || "—"}</td>
    </tr>)}</tbody>
  </table>;
}

function cellTitle(name: string, week: { start: string; end: string }, cell: WorkloadWeekSite, kinds: string[]): string {
  const top = kindsText(cell.kinds, kinds);
  return [
    `${name}, неделя ${formatDay(week.start)}–${formatDay(week.end)}`,
    `Нужно к срокам: ${formatNumber(cell.demand, { digits: 1 })} ч`,
    `Мощность: ${formatNumber(cell.capacity, { digits: 1 })} ч`,
    `По прогнозу занято: ${formatNumber(cell.planned, { digits: 1 })} ч`,
    ...(top ? [`Больше всего: ${top}`] : []),
  ].join("\n");
}

function WeekStrip({ data, sites }: { data: ShopWorkload; sites: string[] }) {
  const overloads = data.bottlenecks.filter((item) => sites.includes(item.site));
  return <section className="panel wl-strip-panel" aria-label="Загрузка по неделям">
    <div className="wl-strip-head">
      <h2>Нужно к срокам и мощность по неделям</h2>
      <span className="wl-legend" aria-hidden="true"><i className="wl-key need" />нужно к срокам <i className="wl-key cap" />мощность <i className="wl-key over" />перегруз</span>
    </div>
    <div className="wl-strip-wrap">
      <table className="wl-strip">
        <thead><tr><th scope="col">Участок</th>{data.weeks.map((week) => <th scope="col" key={week.start} className="num">{weekText(week.start, week.end)}</th>)}</tr></thead>
        <tbody>{sites.map((name) => <tr key={name}>
          <th scope="row">{name}{name !== data.shop && <small> · {data.people[name] ?? 0} чел.</small>}</th>
          {data.weeks.map((week) => {
            const cell = week.sites[name] ?? { capacity: 0, demand: 0, planned: 0, kinds: {} };
            const over = cell.demand > cell.capacity;
            const share = cell.capacity > 0 ? Math.min(cell.demand / cell.capacity, 1) : cell.demand > 0 ? 1 : 0;
            const percent = cell.capacity > 0 ? Math.round((cell.demand / cell.capacity) * 100) : null;
            const title = cellTitle(name, week, cell, data.kinds);
            return <td key={week.start} title={title} aria-label={title} className={over ? "over" : undefined}>
              <span className="wl-track"><i style={{ width: `${share * 100}%` }} /></span>
              <span className="wl-pct num">{cell.capacity <= 0 && cell.demand <= 0 ? "—" : percent === null ? "нет мощности" : `${percent} %`}</span>
            </td>;
          })}
        </tr>)}</tbody>
      </table>
    </div>
    {overloads.length > 0
      ? <div className="wl-bottlenecks"><b>Узкие места:</b> {overloads.slice(0, 4).map((item) => <span key={`${item.site}:${item.start}`}>
          {item.site}, {formatDayMonth(item.start)} — {item.kinds.map(([kind, value]) => `${kind} ${formatNumber(value, { digits: 0 })} ч`).join(", ")}
          {" "}(нужно {formatNumber(item.demand, { digits: 0 })} ч при мощности {formatNumber(item.capacity, { digits: 0 })} ч)</span>)}</div>
      : <div className="wl-bottlenecks muted">Перегруза к срокам на {data.weeks.length} {plural(data.weeks.length, ["неделю", "недели", "недель"])} нет.</div>}
  </section>;
}
