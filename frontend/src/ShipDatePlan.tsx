import { useEffect, useState } from "react";
import { api, failureText } from "./api";
import { formatDay, formatDayMonth, formatWallMoment } from "./format";
import "./ShipDatePlan.css";

interface ShipTask {
  task_id: string;
  number: string;
  new: boolean;
  project: string;
  item: string;
  name: string;
  quantity: number;
}

/** Ответ /api/production/autoplan/ship-plan: обратный план к дате отгрузки и варианты первой поставки. */
export interface ShipPlan {
  ship_date: string;
  start: string;
  due: string;
  fits: boolean;
  /** Конец работы проекта, уже назначенной вручную или начатой; null — такой нет. */
  assigned_finish: string | null;
  start_by: string | null;
  /** Секунды смены от «сейчас» до самого раннего «начать не позже»; минус — столько не хватает. */
  slack_seconds: number;
  tasks: Array<ShipTask & { latest_start: string; latest_finish: string; slack_seconds: number }>;
  late: Array<ShipTask & { position: number; stage: string; code: string; operator_name: string; latest_start: string; latest_finish: string }>;
  unplaced: Array<ShipTask & { position: number; stage: string; reason: string }>;
  warnings: string[];
  first_delivery: Array<{ kind: "share" | "items" | "full"; ready: string; share: number; lots: Array<ShipTask & { lot: number; total: number }> }>;
}

export const shipPlanApi = {
  preview: (payload: { project: string; people: string[]; ship_date: string }) =>
    api<ShipPlan>("/api/production/autoplan/ship-plan", { method: "POST", safe: true, body: JSON.stringify({ mode: "project", ...payload }) }),
};

const hours = (seconds: number) => `${(Math.abs(seconds) / 3600).toLocaleString("ru-RU", { maximumFractionDigits: 1 })} ч`;

function slackText(seconds: number) {
  if (seconds > 0) return `запас ${hours(seconds)}`;
  if (seconds < 0) return `не хватает ${hours(seconds)}`;
  return "впритык";
}

function shareText(share: number) {
  const percent = Math.round(share * 100);
  return share > 0 && percent === 0 ? "<1 %" : `${percent} %`;
}

function today() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
}

function lotsText(variant: ShipPlan["first_delivery"][number]) {
  if (variant.kind === "full") return "Весь проект";
  const lots = variant.lots.map((lot) => lot.lot === lot.total ? `${lot.item} × ${lot.total}` : `${lot.item} × ${lot.lot} из ${lot.total}`);
  const prefix = variant.kind === "items" ? "Целиком: " : variant.lots.length > 1 ? "Комплекты: " : "";
  return `${prefix}${lots.join(", ")}`;
}

function Verdict({ plan }: { plan: ShipPlan }) {
  const day = formatDayMonth(plan.ship_date);
  const assigned = plan.assigned_finish ? formatWallMoment(plan.assigned_finish) : "";
  const assignedLate = Boolean(plan.assigned_finish && plan.assigned_finish > plan.due);
  const tone = plan.fits ? "tone-ok" : "tone-danger";
  let text: string;
  if (!plan.tasks.length && !plan.unplaced.length) {
    text = assigned
      ? `Вся работа проекта уже назначена и кончится ${assigned} — к ${day} ${assignedLate ? "не успевает" : "успевает"}`
      : "Ставить нечего: работа проекта сделана";
  } else if (plan.fits) {
    text = `Успевает к ${day}: начать не позже ${formatWallMoment(plan.start_by)}, ${slackText(plan.slack_seconds)}`;
  } else if (plan.slack_seconds < 0) {
    text = `Не успевает к ${day}: не хватает ${hours(plan.slack_seconds)} рабочего времени — начинать надо было ${formatWallMoment(plan.start_by)}`;
  } else if (plan.unplaced.length) {
    text = `Не успевает к ${day}: часть этапов некому поставить — причины ниже`;
  } else {
    text = `Не успевает к ${day}: назначенная вручную работа кончится ${assigned}`;
  }
  return <div className={`ship-plan-verdict ${tone}`} role="status">{text}</div>;
}

/** «От даты отгрузки» в автоплане «По проекту»: когда начать, чтобы успеть, где не успеваем и что отгрузить первым.
 *  Только подсказка — ничего не назначает. */
