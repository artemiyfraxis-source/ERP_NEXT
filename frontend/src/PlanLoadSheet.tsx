import { useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type MouseEvent, type ReactNode } from "react";
import type { ProductionPlanRow, ProductionPlanSheetCell, ProductionPlanSheetField } from "./api";
import { matchesWords } from "./DataTable";
import { formatDate } from "./format";

/* Лист цеха «ЗАГРУЗ» в ERP (владелец 04.10: «тут вот такое представление я хочу»): строка — проект, слева
   закреплённые колонки листа, справа календарь по дням с полосой плановых работ. Значения считает ERP, ручная
   отметка («Отметка плана») ставится поверх и видна уголком в ячейке. */

export type SheetGroup = "hold" | "active" | "done" | "closed";
export const SHEET_GROUPS: Array<[SheetGroup, string]> = [
  ["hold", "На паузе"],
  ["active", "В работе"],
  ["done", "ОТК, отгрузка, готово"],
  ["closed", "Закрытые проекты"],
];

export type SheetMark = { project: string; field: string; value?: string; text?: string };

const WEEKDAYS = ["вс", "пн", "вт", "ср", "чт", "пт", "сб"];
const MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"];
const PROJECT_COLORS = 12;
// Цвет ячейки «Статус» по слову листа; слово этапа ERP без пары в листе — нейтральное.
const STATUS_TONES: Record<string, string> = {
  "ХОЛД": "hold", "СТОП": "stop", "ПАУЗА": "pause", "ПРВ КОМПЛ": "check", "К ЗАПУСК": "launch",
  "В РАБОТЕ": "work", "ОЖИД КОМПЛ": "wait", "ОЖИД ИНФ": "wait", "ОТК": "qc", "НА ОТГР": "ship",
  "ГОТОВО": "ready", "ОТГРУЖЕН": "shipped",
};

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
export function sheetMonths(days: SheetDay[]): Array<{ key: string; label: string; span: number }> {
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

/** Сдвиг календаря при открытии: сегодня — третьим днём слева (два дня до него видны), если сегодня в листе. */
export function todayScrollLeft(days: Pick<SheetDay, "iso">[], today: string, dayWidth: number, lead = 2) {
  const index = days.findIndex((day) => day.iso === today);
  return index < 0 || dayWidth <= 0 ? 0 : Math.max(0, (index - lead) * dayWidth);
}

/** Строки по группам листа: на паузе сверху, в работе, готовые внизу; внутри — горящий дедлайн первым. */
export function groupSheetRows(rows: ProductionPlanRow[], query = ""): Record<SheetGroup, ProductionPlanRow[]> {
  const result: Record<SheetGroup, ProductionPlanRow[]> = { hold: [], active: [], done: [], closed: [] };
  for (const row of rows) {
    if (!matchesWords([row.project, row.customer], query)) continue;
    result[row.group ?? (row.terminal ? "closed" : "active")].push(row);
  }
  for (const list of Object.values(result)) {
    list.sort((a, b) => (a.days_left ?? 1e9) - (b.days_left ?? 1e9) || a.project.localeCompare(b.project, "ru"));
  }
  return result;
}

const MARK_LABELS: Record<string, string> = {
  comment: "Комментарий", status: "Статус", ready: "% Готов", in_plan: "В плане", customer_supplied: "Есть давал",
  kit_checked: "Пров компл", kit_present: "Компл есть", zkp: "ЗКП", kit_arrival: "Дата прихода компл.",
  tc_ready: "Тех кар", test_sb: "Тест С/Б", kit_picked: "Компл набр",
};

/** Текст записи «Отметка плана» в истории проекта: «Статус: ХОЛД», «Отметка 06.10: П», «В плане: расчёт ERP». */
export function markText(event: { field?: string; value: string; text: string }) {
  const field = event.field ?? "";
  const label = field.startsWith("day:") ? `Отметка ${formatDate(field.slice(4))}` : MARK_LABELS[field] ?? field;
  const value = field === "comment" ? event.text : event.value;
  if (!value || value === "авто") return `${label}: ${field === "comment" || field.startsWith("day:") ? "очищено" : "расчёт ERP"}`;
  return `${label}: ${/^\d{4}-\d{2}-\d{2}$/.test(value) ? formatDate(value) : value}`;
}

export function statusList(value: string) {
  return value.split(",").map((item) => item.trim()).filter(Boolean);
}

function dayMonth(value: string) {
  const formatted = formatDate(value);
  return /^\d{2}\.\d{2}\.\d{4}$/.test(formatted) ? formatted.slice(0, 5) : formatted;
}

function daysTone(days: number | null) {
  if (days === null) return "";
  if (days < 0) return "late";
  if (days <= 7) return "soon";
  return "ok";
}

export type ColumnKey = ProductionPlanSheetField | "project" | "customer" | "days_left" | "deadline";
/** `label` — короткая подпись в одну строку; `name` — полное название колонки листа (подсказка и доступное имя).
    `flag` — да/нет-отметка (узкая колонка с ✓ / —); `fold` — очередь скрытия на узком экране (1 — первой). */
interface Column { key: ColumnKey; label: string; name: string; width: number; left?: boolean; hint?: string; flag?: boolean; fold?: number }

// Порядок — как в листе «ЗАГРУЗ». Все показанные колонки закреплены; календарь начинается сразу за ними.
// На узком экране (16") реже нужные колонки по умолчанию скрыты, чтобы календарь показал не меньше двух недель;
// «Колонки» возвращают любую, порядок листа сохраняется.
const COLUMNS: Column[] = [
  { key: "comment", label: "Комментарий", name: "Комментарий", width: 128, left: true },
  { key: "status", label: "Статус", name: "Статус", width: 104, left: true },
  { key: "ready", label: "Готов", name: "% Готов", width: 62, hint: "выпуск против количества задания, по норме" },
  { key: "in_plan", label: "План", name: "В плане", width: 38, flag: true, fold: 3, hint: "на проект назначены операции" },
  { key: "project", label: "№", name: "№ Проекта", width: 80, left: true },
  { key: "customer", label: "Заказчик", name: "Наименование организации", width: 150, left: true },
  { key: "customer_supplied", label: "Дав.", name: "Есть давал", width: 40, fold: 4, hint: "давальческие комплектующие: НЕТ, ДА или Ч/Т — частично" },
  { key: "kit_checked", label: "Пров.", name: "Пров компл", width: 38, flag: true, fold: 6, hint: "комплектация проверена" },
  { key: "kit_present", label: "Компл", name: "Компл есть", width: 44, flag: true, hint: "все комплектующие на складе" },
  { key: "zkp", label: "ЗКП", name: "ЗКП", width: 38, flag: true, fold: 2, hint: "авто: на проект есть заявки" },
  { key: "kit_arrival", label: "Приход", name: "Дата прихода компл.", width: 50, hint: "когда придут комплектующие" },
  { key: "tc_ready", label: "ТК", name: "Тех кар", width: 36, flag: true, hint: "на каждую КБ выдана ТК" },
  { key: "test_sb", label: "С/Б", name: "Тест С/Б", width: 38, flag: true, fold: 5 },
  { key: "kit_picked", label: "Набр.", name: "Компл набр", width: 40, flag: true, hint: "каждая КБ набрана" },
  { key: "days_left", label: "Дн.", name: "Ост дн", width: 38, fold: 1, hint: "дней до дедлайна" },
  { key: "deadline", label: "Дедлайн", name: "Дедлайн", width: 58 },
];
const FOLD_ORDER = COLUMNS.filter((column) => column.fold).sort((a, b) => a.fold! - b.fold!);
/** Сколько дней календаря должно быть видно рядом с закреплёнными колонками без прокрутки. */
export const MIN_VISIBLE_DAYS = 15;
const COLUMNS_KEY = "erp-zg-columns";

/** Ширина колонки: на планшете отметки шире — под палец. */
function columnWidth(column: Column, coarse: boolean) {
  return coarse && column.flag ? Math.max(column.width, 42) : column.width;
}

/** Какие колонки скрыть по умолчанию, чтобы при ширине листа `available` календарь показал `MIN_VISIBLE_DAYS` дней. */
export function autoHiddenColumns(available: number, dayWidth: number, coarse = false): ColumnKey[] {
  if (available <= 0 || dayWidth <= 0) return [];
  let frozenWidth = COLUMNS.reduce((sum, column) => sum + columnWidth(column, coarse), 0);
  const hidden: ColumnKey[] = [];
  for (const column of FOLD_ORDER) {
    if (frozenWidth + MIN_VISIBLE_DAYS * dayWidth <= available) break;
    hidden.push(column.key);
    frozenWidth -= columnWidth(column, coarse);
  }
  return hidden;
}

function readChosen(): ColumnKey[] | null {
  try {
    const saved = JSON.parse(window.localStorage.getItem(COLUMNS_KEY) ?? "null") as unknown;
    return Array.isArray(saved) ? saved.filter((key): key is ColumnKey => COLUMNS.some((column) => column.key === key)) : null;
  } catch {
    return null;
  }
}

function writeChosen(hidden: ColumnKey[] | null) {
  try {
    if (hidden) window.localStorage.setItem(COLUMNS_KEY, JSON.stringify(hidden));
    else window.localStorage.removeItem(COLUMNS_KEY);
  } catch { /* хранилище недоступно — выбор живёт до перезагрузки */ }
}

function isCoarse() {
  try {
    return window.matchMedia?.("(pointer: coarse)").matches ?? false;
  } catch {
    return false;
  }
}

/** Подсказка к шапке: полное название колонки листа и, где нужно, что она значит. */
export function columnTitle(column: Pick<Column, "name" | "hint">) {
  return column.hint ? `${column.name} — ${column.hint}` : column.name;
}

/** Показанные колонки и их отступы слева для закрепления. */
interface Layout { columns: Column[]; lefts: number[]; width: number; hidden: Set<ColumnKey> }

function sheetLayout(hidden: ColumnKey[], coarse: boolean): Layout {
  const columns = COLUMNS.filter((column) => !hidden.includes(column.key));
  const lefts: number[] = [];
  let width = 0;
  for (const column of columns) {
    lefts.push(width);
    width += columnWidth(column, coarse);
  }
  return { columns, lefts, width, hidden: new Set(hidden) };
}

type Editing = { project: string; field: string; top: number; left: number };

export function PlanLoadSheet({ rows, from, to, today, holidays, shopStatuses, suppliedValues, arrivalValues, canEdit, busy, onMark, onDeadline, onOpenProject, onOpenJournal }: {
  rows: ProductionPlanRow[];
  from: string;
  to: string;
  today: string;
  holidays: string[];
  shopStatuses: string[];
  suppliedValues: string[];
  arrivalValues: string[];
  canEdit: boolean;
  busy: boolean;
  onMark: (mark: SheetMark) => Promise<void>;
  onDeadline: (project: string, deadline: string) => Promise<void>;
  onOpenProject: (row: ProductionPlanRow) => void;
  onOpenJournal: (row: ProductionPlanRow) => void;
}) {
  const [query, setQuery] = useState("");
  const [showClosed, setShowClosed] = useState(false);
  const [editing, setEditing] = useState<Editing | null>(null);
  const [choosing, setChoosing] = useState(false);
  const [chosen, setChosen] = useState<ColumnKey[] | null>(readChosen);
  const [space, setSpace] = useState({ available: 0, day: 0 });
  const [coarse] = useState(isCoarse);
  const wrapRef = useRef<HTMLDivElement>(null);
  const days = useMemo(() => sheetDays(from, to, holidays), [from, to, holidays]);
  const months = useMemo(() => sheetMonths(days), [days]);
  const groups = useMemo(() => groupSheetRows(rows, query), [rows, query]);
  const hidden = chosen ?? autoHiddenColumns(space.available, space.day, coarse);
  const layout = useMemo(() => sheetLayout(hidden, coarse), [hidden.join(","), coarse]); // eslint-disable-line react-hooks/exhaustive-deps

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
  }, []);

  // При открытии календарь стоит на сегодня: оно третьим днём справа от закреплённых колонок.
  useLayoutEffect(() => {
    const wrap = wrapRef.current;
    const day = wrap?.querySelector<HTMLElement>("th.zg-day-head")?.getBoundingClientRect().width ?? 0;
    if (wrap) wrap.scrollLeft = todayScrollLeft(days, today, day);
  }, [from, to, today]); // eslint-disable-line react-hooks/exhaustive-deps

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
    writeChosen(next);
  }

  const visible = SHEET_GROUPS.filter(([group]) => group !== "closed" || showClosed);
  const shown = visible.reduce((count, [group]) => count + groups[group].length, 0);
  const row = editing ? rows.find((item) => item.project === editing.project) : undefined;
  const span = layout.columns.length + days.length;

  return <div className="zg">
    <div className="zg-toolbar">
      <input aria-label="Поиск проекта" placeholder="№ проекта или заказчик" value={query} onChange={(event) => setQuery(event.target.value)} />
      {groups.closed.length > 0 && <label className="zg-toggle"><input type="checkbox" checked={showClosed} onChange={(event) => setShowClosed(event.target.checked)} />Закрытые проекты · {groups.closed.length}</label>}
      <span className="zg-legend" aria-label="Обозначения">
        <span title="Плановые работы по проекту"><i className="zg-key-bar" />работы</span>
        <span title="Количество запуска в штуках"><i className="zg-key-qty">12</i>запуск, шт</span>
        <span title="День прихода комплектующих"><i className="zg-key-arrival" />приход</span>
        <span title="Дедлайн проекта"><i className="zg-key-deadline" />дедлайн</span>
        <span title="Значение поставлено вручную поверх расчёта ERP"><i className="zg-key-manual" />вручную</span>
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
      <table className="zg-table" aria-label="Загрузка цеха по проектам"
        style={{ width: `calc(${layout.width}px + ${days.length} * var(--zg-day))`, "--zg-frozen": `${layout.width}px` } as CSSProperties}>
        <colgroup>
          {layout.columns.map((column) => <col key={column.key} style={{ width: columnWidth(column, coarse) }} />)}
          {days.map((day) => <col key={day.iso} className="zg-day-col" />)}
        </colgroup>
        <thead>
          <tr className="zg-head-months">
            {layout.columns.map((column, index) => <th key={column.key} rowSpan={2} title={columnTitle(column)}
              {...frozen(layout, index, `zg-col-head${column.left ? " zg-left" : ""}`)}>{column.label}</th>)}
            {months.map((month, index) => <th key={month.key} colSpan={month.span} className={`zg-month-head${index ? " zg-month" : ""}`}><span>{month.label}</span></th>)}
          </tr>
          <tr className="zg-head-days">
            {days.map((day) => <th key={day.iso} className={dayClass(day, today, "zg-day-head")}
              title={`${day.weekday} ${formatDate(day.iso)}${day.iso === today ? " · сегодня" : ""}`}><b>{day.day}</b><span>{day.weekday}</span></th>)}
          </tr>
        </thead>
        <tbody>
          {visible.map(([group, label]) => groups[group].length > 0 && <SheetGroupRows key={group} group={group} label={label} rows={groups[group]}
            layout={layout} days={days} today={today} canEdit={canEdit} busy={busy} suppliedValues={suppliedValues}
            open={open} mark={mark} onOpenProject={onOpenProject} onOpenJournal={onOpenJournal} />)}
          {!shown && <tr className="zg-empty-row"><td colSpan={span} className="zg-empty">
            <span className="zg-empty-text">{query ? "Под этот поиск проектов нет." : "Проектов в плане нет — заведите проект в «Проектах»."}</span>
          </td></tr>}
        </tbody>
      </table>
    </div>
    {editing && row && <>
      <div className="zg-scrim" onClick={() => setEditing(null)} />
      <div className="zg-pop" role="dialog" aria-label={`Правка: ${editing.project}`} style={{ top: editing.top, left: editing.left }}>
        <SheetEditor row={row} field={editing.field} busy={busy} shopStatuses={shopStatuses} arrivalValues={arrivalValues}
          mark={mark} onDeadline={async (value) => { setEditing(null); await onDeadline(row.project, value); }}
          close={() => setEditing(null)} />
      </div>
    </>}
  </div>;
}

