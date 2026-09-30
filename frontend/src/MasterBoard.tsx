import { useEffect, useRef, useState } from "react";
import {
  kitsetsApi,
  productionApi,
  type KitsetTile,
  type ProductionAbsence,
  type ProductionLaunchable,
  type ProductionOperation,
  type ProductionOperator,
  type ProductionShift,
  type ProductionSkill,
  type ProductionTask,
} from "./api";
import { KitTile } from "./ProjectCard";

// Экран мастера (владелец, 29.09): после выдачи техкарты проект приходит мастеру, разбитый на операции маршрута,
// и мастер раздаёт их монтажникам. Слева проекты → задания → операции, справа монтажники с загрузкой.
// Назначение идёт тем же assign, что и карточка с Гантом: время считает сервер (первое свободное окно).

// Открытый проект переживает переход в Гант задания и обратно (доска при этом монтируется заново).
let lastProject = "";
export function forgetOpenProject() { lastProject = ""; }

type Mutate = (key: string, operation: () => Promise<unknown>) => Promise<void>;

// Список монтажников отбирает сервер (/operators: активный «Сотрудник» с площадкой или из отдела «Монтаж»).
export function installers(operators: ProductionOperator[]) {
  return operators;
}

function itemId(task: { project: string; item: string }) {
  return task.item.toUpperCase().startsWith(task.project.toUpperCase()) ? task.item : `${task.project}${task.item.toUpperCase()}`;
}

function hours(seconds: number) {
  return `${(seconds / 3600).toLocaleString("ru-RU", { maximumFractionDigits: 1, minimumFractionDigits: 1 })} ч`;
}

