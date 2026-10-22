import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  ApiError, failureText, productionApi,
  type AutoplanContext, type AutoplanMode, type AutoplanPreview, type AutoplanProposal, type AutoplanRequest,
} from "./api";
import { formatDay, formatDayMonth, kbLabel } from "./format";
import type { ProjectDeadlines } from "./MasterBoard";
import { ShipDatePlan } from "./ShipDatePlan";
import { LeftoverHint, SemiLeftoversList, leftoverMatches, useSemiLeftovers } from "./SemiLeftovers";

const STALE_TEXT = "План изменился — обновите предложение";
// Номер будущего задания в расчёте (backend autoplan.NEW_TASK_PREFIX): номер ПЗ дадут при применении.
const NEW_PREFIX = "Новое · ";

function pad(value: number) {
  return String(value).padStart(2, "0");
}

function isoDay(date: Date) {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

/** Время из строки как записано (московское), без пересчёта в часовой пояс браузера. */
function parts(value: string) {
  const match = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/.exec(value);
  return match ? { day: `${match[1]}-${match[2]}-${match[3]}`, short: `${match[3]}.${match[2]}`, time: `${match[4]}:${match[5]}` } : { day: value, short: value, time: "" };
}

function minutesText(seconds: number) {
  const minutes = Math.max(1, Math.round(seconds / 60));
  return minutes >= 60 && minutes % 60 === 0 ? `${minutes / 60} ч` : `${minutes} мин`;
}

const hoursText = (seconds: number) => `${(seconds / 3600).toLocaleString("ru-RU", { maximumFractionDigits: 1 })} ч`;

/** «06.10 15:18–16:40»; a slot that ends on another day shows that day too. */
function slotText(start: string, finish: string) {
  const from = parts(start);
  const to = parts(finish);
  return `${from.short} ${from.time}–${to.day === from.day ? "" : `${to.short} `}${to.time}`;
}

/** Group checkbox: ticked when all are chosen, a dash when only some are. */
function GroupCheck({ label, items, chosen, onToggle }: {
  label: string;
  items: AutoplanProposal[];
  chosen: Set<string>;
  onToggle: (on: boolean) => void;
}) {
  const ref = useRef<HTMLInputElement>(null);
  const count = items.filter((item) => chosen.has(key(item))).length;
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = count > 0 && count < items.length;
  }, [count, items.length]);
  return <input ref={ref} type="checkbox" aria-label={label} checked={count === items.length} onChange={(event) => onToggle(event.target.checked)} />;
}

const key = (item: AutoplanProposal) => `${item.task_id}:${item.operation_id}`;

interface BatchGroup { id: string; label: string; machine: string; items: AutoplanProposal[] }
interface DayGroup { day: string; batches: BatchGroup[] }
interface FitterGroup { id: string; name: string; days: DayGroup[]; items: AutoplanProposal[] }

function group(proposals: AutoplanProposal[]): FitterGroup[] {
  const fitters = new Map<string, FitterGroup>();
  const sorted = [...proposals].sort((left, right) => left.start.localeCompare(right.start));
  for (const item of sorted) {
    let fitter = fitters.get(item.operator_id);
    if (!fitter) {
      fitter = { id: item.operator_id, name: item.operator_name || "Монтажник не указан", days: [], items: [] };
      fitters.set(item.operator_id, fitter);
    }
    fitter.items.push(item);
    const day = parts(item.start).day;
    let dayGroup = fitter.days.find((value) => value.day === day);
    if (!dayGroup) {
      dayGroup = { day, batches: [] };
      fitter.days.push(dayGroup);
    }
    const last = dayGroup.batches[dayGroup.batches.length - 1];
    if (last && last.label === item.setup_label && last.machine === item.machine) last.items.push(item);
    else dayGroup.batches.push({ id: `${fitter.id}:${day}:${dayGroup.batches.length}`, label: item.setup_label, machine: item.machine, items: [item] });
  }
  return [...fitters.values()].sort((left, right) => left.name.localeCompare(right.name, "ru"));
}

function plural(count: number, one: string, few: string, many: string) {
  const rest = count % 100;
  if (rest > 10 && rest < 20) return many;
  const last = count % 10;
  return last === 1 ? one : last >= 2 && last <= 4 ? few : many;
}

const count = (value: number, one: string, few: string, many: string) => `${value} ${plural(value, one, few, many)}`;

