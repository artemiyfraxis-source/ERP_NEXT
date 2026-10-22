import { Fragment, useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";
import { selectRange } from "./bulk";
import { EMPTY, displayCell, formatNumber } from "./format";
import { matchesSearch, searchRows } from "./searchText";
import { LoadMore } from "./LoadMore";
import { useColumnViews } from "./ColumnViews";

/* Общая таблица разделов — то, что в SUPPLYMAP давал лист Google и портальные таблицы (portalGetView,
   portalSaveUserCols): поиск по всем словам, фильтр по значениям колонки, сортировка по заголовку,
   быстрые отборы-чипы, группировка, выбор строк с массовыми действиями, свои колонки, выгрузка в Excel,
   раскрытие строки под форму. Данные — уже загруженные строки страницы; сервер по-прежнему отдаёт их
   страницами, таблица работает с тем, что на экране. */

export type CellValue = string | number | null | undefined;

export interface DataColumn<T> {
  key: string;
  title: string;
  /** Значение для поиска, фильтра, сортировки и выгрузки. */
  value: (row: T) => CellValue;
  /** Как показать ячейку; по умолчанию — само значение. */
  render?: (row: T) => ReactNode;
  /** Выпадающий фильтр по значениям этой колонки. */
  filter?: boolean;
  /** Числовая колонка: сортировка по числу, выравнивание вправо. */
  numeric?: boolean;
  /** Колонка скрыта, пока пользователь её не включит. */
  hidden?: boolean;
  /** Длинный текст с переносом. */
  wrap?: boolean;
  /** Ширина колонки (px) — чтобы несколько таблиц одной страницы шли колонка в колонку. */
  width?: number | string;
  /** Колонка забирает свободную ширину таблицы (длинное название, назначение, подробности). Без неё колонки идут
      по содержимому, а остаток ширины уходит в пустую колонку справа. */
  grow?: boolean;
  /** Самая узкая ширина текста колонки с переносом (`wrap`); у `grow` по умолчанию 240 px. */
  minWidth?: number | string;
  /** С какой ширины текст колонки с переносом (`wrap`) переносится; по умолчанию — по экрану, 240–520 px. `width` у
      такой колонки — нижняя граница: короче `maxWidth` текст идёт одной строкой. */
  maxWidth?: number | string;
  /** Выравнивание ячеек; числовая колонка — вправо. */
  align?: "left" | "center" | "right";
  /** Подсказка при наведении на заголовок (например, полное название при сокращённом). */
  hint?: string;
  /** Правка ячейки прямо в таблице, как в Google Таблицах (по выбору колонки; без неё ячейка только для чтения). */
  edit?: DataCellEdit<T>;
  /** Свой столбец пользователя («+ Столбец»): в «Колонках» у него кнопка «Удалить» — экран спрашивает подтверждение. */
  remove?: () => void;
  /** Считается из других данных («Почему здесь», диапазон, статус карточки): в таблице с правкой ячейка выглядит
      только для чтения. */
  computed?: boolean;
}

export interface DataCellOption {
  value: string;
  label: string;
  /** Как ещё пишут этот вариант при вводе и вставке («22», «22 AWG», «0,35»). */
  aliases?: string[];
}

export interface DataCellEdit<T> {
  /** Записать значение ячейки тем же путём, что окно записи (со старым значением для проверки). Отказ — Error с текстом
      для ячейки: введённое остаётся в поле. */
  save: (row: T, value: string) => Promise<unknown>;
  /** Текст в поле правки; по умолчанию — значение колонки. */
  value?: (row: T) => string;
  /** Можно ли править ячейку этой строки (у записи есть такое поле…); по умолчанию — да. */
  enabled?: (row: T) => boolean;
  /** Ячейка — выбор из списка (сечение из таблицы сечений, категория, вкладка): правка — выпадающий список в ячейке,
      `value` — значение варианта. Ввод буквы выбирает первый вариант на неё, вставка и Ctrl+D ищут вариант по значению,
      подписи или `aliases`. */
  options?: (row: T) => DataCellOption[];
}

export interface DataPreset<T> {
  label: string;
  test: (row: T) => boolean;
}

export interface DataBulkAction<T> {
  label: string;
  run: (rows: T[]) => void | Promise<unknown>;
  disabled?: (rows: T[]) => boolean;
  /** Действие не относится к выбранным строкам — кнопки нет вовсе (а не серой). */
  hidden?: (rows: T[]) => boolean;
}

export interface DataTableProps<T> {
  rows: T[];
  columns: DataColumn<T>[];
  rowKey: (row: T) => string;
  /** Ключ для запоминания колонок, отбора и сортировки у пользователя (localStorage). */
  storageKey?: string;
  /** Ключ общего набора столбцов (сервер, один на всех): «Сохранить для всех» в «Колонках» пишет его, «Только нужные»
      возвращает к нему. Первая колонка — опознаватель записи (Марка, Артикул) — всегда видна. Без `ColumnViewsProvider` — нет. */
  sharedKey?: string;
  label?: string;
  presets?: DataPreset<T>[];
  /** Чип, открытый по умолчанию (пока пользователь не выбрал другой чип; «Все» не запоминается). */
  defaultPreset?: string;
  /** Счётчики чипов — по строкам, прошедшим поиск и фильтры колонок (без самого чипа), как issFilterRender_ в
      SUPPLYMAP. По умолчанию — по всем строкам. */
  presetCountsFollowSearch?: boolean;
  /** «Все» стоит после остальных чипов, а не перед ними. */
  allLast?: boolean;
  groupBy?: (row: T) => string;
  /** Подпись заголовка группы (по умолчанию — её имя); например, заказ и поставщик. */
  groupLabel?: (name: string, rows: T[]) => ReactNode;
  /** Заголовок группы сворачивает её строки (по умолчанию группы всегда раскрыты). */
  collapsibleGroups?: boolean;
  bulkActions?: DataBulkAction<T>[];
  /** Раскрытая строка: форма, карточка, история. */
  renderDetail?: (row: T) => ReactNode;
  rowClassName?: (row: T) => string;
  /** Клик по строке целиком — действие (вместо раскрытия строки); rowTitle — подсказка строки. */
  onRowClick?: (row: T) => void;
  rowTitle?: (row: T) => string;
  /** Подпись отметки строки для чтения с экрана (по умолчанию — ключ строки). */
  rowLabel?: (row: T) => string;
  /** Имя файла выгрузки без расширения; без него кнопки «Excel» нет. */
  exportName?: string;
  emptyText?: string;
  pageSize?: number;
  toolbar?: ReactNode;
  /** Поиск сразу по всем записям на сервере (строки на экране — только страница): «Найти» или Enter в том же поле. */
  onSearch?: (query: string) => void;
  /** Что ещё строки ищет поиск, кроме колонок: скрытые опознаватели записи (полное имя, артикул BAG, синонимы). */
  searchText?: (row: T) => CellValue[];
  searchLabel?: string;
  searchPlaceholder?: string;
  /** Пока строк меньше — без строки поиска/фильтров/колонок и без «Показано N из N»: маленькой таблице они
      только мешают (блоки «Мои позиции» в листе закупки). По умолчанию всегда полная. */
  compactBelow?: number;
  /** Прячет только форму поиска (фильтры/колонки/Excel остаются): поиск есть у раздела выше. */
  hideSearch?: boolean;
  /** На телефоне (≤ 720 px, `responsive.css`) строки идут карточками «колонка: значение» вместо таблицы, которую
      пришлось бы листать вбок. `false` — таблица остаётся таблицей и на телефоне (сетки с правкой в ячейках). */
  phoneCards?: boolean;
  /** После записи ячеек в таблице (и после отказа): перечитать строки с сервера. */
  onEdited?: () => Promise<unknown> | void;
  /** Сортировка внутри групп (owner 16.10.2026: «у каждой группы своя, и это во всех кабелях»; 19.10: «сделай просто
      вверху столбцов сортировку»): щелчок по заголовку сортирует строки в каждой группе, порядок групп не меняется. */
  groupSort?: boolean;
}

type Sort = { key: string; direction: 1 | -1 } | null;

export interface Saved {
  hidden?: string[];
  /** Колонки, которые таблица уже видела при сохранении (владелец 19.10: «почему пропал частотный диапазон»): колонка не
      из этого списка — новая, она показывается или прячется по своему умолчанию, а не по старому `hidden`. */
  known?: string[];
  sort?: Sort;
  preset?: string;
  /** Порядок колонок пользователя (ключи); колонка, которой нет в списке, встаёт после своей соседки слева. */
  order?: string[];
  /** Ширины колонок, перетянутые пользователем (px текста ячейки). */
  widths?: Record<string, number>;
  /** Свои подписи колонок. */
  titles?: Record<string, string>;
  /** Какой общий набор столбцов уже применён к этому виду: новый общий набор заменяет личные скрытие и порядок один раз. */
  sharedSig?: string;
}

const MIN_WIDTH = 40;
const MAX_WIDTH = 900;
const WIDTH_STEP = 16;
/** Поля ячейки слева и справа (`.data-table th, td { padding: 6px 12px }`): ширина колонки = текст + они. */
const CELL_PAD = 24;
const clampWidth = (value: number) => Math.round(Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, value)));

