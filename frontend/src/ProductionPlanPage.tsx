import { useEffect, useMemo, useState } from "react";
import {
  productionApi,
  productionPlanApi,
  type Principal,
  type ProductionAbsence,
  type ProductionOperator,
  type ProductionPlanResult,
  type ProductionPlanRow,
  type ProductionTask,
} from "./api";
import { canAct } from "./navigation";
import { Gantt, deadlineEnd, parseMoment, type GanttBar, type GanttRow } from "./Gantt";
import { sharesOf } from "./MasterBoard";
import { feedsText } from "./productionGraph";
import { PlanLoadSheet, markText, type SheetMark } from "./PlanLoadSheet";

const emptyPlan: ProductionPlanResult = { blockers: [], stages: [], review_values: [], rows: [] };

function shortDate(value: string | null) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric" }).format(new Date(`${value}T00:00:00`));
}

function moment(value: string | null) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

function dayMonth(value: string) {
  return new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "2-digit" }).format(new Date(value.length === 10 ? `${value}T00:00:00` : value));
}

// planDaysTag_
function daysTag(row: ProductionPlanRow) {
  if (row.days_left === null || !row.deadline) return <span className="plan-tag">без дедлайна</span>;
  const days = row.days_left;
  const tone = days < 0 ? "late" : days <= 3 ? "soon" : "ok";
  const text = days < 0 ? `просрочен ${-days} дн` : days === 0 ? "дедлайн сегодня" : `${days} дн до ${dayMonth(row.deadline)}`;
  return <span className={`plan-tag ${tone}`} title={`Дедлайн ${shortDate(row.deadline)}`}>{text}</span>;
}

// Изделие задания, как в SUPPLYMAP (tcKitItemId_): проект + индекс КБ.
function itemId(task: ProductionTask) {
  return task.item.toUpperCase().startsWith(task.project.toUpperCase()) ? task.item : `${task.project}${task.item.toUpperCase()}`;
}

function hours(seconds: number) {
  const value = seconds / 3600;
  return `${(value < 10 ? value.toFixed(1) : String(Math.round(value))).replace(".", ",")} ч`;
}

/** Загрузка по людям: строка — сотрудник, полосы — назначенные ему операции заданий и отсутствия. */
export function operatorLoadRows(
  operators: ProductionOperator[],
  tasks: ProductionTask[],
  absences: ProductionAbsence[],
  span: { from: Date; to: Date },
): GanttRow[] {
  const live = tasks.filter((task) => task.status !== "Отменено");
  return [...operators].sort((a, b) => a.name.localeCompare(b.name, "ru")).map((operator) => {
    let count = 0;
    let seconds = 0;
    const bars: GanttBar[] = [];
    for (const task of live) {
      for (const operation of task.operations) {
        // По долям: у раздробленной по штукам операции у каждого своё окно и своя норма.
        for (const share of sharesOf(operation, operator.id)) {
          if (!share.planned_start || !share.planned_finish) continue;
          const start = parseMoment(share.planned_start);
          const finish = parseMoment(share.planned_finish);
          const done = operation.state === "выполнена" || Boolean(share.finished_at);
          const end = deadlineEnd(task.deadline);
          const late = Boolean(!done && end && finish && finish > end);
          bars.push({
            start: share.planned_start, finish: share.planned_finish,
            tone: done ? "done" : late ? "late" : "default",
            text: `${task.number} · ${itemId(task)}`,
            title: `${task.number} · ${itemId(task)} × ${task.quantity} · этап ${operation.position} ${operation.stage}${feedsText(task, operation) ? ` (${feedsText(task, operation)})` : ""}${share.quantity != null ? ` · ${share.quantity} шт` : ""}${task.deadline ? ` · дедлайн ${shortDate(task.deadline)}` : ""}${done ? " · выполнена" : ""}`,
          });
          if (start && finish && finish > span.from && start < span.to) {
            count += 1;
            seconds += share.quantity != null
              ? Number(operation.setup_seconds) + Number(operation.seconds_per_unit) * share.quantity
              : operation.norm_seconds / Math.max(1, operation.operator_ids.length);
          }
        }
      }
    }
    for (const absence of absences.filter((item) => item.operator_id === operator.id)) {
      bars.push({ start: absence.from_date, finish: nextDay(absence.to_date), tone: "absent", text: absence.kind, title: `${absence.kind}: ${shortDate(absence.from_date)} – ${shortDate(absence.to_date)}` });
    }
    return {
      key: operator.id,
      label: <strong>{operator.name}</strong>,
      sublabel: count ? `${count} опер. · ${hours(seconds)} по норме` : "свободен",
      dim: !count,
      bars,
    };
  });
}

