import { useEffect, useRef, useState } from "react";
import {
  ApiError,
  fulfillmentApi,
  productionApi,
  productionPlanApi,
  type FulfillmentRequest,
  type Principal,
  type ProductionAbsence,
  type ProductionBox,
  type ProjectTechcard,
  type ProductionMaterialSource,
  type ProductionLaunchable,
  type ProductionAssignPlan,
  type ProductionOperation,
  type ProductionOperator,
  type ProductionOutput,
  type ProductionScheduleVariant,
  type ProductionShift,
  type ProductionSkill,
  type ProductionTask,
  type ProductionWorkSession,
} from "./api";
import { canAct, canSee, hasFullAccess } from "./navigation";
import { EtkSheet } from "./ProjectEtk";
import { Gantt, type GanttBar, type GanttRow } from "./Gantt";
import { formatDay, formatDayMonth, formatMoment } from "./format";
import { MasterBoard, type ProjectDeadlines } from "./MasterBoard";
import { AutoplanPanel } from "./AutoplanPanel";
import { DowntimeControl } from "./DowntimeControl";
import { DowntimeReport } from "./DowntimeReport";
import { JobPanel } from "./JobPlan";
import { AutoTextarea } from "./AutoTextarea";
import { StatusChip } from "./StatusChip";
import { BoxLabelPanel, BoxName, boxMatches, boxTitle } from "./BoxLabel";
import { deadlineChips, feedsText, isFirst, isNetwork, previousStage, requiredNote, requiredOf, taskItemLabel, windowText } from "./productionGraph";