/** Колонки в порядке пользователя; новые колонки (их нет в сохранённом порядке) — после соседки слева по описанию. */
export function orderColumns<T>(columns: DataColumn<T>[], order: string[] | undefined): DataColumn<T>[] {
  if (!order?.length) return columns;
  const byKey = new Map(columns.map((column) => [column.key, column]));
  const known = [...new Set(order)].filter((key) => byKey.has(key));
  const result = known.map((key) => byKey.get(key)!);
  columns.forEach((column, index) => {
    if (known.includes(column.key)) return;
    const left = columns.slice(0, index).reverse().find((item) => result.includes(item));
    result.splice(left ? result.indexOf(left) + 1 : 0, 0, column);
  });
  return result;
}

/** Ключ `key` встаёт на место `target` (тащим вправо — после него, влево — перед ним). */
export function moveKey(keys: string[], key: string, target: string): string[] {
  const from = keys.indexOf(key);
  const to = keys.indexOf(target);
  if (from < 0 || to < 0 || from === to) return keys;
  const next = keys.filter((item) => item !== key);
  next.splice(to, 0, key);
  return next;
}

/** Сохранённое состояние любой прошлой версии таблицы: битые поля порядка, ширин и подписей отбрасываются, остальное
    (в том числе поля, которые хранят в том же ключе экраны, — `groups` листа «В работе») остаётся как было. */
function cleanSaved(raw: unknown): Saved {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {};
  const saved = { ...(raw as Record<string, unknown>) } as Saved & Record<string, unknown>;
  const map = (value: unknown) => value && typeof value === "object" && !Array.isArray(value) ? Object.entries(value as Record<string, unknown>) : [];
  const order = Array.isArray(saved.order) ? saved.order.filter((item): item is string => typeof item === "string") : [];
  const widths = map(saved.widths).filter((entry): entry is [string, number] => typeof entry[1] === "number" && Number.isFinite(entry[1]));
  const titles = map(saved.titles).filter((entry): entry is [string, string] => typeof entry[1] === "string" && entry[1].trim() !== "");
  delete saved.order;
  delete saved.widths;
  delete saved.titles;
  delete saved.groupSorts; // прежняя сортировка в строке группы (до 19.10) — не читается
  if (order.length) saved.order = order;
  if (widths.length) saved.widths = Object.fromEntries(widths.map(([key, width]) => [key, clampWidth(width)]));
  if (titles.length) saved.titles = Object.fromEntries(titles);
  if (saved.hidden !== undefined && !Array.isArray(saved.hidden)) delete saved.hidden;
  if (saved.sharedSig !== undefined && typeof saved.sharedSig !== "string") delete saved.sharedSig;
  if (saved.known !== undefined && !(Array.isArray(saved.known) && saved.known.every((item) => typeof item === "string"))) delete saved.known;
  return saved;
}

/** Все слова запроса — хотя бы в одной ячейке строки (SUPPLYMAP stock05Match_); правила сравнения — searchText.ts. */
export function matchesWords(values: CellValue[], query: string) {
  return matchesSearch(values, query);
}

function numberOf(value: CellValue) {
  if (typeof value === "number") return value;
  const parsed = Number(String(value ?? "").replace(/\s/g, "").replace(",", "."));
  return Number.isFinite(parsed) ? parsed : Number.NaN;
}

export function compareCells(a: CellValue, b: CellValue, numeric = false) {
  const emptyA = a === null || a === undefined || a === "";
  const emptyB = b === null || b === undefined || b === "";
  if (emptyA || emptyB) return emptyA === emptyB ? 0 : emptyA ? 1 : -1; // пустые всегда внизу
  if (numeric) {
    const left = numberOf(a);
    const right = numberOf(b);
    if (!Number.isNaN(left) && !Number.isNaN(right)) return left - right;
  }
  return String(a).localeCompare(String(b), "ru", { numeric: true, sensitivity: "base" });
}

/** Сравнение в направлении сортировки; пустые — внизу в обоих направлениях. */
function directed(a: CellValue, b: CellValue, direction: 1 | -1, numeric = false) {
  const empty = (value: CellValue) => value === null || value === undefined || value === "";
  return empty(a) || empty(b) ? compareCells(a, b) : direction * compareCells(a, b, numeric);
}

/** Вариант списка ячейки по введённому или вставленному тексту: значение, подпись или другое написание (без учёта
    регистра и лишних пробелов), иначе единственная подпись, которая с него начинается. */
export function findOption(options: DataCellOption[], text: string): DataCellOption | null {
  const fold = (value: string) => value.toLowerCase().replace(/\s+/g, " ").trim();
  const wanted = fold(text);
  const exact = options.find((option) => option.value === text.trim()
    || [option.label, ...(option.aliases ?? [])].some((name) => fold(name) === wanted));
  if (exact) return exact;
  if (!wanted) return null;
  const starts = options.filter((option) => fold(option.label).startsWith(wanted));
  return starts.length === 1 ? starts[0] : null;
}

/** Текст выгрузки: табуляция, заголовок первой строкой, CRLF — Excel открывает как таблицу. */
export function tableText<T>(rows: T[], columns: DataColumn<T>[]) {
  const cell = (value: CellValue) => String(value ?? "").replace(/[\t\r\n]+/g, " ");
  const lines = [columns.map((column) => column.title).join("\t")];
  for (const row of rows) lines.push(columns.map((column) => cell(column.value(row))).join("\t"));
  return lines.join("\r\n") + "\r\n";
}

/** UTF-16LE с BOM: Excel открывает кириллицу без мастера импорта (как выгрузка склада SK-14). */
function utf16Bytes(text: string) {
  const bytes = new Uint8Array(2 + text.length * 2);
  bytes[0] = 0xff;
  bytes[1] = 0xfe;
  for (let i = 0; i < text.length; i++) {
    const code = text.charCodeAt(i);
    bytes[2 + i * 2] = code & 0xff;
    bytes[3 + i * 2] = code >> 8;
  }
  return bytes;
}

/** Ячейка без своего `render`: число — по-русски с разрядами («12 345,5»), ISO-дата — по-русски, пусто — «—». */
export function cellContent<T>(column: DataColumn<T>, value: CellValue): ReactNode {
  if (value === null || value === undefined || value === "") return <span className="cell-empty">{EMPTY}</span>;
  if (column.numeric) return formatNumber(value);
  return displayCell(value);
}