function frozen(layout: Layout, index: number, extra = ""): { className: string; style: CSSProperties } {
  const classes = ["zg-freeze", extra];
  if (index === layout.columns.length - 1) classes.push("zg-edge");
  return { className: classes.filter(Boolean).join(" "), style: { "--zg-left": `${layout.lefts[index]}px` } as CSSProperties };
}

function dayClass(day: SheetDay, today: string, base: string) {
  return [base, day.off && "zg-off", day.iso === today && "zg-today", day.monthStart && "zg-month"].filter(Boolean).join(" ");
}

function SheetGroupRows({ group, label, rows, layout, days, today, canEdit, busy, suppliedValues, open, mark, onOpenProject, onOpenJournal }: {
  group: SheetGroup;
  label: string;
  rows: ProductionPlanRow[];
  layout: Layout;
  days: SheetDay[];
  today: string;
  canEdit: boolean;
  busy: boolean;
  suppliedValues: string[];
  open: (event: MouseEvent<HTMLElement>, project: string, field: string) => void;
  mark: (project: string, field: string, value: string, text?: string) => Promise<void>;
  onOpenProject: (row: ProductionPlanRow) => void;
  onOpenJournal: (row: ProductionPlanRow) => void;
}) {
  return <>
    <tr className={`zg-group zg-group-${group}`}><td colSpan={layout.columns.length + days.length}><span className="zg-group-label">{label} · {rows.length}</span></td></tr>
    {rows.map((row) => <tr key={row.project} className={`zg-row zg-row-${group}`} data-project={row.project}
      style={{ "--zg-color": `var(--zg-c${projectColor(row.project)})` } as CSSProperties}>
      {layout.columns.map((column, index) => <SheetCellView key={column.key} column={column} index={index} layout={layout} row={row}
        canEdit={canEdit} busy={busy} suppliedValues={suppliedValues} open={open} mark={mark}
        onOpenProject={onOpenProject} onOpenJournal={onOpenJournal} />)}
      <CalendarCells row={row} days={days} today={today} canEdit={canEdit} open={open} />
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

function SheetCellView({ column, index, layout, row, canEdit, busy, suppliedValues, open, mark, onOpenProject, onOpenJournal }: {
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
}) {
  const key = column.key;
  const cell = key in (row.sheet ?? {}) ? row.sheet?.[key as ProductionPlanSheetField] : undefined;
  const manual = cell?.manual ? "zg-manual" : "";
  const editable = canEdit ? "zg-edit" : "";
  const td = (content: ReactNode, extra = "", props: Record<string, unknown> = {}) => {
    const base = frozen(layout, index, [`zg-col-${key}`, manual, extra].filter(Boolean).join(" "));
    return <td {...base} {...props}>{content}</td>;
  };

  if (key === "project") {
    return td(<button className="zg-project" title={row.kits.length ? "Открыть карточку проекта" : "Открыть журнал проекта"} onClick={() => onOpenProject(row)}>{row.project}</button>);
  }
  if (key === "customer") {
    return td(<button className="zg-link" title={row.customer ? `${row.customer} — журнал проекта` : "Журнал проекта"} onClick={() => onOpenJournal(row)}>{row.customer || "—"}</button>);
  }
  if (key === "days_left") {
    return td(row.days_left ?? "", `zg-days ${daysTone(row.days_left)}`, { title: row.days_left === null ? "Без дедлайна" : `Дней до дедлайна: ${row.days_left}` });
  }
  if (key === "deadline") {
    // Колонка «Дн.» скрыта — светофор дедлайна переезжает в «Дедлайн».
    const tone = layout.hidden.has("days_left") ? `zg-days ${daysTone(row.days_left)}` : "";
    return td(row.deadline ? dayMonth(row.deadline) : "", `zg-center ${tone} ${editable}`,
      { title: row.deadline ? `Дедлайн ${formatDate(row.deadline)}${row.days_left !== null ? ` · дней до дедлайна: ${row.days_left}` : ""}` : "Без дедлайна", onClick: (event: MouseEvent<HTMLElement>) => open(event, row.project, "deadline") });
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
    const [first, ...rest] = statusList(value);
    return td(<span className="zg-chips">
      {first && <span className={`zg-chip t-${STATUS_TONES[first] ?? "erp"}`}>{first}</span>}
      {rest.length > 0 && <span className="zg-more">+{rest.length}</span>}
    </span>,
    editable, { title: [value, manualTitle(cell), row.blocker ? `${row.blocker.type}: ${row.blocker.text}` : ""].filter(Boolean).join(" — ") || undefined, ...click });
  }
  if (key === "ready") {
    const percent = Math.max(0, Math.min(100, Number(value) || 0));
    return td(value === "" ? "" : <span className={`zg-ready${percent >= 100 ? " full" : percent === 0 ? " zero" : ""}`}>
      <b>{value}%</b><i style={{ "--zg-ready": `${percent}%` } as CSSProperties} />
    </span>, editable, {
      title: manualTitle(cell, (auto) => auto && `${auto}%`) ?? "Выпуск против количества задания, по норме", ...click,
    });
  }
  if (key === "kit_arrival") {
    const date = /^\d{4}-\d{2}-\d{2}$/.test(value);
    const late = date && row.deadline !== null && value > row.deadline;
    return td(date ? dayMonth(value) : value, `zg-center ${arrivalTone(value, late)} ${editable}`, {
      title: manualTitle(cell, (auto) => /^\d{4}-\d{2}-\d{2}$/.test(auto) ? formatDate(auto) : auto)
        ?? (late ? `Приход ${formatDate(value)} — позже дедлайна` : date ? `Приход комплектующих ${formatDate(value)}` : columnTitle(column)), ...click,
    });
  }
  // comment
  return td(<span className="zg-ellipsis">{value}</span>, editable, { title: value || undefined, ...click });
}

function CalendarCells({ row, days, today, canEdit, open }: {
  row: ProductionPlanRow;
  days: SheetDay[];
  today: string;
  canEdit: boolean;
  open: (event: MouseEvent<HTMLElement>, project: string, field: string) => void;
}) {
  const work = new Set(row.calendar?.work ?? []);
  const starts = row.calendar?.starts ?? {};
  const notes = row.calendar?.notes ?? {};
  const arrival = row.sheet?.kit_arrival?.value ?? "";
  return <>{days.map((day, index) => {
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
      `${row.project} · ${day.weekday} ${formatDate(day.iso)}`,
      busy && "плановые работы", starts[day.iso] && `запуск ${starts[day.iso]} шт`,
      row.deadline === day.iso && "дедлайн", arrival === day.iso && "приход комплектующих", notes[day.iso],
    ].filter(Boolean);
    const text = notes[day.iso] ?? (starts[day.iso] ? String(starts[day.iso]) : "");
    return <td key={day.iso} className={classes.join(" ")} title={titles.join(" · ")}
      onClick={(event) => open(event, row.project, `day:${day.iso}`)}>
      {busy && <i className="zg-bar" />}{text && <span className="zg-day-text">{text}</span>}
    </td>;
  })}</>;
}

function SheetEditor({ row, field, busy, shopStatuses, arrivalValues, mark, onDeadline, close }: {
  row: ProductionPlanRow;
  field: string;
  busy: boolean;
  shopStatuses: string[];
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
  const [picked, setPicked] = useState(() => statusList(cell?.value ?? ""));
  const auto = cell?.manual ? <button className="secondary small" disabled={busy} onClick={() => void mark(row.project, field, "авто")}>
    Вернуть расчёт ERP{cell.auto ? `: ${/^\d{4}-\d{2}-\d{2}$/.test(cell.auto) ? formatDate(cell.auto) : cell.auto}${field === "ready" ? "%" : ""}` : ""}
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
  if (field === "status") {
    return <>{head("Статус")}
      <div className="zg-status-pick">{shopStatuses.map((status) => <label key={status} className={`zg-chip t-${STATUS_TONES[status] ?? "erp"}${picked.includes(status) ? " on" : ""}`}>
        <input type="checkbox" checked={picked.includes(status)} onChange={(event) => setPicked((current) => event.target.checked ? [...current, status] : current.filter((item) => item !== status))} />{status}
      </label>)}</div>
      <div className="zg-pop-acts">
        <button className="primary small" disabled={busy || !picked.length} onClick={() => void mark(row.project, field, picked.filter((item) => shopStatuses.includes(item)).join(", "))}>Поставить статус</button>
        {auto}
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
        <button className="primary small" disabled={busy || !text} onClick={() => void mark(row.project, field, text)}>{text ? `Поставить ${formatDate(text).slice(0, 5)}` : "Поставить дату"}</button>
      </div>
      <div className="zg-pop-acts">{arrivalValues.map((value) => <button key={value} className="secondary small" disabled={busy} onClick={() => void mark(row.project, field, value)}>{value}</button>)}{auto}</div>
    </>;
  }
  // comment
  return <>{head("Комментарий")}
    <textarea aria-label="Комментарий" value={text} autoFocus maxLength={2000} onChange={(event) => setText(event.target.value)}
      onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void mark(row.project, field, "", text.trim()); } }} />
    <div className="zg-pop-acts">
      <button className="primary small" disabled={busy || text.trim() === (cell?.value ?? "")} onClick={() => void mark(row.project, field, "", text.trim())}>Записать</button>
      {cell?.value && <button className="secondary small" disabled={busy} onClick={() => void mark(row.project, field, "", "")}>Очистить</button>}
    </div>
  </>;
}