export function ProductionPage({ principal, mine = false }: { principal: Principal; mine?: boolean }) {
  const [tasks, setTasks] = useState<ProductionTask[]>([]);
  const [operators, setOperators] = useState<ProductionOperator[]>([]);
  const [viewerSite, setViewerSite] = useState("");
  const [execution, setExecution] = useState<{ session: ProductionWorkSession | null; boxes: ProductionBox[] }>({ session: null, boxes: [] });
  const [boxes, setBoxes] = useState<ProductionBox[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState("");
  const [showLaunch, setShowLaunch] = useState(false);
  const [showAutoplan, setShowAutoplan] = useState(false);
  const [showCalendar, setShowCalendar] = useState(false);
  const [showSkills, setShowSkills] = useState(false);
  const [showDowntime, setShowDowntime] = useState(false);
  const [holidays, setHolidays] = useState<string[]>([]);
  const [skills, setSkills] = useState<ProductionSkill[]>([]);
  const [absences, setAbsences] = useState<ProductionAbsence[]>([]);
  const [shift, setShift] = useState<ProductionShift | null>(null);
  const [selectedTaskId, setSelectedTaskId] = useState("");
  const [selectedOperationId, setSelectedOperationId] = useState("");
  const [week, setWeek] = useState(0);
  // Сотрудник: планировщик выбирает монтажника и видит его очередь и коробки как он сам. Мастер его площадки и полный
  // доступ работают за него (владелец, 12.10.2026: «мне нужен полный функционал от неё»), остальные — только смотрят.
  const [viewAs, setViewAs] = useState("");
  // За кого сервер разрешил работать (id из ответа на его очередь): пока очередь не загрузилась — только просмотр.
  const [actFor, setActFor] = useState<string | null>(null);
  const [shown, setShown] = useState<{ number: string; fixed: boolean; box?: ProductionBox } | null>(null);
  const [staff, setStaff] = useState<ProductionOperator[]>([]);
  const [staffSite, setStaffSite] = useState("");
  const canEdit = canAct(principal, "production");
  const canOverview = canSee(principal, "production");
  const selectedTask = tasks.find((task) => task.id === selectedTaskId) ?? null;

  async function reload() {
    setError("");
    try {
      const [taskResult, operatorResult, executionResult] = await Promise.all([
        mine ? productionApi.mine(viewAs) : productionApi.list(),
        mine
          ? Promise.resolve({ operators: [{ id: principal.user.id, name: principal.user.name, site: "" }] })
          : canOverview ? productionApi.operators() : Promise.resolve({ operators: [] }),
        mine
          ? productionApi.execution(viewAs)
          : canOverview ? productionApi.boxes() : Promise.resolve({ boxes: [] }),
      ]);
      setTasks(taskResult.tasks);
      setActFor((taskResult as { can_act?: boolean }).can_act ? viewAs : null);
      setOperators(operatorResult.operators);
      setViewerSite((operatorResult as { viewer_site?: string }).viewer_site ?? "");
      if (mine) setExecution(executionResult as { session: ProductionWorkSession | null; boxes: ProductionBox[] });
      else setBoxes(executionResult.boxes);
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

  useEffect(() => { void reload(); }, [mine, viewAs]);
  // Дедлайн проекта — из производственного плана (РП-1): у плитки и задания он подписан отдельно от срока задания.
  const [projectDeadlines, setProjectDeadlines] = useState<ProjectDeadlines>({});
  useEffect(() => {
    if (mine || !canOverview) return;
    productionPlanApi.load()
      .then((result) => setProjectDeadlines(Object.fromEntries(result.rows.map((row) => [row.project.toLowerCase(), { deadline: row.deadline, days_left: row.days_left }]))))
      .catch(() => setProjectDeadlines({}));
  }, [mine, canOverview]);
  useEffect(() => {
    if (!mine || !canOverview) return;
    productionApi.operators().then((result) => { setStaff(result.operators); setStaffSite(result.viewer_site ?? ""); }).catch(() => undefined);
  }, [mine, canOverview]);
  // Мастер площадки видит монтажников своей площадки, полный доступ — всех.
  const viewable = staff.filter((item) => hasFullAccess(principal) || !staffSite || item.site === staffSite);
  const viewedName = viewable.find((item) => item.id === viewAs)?.name ?? "";
  const acting = mine && Boolean(viewAs) && actFor === viewAs;
  const readOnly = mine && Boolean(viewAs) && !acting;
  const onBehalfOf = acting ? viewAs : "";
  const canPick = mine && canOverview && viewable.length > 0;

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
      // Задание успело измениться (монтажник взял или сдал работу, другой мастер) — показываем свежее, чтобы
      // следующий клик прошёл.
      if (reason instanceof ApiError && reason.status === 409) await reload();
      setError(reason instanceof Error ? reason.message : "Операция с заданием не выполнена");
    } finally {
      setBusy("");
    }
  }

  return <section className="page production-page">
    <header className="page-head">
      <h1>{mine && viewAs ? "Задания" : mine ? "Мои задания" : "Ресурсное планирование"}</h1>
      <span className="page-count">{tasks.length}</span>
      {canPick && <label className="view-as">Сотрудник
        <select aria-label="Сотрудник" style={{ width: "auto" }} value={viewAs} onChange={(event) => setViewAs(event.target.value)}>
          <option value="">Мои задания</option>
          {viewable.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select>
      </label>}
      {canOverview && !mine && <div className="page-actions"><button className="secondary" aria-pressed={showDowntime} onClick={() => setShowDowntime((value) => !value)}>Простои</button>{canEdit && <><button className="secondary" onClick={() => setShowAutoplan(true)}>Автоплан</button><button className="secondary" onClick={() => setShowCalendar((value) => !value)}>Календарь цеха</button><button className="secondary" onClick={() => setShowSkills((value) => !value)}>Компетенции</button><button className="primary" onClick={() => setShowLaunch((value) => !value)}>+ Запустить задание</button></>}</div>}
    </header>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {notice && <div className="success-banner">{notice}</div>}
    {mine && viewAs && <div className={`view-strip${acting ? " acting" : ""}`} role="status"><span>{acting ? `Работаете за сотрудника ${viewedName}` : `Вид сотрудника ${viewedName} — только просмотр`}</span><button type="button" className="secondary" onClick={() => setViewAs("")}>Вернуться к моим заданиям</button></div>}
    {showDowntime && canOverview && !mine && <DowntimeReport close={() => setShowDowntime(false)} />}
    {showCalendar && canEdit && <CalendarForm operators={operators} close={() => setShowCalendar(false)} />}
    {showSkills && canEdit && <SkillsForm operators={operators} close={() => { setShowSkills(false); void reload(); }} />}
    {showAutoplan && canEdit && <AutoplanPanel projectDeadlines={projectDeadlines} close={() => setShowAutoplan(false)} onApplied={(count, created) => { setShowAutoplan(false); setNotice(`Назначено ${count} операций${created ? `, создано заданий: ${created}` : ""}`); void reload(); }} />}
    {showLaunch && canEdit && <LaunchForm tasks={tasks} projectDeadlines={projectDeadlines} busy={Boolean(busy)} mutate={mutate} close={() => setShowLaunch(false)} />}
    {!mine && canOverview && SHOW_TRACEABILITY && <TraceabilitySearch />}
    {!mine && canEdit && <BoxReview boxes={boxes} operators={operators} busy={Boolean(busy)} mutate={mutate} />}
    {/* Задания — первыми (кнопки действий видны сразу), коробки сотрудника — под ними. */}
    {mine && <MyQueue principal={principal} tasks={tasks} session={execution.session} busy={busy} mutate={mutate} readOnly={readOnly} onBehalfOf={onBehalfOf} onSubmitted={(number, box) => setShown({ number, box, fixed: false })} />}
    {mine && <OperatorExecution execution={execution} tasks={tasks} busy={Boolean(busy)} mutate={mutate} readOnly={readOnly} ownerName={viewAs ? viewedName : ""} onBehalfOf={onBehalfOf}
      onSubmitted={(number, box) => setShown({ number, box, fixed: false })} onCorrected={(number, box) => setShown({ number, box, fixed: true })} />}
    {shown && <ShownBox shown={shown} boxes={execution.boxes} tasks={tasks} close={() => setShown(null)} />}
    {!mine && selectedTask && <TaskDetail
      key={`${selectedTask.id}:${selectedTask.version}`}
      task={selectedTask}
      project={projectDeadlines[selectedTask.project.toLowerCase()]}
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
      shift={shift} holidays={holidays} projectDeadlines={projectDeadlines}
      canEdit={canEdit} busy={busy} mutate={mutate}
      openTask={(taskId, operationId) => { setSelectedTaskId(taskId); setSelectedOperationId(operationId ?? ""); }}
    />}
    {mine && loaded && !tasks.length && <div className="empty">{viewAs ? "Операции сотруднику не назначены." : canPick ? "Вам операции не назначены. Выберите сотрудника, чтобы увидеть его задания." : "Операции вам пока не назначены — мастер назначает их в «Ресурсном планировании»."}</div>}
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
    breaks: [["13:00", "13:30"]], effective_hours: "7",
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

  const sortedHolidays = [...holidays].sort();
  const sortedAbsences = absences.map((absence, index) => ({ absence, index }))
    .sort((left, right) => left.absence.from_date.localeCompare(right.absence.from_date));
  const operatorName = (id: string) => operators.find((operator) => operator.id === id)?.name ?? "Сотрудник не в списке";
  const toggleDay = (day: number) => setShift({ ...shift, weekdays: shift.weekdays.includes(day) ? shift.weekdays.filter((value) => value !== day) : [...shift.weekdays, day].sort() });

  return <div className="panel production-calendar">
    <div className="manual-stock-head"><h2>Календарь цеха</h2><button className="link-button" onClick={close}>Закрыть</button></div>
    {error && <div className="error-banner" role="alert">{error}</div>}
    <section className="calendar-block" aria-label="Смена">
      <h3>Смена</h3>
      <div className="form-grid">
        <label>Начало<input aria-label="Начало смены" type="time" value={shift.start} onChange={(event) => setShift({ ...shift, start: event.target.value })} /></label>
        <label>Конец<input aria-label="Конец смены" type="time" value={shift.finish} onChange={(event) => setShift({ ...shift, finish: event.target.value })} /></label>
        <label>Перерыв с<input aria-label="Перерыв с" type="time" value={shift.breaks[0]?.[0] ?? ""} onChange={(event) => setShift({ ...shift, breaks: event.target.value || shift.breaks[0]?.[1] ? [[event.target.value, shift.breaks[0]?.[1] ?? ""]] : [] })} /></label>
        <label>Перерыв до<input aria-label="Перерыв до" type="time" value={shift.breaks[0]?.[1] ?? ""} onChange={(event) => setShift({ ...shift, breaks: shift.breaks[0]?.[0] || event.target.value ? [[shift.breaks[0]?.[0] ?? "", event.target.value]] : [] })} /></label>
        <label title="Без обеда и мелких перерывов: они размазываются по всей смене">Чистое время, ч<input aria-label="Эффективные часы" type="number" min="0.25" max="24" step="0.25" value={shift.effective_hours} onChange={(event) => setShift({ ...shift, effective_hours: event.target.value })} /></label>
        <div className="field-group weekdays" role="group" aria-label="Рабочие дни"><span>Рабочие дни</span>
          <div className="seg-tabs weekday-toggles">{["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"].map((label, day) => <button type="button" key={label} className={shift.weekdays.includes(day) ? "on" : undefined} aria-pressed={shift.weekdays.includes(day)} onClick={() => toggleDay(day)}>{label}</button>)}</div>
        </div>
      </div>
    </section>
    <section className="calendar-block" aria-label="Нерабочие дни и отсутствия">
      <h3>Нерабочие дни и отсутствия</h3>
      <div className="calendar-add"><label>Нерабочий день<input aria-label="Нерабочий день" type="date" value={holiday} onChange={(event) => setHoliday(event.target.value)} /></label><button className="secondary small cal-add" disabled={!holiday} onClick={() => { setHolidays((current) => current.includes(holiday) ? current : [...current, holiday].sort()); setHoliday(""); }}>Добавить праздник</button></div>
      <div className="holiday-chips" aria-label="Нерабочие дни">
        {sortedHolidays.map((day) => <span key={day} className="holiday-chip">{formatDay(day)}<button type="button" aria-label={`Удалить нерабочий день ${formatDay(day)}`} onClick={() => setHolidays((current) => current.filter((value) => value !== day))}>×</button></span>)}
        {!sortedHolidays.length && !busy && <small className="cal-empty">Нерабочих дней нет — выберите день и добавьте.</small>}
      </div>
      <div className="form-grid">
        <label>Оператор<select value={operatorId} onChange={(event) => setOperatorId(event.target.value)}>{operators.map((operator) => <option key={operator.id} value={operator.id}>{operator.name}</option>)}</select></label>
        <label>Вид<select value={kind} onChange={(event) => setKind(event.target.value)}>{["Отпуск", "Больничный", "Отгул", "Командировка", "Учёба"].map((value) => <option key={value}>{value}</option>)}</select></label>
        <label>С<input type="date" value={fromDate} onChange={(event) => setFromDate(event.target.value)} /></label>
        <label>По<input type="date" value={toDate} onChange={(event) => setToDate(event.target.value)} /></label>
      </div>
      <div className="form-actions"><button className="secondary small cal-add" disabled={!operatorId || !fromDate || Boolean(toDate && toDate < fromDate)} onClick={addAbsence}>Добавить отсутствие</button></div>
      {sortedAbsences.length > 0
        ? <div className="table-wrap calendar-absences"><table className="grid"><thead><tr><th>Оператор</th><th>Вид</th><th>С</th><th>По</th><th></th></tr></thead><tbody>
          {sortedAbsences.map(({ absence, index }) => <tr key={absence.id ?? `${absence.operator_id}:${absence.from_date}:${index}`}>
            <td>{absence.operator_name ?? operatorName(absence.operator_id)}</td><td>{absence.kind}</td><td>{formatDay(absence.from_date)}</td><td>{formatDay(absence.to_date)}</td>
            <td className="row-end">{absence.leave_request_id
              ? <small className="cal-leave" title="Зарегистрированное заявление: снимается отзывом заявления в «Моей карточке»">по заявлению</small>
              : <button type="button" className="link-button danger-link" aria-label={`Удалить отсутствие: ${operatorName(absence.operator_id)}, ${formatDay(absence.from_date)}`} onClick={() => setAbsences((current) => current.filter((_, position) => position !== index))}>Удалить</button>}</td>
          </tr>)}
        </tbody></table></div>
        : !busy && <small className="cal-empty">Отсутствий нет — отпуска и больничные добавляются выше.</small>}
    </section>
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
        <div><strong>{name || code}</strong>{name ? <small>{code}</small> : null}</div>
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
function LaunchForm({ tasks, projectDeadlines, busy, mutate, close }: {
  tasks: ProductionTask[];
  projectDeadlines: ProjectDeadlines;
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

  // Задание встаёт в работу в момент запуска (API не принимает дату начала): дата запуска — сегодня, она на кнопке (РП-5).
  const today = formatDayMonth(toLocalInput(new Date().toISOString()));
  const projectDeadline = picked ? projectDeadlines[picked.project.toLowerCase()]?.deadline ?? null : null;
  return <div className="panel production-launch">
    <div className="manual-stock-head"><div><h2>Запустить задание</h2><small>{picked ? `Запуск сегодня, ${today}` : "Выберите сборку с выданной техкартой"}</small></div><button className="link-button" onClick={close}>Отмена</button></div>
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
      {!loading && !items.length && <div className="empty">Нет сборок с выданной техкартой — сначала выдайте техкарту в карточке проекта.</div>}
      {items.length > 0 && !visible.length && <div className="empty">Ничего не найдено.</div>}
    </>}
    {picked && <>
      <button className="secondary small" onClick={() => setPicked(null)}>← все сборки</button>
      <div className="pl-sel"><span className="pl-kb">{picked.index}</span><div><b>{picked.project}</b>{picked.customer ? ` · ${picked.customer}` : ""}<div>{picked.name}</div><small>тираж {picked.run || "—"} · этапов {picked.operations}</small></div></div>
      {doneOf(picked).length > 0 && <div className="pl-warn">Уже запущено: {doneOf(picked).map((task) => `${task.number} × ${task.quantity}`).join(", ")}</div>}
      <div className="production-launch-grid">
        <label>Кол-во<input aria-label="Кол-во" inputMode="numeric" value={quantity} onChange={(event) => setQuantity(event.target.value)} autoFocus /></label>
        <label>Срок задания<input aria-label="Срок задания" type="date" value={deadline} onChange={(event) => setDeadline(event.target.value)} />{projectDeadline && <small className="launch-hint">дедлайн проекта {formatDay(projectDeadline)}</small>}</label>
        <label>Выданный комплект<select aria-label="Выданный комплект" value={fulfillmentRequestId} onChange={(event) => setFulfillmentRequestId(event.target.value)}><option value="">Не связан</option>{issuedRequests.map((request) => <option key={request.id} value={request.id}>{request.number} · {request.purpose}</option>)}</select></label>
      </div>
      <div className="form-actions"><button className="primary" disabled={busy || !/^\d+$/.test(quantity.trim()) || Number(quantity) <= 0} onClick={() => void submit()}>Запустить сегодня, {today}</button></div>
    </>}
  </div>;
}

// Мастер площадки ставит только своих (назначенных раньше оставляем, чтобы были видны); без площадки — всех.
function ownSite(operators: ProductionOperator[], site: string, task: ProductionTask | null) {
  if (!site) return operators;
  const assigned = new Set(task?.operations.flatMap((operation) => operation.operator_ids) ?? []);
  return operators.filter((operator) => operator.site === site || assigned.has(operator.id));
}

function parseDate(value: string | null | undefined) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function factText(task: ProductionTask, operation: ProductionOperation) {
  const fact = operation.fact;
  const processed = Number(fact?.processed ?? 0);
  const main = processed ? `сдано ${fact?.good} из ${requiredOf(task, operation)}${Number(fact?.defect) ? `, брак ${fact?.defect}` : ""}` : "факта нет";
  const ok = Number(operation.wip?.ok ?? 0);
  const closed = Number(operation.wip?.closed ?? 0);
  return main + (ok || closed ? ` · в коробках ${ok}${closed ? ` + ${closed} ждут подтверждения` : ""}` : "");
}

// Задание (prodRenderTask_): Гант операций с фактом, авторасчёт, отмена; клик по операции — карточка назначения.
function TaskDetail({ task, project, initialOperationId = "", tasks, operators, skills, holidays, week, setWeek, canEdit, busy, mutate, back }: {
  task: ProductionTask;
  project?: ProjectDeadlines[string];
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
  const done = task.status === "Выполнено";
  const edit = canEdit && !cancelled && !done;
  // «Выполнено» у цеха — когда финальный этап сдал тираж (сервер проверяет то же); у площадки — по «Сдано N шт».
  const final = task.operations.find((item) => item.final) ?? task.operations[task.operations.length - 1];
  const finalDone = Boolean(final) && Number(final.fact?.good ?? 0) >= Number(final.required_quantity ?? task.quantity);
  const nameOf = (id: string) => operators.find((operator) => operator.id === id)?.name ?? id;
  const operation = task.operations.find((item) => item.id === operationId) ?? null;
  const rows: GanttRow[] = task.operations.map((item) => ({
    key: item.id,
    onClick: edit ? () => setOperationId(item.id) : undefined,
    label: <><b>{item.position}</b> {item.stage}{item.code ? <small className="op-code">{item.code}</small> : null}</>,
    sublabel: <>{item.operator_ids.length ? item.operator_ids.map(nameOf).join(", ") : "не назначено"} · норма {hours(item.norm_seconds)}{feedsText(task, item) && <div>{feedsText(task, item)}</div>}<div>{factText(task, item)}{item.state ? ` · ${item.state}` : ""}</div></>,
    bars: [{
      start: item.planned_start, finish: item.planned_finish,
      title: `${windowText(item.planned_start, item.planned_finish)}${feedsText(task, item) ? ` · ${feedsText(task, item)}` : ""}`,
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
      <div><strong>{task.number} · {taskItemLabel(task)}{task.name ? ` · ${task.name}` : ""}</strong><small>кол-во {task.quantity}{task.deadline ? ` · срок задания ${formatDay(task.deadline)}` : ""}{project?.deadline ? ` · дедлайн проекта ${formatDay(project.deadline)}` : ""} · запущено {formatMoment(task.created_at)}{task.handed_to ? ` · отдано в ${task.handed_to}` : ""}{edit && !task.handed_to ? " · нажмите на операцию, чтобы назначить операторов" : ""}</small>
        {!done && !cancelled && <div className="task-deadlines">{deadlineChips(project, task.deadline).map((chip) => <StatusChip key={chip.kind} tone={chip.tone} title={chip.title}>{chip.text}</StatusChip>)}</div>}</div>
      <StatusChip status={task.status} />
    </div>
    {!done && !cancelled && <DowntimeControl taskId={task.id} performerId="" wholeTask master={canEdit} readOnly={!canEdit} />}
    {canEdit && <div className="page-actions">
      {edit && !task.handed_to && <SchedulePlanner task={task} operators={operators} busy={Boolean(busy)} mutate={mutate} />}
      {edit && !task.handed_to && finalDone && <button className="primary small" disabled={Boolean(busy)} onClick={() => void mutate(`done:${task.id}`, () => productionApi.setDone(task.id, task.version, true))}>Отметить «Выполнено»</button>}
      {done && <button className="secondary small" disabled={Boolean(busy)} onClick={() => {
        if (!window.confirm(`Вернуть задание ${task.number} в работу?`)) return;
        void mutate(`done:${task.id}`, () => productionApi.setDone(task.id, task.version, false));
      }}>Вернуть в работу</button>}
      {!done && <button className="secondary small" disabled={Boolean(busy)} onClick={toggleCancel}>{cancelled ? "Вернуть в работу" : "Отменить задание"}</button>}
    </div>}
    {!cancelled && <JobPanel task={task} operators={operators} canEdit={canEdit} busy={Boolean(busy)} mutate={mutate} />}
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
        title: `${item.number} · этап ${other.position} ${other.stage}: ${formatMoment(other.planned_start)} – ${formatMoment(other.planned_finish)}${feedsText(item, other) ? ` · ${feedsText(item, other)}` : ""}`,
      })));
    if (selected.includes(operator.id) && preview?.start) {
      bars.push({ start: preview.start, finish: preview.finish, tone: "new", text: "этот этап", title: `${formatMoment(preview.start)} – ${formatMoment(preview.finish)}` });
    }
    rows.push({
      key: operator.id,
      label: <label className="pa-who"><input type="checkbox" aria-label={`Назначить ${operator.name}`} checked={selected.includes(operator.id)} onChange={(event) => setSelected((current) => event.target.checked ? [...current, operator.id] : current.filter((id) => id !== operator.id))} />{operator.name}<SkillTag skills={skills} code={operation.code} operatorId={operator.id} /></label>,
      bars,
    });
  }

  return <div className="panel assign-card">
    <div className="manual-stock-head"><div><h2>{task.number} · этап {operation.position} · {operation.stage}</h2><small>{taskItemLabel(task)} × {task.quantity}{requiredNote(task, operation)}{feedsText(task, operation) ? ` · ${feedsText(task, operation)}` : ""} · норма {hours(operation.norm_seconds)} на одного{operation.description ? ` · ${operation.description}` : ""}</small></div><button className="link-button" onClick={close}>Отмена</button></div>
    <div className="pa-ctl"><label>Плановый старт<input aria-label={`Старт этапа ${operation.position}`} type="datetime-local" value={start} onChange={(event) => setStart(event.target.value)} /></label><small>пусто — первое свободное время выбранных операторов</small></div>
    <Gantt rows={rows} labelHead="Оператор" week={week} onWeekChange={setWeek} holidays={holidays} />
    <div className={`pa-plan${planError ? " err" : ""}`} role="status">
      {planError || (!selected.length
        ? `Операторы не выбраны${operation.operator_ids.length ? " — «Назначить» снимет назначение." : "."}`
        : preview ? <><b>План:</b> {formatMoment(preview.start)} → {formatMoment(preview.finish)}{preview.warnings.length > 0 && <ul>{preview.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>}</> : "Считаю план…")}
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
  const feeds = new Map(task.operations.map((operation) => [operation.id, feedsText(task, operation)]));

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
      <small>{taskItemLabel(task)} × {task.quantity}{task.deadline ? ` · срок задания ${formatDay(task.deadline)}` : ""} · {isNetwork(task) ? "этапы идут по схеме: операция начинает, как только все её входы отдали передаточную партию" : "этапы идут цепочкой: следующий начинает, как только предыдущий отдал передаточную партию"}. Операторы — из «Компетенции», по допуску и коэффициенту.</small>
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
          <td>{formatMoment(item.first_ready)}</td><td>{formatMoment(item.finish)}</td>
          <td>{(item.idle_seconds / 3600).toFixed(1).replace(".", ",")} ч</td>
        </tr>)}
      </tbody></table>}
      {variant && <table className="ap-t"><thead><tr><th>Этап</th><th>Операторы</th><th>Старт</th><th>Финиш</th></tr></thead><tbody>
        {variant.operations.map((operation) => <tr key={operation.operation_id}>
          <td><b>{operation.position}</b> {stages.get(operation.position) ?? ""}{feeds.get(operation.operation_id) ? <span className="ap-man"> {feeds.get(operation.operation_id)}</span> : null}</td>
          <td>{operation.operator_ids.map((id) => names.get(id) ?? id).join(", ")}{operation.automatic ? "" : <span className="ap-man"> назначено раньше</span>}</td>
          <td>{formatMoment(operation.start)}</td><td>{formatMoment(operation.finish)}</td>
        </tr>)}
      </tbody></table>}
      {variant?.warnings.length ? <ul>{variant.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul> : null}
      <button className="primary small" disabled={busy || !variant} onClick={() => void mutate(`schedule:${task.id}`, () => productionApi.applySchedule(task.id, task.version, replan, maxCrew, variant.transfer_batch))}>Применить</button>
    </div>}
  </div>;
}

// Очередь оператора (myOpsRender_): операции по дням плана — «Сегодня / Завтра / Начато ранее», факт «сдано X из N»,
// «вместе с», «план истёк». Главное действие карточки — справа вверху, рядом с состоянием (владелец, 12.10.2026: «я не
// вижу старт»): «Начать», у начатой — «Пауза» / «Продолжить» и «Сдать…»; ЭТК — второстепенная кнопка. Начатая — первой.
function MyQueue({ principal, tasks, session, busy, mutate, readOnly = false, onBehalfOf = "", onSubmitted }: {
  principal: Principal;
  tasks: ProductionTask[];
  session: ProductionWorkSession | null;
  busy: string;
  readOnly?: boolean;
  /** Работа за выбранного сотрудника: его id уходит с каждым действием, право проверяет сервер. */
  onBehalfOf?: string;
  onSubmitted: (number: string, box?: ProductionBox) => void;
  mutate: (key: string, operation: () => Promise<unknown>) => Promise<void>;
}) {
  const [starting, setStarting] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [etkOpen, setEtkOpen] = useState("");
  const now = useNow(30_000);
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const weekdays = ["воскресенье", "понедельник", "вторник", "среда", "четверг", "пятница", "суббота"];
  const dayTitle = (date: Date | null, started: boolean) => {
    if (!date) return "Без плана";
    const day = new Date(date.getFullYear(), date.getMonth(), date.getDate());
    const diff = Math.round((day.getTime() - today.getTime()) / 86_400_000);
    const base = `${String(date.getDate()).padStart(2, "0")}.${String(date.getMonth() + 1).padStart(2, "0")}, ${weekdays[date.getDay()]}`;
    return diff === 0 ? `Сегодня · ${base}` : diff === 1 ? `Завтра · ${base}` : diff < 0 ? (started ? `Начато ранее · ${base}` : `Просрочено · план ${base.slice(0, 5)}`) : base;
  };
  const isDone = (task: ProductionTask) => {
    const operation = task.operations[0];
    return Boolean(operation && (operation.state === "выполнена" || operation.my_share?.finished_at));
  };
  const isRunning = (task: ProductionTask) => Boolean(session && session.task_id === task.id && session.operation_id === task.operations[0]?.id);
  const renderCard = (task: ProductionTask, last: { title: string | null }) => {
    const operation = task.operations[0];
    if (!operation) return null;
    // Своя доля: у раздробленной операции у монтажника свои штуки и своё время.
    const share = operation.my_share;
    const plannedStart = share?.planned_start ?? operation.planned_start;
    const plannedFinish = share?.planned_finish ?? operation.planned_finish;
    const startAt = parseDate(plannedStart);
    const finishAt = parseDate(plannedFinish);
    const processed = Number(operation.fact?.processed ?? 0);
    const running = isRunning(task) ? session : null;
    const title = dayTitle(startAt, Boolean(running) || processed > 0 || Boolean(share?.started_at));
    const header = title !== last.title ? <div className="mo-day">{title}</div> : null;
    last.title = title;
    const done = isDone(task);
    const late = Boolean(finishAt && finishAt < now && !done);
    const key = `${task.id}:${operation.id}`;
    const stage = operation.stage || `Этап ${operation.position}`;
    const state = cardState(operation, running, done, now);
    const fact = operation.time_fact;
    const work = { onBehalfOf };
    const paused = running?.status === "Пауза";
    return <div key={key}>
      {header}
      <div className={`mo-card${late ? " late" : ""}${done ? " done" : ""}${running ? " run" : ""}`}>
        <div className="mo-h">
          <div className="mo-name"><b>{stage}</b>{operation.code && <small className="op-code">{operation.code}</small>}</div>
          <div className="mo-side">
            {state && <span className={`mo-state ${state.tone}`} role="status">{state.text}</span>}
            {!readOnly && running && <>
              {paused
                ? <button className="primary" disabled={Boolean(busy)} onClick={() => void mutate(running.id, () => productionApi.workAction(running.id, "resume", { ...work, key: stepKey(running, "resume") }))}>Продолжить</button>
                : <button className="secondary" disabled={Boolean(busy)} onClick={() => void mutate(running.id, () => productionApi.workAction(running.id, "pause", { ...work, key: stepKey(running, "pause") }))}>Пауза</button>}
              <button className={paused ? "secondary" : "primary"} aria-expanded={submitting} onClick={() => setSubmitting((value) => !value)}>Сдать…</button>
            </>}
            {!readOnly && !running && !done && <button className="primary" aria-expanded={starting === key} disabled={Boolean(session) || Boolean(busy)} title={session ? "Сначала сдайте начатую работу" : undefined} onClick={() => setStarting(starting === key ? "" : key)}>Начать</button>}
            <button className="secondary" aria-expanded={etkOpen === key} onClick={() => setEtkOpen(etkOpen === key ? "" : key)}>ЭТК</button>
          </div>
        </div>
        <div className="mo-s">{task.number} · этап {operation.position} · <b>{taskItemLabel(task)}</b> × {task.quantity}{requiredNote(task, operation)}{task.name ? ` · ${task.name}` : ""}{feedsText(task, operation) ? ` · ${feedsText(task, operation)}` : ""}{share?.quantity != null ? <> · <b>ваши {share.quantity} шт</b></> : null}</div>
        {operation.description && !operation.description.startsWith(stage) && <div className="mo-d">{operation.description}</div>}
        <div className="mo-s mo-plan"><span className="mo-window">{startAt ? `план ${windowText(plannedStart, plannedFinish)}` : "время не назначено"}</span> · норма {hours(operation.norm_seconds)}{task.deadline ? ` · срок задания ${formatDay(task.deadline)}` : ""}{operation.mates?.length ? ` · вместе с: ${operation.mates.join(", ")}` : ""}{late ? <> · <b>план истёк</b></> : null}</div>
        <div className="mo-fact">{processed ? <>Сдано <b>{operation.fact?.good}</b> из {requiredOf(task, operation)}{Number(operation.fact?.defect) ? ` · брак ${operation.fact?.defect}` : ""} · {operation.state}</> : "Ещё не сдавали"}{share?.started_at ? ` · взял ${formatMoment(share.started_at)}` : ""}{share?.finished_at ? ` · закончил ${formatMoment(share.finished_at)}` : ""}{fact ? <> · <span className="mo-tf">факт {clock(fact.actual_seconds)} · норма {clock(fact.norm_seconds)}{fact.percent != null ? ` · ${fact.percent} %` : ""}</span></> : null}</div>
        {!done && <DowntimeControl taskId={task.id} operationId={operation.id} primary={firstOfTask.has(key)} performerId={onBehalfOf || principal.user.id} master={canAct(principal, "production")} readOnly={readOnly} onBehalfOf={onBehalfOf} />}
        {!readOnly && running && submitting && <SubmitWorkForm session={running} operation={operation} busy={Boolean(busy)} mutate={mutate} onBehalfOf={onBehalfOf} onSubmitted={(number, box) => { setSubmitting(false); onSubmitted(number, box); }} />}
        {!readOnly && !running && !done && !session && starting === key && <StartWorkForm task={task} operation={operation} busy={Boolean(busy)} mutate={mutate} onBehalfOf={onBehalfOf} />}
        {etkOpen === key && <OperationEtk principal={principal} task={task} operation={operation} />}
      </div>
    </div>;
  };
  // Начатая — первой, невыполненное — по дням плана; выполненное свёрнуто ниже, чтобы «Начать» не искали под сделанным.
  const active = tasks.filter((task) => !isDone(task)).sort((left, right) => Number(isRunning(right)) - Number(isRunning(left)));
  const finished = tasks.filter(isDone);
  // Простой — на задание: полоса с «Закончить простой» — на первой невыполненной карточке задания, на остальных — строка.
  const firstOfTask = new Set(active.filter((task, index) => active.findIndex((other) => other.id === task.id) === index).map((task) => `${task.id}:${task.operations[0]?.id}`));
  const activeMark = { title: null as string | null };
  const finishedMark = { title: null as string | null };
  return <div className="my-queue">
    {active.map((task) => renderCard(task, activeMark))}
    {!active.length && finished.length > 0 && <div className="empty">Невыполненных операций нет — все назначенные сданы.</div>}
    {finished.length > 0 && <details className="mo-finished"><summary>Выполнено · {finished.length}</summary>
      <div className="my-queue">{finished.map((task) => renderCard(task, finishedMark))}</div>
    </details>}
  </div>;
}

/** Текущее время, обновляемое раз в period мс: таймер начатой работы на карточке. */
function useNow(period: number): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), period);
    return () => window.clearInterval(timer);
  }, [period]);
  return now;
}

