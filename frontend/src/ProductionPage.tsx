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

type RouteDraft = {
  position: number;
  code: string;
  stage: string;
  description: string;
  seconds_per_unit: string;
  setup_seconds: string;
};

const emptyOperation = (position: number): RouteDraft => ({
  position,
  code: "",
  stage: "",
  description: "",
  seconds_per_unit: "0",
  setup_seconds: "0",
});

export function ProductionPage({ principal, mine = false }: { principal: Principal; mine?: boolean }) {
  const [tasks, setTasks] = useState<ProductionTask[]>([]);
  const [operators, setOperators] = useState<ProductionOperator[]>([]);
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
  const canEdit = principal.permissions.production === "edit" && principal.roles.some((role) => ["master", "admin"].includes(role));
  const canQuality = principal.permissions.production === "edit" && principal.roles.some((role) => ["qc", "admin"].includes(role));
  const canOverview = principal.roles.some((role) => ["master", "prodhead", "management", "qc", "admin"].includes(role));

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
      if (mine) setExecution(executionResult as { session: ProductionWorkSession | null; boxes: ProductionBox[] });
      else setBoxes(executionResult.boxes);
      setDefects(defectResult.defects);
      setInstruments(instrumentResult.instruments);
      setQcRejections(qcRejectionResult.rejections);
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
    {showSkills && canEdit && <SkillsForm operators={operators} tasks={tasks} close={() => setShowSkills(false)} />}
    {showLaunch && canEdit && <LaunchForm busy={Boolean(busy)} mutate={mutate} close={() => setShowLaunch(false)} />}
    {!mine && canOverview && <TraceabilitySearch />}
    {mine && <OperatorExecution execution={execution} tasks={tasks} busy={Boolean(busy)} mutate={mutate} />}
    {!mine && canEdit && <BoxReview boxes={boxes} operators={operators} busy={Boolean(busy)} mutate={mutate} />}
    {!mine && canEdit && <DefectReview defects={defects} operators={operators} busy={Boolean(busy)} mutate={mutate} />}
    {!mine && canEdit && <QcRejectionReview rejections={qcRejections} busy={Boolean(busy)} mutate={mutate} />}
    {!mine && canQuality && <QualityPanel boxes={boxes} tasks={tasks} instruments={instruments} busy={Boolean(busy)} mutate={mutate} />}
    {!mine && canQuality && <InstrumentRegistry instruments={instruments} busy={Boolean(busy)} mutate={mutate} />}
    <div className="work-list production-list">
      {tasks.map((task) => <TaskCard
        key={`${task.id}:${task.version}`}
        task={task}
        operators={operators}
        canEdit={canEdit && !mine}
        mine={mine}
        busy={busy}
        mutate={mutate}
      />)}
    </div>
    {loaded && !tasks.length && <div className="empty">{mine ? "Назначенных операций пока нет." : "Производственных заданий пока нет."}</div>}
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

function SkillsForm({ operators, tasks, close }: {
  operators: ProductionOperator[];
  tasks: ProductionTask[];
  close: () => void;
}) {
  const [operatorId, setOperatorId] = useState(operators[0]?.id ?? "");
  const [skills, setSkills] = useState<ProductionSkill[]>([]);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const codes = Array.from(new Map(tasks.flatMap((task) => task.operations)
    .filter((operation) => operation.code)
    .map((operation) => [operation.code, operation.stage])).entries())
    .sort(([left], [right]) => left.localeCompare(right));

  useEffect(() => {
    productionApi.skills()
      .then((result) => setSkills(result.skills))
      .catch((reason) => setError(reason instanceof Error ? reason.message : "Компетенции не загружены"))
      .finally(() => setBusy(false));
  }, []);

  function row(code: string) {
    return skills.find((item) => item.operator_id === operatorId && item.operation_code === code)
      ?? { operator_id: operatorId, operation_code: code, allowed: false, stars: 0, coefficient: "1" };
  }

  function update(code: string, patch: Partial<ProductionSkill>) {
    setSkills((current) => {
      const next = { ...row(code), ...patch, operator_id: operatorId, operation_code: code };
      const other = current.filter((item) => !(item.operator_id === operatorId && item.operation_code === code));
      return [...other, next];
    });
  }

  async function save() {
    setBusy(true); setError("");
    try {
      await productionApi.saveSkills(operatorId, codes.map(([code]) => {
        const item = row(code);
        return { operation_code: code, allowed: item.allowed, stars: item.stars, coefficient: item.coefficient };
      }));
      close();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Компетенции не сохранены");
    } finally { setBusy(false); }
  }

  return <div className="panel skills-editor">
    <div className="manual-stock-head"><div><h2>Компетенции операторов</h2><small>Допуск обязателен для авторасчёта; коэффициент 1 — нормативная скорость.</small></div><button className="link-button" onClick={close}>Закрыть</button></div>
    {error && <div className="error-banner" role="alert">{error}</div>}
    <label>Оператор<select aria-label="Оператор компетенций" value={operatorId} onChange={(event) => setOperatorId(event.target.value)}>{operators.map((operator) => <option key={operator.id} value={operator.id}>{operator.name}</option>)}</select></label>
    <div className="skills-grid">
      {codes.map(([code, stage]) => { const item = row(code); return <div key={code} className="skill-row">
        <div><strong>{code}</strong><small>{stage}</small></div>
        <label><input type="checkbox" checked={item.allowed} onChange={(event) => update(code, { allowed: event.target.checked })} />Допуск</label>
        <label>Звёзды<input aria-label={`Звёзды ${code}`} type="number" min="0" max="5" step="1" value={item.stars} onChange={(event) => update(code, { stars: Number(event.target.value) })} /></label>
        <label>Коэффициент<input aria-label={`Коэффициент ${code}`} type="number" min="0.1" max="100" step="0.1" value={item.coefficient} onChange={(event) => update(code, { coefficient: event.target.value })} /></label>
      </div>; })}
    </div>
    {!codes.length && <div className="empty">Сначала запустите хотя бы одно задание с кодами операций.</div>}
    <div className="form-actions"><button className="primary" disabled={busy || !operatorId || !codes.length} onClick={() => void save()}>Сохранить компетенции</button></div>
  </div>;
}

function LaunchForm({ busy, mutate, close }: {
  busy: boolean;
  mutate: (key: string, operation: () => Promise<unknown>) => Promise<void>;
  close: () => void;
}) {
  const [project, setProject] = useState("");
  const [item, setItem] = useState("");
  const [name, setName] = useState("");
  const [quantity, setQuantity] = useState("1");
  const [deadline, setDeadline] = useState("");
  const [fulfillmentRequestId, setFulfillmentRequestId] = useState("");
  const [issuedRequests, setIssuedRequests] = useState<FulfillmentRequest[]>([]);
  const [operations, setOperations] = useState<RouteDraft[]>([emptyOperation(1)]);
  const key = useRef(crypto.randomUUID());

  useEffect(() => {
    fulfillmentApi.list()
      .then((result) => setIssuedRequests(result.requests.filter((request) => request.status === "КБ Выдана")))
      .catch(() => setIssuedRequests([]));
  }, []);

  function change(index: number, field: keyof RouteDraft, value: string) {
    setOperations((current) => current.map((operation, position) => position === index
      ? { ...operation, [field]: field === "position" ? Number(value) : value }
      : operation));
  }

  async function submit() {
    await mutate("launch", async () => {
      const result = await productionApi.launch(key.current, {
        project, item, name, quantity: Number(quantity), deadline: deadline || null,
        fulfillment_request_id: fulfillmentRequestId || null, operations,
      });
      key.current = crypto.randomUUID();
      close();
      return result;
    });
  }

  return <div className="panel production-launch">
    <div className="production-launch-grid">
      <label>Проект<input value={project} onChange={(event) => setProject(event.target.value)} /></label>
      <label>Изделие<input value={item} onChange={(event) => setItem(event.target.value)} /></label>
      <label>Наименование<input value={name} onChange={(event) => setName(event.target.value)} /></label>
      <label>Количество<input type="number" min="1" step="1" value={quantity} onChange={(event) => setQuantity(event.target.value)} /></label>
      <label>Дедлайн<input type="date" value={deadline} onChange={(event) => setDeadline(event.target.value)} /></label>
      <label>Выданный комплект<select aria-label="Выданный комплект" value={fulfillmentRequestId} onChange={(event) => setFulfillmentRequestId(event.target.value)}><option value="">Не связан</option>{issuedRequests.map((request) => <option key={request.id} value={request.id}>{request.number} · {request.purpose}</option>)}</select></label>
    </div>
    <h3>Снимок маршрута</h3>
    {operations.map((operation, index) => <div className="route-draft" key={index}>
      <label>Этап<input aria-label={`Номер этапа ${index + 1}`} type="number" min="1" value={operation.position} onChange={(event) => change(index, "position", event.target.value)} /></label>
      <label>Код<input aria-label={`Код этапа ${index + 1}`} value={operation.code} onChange={(event) => change(index, "code", event.target.value)} /></label>
      <label>Название<input aria-label={`Название этапа ${index + 1}`} value={operation.stage} onChange={(event) => change(index, "stage", event.target.value)} /></label>
      <label>Описание<input aria-label={`Описание этапа ${index + 1}`} value={operation.description} onChange={(event) => change(index, "description", event.target.value)} /></label>
      <label>Сек/шт<input aria-label={`Секунд на штуку ${index + 1}`} type="number" min="0" step="any" value={operation.seconds_per_unit} onChange={(event) => change(index, "seconds_per_unit", event.target.value)} /></label>
      <label>Подготовка, сек<input aria-label={`Подготовка ${index + 1}`} type="number" min="0" step="any" value={operation.setup_seconds} onChange={(event) => change(index, "setup_seconds", event.target.value)} /></label>
      {operations.length > 1 && <button className="link-button danger-link" onClick={() => setOperations((current) => current.filter((_, position) => position !== index))}>Удалить</button>}
    </div>)}
    <div className="form-actions">
      <button className="secondary" onClick={() => setOperations((current) => [...current, emptyOperation(current.length + 1)])}>+ Этап</button>
      <button className="primary" disabled={busy || !project.trim() || !item.trim() || !operations.every((operation) => operation.stage.trim())} onClick={() => void submit()}>Запустить</button>
    </div>
  </div>;
}

function TaskCard({ task, operators, canEdit, mine, busy, mutate }: {
  task: ProductionTask;
  operators: ProductionOperator[];
  canEdit: boolean;
  mine: boolean;
  busy: string;
  mutate: (key: string, operation: () => Promise<unknown>) => Promise<void>;
}) {
  const cancelled = task.status === "Отменено";
  return <article className={`panel production-card${cancelled ? " cancelled" : ""}`}>
    <div className="procurement-title">
      <div><strong>{task.number} · {task.item} × {task.quantity}</strong><small>{task.project}{task.name ? ` · ${task.name}` : ""}{task.deadline ? ` · дедлайн ${task.deadline}` : ""}</small></div>
      <span className="status">{task.status}</span>
    </div>
    <div className="production-summary">Назначено {task.assigned} из {task.operations.length}{task.planned_start ? ` · ${formatDate(task.planned_start)} — ${formatDate(task.planned_finish)}` : ""}</div>
    {canEdit && !cancelled && <SchedulePlanner task={task} operators={operators} busy={Boolean(busy)} mutate={mutate} />}
    <div className="production-operations">
      {task.operations.map((operation) => <OperationRow
        key={operation.id}
        task={task}
        operation={operation}
        operators={operators}
        canEdit={canEdit && !cancelled}
        mine={mine && !cancelled}
        busy={Boolean(busy)}
        mutate={mutate}
      />)}
    </div>
    {canEdit && <button className="link-button danger-link" disabled={Boolean(busy)} onClick={() => void mutate(task.id, () => productionApi.cancel(task.id, task.version, !cancelled))}>{cancelled ? "Вернуть в работу" : "Отменить задание"}</button>}
  </article>;
}

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
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const names = new Map(operators.map((operator) => [operator.id, operator.name]));

  async function calculate() {
    setLoading(true); setError("");
    try { setVariants((await productionApi.schedule(task.id, task.version, replan, maxCrew)).variants); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Автоплан не рассчитан"); }
    finally { setLoading(false); }
  }

  return <div className="schedule-planner">
    <button className="secondary small" onClick={() => setShow((value) => !value)}>⚙ Авторасчёт плана</button>
    {show && <div className="schedule-panel">
      <div className="schedule-controls">
        <label>Операторов на операцию<input aria-label={`Максимум операторов ${task.number}`} type="number" min="1" max="5" value={maxCrew} onChange={(event) => setMaxCrew(Number(event.target.value))} /></label>
        <label><input type="checkbox" checked={replan} onChange={(event) => setReplan(event.target.checked)} />Пересчитать назначенные этапы</label>
        <button className="primary small" disabled={busy || loading} onClick={() => void calculate()}>{loading ? "Считаю…" : "Показать варианты"}</button>
      </div>
      {error && <div className="error-banner" role="alert">{error}</div>}
      <div className="schedule-variants">{variants.map((variant) => <div className="schedule-variant" key={variant.transfer_batch}>
        <div><strong>Партия {variant.transfer_batch} шт</strong><small>Первые: {formatDate(variant.first_ready)} · всё: {formatDate(variant.finish)} · простой {duration(variant.idle_seconds)}</small></div>
        <div className="schedule-route">{variant.operations.map((operation) => <span key={operation.operation_id}>{operation.position}. {operation.operator_ids.map((id) => names.get(id) ?? id).join(", ")} · {formatDate(operation.start)}—{formatDate(operation.finish)}</span>)}</div>
        {variant.warnings.map((warning) => <small className="danger-text" key={warning}>{warning}</small>)}
        <button className="secondary small" disabled={busy} onClick={() => void mutate(`schedule:${task.id}`, () => productionApi.applySchedule(task.id, task.version, replan, maxCrew, variant.transfer_batch))}>Применить</button>
      </div>)}</div>
    </div>}
  </div>;
}

function OperationRow({ task, operation, operators, canEdit, mine, busy, mutate }: {
  task: ProductionTask;
  operation: ProductionOperation;
  operators: ProductionOperator[];
  canEdit: boolean;
  mine: boolean;
  busy: boolean;
  mutate: (key: string, operation: () => Promise<unknown>) => Promise<void>;
}) {
  const [selected, setSelected] = useState(operation.operator_ids);
  const [start, setStart] = useState(operation.planned_start?.slice(0, 16) ?? "");
  const names = operation.operator_ids.map((id) => operators.find((operator) => operator.id === id)?.name ?? id);
  return <div className="production-operation">
    <div><strong>{operation.position}. {operation.stage}</strong><small>{operation.code}{operation.description ? ` · ${operation.description}` : ""}</small></div>
    <span>Норма {duration(operation.norm_seconds)}</span>
    <span>{names.length ? names.join(", ") : "Не назначено"}</span>
    <span>{operation.planned_start ? `${formatDate(operation.planned_start)} — ${formatDate(operation.planned_finish)}` : "Без плана"}</span>
    {canEdit && <div className="operation-assign">
      <label>Операторы<select multiple aria-label={`Операторы этапа ${operation.position}`} value={selected} onChange={(event) => setSelected(Array.from(event.target.selectedOptions, (option) => option.value))}>{operators.map((operator) => <option value={operator.id} key={operator.id}>{operator.name}{operator.site ? ` · ${operator.site}` : ""}</option>)}</select></label>
      <label>Старт<input aria-label={`Старт этапа ${operation.position}`} type="datetime-local" value={start} onChange={(event) => setStart(event.target.value)} /></label>
      <button className="secondary small" disabled={busy} onClick={() => void mutate(operation.id, () => productionApi.assign(task.id, operation.id, task.version, selected, start || null))}>{selected.length ? "Запланировать" : "Снять назначение"}</button>
    </div>}
    {mine && <StartWorkForm task={task} operation={operation} busy={busy} mutate={mutate} />}
  </div>;
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
  const session = execution.session;
  const task = session ? tasks.find((item) => item.id === session.task_id) : null;
  const operation = task?.operations.find((item) => item.id === session?.operation_id);
  return <>
    {session && <div className="panel execution-current">
      <div className="manual-stock-head"><div><h2>Сейчас в работе</h2><small>{task?.number ?? session.task_id} · {operation?.stage ?? session.operation_id}</small></div><span className="status">{session.status}</span></div>
      <div className="execution-actions">
        {session.status === "В работе" && <button className="secondary" disabled={busy} onClick={() => void mutate(session.id, () => productionApi.workAction(session.id, "pause"))}>Пауза</button>}
        {session.status === "Пауза" && <button className="secondary" disabled={busy} onClick={() => void mutate(session.id, () => productionApi.workAction(session.id, "resume"))}>Продолжить</button>}
        <label>Годные<input aria-label="Годные изделия" type="number" min="0" step="any" value={good} onChange={(event) => setGood(event.target.value)} /></label>
        <label>Брак<input aria-label="Брак" type="number" min="0" step="any" value={defect} onChange={(event) => setDefect(event.target.value)} /></label>
        {Number(defect) > 0 && <><label>Характер брака<input aria-label="Характер брака" value={defectCharacter} onChange={(event) => setDefectCharacter(event.target.value)} /></label><label>Описание брака<textarea aria-label="Описание брака" value={defectDescription} onChange={(event) => setDefectDescription(event.target.value)} /></label></>}
        <button className="primary" disabled={busy || !good || (Number(defect) > 0 && (!defectCharacter.trim() || !defectDescription.trim()))} onClick={() => void mutate(session.id, () => productionApi.submitWork(session.id, good, defect || "0", defectCharacter, defectDescription))}>Записать выработку</button>
        <button className="link-button danger-link" disabled={busy} onClick={() => void mutate(session.id, () => productionApi.workAction(session.id, "cancel"))}>Отменить и снять резервы</button>
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
        {box.status === "Открыта" && <button className="secondary small" disabled={busy} onClick={() => void mutate(box.id, () => productionApi.boxAction(box.id, "close", box.version))}>Закрыть коробку</button>}
        {box.status === "Возвращена" && <>
          <button className="secondary small" disabled={busy} onClick={() => setCorrecting(correcting === box.id ? "" : box.id)}>Исправить</button>
          <button className="link-button" disabled={busy} onClick={() => void mutate(box.id, () => productionApi.boxAction(box.id, "reopen", box.version))}>Доложить</button>
        </>}
        {box.status === "Возвращена" && correcting === box.id && <BoxCorrection box={box} busy={busy} mutate={mutate} />}
      </div>)}</div>
    </div>}
  </>;
}

