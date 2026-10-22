import { useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent as ReactKeyboardEvent, type MouseEvent, type ReactNode } from "react";
import type { ProductionPlanRow, ProductionPlanSheetCell, ProductionPlanSheetField } from "./api";
import { AutoTextarea } from "./AutoTextarea";
import { matchesWords } from "./DataTable";
import { formatDate, formatDayMonth } from "./format";
import { ProjectStatus, planStatusApi } from "./ProjectStatus";
import { statusLabel, statusTone, type Tone } from "./statusTone";

/* Лист цеха «ЗАГРУЗ» в ERP (владелец 04.10: «тут вот такое представление я хочу»): строка — проект, слева
   закреплённые колонки листа, справа календарь по дням (или по неделям) с полосой плановых работ. Значения считает
   ERP, ручная отметка («Отметка плана») ставится поверх и видна уголком в ячейке. */

/** Группа листа — один этап из `stages` плана (порядок сервера) или «Закрытые» (статус проекта конечный). */
export const CLOSED_GROUP = "Закрытые";
export interface SheetGroupData { key: string; label: string; closed: boolean; rows: ProductionPlanRow[] }

export type SheetMark = { project: string; field: string; value?: string; text?: string };
/** Масштаб календаря: день — колонка, или неделя — колонка (взгляд на 2–3 месяца). */
export type SheetScale = "day" | "week";

const WEEKDAYS = ["вс", "пн", "вт", "ср", "чт", "пт", "сб"];
const MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"];
const MONTHS_OF = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];
const PROJECT_COLORS = 12;

/** Стабильный цвет проекта: хэш номера в палитру из 12 токенов (`--zg-c0…11`). */
export function projectColor(project: string) {
  let hash = 0;
  for (const char of project) hash = (hash * 31 + (char.codePointAt(0) ?? 0)) >>> 0;
  return hash % PROJECT_COLORS;
}

function pad(value: number) {
  return String(value).padStart(2, "0");
}

function isoDay(day: Date) {
  return `${day.getFullYear()}-${pad(day.getMonth() + 1)}-${pad(day.getDate())}`;
}

function parseDay(value: string) {
  const [year, month, day] = value.split("-").map(Number);
  return new Date(year, month - 1, day);
}

/** Период словами: «5–18 октября 2026», «3 октября – 9 декабря 2026», через год — год у обеих дат. */
export function periodWords(from: string, to: string) {
  const [fy, fm, fd] = from.split("-").map(Number);
  const [ty, tm, td] = to.split("-").map(Number);
  if (!fy || !ty) return "";
  if (fy !== ty) return `${fd} ${MONTHS_OF[fm - 1]} ${fy} – ${td} ${MONTHS_OF[tm - 1]} ${ty}`;
  if (fm === tm) return `${fd}–${td} ${MONTHS_OF[tm - 1]} ${ty}`;
  return `${fd} ${MONTHS_OF[fm - 1]} – ${td} ${MONTHS_OF[tm - 1]} ${ty}`;
}

export interface SheetDay { iso: string; label: string; day: number; weekday: string; off: boolean; monthStart: boolean }

/** Дни календаря листа от `from` до `to` включительно; выходные и праздники цеха — серые. */
export function sheetDays(from: string, to: string, holidays: string[] = []): SheetDay[] {
  const off = new Set(holidays);
  const days: SheetDay[] = [];
  const end = parseDay(to);
  for (let day = parseDay(from); day <= end; day = new Date(day.getFullYear(), day.getMonth(), day.getDate() + 1)) {
    const iso = isoDay(day);
    days.push({
      iso, label: `${pad(day.getDate())}.${pad(day.getMonth() + 1)}`, day: day.getDate(), weekday: WEEKDAYS[day.getDay()],
      off: day.getDay() === 0 || day.getDay() === 6 || off.has(iso), monthStart: day.getDate() === 1,
    });
  }
  return days;
}

/** Строка месяцев над днями: «сентябрь 2026», «октябрь»… — год у первого месяца и при смене года. */
export function sheetMonths(days: Pick<SheetDay, "iso">[]): Array<{ key: string; label: string; span: number }> {
  const months: Array<{ key: string; label: string; span: number }> = [];
  for (const day of days) {
    const key = day.iso.slice(0, 7);
    const last = months[months.length - 1];
    if (last?.key === key) { last.span += 1; continue; }
    const [year, month] = key.split("-").map(Number);
    const withYear = !last || last.key.slice(0, 4) !== key.slice(0, 4);
    months.push({ key, label: `${MONTHS[month - 1]}${withYear ? ` ${year}` : ""}`, span: 1 });
  }
  return months;
}

/** Неделя листа (пн–вс) внутри его периода; `days` — только дни, попавшие в период. */
export interface SheetWeek { iso: string; days: SheetDay[]; from: string; to: string }

/** Полоса «срок» строки: от более раннего из сегодня и первого рабочего дня до дедлайна включительно;
 *  просрочка (дедлайн раньше сегодня) — от дедлайна до сегодня, `late`. Без дедлайна — null. */
export function deadlineSpan(row: Pick<ProductionPlanRow, "deadline" | "calendar">, today: string): { from: string; to: string; late: boolean } | null {
  const deadline = row.deadline;
  if (!deadline) return null;
  if (deadline < today) return { from: deadline, to: today, late: true };
  const first = [...(row.calendar?.work ?? [])].sort()[0];
  return { from: first && first < today ? first : today, to: deadline, late: false };
}

/** Номер дня недели с понедельника: пн — 0 … вс — 6. */
function weekIndex(iso: string) {
  return (parseDay(iso).getDay() + 6) % 7;
}

/** Дни листа по неделям с понедельника; первая и последняя неделя могут быть неполными. */
export function sheetWeeks(days: SheetDay[]): SheetWeek[] {
  const weeks: SheetWeek[] = [];
  for (const day of days) {
    const last = weeks[weeks.length - 1];
    if (last && weekIndex(day.iso) !== 0) { last.days.push(day); last.to = day.iso; continue; }
    weeks.push({ iso: day.iso, days: [day], from: day.iso, to: day.iso });
  }
  return weeks;
}

/** Сдвиг календаря при открытии: сегодня — третьей колонкой слева (две до него видны), если сегодня в листе. */
export function todayScrollLeft(days: Pick<SheetDay, "iso">[], today: string, dayWidth: number, lead = 2) {
  const index = days.findIndex((day) => day.iso === today);
  return index < 0 || dayWidth <= 0 ? 0 : Math.max(0, (index - lead) * dayWidth);
}

/** Строки по этапам в порядке `stages`, закрытые проекты — последней группой. Пустые группы не возвращаются.
 *  Внутри группы: просроченные и горящие первыми (days_left по возрастанию, без срока — в конце), затем номер проекта
 *  с числовым сравнением («КБ9» раньше «КБ10»). Блокер строку из этапа не уводит. Этап вне списка — после известных. */
