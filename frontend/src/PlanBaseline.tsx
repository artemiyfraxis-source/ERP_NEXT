import { useEffect, useMemo, useState } from "react";
import { api, failureText } from "./api";
import { DataTable, type DataColumn } from "./DataTable";
import { EMPTY, formatMoment } from "./format";
import { StatusChip } from "./StatusChip";
import "./plan-baseline.css";

/* «План и факт» производственного плана: «Зафиксировать базовый план» — неизменяемый снимок сроков и тиражей
   запущенных заданий; таблица — отклонения факта от последнего снимка (начато позже, закончено позже, недодано).
   Идея базового плана — PRODUCTION_PLANNER Василия (перенесено с разрешения автора). */

export interface BaselineRow {
  task_id: string;
  task_number: string;
  project: string;
  item: string;
  quantity: number;
  planned_start: string;
  planned_finish: string;
  status: string | null;
  started_at: string | null;
  finished_at: string | null;
  done_quantity: string;
  start_delay_minutes: number | null;
  finish_delay_minutes: number | null;
  late_start: boolean;
  late_finish: boolean;
  shortfall: string;
  short: boolean;
  started: boolean;
  finished: boolean;
}

export interface BaselineHeader {
  id: string;
  number: number;
  created_at: string;
  created_by: string;
  skipped: number;
  tasks: number;
  outside: number;
}

export interface BaselineResult {
  tolerance_minutes: number;
  baseline: BaselineHeader | null;
  rows: BaselineRow[];
  summary?: { total: number; late_start: number; late_finish: number; short: number; on_plan: number };
}

const BASELINE_URL = "/api/production/plan/baseline";

/** Ответ без снимка или чужой формы (заглушка теста) — «не зафиксирован», а не падение экрана. */
export function readBaseline(value: unknown): BaselineResult {
  const body = (value ?? {}) as Partial<BaselineResult>;
  const header = body.baseline && typeof body.baseline === "object" && typeof body.baseline.number === "number" ? body.baseline : null;
  const rows = header && Array.isArray(body.rows) ? body.rows.filter((row) => row && typeof row.task_id === "string") : [];
  return { tolerance_minutes: Number(body.tolerance_minutes) || 60, baseline: header, rows, summary: header ? body.summary : undefined };
}

/** Опоздание словами: «на 40 мин», «на 3 ч», «на 2 дн 4 ч». */
export function delayText(minutes: number | null): string {
  if (minutes === null || minutes <= 0) return "";
  if (minutes < 60) return `на ${minutes} мин`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `на ${hours} ч`;
  const days = Math.floor(hours / 24);
  const rest = hours % 24;
  return rest ? `на ${days} дн ${rest} ч` : `на ${days} дн`;
}

const deviates = (row: BaselineRow) => row.late_start || row.late_finish || row.short;

function startWord(row: BaselineRow) {
  if (!row.late_start) return "";
  return `${row.started ? "Начато позже" : "Не начато, опоздание"} ${delayText(row.start_delay_minutes)}`;
}

function finishWord(row: BaselineRow) {
  if (!row.late_finish) return "";
  return `${row.finished ? "Закончено позже" : "Не закончено, опоздание"} ${delayText(row.finish_delay_minutes)}`;
}

function DeviationMarks({ row }: { row: BaselineRow }) {
  if (row.status === "Отменено") return <StatusChip tone="danger">Отменено · недодано {row.shortfall} шт</StatusChip>;
  if (!deviates(row)) return <StatusChip tone="success">По плану</StatusChip>;
  return <span className="baseline-marks">
    {row.late_start && <StatusChip tone="warning">{startWord(row)}</StatusChip>}
    {row.late_finish && <StatusChip tone="danger">{finishWord(row)}</StatusChip>}
    {row.short && <StatusChip tone="danger">Недодано {row.shortfall} шт</StatusChip>}
  </span>;
}

function PlanFact({ plan, fact, late, tone }: { plan: string; fact: string | null; late: boolean; tone: "warning" | "danger" }) {
  return <span className="baseline-pair">
    <span>{formatMoment(plan) || EMPTY}</span>
    <span className={late ? `baseline-fact is-late is-${tone}` : "baseline-fact"}>{fact ? formatMoment(fact) : EMPTY}</span>
  </span>;
}