// Correction replaces one output of a returned box (SUPPLYMAP `prodWorkFix_`); the box goes back to the master.
function BoxCorrection({ box, busy, mutate }: {
  box: ProductionBox;
  busy: boolean;
  mutate: (key: string, operation: () => Promise<unknown>) => Promise<void>;
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
    <button className="primary small" disabled={busy || invalid} onClick={() => void mutate(box.id, () => productionApi.correctBox(box.id, box.version, {
      output_id: outputId, good_quantity: good, defect_quantity: defect || "0",
      defect_character: defectCharacter, defect_description: defectDescription,
    }))}>Записать исправление</button>
  </div>;
}

function StartWorkForm({ task, operation, busy, mutate }: {
  task: ProductionTask;
  operation: ProductionOperation;
  busy: boolean;
  mutate: (key: string, operation: () => Promise<unknown>) => Promise<void>;
}) {
  const [boxes, setBoxes] = useState<ProductionBox[]>([]);
  const [materials, setMaterials] = useState<ProductionMaterialSource[]>([]);
  const [quantities, setQuantities] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  useEffect(() => {
    productionApi.inputs(task.id, operation.id)
      .then((result) => { setBoxes(result.boxes); setMaterials(result.materials); })
      .catch((reason) => setError(reason instanceof Error ? reason.message : "Коробки не загружены"));
  }, [task.id, operation.id, operation.position]);
  const inputs = boxes.flatMap((box) => {
    const quantity = quantities[box.id]?.trim();
    return quantity && Number(quantity) > 0 ? [{ box_id: box.id, quantity }] : [];
  });
  const materialInputs = materials.flatMap((material) => {
    const quantity = quantities[material.fulfillment_line_id]?.trim();
    return quantity && Number(quantity) > 0 ? [{ fulfillment_line_id: material.fulfillment_line_id, quantity }] : [];
  });
  const selectable = operation.position === 1 ? materials : boxes;
  return <div className="start-work">
    {error && <small className="danger-text">{error}</small>}
    {!selectable.length && !error && <small>{operation.position === 1 ? "Нет выданных складских партий. Свяжите задание с выданной заявкой комплектации." : "Нет подтверждённых коробок предыдущего этапа."}</small>}
    {materials.map((material) => <label key={material.fulfillment_line_id}>{material.grn}{material.package ? ` · ${material.package}` : ""} · {material.article || material.name} · доступно {material.available} {material.unit}<input aria-label={`Количество из партии ${material.grn} ${material.package}`} type="number" min="0" max={material.available} step="any" value={quantities[material.fulfillment_line_id] ?? ""} onChange={(event) => setQuantities((current) => ({ ...current, [material.fulfillment_line_id]: event.target.value }))} /></label>)}
    {boxes.map((box) => <label key={box.id}>{box.number} · доступно {box.available}<input aria-label={`Количество из коробки ${box.number}`} type="number" min="0" max={box.available} step="any" value={quantities[box.id] ?? ""} onChange={(event) => setQuantities((current) => ({ ...current, [box.id]: event.target.value }))} /></label>)}
    {selectable.length > 0 && <button className="primary small" disabled={busy || !(inputs.length || materialInputs.length) || inputs.some((item) => Number(item.quantity) > Number(boxes.find((box) => box.id === item.box_id)?.available ?? 0)) || materialInputs.some((item) => Number(item.quantity) > Number(materials.find((material) => material.fulfillment_line_id === item.fulfillment_line_id)?.available ?? 0))} onClick={() => void mutate(operation.id, () => productionApi.startWork(task.id, operation.id, inputs, materialInputs))}>Начать операцию</button>}
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

function formatDate(value: string | null) {
  if (!value) return "";
  return new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}