export function groupSheetRows(rows: ProductionPlanRow[], stages: string[], query = ""): SheetGroupData[] {
  const byKey = new Map<string, ProductionPlanRow[]>();
  const extra: string[] = [];
  const closed: ProductionPlanRow[] = [];
  for (const row of rows) {
    if (!matchesWords([row.project, row.customer], query)) continue;
    if (row.terminal) { closed.push(row); continue; }
    if (!stages.includes(row.stage) && !extra.includes(row.stage)) extra.push(row.stage);
    byKey.set(row.stage, [...(byKey.get(row.stage) ?? []), row]);
  }
  const order = (a: ProductionPlanRow, b: ProductionPlanRow) =>
    (a.days_left ?? 1e9) - (b.days_left ?? 1e9) || a.project.localeCompare(b.project, "ru", { numeric: true });
  const result: SheetGroupData[] = [...stages, ...extra]
    .map((stage) => ({ key: stage, label: stage || "Без этапа", closed: false, rows: (byKey.get(stage) ?? []).sort(order) }))
    .filter((group) => group.rows.length > 0);
  if (closed.length) result.push({ key: CLOSED_GROUP, label: CLOSED_GROUP, closed: true, rows: closed.sort(order) });
  return result;
}

const MARK_LABELS: Record<string, string> = {
  comment: "Комментарий", status: "Статус", ready: "Готовность", in_plan: "В плане", customer_supplied: "Давальческое",
  kit_checked: "Комплект проверен", kit_present: "Комплект на складе", zkp: "Закуплено", kit_arrival: "Приход комплектующих",
  tc_ready: "Техкарта выдана", test_sb: "Тестовая сборка", kit_picked: "Комплект набран",
};

/** Текст записи «Отметка плана» в истории проекта: «Статус: ХОЛД», «Отметка 06.10: П», «В плане: расчёт ERP». */
export function markText(event: { field?: string; value: string; text: string }) {
  const field = event.field ?? "";
  const label = field.startsWith("day:") ? `Отметка ${formatDate(field.slice(4))}` : MARK_LABELS[field] ?? field;
  const value = field === "comment" ? event.text : event.value;
  if (!value || value === "авто") return `${label}: ${field === "comment" || field.startsWith("day:") ? "очищено" : "расчёт ERP"}`;
  return `${label}: ${/^\d{4}-\d{2}-\d{2}$/.test(value) ? formatDate(value) : field === "status" ? statusList(value).map(statusLabel).join(", ") : value}`;
}

export function statusList(value: string) {
  return value.split(",").map((item) => item.trim()).filter(Boolean);
}

// Главный статус строки — самый тревожный: стоп и ожидание раньше «в работе», «готово» и «новый».
const TONE_ORDER: Record<Tone, number> = { danger: 0, warning: 1, info: 2, success: 3, neutral: 4 };

/** Статусы ячейки по тревожности (стоп, ожидание, в работе, готово, новое); равные — в порядке листа. */
export function orderStatuses(value: string) {
  return statusList(value)
    .map((word, index) => ({ word, index, rank: TONE_ORDER[statusTone(word)] }))
    .sort((a, b) => a.rank - b.rank || a.index - b.index)
    .map((item) => item.word);
}

// Ширина подписи чипа (12 px, полужирный): заглавные шире строчных; оценка с запасом, чтобы слово не резалось.
function chipTextWidth(text: string) {
  let width = 0;
  for (const char of text) {
    if (char === " ") width += 3.6;
    else if (/\d/.test(char)) width += 7.2;
    else if (char !== char.toLowerCase()) width += 9.2;
    else if (char !== char.toUpperCase()) width += 7.2;
    else width += 5;
  }
  return width;
}

export const STATUS_WIDTH = { min: 84, max: 260, caret: 14 };

/** Ширина колонки «Статус» по самому длинному этапу и блокеру рядом с ним (чип со стрелкой меню): слово не обрезается. */
export function statusColumnWidth(rows: Pick<ProductionPlanRow, "stage" | "blocker">[]) {
  let widest = 0;
  for (const row of rows) {
    const stage = chipTextWidth(statusLabel(row.stage)) + 16 + STATUS_WIDTH.caret;
    const blocker = row.blocker ? chipTextWidth(statusLabel(row.blocker.type)) + 16 + 6 : 0;
    widest = Math.max(widest, Math.ceil(stage + blocker + 10));
  }
  return Math.min(STATUS_WIDTH.max, Math.max(STATUS_WIDTH.min, widest));
}

/** Количество запуска переезжает с выходного на первый рабочий день той же полосы работ: на сером его не видно. */
export function startLabels(days: SheetDay[], work: Set<string>, starts: Record<string, number>) {
  const labels: Record<string, number> = {};
  for (const [iso, quantity] of Object.entries(starts)) {
    const index = days.findIndex((day) => day.iso === iso);
    if (index < 0) continue;
    let at = index;
    while (at < days.length && days[at].off && work.has(days[at].iso)) at += 1;
    const target = at < days.length && work.has(days[at].iso) ? days[at].iso : iso;
    labels[target] = (labels[target] ?? 0) + quantity;
  }
  return labels;
}

/** Рабочих дней до дедлайна (владелец 14.10) — по графику смены и праздникам календаря цеха; без них — календарные. */
function workDays(row: ProductionPlanRow): number | null {
  return row.work_days_left ?? row.days_left;
}

function daysTone(days: number | null) {
  if (days === null) return "";
  if (days < 0) return "late";
  if (days <= 5) return "soon";
  return "ok";
}

export type ColumnKey = ProductionPlanSheetField | "project" | "customer" | "days_left" | "deadline";
/** `label` — короткая подпись в одну строку; `name` — полное название колонки (подсказка, «Колонки», доступное имя).
    `flag` — да/нет-отметка (узкая колонка с ✓ / —); `fold` — очередь скрытия на узком экране (1 — первой). */
interface Column { key: ColumnKey; label: string; name: string; width: number; left?: boolean; hint?: string; flag?: boolean; fold?: number }

