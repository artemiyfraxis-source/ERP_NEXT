import { useEffect, useMemo, useState } from "react";
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
import { canAct, canSee, openPlace } from "./navigation";
import { MachineLoadGrid } from "./MachineLoad";
import { PeopleLoadGrid } from "./PeopleLoad";
import { PlanBaseline } from "./PlanBaseline";
import { EMPTY, formatDate, formatDayMonth } from "./format";
import { PlanLoadSheet, periodWords, type SheetMark } from "./PlanLoadSheet";

const emptyPlan: ProductionPlanResult = { blockers: [], stages: [], review_values: [], rows: [] };
const WEEKDAYS = ["вс", "пн", "вт", "ср", "чт", "пт", "сб"];

const shortDate = (value: string | null) => formatDate(value) || EMPTY;

const projectKey = (project: string) => project.trim().toUpperCase();

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

  // Сетка станков перечитывает занятость, когда пришли новые задания (назначили, сдвинули).
  const planVersion = useMemo(() => tasks.map((task) => `${task.id}:${task.version}`).join(","), [tasks]);

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
    // Проект открывается окном в «Ресурсном планировании» (владелец 22.10: «из плана — в проект в производстве»,
    // боковой панели нет): адрес `?section=production&project=…`, кнопка «Назад» возвращает в план.
    // Без доступа к ресурсному планированию — карточка проекта в «Проектах», если она есть.
    if (canSee(principal, "production")) openPlace("production", { project: row.project });
    else if (openProjectCard && hasCard(row)) openProjectCard(row.project);
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
          onOpenProject={openRow} onOpenJournal={openRow} hasCard={hasCard} />}
    </section>

    <PlanBaseline canEdit={canEdit} />

    <section className="panel plan-load" aria-label="Загрузка по людям">
      <div className="plan-section-head"><h2>Загрузка по людям</h2>
        {unassigned.operations > 0 && <span className="plan-tag soon">Без исполнителя: {unassigned.operations} опер. в {unassigned.tasks} зад. — назначить в «Ресурсном планировании»</span>}
      </div>
      {loadError
        ? <div className="empty">Загрузка людей недоступна: {loadError}</div>
        : <PeopleLoadGrid operators={operators} tasks={tasks} absences={absences} holidays={holidays} shift={shift} week={week} onWeekChange={setWeek} />}
    </section>

    <section className="panel plan-load" aria-label="Загрузка станков">
      <div className="plan-section-head"><h2>Загрузка станков</h2></div>
      {loadError
        ? <div className="empty">Загрузка станков недоступна: {loadError}</div>
        : <MachineLoadGrid holidays={holidays} shift={shift} week={week} onWeekChange={setWeek} refreshKey={planVersion} />}
    </section>
  </section>;
}
