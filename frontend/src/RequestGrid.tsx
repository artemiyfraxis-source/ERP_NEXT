import { useEffect, useRef, type ClipboardEvent, type KeyboardEvent } from "react";
import type { RequestSchema } from "./api";
import {
  COMMON_KEYS,
  GRID_COLUMNS,
  GRID_LIMIT,
  LABEL_BY_KEY,
  positionsWord,
  submitLabel,
  type Common,
  type Draft,
  type GridKey,
  type RowErrors,
} from "./requestGridLogic";

type Props = {
  positions: Draft[];
  columns?: GridKey[];
  schema: RequestSchema | null;
  /** per-position errors; null = not shown yet (before the first send / paste) */
  errors: RowErrors[] | null;
  /** how many of the rows are sent (blank rows are not) */
  sendCount: number;
  common?: Common;
  busy: boolean;
  onField: (index: number, key: GridKey, value: string) => void;
  onPaste: (index: number, key: GridKey, text: string) => void;
  onAdd: () => void;
  onRemove: (index: number) => void;
  onDuplicate: (index: number) => void;
  onSubmit: () => void;
  removeLabel?: string;
  /** rows allowed (default: one request = 100 positions) */
  limit?: number;
};

/* Keys of a position that the grid has no column for: the whole-request fields. A row that carries its own
   value (from an Excel column) is marked, the value is in the tooltip. */
const HIDDEN_COMMON = COMMON_KEYS.filter((key) => !GRID_COLUMNS.some((column) => column.key === key));

/** One dense editable grid, one row per position: Tab/Enter/arrows move between cells, a block pasted from
 *  Excel or Sheets fills rows and columns. The bar under it stays on screen with the count and the send button. */
