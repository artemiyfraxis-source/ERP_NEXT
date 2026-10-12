import { useEffect, useMemo, useState } from "react";
import { AutoTextarea } from "./AutoTextarea";
import {
  kitsetsApi,
  productionApi,
  productionPlanApi,
  type Principal,
  type ProductionAbsence,
  type ProductionOperator,
  type ProductionShift,
  type ProductionPlanResult,
  type ProductionPlanRow,
  type ProductionTask,
} from "./api";
import { StageChangeDialog } from "./StageChangeDialog";
import { canAct } from "./navigation";
import { PeopleLoadGrid } from "./PeopleLoad";
import { EMPTY, formatDate, formatDayMonth, formatMoment } from "./format";
import { PlanLoadSheet, markText, periodWords, type SheetMark } from "./PlanLoadSheet";
import { StatusChip } from "./StatusChip";
import { deadlineTone, statusTone } from "./statusTone";

const emptyPlan: ProductionPlanResult = { blockers: [], stages: [], review_values: [], rows: [] };
const WEEKDAYS = ["вс", "пн", "вт", "ср", "чт", "пт", "сб"];

const shortDate = (value: string | null) => formatDate(value) || EMPTY;
const moment = (value: string | null) => formatMoment(value) || EMPTY;

// planDaysTag_
function daysTag(row: ProductionPlanRow) {
  if (row.days_left === null || !row.deadline) return <StatusChip tone="neutral">без дедлайна</StatusChip>;
  const days = row.days_left;
  const text = days < 0 ? `просрочен ${-days} дн` : days === 0 ? "дедлайн сегодня" : `${days} дн до ${formatDayMonth(row.deadline)}`;
  return <StatusChip tone={deadlineTone(days)} title={`Дедлайн ${shortDate(row.deadline)}`}>{text}</StatusChip>;
}

const projectKey = (project: string) => project.trim().toUpperCase();