/** Часы:минуты длительности: 5880 с → «1:38». */
export function clock(seconds: number): string {
  const minutes = Math.max(0, Math.floor((Number(seconds) || 0) / 60));
  return `${Math.floor(minutes / 60)}:${String(minutes % 60).padStart(2, "0")}`;
}

/** Сколько сессия проработала к now: от начала без пауз (идущая пауза тоже не считается). */
export function workedSeconds(session: ProductionWorkSession, now: Date): number {
  const started = parseDate(session.started_at);
  if (!started) return 0;
  const pausedSince = session.status === "Пауза" ? parseDate(session.pause_started_at) : null;
  const end = pausedSince ?? now;
  return Math.max(0, Math.round((end.getTime() - started.getTime()) / 1000) - (Number(session.pause_seconds) || 0));
}

// Ключ повтора действия по сессии: двойной клик по той же кнопке в том же состоянии — один и тот же ключ, сервер не
// делает действие второй раз; после паузы и продолжения состояние другое — и ключ новый.
function stepKey(session: ProductionWorkSession, action: string): string {
  return `${session.id}:${action}:${session.pause_seconds}:${session.pause_started_at ?? ""}`;
}

/** Строка состояния карточки рядом с главной кнопкой. */
function cardState(operation: ProductionOperation, running: ProductionWorkSession | null, done: boolean, now: Date): { text: string; tone: string } | null {
  if (running) {
    const since = formatMoment(running.started_at);
    const sameDay = since.slice(0, 5) === formatMoment(now.toISOString()).slice(0, 5);
    const worked = clock(workedSeconds(running, now));
    return running.status === "Пауза"
      ? { text: `на паузе · ${worked}`, tone: "tone-warn" }
      : { text: `в работе с ${sameDay ? since.slice(6) : since} · ${worked}`, tone: "tone-ok" };
  }
  if (done) return { text: "выполнена", tone: "tone-neutral" };
  const readiness = operation.readiness;
  if (!readiness || readiness.state === "done") return null;
  const text = `${readiness.text}${readiness.boxes.length > 0 && !operation.first ? ` · коробок: ${readiness.boxes.length}` : ""}`;
  return { text, tone: readiness.state === "ready" || readiness.state === "assembly" ? "tone-ok" : "tone-warn" };
}