function nextDay(value: string) {
  const day = parseMoment(value);
  if (!day) return value;
  const next = new Date(day.getFullYear(), day.getMonth(), day.getDate() + 1);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${next.getFullYear()}-${pad(next.getMonth() + 1)}-${pad(next.getDate())}`;
}

const LOAD_DAYS = 21;

export function ProductionPlanPage({ principal, openProjectCard }: {
  principal: Principal;
  /** Opens the project's techcard card (SUPPLYMAP planAct_ 'card' → openKitsetCard); absent without access. */
  openProjectCard?: (project: string) => void;
}) {
  const [data, setData] = useState(emptyPlan);
  const [holidays, setHolidays] = useState<string[]>([]);
  const [absences, setAbsences] = useState<ProductionAbsence[]>([]);
  const [tasks, setTasks] = useState<ProductionTask[]>([]);
  const [operators, setOperators] = useState<ProductionOperator[]>([]);
  const [loadError, setLoadError] = useState("");
  const [week, setWeek] = useState(0);
  const [selectedProject, setSelectedProject] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const canEdit = canAct(principal, "production", ["master", "admin"]);

  async function reload() {
    setError("");
    try {
      const result = await productionPlanApi.load();
      setData(result);
      setSelectedProject((current) => current && result.rows.some((row) => row.project === current) ? current : "");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Производственный план не загружен");
    } finally {
      setLoaded(true);
    }
  }

  useEffect(() => {
    void reload();
    // Календарь только затеняет шкалу и даёт отсутствия: без него план работает.
    productionApi.calendar().then((result) => { setHolidays(result.holidays ?? []); setAbsences(result.absences ?? []); }).catch(() => setHolidays([]));
    // Загрузка по людям — из заданий «Ресурсного планирования»; без доступа к ним блок говорит об этом.
    Promise.all([productionApi.list(), productionApi.operators()])
      .then(([taskResult, operatorResult]) => { setTasks(taskResult.tasks ?? []); setOperators(operatorResult.operators ?? []); })
      .catch((reason) => setLoadError(reason instanceof Error ? reason.message : "Задания не загружены"));
  }, []);

  const sheetSpan = useMemo(() => {
    // Календарь листа: сегодня −7 … +60 дней; сервер присылает границы, без них считаем сами.
    const today = new Date();
    const iso = (day: Date) => `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, "0")}-${String(day.getDate()).padStart(2, "0")}`;
    const shift = (days: number) => iso(new Date(today.getFullYear(), today.getMonth(), today.getDate() + days));
    return { today: data.today ?? shift(0), from: data.calendar_from ?? shift(-7), to: data.calendar_to ?? shift(60) };
  }, [data.today, data.calendar_from, data.calendar_to]);
  const activeCount = data.rows.filter((row) => (row.group ?? (row.terminal ? "closed" : "active")) !== "closed").length;
  const selected = data.rows.find((row) => row.project === selectedProject) ?? null;

  const span = useMemo(() => {
    const today = new Date();
    const from = new Date(today.getFullYear(), today.getMonth(), today.getDate() - ((today.getDay() + 6) % 7) + 7 * week);
    return { from, to: new Date(from.getFullYear(), from.getMonth(), from.getDate() + LOAD_DAYS) };
  }, [week]);
  const loadRows = useMemo(() => operatorLoadRows(operators, tasks, absences, span), [operators, tasks, absences, span]);
  const unassigned = useMemo(() => {
    const live = tasks.filter((task) => task.status !== "Отменено");
    const operations = live.flatMap((task) => task.operations.filter((operation) => !operation.operator_ids.length && operation.state !== "выполнена"));
    return { operations: operations.length, tasks: live.filter((task) => task.operations.some((operation) => !operation.operator_ids.length && operation.state !== "выполнена")).length };
  }, [tasks]);

  async function addEvent(payload: Parameters<typeof productionPlanApi.addEvent>[0]) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await productionPlanApi.addEvent(payload);
      await reload();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Запись в журнал не добавлена");
    } finally {
      setBusy(false);
    }
  }

  async function addMark(mark: SheetMark) {
    await addEvent({ project: mark.project, kind: "mark", blocker: "", text: mark.text ?? "", deadline: null, field: mark.field, value: mark.value ?? "" });
  }

  function openRow(row: ProductionPlanRow) {
    // № проекта открывает его карточку (владелец 29.09: панель снизу неудобна); проект без карточки — журнал сбоку.
    if (openProjectCard && row.kits.length) openProjectCard(row.project);
    else setSelectedProject(row.project);
  }

  return <section className="page production-plan-page">
    <header className="page-head">
      <h1>Производственный план</h1>
      <span className="page-count">{activeCount}</span>
    </header>
    {error && <div className="error-banner" role="alert">{error}</div>}

    <section className="panel plan-projects zg-panel" aria-label="Загрузка цеха">
      <div className="plan-section-head"><h2>Загрузка цеха</h2><span className="zg-span">{shortDate(sheetSpan.from)} – {shortDate(sheetSpan.to)}</span></div>
      {!loaded ? <div className="empty">Загрузка…</div>
        : <PlanLoadSheet rows={data.rows} from={sheetSpan.from} to={sheetSpan.to} today={sheetSpan.today} holidays={holidays}
          shopStatuses={data.shop_statuses ?? []} suppliedValues={data.supplied_values ?? ["НЕТ", "ДА", "Ч/Т"]}
          arrivalValues={data.arrival_values ?? ["Склад", "Ждем", "???"]} canEdit={canEdit} busy={busy}
          onMark={addMark}
          onDeadline={(project, deadline) => addEvent({ project, kind: "deadline", blocker: "", text: "", deadline })}
          onOpenProject={openRow} onOpenJournal={(row) => setSelectedProject(row.project)} />}
      {selected && <div className="plan-drawer" role="dialog" aria-label={`Проект ${selected.project}`}>
        <div className="plan-drawer-scrim" onClick={() => setSelectedProject("")} />
        <div className="plan-drawer-body">
        <button className="link-button plan-details-close" onClick={() => setSelectedProject("")}>✕ Закрыть</button>
        <PlanDetails row={selected} blockers={data.blockers} stages={data.stages} reviewValues={data.review_values} canEdit={canEdit} busy={busy} addEvent={addEvent}
          openProjectCard={openProjectCard && selected.kits.length ? () => openProjectCard(selected.project) : undefined} />
        </div>
      </div>}
    </section>

    <section className="panel plan-load" aria-label="Загрузка по людям">
      <div className="plan-section-head"><h2>Загрузка по людям</h2>
        {unassigned.operations > 0 && <span className="plan-tag soon">Без исполнителя: {unassigned.operations} опер. в {unassigned.tasks} зад. — назначить в «Ресурсном планировании»</span>}
      </div>
      {loadError
        ? <div className="empty">Загрузка людей недоступна: {loadError}</div>
        : <Gantt labelHead="Сотрудник" holidays={holidays} days={LOAD_DAYS} mergeDay week={week} onWeekChange={setWeek}
          empty={<div className="empty">Сотрудников цеха пока нет.</div>}
          rows={loadRows} />}
      <div className="plan-legend"><span className="gantt-bar">операция</span><span className="gantt-bar late">позже дедлайна</span><span className="gantt-bar done">выполнена</span><span className="gantt-bar absent">отсутствие</span></div>
    </section>
  </section>;
}

function PlanDetails({ row, blockers, stages, reviewValues, canEdit, busy, addEvent, openProjectCard }: {
  row: ProductionPlanRow;
  openProjectCard?: () => void;
  blockers: string[];
  stages: string[];
  reviewValues: string[];
  canEdit: boolean;
  busy: boolean;
  addEvent: (payload: Parameters<typeof productionPlanApi.addEvent>[0]) => Promise<void>;
}) {
  const [kind, setKind] = useState<"note" | "block" | "unblock" | "deadline">("note");
  const [blocker, setBlocker] = useState(blockers[0] ?? "");
  const [text, setText] = useState("");
  const [deadline, setDeadline] = useState(row.deadline ?? "");

  useEffect(() => { setDeadline(row.deadline ?? ""); setText(""); }, [row.project, row.deadline]);

  async function submit() {
    await addEvent({ project: row.project, kind, blocker, text, deadline: deadline || null });
    setText("");
    if (kind === "unblock") setKind("note");
  }

  const valid = kind === "unblock" || (kind === "deadline" ? Boolean(deadline) : Boolean(text.trim()) && (kind !== "block" || Boolean(blocker)));
  return <aside className="panel production-plan-details">
    <div className="plan-detail-head"><div><span className="eyebrow">Проект</span><h2>{row.project}</h2></div><span className="status">{row.stage}</span></div>
    <div className="plan-tags">{row.blocker && <span className="plan-tag blocker">{row.blocker.type}</span>}{daysTag(row)}</div>
    {openProjectCard && <div className="plan-acts"><button className="secondary" onClick={openProjectCard}>Карточка проекта</button></div>}
    {row.blocker && <p className="plan-blocker">⛔ {row.blocker.type} с {moment(row.blocker.since)} · {row.blocker.text} — {row.blocker.who}</p>}
    <section><h3>Проект</h3><p>Заказчик: {row.customer || "—"} · дедлайн {shortDate(row.deadline)} · заведён {shortDate(row.opened_on)}</p></section>
    <div className="plan-kit-stages">{row.kits.map((item) => <KitMarkEditor
      key={item.item_index} project={row.project} item={item} stages={stages}
      reviewValues={reviewValues} canEdit={canEdit} busy={busy} addEvent={addEvent}
    />)}
      {/* A project without KBs takes its stage from a journal stage mark with an empty KB. */}
      {!row.kits.length && canEdit && stages.includes(row.stage) && <div className="plan-kit-mark">
        <strong>Без КБ</strong>
        <label>Этап проекта<select aria-label="Этап проекта" value={row.stage_manual ? row.stage : "авто"} disabled={busy}
          onChange={(event) => void addEvent({ project: row.project, kind: "stage", blocker: "", text: "", deadline: null, item_index: "", value: event.target.value })}>
          <option value="авто">Авто · {row.stage}</option>{stages.map((stage) => <option key={stage}>{stage}</option>)}
        </select></label>
      </div>}
    </div>
    {row.supply ? <section><h3>Снабжение</h3><p>Всего {row.supply.total}: склад {row.supply.stock}, в пути {row.supply.transit}, в закупке {row.supply.buying}, в заявках {row.supply.request}.</p>
      <p>Давальческое: {row.supply.customer_supplied ? `${row.supply.customer_supplied_stock} из ${row.supply.customer_supplied} пришло` : "нет"}</p>
      {row.supply.eta && row.supply.stock < row.supply.total && <small>Всё будет к {row.supply.eta}</small>}</section>
      : <section><h3>Снабжение</h3><p>Заявок с этим проектом в НАЗНАЧЕНИИ нет.</p></section>}
    {row.plan && <section><h3>Загрузка цеха</h3><p>{row.plan.tasks} заданий · назначено {row.plan.assigned} из {row.plan.total} этапов.</p>{row.plan.start && <small>{moment(row.plan.start)} — {moment(row.plan.finish)}</small>}</section>}
    {canEdit && <section className="plan-note-form"><h3>Журнал проекта</h3><div className="plan-note-grid">
      <label>Запись<select value={kind} onChange={(event) => setKind(event.target.value as typeof kind)}><option value="note">Комментарий</option><option value="block">Блокер</option>{row.blocker && <option value="unblock">Снять блокер</option>}<option value="deadline">Дедлайн</option></select></label>
      {kind === "block" && <label>Вид<select value={blocker} onChange={(event) => setBlocker(event.target.value)}>{blockers.map((item) => <option key={item}>{item}</option>)}</select></label>}
      {kind === "deadline" && <label>Дата<input type="date" value={deadline} onChange={(event) => setDeadline(event.target.value)} /></label>}
      {kind !== "unblock" && <label className="plan-note-text">Комментарий<textarea value={text} onChange={(event) => setText(event.target.value)} /></label>}
      <button className="primary" disabled={busy || !valid} onClick={() => void submit()}>Записать</button>
    </div></section>}
    <section className="plan-journal"><h3>История</h3>{row.events.map((event) => <article key={event.id}><div><strong>{event.kind}{event.blocker ? ` · ${event.blocker}` : ""}</strong><time>{moment(event.occurred_at)}</time></div><p>{event.field ? markText(event) : event.text || "Без комментария"}</p><small>{event.who}</small></article>)}{!row.events.length && <div className="empty">Записей пока нет.</div>}</section>
  </aside>;
}

function KitMarkEditor({ project, item, stages, reviewValues, canEdit, busy, addEvent }: {
  project: string;
  item: ProductionPlanRow["kits"][number];
  stages: string[];
  reviewValues: string[];
  canEdit: boolean;
  busy: boolean;
  addEvent: (payload: Parameters<typeof productionPlanApi.addEvent>[0]) => Promise<void>;
}) {
  const currentReview = item.review?.value ?? "не проверена";
  const [review, setReview] = useState(currentReview);
  const [reviewText, setReviewText] = useState(item.review?.text ?? "");
  useEffect(() => {
    setReview(currentReview);
    setReviewText(item.review?.text ?? "");
  }, [project, item.item_index, currentReview, item.review?.text]);

  function payload(kind: "stage" | "tcReview", value: string, text = "") {
    return { project, kind, blocker: "", text, deadline: null, item_index: item.item_index, value } as const;
  }

  return <div className="plan-kit-mark">
    <strong>{item.item_index}</strong>
    <span>{item.tc ? "ТК выдана" : "ТК нет"} · {item.kit ? "набрано" : "не набрано"}</span>
    {canEdit ? <>
      <label>Этап<select aria-label={`Этап ${item.item_index}`} value={item.manual ? item.status : "авто"} disabled={busy}
        onChange={(event) => void addEvent(payload("stage", event.target.value))}>
        <option value="авто">Авто · {item.status}</option>{stages.map((stage) => <option key={stage}>{stage}</option>)}
      </select></label>
      <label>Проверка ТК<select aria-label={`Проверка ТК ${item.item_index}`} value={review} disabled={busy}
        onChange={(event) => {
          const value = event.target.value;
          setReview(value);
          if (value !== "с погрешностями") void addEvent(payload("tcReview", value));
        }}>
        {reviewValues.map((value) => <option key={value}>{value}</option>)}
      </select></label>
      {review === "с погрешностями" && <label>Погрешности<textarea aria-label={`Погрешности ${item.item_index}`} value={reviewText} onChange={(event) => setReviewText(event.target.value)} /></label>}
      {review === "с погрешностями" && <button className="secondary small" disabled={busy || !reviewText.trim()} onClick={() => void addEvent(payload("tcReview", review, reviewText))}>Сохранить проверку</button>}
    </> : <>
      <span>{item.status}{item.manual ? " · вручную" : ""}</span>
      <span>{currentReview}{item.review?.text ? ` · ${item.review.text}` : ""}</span>
    </>}
  </div>;
}