const columns: DataColumn<BaselineRow>[] = [
  { key: "task", title: "Задание", value: (row) => row.task_number, render: (row) => <b>{row.task_number}</b> },
  { key: "project", title: "Проект", value: (row) => row.project, filter: true },
  { key: "item", title: "Изделие", value: (row) => row.item },
  { key: "start", title: "Начало: план / факт", value: (row) => `${formatMoment(row.planned_start)} ${formatMoment(row.started_at)}`,
    render: (row) => <PlanFact plan={row.planned_start} fact={row.started_at} late={row.late_start} tone="warning" /> },
  { key: "finish", title: "Конец: план / факт", value: (row) => `${formatMoment(row.planned_finish)} ${formatMoment(row.finished_at)}`,
    render: (row) => <PlanFact plan={row.planned_finish} fact={row.finished_at} late={row.late_finish} tone="danger" /> },
  { key: "quantity", title: "Сдано / план, шт", numeric: true, value: (row) => Number(row.done_quantity),
    render: (row) => <span className={row.short ? "baseline-qty is-short" : "baseline-qty"}>{row.done_quantity} / {row.quantity}</span> },
  { key: "deviation", title: "Отклонение", grow: true,
    value: (row) => row.status === "Отменено" ? "Отменено" : deviates(row) ? [startWord(row), finishWord(row), row.short ? `Недодано ${row.shortfall} шт` : ""].filter(Boolean).join(", ") : "По плану",
    render: (row) => <DeviationMarks row={row} /> },
];

export function PlanBaseline({ canEdit }: { canEdit: boolean }) {
  const [data, setData] = useState<BaselineResult>(() => readBaseline(null));
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<unknown>(BASELINE_URL)
      .then((result) => setData(readBaseline(result)))
      .catch((reason) => setError(failureText(reason, "Базовый план не загружен")))
      .finally(() => setLoaded(true));
  }, []);

  async function fix() {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      setData(readBaseline(await api<unknown>(BASELINE_URL, { method: "POST" })));
    } catch (reason) {
      setError(failureText(reason, "Базовый план не зафиксирован"));
    } finally {
      setBusy(false);
    }
  }

  const header = data.baseline;
  const summary = useMemo(() => {
    const rows = data.rows;
    return {
      lateStart: rows.filter((row) => row.late_start).length,
      lateFinish: rows.filter((row) => row.late_finish).length,
      short: rows.filter((row) => row.short).length,
      onPlan: rows.filter((row) => !deviates(row)).length,
    };
  }, [data.rows]);

  return <section className="panel plan-baseline" aria-label="План и факт">
    <div className="plan-section-head">
      <h2>План и факт</h2>
      {header && <span className="baseline-meta">
        Базовый план №{header.number} от <b>{formatMoment(header.created_at)}</b> · {header.created_by} · заданий: {header.tasks}
        {header.outside > 0 && <span className="plan-tag soon" title="Запущены или получили сроки после фиксации — в снимке их нет">вне базового плана: {header.outside}</span>}
      </span>}
      {header && data.rows.length > 0 && <span className="baseline-summary" aria-label="Итог отклонений">
        <StatusChip tone={summary.lateStart ? "warning" : "neutral"}>Начато позже: {summary.lateStart}</StatusChip>
        <StatusChip tone={summary.lateFinish ? "danger" : "neutral"}>Закончено позже: {summary.lateFinish}</StatusChip>
        <StatusChip tone={summary.short ? "danger" : "neutral"}>Недодано: {summary.short}</StatusChip>
        <StatusChip tone="success">По плану: {summary.onPlan}</StatusChip>
      </span>}
      {canEdit && <button type="button" className="secondary small baseline-fix" disabled={busy || !loaded} onClick={() => void fix()}
        title="Снимок сроков и тиражей запущенных заданий на сейчас; прежние снимки остаются">
        {busy ? "Фиксирую…" : "Зафиксировать базовый план"}
      </button>}
    </div>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {!loaded ? <div className="empty">Загрузка…</div>
      : !header ? <div className="empty">Базовый план не зафиксирован — {canEdit ? "нажмите «Зафиксировать базовый план», и факт заданий сравнится с этим снимком" : "его фиксирует мастер планирования"}.</div>
        : <DataTable rows={data.rows} columns={columns} rowKey={(row) => row.task_id} label="Отклонения от базового плана"
          storageKey="production:baseline" presets={[{ label: "С отклонениями", test: deviates }]} defaultPreset="С отклонениями"
          rowClassName={(row) => deviates(row) ? "baseline-row-off" : ""} exportName={`План и факт №${header.number}`}
          emptyText="Отклонений нет: задания идут по базовому плану" compactBelow={15} />}
  </section>;
}