const periodText = (start: string, lastDay: string) => `${formatDayMonth(start)}–${formatDayMonth(lastDay)}`;

/** Задание в строке результата: у будущего — КБ и пометка «новое задание». */
function TaskCell({ item }: { item: AutoplanProposal }) {
  if (!item.new) return <span className="autoplan-task">{item.task_number}</span>;
  return <span className="autoplan-task">{item.task_number.replace(NEW_PREFIX, "")}<em className="autoplan-new">новое задание</em></span>;
}

/** «Людей N» в автопланировании проекта: N наименее загруженных — с наибольшим свободным временем за горизонт плана. */
export function leastLoaded(fitters: AutoplanContext["fitters"], count: number): string[] {
  return [...fitters].sort((a, b) => b.free_seconds - a.free_seconds || a.name.localeCompare(b.name, "ru"))
    .slice(0, Math.max(0, count)).map((item) => item.id);
}

/** «Как можно раньше» — предложение и применение; «К сроку отгрузки» — расчёт от даты отгрузки (когда начать, успеваем
 *  ли, что отгрузить первым), он ничего не назначает (ShipDatePlan). */
type PlanKind = "asap" | "ship";

export function AutoplanPanel({ close, onApplied, projectDeadlines = {}, preset }: {
  close: () => void;
  /** Из окна проекта на доске (владелец 22.10): «По проекту» на этот проект, тиражи новых заданий, один наименее
   *  загруженный монтажник по умолчанию. */
  preset?: { project: string; runs?: Record<string, number> };
  onApplied: (count: number, created: number) => void;
  /** ДЛ проектов из производственного плана — как на доске мастера; нет в плане — ближайший срок заданий и КБ. */
  projectDeadlines?: ProjectDeadlines;
}) {
  const deadlineOf = (row: AutoplanContext["projects"][number]) => projectDeadlines[row.project.toLowerCase()]?.deadline || row.deadline;
  const sortProjects = (rows: AutoplanContext["projects"]) => [...rows].sort((left, right) =>
    (deadlineOf(left) || "9999").localeCompare(deadlineOf(right) || "9999") || left.project.localeCompare(right.project, "ru"));
  const [mode, setMode] = useState<AutoplanMode>(preset ? "project" : "shop");
  const [project, setProject] = useState(preset?.project ?? "");
  const [kind, setKind] = useState<PlanKind>("asap");
  // Окно проекта: при первой загрузке отмечается один наименее загруженный монтажник — дальше «Людей» и галочки.
  const autoPick = useRef(Boolean(preset));
  const [people, setPeople] = useState<Set<string>>(new Set());
  const [excluded, setExcluded] = useState<Set<string>>(new Set());
  const [dateTo, setDateTo] = useState("");
  const [editDate, setEditDate] = useState(false);
  const [context, setContext] = useState<AutoplanContext | null>(null);
  const [contextError, setContextError] = useState("");
  const [loading, setLoading] = useState(false);
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<{ preview: AutoplanPreview; request: AutoplanRequest } | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const semi = useSemiLeftovers();

  const dialogRef = useRef<HTMLFormElement>(null);
  const closeRef = useRef(close);
  closeRef.current = close;
  useEffect(() => {
    // Открыли из окна проекта — после закрытия фокус туда же: Esc закроет и его.
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    dialogRef.current?.focus();
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") { event.stopPropagation(); closeRef.current(); } };
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("keydown", onKey); if (opener?.isConnected) opener.focus(); };
  }, []);

  // Что войдёт в расчёт и свободные часы людей — заново при смене режима, проекта и «до даты».
  useEffect(() => {
    let active = true;
    setContextError("");
    productionApi.autoplanContext({ mode, ...(mode === "project" && project ? { project } : {}), date_to: dateTo || null })
      .then((value) => {
        if (!active) return;
        setContext(value);
        if (mode === "project" && !project && value.projects.length) setProject(sortProjects(value.projects)[0].project);
        if (autoPick.current && value.fitters.length) {
          autoPick.current = false;
          setPeople(new Set(leastLoaded(value.fitters, 1)));
        }
      })
      .catch((reason) => { if (active) setContextError(failureText(reason, "Не удалось загрузить данные для автоплана")); });
    return () => { active = false; };
  }, [mode, project, dateTo]);

  const tasks = context?.tasks ?? [];
  const ready = (context?.new ?? []).filter((item) => !item.skipped);
  const skipped = (context?.new ?? []).filter((item) => item.skipped);
  const fitters = context?.fitters ?? [];
  const includedTasks = mode === "shop" ? tasks.filter((task) => !excluded.has(task.id)) : tasks;
  const includedNew = mode === "shop" ? ready.filter((item) => !excluded.has(item.techcard_id)) : ready;
  const chosenPeople = fitters.filter((person) => people.has(person.id));
  const listed = tasks.length + ready.length;
  const shownPeriod = result ? result.preview.period : context?.period;
  const groups = useMemo(() => (result ? group(result.preview.proposals) : []), [result]);
  // КБ строки предложения — для подсказки «из остатков»: у задания из контекста, у будущего — КБ техкарты.
  const kbOf = useMemo(() => new Map<string, { project: string; item: string }>([
    ...(context?.tasks ?? []).map((task) => [task.id, { project: task.project, item: task.item }] as const),
    ...(result?.preview.new_tasks ?? []).map((task) => [task.task_id, { project: task.project, item: task.index }] as const),
  ]), [context, result]);
  const leftoversFor = (item: AutoplanProposal) => leftoverMatches(semi.rows ?? [], { ...item, ...(kbOf.get(item.task_id) ?? { project: "", item: "" }) });
  const today = isoDay(new Date());
  const maxDay = isoDay(new Date(Date.now() + ((context?.max_days ?? 56) - 1) * 86_400_000));

  function request(): AutoplanRequest {
    const date = dateTo ? { date_to: dateTo } : {};
    const runs = preset && preset.project === project && preset.runs && Object.keys(preset.runs).length ? { runs: preset.runs } : {};
    if (mode === "project") return { mode, project, people: chosenPeople.map((person) => person.id), ...date, ...runs };
    const exclude = [...tasks.map((task) => task.id), ...ready.map((item) => item.techcard_id)].filter((id) => excluded.has(id));
    return { mode, ...(exclude.length ? { exclude } : {}), ...date };
  }

  // Любое изменение условий делает прежнее предложение неактуальным.
  function changed<T>(apply: (value: T) => void) {
    return (value: T) => { apply(value); setResult(null); setError(""); };
  }
  const switchMode = changed((value: AutoplanMode) => setMode(value));
  const pickProject = changed((value: string) => { setProject(value); setPeople(new Set()); });
  const toggleId = (setter: typeof setPeople) => changed((id: string) => setter((current) => { const next = new Set(current); if (!next.delete(id)) next.add(id); return next; }));
  const togglePerson = toggleId(setPeople);
  const toggleExcluded = toggleId(setExcluded);
  const setAllPeople = changed((on: boolean) => setPeople(on ? new Set(fitters.map((person) => person.id)) : new Set()));
  const setAllWork = changed((on: boolean) => setExcluded(on ? new Set() : new Set([...tasks.map((task) => task.id), ...ready.map((item) => item.techcard_id)])));
  const changeDate = changed((value: string) => setDateTo(value));
  // «Людей N»: N наименее загруженных за горизонт плана; список ниже остаётся для правки.
  const pickLeast = changed((value: number) => setPeople(new Set(leastLoaded(fitters, value))));

  async function run(body: AutoplanRequest = request()) {
    setLoading(true);
    setError("");
    try {
      const preview = await productionApi.autoplanPreview(body);
      setResult({ preview, request: body });
      setSelected(new Set(preview.proposals.map(key)));
    } catch (reason) {
      setResult(null);
      setError(failureText(reason, "План не рассчитан"));
    } finally {
      setLoading(false);
    }
  }

  async function apply() {
    if (!result || applying) return;
    const items = result.preview.proposals.filter((item) => selected.has(key(item)))
      .map((item) => ({ task_id: item.task_id, operation_id: item.operation_id, operator_id: item.operator_id }));
    if (!items.length) return;
    setApplying(true);
    setError("");
    try {
      const applied = await productionApi.autoplanApply({ ...result.request, items, fingerprint: result.preview.fingerprint });
      onApplied(applied.applied, applied.created?.length ?? 0);
    } catch (reason) {
      setApplying(false);
      if (reason instanceof ApiError && reason.status === 409) {
        await run(result.request);
        setError(STALE_TEXT);
        return;
      }
      setError(failureText(reason, "План не применён"));
    }
  }

  function toggle(items: AutoplanProposal[], on: boolean) {
    setSelected((current) => {
      const next = new Set(current);
      for (const item of items) { if (on) next.add(key(item)); else next.delete(key(item)); }
      return next;
    });
  }
  const stats = result?.preview.stats;
  const period = shownPeriod ? periodText(shownPeriod.start, shownPeriod.last_day) : "";
  const ready_ = Boolean(context) && (mode === "shop"
    ? includedTasks.length + includedNew.length > 0
    : Boolean(project) && chosenPeople.length > 0);
  const workText = (tasksCount: number, newCount: number) => [
    tasksCount || !newCount ? count(tasksCount, "задание", "задания", "заданий") : "",
    newCount ? count(newCount, "новая КБ", "новые КБ", "новых КБ") : "",
  ].filter(Boolean).join(", ");
  const projectRow = context?.projects.find((row) => row.project === project);
  // «К сроку отгрузки» — только расчёт: предложения и применения нет.
  const shipOnly = mode === "project" && kind === "ship";

  const proposeButton = <button className={result ? "secondary" : "primary"} disabled={loading || applying || !ready_} title={mode === "project" && context && !chosenPeople.length ? "Отметьте людей на проект" : undefined} onClick={() => void run()}>{result ? "Пересчитать" : `Предложить план${period ? ` на ${period}` : ""}`}</button>;

  return createPortal(<div className="dialog-overlay" onMouseDown={(event) => { if (event.target === event.currentTarget) close(); }}>
    <form ref={dialogRef} tabIndex={-1} className="move-dialog autoplan-dialog panel" role="dialog" aria-modal="true" aria-label="Автоплан" onSubmit={(event) => event.preventDefault()}>
      <div className="autoplan-head">
        <h2>{preset ? `Автопланирование · ${preset.project}` : "Автоплан"}</h2>
        {!preset && <div className="seg-tabs autoplan-modes" role="radiogroup" aria-label="Что планировать">
          {([["shop", "Весь цех"], ["project", "По проекту"]] as Array<[AutoplanMode, string]>).map(([value, label]) =>
            <button key={value} type="button" role="radio" aria-checked={mode === value} className={mode === value ? "on" : undefined} onClick={() => { if (mode !== value) switchMode(value); }}>{label}</button>)}
        </div>}
        <div className="autoplan-period">
          {editDate
            ? <label>План до<input aria-label="План до" type="date" min={today} max={maxDay} value={dateTo} onChange={(event) => changeDate(event.target.value)} /></label>
            : <span>План: <b>{period || "…"}</b>{dateTo ? " (до выбранной даты)" : ""}</span>}
          {editDate || dateTo
            ? <button type="button" className="link-button" onClick={() => { setEditDate(false); changeDate(""); }}>по работе</button>
            : <button type="button" className="link-button" onClick={() => setEditDate(true)}>изменить</button>}
        </div>
      </div>
      <div className="autoplan-scroll">
        {contextError && <div className="error-banner" role="alert">{contextError}</div>}
        {!context && !contextError && <div className="empty">Загрузка…</div>}
        {context && mode === "shop" && <>
          <div className="autoplan-scope">
            <span>{listed ? `${workText(includedTasks.length, includedNew.length)} · ${count(fitters.length, "монтажник", "монтажника", "монтажников")} · план ${period}` : "Запущенных заданий и КБ с выданной техкартой нет — планировать нечего."}</span>
            <small>Ручные назначения и начатая работа остаются на месте; прежний автоплан пересчитывается.</small>
          </div>
          {listed > 0 && <details className="autoplan-tasks autoplan-exclude">
            <summary>Исключить задания{excluded.size ? ` · исключено ${listed - includedTasks.length - includedNew.length}` : ""}</summary>
            <div className="autoplan-tasks-bar">
              <span className="autoplan-count">в плане {includedTasks.length + includedNew.length} из {listed}</span>
              <button type="button" className="link-button" onClick={() => setAllWork(true)} disabled={!excluded.size}>Выбрать все</button>
              <button type="button" className="link-button" onClick={() => setAllWork(false)} disabled={includedTasks.length + includedNew.length === 0}>Снять все</button>
            </div>
            <div className="autoplan-task-list">
              {tasks.map((task) => <label key={task.id} className="autoplan-check autoplan-task-line" title={[task.number, kbLabel(task.project, task.item), task.name].filter(Boolean).join(" · ")}>
                <input type="checkbox" checked={!excluded.has(task.id)} onChange={() => toggleExcluded(task.id)} aria-label={`${task.number} · ${kbLabel(task.project, task.item)}`} />
                <b>{task.number}</b><span className="autoplan-task-item">{kbLabel(task.project, task.item)}</span><span className="autoplan-task-name">{task.name}{task.deadline ? ` · срок ${formatDayMonth(task.deadline)}` : ""}</span>
              </label>)}
              {ready.map((item) => <label key={item.techcard_id} className="autoplan-check autoplan-task-line" title="Задание создастся при применении плана">
                <input type="checkbox" checked={!excluded.has(item.techcard_id)} onChange={() => toggleExcluded(item.techcard_id)} aria-label={`${kbLabel(item.project, item.index)} · новое задание`} />
                <em className="autoplan-new">новое задание</em><span className="autoplan-task-item">{kbLabel(item.project, item.index)}</span><span className="autoplan-task-name">{item.name} · {item.launched ? `остаток ${item.quantity ?? 0} из ${item.run} (запущено ${item.launched})` : `тираж ${item.run}`}{item.deadline ? ` · срок ${formatDayMonth(item.deadline)}` : ""}</span>
              </label>)}
              {skipped.map((item) => <div key={item.techcard_id} className="autoplan-task-line autoplan-skipped">
                <span /><span className="autoplan-new off">без задания</span><span className="autoplan-task-item">{kbLabel(item.project, item.index)}</span><span className="autoplan-task-name">{item.skipped}</span>
              </div>)}
            </div>
          </details>}
        </>}
        {context && mode === "project" && <>
          <div className="autoplan-kind" role="radiogroup" aria-label="Тип планирования">
            <b>Тип планирования</b>
            <div className="seg-tabs">
              {([["asap", "Как можно раньше"], ["ship", "К сроку отгрузки"]] as Array<[PlanKind, string]>).map(([value, label]) =>
                <button key={value} type="button" role="radio" aria-checked={kind === value} className={kind === value ? "on" : undefined} onClick={() => { setKind(value); setResult(null); setError(""); }}>{label}</button>)}
            </div>
            <small>{kind === "asap" ? "ставит работу проекта с ближайшего свободного времени выбранных людей" : "расчёт от даты отгрузки: когда начать и что отгрузить первым — без назначения"}</small>
          </div>
          {!preset && <label className="autoplan-project">Проект
            <select aria-label="Проект" value={project} onChange={(event) => pickProject(event.target.value)}>
              {!context.projects.length && <option value="">Нет проектов с работой</option>}
              {sortProjects(context.projects).map((row) => <option key={row.project} value={row.project}>{row.project} · {deadlineOf(row) ? `ДЛ ${formatDayMonth(deadlineOf(row))}` : "без срока"} · {workText(row.tasks, row.new)}</option>)}
            </select>
          </label>}
          {projectRow && <div className="autoplan-scope">
            <span>{workText(tasks.length, ready.length)} проекта{skipped.length ? ` · без тиража: ${skipped.map((item) => kbLabel(item.project, item.index)).join(", ")}` : ""}</span>
            <small>Другие проекты остаются как есть; их работа — занятость людей.</small>
          </div>}
          <section className="autoplan-tasks" aria-label="Люди на проект">
            <div className="autoplan-tasks-bar">
              <b>Люди на проект</b>
              <label className="autoplan-count-input">Людей<input type="number" min={0} max={fitters.length} step={1} aria-label="Людей на проект" value={chosenPeople.length}
                onChange={(event) => pickLeast(Math.min(fitters.length, Math.max(0, Math.floor(Number(event.target.value) || 0))))} /></label>
              <span className="autoplan-count">{chosenPeople.length ? `выбрано ${chosenPeople.length} из ${fitters.length}` : "отметьте, кого выделить на проект"} · свободно в {period}</span>
              <button type="button" className="link-button" onClick={() => setAllPeople(true)} disabled={chosenPeople.length === fitters.length}>Выбрать все</button>
              <button type="button" className="link-button" onClick={() => setAllPeople(false)} disabled={!chosenPeople.length}>Снять все</button>
            </div>
            <div className="autoplan-task-list">
              {fitters.map((person) => <label key={person.id} className="autoplan-check autoplan-person">
                <input type="checkbox" checked={people.has(person.id)} onChange={() => togglePerson(person.id)} aria-label={person.name} />
                <b>{person.name}</b><span className="autoplan-task-name">{person.site}</span><span className="autoplan-free">свободно {hoursText(person.free_seconds)}</span>
              </label>)}
              {!fitters.length && <small>Монтажников ЦАР нет.</small>}
            </div>
          </section>
          {projectRow && kind === "ship" && <ShipDatePlan project={project} people={chosenPeople.map((person) => person.id)} deadline={deadlineOf(projectRow)} />}
        </>}
        {context && <SemiLeftoversList rows={semi.rows} error={semi.error} />}
        {error && <div className="error-banner" role="alert">{error}</div>}
        {loading && <div className="empty">Считаем план…</div>}
        {result && !loading && <div className="autoplan-result">
          {stats && groups.length > 0 && <div className="autoplan-summary">Переналадок {stats.changeovers} вместо {stats.changeovers_naive}, сэкономлено {hoursText(stats.saved_seconds)}</div>}
          {result.preview.new_tasks.length > 0 && <div className="autoplan-created">При применении создадутся задания: {result.preview.new_tasks.map((item) => `${kbLabel(item.project, item.index)} × ${item.quantity}`).join(", ")}</div>}
          {result.preview.warnings.map((warning) => <div key={warning} className="warning-inline">{warning}</div>)}
          {!groups.length && <div className="empty">{result.preview.unplaced.length ? "Ничего не поместилось — причины ниже." : `Ставить нечего: вся работа на ${period} уже распределена вручную или начата.`}</div>}
          {groups.map((fitter) => <section key={fitter.id} className="autoplan-fitter" aria-label={`Монтажник ${fitter.name}`}>
            <h3><label className="autoplan-check"><GroupCheck label={`Весь план: ${fitter.name}`} items={fitter.items} chosen={selected} onToggle={(on) => toggle(fitter.items, on)} /><span>{fitter.name}</span></label><small>{count(fitter.items.length, "операция", "операции", "операций")}</small></h3>
            {fitter.days.map((day) => <div key={day.day} className="autoplan-day">
              <div className="autoplan-day-title">{formatDay(day.day)}</div>
              {day.batches.map((batch) => {
                const taskCount = new Set(batch.items.map((item) => item.task_id)).size;
                return <div key={batch.id} className="autoplan-batch">
                  <div className="autoplan-batch-head"><label className="autoplan-check"><GroupCheck label={`Наладка: ${batch.label}`} items={batch.items} chosen={selected} onToggle={(on) => toggle(batch.items, on)} /><span>Наладка: {batch.label || "без наладки"} — {count(batch.items.length, "операция", "операции", "операций")} из {taskCount} ПЗ</span></label>{batch.machine && <small>{batch.machine}</small>}</div>
                  {batch.items.map((item) => <div key={key(item)} className="autoplan-row">
                    <input type="checkbox" aria-label={`${item.task_number}, ${item.stage}`} checked={selected.has(key(item))} onChange={(event) => toggle([item], event.target.checked)} />
                    <TaskCell item={item} />
                    <span className="autoplan-op">{item.stage}{item.code && <small>{item.code}</small>}<LeftoverHint matches={leftoversFor(item)} /></span>
                    <span className="autoplan-slot">{slotText(item.start, item.finish)}</span>
                    <span className="autoplan-change">{item.changeover_seconds > 0 ? `переналадка ${minutesText(item.changeover_seconds)}` : "без переналадки"}</span>
                  </div>)}
                </div>;
              })}
            </div>)}
          </section>)}
          {result.preview.unplaced.length > 0 && <section className="autoplan-unplaced" aria-label="Не поместилось">
            <h3>Не поместилось ({result.preview.unplaced.length})</h3>
            {result.preview.unplaced.map((item) => <div key={`${item.task_id ?? item.techcard_id}:${item.operation_id}`} className="autoplan-unplaced-row"><b>{item.task_number.replace(NEW_PREFIX, "")}{item.position ? ` · этап ${item.position}` : ""}</b> — {item.reason}</div>)}
          </section>}
        </div>}
      </div>
      <div className="form-actions autoplan-actions">
        <button type="button" className="secondary" onClick={close}>{shipOnly ? "Закрыть" : "Отмена"}</button>
        {!shipOnly && result && !loading && proposeButton}
        {!shipOnly && (result && !loading ? <button className="primary" disabled={applying || selected.size === 0} onClick={() => void apply()}>Применить выбранное ({selected.size})</button> : proposeButton)}
      </div>
    </form>
  </div>, document.body);
}
