import { type ProjectForecast, type Shipment } from "./api";
import { formatDate } from "./format";
import { isoDay } from "./Gantt";
import { daysUntil } from "./productionGraph";

/* Общие мелочи списков отправок: срок, просрочка, проекты отправки и решение «Упаковывать». */

const ACTIVE = ["Новая", "Собрана", "Упакована"];
const today = () => isoDay(new Date());

/** Срок прошёл, а груз ещё не уехал. */
export function overdue(shipment: Shipment) {
  return Boolean(shipment.deadline) && String(shipment.deadline) < today() && ACTIVE.includes(shipment.status);
}

/** Срок одной строкой: «16.10.2026 · просрочено на 3 дн.» (владелец 15.10: красная дата без слов не читается). Срок раньше
 *  заведения отправки — почти наверняка опечатка в дате (16.01 вместо 16.10): подсказка просит её проверить. */
export function Due({ day, late, since }: { day: string | null; late: boolean; since?: string | null }) {
  if (!day) return <span className="muted">—</span>;
  const behind = late ? Math.max(1, -daysUntil(day)) : 0;
  const odd = late && Boolean(since) && day < String(since).slice(0, 10);
  const title = odd ? `Просрочено на ${behind} дн.: срок раньше даты заведения ${formatDate(String(since).slice(0, 10))} — похоже на опечатку в дате` : undefined;
  return <span className={late ? "ship-due tone-danger" : "ship-due"} title={title}>{formatDate(day)}
    {late && <span className="ship-due-late">{odd ? " · проверьте дату" : ` · просрочено на ${behind} дн.`}</span>}</span>;
}

/** Прогноз готовности отправки по плану цеха: самый поздний из её проектов; уехавшая и отменённая — без прогноза.
 *  Не успевает — прогноз позже срока отправки (срока нет — цех сам говорит «не успевает»). */
export function readiness(projects: string[], forecasts: Record<string, ProjectForecast>, deadline: string | null, status = ""): { ready: string; late: boolean } | null {
  if (status && !ACTIVE.includes(status)) return null;
  const found = projects.map((name) => forecasts[name]).filter((item): item is ProjectForecast => Boolean(item?.ready));
  if (!found.length) return null;
  const ready = found.map((item) => item.ready).sort().at(-1)!;
  return { ready, late: deadline ? ready > deadline : found.some((item) => item.status === "не успевает") };
}

/** «прогноз готовности 05.11.2026» — красным, если позже срока. */
export function ReadyForecast({ forecast }: { forecast: { ready: string; late: boolean } | null }) {
  if (!forecast) return null;
  return <span className={forecast.late ? "ship-forecast tone-danger" : "ship-forecast"} title={forecast.late ? "По плану цеха проект будет готов позже срока" : "Прогноз цеха по «Загрузке»"}>
    прогноз готовности {formatDate(forecast.ready)}</span>;
}

/** «591К.3: КБ1, КБ2; 600К.1: КБ7». */
export function projectsOf(shipment: Shipment) {
  const byProject = new Map<string, string[]>();
  for (const line of shipment.lines) {
    const items = byProject.get(line.project) ?? [];
    if (!items.includes(line.item_index)) items.push(line.item_index);
    byProject.set(line.project, items);
  }
  return [...byProject].map(([project, items]) => `${project}: ${items.join(", ")}`).join("; ");
}

export { PackDecision } from "./PackingPermissionWindow";
