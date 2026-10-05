import { Fragment, useRef, useState, type ClipboardEvent, type MouseEvent, type ReactNode } from "react";
import { ApiError, failureText, procurementApi, type ProcurementLine } from "./api";
import { selectRange } from "./bulk";
import { NoteCell, type LineEditing } from "./ProcurementCellEdit";
import { ProcurementStockPicker, coveredOf, stockApi, toBuyOf } from "./ProcurementStockPicker";
import { RowMenu } from "./ProcurementRowMenu";
import { formatMoney } from "./ProcurementSheetColumns";
import { BulkStatus, StageHeading, type BulkState } from "./ProcurementStages";

const fmtQty = (value: number) => String(Math.round(value * 1e6) / 1e6).replace(".", ",");

/* Блок «В работе» в «Мои позиции»: лист, сгруппированный по поставщикам. Все строки — «В работе» и «Готов к
   запросу» — правятся прямо в ячейках (Поставщик, Наименование на сайте, Факт. кол-во, Ед., Артикул, Ссылка);
   правленые строки сохраняются одной кнопкой «Сохранить всё» через пакетный вызов. «Цена расч.» — только чтение.
   Над строками поставщика — строка группы: число позиций, сумма по расчёту и «Сформировать запрос» по этой
   группе (пока в группе есть несохранённые правки — недоступна). Группа «Без поставщика» стоит последней.
   Контекст заявки — тип задачи, категория, тип, кол-во по расчёту — серой второй строкой в ячейке «Позиция».
   Флажки слева: выбранные строки — в массовые действия над таблицей. Редкие действия строки — в меню «⋯».
   «Наименование на сайте» — поле НАИМЕНОВАНИЕ (Сайт): оно печатается названием позиции в письме поставщику. */

export const LINK_HINT = "Ссылка на товар у поставщика. Если позиция есть на складе — «Со склада» в меню строки";
export const IN_WORK_HINT = "Поставщик, факт. кол-во и ед. обязательны для запроса. Блок из Excel можно вставить в любую ячейку — он разойдётся по колонкам и строкам";

/** Ссылка, по которой можно перейти: http(s):// или www. Всё прочее (старые данные, слово) — просто текст. */
export function linkHref(value: string): string | null {
  const text = value.trim();
  if (/^https?:\/\/\S+$/i.test(text)) return text;
  if (/^www\.\S+$/i.test(text)) return `https://${text}`;
  return null;
}

/** Короткая подпись ссылки — сайт без www: «molex.com». */
export function linkHost(href: string): string {
  try {
    return new URL(href).hostname.replace(/^www\./i, "") || href;
  } catch {
    return href;
  }
}

export type InWorkDraft = {
  supplier: string;
  site_name: string;
  actual_quantity: string;
  actual_unit: string;
  design_article: string;
  link: string;
};

type Field = keyof InWorkDraft;
type Column = { key: Field | "price"; title: string; head?: string; required?: boolean };
// Порядок колонок — он же порядок вставки из Excel. `head` — короткий заголовок, чтобы колонка влезла на 16";
// полное название — в подсказке заголовка и в имени поля. «Цена расч.» только читается: при вставке её место
// пропускается, чтобы блок, скопированный с этого листа, ложился обратно как был.
const FIELDS: Column[] = [
  { key: "supplier", title: "Поставщик", required: true },
  { key: "site_name", title: "Наименование на сайте", head: "Наим. на сайте" },
  { key: "actual_quantity", title: "Факт. кол-во", head: "Кол-во", required: true },
  { key: "actual_unit", title: "Ед.", required: true },
  { key: "design_article", title: "Артикул" },
  { key: "price", title: "Цена расчётная, с НДС", head: "Цена расч." },
  { key: "link", title: "Ссылка" },
];
const COLUMN_COUNT = 2 + FIELDS.length + 2; // флажок + позиция + правка + комментарий + меню

export function inWorkDraftOf(line: ProcurementLine): InWorkDraft {
  return {
    supplier: line.supplier ?? "",
    site_name: line.site_name ?? "",
    actual_quantity: line.actual_quantity ?? line.calculated_quantity,
    actual_unit: line.actual_unit ?? line.calculated_unit,
    design_article: line.design_article ?? "",
    link: line.link ?? "",
  };
}

