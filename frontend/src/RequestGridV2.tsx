import { useEffect, useRef, useState, type ClipboardEvent, type KeyboardEvent } from "react";
import type { RequestSchema } from "./api";
import { ComboBox, type ComboOption } from "./ComboBox";
import { purchasingItemsApi, type ItemRow } from "./purchasingItemsApi";
import {
  GRID_COLUMNS, GRID_LIMIT, LABEL_BY_KEY, positionsWord, submitLabel, type Draft, type GridKey, type RowErrors,
} from "./requestGridLogic";
import { ownFields, type Header } from "./requestFormV2Logic";

/** The new form has no категория column. */
const COLUMNS = GRID_COLUMNS.filter((column) => column.key !== "category");

type Props = {
  positions: Draft[];
  header: Header;
  schema: RequestSchema | null;
  errors: RowErrors[] | null;
  sendCount: number;
  busy: boolean;
  /** nomenclature search; off or failing = the name is a plain input */
  itemsOn: boolean;
  onField: (index: number, key: GridKey, value: string) => void;
  onPickItem: (index: number, item: ItemRow) => void;
  onPaste: (index: number, key: GridKey, text: string) => void;
  onAdd: () => void;
  onRemove: (index: number) => void;
  onDuplicate: (index: number) => void;
  onSubmit: () => void;
};

const SUGGEST_LIMIT = 8;
const DEBOUNCE_MS = 250;

function shortDate(iso: string) {
  const match = iso.match(/^(\d{4})-(\d{2})-(\d{2})/);
  return match ? `${match[3]}.${match[2]}.${match[1]}` : iso;
}

/** «Наименование»: free text with suggestions from the nomenclature while the cell is focused. */
function NameCell({ value, enabled, onText, onPick, ...rest }: {
  value: string; enabled: boolean; onText: (value: string) => void; onPick: (item: ItemRow) => void;
} & Record<string, unknown>) {
  const [focused, setFocused] = useState(false);
  const [items, setItems] = useState<ItemRow[]>([]);
  const query = value.trim();
  const search = enabled && focused && query.length >= 2;

  useEffect(() => {
    if (!search) { setItems([]); return; }
    let live = true;
    const timer = window.setTimeout(() => {
      purchasingItemsApi.list({ q: query, category: "", hasPrice: false, page: 1 })
        .then((result) => { if (live) setItems(result.items.slice(0, SUGGEST_LIMIT)); })
        .catch(() => { if (live) setItems([]); }); // 404 on the server: a plain input
    }, DEBOUNCE_MS);
    return () => { live = false; window.clearTimeout(timer); };
  }, [search, query]);

  const options: ComboOption[] = items.map((item) => ({
    value: item.id, label: item.name, desc: [item.part_number, item.unit].filter(Boolean).join(" · ") || undefined,
  }));
  return <ComboBox
    {...rest}
    options={options}
    value={value}
    prefiltered
    onChange={(next, option) => {
      const item = option ? items.find((row) => row.id === option.value) : undefined;
      if (item) onPick(item); else onText(next);
    }}
    onFocus={() => setFocused(true)}
    onBlur={() => setFocused(false)}
  />;
}

/** The grid of the new form: header values are the default, an empty cell of док. качества / план. дата
 *  takes the header, a typed value is the line's own. Unit is a list-only select, type a searchable list. */