// Владелец 14.10: «норм сортировку столбцов и без сокращений». Подписи — полные названия (в шапке в 2–3 строки), порядок —
// проект и его состояние, затем путь комплектующих и сборки по порядку (давальческое → закуплено → приход → на складе →
// проверен → техкарта → набран → тестовая сборка), сроки — рядом с календарём. «Комментарий» в конце закреплённых колонок и
// скрывается первым (аудит 10.10, ПП-8) — тогда комментарий виден отметкой в ячейке «Проект» с подсказкой. Все показанные
// колонки закреплены; календарь начинается сразу за ними. На узком экране реже нужные колонки скрыты, чтобы календарь
// показал пять недель; «Колонки» возвращают любую, порядок сохраняется. Ширина — по самому длинному слову подписи.
const COLUMNS: Column[] = [
  { key: "project", label: "Проект", name: "Проект", width: 80, left: true },
  { key: "customer", label: "Заказчик", name: "Заказчик", width: 140, left: true },
  { key: "status", label: "Статус", name: "Статус", width: 104, left: true },
  { key: "ready", label: "Готовность, %", name: "Готовность, %", width: 76, fold: 8, hint: "выпуск против количества задания, по норме" },
  { key: "in_plan", label: "В плане", name: "В плане", width: 48, flag: true, fold: 4, hint: "на проект назначены операции" },
  { key: "customer_supplied", label: "Давальческое", name: "Давальческое", width: 82, fold: 5, hint: "давальческие комплектующие: НЕТ, ДА или Ч/Т — частично" },
  { key: "zkp", label: "Закуплено", name: "Закуплено", width: 66, flag: true, fold: 3, hint: "авто: все покупные позиции оплачены — в пути или уже пришли (давальческие не в счёт)" },
  { key: "kit_arrival", label: "Приход комплектующих", name: "Приход комплектующих", width: 100, hint: "когда придут комплектующие" },
  { key: "kit_present", label: "Комплект на складе", name: "Комплект на складе", width: 60, flag: true, hint: "все комплектующие на складе" },
  { key: "kit_checked", label: "Комплект проверен", name: "Комплект проверен", width: 62, flag: true, fold: 7 },
  { key: "tc_ready", label: "Техкарта выдана", name: "Техкарта выдана", width: 60, flag: true, fold: 9, hint: "на каждую КБ выдана ТК" },
  { key: "kit_picked", label: "Комплект набран", name: "Комплект набран", width: 60, flag: true, hint: "каждая КБ набрана" },
  { key: "test_sb", label: "Тестовая сборка", name: "Тестовая сборка", width: 60, flag: true, fold: 6 },
  { key: "deadline", label: "Дедлайн", name: "Дедлайн", width: 58 },
  { key: "days_left", label: "Рабочих дней до дедлайна", name: "Рабочих дней до дедлайна", width: 72, fold: 2 },
  { key: "comment", label: "Комментарий", name: "Комментарий", width: 160, left: true, fold: 1 },
];
const FOLD_ORDER = COLUMNS.filter((column) => column.fold).sort((a, b) => a.fold! - b.fold!);
/** Сколько дней календаря должно быть видно рядом с закреплёнными колонками без прокрутки (пять недель). */
export const MIN_VISIBLE_DAYS = 35;
/** То же в масштабе «Недели». */
export const MIN_VISIBLE_WEEKS = 8;
const COLUMNS_KEY = "erp-zg-columns";
const SCALE_KEY = "erp-zg-scale";
const COLLAPSED_KEY = "erp-zg-collapsed";

/** Ширина колонки: «Статус» — по самому длинному слову; на планшете отметки шире — под палец. */
function columnWidth(column: Column, coarse: boolean, statusWidth = column.width) {
  if (column.key === "status") return statusWidth;
  return coarse && column.flag ? Math.max(column.width, 42) : column.width;
}

/** Какие колонки скрыть по умолчанию, чтобы при ширине листа `available` календарь показал `minVisible` колонок. */
export function autoHiddenColumns(available: number, dayWidth: number, coarse = false, statusWidth = 104, minVisible = MIN_VISIBLE_DAYS): ColumnKey[] {
  if (available <= 0 || dayWidth <= 0) return [];
  let frozenWidth = COLUMNS.reduce((sum, column) => sum + columnWidth(column, coarse, statusWidth), 0);
  const hidden: ColumnKey[] = [];
  for (const column of FOLD_ORDER) {
    if (frozenWidth + minVisible * dayWidth <= available) break;
    hidden.push(column.key);
    frozenWidth -= columnWidth(column, coarse, statusWidth);
  }
  return hidden;
}

function readStored<T>(key: string, parse: (value: unknown) => T | null): T | null {
  try {
    return parse(JSON.parse(window.localStorage.getItem(key) ?? "null") as unknown);
  } catch {
    return null;
  }
}

function writeStored(key: string, value: unknown) {
  try {
    if (value === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, JSON.stringify(value));
  } catch { /* хранилище недоступно — выбор живёт до перезагрузки */ }
}

const readChosen = () => readStored<ColumnKey[]>(COLUMNS_KEY, (saved) => Array.isArray(saved)
  ? saved.filter((key): key is ColumnKey => COLUMNS.some((column) => column.key === key)) : null);
const readCollapsed = () => readStored<string[]>(COLLAPSED_KEY, (saved) => Array.isArray(saved) ? saved.filter((item): item is string => typeof item === "string") : null) ?? [];
const readScale = () => readStored<SheetScale>(SCALE_KEY, (saved) => saved === "week" ? "week" : null) ?? "day";

function isCoarse() {
  try {
    return window.matchMedia?.("(pointer: coarse)").matches ?? false;
  } catch {
    return false;
  }
}

/** Подсказка к шапке: полное название колонки и, где нужно, что она значит. */
export function columnTitle(column: Pick<Column, "name" | "hint">) {
  return column.hint ? `${column.name} — ${column.hint}` : column.name;
}

/** Показанные колонки, их ширины и отступы слева для закрепления. */
interface Layout { columns: Column[]; widths: number[]; lefts: number[]; width: number; hidden: Set<ColumnKey> }

function sheetLayout(hidden: ColumnKey[], coarse: boolean, statusWidth: number): Layout {
  const columns = COLUMNS.filter((column) => !hidden.includes(column.key));
  const lefts: number[] = [];
  const widths: number[] = [];
  let width = 0;
  for (const column of columns) {
    lefts.push(width);
    widths.push(columnWidth(column, coarse, statusWidth));
    width += widths[widths.length - 1];
  }
  return { columns, widths, lefts, width, hidden: new Set(hidden) };
}

type Editing = { project: string; field: string; top: number; left: number };

