import { useLayoutEffect, useMemo, useState, type ReactNode } from "react";
import type { ProductionAbsence, ProductionOperation, ProductionOperator, ProductionShare, ProductionShift, ProductionTask } from "./api";
import { kbLabel } from "./format";
import { deadlineEnd } from "./Gantt";
import { DEFAULT_SHIFT, loadWindow, sharesOf, windowDayHours, workingDay, type LoadClock } from "./MasterBoard";
import { periodWords } from "./PlanLoadSheet";
import { feedsText } from "./productionGraph";

/** Дней в сетке: две недели, на широком блоке (27") — четыре, как шкала листа цеха над ней. */
export const LOAD_DAYS = 14;
export const LOAD_DAYS_WIDE = 28;
/** Ширина блока, с которой сетка показывает четыре недели. */
const WIDE_FROM = 1600;

export type LoadItem = {
  key: string;
  pz: string;
  operation: string;
  feeds: string;
  start: number;
  finish: number;
  done: boolean;
  late: boolean;
  /** Взята, план истёк, не сдана — окно от «сейчас» на остаток (loadWindow). */
  overrun: boolean;
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

/** «708К.4 · КБ1»: сборка задания отдельно от номера проекта (ПП-12). */
function itemId(task: ProductionTask) {
  const glued = task.item.toUpperCase().startsWith(task.project.toUpperCase());
  return kbLabel(task.project, glued ? task.item.slice(task.project.length) : task.item.toUpperCase());
}

export function loadDays(week: number, now = new Date(), count = LOAD_DAYS): string[] {
  const monday = new Date(now.getFullYear(), now.getMonth(), now.getDate() - ((now.getDay() + 6) % 7) + 7 * week);
  return Array.from({ length: count }, (_, index) => isoOf(new Date(monday.getFullYear(), monday.getMonth(), monday.getDate() + index)));
}

/* Загрузка смены — одна шкала для «Производственного плана» и «Ресурсного планирования» (аудит 10.10, ПП-9):
   частично — меньше 85 % смены, смена занята — 85–105 %, перегруз — больше 105 %. Смена — её реальная длина
   (`effective_hours`), а не постоянные 7 ч. */
export type LoadLevel = "free" | "low" | "full" | "over";
export const LOAD_THRESHOLDS = { full: 0.85, over: 1.05 } as const;

/** Часов в смене: эффективные часы смены цеха; без них — 7. */
export function shiftCapacity(shift?: Pick<ProductionShift, "effective_hours"> | null) {
  const hours = Number(shift?.effective_hours);
  return Number.isFinite(hours) && hours > 0 ? hours : 7;
}

/** Уровень загрузки дня по доле смены. */
export function loadLevel(hours: number, capacity: number): LoadLevel {
  if (hours <= 0.05) return "free";
  const share = capacity > 0 ? hours / capacity : Infinity;
  if (share < LOAD_THRESHOLDS.full) return "low";
  if (share <= LOAD_THRESHOLDS.over) return "full";
  return "over";
}

/** «5,6/8» — часы дня из часов смены. */
export function hoursText(hours: number, capacity: number) {
  return `${oneDecimal(hours)}/${oneDecimal(capacity)}`;
}

/** Пороги уровней словами — подсказка легенды. */
export function loadThresholdsText(capacity: number) {
  const full = oneDecimal(capacity * LOAD_THRESHOLDS.full);
  const over = oneDecimal(capacity * LOAD_THRESHOLDS.over);
  return `Смена ${oneDecimal(capacity)} ч. Частично — меньше ${full} ч (85 % смены); смена занята — ${full}–${over} ч (85–105 %); перегруз — больше ${over} ч`;
}

/** Ячейка загрузки дня: цвет — уровень, текст — «часы/смена», отсутствие — словом; с `onClick` — кнопка дня. */
export function LoadCell({ hours, capacity, working, absence, late, today, picked, title, label, onClick, children }: {
  hours: number;
  capacity: number;
  /** Рабочий день по смене и праздникам цеха. */
  working: boolean;
  /** Вид отсутствия («Отпуск», «Больничный»), если человека нет. */
  absence?: string;
  late?: boolean;
  today?: boolean;
  picked?: boolean;
  title: string;
  /** Доступное имя кнопки дня. */
  label?: string;
  onClick?: () => void;
  children?: ReactNode;
}) {
  const level = loadLevel(working && !absence ? hours : 0, capacity);
  const text = absence ? absenceLabel(absence) : !working || level === "free" ? "" : hoursText(hours, capacity);
  const className = ["mb-cell", absence ? "absent" : !working ? "off" : level, late && "late", today && "today", picked && "picked"].filter(Boolean).join(" ");
  return <td className={className}>{onClick
    ? <button title={title} aria-label={label} aria-expanded={picked} onClick={onClick}>{text}{late ? <i className="pl-late" aria-hidden>!</i> : null}{children}</button>
    : <span title={title}>{text}{children}</span>}</td>;
}

/** Легенда загрузки: примеры — из реальной смены («4/8», а не «/7»), пороги словами — в подсказке. */
export function LoadLegend({ capacity, late = true, off = true }: { capacity: number; late?: boolean; off?: boolean }) {
  const sample = (share: number) => hoursText(Math.round(capacity * share * 2) / 2, capacity);
  return <div className="mb-legend pl-legend" title={loadThresholdsText(capacity)}>
    <span className="mb-cell free">&nbsp;</span> свободен
    <span className="mb-cell low">{sample(0.5)}</span> частично, до 85 %
    <span className="mb-cell full">{sample(1)}</span> смена занята
    <span className="mb-cell over">{sample(1.25)}</span> перегруз, больше 105 %
    <span className="mb-cell absent">отпуск</span> отсутствует
    {off && <><span className="mb-cell off">&nbsp;</span> выходной</>}
    {late && <><span className="mb-cell low late">!</span> есть позже дедлайна</>}
  </div>;
}

const dayStart = (day: string) => new Date(`${day}T00:00:00`).getTime();
const itemsOnDay = (items: LoadItem[], day: string) => items.filter((item) => item.finish > dayStart(day) && item.start < dayStart(day) + DAY_MS);

/** Загрузка человека по дням: часы пересечения окон его долей со сменой (как «Монтажники» в Ресурсном планировании);
 *  с clock затянувшаяся доля идёт от «сейчас» на остаток (loadWindow — то же правило, что на доске мастера). */
export function peopleLoad(operators: ProductionOperator[], tasks: ProductionTask[], days: string[], shift: ProductionShift, clock?: LoadClock): PersonLoad[] {
  const live = tasks.filter((task) => task.status !== "Отменено" && !task.handed_to);
  const rows = operators.map((operator) => {
    const items: LoadItem[] = [];
    for (const task of live) {
      for (const op of task.operations) {
        for (const share of sharesOf(op, operator.id)) {
          const done = op.state === "выполнена" || Boolean(share.finished_at);
          const window = loadWindow(task, op, share, shift, done ? undefined : clock);
          if (!window) continue;
          const { start, finish, overrun } = window;
          const end = deadlineEnd(task.deadline);
          items.push({
            key: `${task.id}-${op.id}-${items.length}`, pz: `${task.number} · ${itemId(task)}`, operation: `${op.position}. ${op.stage || op.description}`,
            feeds: feedsText(task, op), start, finish, done, overrun,
            late: Boolean(!done && end && finish > end.getTime()), deadline: task.deadline, quantity: share.quantity, task, op, share,
          });
        }
      }
    }
    const hoursByDay: Record<string, number> = {};
    let total = 0;
    for (const day of days) {
      let sum = 0;
      for (const item of itemsOnDay(items, day)) sum += windowDayHours(item.start, item.finish, day, shift);
      if (sum > 0.05) { hoursByDay[day] = sum; total += sum; }
    }
    return { operator, items, total, hoursByDay };
  });
  return rows.sort((a, b) => Number(b.total > 0) - Number(a.total > 0) || a.operator.name.localeCompare(b.operator.name, "ru"));
}

const ABSENCE_SHORT: Record<string, string> = { "Больничный": "больн.", "Командировка": "командир." };
export const absenceLabel = (kind: string) => ABSENCE_SHORT[kind] ?? kind.toLowerCase();

const minuteOf = (value: string) => { const [h, m] = value.split(":").map(Number); return (h || 0) * 60 + (m || 0); };
const clockOf = (value: number) => new Date(value).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });

