import { Fragment, useRef, useState, type ClipboardEvent, type MouseEvent, type ReactNode } from "react";
import { ApiError, failureText, procurementApi, type ProcurementLine } from "./api";
import { selectRange } from "./bulk";
import { NoteCell, type LineEditing } from "./ProcurementCellEdit";
import { ProcurementStockPicker, coveredOf, stockApi, toBuyOf } from "./ProcurementStockPicker";
import { RowMenu } from "./ProcurementRowMenu";
import { BulkStatus, StageHeading, type BulkState } from "./ProcurementStages";

const fmtQty = (value: number) => String(Math.round(value * 1e6) / 1e6).replace(".", ",");

/* Блок «В работе» в «Мои позиции»: лист, а не форма. Строки «В работе» правятся прямо в ячейках (Поставщик,
   Наименование на сайте, Факт. кол-во, Ед., Артикул, Уточнённое наименование, Ссылка); правленые строки
   сохраняются одной кнопкой «Сохранить всё» через тот же пакетный вызов, что и «Заполнить данные». Строки
   «Готов к запросу» стоят здесь же ниже, с отметкой «готова к запросу», и только показываются (факты сервер
   меняет лишь «В работе»). Контекст заявки — тип задачи, приоритет, категория, тип, кол-во по расчёту — серой
   второй строкой в ячейке «Позиция». Флажки слева: выбранные строки — в массовые действия над таблицей.
   Редкие действия строки — в меню «⋯»; новый пункт — одна строка в массиве `menuItems` ниже.
   «Наименование на сайте» — поле НАИМЕНОВАНИЕ (Сайт): оно печатается названием позиции в письме поставщику. */

export const LINK_HINT = "Ссылка на товар у поставщика. Если позиция есть на складе — «Со склада» в меню строки";
export const IN_WORK_HINT = "Поставщик, факт. кол-во и ед. обязательны. Блок из Excel можно вставить в любую ячейку — он разойдётся по колонкам и строкам";

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
  calculated_name: string;
  link: string;
};

type Field = keyof InWorkDraft;
// Порядок колонок — он же порядок вставки из Excel. `head` — короткий заголовок, чтобы колонка влезла на 16";
// полное название — в подсказке заголовка и в имени поля.
const FIELDS: { key: Field; title: string; head?: string; required?: boolean }[] = [
  { key: "supplier", title: "Поставщик", required: true },
  { key: "site_name", title: "Наименование на сайте", head: "Наим. на сайте" },
  { key: "actual_quantity", title: "Факт. кол-во", head: "Кол-во", required: true },
  { key: "actual_unit", title: "Ед.", required: true },
  { key: "design_article", title: "Артикул" },
  { key: "calculated_name", title: "Уточнённое наименование", head: "Уточн. наименование" },
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
    calculated_name: line.calculated_name,
    link: line.link ?? "",
  };
}

const IN_WORK = "В работе";
const editable = (line: ProcurementLine) => line.status === IN_WORK;
const URGENT = /сроч/i;