export function PlanLoadSheet({ rows, from, to, today, holidays, stages, suppliedValues, arrivalValues, canEdit, busy, onStatusChanged, onMark, onDeadline, onOpenProject, onOpenJournal, hasCard }: {
  rows: ProductionPlanRow[];
  from: string;
  to: string;
  today: string;
  holidays: string[];
  /** Этапы проекта по порядку — из плана (`stages`); по ним строятся группы листа. */
  stages: string[];
  suppliedValues: string[];
  arrivalValues: string[];
  canEdit: boolean;
  busy: boolean;
  /** Статус проекта сменили в меню ячейки «Статус» — перечитать план. */
  onStatusChanged: () => void;
  onMark: (mark: SheetMark) => Promise<void>;
  onDeadline: (project: string, deadline: string) => Promise<void>;
  onOpenProject: (row: ProductionPlanRow) => void;
  onOpenJournal: (row: ProductionPlanRow) => void;
  /** Есть ли у проекта карточка в «Проектах» (подсказка к №); по умолчанию — есть КБ. */
  hasCard?: (row: ProductionPlanRow) => boolean;
}) {
  const [query, setQuery] = useState("");
  const [showClosed, setShowClosed] = useState(false);
  const [showShipped, setShowShipped] = useState(false);
  const [collapsed, setCollapsed] = useState<string[]>(readCollapsed);
  const [editing, setEditing] = useState<Editing | null>(null);
  const [choosing, setChoosing] = useState(false);
  const [chosen, setChosen] = useState<ColumnKey[] | null>(readChosen);
  const [scale, setScale] = useState<SheetScale>(readScale);
  const [space, setSpace] = useState({ available: 0, day: 0 });
  const [coarse] = useState(isCoarse);
  const wrapRef = useRef<HTMLDivElement>(null);
  const days = useMemo(() => sheetDays(from, to, holidays), [from, to, holidays]);
  const weeks = useMemo(() => sheetWeeks(days), [days]);
  const months = useMemo(() => sheetMonths(scale === "week" ? weeks : days), [scale, weeks, days]);
  const groups = useMemo(() => groupSheetRows(rows, stages, query), [rows, stages, query]);
  const statusWidth = useMemo(() => statusColumnWidth(rows), [rows]);
  const hidden = chosen ?? autoHiddenColumns(space.available, space.day, coarse, statusWidth, scale === "week" ? MIN_VISIBLE_WEEKS : MIN_VISIBLE_DAYS);
  const layout = useMemo(() => sheetLayout(hidden, coarse, statusWidth), [hidden.join(","), coarse, statusWidth]); // eslint-disable-line react-hooks/exhaustive-deps
  const units = scale === "week" ? weeks.length : days.length;

  // Ширина листа решает, какие колонки скрыть по умолчанию; меряем до отрисовки, чтобы не мигало.
  useLayoutEffect(() => {
    const wrap = wrapRef.current;
    if (!wrap) return;
    const measure = () => {
      const day = wrap.querySelector<HTMLElement>("th.zg-day-head")?.getBoundingClientRect().width ?? 0;
      setSpace((current) => current.available === wrap.clientWidth && current.day === day ? current : { available: wrap.clientWidth, day });
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(wrap);
    return () => observer.disconnect();
  }, [scale]);

  // При открытии календарь стоит на сегодня: оно третьей колонкой справа от закреплённых (неделя — второй).
  useLayoutEffect(() => {
    const wrap = wrapRef.current;
    const width = wrap?.querySelector<HTMLElement>("th.zg-day-head")?.getBoundingClientRect().width ?? 0;
    if (!wrap) return;
    wrap.scrollLeft = scale === "week"
      ? todayScrollLeft(weeks.map((week) => ({ iso: week.days.some((day) => day.iso === today) ? today : week.iso })), today, width, 1)
      : todayScrollLeft(days, today, width);
  }, [from, to, today, scale]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!editing) return;
    const close = (event: KeyboardEvent) => { if (event.key === "Escape") setEditing(null); };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [editing]);

  function open(event: MouseEvent<HTMLElement>, project: string, field: string) {
    if (!canEdit) return;
    const rect = event.currentTarget.getBoundingClientRect();
    setEditing({
      project, field, top: Math.min(rect.bottom + 2, window.innerHeight - 260),
      left: Math.max(8, Math.min(rect.left, window.innerWidth - 300)),
    });
  }

  async function mark(project: string, field: string, value: string, text = "") {
    setEditing(null);
    await onMark({ project, field, value, text });
  }

  function choose(next: ColumnKey[] | null) {
    setChosen(next);
    writeStored(COLUMNS_KEY, next);
  }

  function pickScale(next: SheetScale) {
    setScale(next);
    writeStored(SCALE_KEY, next === "week" ? next : null);
  }

  const closedCount = groups.find((group) => group.closed)?.rows.length ?? 0;
  const lastStage = stages[stages.length - 1];
  // Отгруженные не мозолят глаза (владелец 12.10): группа последнего этапа скрыта, пока не включён переключатель.
  const shippedCount = groups.find((group) => !group.closed && group.key === lastStage)?.rows.length ?? 0;
  const visible = groups.filter((group) => group.closed ? showClosed : (group.key !== lastStage || showShipped));
  const shown = visible.reduce((count, group) => count + group.rows.length, 0);

  function pickShipped(on: boolean) {
    setShowShipped(on);
    // Показанная группа отгруженных открывается свёрнутой.
    if (on && lastStage && !collapsed.includes(lastStage)) {
      const next = [...collapsed, lastStage];
      setCollapsed(next);
      writeStored(COLLAPSED_KEY, next);
    }
  }

  function toggleGroup(key: string) {
    const next = collapsed.includes(key) ? collapsed.filter((item) => item !== key) : [...collapsed, key];
    setCollapsed(next);
    writeStored(COLLAPSED_KEY, next.length ? next : null);
  }
  const row = editing ? rows.find((item) => item.project === editing.project) : undefined;
  const span = layout.columns.length + units;
  const cardOf = hasCard ?? ((item: ProductionPlanRow) => item.kits.length > 0);

  return <div className="zg">
    <div className="zg-toolbar">
      <input aria-label="Поиск проекта" placeholder="№ проекта или заказчик…" value={query} onChange={(event) => setQuery(event.target.value)} />
      {shippedCount > 0 && <label className="zg-toggle"><input type="checkbox" checked={showShipped} onChange={(event) => pickShipped(event.target.checked)} />Показать отгруженные · {shippedCount}</label>}
      {closedCount > 0 && <label className="zg-toggle"><input type="checkbox" checked={showClosed} onChange={(event) => setShowClosed(event.target.checked)} />Закрытые проекты · {closedCount}</label>}
      <div className="zg-scale" role="group" aria-label="Масштаб календаря">
        <button type="button" className={scale === "day" ? "on" : undefined} aria-pressed={scale === "day"} onClick={() => pickScale("day")}>Дни</button>
        <button type="button" className={scale === "week" ? "on" : undefined} aria-pressed={scale === "week"} onClick={() => pickScale("week")}>Недели</button>
      </div>
      <span className="zg-legend" role="list" aria-label="Обозначения">
        <span role="listitem" title="Плановые работы по проекту; цвет полосы — цвет проекта, как полоска у №"><i className="zg-key-bar" />работы, цвет — проект</span>
        <span role="listitem" title="Срок: от сегодня (или первого рабочего дня) до дедлайна в цвете проекта; просрочка — красным от дедлайна до сегодня"><i className="zg-key-span" />срок до дедлайна</span>
        <span role="listitem" title="Количество запуска в штуках — на первом рабочем дне полосы"><i className="zg-key-qty">12</i>запуск, шт</span>
        <span role="listitem" title="День прихода комплектующих"><i className="zg-key-arrival" />приход</span>
        <span role="listitem" title="Дедлайн проекта — красная черта в конце дня"><i className="zg-key-deadline" />дедлайн</span>
        {scale === "day" && <span role="listitem" title="Выходной или праздник цеха"><i className="zg-key-off" />выходной</span>}
        <span role="listitem" title={scale === "day" ? "Сегодняшний день" : "Текущая неделя"}><i className="zg-key-today" />{scale === "day" ? "сегодня" : "эта неделя"}</span>
        <span role="listitem" title="Значение поставлено вручную поверх расчёта ERP"><i className="zg-key-manual" />вручную</span>
        {layout.hidden.has("comment") && <span role="listitem" title="У проекта есть комментарий — текст в подсказке к №"><i className="zg-key-note" />комментарий</span>}
      </span>
      <button type="button" className={`secondary small zg-columns${layout.hidden.size ? " active" : ""}`} aria-expanded={choosing} onClick={() => setChoosing((current) => !current)}
        title={layout.hidden.size ? `Скрыто: ${COLUMNS.filter((column) => layout.hidden.has(column.key)).map((column) => column.name).join(", ")}` : undefined}>
        Колонки{layout.hidden.size ? ` · ${layout.columns.length} из ${COLUMNS.length}` : ""}
      </button>
    </div>
    {choosing && <div className="column-chooser zg-chooser" role="group" aria-label="Показывать колонки">
      {COLUMNS.map((column) => <label key={column.key} className="check-inline" title={columnTitle(column)}>
        <input type="checkbox" checked={!layout.hidden.has(column.key)} disabled={column.key === "project"}
          onChange={(event) => choose(event.target.checked ? hidden.filter((key) => key !== column.key) : [...hidden, column.key])} />
        {column.name}
      </label>)}
      <button type="button" className="link-button" disabled={!chosen} onClick={() => choose(null)}>По ширине экрана</button>
    </div>}
    <div ref={wrapRef} className={`zg-wrap${shown ? "" : " zg-wrap-empty"}`}>
      <table className={`zg-table${scale === "week" ? " zg-weeks" : ""}`} aria-label="Загрузка цеха по проектам"
        style={{ width: `calc(${layout.width}px + ${units} * var(${scale === "week" ? "--zg-week" : "--zg-day"}))`, "--zg-frozen": `${layout.width}px` } as CSSProperties}>
        <colgroup>
          {layout.columns.map((column, index) => <col key={column.key} style={{ width: layout.widths[index] }} />)}
          {scale === "week"
            ? weeks.map((week) => <col key={week.iso} className="zg-week-col" />)
            : days.map((day) => <col key={day.iso} className="zg-day-col" />)}
        </colgroup>
        <thead>
          <tr className="zg-head-months">
            {layout.columns.map((column, index) => <th key={column.key} rowSpan={2} title={columnTitle(column)}
              {...frozen(layout, index, `zg-col-head${column.left ? " zg-left" : ""}`)}>{column.label}</th>)}
            {months.map((month, index) => <th key={month.key} colSpan={month.span} className={`zg-month-head${index ? " zg-month" : ""}`}><span>{month.label}</span></th>)}
          </tr>
          <tr className="zg-head-days">
            {scale === "week"
              ? weeks.map((week, index) => {
                const current = week.days.some((day) => day.iso === today);
                return <th key={week.iso} className={["zg-day-head zg-week-head", current && "zg-today", weekMonthStart(weeks, index) && "zg-month"].filter(Boolean).join(" ")}
                  title={`Неделя ${formatDate(week.from)} – ${formatDate(week.to)}${current ? " · текущая" : ""}`}>
                  <b>{formatDayMonth(week.from)}</b><span>–{formatDayMonth(week.to)}</span></th>;
              })
              : days.map((day) => <th key={day.iso} className={dayClass(day, today, "zg-day-head")}
                title={`${day.weekday} ${formatDate(day.iso)}${day.iso === today ? " · сегодня" : day.off ? " · выходной" : ""}`}><b>{day.day}</b><span>{day.weekday}</span></th>)}
          </tr>
        </thead>
        <tbody>
          {visible.map((group) => <SheetGroupRows key={group.key} group={group} showSpan={!group.closed && group.key !== lastStage}
            folded={collapsed.includes(group.key)} onToggle={() => toggleGroup(group.key)}
            layout={layout} days={days} weeks={scale === "week" ? weeks : null} units={units} today={today} canEdit={canEdit} busy={busy} suppliedValues={suppliedValues} onStatusChanged={onStatusChanged}
            open={open} mark={mark} onOpenProject={onOpenProject} onOpenJournal={onOpenJournal} hasCard={cardOf} />)}
          {!shown && <tr className="zg-empty-row"><td colSpan={span} className="zg-empty">
            <span className="zg-empty-text">{query ? "Под этот поиск проектов нет." : "Проектов в плане нет — заведите проект в «Проектах»."}</span>
          </td></tr>}
        </tbody>
      </table>
    </div>
    {editing && row && <>
      <div className="zg-scrim" onClick={() => setEditing(null)} />
      <div className="zg-pop" role="dialog" aria-label={`Правка: ${editing.project}`} style={{ top: editing.top, left: editing.left }}>
        <SheetEditor row={row} field={editing.field} busy={busy} arrivalValues={arrivalValues}
          mark={mark} onDeadline={async (value) => { setEditing(null); await onDeadline(row.project, value); }}
          close={() => setEditing(null)} />
      </div>
    </>}
  </div>;
}

