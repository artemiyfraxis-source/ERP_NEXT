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
import { matchesWords } from "./DataTable";
import { Gantt, deadlineEnd, parseMoment, type GanttBar, type GanttRow } from "./Gantt";

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

// SUPPLYMAP PLAN_FILTERS_: «в работе» — not terminal in the registry and not everything shipped.
const planActive = (row: ProductionPlanRow) => !row.terminal && row.stage !== "Отгружено";
const PLAN_FILTERS: Array<[string, string, (row: ProductionPlanRow) => boolean]> = [
  ["active", "В работе", planActive],
  ["blocked", "С блокером", (row) => planActive(row) && Boolean(row.blocker)],
  ["late", "Просрочено", (row) => planActive(row) && row.days_left !== null && row.days_left < 0],
  ["noplan", "Без плана цеха", (row) => planActive(row) && !row.plan && !["В работе", "КБ на складе"].includes(row.stage)],
  ["all", "Все", () => true],
];

// planRenderList_ sort: blocked first, then by days to deadline (burning first), no deadline last.
export function sortPlanRows(rows: ProductionPlanRow[]) {
  return [...rows].sort((a, b) => {
    if (Boolean(a.blocker) !== Boolean(b.blocker)) return a.blocker ? -1 : 1;
    const da = a.days_left ?? 1e9;
    const db = b.days_left ?? 1e9;
    return da - db || a.project.localeCompare(b.project, "ru");
  });
}

// planDaysTag_
function daysTag(row: ProductionPlanRow) {
  if (row.days_left === null || !row.deadline) return <span className="plan-tag">без дедлайна</span>;
  const days = row.days_left;
  const tone = days < 0 ? "late" : days <= 3 ? "soon" : "ok";
  const text = days < 0 ? `просрочен ${-days} дн` : days === 0 ? "дедлайн сегодня" : `${days} дн до ${dayMonth(row.deadline)}`;
  return <span className={`plan-tag ${tone}`} title={`Дедлайн ${shortDate(row.deadline)}`}>{text}</span>;
}

// planKbText_: how many KBs reached each stage of the ladder.
export function kbText(row: ProductionPlanRow, stages: string[]) {
  if (!row.kits.length) return "нет состава";
  const at = (stage: string) => {
    const index = stages.indexOf(stage);
    return row.kits.filter((item) => stages.indexOf(item.status) >= index).length;
  };
  return `КБ ${row.kits.length}: ТК ${at("ТК готова")} · набрано ${at("КБ набрана")} · на складе ${at("КБ на складе")}`;
}

