import { useEffect, useRef, useState } from "react";
import {
  fulfillmentApi,
  productionApi,
  type FulfillmentRequest,
  type Principal,
  type ProductionAbsence,
  type ProductionBox,
  type ProductionMaterialSource,
  type ProductionDefect,
  type ProductionInstrument,
  type ProductionLaunchable,
  type ProductionAssignPlan,
  type ProductionQcRejection,
  type ProductionOperation,
  type ProductionOperator,
  type ProductionOutput,
  type ProductionScheduleVariant,
  type ProductionShift,
  type ProductionSkill,
  type ProductionTask,
  type ProductionWorkSession,
} from "./api";
import { Gantt, type GanttBar, type GanttRow } from "./Gantt";
import { MasterBoard } from "./MasterBoard";

export function ProductionPage({ principal, mine = false }: { principal: Principal; mine?: boolean }) {
  const [tasks, setTasks] = useState<ProductionTask[]>([]);
  const [operators, setOperators] = useState<ProductionOperator[]>([]);
  const [viewerSite, setViewerSite] = useState("");
  const [execution, setExecution] = useState<{ session: ProductionWorkSession | null; boxes: ProductionBox[] }>({ session: null, boxes: [] });
  const [boxes, setBoxes] = useState<ProductionBox[]>([]);
  const [defects, setDefects] = useState<ProductionDefect[]>([]);
  const [instruments, setInstruments] = useState<ProductionInstrument[]>([]);
  const [qcRejections, setQcRejections] = useState<ProductionQcRejection[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState("");
  const [showLaunch, setShowLaunch] = useState(false);
  const [showCalendar, setShowCalendar] = useState(false);
  const [showSkills, setShowSkills] = useState(false);
  const [holidays, setHolidays] = useState<string[]>([]);
  const [skills, setSkills] = useState<ProductionSkill[]>([]);
  const [absences, setAbsences] = useState<ProductionAbsence[]>([]);
  const [shift, setShift] = useState<ProductionShift | null>(null);
  const [selectedTaskId, setSelectedTaskId] = useState("");
  const [selectedOperationId, setSelectedOperationId] = useState("");
  const [week, setWeek] = useState(0);
  const canEdit = principal.permissions.production === "edit" && principal.roles.some((role) => ["master", "admin"].includes(role));
  const canQuality = principal.permissions.production === "edit" && principal.roles.some((role) => ["qc", "admin"].includes(role));
  const canOverview = principal.roles.some((role) => ["master", "prodhead", "management", "qc", "admin"].includes(role));
  const selectedTask = tasks.find((task) => task.id === selectedTaskId) ?? null;

  async function reload() {
    setError("");
    try {
      const [taskResult, operatorResult, executionResult, defectResult, instrumentResult, qcRejectionResult] = await Promise.all([
        mine ? productionApi.mine() : productionApi.list(),
        mine
          ? Promise.resolve({ operators: [{ id: principal.user.id, name: principal.user.name, site: "" }] })
          : canOverview ? productionApi.operators() : Promise.resolve({ operators: [] }),
        mine
          ? productionApi.execution()
          : canOverview ? productionApi.boxes() : Promise.resolve({ boxes: [] }),
        !mine && canOverview ? productionApi.defects() : Promise.resolve({ defects: [] }),
        !mine && canOverview ? productionApi.instruments() : Promise.resolve({ instruments: [] }),
        !mine && canOverview ? productionApi.qcRejections() : Promise.resolve({ rejections: [] }),
      ]);
      setTasks(taskResult.tasks);
      setOperators(operatorResult.operators);
      setViewerSite((operatorResult as { viewer_site?: string }).viewer_site ?? "");
      if (mine) setExecution(executionResult as { session: ProductionWorkSession | null; boxes: ProductionBox[] });
      else setBoxes(executionResult.boxes);
      setDefects(defectResult.defects);
      setInstruments(instrumentResult.instruments);
      setQcRejections(qcRejectionResult.rejections);
      if (!mine && canOverview) {
        // Праздники — для подсветки Ганта, компетенции — для метки допуска в карточке назначения (prodSkillTag_).
        const [calendarResult, skillResult] = await Promise.all([
          productionApi.calendar().catch(() => ({ holidays: [] as string[], absences: [] as ProductionAbsence[], shift: null as ProductionShift | null })),
          productionApi.skills().catch(() => ({ skills: [] as ProductionSkill[] })),
        ]);
        setHolidays(calendarResult.holidays);
        setAbsences(calendarResult.absences ?? []);
        setShift(calendarResult.shift ?? null);
        setSkills(skillResult.skills);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось загрузить производственные задания");
    } finally {
      setLoaded(true);
    }
  }

  useEffect(() => { void reload(); }, [mine]);

  async function mutate(key: string, operation: () => Promise<unknown>) {
    if (busy) return;
    setBusy(key);
    setError("");
    setNotice("");
    try {
      const result = await operation();
      if (result && typeof result === "object" && "warnings" in result && Array.isArray(result.warnings) && result.warnings.length) {
        setNotice(result.warnings.join(" "));
      }
      await reload();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Операция с заданием не выполнена");
    } finally {
      setBusy("");
    }
  }

  return <section className="page production-page">
    <header className="page-head">
      <h1>{mine ? "Мои задания" : "Ресурсное планирование"}</h1>
      <span className="page-count">{tasks.length}</span>
      {canEdit && !mine && <div className="page-actions"><button className="secondary" onClick={() => setShowCalendar((value) => !value)}>Календарь цеха</button><button className="secondary" onClick={() => setShowSkills((value) => !value)}>Компетенции</button><button className="primary" onClick={() => setShowLaunch((value) => !value)}>+ Запустить задание</button></div>}
    </header>
    <p className="page-sub">{mine ? "Берите в работу только подтверждённые коробки предыдущего этапа. Выработка и расход сохраняются без перезаписи." : "Маршрут копируется из техкарты при запуске. Автоплан учитывает смену, отсутствия, компетенции и передаточную партию."}</p>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {notice && <div className="success-banner">{notice}</div>}
    {showCalendar && canEdit && <CalendarForm operators={operators} close={() => setShowCalendar(false)} />}
    {showSkills && canEdit && <SkillsForm operators={operators} close={() => { setShowSkills(false); void reload(); }} />}
    {showLaunch && canEdit && <LaunchForm tasks={tasks} busy={Boolean(busy)} mutate={mutate} close={() => setShowLaunch(false)} />}
    {!mine && canOverview && SHOW_TRACEABILITY && <TraceabilitySearch />}
    {mine && <OperatorExecution execution={execution} tasks={tasks} busy={Boolean(busy)} mutate={mutate} />}
    {!mine && canEdit && <BoxReview boxes={boxes} operators={operators} busy={Boolean(busy)} mutate={mutate} />}
    {!mine && canEdit && <DefectReview defects={defects} operators={operators} busy={Boolean(busy)} mutate={mutate} />}
    {!mine && canEdit && <QcRejectionReview rejections={qcRejections} busy={Boolean(busy)} mutate={mutate} />}
    {!mine && canQuality && <QualityPanel boxes={boxes} tasks={tasks} instruments={instruments} busy={Boolean(busy)} mutate={mutate} />}
    {!mine && canQuality && <InstrumentRegistry instruments={instruments} busy={Boolean(busy)} mutate={mutate} />}
    {mine && <MyQueue tasks={tasks} session={execution.session} busy={busy} mutate={mutate} />}
    {!mine && selectedTask && <TaskDetail
      key={`${selectedTask.id}:${selectedTask.version}`}
      task={selectedTask}
      initialOperationId={selectedOperationId}
      tasks={tasks}
      operators={ownSite(operators, viewerSite, selectedTask)}
      skills={skills}
      holidays={holidays}
      week={week}
      setWeek={setWeek}
      canEdit={canEdit}
      busy={busy}
      mutate={mutate}
      back={() => { setSelectedTaskId(""); setSelectedOperationId(""); }}
    />}
    {!mine && !selectedTask && loaded && canOverview && <MasterBoard
      tasks={tasks} operators={operators} site={viewerSite} skills={skills} absences={absences}
      shift={shift} holidays={holidays}
      canEdit={canEdit} busy={busy} mutate={mutate}
      openTask={(taskId, operationId) => { setSelectedTaskId(taskId); setSelectedOperationId(operationId ?? ""); }}
    />}
    {mine && loaded && !tasks.length && <div className="empty">Назначенных операций пока нет.</div>}
  </section>;
}

function CalendarForm({ operators, close }: { operators: ProductionOperator[]; close: () => void }) {
  const [holidays, setHolidays] = useState<string[]>([]);
  const [absences, setAbsences] = useState<ProductionAbsence[]>([]);
  const [holiday, setHoliday] = useState("");
  const [operatorId, setOperatorId] = useState(operators[0]?.id ?? "");
  const [kind, setKind] = useState("Отпуск");
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const [shift, setShift] = useState<ProductionShift>({
    weekdays: [0, 1, 2, 3, 4], start: "09:00", finish: "17:30",
    breaks: [["13:00", "13:30"]], effective_hours: "8",
  });

  useEffect(() => {
    productionApi.calendar()
      .then((result) => { setHolidays(result.holidays); setAbsences(result.absences); if (result.shift) setShift(result.shift); })
      .catch((reason) => setError(reason instanceof Error ? reason.message : "Календарь не загружен"))
      .finally(() => setBusy(false));
  }, []);

  async function save() {
    setBusy(true);
    setError("");
    try {
      await productionApi.saveCalendar(holidays, absences, shift);
      close();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Календарь не сохранён");
    } finally {
      setBusy(false);
    }
  }

  function addAbsence() {
    if (!operatorId || !fromDate) return;
    setAbsences((current) => [...current, {
      operator_id: operatorId, from_date: fromDate, to_date: toDate || fromDate, kind,
    }]);
    setFromDate("");
    setToDate("");
  }

  return <div className="panel production-calendar">
    <div className="manual-stock-head"><h2>Календарь цеха</h2><button className="link-button" onClick={close}>Закрыть</button></div>
    {error && <div className="error-banner" role="alert">{error}</div>}
    <div className="shift-editor">
      <div><strong>Смена</strong><small>Эффективные часы снимаются с начала смены после вычета перерывов.</small></div>
      <label>Начало<input aria-label="Начало смены" type="time" value={shift.start} onChange={(event) => setShift({ ...shift, start: event.target.value })} /></label>
      <label>Конец<input aria-label="Конец смены" type="time" value={shift.finish} onChange={(event) => setShift({ ...shift, finish: event.target.value })} /></label>
      <label>Эффективно, ч<input aria-label="Эффективные часы" type="number" min="0.25" max="24" step="0.25" value={shift.effective_hours} onChange={(event) => setShift({ ...shift, effective_hours: event.target.value })} /></label>
      <label>Перерыв с<input aria-label="Перерыв с" type="time" value={shift.breaks[0]?.[0] ?? ""} onChange={(event) => setShift({ ...shift, breaks: event.target.value || shift.breaks[0]?.[1] ? [[event.target.value, shift.breaks[0]?.[1] ?? ""]] : [] })} /></label>
      <label>Перерыв до<input aria-label="Перерыв до" type="time" value={shift.breaks[0]?.[1] ?? ""} onChange={(event) => setShift({ ...shift, breaks: shift.breaks[0]?.[0] || event.target.value ? [[shift.breaks[0]?.[0] ?? "", event.target.value]] : [] })} /></label>
      <div className="weekday-picker">{["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"].map((label, day) => <label key={label}><input type="checkbox" checked={shift.weekdays.includes(day)} onChange={(event) => setShift({ ...shift, weekdays: event.target.checked ? [...shift.weekdays, day].sort() : shift.weekdays.filter((value) => value !== day) })} />{label}</label>)}</div>
    </div>
    <div className="calendar-add"><label>Нерабочий день<input aria-label="Нерабочий день" type="date" value={holiday} onChange={(event) => setHoliday(event.target.value)} /></label><button className="secondary small" disabled={!holiday} onClick={() => { setHolidays((current) => current.includes(holiday) ? current : [...current, holiday].sort()); setHoliday(""); }}>Добавить праздник</button></div>
    <div className="calendar-chips">{holidays.map((day) => <button key={day} className="calendar-chip" onClick={() => setHolidays((current) => current.filter((value) => value !== day))}>{day} ×</button>)}</div>
    <div className="calendar-add absence-add">
      <label>Оператор<select value={operatorId} onChange={(event) => setOperatorId(event.target.value)}>{operators.map((operator) => <option key={operator.id} value={operator.id}>{operator.name}</option>)}</select></label>
      <label>Вид<select value={kind} onChange={(event) => setKind(event.target.value)}>{["Отпуск", "Больничный", "Отгул", "Командировка", "Учёба"].map((value) => <option key={value}>{value}</option>)}</select></label>
      <label>С<input type="date" value={fromDate} onChange={(event) => setFromDate(event.target.value)} /></label>
      <label>По<input type="date" value={toDate} onChange={(event) => setToDate(event.target.value)} /></label>
      <button className="secondary small" disabled={!operatorId || !fromDate || Boolean(toDate && toDate < fromDate)} onClick={addAbsence}>Добавить отсутствие</button>
    </div>
    <div className="calendar-absences">{absences.map((absence, index) => <div key={absence.id ?? `${absence.operator_id}:${absence.from_date}:${index}`}><span>{operators.find((operator) => operator.id === absence.operator_id)?.name ?? absence.operator_id} · {absence.kind} · {absence.from_date}—{absence.to_date}</span><button className="link-button danger-link" onClick={() => setAbsences((current) => current.filter((_, position) => position !== index))}>Удалить</button></div>)}</div>
    <div className="form-actions"><button className="primary" disabled={busy} onClick={() => void save()}>Сохранить календарь</button></div>
  </div>;
}

// Компетенции (prodSkillsModal_): коды операций выданных техкарт; «—» — строки нет, допуск по коду не проверяется.
function SkillsForm({ operators, close }: {
  operators: ProductionOperator[];
  close: () => void;
}) {
  const [operatorId, setOperatorId] = useState(operators[0]?.id ?? "");
  const [skills, setSkills] = useState<ProductionSkill[]>([]);
  const [codes, setCodes] = useState<Array<{ code: string; name: string }>>([]);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");

  useEffect(() => {
    productionApi.skills()
      .then((result) => { setSkills(result.skills); setCodes(result.operation_codes ?? []); })
      .catch((reason) => setError(reason instanceof Error ? reason.message : "Компетенции не загружены"))
      .finally(() => setBusy(false));
  }, []);

  function row(code: string) {
    return skills.find((item) => item.operator_id === operatorId && item.operation_code === code);
  }

  function update(code: string, patch: Partial<ProductionSkill> | null) {
    setSaved("");
    setSkills((current) => {
      const other = current.filter((item) => !(item.operator_id === operatorId && item.operation_code === code));
      if (patch === null) return other;
      const base = row(code) ?? { operator_id: operatorId, operation_code: code, allowed: false, stars: 0, coefficient: "1" };
      return [...other, { ...base, ...patch, operator_id: operatorId, operation_code: code }];
    });
  }

  async function save() {
    setBusy(true); setError(""); setSaved("");
    try {
      await productionApi.saveSkills(operatorId, codes.flatMap(({ code }) => {
        const item = row(code);
        return item ? [{ operation_code: code, allowed: item.allowed, stars: item.stars, coefficient: String(item.coefficient).replace(",", ".") }] : [];
      }));
      setSaved("Сохранено.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Компетенции не сохранены");
    } finally { setBusy(false); }
  }

  return <div className="panel skills-editor">
    <div className="manual-stock-head"><div><h2>Компетенции</h2><small>Допуск, звёзды (мастерство 0–5) и коэффициент скорости по операциям выданных техкарт. «—» — не задано. Пока на операцию не заведён ни один оператор, допуск к ней не проверяется. Коэффициент 1 — норма, 1,2 — на 20% быстрее. Без допуска назначить можно — план предупредит.</small></div><button className="link-button" onClick={close}>Закрыть</button></div>
    {error && <div className="error-banner" role="alert">{error}</div>}
    <label>Оператор<select aria-label="Оператор компетенций" value={operatorId} onChange={(event) => { setOperatorId(event.target.value); setSaved(""); }}>{operators.map((operator) => <option key={operator.id} value={operator.id}>{operator.name}{operator.site ? ` · ${operator.site}` : ""}</option>)}</select></label>
    <div className="skills-grid">
      {codes.map(({ code, name }) => { const item = row(code); return <div key={code} className="skill-row">
        <div><strong>{code}</strong><small>{name}</small></div>
        <label>Допуск<select aria-label={`Допуск ${code}`} value={item ? (item.allowed ? "yes" : "no") : ""} onChange={(event) => update(code, event.target.value ? { allowed: event.target.value === "yes" } : null)}><option value="">—</option><option value="yes">допущен</option><option value="no">не допущен</option></select></label>
        <label>Звёзды<select aria-label={`Звёзды ${code}`} disabled={!item} value={item?.stars ?? 0} onChange={(event) => update(code, { stars: Number(event.target.value) })}>{[0, 1, 2, 3, 4, 5].map((value) => <option key={value} value={value}>{value}</option>)}</select></label>
        <label>Коэф.<input aria-label={`Коэффициент ${code}`} disabled={!item} type="number" min="0.1" max="100" step="0.1" value={item?.coefficient ?? "1"} onChange={(event) => update(code, { coefficient: event.target.value })} /></label>
      </div>; })}
    </div>
    {!codes.length && !busy && <div className="empty">Нет выданных техкарт — операций для матрицы пока нет.</div>}
    <div className="form-actions">{saved && <span>{saved}</span>}<button className="primary" disabled={busy || !operatorId || !codes.length} onClick={() => void save()}>Сохранить</button></div>
  </div>;
}

// Запуск задания (prodLaunchModal_): сборки с выданной техкартой по проектам, поиск, «тираж», «уже запущено».
function LaunchForm({ tasks, busy, mutate, close }: {
  tasks: ProductionTask[];
  busy: boolean;
  mutate: (key: string, operation: () => Promise<unknown>) => Promise<void>;
  close: () => void;
}) {
  const [items, setItems] = useState<ProductionLaunchable[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [picked, setPicked] = useState<ProductionLaunchable | null>(null);
  const [quantity, setQuantity] = useState("");
  const [deadline, setDeadline] = useState("");
  const [fulfillmentRequestId, setFulfillmentRequestId] = useState("");
  const [issuedRequests, setIssuedRequests] = useState<FulfillmentRequest[]>([]);
  const key = useRef(crypto.randomUUID());

  useEffect(() => {
    productionApi.launchable()
      .then((result) => setItems(result.items))
      .catch((reason) => setError(reason instanceof Error ? reason.message : "Сборки не загружены"))
      .finally(() => setLoading(false));
    fulfillmentApi.list()
      .then((result) => setIssuedRequests(result.requests.filter((request) => request.status === "КБ Выдана")))
      .catch(() => setIssuedRequests([]));
  }, []);

  const doneOf = (item: ProductionLaunchable) => tasks.filter((task) => task.status !== "Отменено"
    && task.project.toLowerCase() === item.project.toLowerCase() && task.item.toUpperCase() === item.index.toUpperCase());
  const needle = query.trim().toLowerCase();
  const visible = items.filter((item) => !needle || `${item.project} ${item.index} ${item.customer} ${item.name}`.toLowerCase().includes(needle));
  const projects = Array.from(new Set(visible.map((item) => item.project)));

  function pick(item: ProductionLaunchable) {
    setPicked(item);
    setQuantity(item.run);
    setDeadline(item.deadline ?? "");
  }

  async function submit() {
    if (!picked) return;
    await mutate("launch", async () => {
      const result = await productionApi.launch(key.current, {
        techcard_id: picked.techcard_id, quantity: Number(quantity), deadline: deadline || null,
        fulfillment_request_id: fulfillmentRequestId || null,
      });
      key.current = crypto.randomUUID();
      close();
      return result;
    });
  }

  return <div className="panel production-launch">
    <div className="manual-stock-head"><div><h2>Запустить задание</h2><small>Выбери сборку — маршрут задания скопируется из её выданной техкарты.</small></div><button className="link-button" onClick={close}>Отмена</button></div>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {!picked && <>
      {items.length > 0 && <input className="pl-search" aria-label="Поиск сборки" placeholder="Поиск: проект, КБ, заказчик, наименование" value={query} onChange={(event) => setQuery(event.target.value)} autoFocus />}
      {projects.map((project) => <div className="pl-proj" key={project}>
        <div className="pl-ph"><b>{project}</b>{visible.find((item) => item.project === project)?.customer ? ` · ${visible.find((item) => item.project === project)?.customer}` : ""}</div>
        <div className="pl-grid">{visible.filter((item) => item.project === project).map((item) => {
          const launched = doneOf(item).reduce((sum, task) => sum + task.quantity, 0) || item.launched;
          return <button key={item.techcard_id} className="pl-card" onClick={() => pick(item)}>
            <span className="pl-kb">{item.index}</span>
            <span className="pl-name">{item.name || "—"}</span>
            <span className="pl-meta">тираж {item.run || "—"}{launched ? <> · <b>запущено {launched}</b></> : null}</span>
          </button>;
        })}</div>
      </div>)}
      {!loading && !items.length && <div className="empty">Нет сборок с выданной техкартой — сначала собери техкарту в карточке проекта.</div>}
      {items.length > 0 && !visible.length && <div className="empty">Ничего не найдено.</div>}
    </>}
    {picked && <>
      <button className="secondary small" onClick={() => setPicked(null)}>← все сборки</button>
      <div className="pl-sel"><span className="pl-kb">{picked.index}</span><div><b>{picked.project}</b>{picked.customer ? ` · ${picked.customer}` : ""}<div>{picked.name}</div><small>тираж {picked.run || "—"} · этапов {picked.operations}</small></div></div>
      {doneOf(picked).length > 0 && <div className="pl-warn">Уже запущено: {doneOf(picked).map((task) => `${task.number} × ${task.quantity}`).join(", ")}</div>}
      <div className="production-launch-grid">
        <label>Кол-во<input aria-label="Кол-во" inputMode="numeric" value={quantity} onChange={(event) => setQuantity(event.target.value)} autoFocus /></label>
        <label>Дедлайн<input aria-label="Дедлайн" type="date" value={deadline} onChange={(event) => setDeadline(event.target.value)} /></label>
        <label>Выданный комплект<select aria-label="Выданный комплект" value={fulfillmentRequestId} onChange={(event) => setFulfillmentRequestId(event.target.value)}><option value="">Не связан</option>{issuedRequests.map((request) => <option key={request.id} value={request.id}>{request.number} · {request.purpose}</option>)}</select></label>
      </div>
      <div className="form-actions"><button className="primary" disabled={busy || !/^\d+$/.test(quantity.trim()) || Number(quantity) <= 0} onClick={() => void submit()}>Запустить</button></div>
    </>}
  </div>;
}

// Мастер площадки ставит только своих (назначенных раньше оставляем, чтобы были видны); без площадки — всех.
function ownSite(operators: ProductionOperator[], site: string, task: ProductionTask | null) {
  if (!site) return operators;
  const assigned = new Set(task?.operations.flatMap((operation) => operation.operator_ids) ?? []);
  return operators.filter((operator) => operator.site === site || assigned.has(operator.id));
}

// tcKitItemId_: изделие задания в SUPPLYMAP — проект + индекс КБ в верхнем регистре.
function itemId(task: { project: string; item: string }) {
  return task.item.toUpperCase().startsWith(task.project.toUpperCase()) ? task.item : `${task.project}${task.item.toUpperCase()}`;
}

function parseDate(value: string | null | undefined) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function factText(task: ProductionTask, operation: ProductionOperation) {
  const fact = operation.fact;
  const processed = Number(fact?.processed ?? 0);
  const main = processed ? `сдано ${fact?.good} из ${task.quantity}${Number(fact?.defect) ? `, брак ${fact?.defect}` : ""}` : "факта нет";
  const ok = Number(operation.wip?.ok ?? 0);
  const closed = Number(operation.wip?.closed ?? 0);
  return main + (ok || closed ? ` · в коробках ${ok}${closed ? ` + ${closed} ждут подтверждения` : ""}` : "");
}

// Задание (prodRenderTask_): Гант операций с фактом, авторасчёт, отмена; клик по операции — карточка назначения.
function TaskDetail({ task, initialOperationId = "", tasks, operators, skills, holidays, week, setWeek, canEdit, busy, mutate, back }: {
  task: ProductionTask;
  initialOperationId?: string;
  tasks: ProductionTask[];
  operators: ProductionOperator[];
  skills: ProductionSkill[];
  holidays: string[];
  week: number;
  setWeek: (value: number) => void;
  canEdit: boolean;
  busy: string;
  mutate: (key: string, operation: () => Promise<unknown>) => Promise<void>;
  back: () => void;
}) {
  const [operationId, setOperationId] = useState(initialOperationId);
  const cancelled = task.status === "Отменено";
  const edit = canEdit && !cancelled;
  const nameOf = (id: string) => operators.find((operator) => operator.id === id)?.name ?? id;
  const operation = task.operations.find((item) => item.id === operationId) ?? null;
  const rows: GanttRow[] = task.operations.map((item) => ({
    key: item.id,
    onClick: edit ? () => setOperationId(item.id) : undefined,
    label: <><b>{item.position}</b> {item.stage}{item.code ? <span className="pg-q"> {item.code}</span> : null}</>,
    sublabel: <>{item.operator_ids.length ? item.operator_ids.map(nameOf).join(", ") : "не назначено"} · норма {hours(item.norm_seconds)}<div>{factText(task, item)}{item.state ? ` · ${item.state}` : ""}</div></>,
    bars: [{
      start: item.planned_start, finish: item.planned_finish,
      title: `${formatDate(item.planned_start)} – ${formatDate(item.planned_finish)}`,
      text: item.operator_ids.map((id) => nameOf(id).split(" ")[0]).join(", "),
    }],
  }));
  function toggleCancel() {
    const question = cancelled ? `Вернуть задание ${task.number} в работу?` : `Отменить задание ${task.number}? Операции и назначения останутся в истории.`;
    if (!window.confirm(question)) return;
    void mutate(task.id, () => productionApi.cancel(task.id, task.version, !cancelled));
  }
  return <div className="panel production-card">
    <button className="secondary small" onClick={back}>← Экран мастера</button>
    <div className="procurement-title">
      <div><strong>{task.number} · {itemId(task)}{task.name ? ` · ${task.name}` : ""}</strong><small>{task.project} · кол-во {task.quantity}{task.deadline ? ` · дедлайн ${formatDay(task.deadline)}` : ""} · запущено {formatDate(task.created_at)}{cancelled ? " · отменено" : ""}{edit ? " · нажми на операцию, чтобы назначить операторов" : ""}</small></div>
      <span className="status">{task.status}</span>
    </div>
    {canEdit && <div className="page-actions">
      {edit && <SchedulePlanner task={task} operators={operators} busy={Boolean(busy)} mutate={mutate} />}
      <button className="secondary small" disabled={Boolean(busy)} onClick={toggleCancel}>{cancelled ? "Вернуть в работу" : "Отменить задание"}</button>
    </div>}
    <Gantt rows={rows} labelHead="Операция" week={week} onWeekChange={setWeek} holidays={holidays} />
    {operation && edit && <AssignCard
      task={task} operation={operation} tasks={tasks} operators={operators} skills={skills}
      holidays={holidays} week={week} setWeek={setWeek} busy={Boolean(busy)} mutate={mutate}
      close={() => setOperationId("")}
    />}
  </div>;
}

// Метка допуска у оператора (prodSkillTag_): кода нет в матрице — ничего; нет допуска — красным; иначе звёзды и коэффициент.
function SkillTag({ skills, code, operatorId }: { skills: ProductionSkill[]; code: string; operatorId: string }) {
  const byCode = skills.filter((item) => item.operation_code === code);
  if (!byCode.length) return null;
  const skill = byCode.find((item) => item.operator_id === operatorId);
  if (!skill || !skill.allowed) return <small className="danger-text"> нет допуска</small>;
  const coefficient = Number(skill.coefficient);
  return <small> {"★".repeat(skill.stars)}{coefficient !== 1 ? ` ×${String(coefficient).replace(".", ",")}` : ""}</small>;
}

function toLocalInput(value: string | null) {
  const date = parseDate(value);
  if (!date) return "";
  const pad = (number: number) => String(number).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

// Карточка операции (prodAssignCard_): строки Ганта — операторы с их загрузкой, превью плана считает сервер (dry).
function AssignCard({ task, operation, tasks, operators, skills, holidays, week, setWeek, busy, mutate, close }: {
  task: ProductionTask;
  operation: ProductionOperation;
  tasks: ProductionTask[];
  operators: ProductionOperator[];
  skills: ProductionSkill[];
  holidays: string[];
  week: number;
  setWeek: (value: number) => void;
  busy: boolean;
  mutate: (key: string, operation: () => Promise<unknown>) => Promise<void>;
  close: () => void;
}) {
  const [selected, setSelected] = useState<string[]>(operation.operator_ids);
  const [start, setStart] = useState(toLocalInput(operation.planned_start));
  const [preview, setPreview] = useState<ProductionAssignPlan | null>(null);
  const [planError, setPlanError] = useState("");
  const sequence = useRef(0);

  useEffect(() => {
    const current = ++sequence.current;
    setPlanError("");
    if (!selected.length) { setPreview(null); return; }
    const timer = window.setTimeout(() => {
      productionApi.assign(task.id, operation.id, task.version, selected, start || null, true)
        .then((result) => { if (current === sequence.current) setPreview(result.plan); })
        .catch((reason) => { if (current === sequence.current) { setPreview(null); setPlanError(reason instanceof Error ? reason.message : "План не рассчитан"); } });
    }, 350);
    return () => window.clearTimeout(timer);
  }, [selected.join(","), start]);

  const live = tasks.filter((item) => item.status !== "Отменено");
  const rows: GanttRow[] = [];
  let site: string | null = null;
  for (const operator of operators) {
    if (operator.site !== site) {
      site = operator.site;
      rows.push({ key: `site:${site}`, label: <b>{site || "Без площадки"}</b>, bars: [] });
    }
    const bars: GanttBar[] = live.flatMap((item) => item.operations
      .filter((other) => !(item.id === task.id && other.id === operation.id) && other.operator_ids.includes(operator.id))
      .map((other) => ({
        start: other.planned_start, finish: other.planned_finish, tone: "busy",
        text: `${item.number} · ${other.position}`,
        title: `${item.number} · этап ${other.position} ${other.stage}: ${formatDate(other.planned_start)} – ${formatDate(other.planned_finish)}`,
      })));
    if (selected.includes(operator.id) && preview?.start) {
      bars.push({ start: preview.start, finish: preview.finish, tone: "new", text: "этот этап", title: `${formatDate(preview.start)} – ${formatDate(preview.finish)}` });
    }
    rows.push({
      key: operator.id,
      label: <label className="pa-who"><input type="checkbox" aria-label={`Назначить ${operator.name}`} checked={selected.includes(operator.id)} onChange={(event) => setSelected((current) => event.target.checked ? [...current, operator.id] : current.filter((id) => id !== operator.id))} />{operator.name}<SkillTag skills={skills} code={operation.code} operatorId={operator.id} /></label>,
      bars,
    });
  }

  return <div className="panel assign-card">
    <div className="manual-stock-head"><div><h2>{task.number} · этап {operation.position} · {operation.stage}</h2><small>{itemId(task)} × {task.quantity} · норма {hours(operation.norm_seconds)} на одного{operation.description ? ` · ${operation.description}` : ""}</small></div><button className="link-button" onClick={close}>Отмена</button></div>
    <div className="pa-ctl"><label>Плановый старт<input aria-label={`Старт этапа ${operation.position}`} type="datetime-local" value={start} onChange={(event) => setStart(event.target.value)} /></label><small>пусто — первое свободное время выбранных операторов</small></div>
    <Gantt rows={rows} labelHead="Оператор" week={week} onWeekChange={setWeek} holidays={holidays} />
    <div className={`pa-plan${planError ? " err" : ""}`} role="status">
      {planError || (!selected.length
        ? `Операторы не выбраны${operation.operator_ids.length ? " — «Назначить» снимет назначение." : "."}`
        : preview ? <><b>План:</b> {formatDate(preview.start)} → {formatDate(preview.finish)}{preview.warnings.length > 0 && <ul>{preview.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>}</> : "Считаю план…")}
    </div>
    <div className="form-actions"><button className="primary" disabled={busy} onClick={() => void mutate(operation.id, async () => {
      const result = await productionApi.assign(task.id, operation.id, task.version, selected, start || null);
      close();
      return result;
    })}>Назначить</button></div>
  </div>;
}

// Авторасчёт (prodAutoPlanModal_): бригада до 3, варианты по передаточной партии, по умолчанию — самый ранний финиш.
function SchedulePlanner({ task, operators, busy, mutate }: {
  task: ProductionTask;
  operators: ProductionOperator[];
  busy: boolean;
  mutate: (key: string, operation: () => Promise<unknown>) => Promise<void>;
}) {
  const [show, setShow] = useState(false);
  const [replan, setReplan] = useState(false);
  const [maxCrew, setMaxCrew] = useState(1);
  const [variants, setVariants] = useState<ProductionScheduleVariant[]>([]);
  const [pick, setPick] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const names = new Map(operators.map((operator) => [operator.id, operator.name]));
  const stages = new Map(task.operations.map((operation) => [operation.position, operation.stage]));

  useEffect(() => {
    if (!show) return;
    let active = true;
    setLoading(true); setError(""); setVariants([]);
    productionApi.schedule(task.id, task.version, replan, maxCrew)
      .then((result) => {
        if (!active) return;
        let best = 0;
        result.variants.forEach((variant, index) => { if (new Date(variant.finish) < new Date(result.variants[best].finish)) best = index; });
        setVariants(result.variants); setPick(best);
      })
      .catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : "Автоплан не рассчитан"); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [show, replan, maxCrew]);

  const variant = variants[pick];
  return <div className="schedule-planner">
    <button className="secondary small" onClick={() => setShow((value) => !value)}>Авторасчёт плана</button>
    {show && <div className="schedule-panel">
      <small>{itemId(task)} × {task.quantity}{task.deadline ? ` · дедлайн ${formatDay(task.deadline)}` : ""} · этапы идут цепочкой: следующий начинает, как только предыдущий отдал передаточную партию. Операторы — из «Компетенции», по допуску и коэффициенту.</small>
      <div className="schedule-controls">
        <label>Операторов на операцию, до<select aria-label={`Максимум операторов ${task.number}`} value={maxCrew} onChange={(event) => setMaxCrew(Number(event.target.value))}>{[1, 2, 3].map((value) => <option key={value} value={value}>{value}</option>)}</select></label>
        <label><input type="checkbox" checked={replan} onChange={(event) => setReplan(event.target.checked)} />пересчитать и уже назначенные этапы</label>
      </div>
      {loading && <small>Считаю варианты…</small>}
      {error && <div className="error-banner" role="alert">{error}</div>}
      {variants.length > 0 && <table className="ap-t"><thead><tr><th></th><th>Передаточная партия</th><th>Первые изделия</th><th>Всё готово</th><th>Простой бригады</th></tr></thead><tbody>
        {variants.map((item, index) => <tr key={item.transfer_batch} className={index === pick ? "on" : ""} onClick={() => setPick(index)}>
          <td>{index === pick ? "●" : "○"}</td>
          <td>{item.transfer_batch >= task.quantity ? `целиком, ${item.transfer_batch} шт` : `${item.transfer_batch} шт`}</td>
          <td>{formatDate(item.first_ready)}</td><td>{formatDate(item.finish)}</td>
          <td>{(item.idle_seconds / 3600).toFixed(1).replace(".", ",")} ч</td>
        </tr>)}
      </tbody></table>}
      {variant && <table className="ap-t"><thead><tr><th>Этап</th><th>Операторы</th><th>Старт</th><th>Финиш</th></tr></thead><tbody>
        {variant.operations.map((operation) => <tr key={operation.operation_id}>
          <td><b>{operation.position}</b> {stages.get(operation.position) ?? ""}</td>
          <td>{operation.operator_ids.map((id) => names.get(id) ?? id).join(", ")}{operation.automatic ? "" : <span className="ap-man"> назначено раньше</span>}</td>
          <td>{formatDate(operation.start)}</td><td>{formatDate(operation.finish)}</td>
        </tr>)}
      </tbody></table>}
      {variant?.warnings.length ? <ul>{variant.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul> : null}
      <button className="primary small" disabled={busy || !variant} onClick={() => void mutate(`schedule:${task.id}`, () => productionApi.applySchedule(task.id, task.version, replan, maxCrew, variant.transfer_batch))}>Применить</button>
    </div>}
  </div>;
}

// Очередь оператора (myOpsRender_): операции по дням плана — «Сегодня / Завтра / Начато ранее», факт «сдано X из N»,
// «вместе с», «план истёк»; «Начать» нет у выполненных и недоступно, пока есть начатая работа.
function MyQueue({ tasks, session, busy, mutate }: {
  tasks: ProductionTask[];
  session: ProductionWorkSession | null;
  busy: string;
  mutate: (key: string, operation: () => Promise<unknown>) => Promise<void>;
}) {
  const [starting, setStarting] = useState("");
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const weekdays = ["воскресенье", "понедельник", "вторник", "среда", "четверг", "пятница", "суббота"];
  const dayTitle = (date: Date | null) => {
    if (!date) return "Без плана";
    const day = new Date(date.getFullYear(), date.getMonth(), date.getDate());
    const diff = Math.round((day.getTime() - today.getTime()) / 86_400_000);
    const base = `${String(date.getDate()).padStart(2, "0")}.${String(date.getMonth() + 1).padStart(2, "0")}, ${weekdays[date.getDay()]}`;
    return diff === 0 ? `Сегодня · ${base}` : diff === 1 ? `Завтра · ${base}` : diff < 0 ? `Начато ранее · ${base}` : base;
  };
  let last: string | null = null;
  return <div className="my-queue">{tasks.map((task) => {
    const operation = task.operations[0];
    if (!operation) return null;
    const startAt = parseDate(operation.planned_start);
    const finishAt = parseDate(operation.planned_finish);
    const title = dayTitle(startAt);
    const header = title !== last ? <div className="mo-day">{title}</div> : null;
    last = title;
    const running = Boolean(session && session.task_id === task.id && session.operation_id === operation.id);
    const done = operation.state === "выполнена";
    const late = Boolean(finishAt && finishAt < now && !done);
    const key = `${task.id}:${operation.id}`;
    const processed = Number(operation.fact?.processed ?? 0);
    return <div key={key}>
      {header}
      <div className={`mo-card${late ? " late" : ""}${done ? " done" : ""}${running ? " run" : ""}`}>
        <div className="mo-h"><b>{operation.stage || `Этап ${operation.position}`}</b>{operation.code && <span className="pg-q"> {operation.code}</span>}<span className="mo-time">{startAt ? `${formatDate(operation.planned_start)} → ${formatDate(operation.planned_finish)}` : "время не назначено"}</span></div>
        <div className="mo-s">{task.number} · этап {operation.position} · <b>{itemId(task)}</b> × {task.quantity}{task.name ? ` · ${task.name}` : ""}</div>
        {operation.description && <div className="mo-d">{operation.description}</div>}
        <div className="mo-s">норма {hours(operation.norm_seconds)}{task.deadline ? ` · дедлайн ${formatDay(task.deadline)}` : ""}{operation.mates?.length ? ` · вместе с: ${operation.mates.join(", ")}` : ""}{late ? <> · <b>план истёк</b></> : null}</div>
        <div className="mo-fact">{processed ? <>Сдано <b>{operation.fact?.good}</b> из {task.quantity}{Number(operation.fact?.defect) ? ` · брак ${operation.fact?.defect}` : ""} · {operation.state}</> : "Ещё не сдавали"}</div>
        {!running && !done && <div className="mo-act">
          <button className="primary small" disabled={Boolean(session) || Boolean(busy)} title={session ? "Сначала сдай начатую работу" : undefined} onClick={() => setStarting(starting === key ? "" : key)}>▶ Начать</button>
        </div>}
        {!running && !done && !session && starting === key && <StartWorkForm task={task} operation={operation} busy={Boolean(busy)} mutate={mutate} />}
      </div>
    </div>;
  })}</div>;
}

function OperatorExecution({ execution, tasks, busy, mutate }: {
  execution: { session: ProductionWorkSession | null; boxes: ProductionBox[] };
  tasks: ProductionTask[];
  busy: boolean;
  mutate: (key: string, operation: () => Promise<unknown>) => Promise<void>;
}) {
  const [good, setGood] = useState("");
  const [defect, setDefect] = useState("0");
  const [defectCharacter, setDefectCharacter] = useState("");
  const [defectDescription, setDefectDescription] = useState("");
  const [correcting, setCorrecting] = useState("");
  const [shown, setShown] = useState<{ number: string; fixed: boolean } | null>(null);
  const session = execution.session;
  const task = session ? tasks.find((item) => item.id === session.task_id) : null;
  const operation = task?.operations.find((item) => item.id === session?.operation_id);
  return <>
    {session && <div className="panel execution-current">
      <div className="manual-stock-head"><div><h2>Идёт работа</h2><small>{task?.number ?? session.task_id} · этап {operation?.position ?? "—"} {operation?.stage ?? ""} · с {formatDate(session.started_at)}{session.pause_seconds ? ` · пауз ${Math.round(session.pause_seconds / 60)} мин` : ""}{session.status === "Пауза" ? " · на паузе" : ""}</small></div><span className="status">{session.status}</span></div>
      <div className="execution-actions">
        {session.status === "В работе" && <button className="secondary" disabled={busy} onClick={() => void mutate(session.id, () => productionApi.workAction(session.id, "pause"))}>Пауза</button>}
        {session.status === "Пауза" && <button className="secondary" disabled={busy} onClick={() => void mutate(session.id, () => productionApi.workAction(session.id, "resume"))}>Продолжить</button>}
        <label>Годные<input aria-label="Годные изделия" type="number" min="0" step="any" value={good} onChange={(event) => setGood(event.target.value)} /></label>
        <label>Брак<input aria-label="Брак" type="number" min="0" step="any" value={defect} onChange={(event) => setDefect(event.target.value)} /></label>
        {Number(defect) > 0 && <><label>Характер брака<input aria-label="Характер брака" value={defectCharacter} onChange={(event) => setDefectCharacter(event.target.value)} /></label><label>Описание брака<textarea aria-label="Описание брака" value={defectDescription} onChange={(event) => setDefectDescription(event.target.value)} /></label></>}
        <button className="primary" disabled={busy || !good || (Number(defect) > 0 && (!defectCharacter.trim() || !defectDescription.trim()))} onClick={() => void mutate(session.id, async () => {
          const result = await productionApi.submitWork(session.id, good, defect || "0", defectCharacter, defectDescription);
          setShown({ number: result.box.number ?? "", fixed: false });
          setGood(""); setDefect("0"); setDefectCharacter(""); setDefectDescription("");
          return result;
        })}>Сдать</button>
        <button className="link-button danger-link" disabled={busy} onClick={() => { if (window.confirm("Отменить начало? Время не запишется.")) void mutate(session.id, () => productionApi.workAction(session.id, "cancel")); }}>Отменить начало</button>
      </div>
    </div>}
    {execution.boxes.length > 0 && <div className="panel execution-boxes">
      <h2>Мои коробки</h2>
      <div className="box-grid">{execution.boxes.map((box) => <div className="production-box" key={`${box.id}:${box.version}`}>
        <strong>{box.number ?? "Номер после закрытия"}</strong>
        <span>{box.item} · этап {box.operation_position}</span>
        <span>{box.quantity} шт · доступно {box.available}</span>
        <span className="status">{box.status}</span>
        {box.return_reason && <small className="danger-text">{box.return_reason}</small>}
        {box.status === "Возвращена" && <button className="secondary small" disabled={busy} onClick={() => setCorrecting(correcting === box.id ? "" : box.id)}>Исправить</button>}
        {box.status === "Возвращена" && correcting === box.id && <BoxCorrection box={box} busy={busy} mutate={mutate} onDone={(number) => setShown({ number, fixed: true })} />}
      </div>)}</div>
    </div>}
    {shown && <BoxNumberDialog number={shown.number} fixed={shown.fixed} close={() => setShown(null)} />}
  </>;
}

// prodBoxShow_: номер новой коробки крупно — его переписывают на коробку от руки.
function BoxNumberDialog({ number, fixed, close }: { number: string; fixed: boolean; close: () => void }) {
  const title = fixed ? "Коробка исправлена" : "Напиши на коробке";
  return <div className="panel kit-modal box-dialog" role="dialog" aria-label={title}>
    <h3>{title}</h3>
    <p>{fixed ? "Коробка снова ждёт подтверждения мастера." : "Коробка закрыта и ждёт подтверждения мастера. Без номера её не возьмут дальше."}</p>
    <div className="box-no">{number}</div>
    <button className="primary" onClick={close}>Записал</button>
  </div>;
}

// Correction replaces one output of a returned box (SUPPLYMAP `prodWorkFix_`); the box goes back to the master.
function BoxCorrection({ box, busy, mutate, onDone }: {
  box: ProductionBox;
  busy: boolean;
  mutate: (key: string, operation: () => Promise<unknown>) => Promise<void>;
  onDone?: (number: string) => void;
}) {
  const [outputs, setOutputs] = useState<ProductionOutput[]>([]);
  const [outputId, setOutputId] = useState("");
  const [good, setGood] = useState("");
  const [defect, setDefect] = useState("0");
  const [defectCharacter, setDefectCharacter] = useState("");
  const [defectDescription, setDefectDescription] = useState("");
  const [error, setError] = useState("");
  function pick(output: ProductionOutput | undefined) {
    setOutputId(output?.id ?? "");
    setGood(output?.good_quantity ?? "");
    setDefect(output?.defect_quantity ?? "0");
  }
  useEffect(() => {
    productionApi.boxOutputs(box.id)
      .then((result) => { setOutputs(result.outputs); pick(result.outputs[0]); })
      .catch((reason) => setError(reason instanceof Error ? reason.message : "Записи коробки не загружены"));
  }, [box.id]);
  const selected = outputs.find((output) => output.id === outputId);
  const newDefect = Boolean(selected) && Number(defect) > 0 && Number(defect) !== Number(selected?.defect_quantity);
  const invalid = !selected || good === "" || Number(good) + Number(defect || 0) <= 0
    || (newDefect && (!defectCharacter.trim() || !defectDescription.trim()));
  return <div className="start-work">
    {error && <small className="danger-text">{error}</small>}
    {outputs.length > 1 && <label>Запись выработки<select aria-label="Запись выработки" value={outputId} onChange={(event) => pick(outputs.find((output) => output.id === event.target.value))}>
      {outputs.map((output) => <option key={output.id} value={output.id}>{new Date(output.finished_at).toLocaleString("ru-RU")} · {output.good_quantity} годных · {output.defect_quantity} брак</option>)}
    </select></label>}
    <label>Годные<input aria-label={`Годные в коробке ${box.number}`} type="number" min="0" step="any" value={good} onChange={(event) => setGood(event.target.value)} /></label>
    <label>Брак<input aria-label={`Брак в коробке ${box.number}`} type="number" min="0" step="any" value={defect} onChange={(event) => setDefect(event.target.value)} /></label>
    {newDefect && <><label>Характер брака<input aria-label={`Характер брака в коробке ${box.number}`} value={defectCharacter} onChange={(event) => setDefectCharacter(event.target.value)} /></label><label>Описание брака<textarea aria-label={`Описание брака в коробке ${box.number}`} value={defectDescription} onChange={(event) => setDefectDescription(event.target.value)} /></label></>}
    <button className="primary small" disabled={busy || invalid} onClick={() => void mutate(box.id, async () => {
      const result = await productionApi.correctBox(box.id, box.version, {
        output_id: outputId, good_quantity: good, defect_quantity: defect || "0",
        defect_character: defectCharacter, defect_description: defectDescription,
      });
      onDone?.(result.box.number ?? box.number ?? "");
      return result;
    })}>Записать исправление</button>
  </div>;
}

// prodWorkStart_: первая операция начинается сразу (складские партии — по желанию), дальше оператор вводит номера
// входных коробок с предыдущего этапа, подтверждённых мастером; штуки из них берутся при сдаче.
function StartWorkForm({ task, operation, busy, mutate }: {
  task: ProductionTask;
  operation: ProductionOperation;
  busy: boolean;
  mutate: (key: string, operation: () => Promise<unknown>) => Promise<void>;
}) {
  const [boxes, setBoxes] = useState<ProductionBox[]>([]);
  const [materials, setMaterials] = useState<ProductionMaterialSource[]>([]);
  const [quantities, setQuantities] = useState<Record<string, string>>({});
  const [numbers, setNumbers] = useState("");
  const [error, setError] = useState("");
  const first = operation.position === 1;
  useEffect(() => {
    productionApi.inputs(task.id, operation.id)
      .then((result) => { setBoxes(result.boxes); setMaterials(result.materials); })
      .catch((reason) => setError(reason instanceof Error ? reason.message : "Коробки не загружены"));
  }, [task.id, operation.id, operation.position]);
  const inputs = numbers.split(/[,;\n]+/).map((item) => item.trim()).filter(Boolean);
  const materialInputs = materials.flatMap((material) => {
    const quantity = quantities[material.fulfillment_line_id]?.trim();
    return quantity && Number(quantity) > 0 ? [{ fulfillment_line_id: material.fulfillment_line_id, quantity }] : [];
  });
  return <div className="start-work">
    {error && <small className="danger-text">{error}</small>}
    {first && materials.map((material) => <label key={material.fulfillment_line_id}>{material.grn}{material.package ? ` · ${material.package}` : ""} · {material.article || material.name} · доступно {material.available} {material.unit}<input aria-label={`Количество из партии ${material.grn} ${material.package}`} type="number" min="0" max={material.available} step="any" value={quantities[material.fulfillment_line_id] ?? ""} onChange={(event) => setQuantities((current) => ({ ...current, [material.fulfillment_line_id]: event.target.value }))} /></label>)}
    {!first && <>
      <small>Введи номера коробок, из которых берёшь детали (с этапа {operation.position - 1}, подтверждённые мастером). Несколько — через запятую или с новой строки.</small>
      <label>Номера коробок<textarea aria-label="Номера коробок" rows={3} placeholder="000001-8" value={numbers} onChange={(event) => setNumbers(event.target.value)} /></label>
      {boxes.length > 0 && <small>Подтверждённые: {boxes.map((box) => `${box.number} (${box.available} шт)`).join(", ")}</small>}
    </>}
    <button className="primary small" disabled={busy || (!first && !inputs.length) || materialInputs.some((item) => Number(item.quantity) > Number(materials.find((material) => material.fulfillment_line_id === item.fulfillment_line_id)?.available ?? 0))} onClick={() => void mutate(operation.id, () => productionApi.startWork(task.id, operation.id, inputs, materialInputs))}>Начать</button>
  </div>;
}

function BoxReview({ boxes, operators, busy, mutate }: {
  boxes: ProductionBox[];
  operators: ProductionOperator[];
  busy: boolean;
  mutate: (key: string, operation: () => Promise<unknown>) => Promise<void>;
}) {
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const pending = boxes.filter((box) => box.status === "Закрыта оператором");
  if (!pending.length) return null;
  return <div className="panel box-review"><div className="manual-stock-head"><h2>Коробки на подтверждение</h2><span className="page-count">{pending.length}</span></div>
    <div className="box-grid">{pending.map((box) => <div className="production-box" key={`${box.id}:${box.version}`}>
      <strong>{box.number}</strong>
      <span>{box.item} · этап {box.operation_position}</span>
      <span>{box.quantity} шт · {operators.find((operator) => operator.id === box.operator_id)?.name ?? box.operator_id}</span>
      <button className="primary small" disabled={busy} onClick={() => void mutate(box.id, () => productionApi.boxAction(box.id, "confirm", box.version))}>Подтвердить</button>
      <label>Причина возврата<textarea value={reasons[box.id] ?? ""} onChange={(event) => setReasons((current) => ({ ...current, [box.id]: event.target.value }))} /></label>
      <button className="secondary small" disabled={busy || !(reasons[box.id] ?? "").trim()} onClick={() => void mutate(box.id, () => productionApi.returnBox(box.id, box.version, reasons[box.id]))}>Вернуть оператору</button>
    </div>)}</div>
  </div>;
}

function DefectReview({ defects, operators, busy, mutate }: {
  defects: ProductionDefect[];
  operators: ProductionOperator[];
  busy: boolean;
  mutate: (key: string, operation: () => Promise<unknown>) => Promise<void>;
}) {
  const [dispositions, setDispositions] = useState<Record<string, "Доработка" | "Списание" | "Использовать как есть">>({});
  const [acts, setActs] = useState<Record<string, string>>({});
  // A defect record replaced by a box correction stays on record but is decided through its replacement.
  const pending = defects.filter((defect) => defect.status === "Ожидает решения" && !defect.replaced_by_output_id);
  if (!pending.length) return null;
  return <div className="panel box-review defect-review"><div className="manual-stock-head"><h2>Брак цеха</h2><span className="page-count">{pending.length}</span></div>
    <div className="box-grid">{pending.map((defect) => {
      const disposition = dispositions[defect.id] ?? "Доработка";
      return <div className="production-box defect-box" key={`${defect.id}:${defect.version}`}>
        <strong>{defect.number}</strong><span>{defect.quantity} шт · {defect.character}</span><span>{defect.description}</span><small>{operators.find((operator) => operator.id === defect.operator_id)?.name ?? defect.operator_id}</small>
        <label>Решение<select value={disposition} onChange={(event) => setDispositions((current) => ({ ...current, [defect.id]: event.target.value as typeof disposition }))}><option>Доработка</option><option>Списание</option><option>Использовать как есть</option></select></label>
        <label>Акт / основание<input aria-label={`Акт для ${defect.number}`} value={acts[defect.id] ?? ""} onChange={(event) => setActs((current) => ({ ...current, [defect.id]: event.target.value }))} /></label>
        <button className="primary small" disabled={busy || !(acts[defect.id] ?? "").trim()} onClick={() => void mutate(defect.id, () => productionApi.decideDefect(defect.id, defect.version, disposition, acts[defect.id]))}>Принять решение</button>
      </div>;
    })}</div>
  </div>;
}

function QcRejectionReview({ rejections, busy, mutate }: {
  rejections: ProductionQcRejection[];
  busy: boolean;
  mutate: (key: string, operation: () => Promise<unknown>) => Promise<void>;
}) {
  const [dispositions, setDispositions] = useState<Record<string, "Доработка" | "Списание" | "Использовать как есть">>({});
  const [acts, setActs] = useState<Record<string, string>>({});
  const pending = rejections.filter((item) => item.status === "Ожидает решения");
  if (!pending.length) return null;
  return <div className="panel box-review defect-review"><div className="manual-stock-head"><h2>Брак ОТК</h2><span className="page-count">{pending.length}</span></div>
    <div className="box-grid">{pending.map((item) => {
      const disposition = dispositions[item.id] ?? "Доработка";
      return <div className="production-box defect-box" key={item.id}>
        <strong>{item.defect_number}</strong><span>{item.rejected} шт · {item.defect_character}</span><span>{item.defect_description}</span><small>{item.box_number ?? item.box_id} · {item.item}</small>
        <label>Решение<select value={disposition} onChange={(event) => setDispositions((current) => ({ ...current, [item.id]: event.target.value as typeof disposition }))}><option>Доработка</option><option>Списание</option><option>Использовать как есть</option></select></label>
        <label>Акт / основание<input aria-label={`Акт ОТК для ${item.defect_number}`} value={acts[item.id] ?? ""} onChange={(event) => setActs((current) => ({ ...current, [item.id]: event.target.value }))} /></label>
        <button className="primary small" disabled={busy || !(acts[item.id] ?? "").trim()} onClick={() => void mutate(`qc-decision:${item.id}`, () => productionApi.decideQcRejection(item.id, disposition, acts[item.id]))}>Принять решение</button>
      </div>;
    })}</div>
  </div>;
}

function QualityPanel({ boxes, tasks, instruments, busy, mutate }: {
  boxes: ProductionBox[];
  tasks: ProductionTask[];
  instruments: ProductionInstrument[];
  busy: boolean;
  mutate: (key: string, operation: () => Promise<unknown>) => Promise<void>;
}) {
  const [drafts, setDrafts] = useState<Record<string, { accepted: string; rejected: string; character: string; description: string; instrumentIds: string[] }>>({});
  const ready = boxes.filter((box) => {
    const task = tasks.find((item) => item.id === box.task_id);
    const last = task ? Math.max(...task.operations.map((operation) => operation.position)) : -1;
    return box.status === "Подтверждена" && !box.qc_inspected && box.operation_position === last;
  });
  if (!ready.length) return null;
  const update = (boxId: string, patch: Partial<{ accepted: string; rejected: string; character: string; description: string; instrumentIds: string[] }>) => setDrafts((current) => {
    const previous = current[boxId] ?? { accepted: "", rejected: "0", character: "", description: "", instrumentIds: [] };
    return { ...current, [boxId]: { ...previous, ...patch } };
  });
  return <div className="panel box-review qc-review"><div className="manual-stock-head"><h2>Очередь ОТК</h2><span className="page-count">{ready.length}</span></div>
    <div className="box-grid">{ready.map((box) => {
      const draft = drafts[box.id] ?? { accepted: box.available, rejected: "0", character: "", description: "", instrumentIds: [] };
      const totalMatches = Number(draft.accepted || 0) + Number(draft.rejected || 0) === Number(box.available);
      return <div className="production-box" key={`${box.id}:${box.version}`}>
        <strong>{box.number}</strong><span>{box.item} · {box.available} шт</span>
        <label>Годные ОТК<input aria-label={`Годные ОТК ${box.number}`} type="number" min="0" step="1" value={draft.accepted} onChange={(event) => update(box.id, { accepted: event.target.value })} /></label>
        <label>Брак ОТК<input aria-label={`Брак ОТК ${box.number}`} type="number" min="0" step="1" value={draft.rejected} onChange={(event) => update(box.id, { rejected: event.target.value })} /></label>
        {Number(draft.rejected) > 0 && <><label>Характер<input value={draft.character} onChange={(event) => update(box.id, { character: event.target.value })} /></label><label>Описание<textarea value={draft.description} onChange={(event) => update(box.id, { description: event.target.value })} /></label></>}
        <label>Средства измерения<select multiple aria-label={`Средства измерения ${box.number}`} value={draft.instrumentIds} onChange={(event) => update(box.id, { instrumentIds: Array.from(event.target.selectedOptions, (option) => option.value) })}>{instruments.filter((item) => item.status === "Действует").map((item) => <option key={item.id} value={item.id}>{item.inventory_number} · {item.name} · поверка до {item.verification_due.slice(0, 10)}</option>)}</select></label>
        {!totalMatches && <small className="danger-text">Сумма должна быть {box.available}.</small>}
        <button className="primary small" disabled={busy || !totalMatches || (Number(draft.rejected) > 0 && (!draft.character.trim() || !draft.description.trim()))} onClick={() => void mutate(`qc:${box.id}`, () => productionApi.inspectBox(box.id, {
          version: box.version, accepted: Number(draft.accepted), rejected: Number(draft.rejected),
          defect_character: draft.character, defect_description: draft.description,
          instrument_ids: draft.instrumentIds,
        }))}>Принять ОТК</button>
      </div>;
    })}</div>
  </div>;
}

function InstrumentRegistry({ instruments, busy, mutate }: {
  instruments: ProductionInstrument[];
  busy: boolean;
  mutate: (key: string, operation: () => Promise<unknown>) => Promise<void>;
}) {
  const [show, setShow] = useState(false);
  const [draft, setDraft] = useState({ inventory_number: "", name: "", instrument_type: "", verification_number: "", verified_at: "", verification_due: "" });
  return <div className="panel instrument-registry">
    <div className="manual-stock-head"><h2>Средства измерения</h2><button className="secondary small" onClick={() => setShow((value) => !value)}>+ Добавить СИ</button></div>
    {show && <div className="production-launch-grid">{(["inventory_number", "name", "instrument_type", "verification_number"] as const).map((field) => <label key={field}>{({ inventory_number: "Инв. №", name: "Наименование", instrument_type: "Тип", verification_number: "№ поверки" })[field]}<input value={draft[field]} onChange={(event) => setDraft({ ...draft, [field]: event.target.value })} /></label>)}<label>Поверено<input type="datetime-local" value={draft.verified_at} onChange={(event) => setDraft({ ...draft, verified_at: event.target.value })} /></label><label>Поверка до<input type="datetime-local" value={draft.verification_due} onChange={(event) => setDraft({ ...draft, verification_due: event.target.value })} /></label><button className="primary small" disabled={busy || Object.values(draft).some((value) => !value.trim())} onClick={() => void mutate("instrument:new", () => productionApi.registerInstrument(draft))}>Сохранить СИ</button></div>}
    <div className="calendar-absences">{instruments.map((item) => <div key={item.id}><span><strong>{item.inventory_number}</strong> · {item.name} · {item.instrument_type}</span><small>{item.verification_number} · до {item.verification_due.slice(0, 10)} · {item.status}</small></div>)}</div>
  </div>;
}

// Поиск родословной отложен владельцем 29.09: сначала распределение задач из техкарт, прослеживаемость — потом.
// Бэкенд и данные на месте; вернуть блок — поставить true.
const SHOW_TRACEABILITY = false;

function TraceabilitySearch() {
  const [query, setQuery] = useState("");
  const [result, setResult] = useState<Awaited<ReturnType<typeof productionApi.traceability>> | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function search() {
    if (!query.trim()) return;
    setBusy(true); setError("");
    try { setResult(await productionApi.traceability(query.trim())); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Родословная не найдена"); }
    finally { setBusy(false); }
  }
  const labels = new Map(result?.nodes.map((node) => [node.key, node.label]) ?? []);
  return <div className="panel traceability-search">
    <div className="manual-stock-head"><div><h2>Прослеживаемость</h2><small>Серийный номер, коробка, входной ГРН или ГРН готовой продукции</small></div></div>
    <div className="search-row"><input aria-label="Поиск родословной" value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void search(); }} placeholder="Например, 104233-2" /><button className="secondary" disabled={busy || !query.trim()} onClick={() => void search()}>Найти цепочку</button></div>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {result && !result.nodes.length && <div className="empty">Связей по этому номеру не найдено.</div>}
    {result && result.nodes.length > 0 && <div className="genealogy-result">
      <div className="box-grid">{result.nodes.map((node) => <div className="production-box" key={node.key}><strong>{node.label}</strong><span className="status">{({ material: "Материал", box: "Коробка", serial: "Серийный номер", finished_goods: "Готовая продукция" } as Record<string, string>)[node.kind] ?? node.kind}</span><small>{node.details}</small></div>)}</div>
      <div className="genealogy-edges">{result.edges.map((edge, index) => <div key={`${edge.source}:${edge.target}:${index}`}><strong>{labels.get(edge.source) ?? edge.source}</strong><span>→ {edge.relation} · {edge.quantity} →</span><strong>{labels.get(edge.target) ?? edge.target}</strong></div>)}</div>
    </div>}
  </div>;
}

function duration(seconds: number) {
  if (seconds < 60) return `${seconds} сек`;
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.ceil((seconds % 3600) / 60);
  return hours ? `${hours} ч ${minutes} мин` : `${minutes} мин`;
}

function hours(seconds: number) {
  const value = (Number(seconds) || 0) / 3600;
  return `${(value < 10 ? value.toFixed(1) : String(Math.round(value))).replace(".", ",")} ч`;
}

function formatDay(value: string | null) {
  const match = String(value ?? "").match(/^(\d{4})-(\d{2})-(\d{2})/);
  return match ? `${match[3]}.${match[2]}.${match[1]}` : String(value ?? "");
}

function formatDate(value: string | null) {
  if (!value) return "";
  return new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}