/** Неделя открывает новый месяц строки месяцев (месяц недели — по её первому дню в листе). */
function weekMonthStart(weeks: SheetWeek[], index: number) {
  return index > 0 && weeks[index - 1].iso.slice(0, 7) !== weeks[index].iso.slice(0, 7);
}

function frozen(layout: Layout, index: number, extra = ""): { className: string; style: CSSProperties } {
  const classes = ["zg-freeze", extra];
  if (index === layout.columns.length - 1) classes.push("zg-edge");
  return { className: classes.filter(Boolean).join(" "), style: { "--zg-left": `${layout.lefts[index]}px` } as CSSProperties };
}

function dayClass(day: SheetDay, today: string, base: string) {
  return [base, day.off && "zg-off", day.iso === today && "zg-today", day.monthStart && "zg-month"].filter(Boolean).join(" ");
}

function SheetGroupRows({ group, showSpan, folded, onToggle, layout, days, weeks, units, today, canEdit, busy, suppliedValues, onStatusChanged, open, mark, onOpenProject, onOpenJournal, hasCard }: {
  group: SheetGroupData;
  /** Полоса плановых работ нужна только открытым проектам не на последнем этапе. */
  showSpan: boolean;
  folded: boolean;
  onToggle: () => void;
  layout: Layout;
  days: SheetDay[];
  weeks: SheetWeek[] | null;
  units: number;
  today: string;
  canEdit: boolean;
  busy: boolean;
  suppliedValues: string[];
  open: (event: MouseEvent<HTMLElement>, project: string, field: string) => void;
  mark: (project: string, field: string, value: string, text?: string) => Promise<void>;
  onOpenProject: (row: ProductionPlanRow) => void;
  onOpenJournal: (row: ProductionPlanRow) => void;
  onStatusChanged: () => void;
  hasCard: (row: ProductionPlanRow) => boolean;
}) {
  const { rows } = group;
  return <>
    <tr className={`zg-group${group.closed ? " zg-group-closed" : ""}`}><td colSpan={layout.columns.length + units}>
      <button type="button" className="zg-group-label" aria-expanded={!folded} onClick={onToggle}>{group.label} · {rows.length}</button>
    </td></tr>
    {!folded && rows.map((row) => <tr key={row.project} className={`zg-row${group.closed ? " zg-row-closed" : ""}`} data-project={row.project}
      style={{ "--zg-color": `var(--zg-c${projectColor(row.project)})` } as CSSProperties}>
      {layout.columns.map((column, index) => <SheetCellView key={column.key} column={column} index={index} layout={layout} row={row}
        canEdit={canEdit} busy={busy} suppliedValues={suppliedValues} onStatusChanged={onStatusChanged} open={open} mark={mark}
        onOpenProject={onOpenProject} onOpenJournal={onOpenJournal} hasCard={hasCard} />)}
      {weeks ? <WeekCells row={row} weeks={weeks} today={today} showSpan={showSpan} /> : <CalendarCells row={row} days={days} today={today} showSpan={showSpan} canEdit={canEdit} open={open} />}
    </tr>)}
  </>;
}

