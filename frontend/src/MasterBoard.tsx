import { useEffect, useRef, useState } from "react";
import {
  kitsetsApi,
  productionApi,
  type KitsetTile,
  type ProductionAbsence,
  type ProductionLaunchable,
  type ProductionOperation,
  type ProductionOperator,
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

export function MasterBoard({ tasks, operators, site = "", skills, absences, canEdit, busy, mutate, openTask }: {
  tasks: ProductionTask[];
  operators: ProductionOperator[];
  site?: string;
  skills: ProductionSkill[];
  absences: ProductionAbsence[];
  canEdit: boolean;
  busy: string;
  mutate: Mutate;
  openTask: (taskId: string, operationId?: string) => void;
}) {
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

  function assign(operatorIds: string[]) {
    if (!task || !operation) return;
    void mutate(operation.id, () => productionApi.assign(task.id, operation.id, task.version, operatorIds, null));
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
                <thead><tr><th>№</th><th>Операция</th><th>Норма</th><th>Исполнители</th></tr></thead>
                <tbody>{operations.map((op) => {
                  const active = selected?.operationId === op.id;
                  const names = op.operator_ids.map((id) => operators.find((operator) => operator.id === id)?.name ?? "?");
                  return <tr key={op.id} className={`${active ? "active" : ""}${done(op) ? " done" : ""}`}>
                    <td>{op.position}</td>
                    <td>{canEdit && !done(op)
                      ? <button className="mb-op" aria-pressed={active} onClick={() => setSelected(active ? null : { taskId: item.id, operationId: op.id })}>{op.stage}{op.code ? <small> {op.code}</small> : null}</button>
                      : <span>{op.stage}{op.code ? <small> {op.code}</small> : null}</span>}</td>
                    <td>{hours(op.norm_seconds)}</td>
                    <td>{done(op) ? <span className="mb-done">выполнена</span> : names.length ? names.map((name) => <span className="mb-chip" key={name}>{name}</span>) : <span className="mb-free">не назначено</span>}</td>
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
      <div className="mb-head"><h2>Монтажники</h2><span className="mb-count">{site ? `${site} · ` : ""}{people.length}</span></div>
      {task && operation
        ? <div className="mb-target"><b>{task.number} · этап {operation.position} · {operation.stage}</b><small>{itemId(task)} × {task.quantity} · норма {hours(operation.norm_seconds)}</small><button className="link-button" onClick={() => openTask(task.id, operation.id)}>Подробно с Гантом</button></div>
        : <div className="mb-target muted">{canEdit ? "Выбери операцию слева, затем монтажника." : "Загрузка монтажников по назначенным операциям."}</div>}
      <ul className="mb-list">{people.map((person) => {
        const load = installerLoad(person.id, live, absences, today);
        const on = Boolean(operation?.operator_ids.includes(person.id));
        const skill = operation?.code ? skills.find((item) => item.operator_id === person.id && item.operation_code === operation.code) : undefined;
        const coded = Boolean(operation?.code && skills.some((item) => item.operation_code === operation.code));
        return <li key={person.id} className={`${on ? "on" : ""}${load.absent ? " absent" : ""}`}>
          <div className="mb-person">
            <b>{person.name}</b>
            <small>{person.site ? `${person.site} · ` : ""}{load.operations ? `${load.operations} опер. · ${hours(load.seconds)}${load.until ? ` · до ${formatDay(load.until)}` : ""}` : "свободен"}</small>
            {load.absent && <small className="danger-text">{load.absent}</small>}
            {coded && (!skill || !skill.allowed) && <small className="danger-text">нет допуска</small>}
          </div>
          {operation && canEdit && <button
            className={on ? "secondary small" : "primary small"}
            disabled={Boolean(busy)}
            aria-label={`${on ? "Снять" : "Назначить"} ${person.name}`}
            onClick={() => assign(on ? operation.operator_ids.filter((id) => id !== person.id) : [...operation.operator_ids, person.id])}
          >{on ? "Снять" : "Назначить"}</button>}
        </li>;
      })}</ul>
    </aside>
  </div>;
}