// ЭТК операции для монтажника: выданная карта проекта и изделия, только чтение, открыта на этой операции.
function OperationEtk({ principal, task, operation }: { principal: Principal; task: ProductionTask; operation: ProductionOperation }) {
  const [view, setView] = useState<{ card: ProjectTechcard; focus: { position: number; code: string } } | undefined>(undefined);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    productionApi.operationEtk(task.id, operation.id)
      .then((result) => { if (live) setView(result); })
      .catch((reason) => { if (live) setError(reason instanceof Error ? reason.message : "ЭТК не открылась"); });
    return () => { live = false; };
  }, [task.id, operation.id]);
  // Только чтение: правка и пересборка ЭТК — у технолога.
  const viewer: Principal = { ...principal, permissions: { myops: "view" } };
  return <div className="panel mo-etk">
    {error && <div className="error-banner" role="alert">{error}</div>}
    {!error && !view && <div className="empty">Загрузка…</div>}
    {view && <EtkSheet card={view.card} principal={viewer} onChange={() => undefined} focus={view.focus} task taskId={task.id} sketchSource={{
      list: () => productionApi.operationEtkSketches(task.id, operation.id),
      url: (key, at) => productionApi.operationEtkSketchUrl(task.id, operation.id, key, at),
    }} />}
  </div>;
}

