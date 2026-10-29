import { useEffect, useState } from "react";
import { ApiError, failureText, shippingApi, type ProjectAddressee, type Shipment } from "./api";
import { formatDate } from "./format";
import { isoDay } from "./Gantt";
import { intakeApi } from "./productionIntakeApi";
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

/** Переключатель «Упаковывать» проекта (этапы «Готово» и «Принято»): решение менеджера, после него кладовщик может паковать.
 *  Карточку проекта берёт сам, если её не дали; после смены показывает, кто и когда решил. */
export function PackDecision({ project, editable, card: given, onCard }: {
  project: string; editable: boolean; card?: Partial<ProjectAddressee> | null; onCard?: (card: ProjectAddressee) => void;
}) {
  const [card, setCard] = useState<Partial<ProjectAddressee> | null | undefined>(given);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  useEffect(() => { if (given !== undefined) setCard(given); }, [given]);
  useEffect(() => {
    if (given !== undefined) return;
    let live = true;
    intakeApi.dispatch(project).then((result) => { if (live) setCard(result.addressee ?? null); }).catch(() => { /* переключатель останется без отметки */ });
    return () => { live = false; };
  }, [project, given]);
  const decided = Boolean(card?.pack_decided);
  async function toggle(next: boolean) {
    if (busy) return;
    setBusy(true); setProblem("");
    try {
      const { addressee } = await shippingApi.packDecision(project, card?.version ?? 0, next);
      setCard(addressee); onCard?.(addressee);
    } catch (reason) {
      setProblem(reason instanceof ApiError && reason.status === 409 ? "Данные проекта уже изменил другой сотрудник. Закройте окно и откройте заново." : failureText(reason, "Решение не сохранено"));
    } finally { setBusy(false); }
  }
  const when = card?.pack_decided_at ? formatDate(String(card.pack_decided_at).slice(0, 10)) : "";
  return <div className="ship-pack-decision">
    <label className="inline-check"><input type="checkbox" aria-label={`Упаковывать ${project}`} checked={decided} disabled={!editable || busy || card === undefined}
      onChange={(event) => void toggle(event.target.checked)} /> Упаковывать <span className="muted">{project}</span></label>
    {decided && <span className="muted"> — решил{card?.pack_decided_by ? ` ${card.pack_decided_by}` : ""}{when ? `, ${when}` : ""}; склад может паковать</span>}
    {!decided && card !== undefined && <span className="muted"> — склад ждёт решения</span>}
    {problem && <span className="tone-danger" role="alert"> {problem}</span>}
  </div>;
}