function manualTitle(cell: ProductionPlanSheetCell | undefined, shown: (value: string) => string = (value) => value) {
  if (!cell?.manual) return undefined;
  return `Отмечено вручную. Расчёт ERP: ${shown(cell.auto) || "пусто"}`;
}

function suppliedTone(value: string) {
  return value === "Ч/Т" ? "part" : value === "ДА" ? "yes" : "no";
}

function arrivalTone(value: string, late: boolean) {
  if (late) return "zg-late-text";
  if (value === "Ждем") return "zg-arrival-wait";
  if (value === "???") return "zg-arrival-unknown";
  if (value === "Склад") return "zg-arrival-stock";
  return "";
}

function SheetCellView({ column, index, layout, row, canEdit, busy, suppliedValues, onStatusChanged, open, mark, onOpenProject, onOpenJournal, hasCard }: {
  column: Column;
  index: number;
  layout: Layout;
  row: ProductionPlanRow;
  canEdit: boolean;
  busy: boolean;
  suppliedValues: string[];
  open: (event: MouseEvent<HTMLElement>, project: string, field: string) => void;
  mark: (project: string, field: string, value: string, text?: string) => Promise<void>;
  onOpenProject: (row: ProductionPlanRow) => void;
  onOpenJournal: (row: ProductionPlanRow) => void;
  onStatusChanged: () => void;
  hasCard: (row: ProductionPlanRow) => boolean;
}) {
  const key = column.key;
  const cell = key in (row.sheet ?? {}) ? row.sheet?.[key as ProductionPlanSheetField] : undefined;
  const manual = (key === "status" ? row.stage_manual : cell?.manual) ? "zg-manual" : "";
  const editable = canEdit ? "zg-edit" : "";
  const td = (content: ReactNode, extra = "", props: Record<string, unknown> = {}) => {
    const base = frozen(layout, index, [`zg-col-${key}`, manual, extra].filter(Boolean).join(" "));
    // Правимая ячейка доступна с клавиатуры: Tab — к ней, Enter или пробел — правка.
    const onClick = props.onClick as ((event: MouseEvent<HTMLElement>) => void) | undefined;
    const keys = canEdit && onClick ? {
      tabIndex: 0,
      onKeyDown: (event: ReactKeyboardEvent<HTMLElement>) => {
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        onClick({ currentTarget: event.currentTarget } as MouseEvent<HTMLElement>);
      },
    } : {};
    return <td {...base} {...props} {...keys}>{content}</td>;
  };
  const dash = <span className="zg-empty-mark">—</span>;

  if (key === "project") {
    // «Комментарий» скрыт — он виден отметкой у номера, текст — в подсказке.
    const note = layout.hidden.has("comment") ? row.sheet?.comment?.value ?? "" : "";
    const action = "Открыть проект в ресурсном планировании";
    return td(<button className="zg-project" title={note ? `${action}\nКомментарий: ${note}` : action} onClick={() => onOpenProject(row)}>
      {row.project}{note && <i className="zg-note-mark" aria-hidden />}
    </button>, note ? "zg-has-note" : "");
  }
  if (key === "customer") {
    return td(<button className="zg-link" title={row.customer ? `${row.customer} — журнал проекта` : "Журнал проекта"} onClick={() => onOpenJournal(row)}>{row.customer || "—"}</button>);
  }
  if (key === "days_left") {
    const days = workDays(row);
    return td(days ?? "", `zg-days ${daysTone(days)}`, { title: days === null ? "Без дедлайна" : `Рабочих дней до дедлайна: ${days}${row.days_left !== null ? ` (календарных ${row.days_left})` : ""}` });
  }
  if (key === "deadline") {
    // Колонка «Дн.» скрыта — светофор дедлайна переезжает в «Дедлайн».
    const tone = layout.hidden.has("days_left") ? `zg-days ${daysTone(workDays(row))}` : "";
    return td(row.deadline ? formatDayMonth(row.deadline) : dash, `zg-center ${tone} ${editable}`,
      { title: row.deadline ? `Дедлайн ${formatDate(row.deadline)}${workDays(row) !== null ? ` · рабочих дней до дедлайна: ${workDays(row)}` : ""}` : "Без дедлайна", onClick: (event: MouseEvent<HTMLElement>) => open(event, row.project, "deadline") });
  }
  const value = cell?.value ?? "";
  if (column.flag) {
    const checked = value === "да";
    return td(<button type="button" role="checkbox" aria-checked={checked} aria-label={`${column.name} · ${row.project}`}
      className={`zg-flag ${checked ? "on" : "off"}`} disabled={!canEdit || busy}
      onClick={() => {
        const next = checked ? "нет" : "да";
        // Вернули расчётное значение — ручная отметка снимается.
        void mark(row.project, key, next === cell?.auto ? "авто" : next);
      }}>{checked ? "✓" : "—"}</button>, "zg-center", { title: manualTitle(cell) ?? columnTitle(column) });
  }
  if (key === "customer_supplied") {
    const shownValue = <span className={`zg-supplied s-${suppliedTone(value)}`} aria-hidden={canEdit || undefined}>{value && value !== "НЕТ" ? value : "—"}</span>;
    return td(canEdit
      ? <>{shownValue}<select aria-label={`${column.name} · ${row.project}`} className="zg-overlay"
        value={cell?.manual ? value : "авто"} disabled={busy}
        onChange={(event) => void mark(row.project, key, event.target.value === cell?.auto ? "авто" : event.target.value)}>
        <option value="авто">{cell?.auto ?? "НЕТ"}</option>
        {suppliedValues.filter((item) => item !== cell?.auto).map((item) => <option key={item} value={item}>{item}</option>)}
      </select></>
      : shownValue,
    "zg-center", { title: manualTitle(cell) ?? `${columnTitle(column)}: ${value || "НЕТ"}` });
  }
  const click = { onClick: (event: MouseEvent<HTMLElement>) => open(event, row.project, key) };
  if (key === "status") {
    // Тот же чип и меню, что в «Проектах»: этап, блокер, закрытие проекта; расчётные слова листа — в подсказке.
    const words = orderStatuses(value).map(statusLabel).join(", ");
    return td(<ProjectStatus project={row.project} status={row.stage} blocker={row.blocker?.type ?? ""} canEdit={canEdit && !busy}
      api={planStatusApi} floating onChanged={onStatusChanged} />,
    "", { title: [words, row.stage_manual ? "Этап поставлен вручную" : "", row.blocker ? `${row.blocker.type}: ${row.blocker.text}` : ""].filter(Boolean).join(" — ") || undefined });
  }
  if (key === "ready") {
    const percent = Math.max(0, Math.min(100, Number(value) || 0));
    return td(value === "" ? dash : <span className={`zg-ready${percent >= 100 ? " full" : percent === 0 ? " zero" : ""}`}>
      <b>{value}%</b><i style={{ "--zg-ready": `${percent}%` } as CSSProperties} />
    </span>, editable, {
      title: manualTitle(cell, (auto) => auto && `${auto}%`) ?? "Выпуск против количества задания, по норме", ...click,
    });
  }
  if (key === "kit_arrival") {
    const date = /^\d{4}-\d{2}-\d{2}$/.test(value);
    const late = date && row.deadline !== null && value > row.deadline;
    return td(date ? formatDayMonth(value) : value || dash, `zg-center ${arrivalTone(value, late)} ${editable}`, {
      title: manualTitle(cell, (auto) => /^\d{4}-\d{2}-\d{2}$/.test(auto) ? formatDate(auto) : auto)
        ?? (late ? `Приход ${formatDate(value)} — позже дедлайна` : date ? `Приход комплектующих ${formatDate(value)}` : columnTitle(column)), ...click,
    });
  }
  // comment
  return td(<span className="zg-ellipsis">{value}</span>, editable, { title: value || undefined, ...click });
}

