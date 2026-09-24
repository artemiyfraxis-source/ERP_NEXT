import { useEffect, useMemo, useState } from "react";
import {
  productionPlanApi,
  type Principal,
  type ProductionPlanResult,
  type ProductionPlanRow,
} from "./api";

const emptyPlan: ProductionPlanResult = { blockers: [], stages: [], review_values: [], rows: [] };

function shortDate(value: string | null) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric" }).format(new Date(`${value}T00:00:00`));
}

function moment(value: string | null) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

export function ProductionPlanPage({ principal }: { principal: Principal }) {
  const [data, setData] = useState(emptyPlan);
  const [selectedProject, setSelectedProject] = useState("");
  const [filter, setFilter] = useState<"active" | "blocked" | "all">("active");
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
      setSelectedProject((current) => current && result.rows.some((row) => row.project === current)
        ? current
        : result.rows[0]?.project ?? "");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Производственный план не загружен");
    } finally {
      setLoaded(true);
    }
  }

  useEffect(() => { void reload(); }, []);

  const rows = useMemo(() => data.rows.filter((row) => (
    filter === "all" || (filter === "blocked" ? row.blocker : row.stage !== "Отгружено")
  )), [data.rows, filter]);
  const selected = data.rows.find((row) => row.project === selectedProject) ?? null;

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
      <div><h1>Производственный план</h1><p className="page-sub">Проекты, сроки, блокеры, комплектация и загрузка цеха в одной сводке.</p></div>
      <span className="page-count">{rows.length}</span>
    </header>
    {error && <div className="error-banner" role="alert">{error}</div>}
    <div className="plan-filters" role="group" aria-label="Фильтр проектов">
      <button className={filter === "active" ? "secondary active" : "secondary"} onClick={() => setFilter("active")}>Активные</button>
      <button className={filter === "blocked" ? "secondary active" : "secondary"} onClick={() => setFilter("blocked")}>Блокеры</button>
      <button className={filter === "all" ? "secondary active" : "secondary"} onClick={() => setFilter("all")}>Все</button>
    </div>
    <div className="production-plan-layout">
      <div className="production-plan-list">
        {rows.map((row) => <PlanRow key={row.project} row={row} selected={row.project === selectedProject} onClick={() => setSelectedProject(row.project)} />)}
        {loaded && !rows.length && <div className="empty">Проектов в этом фильтре нет.</div>}
      </div>
      {selected && <PlanDetails row={selected} blockers={data.blockers} stages={data.stages} reviewValues={data.review_values} canEdit={canEdit} busy={busy} addEvent={addEvent} />}
    </div>
  </section>;
}

function PlanRow({ row, selected, onClick }: { row: ProductionPlanRow; selected: boolean; onClick: () => void }) {
  const supply = row.supply;
  const urgent = row.days_left !== null && row.days_left < 0 ? " overdue" : row.days_left !== null && row.days_left <= 3 ? " urgent" : "";
  return <button className={`panel production-plan-row${selected ? " selected" : ""}${row.blocker ? " blocked" : ""}`} onClick={onClick}>
    <div className="plan-row-head"><strong>{row.project}</strong><span className="status">{row.stage}</span></div>
    <span className="plan-customer">{row.customer || "Заказчик не указан"}</span>
    <div className="plan-row-metrics">
      <span className={urgent}>Дедлайн: {shortDate(row.deadline)}{row.days_left !== null ? ` · ${row.days_left} дн.` : ""}</span>
      <span>КБ: {row.kits.map((item) => item.item_index).join(", ")}</span>
      <span>Снабжение: {supply ? `${supply.stock}/${supply.total} на складе` : "нет позиций"}</span>
      <span>План: {row.plan ? `${row.plan.assigned}/${row.plan.total} этапов` : "нет заданий"}</span>
    </div>
    {row.blocker && <span className="plan-blocker">⛔ {row.blocker.type} · {row.blocker.text}</span>}
  </button>;
}

function PlanDetails({ row, blockers, stages, reviewValues, canEdit, busy, addEvent }: {
  row: ProductionPlanRow;
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
    <div className="plan-kit-stages">{row.kits.map((item) => <KitMarkEditor
      key={item.item_index} project={row.project} item={item} stages={stages}
      reviewValues={reviewValues} canEdit={canEdit} busy={busy} addEvent={addEvent}
    />)}</div>
    {row.supply && <section><h3>Снабжение</h3><p>Всего {row.supply.total}: склад {row.supply.stock}, в пути {row.supply.transit}, в закупке {row.supply.buying}, в заявках {row.supply.request}.</p>{row.supply.eta && <small>Всё ожидается к {row.supply.eta}</small>}</section>}
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