/** Часть окна операции внутри смены этого дня; «продолжение»/«с» — день, куда или откуда окно уходит за смену. */
export function dayPart(item: LoadItem, day: string, shift: ProductionShift) {
  const from = dayStart(day) + minuteOf(shift.start) * 60_000;
  const to = dayStart(day) + minuteOf(shift.finish) * 60_000;
  const start = Math.max(item.start, from);
  const finish = Math.min(item.finish, to);
  const ok = finish > start;
  const weekdayOf = (value: number) => new Date(value).toLocaleDateString("ru-RU", { weekday: "short" });
  return {
    start: ok ? start : item.start, finish: ok ? finish : item.finish,
    continues: item.finish > to ? weekdayOf(item.finish) : "", resumed: item.start < from ? weekdayOf(item.start) : "",
  };
}

const dayLabel = (day: string) => new Date(`${day}T00:00:00`).toLocaleDateString("ru-RU", { weekday: "short" });

/** Ширина блока: по ней сетка решает, две недели показывать или четыре. */
function useBlockWidth() {
  // Узел появляется не сразу (пока сотрудников нет, блок — строка «пусто»), поэтому ref-функция, а не useRef.
  const [node, ref] = useState<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    if (!node) return;
    setWidth(node.clientWidth);
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => setWidth(node.clientWidth));
    observer.observe(node);
    return () => observer.disconnect();
  }, [node]);
  return { ref, width };
}

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
  const { ref, width } = useBlockWidth();
  const workShift = shift && shift.weekdays ? shift : DEFAULT_SHIFT;
  const capacity = shiftCapacity(workShift);
  const count = width >= WIDE_FROM ? LOAD_DAYS_WIDE : LOAD_DAYS;
  const days = useMemo(() => loadDays(week, new Date(), count), [week, count]);
  const today = isoOf(new Date());
  const people = useMemo(() => peopleLoad(operators, tasks, days, workShift, { now: Date.now(), holidays, absences }),
    [operators, tasks, days, workShift, holidays, absences]);
  const shown = onlyBusy ? people.filter((person) => person.total > 0.05) : people;
  const period = `${dayMonth(days[0])}–${dayMonth(days[days.length - 1])}`;
  const weeks = Math.round(days.length / 7);
  const detail = open ? people.find((person) => person.operator.id === open.id) : undefined;

  if (!operators.length) return <div className="empty">Сотрудников цеха пока нет.</div>;

  return <div className="pl" ref={ref}>
    <div className="pl-head">
      <span className="pl-period" title={`Период загрузки ${period}`}><b>{periodWords(days[0], days[days.length - 1])}</b> · {weeks} нед.</span>
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
        {days.map((day) => {
          const working = workingDay(day, workShift, holidays);
          return <th key={day} scope="col" className={`${working ? "" : "off"}${day === today ? " today" : ""}`}
            title={`${dayLabel(day)} ${dayMonth(day)}${day === today ? " · сегодня" : working ? "" : " · выходной"}`}><b>{dayMonth(day)}</b><span>{dayLabel(day)}</span></th>;
        })}
      </tr></thead>
      <tbody>{shown.map((person) => {
        const free = person.total <= 0.05;
        return <tr key={person.operator.id} className={free ? "pl-free" : ""}>
          <th scope="row"><b>{person.operator.name}</b>
            <small>{person.operator.site ? `${person.operator.site} · ` : ""}{free ? "свободен" : `${oneDecimal(person.total)} ч за ${weeks} нед.`}</small></th>
          {days.map((day) => {
            const absent = absences.find((item) => item.operator_id === person.operator.id && item.from_date <= day && day <= item.to_date);
            const working = workingDay(day, workShift, holidays);
            const load = working && !absent ? person.hoursByDay[day] ?? 0 : 0;
            const inDay = itemsOnDay(person.items, day);
            const late = inDay.some((item) => item.late);
            const title = `${person.operator.name}, ${dayMonth(day)}: ${absent ? absent.kind : !working ? "нерабочий день" : `${oneDecimal(load)} из ${oneDecimal(capacity)} ч${late ? " · есть позже дедлайна" : ""}`}`;
            const picked = open?.id === person.operator.id && open.day === day;
            return <LoadCell key={day} hours={load} capacity={capacity} working={working} absence={absent?.kind} late={late}
              today={day === today} picked={picked} title={title} label={`Загрузка ${person.operator.name} ${dayMonth(day)}`}
              onClick={!absent && inDay.length > 0 ? () => setOpen(picked ? null : { id: person.operator.id, day }) : undefined} />;
          })}
        </tr>;
      })}</tbody>
    </table>
    {!shown.length && <div className="empty">Нет занятых сотрудников за {period}.</div>}
    {open && detail && <DayDetail person={detail} day={open.day} capacity={capacity} shift={workShift} onClose={() => setOpen(null)} />}
    <LoadLegend capacity={capacity} />
  </div>;
}