function CalendarCells({ row, days, today, showSpan, canEdit, open }: {
  row: ProductionPlanRow;
  days: SheetDay[];
  today: string;
  showSpan: boolean;
  canEdit: boolean;
  open: (event: MouseEvent<HTMLElement>, project: string, field: string) => void;
}) {
  const work = new Set(row.calendar?.work ?? []);
  const starts = row.calendar?.starts ?? {};
  const labels = startLabels(days, work, starts);
  const notes = row.calendar?.notes ?? {};
  const arrival = row.sheet?.kit_arrival?.value ?? "";
  const span = showSpan ? deadlineSpan(row, today) : null;
  return <>{days.map((day, index) => {
    const inSpan = Boolean(span && day.iso >= span.from && day.iso <= span.to);
    const busy = work.has(day.iso);
    const classes = [dayClass(day, today, "zg-day")];
    if (busy) {
      classes.push("zg-work");
      if (!work.has(days[index - 1]?.iso ?? "")) classes.push("zg-work-start");
      if (!work.has(days[index + 1]?.iso ?? "")) classes.push("zg-work-end");
    }
    if (row.deadline === day.iso) classes.push("zg-deadline");
    if (arrival === day.iso) classes.push("zg-arrival");
    if (canEdit) classes.push("zg-edit");
    const titles = [
      `${row.project} · ${day.weekday} ${formatDate(day.iso)}${day.off ? " · выходной" : ""}`,
      inSpan && row.deadline && `срок до ${formatDayMonth(row.deadline)}${span!.late ? " · просрочен" : ""}`,
      busy && "плановые работы", starts[day.iso] && `запуск ${starts[day.iso]} шт`,
      row.deadline === day.iso && "дедлайн", arrival === day.iso && "приход комплектующих", notes[day.iso],
    ].filter(Boolean);
    const note = notes[day.iso];
    const quantity = !note && labels[day.iso] ? String(labels[day.iso]) : "";
    return <td key={day.iso} className={classes.join(" ")} title={titles.join(" · ")}
      onClick={(event) => open(event, row.project, `day:${day.iso}`)}>
      {inSpan && <i className={`zg-span-bar${span!.late ? " zg-span-late" : ""}`} />}
      {busy && <i className="zg-bar" />}
      {note && <span className="zg-day-text">{note}</span>}
      {quantity && <span className="zg-qty">{quantity}</span>}
    </td>;
  })}</>;
}

/** Полоса и метки недели в долях её семи дней: работы с первого по последний рабочий день, дедлайн — в конце дня. */
export function weekMarks(week: SheetWeek, row: Pick<ProductionPlanRow, "deadline" | "calendar" | "sheet">, span: ReturnType<typeof deadlineSpan> = null) {
  const work = new Set(row.calendar?.work ?? []);
  const busy = week.days.filter((day) => work.has(day.iso)).map((day) => weekIndex(day.iso));
  const starts = row.calendar?.starts ?? {};
  const quantity = week.days.reduce((sum, day) => sum + (starts[day.iso] ?? 0), 0);
  const notes = week.days.filter((day) => row.calendar?.notes[day.iso]).map((day) => `${formatDayMonth(day.iso)}: ${row.calendar!.notes[day.iso]}`);
  const arrival = row.sheet?.kit_arrival?.value ?? "";
  const inWeek = (iso: string | null) => Boolean(iso && week.days.some((day) => day.iso === iso));
  const spanDays = span ? week.days.filter((day) => day.iso >= span.from && day.iso <= span.to).map((day) => weekIndex(day.iso)) : [];
  return {
    span: spanDays.length ? { from: Math.min(...spanDays) / 7, to: (Math.max(...spanDays) + 1) / 7, late: span!.late } : null,
    bar: busy.length ? { from: Math.min(...busy) / 7, to: (Math.max(...busy) + 1) / 7 } : null,
    quantity, notes,
    deadline: inWeek(row.deadline) ? (weekIndex(row.deadline!) + 1) / 7 : null,
    arrival: inWeek(arrival) ? weekIndex(arrival) / 7 : null,
  };
}

