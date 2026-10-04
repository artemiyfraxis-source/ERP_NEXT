import { Fragment, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { selectRange } from "./bulk";
import { displayCell } from "./format";

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
  width?: number;
  /** Подсказка при наведении на заголовок (например, полное название при сокращённом). */
  hint?: string;
}

export interface DataPreset<T> {
  label: string;
  test: (row: T) => boolean;
}

export interface DataBulkAction<T> {
  label: string;
  run: (rows: T[]) => void | Promise<unknown>;
  disabled?: (rows: T[]) => boolean;
}

export interface DataTableProps<T> {
  rows: T[];
  columns: DataColumn<T>[];
  rowKey: (row: T) => string;
  /** Ключ для запоминания колонок, отбора и сортировки у пользователя (localStorage). */
  storageKey?: string;
  label?: string;
  presets?: DataPreset<T>[];
  /** Счётчики чипов — по строкам, прошедшим поиск и фильтры колонок (без самого чипа), как issFilterRender_ в
      SUPPLYMAP. По умолчанию — по всем строкам. */
  presetCountsFollowSearch?: boolean;
  groupBy?: (row: T) => string;
  bulkActions?: DataBulkAction<T>[];
  /** Раскрытая строка: форма, карточка, история. */
  renderDetail?: (row: T) => ReactNode;
  rowClassName?: (row: T) => string;
  /** Имя файла выгрузки без расширения; без него кнопки «Excel» нет. */
  exportName?: string;
  emptyText?: string;
  pageSize?: number;
  toolbar?: ReactNode;
  /** Поиск сразу по всем записям на сервере (строки на экране — только страница): «Найти» или Enter в том же поле. */
  onSearch?: (query: string) => void;
  searchLabel?: string;
  searchPlaceholder?: string;
  /** Пока строк меньше — без строки поиска/фильтров/колонок и без «Показано N из N»: маленькой таблице они
      только мешают (блоки «Мои позиции» в листе закупки). По умолчанию всегда полная. */
  compactBelow?: number;
}

type Sort = { key: string; direction: 1 | -1 } | null;

interface Saved {
  hidden?: string[];
  sort?: Sort;
  preset?: string;
}