export function goodPrefill(remaining: string | undefined, inputLimit: string | null | undefined): string {
  if (!remaining) return "";
  if (inputLimit == null || inputLimit === "") return remaining;
  const left = Number(remaining), limit = Number(inputLimit);
  if (!Number.isFinite(left) || !Number.isFinite(limit)) return remaining;
  return limit < left ? String(limit) : remaining;
}

// Сдача начатой работы: годные (остаток доли, не больше, чем во взятых входных коробках) и брак с характером.
function SubmitWorkForm({ session, operation, busy, mutate, onBehalfOf = "", onSubmitted }: {
  session: ProductionWorkSession;
  operation: ProductionOperation | undefined;
  busy: boolean;
  onBehalfOf?: string;
  onSubmitted: (number: string, box?: ProductionBox) => void;
  mutate: (key: string, operation: () => Promise<unknown>) => Promise<void>;
}) {
  const [good, setGood] = useState("");
  const [defect, setDefect] = useState("0");
  const [defectCharacter, setDefectCharacter] = useState("");
  const [defectDescription, setDefectDescription] = useState("");
  // «Годные» — остаток его доли, но не больше, чем есть во взятых входных коробках: не печатать то, что уже известно.
  const prefill = goodPrefill(operation?.remaining, session.input_limit);
  useEffect(() => {
    if (prefill) setGood((current) => current === "" ? prefill : current);
  }, [session.id, operation?.id, prefill]); // eslint-disable-line react-hooks/exhaustive-deps
  const work = { onBehalfOf };
  return <div className="execution-actions mo-submit">
    <label>Годные<input aria-label="Годные изделия" type="number" min="0" step="any" value={good} onChange={(event) => setGood(event.target.value)} /></label>
    <label>Брак<input aria-label="Брак" type="number" min="0" step="any" value={defect} onChange={(event) => setDefect(event.target.value)} /></label>
    {Number(defect) > 0 && <><label>Характер брака<input aria-label="Характер брака" value={defectCharacter} onChange={(event) => setDefectCharacter(event.target.value)} /></label><label className="defect-text">Описание брака<AutoTextarea aria-label="Описание брака" multiline value={defectDescription} onChange={setDefectDescription} /></label></>}
    <button className="primary" disabled={busy || !good || (Number(defect) > 0 && (!defectCharacter.trim() || !defectDescription.trim()))} onClick={() => void mutate(session.id, async () => {
      const result = await productionApi.submitWork(session.id, good, defect || "0", defectCharacter, defectDescription, { ...work, key: `${session.id}:submit` });
      onSubmitted(result.box.number ?? "", result.box);
      setGood(""); setDefect("0"); setDefectCharacter(""); setDefectDescription("");
      return result;
    })}>Сдать</button>
    <button className="link-button danger-link" disabled={busy} onClick={() => { if (window.confirm("Отменить начало? Время не запишется.")) void mutate(session.id, () => productionApi.workAction(session.id, "cancel", { ...work, key: `${session.id}:cancel` })); }}>Отменить начало</button>
  </div>;
}