export function ShipDatePlan({ project, people, deadline }: { project: string; people: string[]; deadline: string | null }) {
  const [shipDate, setShipDate] = useState(deadline ?? "");
  const [plan, setPlan] = useState<ShipPlan | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const peopleKey = people.join(",");

  useEffect(() => { setShipDate(deadline ?? ""); }, [project, deadline]);
  useEffect(() => { setPlan(null); setError(""); }, [project, peopleKey, shipDate]);

  async function run() {
    setLoading(true);
    setError("");
    try {
      setPlan(await shipPlanApi.preview({ project, people, ship_date: shipDate }));
    } catch (reason) {
      setPlan(null);
      setError(failureText(reason, "План от даты отгрузки не рассчитан"));
    } finally {
      setLoading(false);
    }
  }

  const ready = Boolean(shipDate) && people.length > 0;
  return <section className="ship-plan" aria-label="От даты отгрузки">
    <div className="ship-plan-bar">
      <b>От даты отгрузки</b>
      <label>Отгрузка<input type="date" aria-label="Дата отгрузки" min={today()} value={shipDate} onChange={(event) => setShipDate(event.target.value)} /></label>
      <button type="button" className="secondary small" disabled={!ready || loading} title={people.length ? undefined : "Отметьте людей на проект"} onClick={() => void run()}>
        {shipDate ? `Рассчитать к ${formatDay(shipDate)}` : "Рассчитать"}
      </button>
      {!plan && !loading && <small className="ship-plan-hint">когда начать, чтобы успеть, и что отгрузить первым</small>}
    </div>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {loading && <div className="empty">Считаем от даты отгрузки…</div>}
    {plan && !loading && <div className="ship-plan-body">
      <Verdict plan={plan} />
      {plan.warnings.map((warning) => <div key={warning} className="warning-inline">{warning}</div>)}
      {plan.tasks.length > 0 && <div className="ship-plan-scroll"><table className="ship-plan-table">
        <thead><tr><th>КБ</th><th className="num">Тираж</th><th>Начать не позже</th><th>Закончить к</th><th>Запас</th></tr></thead>
        <tbody>{plan.tasks.map((row) => <tr key={row.task_id} className={row.slack_seconds < 0 ? "late" : undefined}>
          <td><b>{row.item}</b>{row.new && <em className="ship-plan-new">новое задание</em>}<span className="ship-plan-name">{row.name}</span></td>
          <td className="num">{row.quantity}</td>
          <td>{formatWallMoment(row.latest_start)}</td>
          <td>{formatWallMoment(row.latest_finish)}</td>
          <td className={row.slack_seconds < 0 ? "tone-danger" : undefined}>{slackText(row.slack_seconds)}</td>
        </tr>)}</tbody>
      </table></div>}
      {plan.late.length > 0 && <details className="ship-plan-list">
        <summary>Где не успеваем · {plan.late.length} {plan.late.length === 1 ? "этап" : plan.late.length < 5 ? "этапа" : "этапов"}</summary>
        {plan.late.map((row) => <div key={`${row.task_id}:${row.position}`} className="ship-plan-line ship-plan-op">
          <b>{row.item} · этап {row.position}</b><span>{row.stage}</span><span>{row.operator_name}</span><span>начать {formatWallMoment(row.latest_start)}</span>
        </div>)}
      </details>}
      {plan.unplaced.length > 0 && <div className="ship-plan-list">
        {plan.unplaced.map((row) => <div key={`${row.task_id}:${row.position}`} className="ship-plan-line"><b>{row.item} · этап {row.position}</b><span>{row.reason}</span></div>)}
      </div>}
      {plan.first_delivery.length > 0 && <>
        <h3 className="ship-plan-title">Первая поставка — что сделать первым</h3>
        <div className="ship-plan-scroll"><table className="ship-plan-table">
          <thead><tr><th>Готово к</th><th>Что в поставке</th><th className="num">Доля работы</th><th /></tr></thead>
          <tbody>{plan.first_delivery.map((variant, index) => {
            const late = variant.ready.slice(0, 10) > plan.ship_date;
            return <tr key={`${variant.kind}:${variant.ready}`}>
              <td className={late ? "tone-danger" : undefined}>{formatWallMoment(variant.ready)}</td>
              <td className="ship-plan-lots">{lotsText(variant)}</td>
              <td className="num">{shareText(variant.share)}</td>
              <td className="ship-plan-mark">{index === 0 && plan.first_delivery.length > 1 ? "раньше всего" : late ? "позже отгрузки" : ""}</td>
            </tr>;
          })}</tbody>
        </table></div>
      </>}
    </div>}
  </section>;
}
