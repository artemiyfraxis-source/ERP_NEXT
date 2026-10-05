import { useEffect, useMemo, useState, type FormEvent, type ReactNode } from "react";
import { DataTable, type DataBulkAction, type DataColumn } from "./DataTable";
import { failureText, type FactPriceRow, type InvoiceUpload, type ManualRatePrompt, type OrderInvoiceInput, type ProcurementLine } from "./api";
import { formatDate, formatDay } from "./format";
import { ExpectedDateCell, NoteCell, type LineEditing } from "./ProcurementCellEdit";
import { RowMenu, type MenuItem } from "./ProcurementRowMenu";
import { formatAmount } from "./ProcurementSheetColumns";

/* «Мои позиции» листа закупки — блоки в порядке работы (владелец, 04.10): «В работе» (ProcurementInWorkTable),
   «Жду счёт», «Ждёт оплаты» и «Оплачено» (в пути и карантин) — таблицы по заказам: заголовок группы
   «PO-012 от 05.10 · поставщик · N поз. · факт … ₽ / расчёт … ₽» с действиями заказа («Вставить счёт»,
   «Оплачено», меню «⋯»), под ним строки заказа; «Цена факт» правится в строке, «Сохранить всё» — одним вызовом.
   «Дозавоз» — только когда он есть, «Прочие позиции» внизу. Блок виден и пустым: заголовок с «0» и одна серая
   строка. Ручного этапа «Согласование» нет (владелец, 04.10: «сразу запрос и всё»).
   Таблицы показывают; что делать со строкой, решает страница (`actionsOf`, колбэки): редкие действия лежат в
   меню «⋯» строки, формы открываются под таблицей, массовые — над таблицей, когда выбрана хоть одна строка.
   Две колонки правятся прямо в ячейке (`editing`): «Комментарий» и «Ожид. дата» от «Жду счет» до «Карантин»;
   «Факт. дата» (день приёмки) только показывается. */

/** Действие строки: выполняется сразу (`run`) или открывает форму под таблицей (`panel`). */
export type RowAction = { label: string; title?: string; disabled?: boolean; run?: () => void; panel?: () => ReactNode };

/** До стольких строк таблица блока — без строки поиска и «Показано N из N»: искать в трёх строках нечего. */
export const COMPACT_BELOW = 8;

/** Ход массового действия блока («Отменяем: 12 из 40») и его итог со списком отказов по строкам. */
export type BulkState = { label: string; done: number; total: number; summary?: string; failures?: string[] };

/** Меню строк одной стадии и форма выбранного действия под таблицей. */
function useLinePanel(
  lines: ProcurementLine[],
  actionsOf: (line: ProcurementLine) => RowAction[],
  disabled: boolean,
  busyId: string,
) {
  const [open, setOpen] = useState<{ id: string; label: string } | null>(null);
  const line = open ? lines.find((item) => item.id === open.id) : undefined;
  const action = open && line ? actionsOf(line).find((item) => item.label === open.label && item.panel) : undefined;
  // Строка ушла на другую стадию — форма закрывается и не всплывёт, если строка вернётся.
  useEffect(() => { if (open && !action) setOpen(null); }, [open, action]);

  const menu = (row: ProcurementLine) => (
    <RowMenu
      label={`Действия ${row.legacy_id}`}
      disabled={disabled || busyId === row.id}
      items={actionsOf(row).map((item): MenuItem => ({
        label: item.label,
        title: item.title,
        disabled: item.disabled,
        run: () => {
          if (item.panel) setOpen(open?.id === row.id && open.label === item.label ? null : { id: row.id, label: item.label });
          else item.run?.();
        },
      }))}
    />
  );

  const node = open && line && action ? (
    <div className="line-panel" key={`${line.id}:${line.version}:${open.label}`}>
      <div className="panel-heading">
        <h2>{line.legacy_id} · {open.label.replace(/…$/, "")}</h2>
        <div className="heading-actions">
          <span>{line.calculated_name}</span>
          <button type="button" className="secondary small" onClick={() => setOpen(null)}>Закрыть</button>
        </div>
      </div>
      <div className="line-panel-body">{action.panel!()}</div>
    </div>
  ) : null;

  return { menu, node };
}

/** Одна строка заголовка блока: название и число, подсказка — знаком «?», справа действия. */
export function StageHeading({ title, count, hint, children }: { title: string; count: number; hint?: string; children?: ReactNode }) {
  return (
    <div className="panel-heading stage-head">
      <h2>{title} <span className="stage-count">{count}</span></h2>
      {hint && <span className="stage-help" tabIndex={0} role="note" aria-label={hint} title={hint}>?</span>}
      <div className="heading-actions">{children}</div>
    </div>
  );
}