function DayDetail({ person, day, capacity, shift, onClose }: { person: PersonLoad; day: string; capacity: number; shift: ProductionShift; onClose: () => void }) {
  const items = itemsOnDay(person.items, day);
  const stamp = (value: number) => `${dayMonth(isoOf(new Date(value)))} ${clockOf(value)}`;
  return <div className="pl-detail" role="group" aria-label={`Что в дне: ${person.operator.name}, ${dayMonth(day)}`}>
    <div className="pl-detail-head"><b>{person.operator.name} · {dayLabel(day)} {dayMonth(day)}</b>
      <span>{oneDecimal(person.hoursByDay[day] ?? 0)} из {oneDecimal(capacity)} ч</span>
      <button className="mb-day-close" aria-label="Закрыть день" title="Закрыть" onClick={onClose}>×</button></div>
    <ul>{items.map((item) => <li key={item.key} className={`${item.done ? "done" : ""}${item.late ? " late" : ""}`}>
      <b>{item.pz}</b> <span>{item.operation}</span>
      {item.feeds && <small> ({item.feeds})</small>}
      {(() => {
        const part = dayPart(item, day, shift);
        const label = `${dayLabel(day)} ${clockOf(part.start)}–${clockOf(part.finish)}${part.resumed ? ` · с ${part.resumed}` : ""}${part.continues ? ` · продолжение ${part.continues}` : ""}`;
        return <>
          <small> · {label}{item.quantity != null ? ` · ${item.quantity} шт` : ""}</small>
          {(part.resumed || part.continues) && <span className="pl-whole">вся операция {stamp(item.start)} – {stamp(item.finish)}</span>}
        </>;
      })()}
      {item.deadline && <small> · дедлайн {dayMonth(item.deadline)}</small>}
      {item.done && <em> · выполнена</em>}
      {item.overrun && <strong> · затянулось</strong>}
      {item.late && <strong> · позже дедлайна</strong>}
    </li>)}</ul>
  </div>;
}