// Начатая работа, которой нет в очереди (задание отменили или оно за пределом списка): управление здесь, чтобы её
// можно было сдать или отменить. Коробки сотрудника — всегда здесь.
function OperatorExecution({ execution, tasks, busy, mutate, readOnly = false, ownerName = "", onBehalfOf = "", onSubmitted, onCorrected }: {
  execution: { session: ProductionWorkSession | null; boxes: ProductionBox[] };
  tasks: ProductionTask[];
  busy: boolean;
  readOnly?: boolean;
  ownerName?: string;
  onBehalfOf?: string;
  onSubmitted: (number: string, box?: ProductionBox) => void;
  onCorrected: (number: string, box?: ProductionBox) => void;
  mutate: (key: string, operation: () => Promise<unknown>) => Promise<void>;
}) {
  const [correcting, setCorrecting] = useState("");
  const session = execution.session;
  const queued = session ? tasks.some((item) => item.id === session.task_id && item.operations[0]?.id === session.operation_id) : false;
  const task = session ? tasks.find((item) => item.id === session.task_id) : null;
  const operation = task?.operations.find((item) => item.id === session?.operation_id);
  const work = { onBehalfOf };
  return <>
    {session && !queued && <div className="panel execution-current">
      <div className="manual-stock-head"><div><h2>Идёт работа{operation?.stage ? `: ${operation.stage}` : ""}</h2><small>{task?.number ?? "Задание"}{operation ? ` · этап ${operation.position}` : ""}{task ? ` · ${taskItemLabel(task)}` : ""} · с {formatMoment(session.started_at)}{session.pause_seconds ? ` · пауз ${Math.round(session.pause_seconds / 60)} мин` : ""}</small></div><StatusChip status={session.status} /></div>
      {!readOnly && <div className="execution-actions">
        {session.status === "В работе" && <button className="secondary" disabled={busy} onClick={() => void mutate(session.id, () => productionApi.workAction(session.id, "pause", { ...work, key: stepKey(session, "pause") }))}>Пауза</button>}
        {session.status === "Пауза" && <button className="secondary" disabled={busy} onClick={() => void mutate(session.id, () => productionApi.workAction(session.id, "resume", { ...work, key: stepKey(session, "resume") }))}>Продолжить</button>}
        <SubmitWorkForm session={session} operation={operation} busy={busy} mutate={mutate} onBehalfOf={onBehalfOf} onSubmitted={onSubmitted} />
      </div>}
    </div>}
    {execution.boxes.length > 0 && <div className="panel execution-boxes">
      <h2>{ownerName ? `Коробки: ${ownerName}` : "Мои коробки"}</h2>
      <div className="box-grid">{execution.boxes.map((box) => <div className="production-box" key={`${box.id}:${box.version}`}>
        <BoxName box={box} />
        <span>{box.item} · этап {box.operation_position}</span>
        <span>{box.quantity} шт · доступно {box.available}</span>
        <StatusChip status={box.status} />
        {box.return_reason && <small className="danger-text">{box.return_reason}</small>}
        {!readOnly && box.status === "Возвращена" && <button className="secondary small" disabled={busy} onClick={() => setCorrecting(correcting === box.id ? "" : box.id)}>Исправить</button>}
        {!readOnly && box.status === "Возвращена" && correcting === box.id && <BoxCorrection box={box} busy={busy} mutate={mutate} onBehalfOf={onBehalfOf} onDone={onCorrected} />}
      </div>)}</div>
    </div>}
  </>;
}