/** Ход и итог массового действия под заголовком блока. */
export function BulkStatus({ bulk, onClose }: { bulk?: BulkState | null; onClose?: () => void }) {
  if (!bulk) return null;
  if (bulk.summary === undefined) {
    return <div className="bulk-status" role="status">{bulk.label}: {bulk.done} из {bulk.total}</div>;
  }
  const failures = bulk.failures ?? [];
  return (
    <div className={failures.length ? "bulk-status failed" : "bulk-status"} role="status">
      <span>{bulk.summary}</span>
      {failures.length > 0 && <ul className="bulk-failures">{failures.map((text) => <li key={text}>{text}</li>)}</ul>}
      {onClose && <button type="button" className="link-button" onClick={onClose}>Закрыть</button>}
    </div>
  );
}

function StageEmpty({ text }: { text: string }) {
  return <p className="stage-empty">{text}</p>;
}

const qtyOf = (line: ProcurementLine) => `${line.actual_quantity ?? line.calculated_quantity} ${line.actual_unit ?? line.calculated_unit}`;

const idColumn: DataColumn<ProcurementLine> = { key: "legacy_id", title: "ID", value: (line) => line.legacy_id };
const nameColumn: DataColumn<ProcurementLine> = { key: "name", title: "Наименование", value: (line) => line.calculated_name, wrap: true };
const supplierColumn: DataColumn<ProcurementLine> = { key: "supplier", title: "Поставщик", value: (line) => line.supplier ?? "", filter: true };
const invoiceColumn: DataColumn<ProcurementLine> = {
  key: "invoice", title: "Счёт", value: (line) => line.invoice_number ?? "",
  render: (line) => line.invoice_url
    ? <span className="doc-links"><a href={line.invoice_url} target="_blank" rel="noreferrer" onClick={(event) => event.stopPropagation()}>{line.invoice_number ? `№ ${line.invoice_number}` : "счёт"}</a></span>
    : "—",
};
const sumColumn: DataColumn<ProcurementLine> = { key: "sum", title: "Сумма, ₽", value: (line) => line.actual_sum, numeric: true, render: (line) => formatAmount(line.actual_sum) };

type StageProps = {
  lines: ProcurementLine[];
  disabled: boolean;
  busyId: string;
  actionsOf: (line: ProcurementLine) => RowAction[];
  /** Правка комментария и ожидаемой даты в ячейке; без неё колонки только показывают. */
  editing?: LineEditing;
  /** Ход и итог массового действия этого блока. */
  bulk?: BulkState | null;
  onCloseBulk?: () => void;
};

/** «Комментарий» строки. */
function noteColumn(editing: LineEditing | undefined, disabled: boolean): DataColumn<ProcurementLine> {
  return { key: "note", title: "Комментарий", value: (line) => line.note ?? "", wrap: true, render: (line) => <NoteCell line={line} editing={editing} disabled={disabled} /> };
}

/** «Ожид. дата» строки: в стадиях, где её можно менять, — правится в ячейке. */
function expectedColumn(editing: LineEditing | undefined, disabled: boolean): DataColumn<ProcurementLine> {
  return { key: "expected", title: "Ожид. дата", value: (line) => line.expected_arrival, render: (line) => <ExpectedDateCell line={line} editing={editing} disabled={disabled} /> };
}

/** «Факт. дата»: день последнего входного контроля, ставит приёмка. */
const actualArrivalColumn: DataColumn<ProcurementLine> = { key: "actual_arrival", title: "Факт. дата", value: (line) => line.actual_arrival ?? "", render: (line) => formatDay(line.actual_arrival) };

function menuColumn(menu: (line: ProcurementLine) => ReactNode): DataColumn<ProcurementLine> {
  return { key: "actions", title: "", value: () => "", render: menu, width: 48 };
}

// ── «Жду счёт», «Ждёт оплаты», «Оплачено»: таблицы по заказам ──────────────────────────────────

export type OrderGroup = {
  key: string;
  number: string;
  supplier: string;
  date: string | null;
  url: string | null;
  expected: string | null;
  filled: number;
  lines: ProcurementLine[];
};

/** `portalKpOrders`: строки по № заказа — заказ, поставщик, число строк. */
export function groupByOrder(lines: ProcurementLine[]): OrderGroup[] {
  const groups = new Map<string, OrderGroup>();
  for (const line of lines) {
    const number = line.order_number ?? "";
    let group = groups.get(number);
    if (!group) {
      group = { key: `order:${number}`, number, supplier: "", date: null, url: null, expected: null, filled: 0, lines: [] };
      groups.set(number, group);
    }
    group.lines.push(line);
    group.supplier ||= line.supplier ?? "";
    group.date ||= line.order_date;
    group.url ||= line.order_url ?? null;
    if (line.expected_arrival && (!group.expected || line.expected_arrival < group.expected)) group.expected = line.expected_arrival;
    if (line.actual_sum) group.filled += 1;
  }
  return [...groups.values()];
}