/** Серая вторая строка «Позиции»: то, что SUPPLYMAP показывал колонками ТИП ЗАДАЧИ … ЕД.ИЗМ (Расчет). */
export function contextOf(line: ProcurementLine): string {
  return [line.task_type, line.category, line.item_type, `расчёт ${line.calculated_quantity} ${line.calculated_unit}`]
    .filter((part) => part && part.trim())
    .join(" · ");
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
  onCloseBulk, onFill, renderDetail,
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
  /** «Заполнить данные» — все строки «В работе» одной формой. */
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

  const ids = new Set(lines.map((line) => line.id));
  const chosen = lines.filter((line) => selected.has(line.id));
  const chosenReady = chosen.filter((line) => !editable(line));

  const draftOf = (line: ProcurementLine): InWorkDraft => ({ ...inWorkDraftOf(line), ...(editable(line) ? edits[line.id] : undefined) });
  const dirtyFields = (line: ProcurementLine): Field[] => {
    if (!editable(line) || !edits[line.id]) return [];
    const base = inWorkDraftOf(line);
    return (Object.entries(edits[line.id]) as [Field, string][]).filter(([key, value]) => value !== base[key]).map(([key]) => key);
  };
  const edited = lines.filter((line) => dirtyFields(line).length > 0);

  function patch(updates: { line: ProcurementLine; key: Field; value: string }[]) {
    setEdits((current) => {
      const next = { ...current };
      for (const { line, key, value } of updates) next[line.id] = { ...next[line.id], [key]: value };
      return next;
    });
  }

  // Вставка из Excel: табы — колонки, переводы строк — строки; начинается с ячейки, куда вставили.
  // Строки «Готов к запросу» не правятся — их часть блока пропускается, остальное ложится на свои строки.
  function paste(event: ClipboardEvent, rowIndex: number, colIndex: number) {
    const text = event.clipboardData.getData("text").replace(/\r/g, "").replace(/\n$/, "");
    if (!/[\t\n]/.test(text)) return;
    event.preventDefault();
    const updates: { line: ProcurementLine; key: Field; value: string }[] = [];
    text.split("\n").forEach((rowText, i) => {
      const line = lines[rowIndex + i];
      if (!line || !editable(line)) return;
      rowText.split("\t").forEach((value, j) => {
        const field = FIELDS[colIndex + j];
        if (field) updates.push({ line, key: field.key, value: value.trim() });
      });
    });
    patch(updates);
  }

  async function saveAll() {
    if (!edited.length) return;
    const bad = new Set(edited.filter((line) => {
      const draft = draftOf(line);
      return FIELDS.some((field) => invalidField(field.key, draft[field.key]));
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
        return {
          line_id: line.id,
          legacy_id: line.legacy_id,
          version: line.version,
          design_article: draft.design_article.trim() || null,
          calculated_name: draft.calculated_name.trim() || null,
          site_name: draft.site_name.trim() || null,
          link: draft.link.trim() || null,
          supplier: draft.supplier.trim(),
          actual_quantity: draft.actual_quantity.trim().replace(",", "."),
          actual_unit: draft.actual_unit.trim(),
        };
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
    const order = lines.map((item) => item.id);
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
    const value = draftOf(line)[field.key];
    if (!editable(line)) {
      if (field.key === "link") {
        const href = linkHref(value);
        return href
          ? <a className="link-host" href={href} target="_blank" rel="noreferrer" aria-label={`Открыть ссылку ${line.legacy_id}`} title={value}>{linkHost(href)} ↗</a>
          : <span className="cell-ro" title={value}>{value}</span>;
      }
      return <span className="cell-ro" title={value}>{value}</span>;
    }
    const label = `${field.title} ${line.legacy_id}`;
    const off = disabled || saving;
    const bad = invalid.has(line.id) && invalidField(field.key, value);
    const set = (next: string) => patch([{ line, key: field.key, value: next }]);
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
        title={field.key === "site_name" || field.key === "calculated_name" ? value : undefined}
        onChange={(event) => set(event.target.value)}
        onPaste={(event) => paste(event, rowIndex, colIndex)}
      />
    );
  }

  const allSelected = lines.length > 0 && lines.every((line) => selected.has(line.id));

  return (
    <section className="panel table-panel procurement-stage stage-block" aria-label="В работе">
      <StageHeading title="В работе" count={lines.length} hint={IN_WORK_HINT}>
        {edited.length > 0 && <span className="dirty-note">изменено: {edited.length}</span>}
        {onFill && <button type="button" className="secondary small" disabled={disabled} onClick={onFill}>Заполнить данные</button>}
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
              <col className="c-qty" /><col className="c-unit" /><col className="c-article" /><col className="c-name" />
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
              {lines.map((line, rowIndex) => {
                const covered = coveredOf(line);
                const dirty = new Set(dirtyFields(line));
                const ready = !editable(line);
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
                          {ready && <span className="chip tone-ok">готова к запросу</span>}
                          {line.priority && URGENT.test(line.priority) && <span className="chip tone-danger">{line.priority}</span>}
                          {filling?.has(line.id) && <em className="filling">заполняется…</em>}
                          {covered > 0 && <span className="stock-note">со склада: {fmtQty(covered)} {line.calculated_unit} · к закупке: {fmtQty(Number(toBuyOf(line)))} · </span>}
                          {contextOf(line)}
                        </span>
                      </td>
                      {FIELDS.map((field, colIndex) => {
                        const value = draftOf(line)[field.key];
                        const cls = [
                          ready ? "ro" : "edit",
                          dirty.has(field.key) ? "dirty" : "",
                          invalid.has(line.id) && !ready && invalidField(field.key, value) ? "invalid" : "",
                          field.key === "actual_quantity" ? "num" : "",
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
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
