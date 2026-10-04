import { Fragment, useState, type ClipboardEvent, type ReactNode } from "react";
import { ApiError, failureText, procurementApi, type ProcurementLine } from "./api";
import { ProcurementStockPicker, coveredOf, stockApi, toBuyOf } from "./ProcurementStockPicker";
import { RowMenu } from "./ProcurementRowMenu";

const fmtQty = (value: number) => String(Math.round(value * 1e6) / 1e6).replace(".", ",");

/* «Мои позиции» в статусе «В работе»: одна строка таблицы на позицию, обязательные поля — прямо в ячейках
   (Поставщик, Наименование на сайте, Факт. кол-во, Ед., Артикул, Уточнённое наименование, Ссылка). Правленые
   строки сохраняются одной кнопкой «Сохранить всё» через тот же пакетный вызов, что и «Заполнить данные».
   Для контекста слева от правки — то, что SUPPLYMAP показывает в «Заполнить данные»: тип задачи, приоритет,
   категория, тип, кол-во и ед. по расчёту. Редкие действия (прослеживаемость, раздробить, согласование,
   отмена) — в меню «⋯» строки; новый пункт меню — одна строка в массиве `menuItems` ниже.
   «Наименование на сайте» — поле НАИМЕНОВАНИЕ (Сайт): оно печатается названием позиции в письме поставщику. */

export const LINK_HINT = "Ссылка на товар у поставщика. Если позиция есть на складе — «Со склада» в меню строки";