/** «2026-10-05» → «05.10»: день заказа в заголовке группы (заказы на экране свежие, год не нужен). */
export function formatShortDay(value: string | null | undefined): string {
  const match = String(value ?? "").match(/^\d{4}-(\d{2})-(\d{2})/);
  return match ? `${match[2]}.${match[1]}` : String(value ?? "");
}

const total = (lines: ProcurementLine[], pick: (line: ProcurementLine) => string | null | undefined) =>
  lines.reduce((sum, line) => sum + (Number(pick(line)) || 0), 0);

/** «PO-012 от 05.10 · Поставщик» и «3 поз. · факт 1 250,00 ₽ / расчёт 1 100,00 ₽». */
function groupHeading(group: OrderGroup) {
  return {
    title: `${group.number || "Без № заказа"}${group.date ? ` от ${formatShortDay(group.date)}` : ""}${group.supplier ? ` · ${group.supplier}` : ""}`,
    meta: `${group.lines.length} поз. · факт ${group.lines.some((line) => line.actual_sum) ? `${formatAmount(total(group.lines, (line) => line.actual_sum))} ₽` : "—"} / расчёт ${formatAmount(total(group.lines, (line) => line.calculated_sum))} ₽`,
  };
}

// Что закупщик вводит сам до счёта: валюту, цену, цену с НДС и сумму выводит «Цена факт», наименование в счёт
// подставляется при «Вставить счёт», ожидаемая дата вводится в форме счёта.
const PRICE_FIELDS = new Set(["ВАЛЮТА", "ЦЕНА (факт)", "ЦЕНА С НДС (факт)", "СУММА (факт)"]);
const missingForInvoice = (line: ProcurementLine) => {
  const rest = line.pay_missing.filter((name) => !PRICE_FIELDS.has(name) && name !== "ОЖИД. ДАТА" && name !== "НАИМЕНОВАНИЕ (Счет)");
  return line.pay_missing.some((name) => PRICE_FIELDS.has(name)) ? ["Цена факт", ...rest] : rest;
};

/** Ближайший рабочий день (сегодня, если будни; иначе понедельник) — ISO, по местному времени. */
export function nearestWorkingDay(from = new Date()): string {
  const day = new Date(from.getFullYear(), from.getMonth(), from.getDate());
  while (day.getDay() === 0 || day.getDay() === 6) day.setDate(day.getDate() + 1);
  const pad = (part: number) => String(part).padStart(2, "0");
  return `${day.getFullYear()}-${pad(day.getMonth() + 1)}-${pad(day.getDate())}`;
}

type GroupColumn = { key: string; title: string; numeric?: boolean; cell?: string; render: (line: ProcurementLine) => ReactNode };

/** Правка «Цена факт» (с НДС) в ячейке: черновики по строкам, «Сохранить всё» — одним вызовом. */
type PriceEdit = {
  valueOf: (line: ProcurementLine) => string;
  set: (line: ProcurementLine, value: string) => void;
  /** «Сумма факт» строки: пока цена набрана, но не сохранена — кол-во факт × набранная цена. */
  sumOf: (line: ProcurementLine) => string | null | undefined;
  disabled: boolean;
};

/** Строки по заказам: заголовок группы (`tr.group-row`) с итогами и действиями, под ним строки заказа. */
function OrderGroupTable({ label, groups, columns, headerActions, groupExtra, menu }: {
  label: string;
  groups: OrderGroup[];
  columns: GroupColumn[];
  headerActions: (group: OrderGroup) => ReactNode;
  /** Форма под заголовком группы (счёт). */
  groupExtra?: (group: OrderGroup) => ReactNode;
  menu?: (line: ProcurementLine) => ReactNode;
}) {
  const span = columns.length + (menu ? 1 : 0);
  return (
    <div className="data-table" role="region" aria-label={label}>
      <div className="table-wrap">
        <table className="grid wide">
          <thead>
            <tr>
              {columns.map((column) => <th key={column.key} className={column.numeric ? "num" : undefined}>{column.title}</th>)}
              {menu && <th aria-label="Действия" />}
            </tr>
          </thead>
          {groups.map((group) => {
            const heading = groupHeading(group);
            const extra = groupExtra?.(group);
            return (
              <tbody key={group.key} aria-label={heading.title}>
                <tr className="group-row">
                  <td className="group-cell" colSpan={span}>
                    <div className="group-line">
                      <span className="group-title">{heading.title}</span>
                      <span className="group-meta">{heading.meta}</span>
                      <span className="group-actions">{headerActions(group)}</span>
                    </div>
                  </td>
                </tr>
                {extra && <tr className="group-form"><td colSpan={span}>{extra}</td></tr>}
                {group.lines.map((line) => (
                  <tr key={line.id}>
                    {columns.map((column) => <td key={column.key} className={[column.numeric && "num", column.cell].filter(Boolean).join(" ") || undefined}>{column.render(line)}</td>)}
                    {menu && <td className="menu">{menu(line)}</td>}
                  </tr>
                ))}
              </tbody>
            );
          })}
        </table>
      </div>
    </div>
  );
}

