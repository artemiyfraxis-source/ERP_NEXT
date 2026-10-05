import { useMemo, useState } from "react";
import type { ProductionAbsence, ProductionOperation, ProductionOperator, ProductionShare, ProductionShift, ProductionTask } from "./api";
import { deadlineEnd } from "./Gantt";
import { DEFAULT_SHIFT, dayHours, sharesOf, workingDay } from "./MasterBoard";
import { feedsText } from "./productionGraph";

export const LOAD_DAYS = 14;

export type LoadItem = {
  key: string;
  pz: string;
  operation: string;
  feeds: string;
  start: number;
  finish: number;
  done: boolean;
  late: boolean;
  deadline: string | null;
  quantity: number | null;
  task: ProductionTask;
  op: ProductionOperation;
  share: ProductionShare;
};

export type PersonLoad = {
  operator: ProductionOperator;
  items: LoadItem[];
  total: number;
  hoursByDay: Record<string, number>;
};

const pad = (n: number) => String(n).padStart(2, "0");
export const isoOf = (date: Date) => `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
const dayMonth = (day: string) => `${day.slice(8, 10)}.${day.slice(5, 7)}`;
const oneDecimal = (value: number) => value.toLocaleString("ru-RU", { maximumFractionDigits: 1 });
const DAY_MS = 86_400_000;

function itemId(task: ProductionTask) {
  return task.item.toUpperCase().startsWith(task.project.toUpperCase()) ? task.item : `${task.project}${task.item.toUpperCase()}`;
}

export function loadDays(week: number, now = new Date()): string[] {
  const monday = new Date(now.getFullYear(), now.getMonth(), now.getDate() - ((now.getDay() + 6) % 7) + 7 * week);
  return Array.from({ length: LOAD_DAYS }, (_, index) => isoOf(new Date(monday.getFullYear(), monday.getMonth(), monday.getDate() + index)));
}

const dayStart = (day: string) => new Date(`${day}T00:00:00`).getTime();
const itemsOnDay = (items: LoadItem[], day: string) => items.filter((item) => item.finish > dayStart(day) && item.start < dayStart(day) + DAY_MS);

// Часы одной доли в день: dayHours пропускает выполненные, поэтому считаем её как невыполненную.
function itemDayHours(operatorId: string, day: string, item: LoadItem, shift: ProductionShift) {
  const operation = { ...item.op, state: "не начата" as const, shares: [{ ...item.share, operator_id: operatorId, finished_at: null }] };
  return dayHours(operatorId, day, [{ ...item.task, operations: [operation] }], shift);
}

/** Загрузка человека по дням: часы пересечения плановых окон его долей со сменой (как «Монтажники» в Ресурсном планировании). */
export function peopleLoad(operators: ProductionOperator[], tasks: ProductionTask[], days: string[], shift: ProductionShift): PersonLoad[] {
  const live = tasks.filter((task) => task.status !== "Отменено" && !task.handed_to);
  const rows = operators.map((operator) => {
    const items: LoadItem[] = [];
    for (const task of live) {
      for (const op of task.operations) {
        for (const share of sharesOf(op, operator.id)) {
          if (!share.planned_start || !share.planned_finish) continue;
          const finish = new Date(share.planned_finish).getTime();
          const done = op.state === "выполнена" || Boolean(share.finished_at);
          const end = deadlineEnd(task.deadline);
          items.push({
            key: `${task.id}-${op.id}-${items.length}`, pz: `${task.number} · ${itemId(task)}`, operation: `${op.position}. ${op.stage || op.description}`,
            feeds: feedsText(task, op), start: new Date(share.planned_start).getTime(), finish, done,
            late: Boolean(!done && end && finish > end.getTime()), deadline: task.deadline, quantity: share.quantity, task, op, share,
          });
        }
      }
    }
    const hoursByDay: Record<string, number> = {};
    let total = 0;
    for (const day of days) {
      let sum = 0;
      for (const item of itemsOnDay(items, day)) sum += itemDayHours(operator.id, day, item, shift);
      if (sum > 0.05) { hoursByDay[day] = sum; total += sum; }
    }
    return { operator, items, total, hoursByDay };
  });
  return rows.sort((a, b) => Number(b.total > 0) - Number(a.total > 0) || a.operator.name.localeCompare(b.operator.name, "ru"));
}

const dayLabel = (day: string) => new Date(`${day}T00:00:00`).toLocaleDateString("ru-RU", { weekday: "short" });

export function PeopleLoadGrid({ operators, tasks, absences, holidays, shift, week, onWeekChange }: {
  operators: ProductionOperator[];
  tasks: ProductionTask[];
  absences: ProductionAbsence[];
  holidays: string[];
  shift?: ProductionShift;
  week: number;
  onWeekChange: (week: number) => void;
}) {
  const [onlyBusy, setOnlyBusy] = useState(false);
  const [open, setOpen] = useState<{ id: string; day: string } | null>(null);
  const workShift = shift && shift.weekdays ? shift : DEFAULT_SHIFT;
  const capacity = Number(workShift.effective_hours || 7);
  const days = useMemo(() => loadDays(week), [week]);
  const today = isoOf(new Date());
  const people = useMemo(() => peopleLoad(operators, tasks, days, workShift), [operators, tasks, days, workShift]);
  const shown = onlyBusy ? people.filter((person) => person.total > 0.05) : people;
  const period = `${dayMonth(days[0])}–${dayMonth(days[days.length - 1])}`;
  const detail = open ? people.find((person) => person.operator.id === open.id) : undefined;

  if (!operators.length) return <div className="empty">Сотрудников цеха пока нет.</div>;

  return <div className="pl">
    <div className="pl-head">
      <b className="pl-period" title="Период загрузки">{period}</b>
      <label className="pl-only"><input type="checkbox" checked={onlyBusy} onChange={(event) => setOnlyBusy(event.target.checked)} /> Только занятые</label>
      <div className="mb-week-nav">
        <button className="secondary small" onClick={() => onWeekChange(week - 1)}>← неделя</button>
        <button className="secondary small" onClick={() => onWeekChange(0)} disabled={week === 0}>Сегодня</button>
        <button className="secondary small" onClick={() => onWeekChange(week + 1)}>неделя →</button>
      </div>
    </div>
    <table className="mb-week pl-table">
      <thead><tr>
        <th scope="col">Сотрудник</th>
        {days.map((day) => <th key={day} scope="col" className={`${workingDay(day, workShift, holidays) ? "" : "off"}${day === today ? " today" : ""}`}>{dayLabel(day)}<span>{dayMonth(day)}</span></th>)}
      </tr></thead>
      <tbody>{shown.map((person) => {
        const free = person.total <= 0.05;
        return <tr key={person.operator.id} className={free ? "pl-free" : ""}>
          <th scope="row"><b>{person.operator.name}</b>
            <small>{person.operator.site ? `${person.operator.site} · ` : ""}{free ? "свободен" : `${oneDecimal(person.total)} ч за 2 нед.`}</small></th>
          {days.map((day) => {
            const absent = absences.find((item) => item.operator_id === person.operator.id && item.from_date <= day && day <= item.to_date);
            const working = workingDay(day, workShift, holidays);
            const load = working && !absent ? person.hoursByDay[day] ?? 0 : 0;
            const level = load <= 0.05 ? "free" : load < capacity * 0.5 ? "low" : load <= capacity + 0.05 ? "full" : "over";
            const inDay = itemsOnDay(person.items, day);
            const late = inDay.some((item) => item.late);
            const text = absent ? absent.kind.slice(0, 3).toLowerCase() : !working ? "" : `${oneDecimal(load)}/${oneDecimal(capacity)}`;
            const title = `${person.operator.name}, ${dayMonth(day)}: ${absent ? absent.kind : !working ? "нерабочий день" : `${oneDecimal(load)} из ${oneDecimal(capacity)} ч${late ? " · есть позже дедлайна" : ""}`}`;
            const picked = open?.id === person.operator.id && open.day === day;
            const className = `mb-cell ${absent ? "absent" : !working ? "off" : level}${late ? " late" : ""}${day === today ? " today" : ""}${picked ? " picked" : ""}`;
            const clickable = !absent && inDay.length > 0;
            return <td key={day} className={className}>{clickable
              ? <button title={title} aria-label={`Загрузка ${person.operator.name} ${dayMonth(day)}`} aria-expanded={picked}
                onClick={() => setOpen(picked ? null : { id: person.operator.id, day })}>{text}{late ? <i className="pl-late" aria-hidden>!</i> : null}</button>
              : <span title={title}>{text}</span>}</td>;
          })}
        </tr>;
      })}</tbody>
    </table>
    {!shown.length && <div className="empty">Нет занятых сотрудников за {period}.</div>}
    {open && detail && <DayDetail person={detail} day={open.day} capacity={capacity} onClose={() => setOpen(null)} />}
    <div className="mb-legend"><span className="mb-cell free">0/7</span> свободен <span className="mb-cell low">3/7</span> частично <span className="mb-cell full">7/7</span> смена занята <span className="mb-cell over">9/7</span> перегруз <span className="mb-cell absent">отп</span> отсутствует <span className="mb-cell off">&nbsp;</span> выходной <span className="mb-cell low late">!</span> есть позже дедлайна</div>
  </div>;
}

function DayDetail({ person, day, capacity, onClose }: { person: PersonLoad; day: string; capacity: number; onClose: () => void }) {
  const items = itemsOnDay(person.items, day);
  const clock = (value: number) => new Date(value).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  return <div className="pl-detail" role="group" aria-label={`Что в дне: ${person.operator.name}, ${dayMonth(day)}`}>
    <div className="pl-detail-head"><b>{person.operator.name} · {dayLabel(day)} {dayMonth(day)}</b>
      <span>{oneDecimal(person.hoursByDay[day] ?? 0)} из {oneDecimal(capacity)} ч</span>
      <button className="mb-day-close" aria-label="Закрыть день" onClick={onClose}>×</button></div>
    <ul>{items.map((item) => <li key={item.key} className={`${item.done ? "done" : ""}${item.late ? " late" : ""}`}>
      <b>{item.pz}</b> <span>{item.operation}</span>
      {item.feeds && <small> ({item.feeds})</small>}
      <small> · {clock(item.start)}–{clock(item.finish)}{item.quantity != null ? ` · ${item.quantity} шт` : ""}</small>
      {item.deadline && <small> · дедлайн {dayMonth(item.deadline)}</small>}
      {item.done && <em> · выполнена</em>}
      {item.late && <strong> · позже дедлайна</strong>}
    </li>)}</ul>
  </div>;
}