function WeekCells({ row, weeks, today, showSpan }: { row: ProductionPlanRow; weeks: SheetWeek[]; today: string; showSpan: boolean }) {
  const span = showSpan ? deadlineSpan(row, today) : null;
  const work = new Set(row.calendar?.work ?? []);
  const shift = (iso: string, by: number) => { const day = parseDay(iso); return isoDay(new Date(day.getFullYear(), day.getMonth(), day.getDate() + by)); };
  return <>{weeks.map((week, index) => {
    const marks = weekMarks(week, row, span);
    const current = week.days.some((day) => day.iso === today);
    const firstBusy = week.days.find((day) => work.has(day.iso));
    const lastBusy = [...week.days].reverse().find((day) => work.has(day.iso));
    const classes = ["zg-day zg-week", current && "zg-today", weekMonthStart(weeks, index) && "zg-month"];
    if (marks.bar) {
      classes.push("zg-work");
      if (!work.has(shift(firstBusy!.iso, -1))) classes.push("zg-work-start");
      if (!work.has(shift(lastBusy!.iso, 1))) classes.push("zg-work-end");
    }
    const titles = [
      `${row.project} · неделя ${formatDayMonth(week.from)}–${formatDayMonth(week.to)}`,
      marks.span && row.deadline && `срок до ${formatDayMonth(row.deadline)}${marks.span.late ? " · просрочен" : ""}`,
      marks.bar && `плановые работы ${formatDayMonth(firstBusy!.iso)}–${formatDayMonth(lastBusy!.iso)}`,
      marks.quantity > 0 && `запуск ${marks.quantity} шт`,
      marks.deadline !== null && `дедлайн ${formatDate(row.deadline)}`,
      marks.arrival !== null && `приход комплектующих ${formatDate(row.sheet?.kit_arrival?.value)}`,
      ...marks.notes,
    ].filter(Boolean);
    const style = {
      ...(marks.span ? { "--zg-span-from": `${marks.span.from * 100}%`, "--zg-span-to": `${(1 - marks.span.to) * 100}%` } : {}),
      ...(marks.bar ? { "--zg-from": `${marks.bar.from * 100}%`, "--zg-to": `${(1 - marks.bar.to) * 100}%` } : {}),
      ...(marks.deadline !== null ? { "--zg-deadline-at": `${marks.deadline * 100}%` } : {}),
      ...(marks.arrival !== null ? { "--zg-arrival-at": `${marks.arrival * 100}%` } : {}),
    } as CSSProperties;
    return <td key={week.iso} className={classes.filter(Boolean).join(" ")} title={titles.join(" · ")} style={style}>
      {marks.span && <i className={`zg-span-bar${marks.span.late ? " zg-span-late" : ""}`} />}
      {marks.bar && <i className="zg-bar" />}
      {marks.deadline !== null && <i className="zg-week-deadline" />}
      {marks.arrival !== null && <i className="zg-week-arrival" />}
      {marks.quantity > 0 && <span className="zg-qty">{marks.quantity}</span>}
    </td>;
  })}</>;
}

function SheetEditor({ row, field, busy, arrivalValues, mark, onDeadline, close }: {
  row: ProductionPlanRow;
  field: string;
  busy: boolean;
  arrivalValues: string[];
  mark: (project: string, field: string, value: string, text?: string) => Promise<void>;
  onDeadline: (value: string) => Promise<void>;
  close: () => void;
}) {
  const cell = row.sheet?.[field as ProductionPlanSheetField];
  const [text, setText] = useState(() => {
    if (field === "deadline") return row.deadline ?? "";
    if (field.startsWith("day:")) return row.calendar?.notes[field.slice(4)] ?? "";
    if (field === "kit_arrival") return /^\d{4}-\d{2}-\d{2}$/.test(cell?.value ?? "") ? cell!.value : "";
    return cell?.value ?? "";
  });
  const autoShown = (value: string) => /^\d{4}-\d{2}-\d{2}$/.test(value) ? formatDate(value) : value;
  const auto = cell?.manual ? <button className="secondary small" disabled={busy} onClick={() => void mark(row.project, field, "авто")}>
    Вернуть расчёт ERP{cell.auto ? `: ${autoShown(cell.auto)}${field === "ready" ? "%" : ""}` : ""}
  </button> : null;
  const head = (title: string) => <div className="zg-pop-head"><strong>{title}</strong><span>{row.project}</span><button className="link-button" aria-label="Закрыть" onClick={close}>✕</button></div>;

  if (field === "deadline") {
    return <>{head("Дедлайн")}
      <input type="date" aria-label="Дата дедлайна" value={text} onChange={(event) => setText(event.target.value)} autoFocus />
      <div className="zg-pop-acts"><button className="primary small" disabled={busy || !text || text === row.deadline} onClick={() => void onDeadline(text)}>
        {text ? `Поставить дедлайн ${formatDate(text)}` : "Поставить дедлайн"}</button></div>
    </>;
  }
  if (field.startsWith("day:")) {
    const day = field.slice(4);
    return <>{head(`Отметка дня ${formatDate(day)}`)}
      <input aria-label="Отметка дня" maxLength={16} value={text} autoFocus placeholder="Например: П"
        onChange={(event) => setText(event.target.value)}
        onKeyDown={(event) => { if (event.key === "Enter") void mark(row.project, field, text.trim()); }} />
      <div className="zg-pop-acts">
        <button className="primary small" disabled={busy || !text.trim()} onClick={() => void mark(row.project, field, text.trim())}>Записать</button>
        {row.calendar?.notes[day] && <button className="secondary small" disabled={busy} onClick={() => void mark(row.project, field, "")}>Убрать отметку</button>}
      </div>
    </>;
  }
  if (field === "ready") {
    return <>{head("% готовности")}
      <input type="number" min={0} max={100} aria-label="Процент готовности" value={text} autoFocus onChange={(event) => setText(event.target.value)}
        onKeyDown={(event) => { if (event.key === "Enter" && text !== "") void mark(row.project, field, text); }} />
      <div className="zg-pop-acts"><button className="primary small" disabled={busy || text === "" || Number(text) < 0 || Number(text) > 100} onClick={() => void mark(row.project, field, text)}>Записать {text !== "" ? `${text}%` : ""}</button>{auto}</div>
    </>;
  }
  if (field === "kit_arrival") {
    return <>{head("Дата прихода комплектующих")}
      <div className="zg-pop-row">
        <input type="date" aria-label="Дата прихода" value={text} onChange={(event) => setText(event.target.value)} autoFocus />
        <button className="primary small" disabled={busy || !text} onClick={() => void mark(row.project, field, text)}>{text ? `Поставить ${formatDayMonth(text)}` : "Поставить дату"}</button>
      </div>
      <div className="zg-pop-acts">{arrivalValues.map((value) => <button key={value} className="secondary small" disabled={busy} onClick={() => void mark(row.project, field, value)}>{value}</button>)}{auto}</div>
    </>;
  }
  // comment
  return <>{head("Комментарий")}
    <AutoTextarea aria-label="Комментарий" value={text} autoFocus maxLength={2000} maxRows={8} onChange={setText}
      onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void mark(row.project, field, "", text.trim()); } }} />
    <div className="zg-pop-acts">
      <button className="primary small" disabled={busy || text.trim() === (cell?.value ?? "")} onClick={() => void mark(row.project, field, "", text.trim())}>Записать</button>
      {cell?.value && <button className="secondary small" disabled={busy} onClick={() => void mark(row.project, field, "", "")}>Очистить</button>}
    </div>
  </>;
}
