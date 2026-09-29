import { useEffect, useMemo, useState } from "react";
import {
  productionApi,
  productionPlanApi,
  type Principal,
  type ProductionPlanResult,
  type ProductionPlanRow,
} from "./api";
import { matchesWords } from "./DataTable";
import { Gantt } from "./Gantt";

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

export function ProductionPlanPage({ principal, openProjectCard }: {
  principal: Principal;
  /** Opens the project's techcard card (SUPPLYMAP planAct_ 'card' → openKitsetCard); absent without access. */
  openProjectCard?: (project: string) => void;
}) {
  const [data, setData] = useState(emptyPlan);
  const [holidays, setHolidays] = useState<string[]>([]);
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
      setSelectedProject((current) => current && result.rows.some((row) => row.project === current)
        ? current
        : sortPlanRows(result.rows.filter(planActive))[0]?.project ?? "");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Производственный план не загружен");
    } finally {
      setLoaded(true);
    }
  }

  useEffect(() => {
    void reload();
    // Holidays only shade the timeline: without the calendar the plan still works.
    productionApi.calendar().then((result) => setHolidays(result.holidays ?? [])).catch(() => setHolidays([]));
  }, []);

  const current = PLAN_FILTERS.find(([id]) => id === filter) ?? PLAN_FILTERS[0];
  const rows = useMemo(() => {
    // #planQ: № проекта или заказчик — the shared DataTable word search over those two fields.
    return sortPlanRows(data.rows.filter(current[2]).filter((row) => matchesWords([row.project, row.customer], query)));
  }, [data.rows, current, query]);
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
      <div><h1>Производственный план</h1><p className="page-sub">Проекты, заведённые в «Проекты». Этап, комплектующие и план заданий считаются сами; руками — только блокер, комментарий и дедлайн: нажми на проект.</p></div>
      <span className="page-count">{rows.length}</span>
    </header>
    {error && <div className="error-banner" role="alert">{error}</div>}
    <div className="plan-filters" role="group" aria-label="Фильтр проектов">
      {PLAN_FILTERS.map(([id, label, test]) => <button key={id} className={id === current[0] ? "secondary active" : "secondary"} onClick={() => setFilter(id)}>
        {label}<b>{data.rows.filter(test).length}</b>
      </button>)}
      <input aria-label="Поиск проекта" placeholder="№ проекта или заказчик" value={query} onChange={(event) => setQuery(event.target.value)} />
    </div>
    <div className="production-plan-layout">
      <div className="production-plan-gantt">
        <Gantt labelHead="Проект" holidays={holidays}
          empty={loaded ? <div className="empty">Под этот фильтр проектов нет.</div> : null}
          rows={rows.map((row) => ({
            key: row.project,
            dim: row.terminal,
            selected: row.project === selectedProject,
            onClick: () => setSelectedProject(row.project),
            deadline: row.deadline,
            label: <>
              <div className="plan-row-title"><strong>{row.project}</strong><span className="plan-customer">{row.customer}</span></div>
              <div className="plan-tags">
                <span className="plan-tag stage">{row.stage}</span>
                {row.blocker && <span className="plan-tag blocker" title={row.blocker.text}>{row.blocker.type}</span>}
                {daysTag(row)}
              </div>
            </>,
            sublabel: <><div>{kbText(row, data.stages)}</div><div>Компл.: {supplyText(row.supply)}</div></>,
            note: row.last ? `${dayMonth(row.last.occurred_at)} · ${row.last.text}` : undefined,
            bars: row.plan ? [{
              start: row.plan.start, finish: row.plan.finish,
              tone: row.plan.assigned < row.plan.total ? "partial" as const : "default" as const,
              text: `план ${row.plan.tasks} зад.`,
              title: `${moment(row.plan.start)} – ${moment(row.plan.finish)}`,
            }] : [],
          }))} />
      </div>
      {selected && <PlanDetails row={selected} blockers={data.blockers} stages={data.stages} reviewValues={data.review_values} canEdit={canEdit} busy={busy} addEvent={addEvent}
        openProjectCard={openProjectCard && selected.kits.length ? () => openProjectCard(selected.project) : undefined} />}
    </div>
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
