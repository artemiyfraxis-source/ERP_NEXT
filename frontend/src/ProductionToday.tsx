import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { failureText, type Principal } from "./api";
import { canSee, openPlace } from "./navigation";
import { StatusChip } from "./StatusChip";
import type { Tone } from "./statusTone";
import { formatDay, formatDayMonth, formatNumber, formatWallMoment, formatWallTime, plural } from "./format";
import {
  productionTodayApi,
  type ShopToday,
  type TodayFix,
  type TodayLine,
  type TodayOperationRef,
  type TodayPerson,
  type TodayStuck,
} from "./productionTodayApi";
import "./productionToday.css";

/* «Сегодня» мастера участка (владелец 29.10.2026, идея №5): кто что делает сейчас, план на сегодня, просроченное и что
   стоит и почему — на одном экране, обновляется раз в минуту. Только чтение: правила на сервере (production/today.py). */

export const REFRESH_MS = 60_000;
const WEEKDAYS = ["вс", "пн", "вт", "ср", "чт", "пт", "сб"];

const PERSON_TONE: Record<TodayPerson["state"], Tone> = {
  "работает": "info", "пауза": "warning", "простой": "danger", "отсутствует": "neutral", "свободен": "neutral",
};
const PERSON_TITLE: Record<TodayPerson["state"], string> = {
  "работает": "Открыта работа в «Заданиях»", "пауза": "Работа на паузе", "простой": "Отмечен простой",
  "отсутствует": "Отсутствие в «Календаре цеха»", "свободен": "Нет открытой работы и простоя",
};
const LINE_TONE: Record<TodayLine["state"], Tone> = { "не начата": "neutral", "в работе": "info", "сделано": "success" };
const STUCK_WORD: Record<TodayStuck["kind"], string> = {
  downtime: "простой", blocker: "блокер", skill: "нет допуска", input: "ждёт этап", etk: "нет ЭТК", kit: "нет комплекта",
};

function weekday(iso: string): string {
  const [year, month, day] = iso.split("-").map(Number);
  return WEEKDAYS[new Date(year, month - 1, day).getDay()] ?? "";
}

/** Момент сегодняшнего дня — «14:30», другого дня — «28.10 14:30». */
function when(value: string | null | undefined, today: string): string {
  if (!value) return "";
  return value.startsWith(today) ? formatWallTime(value) : formatWallMoment(value);
}

function qty(value: number): string {
  return formatNumber(value, { digits: 2 });
}

function kb(ref: { project: string; item: string }): string {
  return [ref.project, ref.item].filter(Boolean).join(" · ");
}

export interface ProductionTodayProps {
  principal: Principal;
  /** Карточка задания на «Доске» (операция — выделена). */
  openTask?: (taskId: string, operationId?: string) => void;
  /** Окно «Компетенции» (у мастера с правкой). */
  openSkills?: () => void;
  /** Вкладка «Загрузка». */
  openWorkload?: () => void;
}