/** «Проекты» без открытой карточки: тот же адрес раздела, что у ссылок меню (App слушает popstate). */
function openProjectsList() {
  const url = new URL(window.location.href);
  url.search = "?section=projects";
  window.history.pushState({}, "", url);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

export function ProductionPlanPage({ principal, openProjectCard }: {
  principal: Principal;
  /** Opens the project's techcard card (SUPPLYMAP planAct_ 'card' → openKitsetCard); absent without access. */
  openProjectCard?: (project: string) => void;
}) {
  const [data, setData] = useState(emptyPlan);
  const [holidays, setHolidays] = useState<string[]>([]);
  const [shift, setShift] = useState<ProductionShift | undefined>(undefined);
  const [absences, setAbsences] = useState<ProductionAbsence[]>([]);
  const [tasks, setTasks] = useState<ProductionTask[]>([]);
  const [operators, setOperators] = useState<ProductionOperator[]>([]);
  const [loadError, setLoadError] = useState("");
  const [week, setWeek] = useState(0);
  const [selectedProject, setSelectedProject] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  // Проекты с карточкой в «Проектах»: № открывает карточку только у них (ПП-5); список не пришёл — по наличию КБ.
  const [cards, setCards] = useState<Set<string> | null>(null);
  const canEdit = canAct(principal, "production");
  const hasCard = (row: ProductionPlanRow) => cards ? cards.has(projectKey(row.project)) : row.kits.length > 0;

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
    productionApi.calendar().then((result) => { setHolidays(result.holidays ?? []); setAbsences(result.absences ?? []); setShift(result.shift); }).catch(() => setHolidays([]));
    // Загрузка по людям — из заданий «Ресурсного планирования»; без доступа к ним блок говорит об этом.
    Promise.all([productionApi.list(), productionApi.operators()])
      .then(([taskResult, operatorResult]) => { setTasks(taskResult.tasks ?? []); setOperators(operatorResult.operators ?? []); })
      .catch((reason) => setLoadError(reason instanceof Error ? reason.message : "Задания не загружены"));
    if (openProjectCard) {
      kitsetsApi.list()
        .then((result) => setCards(Array.isArray(result.items) ? new Set(result.items.map((item) => projectKey(item.project))) : null))
        .catch(() => setCards(null));
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const sheetSpan = useMemo(() => {
    // Календарь листа: сегодня −7 … +60 дней; сервер присылает границы, без них считаем сами.
    const today = new Date();
    const iso = (day: Date) => `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, "0")}-${String(day.getDate()).padStart(2, "0")}`;
    const shift = (days: number) => iso(new Date(today.getFullYear(), today.getMonth(), today.getDate() + days));
    return { today: data.today ?? shift(0), from: data.calendar_from ?? shift(-7), to: data.calendar_to ?? shift(60) };
  }, [data.today, data.calendar_from, data.calendar_to]);
  const activeCount = data.rows.filter((row) => (row.group ?? (row.terminal ? "closed" : "active")) !== "closed" && !row.terminal && row.stage !== "Отгружено").length;
  const selected = data.rows.find((row) => row.project === selectedProject) ?? null;

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

  /** Ручной этап — после подтверждения в окне смены этапа; ошибка уходит в само окно (409, причина назад). */
  async function applyStage(project: string, kb: string, value: string, reason: string, previewToken: string) {
    await productionPlanApi.addEvent({ project, kind: "stage", blocker: "", text: "", deadline: null, item_index: kb, value, reason, preview_token: previewToken });
    await reload();
  }

  async function addMark(mark: SheetMark) {
    await addEvent({ project: mark.project, kind: "mark", blocker: "", text: mark.text ?? "", deadline: null, field: mark.field, value: mark.value ?? "" });
  }

  function openRow(row: ProductionPlanRow) {
    // № проекта открывает его карточку (владелец 29.09: панель снизу неудобна); проект без карточки — журнал сбоку
    // со строкой «карточки нет» вместо ложного «Карточка уже удалена».
    if (openProjectCard && hasCard(row)) openProjectCard(row.project);
    else setSelectedProject(row.project);
  }

  return <section className="page production-plan-page">
    <header className="page-head">
      <h1>Производственный план</h1>
      {/* Как в «Проектах»: счётчик только когда есть что считать — пустой план не показывает «0». */}
      {activeCount > 0 && <span className="page-count" title="Проектов в плане, без отгруженных и закрытых">{activeCount}</span>}
    </header>
    {error && <div className="error-banner" role="alert">{error}</div>}

    <section className="panel plan-projects zg-panel" aria-label="Загрузка цеха">
      <div className="plan-section-head"><h2>Загрузка цеха</h2>
        <span className="zg-span" title={`Период листа ${shortDate(sheetSpan.from)} – ${shortDate(sheetSpan.to)}`}><b>{periodWords(sheetSpan.from, sheetSpan.to)}</b> · сегодня {WEEKDAYS[new Date(`${sheetSpan.today}T00:00:00`).getDay()]} {formatDayMonth(sheetSpan.today)}</span></div>
      {!loaded ? <div className="empty">Загрузка…</div>
        : <PlanLoadSheet rows={data.rows} from={sheetSpan.from} to={sheetSpan.to} today={sheetSpan.today} holidays={holidays} stages={data.stages}
          suppliedValues={data.supplied_values ?? ["НЕТ", "ДА", "Ч/Т"]}
          arrivalValues={data.arrival_values ?? ["Склад", "Ждем", "???"]} canEdit={canEdit} busy={busy}
          onStatusChanged={() => void reload()} onMark={addMark}
          onDeadline={(project, deadline) => addEvent({ project, kind: "deadline", blocker: "", text: "", deadline })}
          onOpenProject={openRow} onOpenJournal={(row) => setSelectedProject(row.project)} hasCard={hasCard} />}
      {selected && <div className="plan-drawer" role="dialog" aria-label={`Проект ${selected.project}`}>
        <div className="plan-drawer-scrim" onClick={() => setSelectedProject("")} />
        <div className="plan-drawer-body">
        <button className="link-button plan-details-close" onClick={() => setSelectedProject("")}>✕ Закрыть</button>
        <PlanDetails row={selected} blockers={data.blockers} stages={data.stages} reviewValues={data.review_values} canEdit={canEdit} busy={busy} addEvent={addEvent} applyStage={applyStage}
          openProjectCard={openProjectCard && hasCard(selected) ? () => openProjectCard(selected.project) : undefined}
          noCard={Boolean(openProjectCard) && !hasCard(selected)} />
        </div>
      </div>}
    </section>

    <section className="panel plan-load" aria-label="Загрузка по людям">
      <div className="plan-section-head"><h2>Загрузка по людям</h2>
        {unassigned.operations > 0 && <span className="plan-tag soon">Без исполнителя: {unassigned.operations} опер. в {unassigned.tasks} зад. — назначить в «Ресурсном планировании»</span>}
      </div>
      {loadError
        ? <div className="empty">Загрузка людей недоступна: {loadError}</div>
        : <PeopleLoadGrid operators={operators} tasks={tasks} absences={absences} holidays={holidays} shift={shift} week={week} onWeekChange={setWeek} />}
    </section>
  </section>;
}

function PlanDetails({ row, blockers, stages, reviewValues, canEdit, busy, addEvent, applyStage, openProjectCard, noCard = false }: {
  row: ProductionPlanRow;
  openProjectCard?: () => void;
  /** У проекта нет карточки в «Проектах» — честная строка и переход к ним. */
  noCard?: boolean;
  blockers: string[];
  stages: string[];
  reviewValues: string[];
  canEdit: boolean;
  busy: boolean;
  addEvent: (payload: Parameters<typeof productionPlanApi.addEvent>[0]) => Promise<void>;
  applyStage: (project: string, kb: string, value: string, reason: string, previewToken: string) => Promise<void>;
}) {
  const [stagePick, setStagePick] = useState<{ kb: string; current: string; value: string } | null>(null);
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
    <div className="plan-detail-head"><div><span className="eyebrow">Проект</span><h2>{row.project}</h2></div><StatusChip status={row.stage} /></div>
    <div className="plan-tags">{row.blocker && <StatusChip status={row.blocker.type} />}{daysTag(row)}</div>
    {openProjectCard && <div className="plan-acts"><button className="secondary" onClick={openProjectCard}>Открыть карточку проекта</button></div>}
    {noCard && <div className="warning-inline plan-no-card" role="note">
      <span>Карточки проекта {row.project} в «Проектах» нет — создайте её из расчёта.</span>
      <a className="secondary small plan-link-button" href="?section=projects" onClick={(event) => {
        if (event.ctrlKey || event.metaKey || event.shiftKey || event.button !== 0) return;
        event.preventDefault();
        openProjectsList();
      }}>Открыть «Проекты»</a>
    </div>}
    {row.blocker && <p className="plan-blocker" data-tone={statusTone(row.blocker.type)}><b>{row.blocker.type}</b> с {moment(row.blocker.since)} · {row.blocker.text} — {row.blocker.who}</p>}
    <section><h3>Проект</h3><p>Заказчик: {row.customer || "—"} · дедлайн {shortDate(row.deadline)} · заведён {shortDate(row.opened_on)}</p></section>
    <div className="plan-kit-stages">{row.kits.map((item) => <KitMarkEditor
      key={item.item_index} project={row.project} item={item} stages={stages}
      reviewValues={reviewValues} canEdit={canEdit} busy={busy} addEvent={addEvent}
      pickStage={(value) => setStagePick({ kb: item.item_index, current: item.status, value })}
    />)}
      {/* A project without KBs takes its stage from a journal stage mark with an empty KB. */}
      {!row.kits.length && canEdit && stages.includes(row.stage) && <div className="plan-kit-mark">
        <strong>Без КБ</strong>
        <label>Этап проекта<select aria-label="Этап проекта" value={row.stage_manual ? row.stage : "авто"} disabled={busy}
          onChange={(event) => setStagePick({ kb: "", current: row.stage, value: event.target.value })}>
          <option value="авто">Авто · {row.stage}</option>{stages.map((stage) => <option key={stage}>{stage}</option>)}
        </select></label>
      </div>}
    </div>
    {stagePick && <StageChangeDialog project={row.project} kb={stagePick.kb} current={stagePick.current} value={stagePick.value}
      loadPreview={() => productionPlanApi.stagePreview(row.project, stagePick.value, stagePick.kb)}
      apply={async (reason, token) => { await applyStage(row.project, stagePick.kb, stagePick.value, reason, token); setStagePick(null); }}
      onClose={() => setStagePick(null)} />}
    {row.supply ? <section><h3>Снабжение</h3><p>Всего {row.supply.total}: склад {row.supply.stock}, в пути {row.supply.transit}, в закупке {row.supply.buying}, в заявках {row.supply.request}.</p>
      <p>Давальческое: {row.supply.customer_supplied ? `${row.supply.customer_supplied_stock} из ${row.supply.customer_supplied} пришло` : "нет"}</p>
      {row.supply.eta && row.supply.stock < row.supply.total && <small>Всё будет к {row.supply.eta}</small>}</section>
      : <section><h3>Снабжение</h3><p>Заявок с этим проектом в НАЗНАЧЕНИИ нет.</p></section>}
    {row.plan && <section><h3>Загрузка цеха</h3><p>{row.plan.tasks} заданий · назначено {row.plan.assigned} из {row.plan.total} этапов.</p>{row.plan.start && <small>{moment(row.plan.start)} — {moment(row.plan.finish)}</small>}</section>}
    {canEdit && <section className="plan-note-form"><h3>Журнал проекта</h3><div className="plan-note-grid">
      <label>Запись<select value={kind} onChange={(event) => setKind(event.target.value as typeof kind)}><option value="note">Комментарий</option><option value="block">Блокер</option>{row.blocker && <option value="unblock">Снять блокер</option>}<option value="deadline">Дедлайн</option></select></label>
      {kind === "block" && <label>Вид<select value={blocker} onChange={(event) => setBlocker(event.target.value)}>{blockers.map((item) => <option key={item}>{item}</option>)}</select></label>}
      {kind === "deadline" && <label>Дата<input type="date" value={deadline} onChange={(event) => setDeadline(event.target.value)} /></label>}
      {kind !== "unblock" && <label className="plan-note-text">Комментарий<AutoTextarea value={text} multiline maxRows={8} onChange={setText} /></label>}
      <button className="primary" disabled={busy || !valid} onClick={() => void submit()}>Записать</button>
    </div></section>}
    <section className="plan-journal"><h3>История</h3>{row.events.map((event) => <article key={event.id}><div><strong>{event.kind}{event.blocker ? ` · ${event.blocker}` : ""}</strong><time>{moment(event.occurred_at)}</time></div><p>{event.field ? markText(event) : event.text || "Без комментария"}</p><small>{event.who}</small></article>)}{!row.events.length && <div className="empty">Записей пока нет.</div>}</section>
  </aside>;
}

function KitMarkEditor({ project, item, stages, reviewValues, canEdit, busy, addEvent, pickStage }: {
  project: string;
  item: ProductionPlanRow["kits"][number];
  stages: string[];
  reviewValues: string[];
  canEdit: boolean;
  busy: boolean;
  addEvent: (payload: Parameters<typeof productionPlanApi.addEvent>[0]) => Promise<void>;
  pickStage: (value: string) => void;
}) {
  const currentReview = item.review?.value ?? "не проверена";
  const [review, setReview] = useState(currentReview);
  const [reviewText, setReviewText] = useState(item.review?.text ?? "");
  useEffect(() => {
    setReview(currentReview);
    setReviewText(item.review?.text ?? "");
  }, [project, item.item_index, currentReview, item.review?.text]);

  function payload(kind: "tcReview", value: string, text = "") {
    return { project, kind, blocker: "", text, deadline: null, item_index: item.item_index, value } as const;
  }

  return <div className="plan-kit-mark">
    <strong>{item.item_index}</strong>
    <span>{item.tc ? "ТК выдана" : "ТК нет"} · {item.kit ? "набрано" : "не набрано"}</span>
    {canEdit ? <>
      <label>Этап<select aria-label={`Этап ${item.item_index}`} value={item.manual ? item.status : "авто"} disabled={busy}
        onChange={(event) => pickStage(event.target.value)}>
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
      {review === "с погрешностями" && <label>Погрешности<AutoTextarea aria-label={`Погрешности ${item.item_index}`} value={reviewText} multiline onChange={setReviewText} /></label>}
      {review === "с погрешностями" && <button className="secondary small" disabled={busy || !reviewText.trim()} onClick={() => void addEvent(payload("tcReview", review, reviewText))}>Сохранить проверку</button>}
    </> : <>
      <span>{item.status}{item.manual ? " · вручную" : ""}</span>
      <span>{currentReview}{item.review?.text ? ` · ${item.review.text}` : ""}</span>
    </>}
  </div>;
}