function OrderMenu({ group, disabled, items }: { group: OrderGroup; disabled: boolean; items: MenuItem[] }) {
  return <RowMenu label={`Действия заказа ${group.number || "без номера"}`} disabled={disabled} items={items} />;
}

const documentItems = (group: OrderGroup, onPrint: (url: string) => void): MenuItem[] => [
  { label: "Документ запроса", disabled: !group.url, title: group.url ? undefined : "Документ запроса не сформирован", run: () => { if (group.url) window.open(group.url, "_blank", "noopener"); } },
  { label: "Печать запроса", disabled: !group.url, run: () => { if (group.url) onPrint(group.url); } },
];

function priceColumn(price: PriceEdit): GroupColumn {
  return {
    key: "price", title: "Цена факт, ₽", numeric: true, cell: "cell-edit",
    render: (line) => (
      <input
        className="cell-input" aria-label={`Цена факт ${line.legacy_id}`} inputMode="decimal" disabled={price.disabled}
        value={price.valueOf(line)} onChange={(event) => price.set(line, event.target.value)}
      />
    ),
  };
}

/** Черновики «Цена факт» стадии: тронутые строки, «Сохранить всё (N)»; строки, что сервер пропустил, остаются набранными. */
function useFactPrices(lines: ProcurementLine[], onSave: (rows: FactPriceRow[]) => Promise<string[] | void>, disabled: boolean) {
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const base = (line: ProcurementLine) => line.actual_price_with_vat ?? "";
  const dirty = lines.filter((line) => edits[line.id] !== undefined && edits[line.id].trim() !== base(line));
  const price: PriceEdit = {
    valueOf: (line) => edits[line.id] ?? base(line),
    set: (line, value) => setEdits((current) => ({ ...current, [line.id]: value })),
    sumOf: (line) => {
      const typed = edits[line.id];
      if (typed === undefined || typed.trim() === base(line)) return line.actual_sum;
      const price = Number(typed.trim().replace(/\s/g, "").replace(",", "."));
      const quantity = Number(line.actual_quantity ?? line.calculated_quantity);
      return typed.trim() && Number.isFinite(price) && Number.isFinite(quantity) ? String(Math.round(price * quantity * 100) / 100) : null;
    },
    disabled: disabled || saving,
  };
  async function saveAll() {
    if (!dirty.length || saving) return;
    setSaving(true);
    try {
      const rows = dirty.map((line): FactPriceRow => ({
        line_id: line.id, version: line.version, legacy_id: line.legacy_id,
        price_with_vat: edits[line.id].trim().replace(",", ".") || null,
      }));
      const kept = new Set((await onSave(rows)) ?? []);
      setEdits((current) => Object.fromEntries(Object.entries(current).filter(([id]) => kept.has(lines.find((line) => line.id === id)?.legacy_id ?? ""))));
    } finally {
      setSaving(false);
    }
  }
  const button = (
    <button type="button" className={dirty.length ? "primary small" : "secondary small"} disabled={disabled || saving || !dirty.length} onClick={() => void saveAll()}>
      {saving ? "Сохраняем…" : dirty.length ? `Сохранить всё (${dirty.length})` : "Сохранить всё"}
    </button>
  );
  return { price, button };
}

export type OrderInvoiceHandlers = {
  /** Файл счёта → ссылка и распознанные № и дата. */
  onUploadInvoice: (file: File, lineId: string) => Promise<InvoiceUpload>;
  /** Отправка счёта по заказу; при отказе бросает Error с текстом сервера. */
  onSubmitInvoice: (group: OrderGroup, invoice: OrderInvoiceInput) => Promise<void>;
};

/** «Вставить счёт» / «Заменить счёт»: один счёт на все строки заказа — файл, № и дата (из файла, правятся),
 *  ожидаемая дата обязательна. Все строки заказа или ни одной. */
