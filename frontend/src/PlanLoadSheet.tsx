import { useEffect, useMemo, useState, type CSSProperties, type MouseEvent, type ReactNode } from "react";
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

export interface SheetDay { iso: string; label: string; weekday: string; off: boolean; monthStart: boolean }

/** Дни календаря листа от `from` до `to` включительно; выходные и праздники цеха — серые. */
export function sheetDays(from: string, to: string, holidays: string[] = []): SheetDay[] {
  const off = new Set(holidays);
  const days: SheetDay[] = [];
  const end = parseDay(to);
  for (let day = parseDay(from); day <= end; day = new Date(day.getFullYear(), day.getMonth(), day.getDate() + 1)) {
    const iso = isoDay(day);
    days.push({
      iso, label: `${pad(day.getDate())}.${pad(day.getMonth() + 1)}`, weekday: WEEKDAYS[day.getDay()],
      off: day.getDay() === 0 || day.getDay() === 6 || off.has(iso), monthStart: day.getDate() === 1,
    });
  }
  return days;
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

type ColumnKey = ProductionPlanSheetField | "project" | "customer" | "days_left" | "deadline";
/** `label` — короткая подпись в одну строку; `name` — полное название колонки листа (подсказка и доступное имя). */
interface Column { key: ColumnKey; label: string; name: string; width: number; wide?: boolean; left?: boolean; hint?: string }

// Порядок — как в листе «ЗАГРУЗ»; ширина — по содержимому, подпись — короткая. Первые пять колонок закреплены
// всегда, остальные — на широком экране (≥1900 px), чтобы календарь начинался сразу за ними.
const COLUMNS: Column[] = [
  { key: "comment", label: "Комментарий", name: "Комментарий", width: 120, left: true },
  { key: "status", label: "Статус", name: "Статус", width: 116, left: true },
  { key: "ready", label: "Готов %", name: "% Готов", width: 54, hint: "выпуск против количества задания, по норме" },
  { key: "in_plan", label: "В плане", name: "В плане", width: 54, hint: "на проект назначены операции" },
  { key: "project", label: "№", name: "№ Проекта", width: 78, left: true },
  { key: "customer", label: "Заказчик", name: "Наименование организации", width: 136, wide: true, left: true },
  { key: "customer_supplied", label: "Давал", name: "Есть давал", width: 48, wide: true, hint: "давальческие комплектующие: НЕТ, ДА или Ч/Т — частично" },
  { key: "kit_checked", label: "Пров.", name: "Пров компл", width: 44, wide: true, hint: "комплектация проверена" },
  { key: "kit_present", label: "Компл.", name: "Компл есть", width: 52, wide: true, hint: "все комплектующие на складе" },
  { key: "zkp", label: "ЗКП", name: "ЗКП", width: 40, wide: true, hint: "авто: на проект есть заявки" },
  { key: "kit_arrival", label: "Приход", name: "Дата прихода компл.", width: 54, wide: true, hint: "когда придут комплектующие" },
  { key: "tc_ready", label: "ТК", name: "Тех кар", width: 36, wide: true, hint: "на каждую КБ выдана ТК" },
  { key: "test_sb", label: "Тест С/Б", name: "Тест С/Б", width: 60, wide: true },
  { key: "kit_picked", label: "Набр.", name: "Компл набр", width: 46, wide: true, hint: "каждая КБ набрана" },
  { key: "days_left", label: "Ост. дн", name: "Ост дн", width: 52, wide: true, hint: "дней до дедлайна" },
  { key: "deadline", label: "Дедлайн", name: "Дедлайн", width: 66, wide: true },
];
const FLAG_FIELDS = new Set<ColumnKey>(["in_plan", "kit_checked", "kit_present", "zkp", "tc_ready", "test_sb", "kit_picked"]);
const LEFTS = COLUMNS.reduce<number[]>((lefts, column, index) => [...lefts, index ? lefts[index - 1] + COLUMNS[index - 1].width : 0], []);
const NARROW_EDGE = COLUMNS.findIndex((column) => column.key === "project");
const FROZEN_WIDTH = COLUMNS.reduce((sum, column) => sum + column.width, 0);

/** Подсказка к шапке: полное название колонки листа и, где нужно, что она значит. */
export function columnTitle(column: Pick<Column, "name" | "hint">) {
  return column.hint ? `${column.name} — ${column.hint}` : column.name;
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
  const days = useMemo(() => sheetDays(from, to, holidays), [from, to, holidays]);
  const groups = useMemo(() => groupSheetRows(rows, query), [rows, query]);

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

  const visible = SHEET_GROUPS.filter(([group]) => group !== "closed" || showClosed);
  const shown = visible.reduce((count, [group]) => count + groups[group].length, 0);
  const row = editing ? rows.find((item) => item.project === editing.project) : undefined;

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
    </div>
    <div className="zg-wrap">
      <table className="zg-table" aria-label="Загрузка цеха по проектам" style={{ width: `calc(${FROZEN_WIDTH}px + ${days.length} * var(--zg-day))` }}>
        <colgroup>
          {COLUMNS.map((column) => <col key={column.key} style={{ width: column.width }} />)}
          {days.map((day) => <col key={day.iso} className="zg-day-col" />)}
        </colgroup>
        <thead>
          <tr>
            {COLUMNS.map((column, index) => <th key={column.key} title={columnTitle(column)} {...frozen(index, column.left ? "zg-left" : "")}>{column.label}</th>)}
            {days.map((day) => <th key={day.iso} className={dayClass(day, today, "zg-day-head")}
              title={`${day.weekday} ${formatDate(day.iso)}${day.iso === today ? " · сегодня" : ""}`}><span>{day.weekday}</span>{day.label}</th>)}
          </tr>
        </thead>
        <tbody>
          {visible.map(([group, label]) => groups[group].length > 0 && <SheetGroupRows key={group} group={group} label={label} rows={groups[group]}
            days={days} today={today} canEdit={canEdit} busy={busy} suppliedValues={suppliedValues}
            open={open} mark={mark} onOpenProject={onOpenProject} onOpenJournal={onOpenJournal} />)}
          {!shown && <tr><td colSpan={COLUMNS.length + days.length} className="zg-empty">
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

function frozen(index: number, extra = ""): { className: string; style: CSSProperties } {
  const column = COLUMNS[index];
  const classes = [column.wide ? "zg-freeze-wide" : "zg-freeze", extra];
  if (index === NARROW_EDGE) classes.push("zg-edge-narrow");
  if (index === COLUMNS.length - 1) classes.push("zg-edge-wide");
  return { className: classes.filter(Boolean).join(" "), style: { "--zg-left": `${LEFTS[index]}px` } as CSSProperties };
}

function dayClass(day: SheetDay, today: string, base: string) {
  return [base, day.off && "zg-off", day.iso === today && "zg-today", day.monthStart && "zg-month"].filter(Boolean).join(" ");
}

function SheetGroupRows({ group, label, rows, days, today, canEdit, busy, suppliedValues, open, mark, onOpenProject, onOpenJournal }: {
  group: SheetGroup;
  label: string;
  rows: ProductionPlanRow[];
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
    <tr className={`zg-group zg-group-${group}`}><td colSpan={COLUMNS.length + days.length}><span className="zg-group-label">{label} · {rows.length}</span></td></tr>
    {rows.map((row) => <tr key={row.project} className={`zg-row zg-row-${group}`} data-project={row.project}
      style={{ "--zg-color": `var(--zg-c${projectColor(row.project)})` } as CSSProperties}>
      {COLUMNS.map((column, index) => <SheetCellView key={column.key} column={column} index={index} row={row}
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

function SheetCellView({ column, index, row, canEdit, busy, suppliedValues, open, mark, onOpenProject, onOpenJournal }: {
  column: Column;
  index: number;
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
    const base = frozen(index, [`zg-col-${key}`, manual, extra].filter(Boolean).join(" "));
    return <td {...base} {...props}>{content}</td>;
  };

  if (key === "project") {
    return td(<button className="zg-project" title={row.kits.length ? "Открыть карточку проекта" : "Открыть журнал проекта"} onClick={() => onOpenProject(row)}>{row.project}</button>);
  }
  if (key === "customer") {
    return td(<button className="zg-link" title={row.customer ? `${row.customer} — журнал проекта` : "Журнал проекта"} onClick={() => onOpenJournal(row)}>{row.customer || "—"}</button>);
  }
  if (key === "days_left") {
    return td(row.days_left ?? "", `zg-days ${daysTone(row.days_left)}`);
  }
  if (key === "deadline") {
    return td(row.deadline ? dayMonth(row.deadline) + "." + row.deadline.slice(2, 4) : "", editable,
      { title: row.deadline ? `Дедлайн ${formatDate(row.deadline)}` : "Без дедлайна", onClick: (event: MouseEvent<HTMLElement>) => open(event, row.project, "deadline") });
  }
  const value = cell?.value ?? "";
  if (FLAG_FIELDS.has(key)) {
    const checked = value === "да";
    return td(<input type="checkbox" aria-label={`${column.name} · ${row.project}`} checked={checked} disabled={!canEdit || busy}
      onChange={(event) => {
        const next = event.target.checked ? "да" : "нет";
        // Вернули расчётное значение — ручная отметка снимается.
        void mark(row.project, key, next === cell?.auto ? "авто" : next);
      }} />, "zg-center", { title: manualTitle(cell) ?? columnTitle(column) });
  }
  if (key === "customer_supplied") {
    return td(canEdit
      ? <select aria-label={`${column.name} · ${row.project}`} className={`zg-supplied s-${value === "Ч/Т" ? "part" : value === "ДА" ? "yes" : "no"}`}
        value={cell?.manual ? value : "авто"} disabled={busy}
        onChange={(event) => void mark(row.project, key, event.target.value === cell?.auto ? "авто" : event.target.value)}>
        <option value="авто">{cell?.auto ?? "НЕТ"}</option>
        {suppliedValues.filter((item) => item !== cell?.auto).map((item) => <option key={item} value={item}>{item}</option>)}
      </select>
      : <span className={`zg-supplied s-${value === "Ч/Т" ? "part" : value === "ДА" ? "yes" : "no"}`}>{value}</span>,
    "zg-center", { title: manualTitle(cell) ?? columnTitle(column) });
  }
  const click = { onClick: (event: MouseEvent<HTMLElement>) => open(event, row.project, key) };
  if (key === "status") {
    return td(<span className="zg-chips">{statusList(value).map((item) => <span key={item} className={`zg-chip t-${STATUS_TONES[item] ?? "erp"}`}>{item}</span>)}</span>,
      editable, { title: [value, manualTitle(cell), row.blocker ? `${row.blocker.type}: ${row.blocker.text}` : ""].filter(Boolean).join(" — ") || undefined, ...click });
  }
  if (key === "ready") {
    const percent = Number(value);
    return td(value === "" ? "" : `${value}%`, `zg-num ${editable}`, {
      title: manualTitle(cell, (auto) => auto && `${auto}%`) ?? "Выпуск против количества задания, по норме",
      style: { ...frozen(index).style, "--zg-ready": `${Number.isFinite(percent) ? percent : 0}%` } as CSSProperties, ...click,
    });
  }
  if (key === "kit_arrival") {
    const late = /^\d{4}-\d{2}-\d{2}$/.test(value) && row.deadline !== null && value > row.deadline;
    return td(/^\d{4}-\d{2}-\d{2}$/.test(value) ? dayMonth(value) : value, `zg-center ${late ? "zg-late-text" : ""} ${editable}`, {
      title: manualTitle(cell, (auto) => /^\d{4}-\d{2}-\d{2}$/.test(auto) ? formatDate(auto) : auto) ?? (late ? "Приход позже дедлайна" : undefined), ...click,
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
      `${row.project} · ${WEEKDAYS[parseDay(day.iso).getDay()]} ${formatDate(day.iso)}`,
      busy && "плановые работы", starts[day.iso] && `запуск ${starts[day.iso]} шт`,
      row.deadline === day.iso && "дедлайн", arrival === day.iso && "приход комплектующих", notes[day.iso],
    ].filter(Boolean);
    return <td key={day.iso} className={classes.join(" ")} title={titles.join(" · ")}
      onClick={(event) => open(event, row.project, `day:${day.iso}`)}>
      {notes[day.iso] ?? (starts[day.iso] ? String(starts[day.iso]) : "")}
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