export function RequestGridV2({
  positions, header, schema, errors, sendCount, busy, itemsOn,
  onField, onPickItem, onPaste, onAdd, onRemove, onDuplicate, onSubmit,
}: Props) {
  const root = useRef<HTMLDivElement>(null);
  const pendingFocus = useRef<{ row: number; key: GridKey } | null>(null);
  const atLimit = positions.length >= GRID_LIMIT;
  const badRows = errors ? errors.map((rowErrors, index) => (Object.keys(rowErrors).length ? index : -1)).filter((i) => i >= 0) : [];

  const cell = (row: number, key: GridKey) => root.current?.querySelector<HTMLElement>(`[data-cell="${row}:${key}"]`) ?? null;

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
    if (!/[\t\n]/.test(text.replace(/\r?\n$/, ""))) return;
    event.preventDefault();
    onPaste(row, key, text);
  }

  function firstError() {
    if (!badRows.length || !errors) return;
    const row = badRows[0];
    const key = COLUMNS.find((column) => errors[row][column.key])?.key ?? COLUMNS[0].key;
    const element = cell(row, key);
    element?.scrollIntoView({ block: "center" });
    element?.focus();
  }

  function control(row: number, key: GridKey, draft: Draft, error: string | undefined) {
    const label = GRID_COLUMNS.find((column) => column.key === key)!.label;
    const common = {
      "data-cell": `${row}:${key}`,
      "aria-label": `${label}, строка ${row + 1}`,
      "aria-invalid": error ? true : undefined,
      title: error,
      className: error ? "rg-bad" : undefined,
      onKeyDown: (event: KeyboardEvent) => onKeyDown(event, row, key),
      onPaste: (event: ClipboardEvent) => handlePaste(event, row, key),
    };
    const value = draft[key];
    const set = (next: string) => onField(row, key, next);
    if (key === "name") {
      return <NameCell {...common} value={value} enabled={itemsOn} onText={set} onPick={(item) => onPickItem(row, item)} />;
    }
    if (key === "unit") {
      const options = (schema?.units ?? []).map((unit) => ({ value: unit, label: unit }));
      return <ComboBox {...common} strict options={options} value={value} placeholder="шт" onChange={set} onClear={() => set("")} />;
    }
    if (key === "item_type") {
      const options = (schema?.item_types ?? []).map((type) => ({ value: type, label: type }));
      return <ComboBox {...common} options={options} value={value} placeholder="Комплектующие" onChange={set} />;
    }
    if (key === "quality_document") {
      return (
        <select {...common} value={value} onChange={(e) => set(e.target.value)}>
          <option value="">как в шапке</option>
          <option>Да</option><option>Нет</option>
        </select>
      );
    }
    if (key === "planned_date") {
      const inherited = header.planned_date ? `По шапке: ${shortDate(header.planned_date)}` : "По умолчанию +14 дней";
      return <input {...common} type="date" value={value} title={error ?? (value ? undefined : inherited)} className={error ? "rg-bad" : value ? undefined : "rg-inherit"} onChange={(e) => set(e.target.value)} />;
    }
    const numeric = key === "quantity" || key === "price" || key === "price_with_vat";
    return (
      <input
        {...common}
        inputMode={numeric ? "decimal" : undefined}
        value={value}
        placeholder={key === "link" ? "https://…" : key === "quantity" ? "1" : undefined}
        onChange={(e) => set(e.target.value)}
      />
    );
  }

  return (
    <div className="rg" ref={root}>
      <div className="rg-scroll">
        <table className="rg-table rg-table-v2">
          <thead>
            <tr>
              <th className="rg-no">№</th>
              {COLUMNS.map((column) => (
                <th key={column.key} className={`rg-col-${column.key}`}>{column.label}{column.required ? " *" : ""}</th>
              ))}
              <th className="rg-act" />
            </tr>
          </thead>
          <tbody>
            {positions.map((draft, row) => {
              const rowErrors = errors?.[row] ?? {};
              const own = ownFields(draft, header);
              return (
                <tr key={row} className={Object.keys(rowErrors).length ? "rg-row-bad" : undefined}>
                  <td className="rg-no">
                    {row + 1}
                    {own.length > 0 && (
                      <span className="rg-own" title={`Своё у позиции: ${own.map((key) => `${LABEL_BY_KEY[key]} — ${draft[key]}`).join("; ")}`}>своё</span>
                    )}
                  </td>
                  {COLUMNS.map((column) => (
                    <td key={column.key} className={`rg-col-${column.key}`}>{control(row, column.key, draft, rowErrors[column.key])}</td>
                  ))}
                  <td className="rg-act">
                    <button type="button" className="rg-link" onClick={() => onDuplicate(row)} disabled={atLimit} aria-label={`Копия строки ${row + 1}`}>Копия</button>
                    <button type="button" className="rg-link" onClick={() => onRemove(row)} aria-label={`Убрать строку ${row + 1}`}>Убрать</button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="rg-bar">
        <span className="rg-count">
          Позиций: <b>{positions.length}</b> из {GRID_LIMIT}
          {sendCount !== positions.length && <> · к отправке <b>{sendCount}</b></>}
        </span>
        {badRows.length > 0 && (
          <span className="rg-errors" role="status">
            {positionsWord(badRows.length)} с ошибкой
            <button type="button" className="rg-link" onClick={firstError}>К первой</button>
          </span>
        )}
        <span className="rg-spacer" />
        <button type="button" className="secondary small" disabled={atLimit} title={atLimit ? `В одной заявке не больше ${GRID_LIMIT} позиций` : undefined} onClick={onAdd}>
          + Добавить строку
        </button>
        <button type="button" className="primary" disabled={busy || !schema || sendCount === 0} onClick={onSubmit}>
          {busy ? "Сохраняем…" : sendCount === 0 ? "Оформить" : submitLabel(sendCount)}
        </button>
      </div>
    </div>
  );
}