function OrderInvoiceForm({ group, replace, disabled, onUploadInvoice, onSubmitInvoice, onClose }: OrderInvoiceHandlers & {
  group: OrderGroup;
  replace: boolean;
  disabled: boolean;
  onClose: () => void;
}) {
  const first = group.lines[0];
  const [url, setUrl] = useState("");
  const [fileNote, setFileNote] = useState("");
  const [number, setNumber] = useState(replace ? first?.invoice_number ?? "" : "");
  const [date, setDate] = useState(replace ? first?.invoice_date ?? "" : "");
  const [expected, setExpected] = useState(replace && group.expected ? group.expected : nearestWorkingDay());
  const [uploading, setUploading] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const busy = uploading || sending;

  async function upload(file: File) {
    setUploading(true);
    setError("");
    setFileNote("");
    try {
      const result = await onUploadInvoice(file, first.id);
      setUrl(result.url);
      if (result.invoice_number) setNumber(result.invoice_number);
      if (result.invoice_date) setDate(result.invoice_date);
      setFileNote(result.hint
        ? `${result.file_name} · ${result.hint}`
        : `${result.file_name} · распознано: № ${result.invoice_number || "—"}, дата ${formatDay(result.invoice_date) || "—"} — проверьте`);
    } catch (reason) {
      setError(failureText(reason, "Счёт не загружен"));
    } finally {
      setUploading(false);
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSending(true);
    setError("");
    try {
      await onSubmitInvoice(group, {
        invoice_url: url,
        invoice_number: number.trim() || null,
        invoice_date: date || null,
        expected_arrival: expected,
        lines: group.lines.map((line) => ({ line_id: line.id, version: line.version })),
      });
      onClose();
    } catch (reason) {
      setError(failureText(reason, "Счёт не принят"));
      setSending(false);
    }
  }

  return (
    <form className="inline-action order-invoice-form" onSubmit={submit} aria-label={`${replace ? "Заменить счёт" : "Вставить счёт"} ${group.number}`}>
      <label>Файл счёта
        <input type="file" accept=".pdf,image/*" disabled={disabled || busy} onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ""; if (file) void upload(file); }} />
      </label>
      <label>№ счёта<input value={number} disabled={disabled || busy} onChange={(event) => setNumber(event.target.value)} /></label>
      <label>Дата счёта<input type="date" value={date} disabled={disabled || busy} onChange={(event) => setDate(event.target.value)} /></label>
      <label>Ожидаемая дата *<input type="date" required value={expected} disabled={disabled || busy} onChange={(event) => setExpected(event.target.value)} /></label>
      <button className="primary small" disabled={disabled || busy || !url || !expected}>
        {sending ? "Передаём…" : replace ? "Заменить счёт" : `В оплату (${group.lines.length})`}
      </button>
      <button type="button" className="secondary small" disabled={sending} onClick={onClose}>Закрыть</button>
      {uploading && <small className="hint">Загружаем счёт…</small>}
      {fileNote && <small className="hint">{fileNote}</small>}
      {error && <small className="danger-text" role="alert">{error}</small>}
    </form>
  );
}

/** Какая форма счёта открыта под заголовком какого заказа. */
function useOpenGroup(groups: OrderGroup[]) {
  const [openKey, setOpenKey] = useState<string | null>(null);
  useEffect(() => { if (openKey && !groups.some((group) => group.key === openKey)) setOpenKey(null); }, [groups, openKey]);
  return { openKey, toggle: (key: string) => setOpenKey((current) => (current === key ? null : key)), close: () => setOpenKey(null) };
}