/** Ссылка, по которой можно перейти: http(s):// или www. Всё прочее (старые данные, слово) — просто текст. */
export function linkHref(value: string): string | null {
  const text = value.trim();
  if (/^https?:\/\/\S+$/i.test(text)) return text;
  if (/^www\.\S+$/i.test(text)) return `https://${text}`;
  return null;
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
// Порядок колонок — он же порядок вставки из Excel.
const FIELDS: { key: Field; title: string; width: number }[] = [
  { key: "supplier", title: "Поставщик *", width: 170 },
  { key: "site_name", title: "Наименование на сайте", width: 190 },
  { key: "actual_quantity", title: "Факт. кол-во *", width: 90 },
  { key: "actual_unit", title: "Ед. *", width: 70 },
  { key: "design_article", title: "Артикул", width: 130 },
  { key: "calculated_name", title: "Уточнённое наименование", width: 260 },
  { key: "link", title: "Ссылка", width: 240 },
];

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

// Из заявки, только для чтения (PURCHASE_INWORK_COLS_ в Portal.gs: ТИП ЗАДАЧИ … ЕД.ИЗМ (Расчет)).
const CONTEXT: { key: string; title: string; width: number; value: (line: ProcurementLine) => string }[] = [
  { key: "task_type", title: "Тип задачи", width: 90, value: (line) => line.task_type ?? "" },
  { key: "priority", title: "Приоритет", width: 90, value: (line) => line.priority ?? "" },
  { key: "category", title: "Категория", width: 100, value: (line) => line.category ?? "" },
  { key: "item_type", title: "Тип", width: 100, value: (line) => line.item_type ?? "" },
  { key: "calculated_quantity", title: "Кол-во (расчёт)", width: 80, value: (line) => line.calculated_quantity },
  { key: "calculated_unit", title: "Ед. (расчёт)", width: 70, value: (line) => line.calculated_unit },
];
const COLUMN_COUNT = 1 + CONTEXT.length + FIELDS.length + 1; // позиция + контекст + правка + меню

type Stored = { version: number; values: InWorkDraft };
type Update = { line: ProcurementLine; key: Field; value: string };

export function ProcurementInWorkTable({ lines, units, disabled, busyId, onChanged, onError, onPutOnApproval, onCancel, renderDetail }: {
  lines: ProcurementLine[];
  units: string[];
  disabled: boolean;
  busyId: string;
  onChanged: (message: string) => void;
  onError: (message: string) => void;
  onPutOnApproval: (line: ProcurementLine) => void;
  onCancel: (line: ProcurementLine) => void;
  /** Замена, прослеживаемость, раздробить — раскрываются под строкой. */
  renderDetail: (line: ProcurementLine) => ReactNode;
}) {
  const [edits, setEdits] = useState<Record<string, Stored>>({});
  const [invalid, setInvalid] = useState<Set<string>>(new Set());
  const [open, setOpen] = useState<string | null>(null);
  const [stockFor, setStockFor] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const isEdited = (line: ProcurementLine) => {
    const stored = edits[line.id];
    return Boolean(stored && stored.version === line.version);
  };
  const draftOf = (line: ProcurementLine): InWorkDraft => (isEdited(line) ? edits[line.id].values : inWorkDraftOf(line));
  const edited = lines.filter(isEdited);

  function patch(updates: Update[]) {
    setEdits((current) => {
      const next = { ...current };
      for (const { line, key, value } of updates) {
        const stored = next[line.id];
        const base = stored && stored.version === line.version ? stored.values : inWorkDraftOf(line);
        next[line.id] = { version: line.version, values: { ...base, [key]: value } };
      }
      return next;
    });
  }

  // Вставка из Excel: табы — колонки, переводы строк — строки; начинается с ячейки, куда вставили.
  function paste(event: ClipboardEvent, rowIndex: number, colIndex: number) {
    const text = event.clipboardData.getData("text").replace(/\r/g, "").replace(/\n$/, "");
    if (!/[\t\n]/.test(text)) return;
    event.preventDefault();
    const updates: Update[] = [];
    text.split("\n").forEach((rowText, i) => {
      const line = lines[rowIndex + i];
      if (!line) return;
      rowText.split("\t").forEach((value, j) => {
        const field = FIELDS[colIndex + j];
        if (field) updates.push({ line, key: field.key, value: value.trim() });
      });
    });
    patch(updates);
  }

  async function saveAll() {
    if (!edited.length) return;
    const bad = new Set<string>();
    for (const line of edited) {
      const draft = draftOf(line);
      if (!draft.supplier.trim() || !(Number(draft.actual_quantity.replace(",", ".")) > 0) || !draft.actual_unit.trim()) bad.add(line.id);
    }
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
      setEdits({});
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

  function cell(line: ProcurementLine, rowIndex: number, colIndex: number) {
    const field = FIELDS[colIndex];
    const value = draftOf(line)[field.key];
    const bad = invalid.has(line.id) && (
      (field.key === "supplier" && !value.trim()) ||
      (field.key === "actual_quantity" && !(Number(value.replace(",", ".")) > 0)) ||
      (field.key === "actual_unit" && !value.trim())
    );
    const label = `${field.title.replace(" *", "")} ${line.legacy_id}`;
    const off = disabled || saving;
    if (field.key === "actual_unit") {
      return (
        <select aria-label={label} aria-invalid={bad || undefined} disabled={off} value={value} onChange={(event) => patch([{ line, key: field.key, value: event.target.value }])}>
          <option value="">—</option>
          {(value && !units.includes(value) ? [value, ...units] : units).map((unit) => <option key={unit} value={unit}>{unit}</option>)}
        </select>
      );
    }
    const href = field.key === "link" ? linkHref(value) : null;
    const textLink = field.key === "link" && value.trim() !== "" && !href;
    return (
      <span className="inwork-cell">
        <input
          aria-label={label}
          aria-invalid={bad || undefined}
          disabled={off}
          inputMode={field.key === "actual_quantity" ? "decimal" : undefined}
          value={value}
          title={textLink ? LINK_HINT : undefined}
          className={textLink ? "soft-warn" : undefined}
          onChange={(event) => patch([{ line, key: field.key, value: event.target.value }])}
          onPaste={(event) => paste(event, rowIndex, colIndex)}
        />
        {href && <a href={href} target="_blank" rel="noreferrer" aria-label={`Открыть ссылку ${line.legacy_id}`} title="Открыть ссылку">↗</a>}
      </span>
    );
  }

  const anyTextLink = lines.some((line) => {
    const link = draftOf(line).link;
    return link.trim() !== "" && !linkHref(link);
  });

  return (
    <div className="inwork">
      <div className="inwork-bar">
        <small>{lines.length} в работе · изменено: {edited.length} · блок из Excel можно вставить в любую ячейку</small>
        <button type="button" className="primary small" disabled={disabled || saving || !edited.length} onClick={() => void saveAll()}>
          {saving ? "Сохраняем…" : edited.length ? `Сохранить всё (${edited.length})` : "Сохранить всё"}
        </button>
      </div>
      {anyTextLink && <small className="warning-inline">{LINK_HINT}</small>}
      <div className="inwork-scroll">
        <table className="inwork-table">
          <thead>
            <tr>
              <th>Позиция</th>
              {CONTEXT.map((column) => <th key={column.key} style={{ minWidth: column.width }}>{column.title}</th>)}
              {FIELDS.map((field) => <th key={field.key} style={{ minWidth: field.width }} title={field.key === "link" ? LINK_HINT : undefined}>{field.title}</th>)}
              <th aria-label="Действия" />
            </tr>
          </thead>
          <tbody>
            {lines.map((line, rowIndex) => {
              const covered = coveredOf(line);
              const menuItems: { label: string; disabled?: boolean; title?: string; run: () => void }[] = [
                { label: "Со склада", run: () => { setOpen(null); setStockFor(stockFor === line.id ? null : line.id); } },
                ...(covered > 0 ? [{ label: "Снять «Со склада»", run: () => void releaseStock(line) }] : []),
                { label: "Прослеживаемость, раздробить…", run: () => { setStockFor(null); setOpen(open === line.id ? null : line.id); } },
                { label: "На согласование", run: () => onPutOnApproval(line) },
                { label: "Отменить строку", run: () => onCancel(line) },
              ];
              return (
                <Fragment key={line.id}>
                  <tr className={isEdited(line) ? "edited" : undefined}>
                    <td className="inwork-name">
                      <strong>{line.legacy_id}</strong> <span title={`${line.calculated_name} · ${line.designation}`}>{line.calculated_name}</span>
                      {covered > 0 && <small className="stock-note"> · со склада: {fmtQty(covered)} {line.calculated_unit} · к закупке: {fmtQty(Number(toBuyOf(line)))}</small>}
                    </td>
                    {CONTEXT.map((column) => <td key={column.key} className="inwork-context">{column.value(line)}</td>)}
                    {FIELDS.map((field, colIndex) => <td key={field.key}>{cell(line, rowIndex, colIndex)}</td>)}
                    <td className="inwork-menu">
                      <RowMenu label={`Действия ${line.legacy_id}`} disabled={disabled || busyId === line.id} items={menuItems} />
                    </td>
                  </tr>
                  {stockFor === line.id && (
                    <tr className="inwork-detail"><td colSpan={COLUMN_COUNT}>
                      <ProcurementStockPicker
                        line={line}
                        disabled={disabled}
                        onClose={() => setStockFor(null)}
                        onDone={(message) => { setStockFor(null); onChanged(message); }}
                      />
                    </td></tr>
                  )}
                  {open === line.id && <tr className="inwork-detail"><td colSpan={COLUMN_COUNT}>{renderDetail(line)}</td></tr>}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