function formatDay(value: string | null | undefined) {
  if (!value) return "";
  const date = new Date(value.length === 10 ? `${value}T00:00:00` : value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit" });
}

function isoDay(date: Date) {
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

const done = (operation: ProductionOperation) => operation.state === "выполнена";

export type InstallerLoad = { operations: number; seconds: number; until: string | null; absent: string };

// Загрузка монтажника: невыполненные операции живых заданий; норма бригады делится поровну.
export function installerLoad(operatorId: string, tasks: ProductionTask[], absences: ProductionAbsence[], today: string): InstallerLoad {
  let operations = 0;
  let seconds = 0;
  let until: string | null = null;
  for (const task of tasks) {
    if (task.status === "Отменено") continue;
    for (const operation of task.operations) {
      if (done(operation) || !operation.operator_ids.includes(operatorId)) continue;
      operations += 1;
      seconds += operation.norm_seconds / operation.operator_ids.length;
      if (operation.planned_finish && (!until || operation.planned_finish > until)) until = operation.planned_finish;
    }
  }
  const absence = absences.find((item) => item.operator_id === operatorId && item.from_date <= today && today <= item.to_date);
  return { operations, seconds, until, absent: absence ? `${absence.kind} до ${formatDay(absence.to_date)}` : "" };
}

// ── Неделя монтажников (владелец, 30.09: «не понимаю, на какой период я закидываю»). Мастер видит загрузку
// каждого монтажника по дням в часах и ставит операцию кликом в день: сервер планирует её с начала смены этого
// дня в первое свободное окно (тот же assign и план, что в карточке с Гантом, prodPlan_).
const DEFAULT_SHIFT: ProductionShift = { weekdays: [0, 1, 2, 3, 4], start: "09:00", finish: "17:30", breaks: [["13:00", "13:30"]], effective_hours: "8" };
const WEEK_DAYS = 7;

function addDays(day: string, count: number) {
  const date = new Date(`${day}T00:00:00`);
  date.setDate(date.getDate() + count);
  return isoDay(date);
}

function minutes(value: string) {
  const [h, m] = value.split(":").map(Number);
  return (h || 0) * 60 + (m || 0);
}

export function workingDay(day: string, shift: ProductionShift, holidays: string[]) {
  const weekday = (new Date(`${day}T00:00:00`).getDay() + 6) % 7;
  return shift.weekdays.includes(weekday) && !holidays.includes(day);
}

// Часы монтажника в день: пересечение плановых окон его невыполненных операций со сменой дня, приведённое
// к эффективным часам смены (обед и перерывы не считаются).
export function dayHours(operatorId: string, day: string, tasks: ProductionTask[], shift: ProductionShift) {
  const from = new Date(`${day}T${shift.start}:00`).getTime();
  const to = new Date(`${day}T${shift.finish}:00`).getTime();
  const span = Math.max(1, minutes(shift.finish) - minutes(shift.start)) / 60;
  const ratio = Math.min(1, Number(shift.effective_hours || 8) / span);
  let total = 0;
  for (const task of tasks) {
    if (task.status === "Отменено") continue;
    for (const operation of task.operations) {
      if (done(operation) || !operation.operator_ids.includes(operatorId) || !operation.planned_start || !operation.planned_finish) continue;
      const start = Math.max(from, new Date(operation.planned_start).getTime());
      const finish = Math.min(to, new Date(operation.planned_finish).getTime());
      if (finish > start) total += (finish - start) / 3_600_000;
    }
  }
  return total * ratio;
}

function oneDecimal(value: number) {
  return value.toLocaleString("ru-RU", { maximumFractionDigits: 1 });
}

function planText(operation: ProductionOperation) {
  if (!operation.planned_start || !operation.planned_finish) return "";
  const start = new Date(operation.planned_start);
  const finish = new Date(operation.planned_finish);
  const time = (date: Date) => date.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  const day = (date: Date) => date.toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit" });
  return day(start) === day(finish) ? `${day(start)} ${time(start)}–${time(finish)}` : `${day(start)} ${time(start)} – ${day(finish)} ${time(finish)}`;
}

export function MasterBoard({ tasks, operators, site = "", skills, absences, shift, holidays = [], canEdit, busy, mutate, openTask }: {
  tasks: ProductionTask[];
  operators: ProductionOperator[];
  site?: string;
  skills: ProductionSkill[];
  absences: ProductionAbsence[];
  shift?: ProductionShift | null;
  holidays?: string[];
  canEdit: boolean;
  busy: string;
  mutate: Mutate;
  openTask: (taskId: string, operationId?: string) => void;
}) {
  const [weekStart, setWeekStart] = useState(() => isoDay(new Date()));
  const [ready, setReady] = useState<ProductionLaunchable[]>([]);
  const [selected, setSelected] = useState<{ taskId: string; operationId: string } | null>(null);
  const [onlyFree, setOnlyFree] = useState(false);
  const [tiles, setTiles] = useState<{ items: KitsetTile[]; statuses: string[] }>({ items: [], statuses: [] });
  const [openProject, setOpenProjectState] = useState(lastProject);
  const keys = useRef(new Map<string, string>());

  function setOpenProject(project: string) {
    lastProject = project;
    setOpenProjectState(project);
    setSelected(null);
  }

  // Плитки как в «Проектах» (владелец, 29.09: «примерный вид проектов»): статус, ТК, заказчик, сборки.
  useEffect(() => {
    kitsetsApi.list("upd").then(setTiles).catch(() => setTiles({ items: [], statuses: [] }));
  }, []);

  useEffect(() => {
    if (!canEdit) return;
    productionApi.launchable().then((result) => setReady(result.items)).catch(() => setReady([]));
  }, [canEdit, tasks.length]);

  const live = tasks.filter((task) => task.status !== "Отменено");
  const launchedKeys = new Set(live.map((task) => `${task.project.toLowerCase()}|${itemId(task).toUpperCase()}`));
  const waiting = ready.filter((item) => !item.launched && !launchedKeys.has(`${item.project.toLowerCase()}|${itemId({ project: item.project, item: item.index }).toUpperCase()}`));
  const projects = Array.from(new Set([...live.map((task) => task.project), ...waiting.map((item) => item.project)]));
  const deadlineOf = (project: string) => [...live.filter((task) => task.project === project).map((task) => task.deadline),
    ...waiting.filter((item) => item.project === project).map((item) => item.deadline)].filter(Boolean).sort()[0] ?? "";
  projects.sort((a, b) => (deadlineOf(a) || "9999").localeCompare(deadlineOf(b) || "9999") || a.localeCompare(b, "ru"));

  const shown = projects.find((project) => project === openProject) ?? "";
  const freeOf = (project: string) => live.filter((item) => item.project === project)
    .reduce((sum, item) => sum + item.operations.filter((op) => !done(op) && !op.operator_ids.length).length, 0);
  function tileFacts(project: string) {
    const deadline = deadlineOf(project);
    const free = freeOf(project);
    const total = live.filter((item) => item.project === project).reduce((sum, item) => sum + item.operations.length, 0);
    const toLaunch = waiting.filter((item) => item.project === project).length;
    return <>
      {deadline && <span className="mb-deadline">ДЛ {formatDay(deadline)}</span>}
      {total > 0 && <span className={free ? "mb-free" : "mb-done"}>{free ? `без исполнителя ${free} из ${total}` : `распределено ${total}`}</span>}
      {toLaunch > 0 && <span className="mb-launch">к запуску {toLaunch}</span>}
    </>;
  }

  // Мастер площадки (Степан в Жуковском) видит и ставит только своих монтажников; без площадки — всех.
  const people = installers(operators).filter((person) => !site || person.site === site);
  const today = isoDay(new Date());
  const task = selected ? live.find((item) => item.id === selected.taskId) ?? null : null;
  const operation = task?.operations.find((item) => item.id === selected?.operationId) ?? null;
  const unassigned = live.reduce((sum, item) => sum + item.operations.filter((op) => !done(op) && !op.operator_ids.length).length, 0);

  const workShift = shift ?? DEFAULT_SHIFT;
  const days = Array.from({ length: WEEK_DAYS }, (_, index) => addDays(weekStart, index));

  // Сегодня — «первое свободное время с этого момента»; другой день — с начала его смены.
  function assign(operatorIds: string[], day: string | null) {
    if (!task || !operation) return;
    const start = day && day > today ? `${day}T${workShift.start}` : null;
    void mutate(operation.id, () => productionApi.assign(task.id, operation.id, task.version, operatorIds, start));
  }

  function unassign(item: ProductionTask, op: ProductionOperation, operatorId: string) {
    void mutate(op.id, () => productionApi.assign(item.id, op.id, item.version, op.operator_ids.filter((id) => id !== operatorId), null));
  }

  function launch(item: ProductionLaunchable) {
    const key = keys.current.get(item.techcard_id) ?? crypto.randomUUID();
    keys.current.set(item.techcard_id, key);
    void mutate(`launch:${item.techcard_id}`, () => productionApi.launch(key, {
      techcard_id: item.techcard_id, quantity: Number(item.run), deadline: item.deadline, fulfillment_request_id: null,
    }));
  }

  return <div className="master-board">
    <section className="panel mb-projects" aria-label="Проекты и операции">
      <div className="mb-head">
        <h2>Проекты и операции</h2>
        <span className="mb-count">{unassigned ? `без исполнителя: ${unassigned}` : "всё распределено"}</span>
        <label className="mb-toggle"><input type="checkbox" checked={onlyFree} onChange={(event) => setOnlyFree(event.target.checked)} />только нераспределённые</label>
      </div>
      {!projects.length && <div className="empty">Проектов с выданной техкартой пока нет.</div>}
      {!shown && projects.length > 0 && <div className="tiles kit-tiles mb-tiles">{projects.map((project) => {
        if (onlyFree && !freeOf(project)) return null;
        const tile = tiles.items.find((item) => item.project.toLowerCase() === project.toLowerCase());
        const extra = <div className="mb-tile-extra">{tileFacts(project)}</div>;
        return tile
          ? <KitTile key={project} item={tile} statuses={tiles.statuses} canEdit={false} open={() => setOpenProject(project)} extra={extra} />
          : <div key={project} className="tile kit-tile" role="button" tabIndex={0} onClick={() => setOpenProject(project)} onKeyDown={(event) => { if (event.key === "Enter") setOpenProject(project); }}>
            <div className="tile-body"><div className="kit-tile-head"><strong className="t-h">{project}</strong></div>{extra}</div>
          </div>;
      })}</div>}
      {shown && [shown].map((project) => {
        const projectTasks = live.filter((item) => item.project === project);
        const projectReady = waiting.filter((item) => item.project === project);
        const deadline = deadlineOf(project);
        return <div className="mb-project" key={project}>
          <div className="mb-project-head"><button className="secondary small" onClick={() => setOpenProject("")}>← Все проекты</button><b>{project}</b>{deadline && <span className="mb-deadline">ДЛ {formatDay(deadline)}</span>}</div>
          {projectTasks.map((item) => {
            const operations = item.operations.filter((op) => !onlyFree || (!done(op) && !op.operator_ids.length));
            if (onlyFree && !operations.length) return null;
            return <div className="mb-task" key={item.id}>
              <div className="mb-task-head">
                <button className="link-button" onClick={() => openTask(item.id)}>{item.number} · Гант</button>
                <span><b>{itemId(item)}</b> × {item.quantity}{item.name ? ` · ${item.name}` : ""}</span>
                <small>назначено {item.assigned} из {item.operations.length}</small>
              </div>
              <table className="mb-ops">
                <thead><tr><th>№</th><th>Операция</th><th>Норма</th><th>Исполнители</th><th>План</th></tr></thead>
                <tbody>{operations.map((op) => {
                  const active = selected?.operationId === op.id;
                  const people = op.operator_ids.map((id) => ({ id, name: operators.find((operator) => operator.id === id)?.name ?? "?" }));
                  return <tr key={op.id} className={`${active ? "active" : ""}${done(op) ? " done" : ""}`}>
                    <td>{op.position}</td>
                    <td>{canEdit && !done(op)
                      ? <button className="mb-op" aria-pressed={active} onClick={() => setSelected(active ? null : { taskId: item.id, operationId: op.id })}>{op.stage}{op.code ? <small> {op.code}</small> : null}</button>
                      : <span>{op.stage}{op.code ? <small> {op.code}</small> : null}</span>}</td>
                    <td>{hours(op.norm_seconds)}</td>
                    <td>{done(op) ? <span className="mb-done">выполнена</span> : people.length ? people.map((person) => <span className="mb-chip" key={person.id}>{person.name}{canEdit && <button className="mb-chip-x" aria-label={`Снять ${person.name}`} title="Снять с операции" disabled={Boolean(busy)} onClick={() => unassign(item, op, person.id)}>×</button>}</span>) : <span className="mb-free">не назначено</span>}</td>
                    <td className="mb-plan">{planText(op)}</td>
                  </tr>;
                })}</tbody>
              </table>
            </div>;
          })}
          {!onlyFree && projectReady.map((item) => <div className="mb-ready" key={item.techcard_id}>
            <span><b>{item.project}{item.index}</b>{item.name ? ` · ${item.name}` : ""}<small> · техкарта выдана, этапов {item.operations}, тираж {item.run || "—"}</small></span>
            <button className="secondary small" disabled={Boolean(busy) || !/^\d+$/.test(item.run) || Number(item.run) <= 0} title={/^\d+$/.test(item.run) ? "" : "Тиража нет — запусти через «+ Запустить задание»"} onClick={() => launch(item)}>В работу</button>
          </div>)}
        </div>;
      })}
    </section>
    <aside className="panel mb-people" aria-label="Монтажники">
      <div className="mb-head">
        <h2>Монтажники</h2><span className="mb-count">{site ? `${site} · ` : ""}{people.length}</span>
        <div className="mb-week-nav">
          <button className="secondary small" aria-label="Неделя назад" onClick={() => setWeekStart(addDays(weekStart, -WEEK_DAYS))}>‹</button>
          <button className="secondary small" onClick={() => setWeekStart(today)}>Сегодня</button>
          <button className="secondary small" aria-label="Неделя вперёд" onClick={() => setWeekStart(addDays(weekStart, WEEK_DAYS))}>›</button>
        </div>
      </div>
      {task && operation
        ? <div className="mb-target"><b>{operation.position}. {operation.stage} · {hours(operation.norm_seconds)}</b><small>{task.number} · {itemId(task)} × {task.quantity}. Нажми на день у монтажника — операция встанет с этого дня в первое свободное время.</small><button className="link-button" onClick={() => openTask(task.id, operation.id)}>Точное время — на Ганте</button></div>
        : <div className="mb-target muted">{canEdit ? "Выбери операцию слева, затем день у монтажника." : "Загрузка монтажников по дням, часы по норме."}</div>}
      <table className="mb-week">
        <thead><tr><th>Монтажник</th>{days.map((day) => {
          const date = new Date(`${day}T00:00:00`);
          return <th key={day} className={`${workingDay(day, workShift, holidays) ? "" : "off"}${day === today ? " today" : ""}`}>
            <span>{date.toLocaleDateString("ru-RU", { weekday: "short" })}</span>{formatDay(day)}
          </th>;
        })}</tr></thead>
        <tbody>{people.map((person) => {
          const on = Boolean(operation?.operator_ids.includes(person.id));
          const skill = operation?.code ? skills.find((item) => item.operator_id === person.id && item.operation_code === operation.code) : undefined;
          const coded = Boolean(operation?.code && skills.some((item) => item.operation_code === operation.code));
          return <tr key={person.id} className={on ? "on" : ""}>
            <th scope="row"><b>{on ? "✓ " : ""}{person.name}</b><small>{person.site}{coded && (!skill || !skill.allowed) ? <span className="danger-text"> · нет допуска</span> : null}</small></th>
            {days.map((day) => {
              const absent = absences.find((item) => item.operator_id === person.id && item.from_date <= day && day <= item.to_date);
              const working = workingDay(day, workShift, holidays);
              const load = working && !absent ? dayHours(person.id, day, tasks, workShift) : 0;
              const capacity = Number(workShift.effective_hours || 8);
              const level = load <= 0.05 ? "free" : load < capacity * 0.5 ? "low" : load <= capacity + 0.05 ? "full" : "over";
              const text = absent ? absent.kind.slice(0, 3).toLowerCase() : !working ? "" : load > 0.05 ? oneDecimal(load) : "";
              const title = `${person.name}, ${formatDay(day)}: ${absent ? absent.kind : !working ? "нерабочий день" : `${oneDecimal(load)} из ${oneDecimal(capacity)} ч`}`;
              const clickable = Boolean(operation && canEdit && working && !absent && day >= today);
              const className = `mb-cell ${absent ? "absent" : !working ? "off" : level}${day === today ? " today" : ""}`;
              return <td key={day} className={className}>{clickable
                ? <button disabled={Boolean(busy)} title={title} aria-label={`Назначить ${person.name} на ${formatDay(day)}`}
                  onClick={() => assign(on ? operation!.operator_ids : [...operation!.operator_ids, person.id], day)}>{text || "+"}</button>
                : <span title={title}>{text}</span>}</td>;
            })}
          </tr>;
        })}</tbody>
      </table>
      <div className="mb-legend"><span className="mb-cell low">ч</span> загружен частично <span className="mb-cell full">ч</span> смена занята <span className="mb-cell over">ч</span> перегруз <span className="mb-cell absent">отп</span> отсутствует</div>
    </aside>
  </div>;
}