const READY = "Готов к запросу";
export const NO_SUPPLIER = "Без поставщика";
const URGENT = /сроч/i;

/** Серая вторая строка «Позиции»: то, что SUPPLYMAP показывал колонками ТИП ЗАДАЧИ … ЕД.ИЗМ (Расчет). */
export function contextOf(line: ProcurementLine): string {
  return [line.task_type, line.category, line.item_type, `расчёт ${line.calculated_quantity} ${line.calculated_unit}`]
    .filter((part) => part && part.trim())
    .join(" · ");
}

/** «1 позиция», «2 позиции», «5 позиций». */
export function positionsWord(count: number): string {
  const tail = count % 100;
  const last = count % 10;
  const word = tail >= 11 && tail <= 14 ? "позиций" : last === 1 ? "позиция" : last >= 2 && last <= 4 ? "позиции" : "позиций";
  return `${count} ${word}`;
}

/** Цена по расчёту: с НДС, иначе без. */
export function calculatedPriceOf(line: ProcurementLine): string {
  const price = line.calculated_price_with_vat || line.calculated_price;
  return price ? formatMoney(price) : "";
}

type Group = { key: string; title: string; lines: ProcurementLine[] };

/** Группы по сохранённому поставщику (без пробелов по краям, без учёта регистра); «Без поставщика» — последней. */
export function groupBySupplier(lines: ProcurementLine[]): Group[] {
  const groups = new Map<string, Group>();
  for (const line of lines) {
    const title = (line.supplier ?? "").trim();
    const key = title.toLowerCase();
    const group = groups.get(key) ?? { key, title: title || NO_SUPPLIER, lines: [] };
    group.lines.push(line);
    groups.set(key, group);
  }
  const named = [...groups.values()].filter((group) => group.key).sort((a, b) => a.title.localeCompare(b.title, "ru"));
  const none = groups.get("");
  return none ? [...named, none] : named;
}

function groupSum(group: Group): string {
  const sum = group.lines.reduce((total, line) => total + (Number(line.calculated_sum) || 0), 0);
  return formatMoney(Math.round(sum * 100) / 100);
}

function invalidField(key: Field, value: string) {
  if (key === "supplier" || key === "actual_unit") return !value.trim();
  if (key === "actual_quantity") return !(Number(value.replace(",", ".")) > 0);
  return false;
}

/** «Ссылка»: в покое — короткое имя сайта «molex.com ↗», ведёт на товар; щелчок по ячейке — правка. */
function LinkCell({ id, value, disabled, onChange, onPaste }: {
  id: string;
  value: string;
  disabled: boolean;
  onChange: (value: string) => void;
  onPaste: (event: ClipboardEvent) => void;
}) {
  const [open, setOpen] = useState(false);
  const label = `Ссылка ${id}`;
  if (open && !disabled) {
    return (
      <input
        className="cell-input" aria-label={label} autoFocus value={value}
        onChange={(event) => onChange(event.target.value)}
        onBlur={() => setOpen(false)}
        onKeyDown={(event) => { if (event.key === "Enter" || event.key === "Escape") setOpen(false); }}
        onPaste={onPaste}
      />
    );
  }
  const href = linkHref(value);
  const text = !href && value.trim() !== "";
  return (
    <span className="link-cell">
      <button
        type="button" className={text ? "cell-text soft-warn" : "cell-text"} aria-label={label} disabled={disabled}
        title={text ? `${value} — ${LINK_HINT}` : href ? `Изменить ссылку: ${value}` : "Добавить ссылку"}
        onClick={() => setOpen(true)}
        onPaste={(event) => {
          onPaste(event);
          if (event.defaultPrevented) return;
          event.preventDefault();
          onChange(event.clipboardData.getData("text").trim());
        }}
      >
        {text ? value : ""}
      </button>
      {href && (
        <a className="link-host" href={href} target="_blank" rel="noreferrer" aria-label={`Открыть ссылку ${id}`} title={value}>
          {linkHost(href)} ↗
        </a>
      )}
    </span>
  );
}