/** Подсказка поиска по умолчанию — что ищем: первые колонки таблицы («Поиск: №, проект, назначение…»). */
export function searchHint<T>(columns: DataColumn<T>[]) {
  const titles = columns.map((column) => column.title.trim()).filter(Boolean).slice(0, 3)
    // «Проект» → «проект», а сокращения («ГРН», «КД», «№») — как есть.
    .map((title) => (/^[А-ЯЁA-Z][а-яёa-z]/.test(title) ? title[0].toLowerCase() + title.slice(1) : title));
  return titles.length ? `Поиск: ${titles.join(", ")}…` : "Поиск…";
}

/** Классы ячейки колонки. `fit` — по содержимому (ширина 1 px в автоматической раскладке таблицы: колонка не шире
    своего текста), `grow` — забирает свободную ширину; в таблице с фиксированной раскладкой ширины задаёт экран. */
export function columnClass<T>(column: DataColumn<T>, index: number, sized: boolean) {
  return [
    column.numeric ? "num" : column.wrap ? "text" : "",
    column.align && column.align !== (column.numeric ? "right" : "left") ? `align-${column.align}` : "",
    sized ? (column.grow ? "grow" : column.width === undefined ? "fit" : "") : "",
    index === 0 ? "key" : "",
  ].filter(Boolean).join(" ") || undefined;
}

function cssSize(value: number | string | undefined) {
  return typeof value === "number" ? `${value}px` : value;
}

function wrapStyle<T>(column: DataColumn<T>): CSSProperties | undefined {
  const style: Record<string, string> = {};
  const min = cssSize(column.minWidth);
  const max = cssSize(column.maxWidth);
  if (min) style["--cell-min"] = min;
  if (max) style["--cell-max"] = max;
  return Object.keys(style).length ? style as CSSProperties : undefined;
}

export function readSaved(storageKey?: string): Saved {
  if (!storageKey) return {};
  try {
    return cleanSaved(JSON.parse(window.localStorage.getItem(`erp-table:${storageKey}`) ?? "{}"));
  } catch {
    return {};
  }
}

export function writeSaved(storageKey: string | undefined, saved: Saved) {
  if (!storageKey) return;
  try {
    window.localStorage.setItem(`erp-table:${storageKey}`, JSON.stringify(saved));
  } catch { /* хранилище недоступно — настройки живут до перезагрузки */ }
}

type CellRef = { row: string; col: string };
type Editing = CellRef & { draft: string; error: string; saving: boolean };
type Step = "up" | "down" | "left" | "right" | "next" | "prev";
type Note = { tone: "ok" | "danger" | "info"; text: string };

const failure = (reason: unknown) => reason instanceof Error && reason.message ? reason.message : "Не записано";
const sameCell = (a: CellRef | null, b: CellRef | null) => Boolean(a && b && a.row === b.row && a.col === b.col);