export function AwaitingInvoiceStage({ lines, disabled, busyId, actionsOf, editing, bulk, onCloseBulk, onOpenKp, onPrint, onSavePrices, onUploadInvoice, onSubmitInvoice }: StageProps & OrderInvoiceHandlers & {
  /** «Внести КП» по заказу. */
  onOpenKp: (references: string[]) => void;
  onPrint: (url: string) => void;
  onSavePrices: (rows: FactPriceRow[]) => Promise<string[] | void>;
}) {
  const groups = useMemo(() => groupByOrder(lines), [lines]);
  const panel = useLinePanel(lines, actionsOf, disabled, busyId);
  const prices = useFactPrices(lines, onSavePrices, disabled);
  const open = useOpenGroup(groups);
  const columns: GroupColumn[] = [
    { key: "id", title: "ID", render: (line) => <strong>{line.legacy_id}</strong> },
    { key: "name", title: "Наименование", render: (line) => line.calculated_name },
    { key: "qty", title: "Кол-во (факт)", render: qtyOf },
    priceColumn(prices.price),
    { key: "sum", title: "Сумма факт, ₽", numeric: true, render: (line) => formatAmount(prices.price.sumOf(line)) },
    { key: "calc", title: "Расчёт, ₽", numeric: true, render: (line) => formatAmount(line.calculated_sum) },
    { key: "expected", title: "Ожид. дата", render: (line) => <ExpectedDateCell line={line} editing={editing} disabled={disabled} /> },
    {
      key: "missing", title: "Не хватает для счёта",
      render: (line) => { const missing = missingForInvoice(line); return missing.length ? <span className="tone-warn">{missing.join(", ")}</span> : <span className="tone-ok">всё есть</span>; },
    },
    { key: "note", title: "Комментарий", render: (line) => <NoteCell line={line} editing={editing} disabled={disabled} /> },
  ];
  return (
    <section className="panel table-panel procurement-stage">
      <StageHeading title="Жду счёт" count={groups.length} hint="«Вставить счёт» — один файл на заказ: все его строки уходят «В оплату» или ни одной. «Цена факт» правится в строке; «Внести КП» — в меню заказа">
        {prices.button}
      </StageHeading>
      <BulkStatus bulk={bulk} onClose={onCloseBulk} />
      {!groups.length ? <StageEmpty text="Нет заказов, ждущих счёт" /> : (
        <OrderGroupTable
          label="Жду счёт"
          groups={groups}
          columns={columns}
          menu={panel.menu}
          headerActions={(group) => (
            <>
              <button type="button" className="primary small" disabled={disabled || !group.number} aria-expanded={open.openKey === group.key} onClick={() => open.toggle(group.key)}>Вставить счёт</button>
              <OrderMenu group={group} disabled={disabled} items={[
                ...documentItems(group, onPrint),
                { label: "Внести КП", disabled: !group.number, run: () => onOpenKp([group.number]) },
              ]} />
            </>
          )}
          groupExtra={(group) => open.openKey === group.key
            ? <OrderInvoiceForm key={group.key} group={group} replace={false} disabled={disabled} onUploadInvoice={onUploadInvoice} onSubmitInvoice={onSubmitInvoice} onClose={open.close} />
            : null}
        />
      )}
      {panel.node}
    </section>
  );
}

// ── Ждёт оплаты ─────────────────────────────────────────────────────────────────────────────────

/** `portalPurchaseToPay`: «В оплату» со счётом и без отметки об оплате — кандидаты на «Оплачено → В пути». */
export function payable(line: ProcurementLine) {
  return line.status === "В оплату" && Boolean(line.invoice_url) && !line.paid_at;
}

function payReadiness(line: ProcurementLine) {
  if (!line.invoice_url) return <span className="tone-warn">нет счёта</span>;
  if (line.pay_missing.length) return <span className="tone-warn">не хватает: {line.pay_missing.join(", ")}</span>;
  return <span className="tone-ok">можно оплачивать</span>;
}