export function ProcurementInWorkTable({
  lines, units, disabled, busyId, editing, filling, bulk, onChanged, onError, onCancel, onCancelMany, onBuildRequests,
  onCloseBulk, renderDetail,
}: {
  /** «В работе», затем «Готов к запросу». */
  lines: ProcurementLine[];
  units: string[];
  disabled: boolean;
  busyId: string;
  /** Правка «Комментария» в ячейке. */
  editing?: LineEditing;
  /** Только что взятые строки: сервер ещё дозаполняет их пустые поля. */
  filling?: { has: (id: string) => boolean };
  /** Ход и итог массового действия этого блока. */
  bulk?: BulkState | null;
  onChanged: (message: string) => void;
  onError: (message: string) => void;
  onCancel: (line: ProcurementLine) => void;
  onCancelMany: (lines: ProcurementLine[]) => Promise<unknown>;
  onBuildRequests: (lines: ProcurementLine[]) => Promise<unknown>;
  onCloseBulk?: () => void;
  /** Больше не используется: поля правятся в ячейках. Оставлено для совместимости. */
  onFill?: () => void;
  /** Прослеживаемость, раздробить — раскрываются под строкой. */
  renderDetail: (line: ProcurementLine) => ReactNode;
}) {
  // Правки — только тронутые ячейки поверх свежих данных строки: дозаполнение с сервера или перечитанная
  // очередь не стирают набранное, а нетронутые ячейки показывают то, что пришло.
  const [edits, setEdits] = useState<Record<string, Partial<InWorkDraft>>>({});
  const [invalid, setInvalid] = useState<Set<string>>(new Set());
  const [open, setOpen] = useState<string | null>(null);
  const [stockFor, setStockFor] = useState<Set<string>>(new Set());
  const [saving, setSaving] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const anchor = useRef<string | null>(null);

  const groups = groupBySupplier(lines);
  // Строки в порядке на экране: по ним идут вставка из Excel и выбор Shift-диапазоном.
  const ordered = groups.flatMap((group) => group.lines);
  const ids = new Set(ordered.map((line) => line.id));
  const chosen = ordered.filter((line) => selected.has(line.id));
  const chosenReady = chosen.filter((line) => line.status === READY);

  const draftOf = (line: ProcurementLine): InWorkDraft => ({ ...inWorkDraftOf(line), ...edits[line.id] });
  const dirtyFields = (line: ProcurementLine): Field[] => {
    if (!edits[line.id]) return [];
    const base = inWorkDraftOf(line);
    return (Object.entries(edits[line.id]) as [Field, string][]).filter(([key, value]) => value !== base[key]).map(([key]) => key);
  };
  const edited = lines.filter((line) => dirtyFields(line).length > 0);
  // Пустой поставщик, который не трогали, не проверяем и не отправляем: сервер оставит поле как есть.
  const fieldsOf = (line: ProcurementLine): Field[] => {
    const keys = FIELDS.map((field) => field.key).filter((key): key is Field => key !== "price");
    return keys.filter((key) => key !== "supplier" || dirtyFields(line).includes("supplier") || draftOf(line).supplier.trim() !== "");
  };

  function patch(updates: { line: ProcurementLine; key: Field; value: string }[]) {
    setEdits((current) => {
      const next = { ...current };
      for (const { line, key, value } of updates) next[line.id] = { ...next[line.id], [key]: value };
      return next;
    });
  }

  // Вставка из Excel: табы — колонки, переводы строк — строки; начинается с ячейки, куда вставили.
  // Колонка «Цена расч.» только читается — её место в блоке пропускается.
  function paste(event: ClipboardEvent, rowIndex: number, colIndex: number) {
    const text = event.clipboardData.getData("text").replace(/\r/g, "").replace(/\n$/, "");
    if (!/[\t\n]/.test(text)) return;
    event.preventDefault();
    const updates: { line: ProcurementLine; key: Field; value: string }[] = [];
    text.split("\n").forEach((rowText, i) => {
      const line = ordered[rowIndex + i];
      if (!line) return;
      rowText.split("\t").forEach((value, j) => {
        const field = FIELDS[colIndex + j];
        if (field && field.key !== "price") updates.push({ line, key: field.key, value: value.trim() });
      });
    });
    patch(updates);
  }

  async function saveAll() {
    if (!edited.length) return;
    const bad = new Set(edited.filter((line) => {
      const draft = draftOf(line);
      return fieldsOf(line).some((field) => invalidField(field, draft[field]));
    }).map((line) => line.id));
    setInvalid(bad);
    if (bad.size) {
      onError(`Заполните поставщика, факт. количество и единицу: ${edited.filter((line) => bad.has(line.id)).map((line) => line.legacy_id).join(", ")}`);
      return;
    }
    setSaving(true);
    onError("");
    try {
      const result = await procurementApi.saveFactsBatch(edited.map((line) => {
        const draft = draftOf(line);
        const row: Record<string, unknown> = {
          line_id: line.id,
          legacy_id: line.legacy_id,
          version: line.version,
          design_article: draft.design_article.trim() || null,
          site_name: draft.site_name.trim() || null,
          link: draft.link.trim() || null,
          actual_quantity: draft.actual_quantity.trim().replace(",", "."),
          actual_unit: draft.actual_unit.trim(),
        };
        if (fieldsOf(line).includes("supplier")) row.supplier = draft.supplier.trim();
        return row;
      }));
      const parts = [`Сохранено строк: ${result.saved}`];
      if (result.skipped.length) parts.push(`пропущено (строка изменилась): ${result.skipped.join(", ")}`);
      if (result.ready) parts.push(`готовы к запросу: ${result.ready}`);
      // Пропущенные и не принятые строки сохраняют набранное — его можно поправить и сохранить ещё раз.
      const kept = new Set([...result.skipped, ...result.failed.map((item) => item.legacy_id)]);
      setEdits((current) => Object.fromEntries(Object.entries(current).filter(([id]) => {
        const line = lines.find((item) => item.id === id);
        return line && kept.has(line.legacy_id);
      })));
      setInvalid(new Set());
      onChanged(parts.join(" · "));
      if (result.failed.length) onError(result.failed.map((item) => `${item.legacy_id}: ${item.error}`).join("; "));
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "Данные не сохранены");
    } finally {
      setSaving(false);
    }
  }

  async function releaseStock(line: ProcurementLine) {
    onError("");
    try {
      await stockApi.release(line.id, line.version);
      onChanged(`${line.legacy_id}: «Со склада» снято`);
    } catch (reason) {
      onError(failureText(reason, "Резерв не снят"));
      if (reason instanceof ApiError && reason.status === 409) onChanged("");
    }
  }

  function pick(line: ProcurementLine, event: MouseEvent<HTMLInputElement>) {
    const order = ordered.map((item) => item.id);
    const checked = event.currentTarget.checked;
    const from = anchor.current;
    const shift = event.shiftKey;
    setSelected((current) => selectRange(order, current, from, line.id, checked, shift));
    anchor.current = line.id;
  }

  async function bulkRun(action: (rows: ProcurementLine[]) => Promise<unknown>, rows: ProcurementLine[]) {
    try {
      await action(rows);
      setSelected(new Set());
    } catch { /* итог показывает блок, выбор остаётся */ }
  }

  function stockToggle(id: string) {
    setOpen(null);
    setStockFor((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  function cell(line: ProcurementLine, rowIndex: number, colIndex: number) {
    const field = FIELDS[colIndex];
    if (field.key === "price") {
      const price = calculatedPriceOf(line);
      return <span className="cell-ro" title={price}>{price}</span>;
    }
    const key = field.key;
    const value = draftOf(line)[key];
    const label = `${field.title} ${line.legacy_id}`;
    const off = disabled || saving;
    const bad = invalid.has(line.id) && fieldsOf(line).includes(key) && invalidField(key, value);
    const set = (next: string) => patch([{ line, key, value: next }]);
    if (field.key === "actual_unit") {
      return (
        <select className="cell-select" aria-label={label} aria-invalid={bad || undefined} disabled={off} value={value} onChange={(event) => set(event.target.value)} onPaste={(event) => paste(event, rowIndex, colIndex)}>
          <option value="">—</option>
          {(value && !units.includes(value) ? [value, ...units] : units).map((unit) => <option key={unit} value={unit}>{unit}</option>)}
        </select>
      );
    }
    if (field.key === "link") {
      return <LinkCell id={line.legacy_id} value={value} disabled={off} onChange={set} onPaste={(event) => paste(event, rowIndex, colIndex)} />;
    }
    return (
      <input
        className="cell-input"
        aria-label={label}
        aria-invalid={bad || undefined}
        disabled={off}
        inputMode={field.key === "actual_quantity" ? "decimal" : undefined}
        value={value}
        title={field.key === "site_name" ? value : undefined}
        onChange={(event) => set(event.target.value)}
        onPaste={(event) => paste(event, rowIndex, colIndex)}
      />
    );
  }

  const allSelected = ordered.length > 0 && ordered.every((line) => selected.has(line.id));

  return (
    <section className="panel table-panel procurement-stage stage-block" aria-label="В работе">
      <StageHeading title="В работе" count={lines.length} hint={IN_WORK_HINT}>
        {edited.length > 0 && <span className="dirty-note">изменено: {edited.length}</span>}
        <button type="button" className={edited.length ? "primary small" : "secondary small"} disabled={disabled || saving || !edited.length} onClick={() => void saveAll()}>
          {saving ? "Сохраняем…" : edited.length ? `Сохранить всё (${edited.length})` : "Сохранить всё"}
        </button>
      </StageHeading>
      <BulkStatus bulk={bulk} onClose={onCloseBulk} />
      {chosen.length > 0 && (
        <div className="bulk-bar stage-bulk" role="toolbar" aria-label="Действия с выбранными">
          <span>Выбрано: {chosen.length}</span>
          <button
            type="button" className="primary small" disabled={disabled || !chosenReady.length}
            title={chosenReady.length ? undefined : "Среди выбранных нет строк «Готов к запросу»"}
            onClick={() => void bulkRun(onBuildRequests, chosenReady)}
          >
            Сформировать запросы поставщикам{chosenReady.length && chosenReady.length < chosen.length ? ` (${chosenReady.length})` : ""}
          </button>
          <button type="button" className="secondary small" disabled={disabled} onClick={() => { setOpen(null); setStockFor(new Set(chosen.map((line) => line.id))); setSelected(new Set()); }}>Со склада</button>
          <button type="button" className="secondary small tone-danger" disabled={disabled} onClick={() => void bulkRun(onCancelMany, chosen)}>Отменить строку</button>
          <button type="button" className="secondary small" onClick={() => setSelected(new Set())}>Снять выбор</button>
        </div>
      )}
      {!lines.length ? <p className="stage-empty">Нет строк в работе — возьмите позиции из «Неразобранных»</p> : (
        <div className="buy-scroll">
          <table className="buy-grid">
            <colgroup>
              <col className="c-sel" /><col className="c-pos" /><col className="c-supplier" /><col className="c-site" />
              <col className="c-qty" /><col className="c-unit" /><col className="c-article" /><col className="c-price" />
              <col className="c-link" /><col className="c-note" /><col className="c-menu" />
            </colgroup>
            <thead>
              <tr>
                <th className="sel"><input type="checkbox" aria-label="Выбрать все" checked={allSelected} onChange={(event) => setSelected(event.target.checked ? new Set(ids) : new Set())} /></th>
                <th className="pos">Позиция</th>
                {FIELDS.map((field) => (
                  <th
                    key={field.key} className={field.required ? "required" : undefined}
                    title={[field.title, field.required ? "обязательно для запроса" : "", field.key === "link" ? LINK_HINT : ""].filter(Boolean).join(" — ")}
                  >
                    {field.head ?? field.title}
                  </th>
                ))}
                <th>Комментарий</th>
                <th aria-label="Действия" />
              </tr>
            </thead>
            <tbody>
              {groups.map((group) => {
                const groupDirty = group.lines.some((line) => dirtyFields(line).length > 0);
                const groupReady = group.lines.filter((line) => line.status === READY);
                const requestTitle = groupDirty
                  ? "Есть несохранённые правки — нажмите «Сохранить всё»"
                  : groupReady.length ? undefined : "В группе нет строк «Готов к запросу» — заполните поставщика, кол-во и ед.";
                return (
                  <Fragment key={group.key || "none"}>
                    <tr className="group-row">
                      <td className="group-cell" colSpan={COLUMN_COUNT}>
                        <div className="group-line">
                          <span className="group-title">{group.title}</span>
                          <span className="group-meta">
                            {positionsWord(group.lines.length)}{group.key ? ` · ${groupSum(group)} по расчёту` : ""}
                          </span>
                          {group.key && (
                            <span className="group-actions">
                              <button
                                type="button" className="primary small" title={requestTitle}
                                disabled={disabled || saving || groupDirty || !groupReady.length}
                                onClick={() => void onBuildRequests(groupReady)}
                              >
                                Сформировать запрос
                              </button>
                            </span>
                          )}
                        </div>
                      </td>
                    </tr>
                    {group.lines.map((line) => {
                const rowIndex = ordered.indexOf(line);
                const covered = coveredOf(line);
                const dirty = new Set(dirtyFields(line));
                const ready = line.status === READY;
                const menuItems: { label: string; disabled?: boolean; title?: string; run: () => void }[] = [
                  { label: "Со склада", run: () => stockToggle(line.id) },
                  ...(covered > 0 ? [{ label: "Снять «Со склада»", run: () => void releaseStock(line) }] : []),
                  { label: "Прослеживаемость, раздробить…", run: () => { setStockFor(new Set()); setOpen(open === line.id ? null : line.id); } },
                  { label: "Отменить строку", run: () => onCancel(line) },
                ];
                const rowClass = [dirty.size ? "edited" : "", selected.has(line.id) ? "selected" : "", ready ? "ready" : ""].join(" ").trim();
                return (
                  <Fragment key={line.id}>
                    <tr className={rowClass || undefined}>
                      <td className="sel">
                        <input type="checkbox" aria-label={`Выбрать ${line.legacy_id}`} checked={selected.has(line.id)} onChange={() => undefined} onClick={(event) => pick(line, event)} />
                      </td>
                      <td className="pos" title={[line.calculated_name, line.designation, contextOf(line)].filter(Boolean).join("\n")}>
                        <span className="pos-main"><strong>{line.legacy_id}</strong> {line.calculated_name}</span>
                        <span className="pos-sub">
                          {ready && <span className="chip tone-ok">{READY}</span>}
                          {line.priority && URGENT.test(line.priority) && <span className="chip tone-danger">{line.priority}</span>}
                          {filling?.has(line.id) && <em className="filling">заполняется…</em>}
                          {covered > 0 && <span className="stock-note">со склада: {fmtQty(covered)} {line.calculated_unit} · к закупке: {fmtQty(Number(toBuyOf(line)))} · </span>}
                          {contextOf(line)}
                        </span>
                      </td>
                      {FIELDS.map((field, colIndex) => {
                        const value = field.key === "price" ? "" : draftOf(line)[field.key];
                        const cls = [
                          field.key === "price" ? "ro" : "edit",
                          dirty.has(field.key as Field) ? "dirty" : "",
                          field.key !== "price" && invalid.has(line.id) && fieldsOf(line).includes(field.key) && invalidField(field.key, value) ? "invalid" : "",
                          field.key === "actual_quantity" || field.key === "price" ? "num" : "",
                        ].join(" ").trim();
                        return <td key={field.key} className={cls}>{cell(line, rowIndex, colIndex)}</td>;
                      })}
                      <td className="note"><NoteCell line={line} editing={editing} disabled={disabled} /></td>
                      <td className="menu">
                        <RowMenu label={`Действия ${line.legacy_id}`} disabled={disabled || busyId === line.id} items={menuItems} />
                      </td>
                    </tr>
                    {stockFor.has(line.id) && (
                      <tr className="buy-detail"><td colSpan={COLUMN_COUNT}>
                        <ProcurementStockPicker
                          line={line}
                          disabled={disabled}
                          onClose={() => stockToggle(line.id)}
                          onDone={(message) => { stockToggle(line.id); onChanged(message); }}
                        />
                      </td></tr>
                    )}
                    {open === line.id && <tr className="buy-detail"><td colSpan={COLUMN_COUNT}>{renderDetail(line)}</td></tr>}
                  </Fragment>
                );
              })}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