export function DataTable<T>({
  rows,
  columns,
  rowKey,
  storageKey,
  sharedKey,
  label = "Таблица",
  presets = [],
  defaultPreset = "",
  presetCountsFollowSearch = false,
  allLast = false,
  groupBy,
  groupLabel,
  collapsibleGroups,
  bulkActions = [],
  renderDetail,
  rowClassName,
  onRowClick,
  rowTitle,
  rowLabel,
  exportName,
  emptyText = "Ничего не найдено.",
  pageSize = 200,
  toolbar,
  onSearch,
  searchText,
  searchLabel,
  searchPlaceholder,
  compactBelow = 0,
  hideSearch = false,
  phoneCards = true,
  onEdited,
  groupSort = false,
}: DataTableProps<T>) {
  const compact = rows.length < compactBelow;
  const saved = useMemo(() => readSaved(storageKey), [storageKey]);
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(new Set());
  const [query, setQuery] = useState("");
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [sort, setSort] = useState<Sort>(saved.sort ?? null);
  const [preset, setPreset] = useState(saved.preset || defaultPreset);
  const defaultHidden = () => columns.filter((column) => column.hidden).map((column) => column.key);
  const [hidden, setHidden] = useState<string[]>(() => {
    if (!saved.hidden) return defaultHidden();
    if (!saved.known) return saved.hidden;
    // Новая колонка (её не было при сохранении) — по своему умолчанию; остальные — как оставил пользователь.
    const known = new Set(saved.known);
    const fresh = columns.filter((column) => !known.has(column.key));
    return [...saved.hidden.filter((key) => !fresh.some((column) => column.key === key)),
      ...fresh.filter((column) => column.hidden).map((column) => column.key)];
  });
  const [order, setOrder] = useState<string[] | undefined>(saved.order);
  // Общий набор столбцов: пока сервер его не отдал или его нет — всё как раньше. Новый набор (другая подпись) один раз
  // заменяет личные скрытие и порядок у каждого; дальше пользователь может подстроить под себя.
  const columnViews = useColumnViews();
  const sharedView = sharedKey ? columnViews?.views[sharedKey] : undefined;
  const sharedSig = sharedView ? sharedView.map((item) => `${item.key}:${item.visible ? 1 : 0}`).join("|") : "";
  const [seenSig, setSeenSig] = useState(saved.sharedSig ?? "");
  const lockedKey = sharedKey ? columns[0]?.key : undefined;
  const [sharing, setSharing] = useState<{ tone: "ok" | "danger"; text: string } | null>(null);
  const [listDrag, setListDrag] = useState<string | null>(null);
  function applyShared() {
    if (!sharedView) return;
    const shown = new Map(sharedView.map((item) => [item.key, item.visible]));
    setHidden(columns.filter((column) => shown.has(column.key) ? !shown.get(column.key) : column.hidden).map((column) => column.key));
    setOrder(sharedView.map((item) => item.key));
    setSeenSig(sharedSig);
  }
  useEffect(() => {
    if (sharedView && sharedSig !== seenSig) applyShared();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sharedSig]);
  const [widths, setWidths] = useState<Record<string, number>>(saved.widths ?? {});
  const [titles, setTitles] = useState<Record<string, string>>(saved.titles ?? {});
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [expanded, setExpanded] = useState<string | null>(null);
  const [limit, setLimit] = useState(pageSize);
  const [choosingColumns, setChoosingColumns] = useState(false);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [running, setRunning] = useState("");
  // Cell editing like a sheet: the active cell (and a range in its column from `rangeFrom`), the open editor, the note
  // about a pasted or filled range.
  const [active, setActive] = useState<CellRef | null>(null);
  const [rangeFrom, setRangeFrom] = useState<string | null>(null);
  const [editing, setEditing] = useState<Editing | null>(null);
  const editingRef = useRef<Editing | null>(null);
  editingRef.current = editing;
  const [note, setNote] = useState<Note | null>(null);
  const [dropTarget, setDropTarget] = useState<string | null>(null);
  const dragKey = useRef<string | null>(null);
  // An opened row's card stays inside the visible part of a table wider than the screen (its cell spans
  // every column): the card is pinned to the left edge and as wide as the scroll box, not the table.
  const wrapRef = useRef<HTMLDivElement>(null);
  // Columns size by their content and the spare width goes to a `grow` column or to an empty last column — in the
  // automatic table layout. A screen that pins its widths with `table-layout: fixed` keeps them: no `fit`, no filler.
  const tableRef = useRef<HTMLTableElement>(null);
  const [fixedLayout, setFixedLayout] = useState(false);
  useLayoutEffect(() => {
    const table = tableRef.current;
    if (table && typeof window.getComputedStyle === "function") setFixedLayout(window.getComputedStyle(table).tableLayout === "fixed");
  }, []);
  useEffect(() => {
    const wrap = wrapRef.current;
    if (!wrap || typeof ResizeObserver === "undefined") return;
    const sync = () => wrap.style.setProperty("--detail-width", `${wrap.clientWidth}px`);
    sync();
    const observer = new ResizeObserver(sync);
    observer.observe(wrap);
    return () => observer.disconnect();
  }, []);

  // A screen may keep its own fields under the same key (the «В работе» sheet's groups): they are written back as found.
  useEffect(() => {
    const { order: _order, widths: _widths, titles: _titles, ...kept } = readSaved(storageKey);
    writeSaved(storageKey, {
    ...kept, hidden, ...(seenSig ? { sharedSig: seenSig } : {}), known: [...new Set([...(kept.known ?? []), ...columns.map((column) => column.key)])], sort, preset,
    ...(order ? { order } : {}),
    ...(Object.keys(widths).length ? { widths } : {}),
    ...(Object.keys(titles).length ? { titles } : {}),
    });
  }, [storageKey, hidden, sort, preset, order, widths, titles, columns, seenSig]);

  // The user's order and captions: everything below (headers, search hint, filters, Excel) reads these columns.
  const ordered = useMemo(() => orderColumns(columns, order), [columns, order]);
  const arranged = useMemo(() => ordered.map((column) => {
    const title = titles[column.key]?.trim();
    return title && title !== column.title ? { ...column, title, hint: column.hint ?? column.title } : column;
  }), [ordered, titles]);
  const visible = arranged.filter((column) => column.key === lockedKey || !hidden.includes(column.key));
  const activePreset = presets.find((item) => item.label === preset);

  const searched = useMemo(() => searchRows(rows.filter((row) => {
    for (const [key, wanted] of Object.entries(filters)) {
      if (!wanted) continue;
      const column = columns.find((item) => item.key === key);
      if (column && String(column.value(row) ?? "") !== wanted) return false;
    }
    return true;
  }), (row) => [...columns.map((column) => column.value(row)), ...(searchText?.(row) ?? [])], query), [rows, columns, filters, query, searchText]);
  const counted = presetCountsFollowSearch ? searched : rows;

  const filtered = useMemo(() => {
    const result = searched.filter((row) => !activePreset || activePreset.test(row));
    // With a sort inside groups the rows keep the screen's order here (it places the groups); the groups sort below.
    if (sort && !groupSort) {
      const column = columns.find((item) => item.key === sort.key);
      if (column) {
        result.sort((a, b) => sort.direction * compareCells(column.value(a), column.value(b), column.numeric));
      }
    }
    return result;
  }, [searched, columns, sort, activePreset, groupSort]);

  const shown = filtered.slice(0, limit);
  const keys = new Set(rows.map(rowKey));
  const selectedRows = rows.filter((row) => selected.has(rowKey(row)));
  // Выбор переживает перезагрузку данных только для строк, которые остались.
  useEffect(() => {
    setSelected((current) => {
      const next = new Set([...current].filter((key) => keys.has(key)));
      return next.size === current.size ? current : next;
    });
  }, [rows]); // eslint-disable-line react-hooks/exhaustive-deps

  function toggleSort(key: string) {
    setSort((current) => {
      if (!current || current.key !== key) return { key, direction: 1 };
      if (current.direction === 1) return { key, direction: -1 };
      return null;
    });
  }

  // Shift+щелчок по флажку отмечает (или снимает) все строки от прошлого щелчка до этого, как в таблицах.
  const anchor = useRef<string | null>(null);
  function toggleRow(key: string, checked: boolean, shift: boolean) {
    const sequence = shown.map(rowKey);
    const from = anchor.current;
    setSelected((current) => selectRange(sequence, current, from, key, checked, shift));
    anchor.current = key;
  }

  const allShownSelected = shown.length > 0 && shown.every((row) => selected.has(rowKey(row)));
  function toggleAll(checked: boolean) {
    setSelected((current) => {
      const next = new Set(current);
      for (const row of shown) {
        if (checked) next.add(rowKey(row)); else next.delete(rowKey(row));
      }
      return next;
    });
  }

  function toggleGroup(groupRows: T[], checked: boolean) {
    setSelected((current) => {
      const next = new Set(current);
      for (const row of groupRows) {
        if (checked) next.add(rowKey(row)); else next.delete(rowKey(row));
      }
      return next;
    });
  }

  async function runBulk(action: DataBulkAction<T>) {
    setRunning(action.label);
    try {
      await action.run(selectedRows);
      setSelected(new Set());
    } catch { /* ошибку показывает страница, выбор остаётся */ } finally {
      setRunning("");
    }
  }

  function download() {
    const blob = new Blob([utf16Bytes(tableText(filtered, visible))], { type: "text/tab-separated-values" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${exportName}.xls`;
    link.click();
    URL.revokeObjectURL(url);
  }

  // --- Columns: order, width, caption ---
  const moveColumn = (key: string, target: string) => setOrder(moveKey(ordered.map((column) => column.key), key, target));
  function shiftColumn(key: string, delta: number) {
    const list = ordered.map((column) => column.key);
    const target = list[list.indexOf(key) + delta];
    if (target) setOrder(moveKey(list, key, target));
  }
  const setWidth = (key: string, width: number | null) => setWidths((current) => {
    const next = { ...current };
    if (width === null) delete next[key]; else next[key] = clampWidth(width);
    return next;
  });
  /** Ширина текста колонки сейчас: перетянутая или измеренная по заголовку на экране. */
  function widthNow(key: string) {
    if (widths[key]) return widths[key];
    const head = [...(tableRef.current?.querySelectorAll<HTMLElement>("th[data-col]") ?? [])].find((item) => item.dataset.col === key);
    const measured = (head?.getBoundingClientRect().width ?? 0) - CELL_PAD;
    return measured > 0 ? measured : 120;
  }
  function startResize(event: ReactPointerEvent<HTMLSpanElement>, key: string) {
    event.preventDefault();
    event.stopPropagation();
    const handle = event.currentTarget;
    const start = event.clientX;
    const base = widthNow(key);
    try { handle.setPointerCapture(event.pointerId); } catch { /* jsdom, старые браузеры */ }
    const move = (next: PointerEvent) => setWidth(key, base + next.clientX - start);
    const stop = () => {
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", stop);
      handle.removeEventListener("pointercancel", stop);
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", stop);
    handle.addEventListener("pointercancel", stop);
  }
  function resetView() {
    setOrder(undefined);
    setWidths({});
    setTitles({});
    setHidden(defaultHidden());
  }
  /** «Только нужные»: общий набор, а пока его нет — умолчание таблицы. */
  function onlyNeeded() {
    if (sharedView) { applyShared(); return; }
    setOrder(undefined);
    setHidden(defaultHidden());
  }
  async function publishView() {
    if (!sharedKey || !columnViews) return;
    setSharing(null);
    try {
      await columnViews.save(sharedKey, ordered.map((column) => ({ key: column.key, visible: column.key === lockedKey || !hidden.includes(column.key) })));
      setSharing({ tone: "ok", text: "Сохранено для всех" });
    } catch (reason) {
      setSharing({ tone: "danger", text: failure(reason) });
    }
  }
  const customized = Boolean(order || Object.keys(widths).length || Object.keys(titles).length);

  const selectable = bulkActions.length > 0;
  const sized = !fixedLayout;
  const filler = sized && !visible.some((column) => column.grow);
  const span = visible.length + (selectable ? 1 : 0) + (filler ? 1 : 0);
  const filterValues = (column: DataColumn<T>) =>
    [...new Set(rows.map((row) => String(column.value(row) ?? "")))].sort((a, b) => compareCells(a, b));

  const groups: Array<[string, T[]]> = [];
  if (groupBy) {
    const byName = new Map<string, T[]>();
    for (const row of shown) {
      const name = groupBy(row);
      if (!byName.has(name)) { byName.set(name, []); groups.push([name, byName.get(name)!]); }
      byName.get(name)!.push(row);
    }
    // The header's sort orders the rows inside every group; the groups stay where they are.
    const column = groupSort && sort ? columns.find((item) => item.key === sort.key) : undefined;
    if (column && sort) {
      for (const [, groupRows] of groups) groupRows.sort((a, b) => directed(column.value(a), column.value(b), sort.direction, column.numeric));
    }
  } else groups.push(["", shown]);

  // --- Cells edited in place ---
  const lines = groups.flatMap(([name, groupRows]) => collapsibleGroups && collapsed.has(name) ? [] : groupRows);
  const byKey = new Map(rows.map((row) => [rowKey(row), row]));
  const editable = (column: DataColumn<T> | undefined, row: T | undefined): column is DataColumn<T> & { edit: DataCellEdit<T> } =>
    Boolean(column?.edit && row && (column.edit.enabled?.(row) ?? true));
  const textOf = (column: DataColumn<T>, row: T) => column.edit?.value ? column.edit.value(row) : String(column.value(row) ?? "");
  // A choice cell copies what it shows (the size «22», not the size row's id); a paste finds the option again.
  const copyOf = (column: DataColumn<T>, row: T) => column.edit?.options ? String(column.value(row) ?? "") : textOf(column, row);
  /** The value a typed or pasted text writes: a choice cell takes an option of its list, or refuses. */
  function resolve(column: DataColumn<T>, row: T, text: string): string {
    const options = column.edit?.options?.(row);
    if (!options || text === textOf(column, row)) return text;
    const option = findOption(options, text);
    if (!option) throw new Error(`«${text}» — нет в списке`);
    return option.value;
  }
  const nameOf = (row: T) => rowLabel?.(row) ?? rowKey(row);
  // A choice opened by the mouse shows its list at once; arrows in a closed list only move the draft (Enter writes it),
  // a pick by the mouse is written at once.
  const pickerOnOpen = useRef(false);
  const keyed = useRef(false);
  const hasEditing = visible.some((column) => column.edit);
  // The grid is one Tab stop: the active cell, or the first editable one.
  const entry: CellRef | null = active ?? (() => {
    for (const row of lines) {
      const column = visible.find((item) => editable(item, row));
      if (column) return { row: rowKey(row), col: column.key };
    }
    return null;
  })();

  function step(cell: CellRef, where: Step): CellRef | null {
    const r = lines.findIndex((row) => rowKey(row) === cell.row);
    const c = visible.findIndex((column) => column.key === cell.col);
    if (r < 0 || c < 0) return null;
    if (where === "up" || where === "down") {
      const by = where === "down" ? 1 : -1;
      for (let i = r + by; i >= 0 && i < lines.length; i += by) {
        if (editable(visible[c], lines[i])) return { row: rowKey(lines[i]), col: cell.col };
      }
      return null;
    }
    const forward = where === "right" || where === "next";
    const wrap = where === "next" || where === "prev";
    let i = r;
    let j = c;
    for (;;) {
      j += forward ? 1 : -1;
      if (j < 0 || j >= visible.length) {
        if (!wrap) return null;
        i += forward ? 1 : -1;
        if (i < 0 || i >= lines.length) return null;
        j = forward ? 0 : visible.length - 1;
      }
      if (editable(visible[j], lines[i])) return { row: rowKey(lines[i]), col: visible[j].key };
    }
  }

  /** Rows of the selected range (in the active cell's column, as they stand on the screen), top first. */
  function rangeRows(): T[] {
    if (!active) return [];
    const column = visible.find((item) => item.key === active.col);
    const at = lines.findIndex((row) => rowKey(row) === active.row);
    const from = rangeFrom === null ? at : lines.findIndex((row) => rowKey(row) === rangeFrom);
    if (!column || at < 0 || from < 0) return [];
    return lines.slice(Math.min(at, from), Math.max(at, from) + 1).filter((row) => editable(column, row));
  }

  function select(cell: CellRef | null, extend = false) {
    if (!cell) return;
    if (extend && active && active.col === cell.col) setRangeFrom((current) => current ?? active.row);
    else setRangeFrom(null);
    setActive(cell);
  }

  function startEdit(cell: CellRef, draft?: string, byPointer = false) {
    const row = byKey.get(cell.row);
    const column = visible.find((item) => item.key === cell.col);
    if (!editable(column, row)) return;
    const current = textOf(column, row!);
    let text = draft ?? current;
    const options = column.edit.options?.(row!);
    if (options && draft !== undefined) {
      // A typed letter picks the first option starting with it; Delete — the empty option when the list has one.
      const fold = (value: string) => value.toLowerCase();
      const option = draft === "" ? options.find((item) => item.value === "")
        : findOption(options, draft) ?? options.find((item) => fold(item.label).startsWith(fold(draft)));
      text = option ? option.value : current;
    }
    setNote(null);
    setRangeFrom(null);
    setActive(cell);
    keyed.current = false;
    pickerOnOpen.current = Boolean(options && byPointer);
    setEditing({ ...cell, draft: text, error: "", saving: false });
  }

  async function refresh() {
    try { await onEdited?.(); } catch { /* перечитать не вышло — строки остаются прежними */ }
  }

  async function commit(then: Step | "stay") {
    const edit = editingRef.current;
    if (!edit || edit.saving) return;
    const row = byKey.get(edit.row);
    const column = visible.find((item) => item.key === edit.col);
    const value = column?.edit?.options ? edit.draft : edit.draft.trim();
    const move = () => { if (then !== "stay") select(step(edit, then) ?? edit); };
    if (!editable(column, row) || value === textOf(column, row!).trim()) {
      setEditing(null);
      move();
      return;
    }
    setEditing({ ...edit, saving: true, error: "" });
    const still = (current: Editing | null) => sameCell(current, edit);
    try {
      await column.edit.save(row!, value);
    } catch (reason) {
      const text = failure(reason);
      // The typed value stays in the cell with the reason; the rows are read again, so Enter writes over the fresh value.
      setEditing((current) => still(current) ? { ...edit, saving: false, error: text } : current);
      if (!still(editingRef.current)) setNote({ tone: "danger", text: `${nameOf(row!)}, ${column.title}: ${text}` });
      await refresh();
      return;
    }
    await refresh();
    setEditing((current) => still(current) ? null : current);
    if (still(editingRef.current)) move();
  }

  /** Paste and fill-down: the same save per cell, one after another, and one line about the result. */
  async function writeMany(column: DataColumn<T>, cells: Array<{ row: T; value: string }>) {
    if (!column.edit) return;
    const failed: string[] = [];
    // A choice cell writes the option the text names; a text that names none is refused for that row.
    const todo = cells.filter((cell) => editable(column, cell.row)).flatMap((cell) => {
      try { return [{ row: cell.row, value: resolve(column, cell.row, cell.value.trim()) }]; } catch (reason) {
        failed.push(`${nameOf(cell.row)} — ${failure(reason)}`);
        return [];
      }
    }).filter((cell) => cell.value !== textOf(column, cell.row).trim());
    const total = todo.length + failed.length;
    if (!total) return;
    setNote({ tone: "info", text: `Записываем ячеек: ${todo.length}…` });
    for (const cell of todo) {
      try { await column.edit.save(cell.row, cell.value); } catch (reason) { failed.push(`${nameOf(cell.row)} — ${failure(reason)}`); }
    }
    await refresh();
    setNote(failed.length
      ? { tone: "danger", text: `Не записано ${failed.length} из ${total}: ${failed.slice(0, 3).join("; ")}${failed.length > 3 ? "…" : ""}` }
      : { tone: "ok", text: `${column.title}: записано ячеек — ${todo.length}` });
  }

  function paste(text: string) {
    if (!active) return;
    const column = visible.find((item) => item.key === active.col);
    if (!column?.edit) return;
    // A column copied from Excel or Sheets: one value per line (the first cell of a line copied with several columns).
    const values = text.replace(/\r\n?/g, "\n").replace(/\n+$/, "").split("\n").map((line) => line.split("\t")[0]);
    const range = rangeRows();
    if (values.length === 1 && range.length > 1) {
      void writeMany(column, range.map((row) => ({ row, value: values[0] })));
      return;
    }
    const start = lines.indexOf(range[0] ?? byKey.get(active.row)!);
    const targets = lines.slice(Math.max(start, 0)).filter((row) => editable(column, row)).slice(0, values.length);
    void writeMany(column, targets.map((row, index) => ({ row, value: values[index] })));
  }

  function fillDown() {
    if (!active) return;
    const column = visible.find((item) => item.key === active.col);
    if (!column?.edit) return;
    const range = rangeRows();
    if (range.length > 1) {
      const value = textOf(column, range[0]);
      void writeMany(column, range.slice(1).map((row) => ({ row, value })));
      return;
    }
    // One cell: the value of the editable cell above, as Ctrl+D in a sheet.
    const above = step(active, "up");
    const source = above && byKey.get(above.row);
    const row = byKey.get(active.row);
    if (source && row) void writeMany(column, [{ row, value: textOf(column, source) }]);
  }

  function cellKeys(event: ReactKeyboardEvent<HTMLTableCellElement>, cell: CellRef) {
    if (event.target !== event.currentTarget) return;
    const ctrl = event.ctrlKey || event.metaKey;
    const arrows: Record<string, Step> = { ArrowUp: "up", ArrowDown: "down", ArrowLeft: "left", ArrowRight: "right" };
    if (event.key === "Enter" || event.key === "F2") { event.preventDefault(); startEdit(cell); return; }
    if (event.key === "Escape") {
      event.preventDefault();
      setActive(null);
      setRangeFrom(null);
      const line = event.currentTarget.closest("tr");
      if (line?.tabIndex === 0) line.focus(); else event.currentTarget.blur();
      return;
    }
    if (event.key === "Delete" || event.key === "Backspace") { event.preventDefault(); startEdit(cell, ""); return; }
    if (ctrl && event.code === "KeyD") { event.preventDefault(); fillDown(); return; }
    if (arrows[event.key]) {
      event.preventDefault();
      const where = arrows[event.key];
      select(step(cell, where), event.shiftKey && (where === "up" || where === "down"));
      return;
    }
    if (event.key === "Tab") {
      const next = step(cell, event.shiftKey ? "prev" : "next");
      if (next) { event.preventDefault(); select(next); }
      return;
    }
    if (!ctrl && !event.altKey && event.key.length === 1) { event.preventDefault(); startEdit(cell, event.key); }
  }

  function editorKeys(event: ReactKeyboardEvent<HTMLInputElement | HTMLSelectElement>) {
    event.stopPropagation();
    if (event.key === "Enter") { event.preventDefault(); void commit(event.shiftKey ? "up" : "down"); }
    else if (event.key === "Tab") { event.preventDefault(); void commit(event.shiftKey ? "prev" : "next"); }
    else if (event.key === "Escape") { event.preventDefault(); setEditing(null); }
  }

  // Keyboard focus follows the active cell (not while its editor is open: the editor has it).
  useEffect(() => {
    if (!active || (editing && sameCell(editing, active))) return;
    const cell = [...(tableRef.current?.querySelectorAll<HTMLElement>("td[data-col]") ?? [])]
      .find((item) => item.dataset.col === active.col && item.dataset.row === active.row);
    if (cell && document.activeElement !== cell) cell.focus();
  }, [active, editing]);

  const inRange = new Set(rangeFrom !== null ? rangeRows().map(rowKey) : []);

  function renderCell(row: T, column: DataColumn<T>, index: number) {
    const key = rowKey(row);
    const width = widths[column.key];
    const sizedColumn = width ? { ...column, width } : column;
    const classes = [columnClass(sizedColumn, index, sized), width ? "dt-w" : ""];
    const style = width ? { "--dt-w": `${width}px` } as CSSProperties : undefined;
    const content = column.render ? column.render(row) : cellContent(column, column.value(row));
    const body = column.wrap
      ? <div className="cell-wrap" style={wrapStyle(column)}>{content}</div>
      // A narrowed column cuts long text with «…»: the whole value on hover.
      : width ? <div className="dt-clip" title={String(column.value(row) ?? "") || undefined}>{content}</div> : content;
    if (!editable(column, row)) {
      if (column.computed && hasEditing) classes.push("dt-computed");
      return <td key={column.key} className={classes.filter(Boolean).join(" ") || undefined} style={style} data-label={column.title}>{body}</td>;
    }
    const cell = { row: key, col: column.key };
    const isActive = sameCell(active, cell);
    const edit = editing && sameCell(editing, cell) ? editing : null;
    const options = edit ? column.edit.options?.(row) : undefined;
    if (column.edit.options) classes.push("dt-choice");
    classes.push("dt-editable", isActive ? "dt-active" : "", inRange.has(key) && active?.col === column.key ? "dt-in-range" : "",
      edit ? "dt-editing" : "", edit?.error ? "dt-error" : "");
    return (
      <td
        key={column.key}
        className={classes.filter(Boolean).join(" ")}
        style={style}
        data-label={column.title}
        data-row={key}
        data-col={column.key}
        tabIndex={edit ? undefined : sameCell(entry, cell) ? 0 : -1}
        onClick={(event) => {
          event.stopPropagation();
          if (edit) return;
          if (isActive && !event.shiftKey) startEdit(cell, undefined, true); else select(cell, event.shiftKey);
        }}
        onDoubleClick={(event) => { event.stopPropagation(); if (!edit) startEdit(cell, undefined, true); }}
        onKeyDown={(event) => cellKeys(event, cell)}
        onCopy={(event) => {
          if (edit || event.target !== event.currentTarget) return;
          event.preventDefault();
          const range = rangeRows();
          event.clipboardData.setData("text/plain", (range.length ? range : [row]).map((item) => copyOf(column, item)).join("\r\n"));
        }}
        onPaste={(event) => {
          if (edit || event.target !== event.currentTarget) return;
          event.preventDefault();
          paste(event.clipboardData.getData("text/plain"));
        }}
      >
        {edit ? <>
          {options ? <select
            className="dt-cell-input dt-cell-select"
            aria-label={`${column.title}: ${nameOf(row)}`}
            aria-invalid={edit.error ? true : undefined}
            aria-busy={edit.saving || undefined}
            autoFocus
            disabled={edit.saving}
            value={edit.draft}
            ref={(select) => {
              if (!select || !pickerOnOpen.current) return;
              pickerOnOpen.current = false;
              try { (select as HTMLSelectElement & { showPicker?: () => void }).showPicker?.(); } catch { /* the list opens on the next click */ }
            }}
            onPointerDown={() => { keyed.current = false; }}
            onChange={(event) => {
              const draft = event.target.value;
              const next = { ...edit, draft, error: "" };
              setEditing((current) => current && sameCell(current, edit) ? next : current);
              if (!keyed.current) { editingRef.current = next; void commit("stay"); }
            }}
            onKeyDown={(event) => { keyed.current = true; editorKeys(event); }}
            onClick={(event) => event.stopPropagation()}
            onBlur={() => { const current = editingRef.current; if (current && sameCell(current, edit) && !current.saving && !current.error) void commit("stay"); }}
          >
            {!options.some((option) => option.value === edit.draft) && <option value={edit.draft}>{copyOf(column, row) || EMPTY}</option>}
            {options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
          </select> : <input
            className="dt-cell-input"
            autoComplete="off"
            aria-label={`${column.title}: ${nameOf(row)}`}
            aria-invalid={edit.error ? true : undefined}
            aria-busy={edit.saving || undefined}
            autoFocus
            inputMode={column.numeric ? "decimal" : undefined}
            readOnly={edit.saving}
            value={edit.draft}
            onChange={(event) => { const draft = event.target.value; setEditing((current) => current && sameCell(current, edit) ? { ...current, draft, error: "" } : current); }}
            onKeyDown={editorKeys}
            onClick={(event) => event.stopPropagation()}
            onBlur={() => { const current = editingRef.current; if (current && sameCell(current, edit) && !current.saving && !current.error) void commit("stay"); }}
          />}
          {edit.error && <small className="cell-edit-error" role="alert">{edit.error}</small>}
        </> : body}
      </td>
    );
  }

  function renderRow(row: T) {
    const key = rowKey(row);
    const open = expanded === key;
    const activate = onRowClick ? () => onRowClick(row) : renderDetail ? () => setExpanded(open ? null : key) : undefined;
    return (
      <Fragment key={key}>
        <tr
          className={[rowClassName?.(row) ?? "", activate ? "clickable" : "", open ? "open" : ""].join(" ").trim() || undefined}
          title={rowTitle?.(row) || undefined}
          onClick={activate}
          // A clickable row opens from the keyboard too: Tab to it, Enter (erp-dense-ui «Enter opens a row»).
          tabIndex={activate ? 0 : undefined}
          aria-expanded={renderDetail && !onRowClick ? open : undefined}
          onKeyDown={activate ? (event) => {
            if (event.target === event.currentTarget && (event.key === "Enter" || event.key === " ")) { event.preventDefault(); activate(); }
          } : undefined}
        >
          {selectable && (
            <td onClick={(event) => event.stopPropagation()}>
              <label className="sel-hit"><input type="checkbox" aria-label={rowLabel ? `Выбрать: ${rowLabel(row)}` : `Выбрать ${key}`} checked={selected.has(key)} onChange={() => undefined} onClick={(event) => toggleRow(key, event.currentTarget.checked, event.shiftKey)} /></label>
            </td>
          )}
          {visible.map((column, index) => renderCell(row, column, index))}
          {filler && <td className="dt-fill" aria-hidden="true" />}
        </tr>
        {open && renderDetail && (
          <tr className="detail"><td colSpan={span}><div className="detail-body">{renderDetail(row)}</div></td></tr>
        )}
      </Fragment>
    );
  }

  const filterColumns = arranged.filter((column) => column.filter);
  // A filter is on when a value is chosen; "" means «все».
  const activeFilters = filterColumns.filter((column) => (filters[column.key] ?? "") !== "");

  return (
    <section className={phoneCards ? "data-table dt-cards" : "data-table"} aria-label={label}>
      {!compact && <div className="filters table-toolbar">
        {!hideSearch && <form className="table-search" role="search" onSubmit={(event) => { event.preventDefault(); onSearch?.(query.trim()); }}>
          <input className="search" type="search" aria-label={searchLabel ?? `Поиск: ${label}`} placeholder={searchPlaceholder ?? searchHint(visible)} value={query} onChange={(event) => { setQuery(event.target.value); setLimit(pageSize); }} />
          {onSearch && <button type="submit" className="secondary small">Найти</button>}
        </form>}
        {filterColumns.length > 0 && (
          <button type="button" className={activeFilters.length ? "secondary small filter-toggle active" : "secondary small filter-toggle"} aria-expanded={filtersOpen} onClick={() => setFiltersOpen((open) => !open)}>
            Фильтры{activeFilters.length ? ` · ${activeFilters.length}` : ""}
          </button>
        )}
        <div className="toolbar-end">
          {toolbar}
          <button type="button" className="secondary small" aria-expanded={choosingColumns} onClick={() => setChoosingColumns((current) => !current)}>Колонки</button>
          {exportName && <button type="button" className="secondary small" onClick={download}>Excel</button>}
        </div>
      </div>}
      {activeFilters.length > 0 && (
        <div className="filter-tags" aria-label="Выбранные фильтры">
          {activeFilters.map((column) => (
            <button type="button" key={column.key} className="filter-tag" aria-label={`Снять фильтр: ${column.title}`} onClick={() => setFilters((current) => ({ ...current, [column.key]: "" }))}>
              {column.title}: <b>{filters[column.key] || "—"}</b> <span aria-hidden="true">✕</span>
            </button>
          ))}
          {activeFilters.length > 1 && <button type="button" className="link-button" onClick={() => setFilters({})}>Сбросить все</button>}
        </div>
      )}
      <div className="filter-panel" hidden={!filtersOpen}>
        {filterColumns.map((column) => (
          <select key={column.key} aria-label={`Фильтр: ${column.title}`} value={filters[column.key] ?? ""} onChange={(event) => setFilters((current) => ({ ...current, [column.key]: event.target.value }))}>
            <option value="">{column.title}: все</option>
            {filterValues(column).map((value) => <option key={value} value={value}>{value || "—"}</option>)}
          </select>
        ))}
      </div>
      {choosingColumns && (
        <fieldset className="column-chooser">
          <legend>Показывать колонки</legend>
          <div className="column-chooser-head">
            <span>Показано <b>{visible.length}</b> из {columns.length}. Порядок — перетаскиванием строки; ширину можно менять и в заголовке.</span>
            <span className="column-actions">
              {sharing && <span className={sharing.tone === "ok" ? "tone-ok" : "tone-danger"} role="status">{sharing.text}</span>}
              <button type="button" className="secondary small" disabled={hidden.length === 0} onClick={() => setHidden([])}>Показать все</button>
              <button type="button" className="secondary small" onClick={onlyNeeded}>Только нужные</button>
              {sharedKey && columnViews?.canEdit && <button type="button" className="primary small" onClick={() => void publishView()}>Сохранить для всех</button>}
              <button type="button" className="secondary small" disabled={!customized && hidden.join() === defaultHidden().join()} onClick={resetView}>Сбросить вид</button>
            </span>
          </div>
          <ol className="column-list">
            {ordered.map((column, index) => {
              const width = widths[column.key];
              return (
                <li key={column.key} className={`column-item${listDrag === column.key ? " dragging" : ""}`}
                  onDragOver={(event) => { if (listDrag) event.preventDefault(); }}
                  onDrop={(event) => { event.preventDefault(); if (listDrag && listDrag !== column.key) moveColumn(listDrag, column.key); setListDrag(null); }}>
                  <span className="column-name">
                    <span className="column-grip" draggable aria-hidden="true" title="Перетащите, чтобы переставить"
                      onDragStart={(event) => { setListDrag(column.key); event.dataTransfer.effectAllowed = "move"; event.dataTransfer.setData("text/plain", column.key); }}
                      onDragEnd={() => setListDrag(null)}>⠿</span>
                    <label className="check-inline">
                      <input type="checkbox" checked={column.key === lockedKey || !hidden.includes(column.key)} disabled={column.key === lockedKey} title={column.key === lockedKey ? "Опознаватель записи — всегда в таблице" : undefined} onChange={(event) => setHidden((current) => event.target.checked ? current.filter((key) => key !== column.key) : [...current, column.key])} />
                      {column.title}
                    </label>
                    {column.remove && <button type="button" className="link-button danger-link column-remove" aria-label={`Удалить столбец: ${column.title}`} title="Удалить свой столбец" onClick={column.remove}>Удалить</button>}
                  </span>
                  <input
                    className="column-caption" aria-label={`Своя подпись: ${column.title}`} placeholder="своя подпись…" maxLength={60} autoComplete="off"
                    value={titles[column.key] ?? ""}
                    onChange={(event) => { const value = event.target.value; setTitles((current) => { const next = { ...current, [column.key]: value }; if (!value.trim()) delete next[column.key]; return next; }); }}
                  />
                  <span className="column-tools">
                    <button type="button" className="secondary small" aria-label={`Выше: ${column.title}`} title="Выше" disabled={index === 0} onClick={() => shiftColumn(column.key, -1)}>↑</button>
                    <button type="button" className="secondary small" aria-label={`Ниже: ${column.title}`} title="Ниже" disabled={index === ordered.length - 1} onClick={() => shiftColumn(column.key, 1)}>↓</button>
                    <button type="button" className="secondary small" aria-label={`Уже: ${column.title}`} title="Уже" onClick={() => setWidth(column.key, widthNow(column.key) - WIDTH_STEP)}>−</button>
                    <button type="button" className="link-button column-width" aria-label={`Ширина ${column.title}: ${width ? `${width} пикселей, вернуть по содержимому` : "по содержимому"}`} title={width ? "Вернуть ширину по содержимому" : undefined} disabled={!width} onClick={() => setWidth(column.key, null)}>{width ? `${width}\u00a0px` : "авто"}</button>
                    <button type="button" className="secondary small" aria-label={`Шире: ${column.title}`} title="Шире" onClick={() => setWidth(column.key, widthNow(column.key) + WIDTH_STEP)}>+</button>
                  </span>
                </li>
              );
            })}
          </ol>
        </fieldset>
      )}
      {presets.length > 0 && (
        <div className="calendar-chips" role="group" aria-label="Быстрый отбор">
          {!allLast && <button type="button" className={`calendar-chip${preset ? "" : " active"}`} aria-pressed={!preset} onClick={() => setPreset("")}>Все · {counted.length}</button>}
          {presets.map((item) => (
            <button type="button" key={item.label} className={`calendar-chip${preset === item.label ? " active" : ""}`} aria-pressed={preset === item.label} onClick={() => setPreset(preset === item.label ? "" : item.label)}>
              {item.label} · {counted.filter(item.test).length}
            </button>
          ))}
          {allLast && <button type="button" className={`calendar-chip${preset ? "" : " active"}`} aria-pressed={!preset} onClick={() => setPreset("")}>Все · {counted.length}</button>}
        </div>
      )}
      {selectable && selectedRows.length > 0 && (
        <div className="bulk-bar" role="toolbar" aria-label="Действия с выбранными">
          <span>Выбрано: {selectedRows.length}</span>
          {bulkActions.filter((action) => !action.hidden?.(selectedRows)).map((action) => (
            <button type="button" key={action.label} className="primary small" disabled={Boolean(running) || action.disabled?.(selectedRows)} onClick={() => void runBulk(action)}>
              {running === action.label ? "Выполняем…" : action.label}
            </button>
          ))}
          <button type="button" className="secondary small" onClick={() => setSelected(new Set())}>Снять выбор</button>
        </div>
      )}
      <div className="table-wrap" ref={wrapRef}>
        <table ref={tableRef} className={["grid wide", selectable ? "selectable" : "", sized ? "sized" : "", hasEditing ? "dt-edit-grid" : ""].filter(Boolean).join(" ")}>
          <thead>
            <tr>
              {selectable && <th><label className="sel-hit"><input type="checkbox" aria-label="Выбрать все показанные" checked={allShownSelected} onChange={(event) => toggleAll(event.target.checked)} /></label></th>}
              {visible.map((column, index) => {
                const direction = sort?.key === column.key ? sort.direction : 0;
                const width = widths[column.key];
                const dragged = dragKey.current;
                const drop = dropTarget === column.key && dragged
                  ? visible.findIndex((item) => item.key === dragged) < index ? "dt-drop-after" : "dt-drop-before" : "";
                return (
                  <th
                    key={column.key}
                    data-col={column.key}
                    className={[columnClass(width ? { ...column, width } : column, index, sized), width ? "dt-w" : "", drop].filter(Boolean).join(" ") || undefined}
                    style={width ? { "--dt-w": `${width}px` } as CSSProperties : column.width ? { width: column.width } : undefined}
                    title={column.hint ?? (column.computed && hasEditing ? "Считается — в таблице не правится"
                      : groupSort ? "Сортировка — внутри каждой группы" : undefined)}
                    aria-sort={direction === 1 ? "ascending" : direction === -1 ? "descending" : "none"}
                    draggable
                    onDragStart={(event) => {
                      if ((event.target as HTMLElement).closest?.(".dt-resize")) { event.preventDefault(); return; }
                      dragKey.current = column.key;
                      if (event.dataTransfer) { event.dataTransfer.effectAllowed = "move"; event.dataTransfer.setData("text/plain", column.title); }
                    }}
                    onDragOver={(event) => {
                      if (!dragKey.current || dragKey.current === column.key) return;
                      event.preventDefault();
                      if (dropTarget !== column.key) setDropTarget(column.key);
                    }}
                    onDragLeave={() => setDropTarget((current) => current === column.key ? null : current)}
                    onDrop={(event) => {
                      event.preventDefault();
                      const moved = dragKey.current;
                      dragKey.current = null;
                      setDropTarget(null);
                      if (moved && moved !== column.key) moveColumn(moved, column.key);
                    }}
                    onDragEnd={() => { dragKey.current = null; setDropTarget(null); }}
                  >
                    <button type="button" className="sort-head" onClick={() => toggleSort(column.key)}>
                      {column.title}{direction === 1 ? " ▲" : direction === -1 ? " ▼" : ""}
                    </button>
                    <span
                      className="dt-resize"
                      aria-hidden="true"
                      title="Тянуть — ширина, двойной щелчок — по содержимому"
                      draggable={false}
                      onPointerDown={(event) => startResize(event, column.key)}
                      onDoubleClick={() => setWidth(column.key, null)}
                      onClick={(event) => event.stopPropagation()}
                    />
                  </th>
                );
              })}
              {filler && <th className="dt-fill" aria-hidden="true" />}
            </tr>
          </thead>
          <tbody>
            {groups.map(([name, groupRows]) => (
              <Fragment key={`group:${name}`}>
                {groupBy && (
                  <tr className="group-row">
                    <td colSpan={span}>
                      {selectable && (() => {
                        const picked = groupRows.filter((row) => selected.has(rowKey(row))).length;
                        return (
                          <input
                            type="checkbox"
                            aria-label="Выбрать группу"
                            title="Выбрать группу"
                            checked={picked === groupRows.length}
                            ref={(input) => { if (input) input.indeterminate = picked > 0 && picked < groupRows.length; }}
                            onChange={(event) => toggleGroup(groupRows, event.currentTarget.checked)}
                          />
                        );
                      })()}
                      {(() => {
                        const picked = selectable ? groupRows.filter((row) => selected.has(rowKey(row))).length : 0;
                        const title = <><strong>{(groupLabel ? groupLabel(name, groupRows) : name) || "—"}</strong> · {groupRows.length}{picked > 0 && <span className="group-picked"> · выбрано {picked}</span>}</>;
                        return collapsibleGroups
                          ? <button type="button" aria-expanded={!collapsed.has(name)} onClick={() => setCollapsed((current) => {
                            const next = new Set(current);
                            if (!next.delete(name)) next.add(name);
                            return next;
                          })} style={{ all: "unset", cursor: "pointer" }}>{collapsed.has(name) ? "▸" : "▾"} {title}</button>
                          : title;
                      })()}
                    </td>
                  </tr>
                )}
                {!(collapsibleGroups && collapsed.has(name)) && groupRows.map(renderRow)}
              </Fragment>
            ))}
            {!shown.length && <tr><td colSpan={span} className="empty">{emptyText}</td></tr>}
          </tbody>
        </table>
        {filtered.length > shown.length && <LoadMore token={shown.length} onMore={() => setLimit((current) => current + pageSize)} />}
      </div>
      {!compact && (
        <div className="table-footer">
          <span>Показано {shown.length} из {filtered.length}{filtered.length !== rows.length ? ` (всего ${rows.length})` : ""}</span>
          {note && <span className={`dt-edit-note tone-${note.tone}`} role="status">{note.text}</span>}
          {!note && hasEditing && <span className="dt-edit-hint">Щелчок по ячейке и ввод — правка · Enter — записать · Esc — отмена · Tab — дальше · Ctrl+V — вставить столбец · Ctrl+D — заполнить вниз</span>}
        </div>
      )}
    </section>
  );
}