// planSupplyText_: «на складе 12/20 · в пути 5 · в закупке 3 · ждём до 03.10 · давальч. 1/2».
export function supplyText(supply: ProductionPlanRow["supply"]) {
  if (!supply) return "заявок нет";
  const parts = [`на складе ${supply.stock}/${supply.total}`];
  if (supply.transit) parts.push(`в пути ${supply.transit}`);
  if (supply.buying) parts.push(`в закупке ${supply.buying}`);
  if (supply.request) parts.push(`заявка ${supply.request}`);
  if (supply.eta && supply.stock < supply.total) parts.push(`ждём до ${supply.eta.slice(0, 5)}`);
  if (supply.customer_supplied) parts.push(`давальч. ${supply.customer_supplied_stock}/${supply.customer_supplied}`);
  return parts.join(" · ");
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
        if (!operation.operator_ids.includes(operator.id) || !operation.planned_start || !operation.planned_finish) continue;
        const start = parseMoment(operation.planned_start);
        const finish = parseMoment(operation.planned_finish);
        const done = operation.state === "выполнена";
        const end = deadlineEnd(task.deadline);
        const late = Boolean(!done && end && finish && finish > end);
        bars.push({
          start: operation.planned_start, finish: operation.planned_finish,
          tone: done ? "done" : late ? "late" : "default",
          text: `${task.number} · ${itemId(task)}`,
          title: `${task.number} · ${itemId(task)} × ${task.quantity} · этап ${operation.position} ${operation.stage}${task.deadline ? ` · дедлайн ${shortDate(task.deadline)}` : ""}${done ? " · выполнена" : ""}`,
        });
        if (start && finish && finish > span.from && start < span.to) {
          count += 1;
          seconds += operation.norm_seconds / Math.max(1, operation.operator_ids.length);
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

const LOAD_DAYS = 28;

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
  const [filter, setFilter] = useState("active");
  const [query, setQuery] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const canEdit = principal.permissions.production === "edit"
    && principal.roles.some((role) => ["master", "admin"].includes(role));

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

  const current = PLAN_FILTERS.find(([id]) => id === filter) ?? PLAN_FILTERS[0];
  const rows = useMemo(() => {
    // #planQ: № проекта или заказчик — the shared DataTable word search over those two fields.
    return sortPlanRows(data.rows.filter(current[2]).filter((row) => matchesWords([row.project, row.customer], query)));
  }, [data.rows, current, query]);
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

  return <section className="page production-plan-page">
    <header className="page-head">
      <div><h1>Производственный план</h1><p className="page-sub">Сверху — проекты в работе и их дедлайны, ниже — загрузка людей по заданиям. Блокер, комментарий и дедлайн — нажми на проект.</p></div>
      <span className="page-count">{rows.length}</span>
    </header>
    {error && <div className="error-banner" role="alert">{error}</div>}

    <section className="panel plan-projects" aria-label="Проекты">
      <div className="plan-section-head"><h2>Проекты</h2>
        <div className="plan-filters" role="group" aria-label="Фильтр проектов">
          {PLAN_FILTERS.map(([id, label, test]) => <button key={id} className={id === current[0] ? "secondary active" : "secondary"} onClick={() => setFilter(id)}>
            {label}<b>{data.rows.filter(test).length}</b>
          </button>)}
          <input aria-label="Поиск проекта" placeholder="№ проекта или заказчик" value={query} onChange={(event) => setQuery(event.target.value)} />
        </div>
      </div>
      <div className="table-wrap"><table className="plan-table">
        <thead><tr><th>Проект</th><th>Этап</th><th>Дедлайн</th><th>Комплектующие</th><th>План цеха</th><th>Последняя запись</th></tr></thead>
        <tbody>
          {rows.map((row) => <tr key={row.project} className={`${row.project === selectedProject ? "selected" : ""}${row.terminal ? " dim" : ""}`}
            onClick={() => setSelectedProject(row.project === selectedProject ? "" : row.project)}>
            <td><strong>{row.project}</strong><div className="plan-customer">{row.customer}</div></td>
            <td><div className="plan-tags"><span className="plan-tag stage">{row.stage}</span>{row.blocker && <span className="plan-tag blocker" title={row.blocker.text}>{row.blocker.type}</span>}</div><small>{kbText(row, data.stages)}</small></td>
            <td><div>{shortDate(row.deadline)}</div>{daysTag(row)}</td>
            <td><small>{supplyText(row.supply)}</small></td>
            <td>{row.plan ? <small>{row.plan.start ? dayMonth(row.plan.start) : "без сроков"}{row.plan.finish ? ` – ${dayMonth(row.plan.finish)}` : ""} · {row.plan.tasks} зад. · назначено {row.plan.assigned}/{row.plan.total}</small> : <small className="muted">нет заданий</small>}</td>
            <td><small>{row.last ? `${dayMonth(row.last.occurred_at)} · ${row.last.text}` : "—"}</small></td>
          </tr>)}
          {loaded && !rows.length && <tr><td colSpan={6} className="empty">Под этот фильтр проектов нет.</td></tr>}
        </tbody>
      </table></div>
      {selected && <div className="plan-details-wrap">
        <button className="link-button plan-details-close" onClick={() => setSelectedProject("")}>Свернуть</button>
        <PlanDetails row={selected} blockers={data.blockers} stages={data.stages} reviewValues={data.review_values} canEdit={canEdit} busy={busy} addEvent={addEvent}
          openProjectCard={openProjectCard && selected.kits.length ? () => openProjectCard(selected.project) : undefined} />
      </div>}
    </section>

    <section className="panel plan-load" aria-label="Загрузка по людям">
      <div className="plan-section-head"><h2>Загрузка по людям</h2>
        {unassigned.operations > 0 && <span className="plan-tag soon">Без исполнителя: {unassigned.operations} опер. в {unassigned.tasks} зад. — назначить в «Ресурсном планировании»</span>}
      </div>
      {loadError
        ? <div className="empty">Загрузка людей недоступна: {loadError}</div>
        : <Gantt labelHead="Сотрудник" holidays={holidays} days={LOAD_DAYS} week={week} onWeekChange={setWeek}
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
    <section className="plan-journal"><h3>История</h3>{row.events.map((event) => <article key={event.id}><div><strong>{event.kind}{event.blocker ? ` · ${event.blocker}` : ""}</strong><time>{moment(event.occurred_at)}</time></div><p>{event.text || "Без комментария"}</p><small>{event.who}</small></article>)}{!row.events.length && <div className="empty">Записей пока нет.</div>}</section>
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
