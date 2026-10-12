import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ApiError, failureText, productionApi, type AutoplanPreview, type AutoplanProposal, type AutoplanRequest, type ProductionTask } from "./api";
import { formatDay } from "./format";
import { taskItemLabel } from "./productionGraph";

const STALE_TEXT = "План изменился — обновите предложение";

function pad(value: number) {
  return String(value).padStart(2, "0");
}

function isoDay(date: Date) {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

/** Сегодня + N рабочих дней (сб и вс пропускаются). */
export function addWorkingDays(from: Date, count: number): Date {
  const date = new Date(from.getFullYear(), from.getMonth(), from.getDate());
  let left = count;
  while (left > 0) {
    date.setDate(date.getDate() + 1);
    if (date.getDay() !== 0 && date.getDay() !== 6) left -= 1;
  }
  return date;
}

/** Время из строки как записано (московское), без пересчёта в часовой пояс браузера. */
function parts(value: string) {
  const match = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/.exec(value);
  return match ? { day: `${match[1]}-${match[2]}-${match[3]}`, short: `${match[3]}.${match[2]}`, time: `${match[4]}:${match[5]}` } : { day: value, short: value, time: "" };
}

const shortDay = (iso: string) => formatDay(iso).slice(0, 5);

function minutesText(seconds: number) {
  const minutes = Math.max(1, Math.round(seconds / 60));
  return minutes >= 60 && minutes % 60 === 0 ? `${minutes / 60} ч` : `${minutes} мин`;
}

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

export function AutoplanPanel({ tasks, close, onApplied }: {
  tasks: ProductionTask[];
  close: () => void;
  onApplied: (count: number) => void;
}) {
  const today = new Date();
  const [from, setFrom] = useState(isoDay(today));
  const [to, setTo] = useState(isoDay(addWorkingDays(today, 6)));
  const [replan, setReplan] = useState(false);
  const [loading, setLoading] = useState(false);
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<{ preview: AutoplanPreview; request: AutoplanRequest } | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const live = useMemo(() => tasks.filter((task) => task.status !== "Отменено" && task.status !== "Выполнено"), [tasks]);
  // Все запущенные задания отмечены с самого начала; в запрос task_ids уходит, только если часть снята.
  const [chosenTasks, setChosenTasks] = useState<Set<string>>(() => new Set(live.map((task) => task.id)));
  const dialogRef = useRef<HTMLFormElement>(null);
  const closeRef = useRef(close);
  closeRef.current = close;
  useEffect(() => {
    dialogRef.current?.focus();
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") { event.stopPropagation(); closeRef.current(); } };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);
  const chosenLive = live.filter((task) => chosenTasks.has(task.id));
  const taskIds = chosenLive.length < live.length ? chosenLive.map((task) => task.id) : [];
  const periodText = from && to ? `${shortDay(from)}–${shortDay(to)}` : "";
  const groups = useMemo(() => (result ? group(result.preview.proposals) : []), [result]);

  async function run(request?: AutoplanRequest) {
    const body: AutoplanRequest = request ?? {
      date_from: from, date_to: to, replan_auto: replan,
      ...(taskIds.length ? { task_ids: taskIds } : {}),
    };
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
      onApplied(applied.applied);
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
  const toggleTask = (id: string) => setChosenTasks((current) => { const next = new Set(current); if (!next.delete(id)) next.add(id); return next; });
  const setAllTasks = (on: boolean) => setChosenTasks(on ? new Set(live.map((task) => task.id)) : new Set());
  const stats = result?.preview.stats;

  const proposeButton = <button className={result ? "secondary" : "primary"} disabled={loading || applying || !from || !to || to < from || (live.length > 0 && chosenLive.length === 0)} onClick={() => void run()}>Предложить план на {periodText}</button>;

  return createPortal(<div className="dialog-overlay" onMouseDown={(event) => { if (event.target === event.currentTarget) close(); }}>
    <form ref={dialogRef} tabIndex={-1} className="move-dialog autoplan-dialog panel" role="dialog" aria-modal="true" aria-label="Автоплан" onSubmit={(event) => event.preventDefault()}>
      <div className="autoplan-head">
        <h2>Автоплан</h2>
        <div className="autoplan-form">
          <label>С<input aria-label="Период с" type="date" value={from} onChange={(event) => setFrom(event.target.value)} /></label>
          <label>По<input aria-label="Период по" type="date" value={to} onChange={(event) => setTo(event.target.value)} /></label>
          <button type="button" className="secondary small" onClick={() => { setFrom(isoDay(new Date())); setTo(isoDay(addWorkingDays(new Date(), 6))); }}>Неделя</button>
        </div>
      </div>
      <div className="autoplan-scroll">
      <section className="autoplan-tasks" aria-label="Задания">
        <div className="autoplan-tasks-bar">
          <b>Задания</b>
          <span className="autoplan-count">выбрано {chosenLive.length} из {live.length}</span>
          <button type="button" className="link-button" onClick={() => setAllTasks(true)} disabled={chosenLive.length === live.length}>Выбрать все</button>
          <button type="button" className="link-button" onClick={() => setAllTasks(false)} disabled={chosenLive.length === 0}>Снять все</button>
        </div>
        <div className="autoplan-task-list">
          {live.map((task) => {
            const name = task.name ?? "";
            return <label key={task.id} className="autoplan-check autoplan-task-line" title={[task.number, taskItemLabel(task), name].filter(Boolean).join(" · ")}>
              <input type="checkbox" checked={chosenTasks.has(task.id)} onChange={() => toggleTask(task.id)} aria-label={`${task.number} · ${taskItemLabel(task)}${name ? ` · ${name}` : ""}`} />
              <b>{task.number}</b><span className="autoplan-task-item">{taskItemLabel(task)}</span>{name && <span className="autoplan-task-name">{name}</span>}
            </label>;
          })}
          {!live.length && <small>Запущенных заданий нет.</small>}
        </div>
      </section>
      <label className="autoplan-check"><input type="checkbox" checked={replan} onChange={(event) => setReplan(event.target.checked)} /><span>Пересчитать прежний автоплан</span></label>
      {error && <div className="error-banner" role="alert">{error}</div>}
      {loading && <div className="empty">Считаем план…</div>}
      {result && !loading && <div className="autoplan-result">
        {stats && <div className="autoplan-summary">Переналадок {stats.changeovers} вместо {stats.changeovers_naive}, сэкономлено {(stats.saved_seconds / 3600).toLocaleString("ru-RU", { maximumFractionDigits: 1 })} ч</div>}
        {result.preview.warnings.map((warning) => <div key={warning} className="warning-inline">{warning}</div>)}
        {!groups.length && <div className="empty">На {periodText} нечего планировать.</div>}
        {groups.map((fitter) => <section key={fitter.id} className="autoplan-fitter" aria-label={`Монтажник ${fitter.name}`}>
          <h3><label className="autoplan-check"><GroupCheck label={`Весь план: ${fitter.name}`} items={fitter.items} chosen={selected} onToggle={(on) => toggle(fitter.items, on)} /><span>{fitter.name}</span></label><small>{fitter.items.length} {plural(fitter.items.length, "операция", "операции", "операций")}</small></h3>
          {fitter.days.map((day) => <div key={day.day} className="autoplan-day">
            <div className="autoplan-day-title">{formatDay(day.day)}</div>
            {day.batches.map((batch) => {
              const taskCount = new Set(batch.items.map((item) => item.task_id)).size;
              return <div key={batch.id} className="autoplan-batch">
                <div className="autoplan-batch-head"><label className="autoplan-check"><GroupCheck label={`Наладка: ${batch.label}`} items={batch.items} chosen={selected} onToggle={(on) => toggle(batch.items, on)} /><span>Наладка: {batch.label || "без наладки"} — {batch.items.length} {plural(batch.items.length, "операция", "операции", "операций")} из {taskCount} ПЗ</span></label>{batch.machine && <small>{batch.machine}</small>}</div>
                {batch.items.map((item) => <div key={key(item)} className="autoplan-row">
                  <input type="checkbox" aria-label={`${item.task_number}, ${item.stage}`} checked={selected.has(key(item))} onChange={(event) => toggle([item], event.target.checked)} />
                  <span className="autoplan-task">{item.task_number}</span>
                  <span className="autoplan-op">{item.stage}{item.code && <small>{item.code}</small>}</span>
                  <span className="autoplan-slot">{slotText(item.start, item.finish)}</span>
                  <span className="autoplan-change">{item.changeover_seconds > 0 ? `переналадка ${minutesText(item.changeover_seconds)}` : "без переналадки"}</span>
                </div>)}
              </div>;
            })}
          </div>)}
        </section>)}
        {result.preview.unplaced.length > 0 && <section className="autoplan-unplaced" aria-label="Не поместилось">
          <h3>Не поместилось ({result.preview.unplaced.length})</h3>
          {result.preview.unplaced.map((item) => <div key={`${item.task_id}:${item.operation_id}`} className="autoplan-unplaced-row"><b>{item.task_number}{item.position ? ` · этап ${item.position}` : ""}</b> — {item.reason}</div>)}
        </section>}
      </div>}
      </div>
      <div className="form-actions autoplan-actions">
        <button type="button" className="secondary" onClick={close}>Отмена</button>
        {result && !loading && proposeButton}
        {result && !loading ? <button className="primary" disabled={applying || selected.size === 0} onClick={() => void apply()}>Применить выбранное ({selected.size})</button> : proposeButton}
      </div>
    </form>
  </div>, document.body);
}