export function ProductionToday({ principal, openTask, openSkills, openWorkload }: ProductionTodayProps) {
  const [data, setData] = useState<ShopToday | null>(null);
  const [error, setError] = useState("");
  const [site, setSite] = useState("");
  const [loadedAt, setLoadedAt] = useState("");
  const [busy, setBusy] = useState(false);
  const request = useRef(0);

  const load = useCallback(async (wanted: string) => {
    const ticket = ++request.current;
    setBusy(true);
    try {
      const result = await productionTodayApi.load(wanted);
      if (ticket !== request.current) return;
      setData(result);
      setError("");
      setLoadedAt(formatWallTime(result.now));
    } catch (reason) {
      if (ticket === request.current) setError(failureText(reason, "«Сегодня» не загрузилось"));
    } finally {
      if (ticket === request.current) setBusy(false);
    }
  }, []);

  useEffect(() => {
    void load(site);
    const timer = window.setInterval(() => { if (!document.hidden) void load(site); }, REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [load, site]);

  if (!data) return error ? <div className="error-banner" role="alert">{error}</div> : <div className="empty">Загрузка…</div>;

  /** Где исправить: кнопка, если у пользователя есть доступ туда; иначе — подпись места. */
  function fixOf(item: TodayStuck): (() => void) | undefined {
    const fix: TodayFix = item.fix;
    switch (fix) {
      case "task": return openTask && item.task_id ? () => openTask(item.task_id!, item.operation_id ?? undefined) : undefined;
      case "skills": return openSkills;
      case "workload": return openWorkload;
      case "techcards": return canSee(principal, "techcards") ? () => openPlace("techcards", { project: item.project }) : undefined;
      case "issues": return canSee(principal, "fulfillment") ? () => openPlace("issues") : undefined;
      case "prodplan": return canSee(principal, "production") ? () => openPlace("prodplan") : undefined;
    }
  }

  const working = data.people.filter((person) => person.state === "работает" || person.state === "пауза").length;
  const free = data.people.filter((person) => person.state === "свободен").length;
  const away = data.people.filter((person) => person.state === "отсутствует").length;
  const lateCount = data.overdue.operations.length + data.overdue.tasks.length + data.overdue.projects.length;

  return <div className="td">
    <div className="td-head">
      {data.can_switch
        ? <div className="seg-tabs" role="radiogroup" aria-label="Участок">
          {data.sites.map((name) => <button type="button" role="radio" key={name} aria-checked={data.site === name}
            className={data.site === name ? "on" : undefined} onClick={() => setSite(name)}>{name}</button>)}
        </div>
        : <b className="td-site">Участок {data.site}</b>}
      <span className="td-date">на <b className="num">{formatDay(data.today)}</b>, {weekday(data.today)} · смена {data.shift.start}–{data.shift.finish}</span>
      <span className="td-counts">
        работают <b className="num">{working}</b> · свободны <b className="num">{free}</b>
        {away > 0 && <> · отсутствуют <b className="num">{away}</b></>}
        {lateCount > 0 && <> · <span className="danger-text">просрочено {lateCount}</span></>}
        {data.stuck.length > 0 && <> · <span className="td-warn">стоит {data.stuck.length}</span></>}
      </span>
      <span className="td-refresh" aria-live="polite">
        {error ? <span className="danger-text" title={error}>не обновилось: {error}</span> : <>обновлено {loadedAt} · раз в минуту</>}
        <button type="button" className="secondary small" disabled={busy} onClick={() => void load(site)}>Обновить</button>
      </span>
    </div>
    {!data.working_day && <div className="warning-inline">{formatDay(data.today)} — нерабочий день по календарю цеха.</div>}

    <div className="td-grid">
      <section className="panel td-panel" aria-label="Кто что делает сейчас">
        <h2>Кто что делает сейчас <span className="td-count">{data.people.length}</span></h2>
        {data.people.length
          ? <People people={data.people} today={data.today} openTask={openTask} />
          : <div className="empty">На участке {data.site} нет монтажников — отметьте «Монтажник» в карточке сотрудника.</div>}
      </section>

      <section className="panel td-panel" aria-label="Стоит и почему">
        <h2>Стоит и почему <span className="td-count">{data.stuck.length}</span></h2>
        {data.stuck.length
          ? <Stuck items={data.stuck} today={data.today} fixOf={fixOf} />
          : <div className="empty">Не стоит ничего: запланированное может начаться.</div>}
      </section>

      <section className="panel td-panel" aria-label="План на сегодня">
        <h2>План на {formatDayMonth(data.today)} <span className="td-count">{planDone(data.plan)}</span></h2>
        {data.plan.length
          ? <Plan lines={data.plan} today={data.today} openTask={openTask} />
          : <div className="empty">На {formatDay(data.today)} операций не запланировано — назначьте их на «Доске».</div>}
        {data.handed.length > 0 && <>
          <h2 className="td-subhead">Отдано участку целиком <span className="td-count">{data.handed.length}</span></h2>
          <Handed data={data} openTask={openTask} />
        </>}
      </section>

      <section className="panel td-panel" aria-label="Просрочено">
        <h2>Просрочено <span className="td-count">{lateCount}</span></h2>
        {lateCount
          ? <Overdue data={data} openTask={openTask} openWorkload={openWorkload} />
          : <div className="empty">Просроченного нет.</div>}
      </section>
    </div>
  </div>;
}

function planDone(lines: TodayLine[]): string {
  const done = lines.filter((line) => line.state === "сделано").length;
  return `${done} из ${lines.length}`;
}

function OperationText({ op, openTask }: { op: TodayOperationRef; openTask?: (taskId: string, operationId?: string) => void }) {
  const title = `${op.task_number} · этап ${op.position}${op.code ? ` · ${op.code}` : ""}`;
  const label = <>{op.stage} <span className="td-sub">{kb(op)} · {op.task_number}</span></>;
  return openTask
    ? <button type="button" className="link-button td-op" title={`${title} — открыть карточку задания`} onClick={() => openTask(op.task_id, op.operation_id)}>{label}</button>
    : <span className="td-op" title={title}>{label}</span>;
}

function People({ people, today, openTask }: { people: TodayPerson[]; today: string; openTask?: (taskId: string, operationId?: string) => void }) {
  return <table className="grid td-table" aria-label="Монтажники участка">
    <thead><tr><th scope="col">Монтажник</th><th scope="col">Состояние</th><th scope="col">Что делает</th><th scope="col" className="num">С</th><th scope="col" className="num">План дня</th></tr></thead>
    <tbody>{people.map((person) => <tr key={person.id}>
      <th scope="row">{person.name}</th>
      <td><StatusChip tone={PERSON_TONE[person.state]} title={PERSON_TITLE[person.state]}>{person.state}</StatusChip></td>
      <td className="td-what">{personWhat(person, today, openTask)}</td>
      <td className="num">{personSince(person, today)}</td>
      <td className="num">{person.planned ? `${person.done} из ${person.planned}` : <span className="muted">—</span>}</td>
    </tr>)}</tbody>
  </table>;
}

function personWhat(person: TodayPerson, today: string, openTask?: (taskId: string, operationId?: string) => void) {
  if (person.work) return <OperationText op={person.work} openTask={openTask} />;
  if (person.downtime) return <span title={kb(person.downtime)}>{person.downtime.reason} <span className="td-sub">{kb(person.downtime)}</span></span>;
  if (person.absence) return <span>{person.absence.kind} до {formatDay(person.absence.to)}</span>;
  if (person.next) return <span>дальше в {when(person.next.planned_start, today)}: <OperationText op={person.next} openTask={openTask} /></span>;
  return <span className="muted">на сегодня работы нет</span>;
}

function personSince(person: TodayPerson, today: string) {
  if (person.work) return person.state === "пауза" && person.work.paused_since ? `пауза ${when(person.work.paused_since, today)}` : when(person.work.since, today);
  if (person.downtime) return when(person.downtime.since, today);
  return <span className="muted">—</span>;
}

function Plan({ lines, today, openTask }: { lines: TodayLine[]; today: string; openTask?: (taskId: string, operationId?: string) => void }) {
  // Сделанное за день свёрнуто: мастеру важнее, что осталось; счётчик «N из M» — в заголовке панели.
  const [showDone, setShowDone] = useState(false);
  const done = lines.filter((line) => line.state === "сделано").length;
  const shown = showDone ? lines : lines.filter((line) => line.state !== "сделано");
  const byPerson = new Map<string, TodayLine[]>();
  for (const line of shown) byPerson.set(line.operator_name, [...(byPerson.get(line.operator_name) ?? []), line]);
  const people = [...byPerson.keys()].sort((a, b) => a.localeCompare(b, "ru"));
  return <>
  {done > 0 && <label className="check-inline td-toggle"><input type="checkbox" checked={showDone} onChange={(event) => setShowDone(event.target.checked)} />показать сделанные ({done})</label>}
  {shown.length === 0 ? <div className="empty">Всё на сегодня сделано.</div> : <table className="grid td-table" aria-label="План на сегодня">
    <thead><tr><th scope="col">Монтажник</th><th scope="col">Время</th><th scope="col">Операция</th><th scope="col" className="num">Сделано</th><th scope="col">Статус</th></tr></thead>
    <tbody>{people.map((name) => <Fragment key={name}>{(byPerson.get(name) ?? []).map((line, index, own) => <tr key={`${line.operation_id}:${line.operator_id}`}>
      {index === 0 && <th scope="row" rowSpan={own.length}>{name}</th>}
      <td className={`num${line.late ? " danger-text" : ""}`} title={line.late ? "Плановый конец прошёл, не сдано" : undefined}>
        {when(line.planned_start, today)}–{when(line.planned_finish, today)}
      </td>
      <td className="td-what"><OperationText op={line} openTask={openTask} />{line.crew && <span className="td-sub"> · бригада</span>}</td>
      <td className="num" title={line.remaining ? `Осталось ${qty(line.remaining)} шт` : undefined}>{qty(line.done)} из {qty(line.quantity)}</td>
      <td><StatusChip tone={line.late && line.state !== "сделано" ? "danger" : LINE_TONE[line.state]}>{line.state}</StatusChip></td>
    </tr>)}</Fragment>)}</tbody>
  </table>}
  </>;
}

function Handed({ data, openTask }: { data: ShopToday; openTask?: (taskId: string, operationId?: string) => void }) {
  return <table className="grid td-table" aria-label="Отдано участку целиком">
    <thead><tr><th scope="col">Задание</th><th scope="col">Проект · КБ</th><th scope="col">Срок</th><th scope="col" className="num">Тираж</th><th scope="col" className="num">Сдано</th><th scope="col" className="num">Осталось</th></tr></thead>
    <tbody>{data.handed.map((task) => {
      const late = Boolean(task.deadline && task.deadline < data.today);
      return <tr key={task.task_id}>
        <th scope="row">{openTask ? <button type="button" className="link-button" onClick={() => openTask(task.task_id)}>{task.task_number}</button> : task.task_number}</th>
        <td>{kb(task)} <span className="td-sub">{task.name}</span></td>
        <td className={`num${late ? " danger-text" : ""}`}>{task.deadline ? formatDay(task.deadline) : <span className="muted">—</span>}</td>
        <td className="num">{formatNumber(task.quantity)}</td>
        <td className="num">{task.delivered ? formatNumber(task.delivered) : <span className="muted">—</span>}</td>
        <td className="num"><b>{formatNumber(task.remaining)}</b></td>
      </tr>;
    })}</tbody>
  </table>;
}

function Overdue({ data, openTask, openWorkload }: { data: ShopToday; openTask?: (taskId: string, operationId?: string) => void; openWorkload?: () => void }) {
  const { operations, tasks, projects } = data.overdue;
  return <div className="td-overdue">
    {operations.length > 0 && <table className="grid td-table" aria-label="Просроченные операции">
      <thead><tr><th scope="col">Операция</th><th scope="col">Кто</th><th scope="col">План до</th><th scope="col" className="num">Осталось</th></tr></thead>
      <tbody>{operations.map((line) => <tr key={`${line.operation_id}:${line.operator_id}`}>
        <td className="td-what"><OperationText op={line} openTask={openTask} /></td>
        <td>{line.operator_name}</td>
        <td className="num danger-text">{when(line.planned_finish, data.today)}</td>
        <td className="num">{qty(line.remaining)} из {qty(line.quantity)}</td>
      </tr>)}</tbody>
    </table>}
    {tasks.length > 0 && <table className="grid td-table" aria-label="Задания со сроком в прошлом">
      <thead><tr><th scope="col">Задание</th><th scope="col">Проект · КБ</th><th scope="col">Срок</th><th scope="col" className="num">Просрочено</th></tr></thead>
      <tbody>{tasks.map((task) => <tr key={task.task_id}>
        <th scope="row">{openTask ? <button type="button" className="link-button" onClick={() => openTask(task.task_id)}>{task.task_number}</button> : task.task_number}</th>
        <td>{kb(task)}{task.site !== data.site && <span className="td-sub"> · {task.site}</span>}</td>
        <td className="num danger-text">{formatDay(task.deadline)}</td>
        <td className="num">{task.days} {plural(task.days, ["день", "дня", "дней"])}</td>
      </tr>)}</tbody>
    </table>}
    {projects.length > 0 && <table className="grid td-table" aria-label="Проекты «не успевает»">
      <thead><tr><th scope="col">Проект «не успевает»</th><th scope="col">Срок</th><th scope="col">Готовность по прогнозу</th><th scope="col" className="num">Часов участка</th></tr></thead>
      <tbody>{projects.map((project) => <tr key={project.project}>
        <th scope="row">{openWorkload ? <button type="button" className="link-button" title="Открыть «Загрузку»" onClick={openWorkload}>{project.project}</button> : project.project}
          {project.customer && <span className="td-sub"> {project.customer}</span>}</th>
        <td className="num">{project.deadline ? formatDay(project.deadline) : <span className="muted">—</span>}</td>
        <td className="num danger-text">{project.ready ? formatDay(project.ready) : "не помещается в год"}</td>
        <td className="num">{formatNumber(project.hours, { digits: 1 })}</td>
      </tr>)}</tbody>
    </table>}
  </div>;
}

function Stuck({ items, today, fixOf }: { items: TodayStuck[]; today: string; fixOf: (item: TodayStuck) => (() => void) | undefined }) {
  return <table className="grid td-table" aria-label="Что стоит">
    <thead><tr><th scope="col">Проект · КБ</th><th scope="col">Что</th><th scope="col">Причина</th><th scope="col">Где исправить</th></tr></thead>
    <tbody>{items.map((item, index) => {
      const fix = fixOf(item);
      return <tr key={`${item.kind}:${item.task_id ?? item.project}:${item.item}:${item.operation_id ?? ""}:${index}`}>
        <th scope="row">{kb(item)}</th>
        <td className="td-what">{item.stage
          ? <>{item.position}. {item.stage}{item.operator_name && <span className="td-sub"> · {item.operator_name}</span>}</>
          : item.task_number || <span className="muted">{item.item ? "КБ" : "проект"}</span>}</td>
        <td className={`td-reason td-${item.kind === "downtime" || item.kind === "blocker" ? "stop" : "wait"}`} title={STUCK_WORD[item.kind]}>{item.reason}
          {item.since && <span className="td-sub"> с {when(item.since, today)}</span>}</td>
        <td>{fix ? <button type="button" className="secondary small" onClick={fix}>{item.fix_label}</button> : <span className="td-sub">{item.fix_label}</span>}</td>
      </tr>;
    })}</tbody>
  </table>;
}