// prodBoxShow_: после «Сдать» — код коробки крупно (его пишут маркером), свежая коробка из списка или из ответа сдачи.
function ShownBox({ shown, boxes, tasks, close }: {
  shown: { number: string; fixed: boolean; box?: ProductionBox };
  boxes: ProductionBox[];
  tasks: ProductionTask[];
  close: () => void;
}) {
  const box = boxes.find((item) => item.id === shown.box?.id) ?? shown.box;
  // В «Моих заданиях» одно задание приходит несколькими карточками (по операции на карточку) — ищем по всем.
  const stage = box ? tasks.flatMap((task) => task.id === box.task_id ? task.operations : []).find((item) => item.id === box.operation_id)?.stage ?? "" : "";
  return <BoxLabelPanel number={shown.number} box={box} stage={stage} fixed={shown.fixed} close={close} />;
}

// Correction replaces one output of a returned box (SUPPLYMAP `prodWorkFix_`); the box goes back to the master.
function BoxCorrection({ box, busy, mutate, onDone, onBehalfOf = "" }: {
  box: ProductionBox;
  busy: boolean;
  onBehalfOf?: string;
  mutate: (key: string, operation: () => Promise<unknown>) => Promise<void>;
  onDone?: (number: string, box?: ProductionBox) => void;
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
    {newDefect && <><label>Характер брака<input aria-label={`Характер брака в коробке ${box.number}`} value={defectCharacter} onChange={(event) => setDefectCharacter(event.target.value)} /></label><label className="defect-text">Описание брака<AutoTextarea aria-label={`Описание брака в коробке ${box.number}`} multiline value={defectDescription} onChange={setDefectDescription} /></label></>}
    <button className="primary small" disabled={busy || invalid} onClick={() => void mutate(box.id, async () => {
      const result = await productionApi.correctBox(box.id, box.version, {
        output_id: outputId, good_quantity: good, defect_quantity: defect || "0",
        defect_character: defectCharacter, defect_description: defectDescription,
      }, onBehalfOf);
      onDone?.(result.box.number ?? box.number ?? "", result.box);
      return result;
    })}>Записать исправление</button>
  </div>;
}

// prodWorkStart_: первая операция начинается сразу (складские партии — по желанию), дальше оператор вводит номера
// входных коробок с предыдущего этапа, подтверждённых мастером; штуки из них берутся при сдаче.
function StartWorkForm({ task, operation, busy, mutate, onBehalfOf = "" }: {
  task: ProductionTask;
  operation: ProductionOperation;
  busy: boolean;
  onBehalfOf?: string;
  mutate: (key: string, operation: () => Promise<unknown>) => Promise<void>;
}) {
  const [boxes, setBoxes] = useState<ProductionBox[]>([]);
  const [materials, setMaterials] = useState<ProductionMaterialSource[]>([]);
  const [quantities, setQuantities] = useState<Record<string, string>>({});
  const [numbers, setNumbers] = useState("");
  const [error, setError] = useState("");
  const first = isFirst(operation);
  const source = feedsText(task, operation);
  // Ключ повтора на эту форму: двойной клик «Начать работу» не начинает дважды.
  const key = useRef(crypto.randomUUID());
  useEffect(() => {
    productionApi.inputs(task.id, operation.id, onBehalfOf)
      .then((result) => { setBoxes(result.boxes); setMaterials(result.materials); })
      .catch((reason) => setError(reason instanceof Error ? reason.message : "Коробки не загружены"));
  }, [task.id, operation.id, operation.position]);
  const [picked, setPicked] = useState<string[]>([]);
  const chips = operation.readiness?.boxes.map((box) => ({ id: box.id, number: box.number ?? "", code: box.code ?? null, quantity: box.quantity, stage: box.stage, position: box.operation_position }))
    ?? boxes.map((box) => ({ id: box.id, number: box.number ?? "", code: box.code, quantity: box.available, stage: "", position: box.operation_position }));
  const typed = numbers.split(/[,;\n]+/).map((item) => item.trim()).filter(Boolean);
  const inputs = [...picked, ...typed.filter((item) => !picked.includes(item))];
  const materialInputs = materials.flatMap((material) => {
    const quantity = quantities[material.fulfillment_line_id]?.trim();
    return quantity && Number(quantity) > 0 ? [{ fulfillment_line_id: material.fulfillment_line_id, quantity }] : [];
  });
  const [manual, setManual] = useState(false);
  const wrongMaterial = materialInputs.some((item) => Number(item.quantity) > Number(materials.find((material) => material.fulfillment_line_id === item.fulfillment_line_id)?.available ?? 0));
  const needBox = !first && !inputs.length;
  const why = busy ? "Идёт сохранение" : needBox ? "Выберите коробку или введите её код" : wrongMaterial ? "Количество больше доступного в партии" : "";
  return <div className="start-work sw-col">
    {error && <small className="danger-text">{error}</small>}
    {first && materials.map((material) => <label key={material.fulfillment_line_id}>{material.grn}{material.package ? ` · ${material.package}` : ""} · {material.article || material.name} · доступно {material.available} {material.unit}<input aria-label={`Количество из партии ${material.grn} ${material.package}`} type="number" min="0" max={material.available} step="any" value={quantities[material.fulfillment_line_id] ?? ""} onChange={(event) => setQuantities((current) => ({ ...current, [material.fulfillment_line_id]: event.target.value }))} /></label>)}
    {!first && <>
      <div className="sw-title">Из каких коробок берёте детали <small>({source ? `${source}; подтверждённые мастером` : `с этапа ${previousStage(operation)}, подтверждённые мастером`})</small></div>
      {chips.length > 0 && <div className="box-chips" role="group" aria-label="Готовые коробки">{chips.map((chip) => <button key={chip.id} type="button" className={`box-chip${picked.includes(chip.number) ? " on" : ""}`} aria-pressed={picked.includes(chip.number)}
        onClick={() => setPicked((current) => current.includes(chip.number) ? current.filter((item) => item !== chip.number) : [...current, chip.number])}>
        <b>{boxTitle(chip)}</b><span>{chip.quantity} шт{source ? ` · ${chip.stage || `этап ${chip.position}`}` : ""}</span></button>)}</div>}
      {chips.length > 0 && !manual && !typed.length
        ? <button type="button" className="link-button sw-manual-toggle" onClick={() => setManual(true)}>Ввести код вручную</button>
        : <label className="sw-manual">Ввести код вручную<textarea aria-label="Номера коробок" rows={2} placeholder="код с коробки или номер 000001-8; несколько — через запятую" value={numbers} onChange={(event) => setNumbers(event.target.value)} /></label>}
    </>}
    <button className="primary sw-go" disabled={busy || needBox || wrongMaterial} onClick={() => void mutate(operation.id, () => productionApi.startWork(task.id, operation.id, inputs, materialInputs, { onBehalfOf, key: key.current }))}>Начать работу</button>
    {why && <small className="sw-why">{why}</small>}
  </div>;
}

function BoxReview({ boxes, operators, busy, mutate }: {
  boxes: ProductionBox[];
  operators: ProductionOperator[];
  busy: boolean;
  mutate: (key: string, operation: () => Promise<unknown>) => Promise<void>;
}) {
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [returning, setReturning] = useState<Record<string, boolean>>({});
  const [query, setQuery] = useState("");
  const pending = boxes.filter((box) => box.status === "Закрыта оператором");
  if (!pending.length) return null;
  const shown = pending.filter((box) => boxMatches(box, query));
  const confirmAll = () => mutate("confirm-all", async () => {
    for (const box of pending) await productionApi.boxAction(box.id, "confirm", box.version);
  });
  return <div className="panel box-review"><div className="manual-stock-head"><h2>Коробки на подтверждение</h2><span className="page-count">{pending.length}</span>
    {pending.length > 3 && <input className="search box-search" type="search" aria-label="Найти коробку по коду" placeholder="Код с коробки или номер" value={query} onChange={(event) => setQuery(event.target.value)} />}
    {pending.length > 1 && <button className="primary small" disabled={busy} onClick={() => void confirmAll()}>Подтвердить все</button>}</div>
    {!shown.length && <div className="empty">Нет коробки с кодом «{query}» — проверьте код на коробке.</div>}
    <div className="box-grid">{shown.map((box) => <div className="production-box" key={`${box.id}:${box.version}`}>
      <BoxName box={box} />
      <span>{box.item} · этап {box.operation_position}</span>
      <span>{box.quantity} шт · {operators.find((operator) => operator.id === box.operator_id)?.name ?? box.operator_id}</span>
      <button className="primary small" disabled={busy} onClick={() => void mutate(box.id, () => productionApi.boxAction(box.id, "confirm", box.version))}>Подтвердить</button>
      {returning[box.id]
        ? <>
          <label>Причина возврата<textarea value={reasons[box.id] ?? ""} onChange={(event) => setReasons((current) => ({ ...current, [box.id]: event.target.value }))} /></label>
          <div className="box-return-act">
            <button className="secondary small" disabled={busy || !(reasons[box.id] ?? "").trim()} onClick={() => void mutate(box.id, () => productionApi.returnBox(box.id, box.version, reasons[box.id]))}>Вернуть оператору</button>
            <button className="link-button" onClick={() => setReturning((current) => ({ ...current, [box.id]: false }))}>Отмена</button>
          </div>
        </>
        : <button className="link-button" onClick={() => setReturning((current) => ({ ...current, [box.id]: true }))}>Вернуть…</button>}
    </div>)}</div>
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