/** «Оплачено» заказа; когда ЦБ молчит, сервер просит курс — поле встаёт рядом с кнопкой. */
function PaidOrderAction({ group, disabled, onPaidOrder }: {
  group: OrderGroup;
  disabled: boolean;
  onPaidOrder: (group: OrderGroup, manualRate?: string) => Promise<ManualRatePrompt | null>;
}) {
  const [prompt, setPrompt] = useState<ManualRatePrompt | null>(null);
  const [rate, setRate] = useState("");
  const [busy, setBusy] = useState(false);
  const notReady = group.lines.some((line) => !payable(line) || line.pay_missing.length > 0);

  async function pay() {
    setBusy(true);
    try {
      setPrompt(await onPaidOrder(group, prompt ? rate.trim() : undefined));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      {prompt && (
        <label className="inline-rate">{prompt.message}. Курс, ₽ за 1 {prompt.currency} *
          <input inputMode="decimal" required value={rate} onChange={(event) => setRate(event.target.value)} />
        </label>
      )}
      <button
        type="button" className="primary small" disabled={disabled || busy || notReady || (Boolean(prompt) && !rate.trim())}
        title={notReady ? "Оплата закрыта: у строк нет счёта или не заполнены суммы" : undefined} onClick={() => void pay()}
      >
        {busy ? "Проводим…" : "Оплачено"}
      </button>
    </>
  );
}

export function ToPayStage({ lines, disabled, busyId, actionsOf, editing, bulk, onCloseBulk, onPrint, onSavePrices, onUploadInvoice, onSubmitInvoice, onPaidOrder }: StageProps & OrderInvoiceHandlers & {
  onPrint: (url: string) => void;
  onSavePrices: (rows: FactPriceRow[]) => Promise<string[] | void>;
  /** «Оплачено» по заказу; вернёт вопрос про курс, если ЦБ не ответил. */
  onPaidOrder: (group: OrderGroup, manualRate?: string) => Promise<ManualRatePrompt | null>;
}) {
  const groups = useMemo(() => groupByOrder(lines), [lines]);
  const panel = useLinePanel(lines, actionsOf, disabled, busyId);
  const prices = useFactPrices(lines, onSavePrices, disabled);
  const open = useOpenGroup(groups);
  const columns: GroupColumn[] = [
    { key: "id", title: "ID", render: (line) => <strong>{line.legacy_id}</strong> },
    { key: "name", title: "Наименование", render: (line) => line.calculated_name },
    { key: "qty", title: "Кол-во (факт)", render: qtyOf },
    priceColumn(prices.price),
    { key: "sum", title: "Сумма факт, ₽", numeric: true, render: (line) => formatAmount(prices.price.sumOf(line)) },
    { key: "calc", title: "Расчёт, ₽", numeric: true, render: (line) => formatAmount(line.calculated_sum) },
    { key: "invoice", title: "Счёт", render: (line) => invoiceColumn.render!(line) },
    { key: "expected", title: "Ожид. дата", render: (line) => <ExpectedDateCell line={line} editing={editing} disabled={disabled} /> },
    { key: "ready", title: "Готовность", render: payReadiness },
    { key: "note", title: "Комментарий", render: (line) => <NoteCell line={line} editing={editing} disabled={disabled} /> },
  ];
  return (
    <section className="panel table-panel procurement-stage">
      <StageHeading title="Ждёт оплаты" count={groups.length} hint="«Оплачено» — все строки заказа уходят «В пути» в приёмку. «Цена факт» правится в строке">
        {prices.button}
      </StageHeading>
      <BulkStatus bulk={bulk} onClose={onCloseBulk} />
      {!groups.length ? <StageEmpty text="Нет счетов к оплате" /> : (
        <OrderGroupTable
          label="Ждёт оплаты"
          groups={groups}
          columns={columns}
          menu={panel.menu}
          headerActions={(group) => (
            <>
              <PaidOrderAction group={group} disabled={disabled} onPaidOrder={onPaidOrder} />
              <OrderMenu group={group} disabled={disabled} items={[
                ...documentItems(group, onPrint),
                { label: "Заменить счёт", run: () => open.toggle(group.key) },
              ]} />
            </>
          )}
          groupExtra={(group) => open.openKey === group.key
            ? <OrderInvoiceForm key={group.key} group={group} replace disabled={disabled} onUploadInvoice={onUploadInvoice} onSubmitInvoice={onSubmitInvoice} onClose={open.close} />
            : null}
        />
      )}
      {panel.node}
    </section>
  );
}

// ── Дозавоз ─────────────────────────────────────────────────────────────────────────────────────

function BackorderAction({ line, disabled, onConfirm }: { line: ProcurementLine; disabled: boolean; onConfirm: (line: ProcurementLine, date: string) => void }) {
  const [date, setDate] = useState(line.expected_arrival ?? "");
  return (
    <span className="backorder-action">
      <input type="date" aria-label={`Новая ожидаемая дата ${line.legacy_id}`} value={date} onChange={(event) => setDate(event.target.value)} />
      <button type="button" className="primary small" disabled={disabled || !date} onClick={() => onConfirm(line, date)}>Досыл подтверждён → в пути</button>
    </span>
  );
}

const backorderBase = (line: ProcurementLine) => line.legacy_id.replace(/-Д\d*$/, "");

const backorderNoteColumn: DataColumn<ProcurementLine> = {
  key: "basis", title: "Основание", wrap: true,
  value: (line) => `Дозавоз по ${backorderBase(line)}: недовоз уже оплачен по счёту ${line.invoice_number ? `№ ${line.invoice_number}` : line.invoice_url ?? ""}`,
};

/** Дозавоз — недовоз оплаченной строки (приёмка ставит строку `-Д` в «Согласование»): подтвердить досыл
 *  с новой датой или отменить. Блок есть, только когда дозавоз есть. */
export function BackorderStage({ lines, disabled, busyId, actionsOf, editing, onConfirmBackorder }: StageProps & {
  onConfirmBackorder: (line: ProcurementLine, date: string) => void;
}) {
  const panel = useLinePanel(lines, actionsOf, disabled, busyId);
  const columns: DataColumn<ProcurementLine>[] = [
    idColumn,
    nameColumn,
    supplierColumn,
    { key: "qty", title: "Кол-во", value: qtyOf },
    backorderNoteColumn,
    // «Сумма» есть, только когда ей есть что показать: не `hidden`, а вне списка, иначе таблица запомнит «скрыто».
    ...(lines.some((line) => line.actual_sum) ? [sumColumn] : []),
    noteColumn(editing, disabled),
    {
      key: "decision", title: "Решение", value: () => "",
      render: (line) => <BackorderAction line={line} disabled={disabled || busyId === line.id} onConfirm={onConfirmBackorder} />,
    },
  ];
  return (
    <section className="panel table-panel procurement-stage">
      <StageHeading title="Дозавоз" count={lines.length} hint="Недовоз уже оплачен: подтвердите досыл с новой датой или отмените дозавоз в меню строки" />
      <DataTable
        label="Дозавоз"
        rows={lines}
        columns={[...columns, menuColumn(panel.menu)]}
        rowKey={(line) => line.id}
        storageKey="procurement-backorder"
        compactBelow={COMPACT_BELOW}
        emptyText="Дозавоза нет"
      />
      {panel.node}
    </section>
  );
}

// ── Оплачено: в пути и карантин (стадии приёмки) ────────────────────────────────────────────────

export function PaidStage({ lines, disabled, busyId, actionsOf, editing, bulk, onCloseBulk, onReconcile, onPrint }: StageProps & {
  /** «Сверить курс с ЦБ» для строк с курсом, введённым вручную. */
  onReconcile: (lines: ProcurementLine[]) => Promise<unknown>;
  onPrint: (url: string) => void;
}) {
  const groups = useMemo(() => groupByOrder(lines), [lines]);
  const panel = useLinePanel(lines, actionsOf, disabled, busyId);
  // «В счёте» и «Курс ЦБ» — только когда есть счёт в валюте; рублёвые строки их не заполняют.
  const foreign = lines.some((line) => line.source_currency);
  const manual = lines.filter((line) => line.exchange_rate_manual);
  const columns: GroupColumn[] = [
    { key: "id", title: "ID", render: (line) => <strong>{line.legacy_id}</strong> },
    { key: "name", title: "Наименование", render: (line) => line.calculated_name },
    { key: "qty", title: "Кол-во (факт)", render: qtyOf },
    { key: "expected", title: "Ожид. дата", render: (line) => <ExpectedDateCell line={line} editing={editing} disabled={disabled} /> },
    { key: "actual_arrival", title: "Факт. дата", render: (line) => formatDay(line.actual_arrival) },
    { key: "status", title: "Статус", render: (line) => line.status === "Карантин" ? <span className="tone-warn" title="Приёмка нашла несоответствие — ждёт решения">Карантин</span> : line.status },
    { key: "invoice", title: "Счёт", render: (line) => invoiceColumn.render!(line) },
    { key: "sum", title: "Сумма, ₽", numeric: true, render: (line) => formatAmount(line.actual_sum) },
    ...(foreign ? [
      { key: "source", title: "В счёте", render: (line: ProcurementLine) => (line.source_currency ? `${line.source_sum ?? line.source_price_with_vat ?? line.source_price ?? ""} ${line.source_currency}` : "") },
      {
        key: "rate", title: "Курс ЦБ",
        render: (line: ProcurementLine) => line.source_currency
          ? <>{line.exchange_rate} на {formatDate(line.exchange_rate_date)}{line.exchange_rate_manual && <span className="tone-warn"> · вручную, сверьте</span>}</>
          : "",
      },
    ] : []),
    { key: "note", title: "Комментарий", render: (line) => <NoteCell line={line} editing={editing} disabled={disabled} /> },
  ];
  return (
    <section className="panel table-panel procurement-stage">
      <StageHeading title="Оплачено" count={groups.length} hint="Оплачено и передано в приёмку: в пути и на карантине (приёмка нашла несоответствие — ждёт решения)">
        {manual.length > 0 && (
          <button type="button" className="secondary small" disabled={disabled} onClick={() => void onReconcile(manual)}>Сверить курс с ЦБ ({manual.length})</button>
        )}
      </StageHeading>
      <BulkStatus bulk={bulk} onClose={onCloseBulk} />
      {!groups.length ? <StageEmpty text="Нет оплаченных позиций в пути" /> : (
        <OrderGroupTable
          label="Оплачено"
          groups={groups}
          columns={columns}
          menu={panel.menu}
          headerActions={(group) => <OrderMenu group={group} disabled={disabled} items={documentItems(group, onPrint)} />}
        />
      )}
      {panel.node}
    </section>
  );
}

// ── Прочие статусы: строка не пропадает с экрана, даже если стадия новая ───────────────────────────

export function OtherStage({ lines, disabled, busyId, actionsOf, editing }: StageProps) {
  const panel = useLinePanel(lines, actionsOf, disabled, busyId);
  return (
    <section className="panel table-panel procurement-stage">
      <StageHeading title="Прочие позиции" count={lines.length} />
      <DataTable
        label="Прочие позиции"
        rows={lines}
        columns={[
          idColumn, nameColumn, supplierColumn,
          { key: "status", title: "Статус", value: (line) => line.status, filter: true },
          { key: "expected", title: "Ожид. дата", value: (line) => line.expected_arrival, render: (line) => formatDay(line.expected_arrival) },
          actualArrivalColumn, noteColumn(editing, disabled), menuColumn(panel.menu),
        ]}
        rowKey={(line) => line.id}
        storageKey="procurement-other"
        compactBelow={COMPACT_BELOW}
      />
      {panel.node}
    </section>
  );
}