export function RequestGrid({
  positions, columns, schema, errors, sendCount, common, busy,
  onField, onPaste, onAdd, onRemove, onDuplicate, onSubmit, removeLabel = "Убрать", limit = GRID_LIMIT,
}: Props) {
  const root = useRef<HTMLDivElement>(null);
  const pendingFocus = useRef<{ row: number; key: GridKey } | null>(null);
  const shown = GRID_COLUMNS.filter((column) => !columns || columns.includes(column.key));
  const atLimit = positions.length >= limit;
  const badRows = errors ? errors.map((rowErrors, index) => (Object.keys(rowErrors).length ? index : -1)).filter((i) => i >= 0) : [];

  function cell(row: number, key: GridKey): HTMLElement | null {
    return root.current?.querySelector<HTMLElement>(`[data-cell="${row}:${key}"]`) ?? null;
  }

  useEffect(() => {
    const target = pendingFocus.current;
    if (!target) return;
    const element = cell(target.row, target.key);
    if (element) {
      pendingFocus.current = null;
      element.focus();
      if (element instanceof HTMLInputElement) element.select();
    }
  });

  function move(row: number, key: GridKey, delta: number) {
    const next = row + delta;
    if (next < 0) return;
    if (next >= positions.length) {
      if (atLimit) return;
      pendingFocus.current = { row: next, key };
      onAdd();
      return;
    }
    const element = cell(next, key);
    element?.focus();
    if (element instanceof HTMLInputElement) element.select();
  }

  function onKeyDown(event: KeyboardEvent, row: number, key: GridKey) {
    const target = event.target as HTMLElement;
    const isSelect = target instanceof HTMLSelectElement;
    const isDate = target instanceof HTMLInputElement && target.type === "date";
    if (event.key === "Enter") {
      event.preventDefault();
      move(row, key, event.shiftKey ? -1 : 1);
    } else if ((event.key === "ArrowDown" || event.key === "ArrowUp") && !isSelect && !isDate && !event.altKey) {
      event.preventDefault();
      move(row, key, event.key === "ArrowDown" ? 1 : -1);
    }
  }

  function handlePaste(event: ClipboardEvent, row: number, key: GridKey) {
    const text = event.clipboardData.getData("text/plain");
    // a single value pastes into the cell the usual way; a block (tabs or lines) goes through the grid
    if (!/[\t\n]/.test(text.replace(/\r?\n$/, ""))) return;
    event.preventDefault();
    onPaste(row, key, text);
  }

  function firstError() {
    if (!badRows.length || !errors) return;
    const row = badRows[0];
    const key = shown.find((column) => errors[row][column.key])?.key ?? shown[0].key;
    const element = cell(row, key);
    element?.scrollIntoView({ block: "center" });
    element?.focus();
  }

  function control(row: number, key: GridKey, draft: Draft, error: string | undefined) {
    const common = {
      "data-cell": `${row}:${key}`,
      "aria-label": `${GRID_COLUMNS.find((column) => column.key === key)!.label}, строка ${row + 1}`,
      "aria-invalid": error ? true : undefined,
      title: error,
      className: error ? "rg-bad" : undefined,
      onKeyDown: (event: KeyboardEvent) => onKeyDown(event, row, key),
      onPaste: (event: ClipboardEvent) => handlePaste(event, row, key),
    };
    const value = draft[key];
    const set = (next: string) => onField(row, key, next);
    if (key === "category") {
      const options = schema?.categories ?? [];
      return (
        <select {...common} value={value} onChange={(e) => set(e.target.value)}>
          {value && !options.includes(value) && <option>{value}</option>}
          {options.map((item) => <option key={item}>{item}</option>)}
        </select>
      );
    }
    if (key === "quality_document") {
      return (
        <select {...common} value={value} disabled={draft.category === "ОП"} onChange={(e) => set(e.target.value)}>
          <option value="">—</option><option>Да</option><option>Нет</option>
        </select>
      );
    }
    if (key === "planned_date") return <input {...common} type="date" value={value} onChange={(e) => set(e.target.value)} />;
    const list = key === "item_type" ? "rg-types" : key === "unit" ? "rg-units" : undefined;
    const numeric = key === "quantity" || key === "price" || key === "price_with_vat";
    return (
      <input
        {...common}
        list={list}
        inputMode={numeric ? "decimal" : undefined}
        value={value}
        placeholder={key === "link" ? "https://…" : undefined}
        onChange={(e) => set(e.target.value)}
      />
    );
  }

  return (
    <div className="rg" ref={root}>
      <datalist id="rg-types">{(schema?.item_types ?? []).map((item) => <option key={item} value={item} />)}</datalist>
      <datalist id="rg-units">{(schema?.units ?? []).map((item) => <option key={item} value={item} />)}</datalist>
      <div className="rg-scroll">
        <table className="rg-table">
          <thead>
            <tr>
              <th className="rg-no">№</th>
              {shown.map((column) => (
                <th key={column.key} className={`rg-col-${column.key}`}>{column.label}{column.required ? " *" : ""}</th>
              ))}
              <th className="rg-act" />
            </tr>
          </thead>
          <tbody>
            {positions.map((draft, row) => {
              const rowErrors = errors?.[row] ?? {};
              const own = common ? HIDDEN_COMMON.filter((key) => draft[key] && draft[key] !== common[key]) : [];
              const hiddenBad = HIDDEN_COMMON.filter((key) => rowErrors[key]);
              return (
                <tr key={row} className={Object.keys(rowErrors).length ? "rg-row-bad" : undefined}>
                  <td className="rg-no">
                    {row + 1}
                    {own.length > 0 && (
                      <span className="rg-own" title={`Своё у позиции: ${own.map((key) => `${LABEL_BY_KEY[key]} — ${draft[key]}`).join("; ")}`}>своё</span>
                    )}
                    {hiddenBad.length > 0 && (
                      <span className="rg-own rg-own-bad" title={hiddenBad.map((key) => rowErrors[key]).join("; ")}>нет данных</span>
                    )}
                  </td>
                  {shown.map((column) => (
                    <td key={column.key} className={`rg-col-${column.key}`} data-label={column.label}>{control(row, column.key, draft, rowErrors[column.key])}</td>
                  ))}
                  <td className="rg-act">
                    <button type="button" className="rg-link" onClick={() => onDuplicate(row)} disabled={atLimit} aria-label={`Копия строки ${row + 1}`}>Копия</button>
                    <button type="button" className="rg-link" onClick={() => onRemove(row)} aria-label={`${removeLabel} строку ${row + 1}`}>{removeLabel}</button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="rg-bar">
        <span className="rg-count">
          Позиций: <b>{positions.length}</b> из {limit}
          {sendCount !== positions.length && <> · к отправке <b>{sendCount}</b></>}
        </span>
        {badRows.length > 0 && (
          <span className="rg-errors" role="status">
            {positionsWord(badRows.length)} с ошибкой
            <button type="button" className="rg-link" onClick={firstError}>К первой</button>
          </span>
        )}
        <span className="rg-spacer" />
        <button type="button" className="secondary small" disabled={atLimit} title={atLimit ? `В одной заявке не больше ${limit} позиций` : undefined} onClick={onAdd}>
          + Добавить строку
        </button>
        <button type="button" className="primary" disabled={busy || !schema || sendCount === 0} onClick={onSubmit}>
          {busy ? "Сохраняем…" : sendCount === 0 ? "Оформить" : submitLabel(sendCount)}
        </button>
      </div>
    </div>
  );
}