/** Все слова запроса — хотя бы в одной ячейке строки (SUPPLYMAP stock05Match_). */
export function matchesWords(values: CellValue[], query: string) {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return true;
  const text = values.map((value) => String(value ?? "").toLowerCase()).join(" ");
  return words.every((word) => text.includes(word));
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

function readSaved(storageKey?: string): Saved {
  if (!storageKey) return {};
  try {
    return JSON.parse(window.localStorage.getItem(`erp-table:${storageKey}`) ?? "{}") as Saved;
  } catch {
    return {};
  }
}

function writeSaved(storageKey: string | undefined, saved: Saved) {
  if (!storageKey) return;
  try {
    window.localStorage.setItem(`erp-table:${storageKey}`, JSON.stringify(saved));
  } catch { /* хранилище недоступно — настройки живут до перезагрузки */ }
}

export function DataTable<T>({
  rows,
  columns,
  rowKey,
  storageKey,
  label = "Таблица",
  presets = [],
  presetCountsFollowSearch = false,
  groupBy,
  bulkActions = [],
  renderDetail,
  rowClassName,
  exportName,
  emptyText = "Ничего не найдено.",
  pageSize = 200,
  toolbar,
  onSearch,
  searchLabel,
  searchPlaceholder,
  compactBelow = 0,
}: DataTableProps<T>) {
  const compact = rows.length < compactBelow;
  const saved = useMemo(() => readSaved(storageKey), [storageKey]);
  const [query, setQuery] = useState("");
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [sort, setSort] = useState<Sort>(saved.sort ?? null);
  const [preset, setPreset] = useState(saved.preset ?? "");
  const [hidden, setHidden] = useState<string[]>(
    saved.hidden ?? columns.filter((column) => column.hidden).map((column) => column.key),
  );
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [expanded, setExpanded] = useState<string | null>(null);
  const [limit, setLimit] = useState(pageSize);
  const [choosingColumns, setChoosingColumns] = useState(false);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [running, setRunning] = useState("");
  // An opened row's card stays inside the visible part of a table wider than the screen (its cell spans
  // every column): the card is pinned to the left edge and as wide as the scroll box, not the table.
  const wrapRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const wrap = wrapRef.current;
    if (!wrap || typeof ResizeObserver === "undefined") return;
    const sync = () => wrap.style.setProperty("--detail-width", `${wrap.clientWidth}px`);
    sync();
    const observer = new ResizeObserver(sync);
    observer.observe(wrap);
    return () => observer.disconnect();
  }, []);

  useEffect(() => writeSaved(storageKey, { hidden, sort, preset }), [storageKey, hidden, sort, preset]);

  const visible = columns.filter((column) => !hidden.includes(column.key));
  const activePreset = presets.find((item) => item.label === preset);

  const searched = useMemo(() => rows.filter((row) => {
    for (const [key, wanted] of Object.entries(filters)) {
      if (!wanted) continue;
      const column = columns.find((item) => item.key === key);
      if (column && String(column.value(row) ?? "") !== wanted) return false;
    }
    return matchesWords(columns.map((column) => column.value(row)), query);
  }), [rows, columns, filters, query]);
  const counted = presetCountsFollowSearch ? searched : rows;

  const filtered = useMemo(() => {
    const result = searched.filter((row) => !activePreset || activePreset.test(row));
    if (sort) {
      const column = columns.find((item) => item.key === sort.key);
      if (column) {
        result.sort((a, b) => sort.direction * compareCells(column.value(a), column.value(b), column.numeric));
      }
    }
    return result;
  }, [searched, columns, sort, activePreset]);

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
    const order = shown.map(rowKey);
    const from = anchor.current;
    setSelected((current) => selectRange(order, current, from, key, checked, shift));
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

  const selectable = bulkActions.length > 0;
  const span = visible.length + (selectable ? 1 : 0);
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
  } else groups.push(["", shown]);

  function renderRow(row: T) {
    const key = rowKey(row);
    const open = expanded === key;
    return (
      <Fragment key={key}>
        <tr
          className={[rowClassName?.(row) ?? "", renderDetail ? "clickable" : "", open ? "open" : ""].join(" ").trim() || undefined}
          onClick={renderDetail ? () => setExpanded(open ? null : key) : undefined}
        >
          {selectable && (
            <td onClick={(event) => event.stopPropagation()}>
              <input type="checkbox" aria-label={`Выбрать ${key}`} checked={selected.has(key)} onChange={() => undefined} onClick={(event) => toggleRow(key, event.currentTarget.checked, event.shiftKey)} />
            </td>
          )}
          {visible.map((column, index) => (
            <td key={column.key} className={[column.numeric ? "num" : column.wrap ? "text" : "", index === 0 ? "key" : ""].join(" ").trim() || undefined}>
              {column.wrap
                ? <div className="cell-wrap">{column.render ? column.render(row) : displayCell(column.value(row)) ?? ""}</div>
                : column.render ? column.render(row) : displayCell(column.value(row)) ?? ""}
            </td>
          ))}
        </tr>
        {open && renderDetail && (
          <tr className="detail"><td colSpan={span}><div className="detail-body">{renderDetail(row)}</div></td></tr>
        )}
      </Fragment>
    );
  }

  const filterColumns = columns.filter((column) => column.filter);
  // A filter is on when a value is chosen; "" means «все».
  const activeFilters = filterColumns.filter((column) => (filters[column.key] ?? "") !== "");

  return (
    <section className="data-table" aria-label={label}>
      {!compact && <div className="filters table-toolbar">
        <form className="table-search" role="search" onSubmit={(event) => { event.preventDefault(); onSearch?.(query.trim()); }}>
          <input className="search" type="search" aria-label={searchLabel ?? `Поиск: ${label}`} placeholder={searchPlaceholder ?? "Поиск по всем колонкам"} value={query} onChange={(event) => { setQuery(event.target.value); setLimit(pageSize); }} />
          {onSearch && <button type="submit" className="secondary small">Найти</button>}
        </form>
        {filterColumns.length > 0 && (
          <button type="button" className={activeFilters.length ? "secondary small filter-toggle active" : "secondary small filter-toggle"} aria-expanded={filtersOpen} onClick={() => setFiltersOpen((open) => !open)}>
            Фильтры{activeFilters.length ? ` · ${activeFilters.length}` : ""}
          </button>
        )}
        <div className="toolbar-end">
          {toolbar}
          <button type="button" className="secondary small" onClick={() => setChoosingColumns((current) => !current)}>Колонки</button>
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
          {columns.map((column) => (
            <label key={column.key} className="check-inline">
              <input type="checkbox" checked={!hidden.includes(column.key)} onChange={(event) => setHidden((current) => event.target.checked ? current.filter((key) => key !== column.key) : [...current, column.key])} />
              {column.title}
            </label>
          ))}
        </fieldset>
      )}
      {presets.length > 0 && (
        <div className="calendar-chips" role="group" aria-label="Быстрый отбор">
          <button type="button" className={`calendar-chip${preset ? "" : " active"}`} aria-pressed={!preset} onClick={() => setPreset("")}>Все · {counted.length}</button>
          {presets.map((item) => (
            <button type="button" key={item.label} className={`calendar-chip${preset === item.label ? " active" : ""}`} aria-pressed={preset === item.label} onClick={() => setPreset(preset === item.label ? "" : item.label)}>
              {item.label} · {counted.filter(item.test).length}
            </button>
          ))}
        </div>
      )}
      {selectable && selectedRows.length > 0 && (
        <div className="bulk-bar" role="toolbar" aria-label="Действия с выбранными">
          <span>Выбрано: {selectedRows.length}</span>
          {bulkActions.map((action) => (
            <button type="button" key={action.label} className="primary small" disabled={Boolean(running) || action.disabled?.(selectedRows)} onClick={() => void runBulk(action)}>
              {running === action.label ? "Выполняем…" : action.label}
            </button>
          ))}
          <button type="button" className="secondary small" onClick={() => setSelected(new Set())}>Снять выбор</button>
        </div>
      )}
      <div className="table-wrap" ref={wrapRef}>
        <table className={selectable ? "grid wide selectable" : "grid wide"}>
          <thead>
            <tr>
              {selectable && <th><input type="checkbox" aria-label="Выбрать все показанные" checked={allShownSelected} onChange={(event) => toggleAll(event.target.checked)} /></th>}
              {visible.map((column) => {
                const direction = sort?.key === column.key ? sort.direction : 0;
                return (
                  <th key={column.key} className={column.numeric ? "num" : undefined} style={column.width ? { width: column.width } : undefined} title={column.hint} aria-sort={direction === 1 ? "ascending" : direction === -1 ? "descending" : "none"}>
                    <button type="button" className="sort-head" onClick={() => toggleSort(column.key)}>
                      {column.title}{direction === 1 ? " ▲" : direction === -1 ? " ▼" : ""}
                    </button>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {groups.map(([name, groupRows]) => (
              <Fragment key={`group:${name}`}>
                {groupBy && <tr className="group-row"><td colSpan={span}><strong>{name || "—"}</strong> · {groupRows.length}</td></tr>}
                {groupRows.map(renderRow)}
              </Fragment>
            ))}
            {!shown.length && <tr><td colSpan={span} className="empty">{emptyText}</td></tr>}
          </tbody>
        </table>
      </div>
      {!compact && (
        <div className="table-footer">
          <span>Показано {shown.length} из {filtered.length}{filtered.length !== rows.length ? ` (всего ${rows.length})` : ""}</span>
          {filtered.length > shown.length && <button type="button" className="secondary small" onClick={() => setLimit((current) => current + pageSize)}>Показать ещё</button>}
        </div>
      )}
    </section>
  );
}
