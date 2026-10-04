import { useEffect, useMemo, useState, type ReactNode } from "react";
import { DataTable, type DataBulkAction, type DataColumn } from "./DataTable";
import type { ProcurementLine } from "./api";
import { formatDate, formatDay } from "./format";
import { ExpectedDateCell, NoteCell, type LineEditing } from "./ProcurementCellEdit";
import { RowMenu, type MenuItem } from "./ProcurementRowMenu";
import { formatAmount } from "./ProcurementSheetColumns";

/* «Мои позиции» листа закупки — блоки в порядке работы (владелец, 04.10): «В работе» (ProcurementInWorkTable),
   «Жду счёт» по заказам (portalKpOrders), «Ждёт оплаты» (portalPurchaseToPay), «Оплачено» (в пути и карантин),
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

// ── Жду счёт: по заказам ────────────────────────────────────────────────────────────────────────

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

/** `portalKpOrders`: строки «Жду счет» по № заказа — заказ, поставщик, число строк. */
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

const missingForInvoice = (line: ProcurementLine) => line.pay_missing.filter((name) => name !== "ОЖИД. ДАТА");

export function AwaitingInvoiceStage({ lines, disabled, busyId, actionsOf, editing, bulk, onCloseBulk, onOpenKp, onPrint }: StageProps & {
  /** «Внести КП» по заказам по очереди — один заказ или все выбранные. */
  onOpenKp: (references: string[]) => void;
  onPrint: (url: string) => void;
}) {
  const groups = useMemo(() => groupByOrder(lines), [lines]);
  const panel = useLinePanel(lines, actionsOf, disabled, busyId);
  const columns: DataColumn<OrderGroup>[] = [
    { key: "number", title: "№ заказа", value: (group) => group.number, render: (group) => group.number || "Без № заказа" },
    { key: "supplier", title: "Поставщик", value: (group) => group.supplier, filter: true },
    { key: "count", title: "Позиций", value: (group) => group.lines.length, numeric: true },
    { key: "date", title: "Заказ от", value: (group) => group.date },
    { key: "expected", title: "Ожидается", value: (group) => group.expected },
    { key: "filled", title: "КП внесено", value: (group) => `${group.filled} из ${group.lines.length}` },
    {
      key: "document", title: "Документ", value: (group) => group.url ?? "",
      render: (group) => group.url ? (
        <span className="doc-links">
          <a href={group.url} target="_blank" rel="noreferrer" onClick={(event) => event.stopPropagation()}>Открыть документ</a>
          <button type="button" className="link-button" onClick={(event) => { event.stopPropagation(); onPrint(group.url!); }}>Печать</button>
        </span>
      ) : "—",
    },
    {
      key: "kp", title: "", value: () => "",
      render: (group) => (
        <button type="button" className="primary small" disabled={disabled || !group.number} onClick={(event) => { event.stopPropagation(); onOpenKp([group.number]); }}>Внести КП</button>
      ),
    },
  ];
  const bulkActions: DataBulkAction<OrderGroup>[] = [
    {
      label: "Внести КП",
      run: (rows) => onOpenKp(rows.map((group) => group.number).filter(Boolean)),
      disabled: (rows) => disabled || !rows.some((group) => group.number),
    },
    {
      label: "Печать запросов",
      run: (rows) => { for (const group of rows) if (group.url) onPrint(group.url); },
      disabled: (rows) => !rows.some((group) => group.url),
    },
  ];
  return (
    <section className="panel table-panel procurement-stage">
      <StageHeading title="Жду счёт" count={groups.length} hint="«Внести КП» — все строки заказа одной формой, можно из файла КП; несколько выбранных заказов — по очереди" />
      <BulkStatus bulk={bulk} onClose={onCloseBulk} />
      {!groups.length ? <StageEmpty text="Нет заказов, ждущих счёт" /> : (
        <DataTable
          label="Жду счёт"
          rows={groups}
          columns={columns}
          rowKey={(group) => group.key}
          storageKey="procurement-awaiting-invoice"
          compactBelow={COMPACT_BELOW}
          bulkActions={bulkActions}
          emptyText="Нет заказов, ждущих счёт"
          renderDetail={(group) => (
            <table className="buy-grid order-lines">
              <thead>
                <tr>
                  <th>ID</th><th>Наименование</th><th>Кол-во (факт)</th><th>Цена, ₽</th><th>Сумма, ₽</th><th>№ счёта</th><th>Ожид. дата</th><th>Не хватает для счёта</th><th>Комментарий</th><th aria-label="Действия" />
                </tr>
              </thead>
              <tbody>
                {group.lines.map((line) => {
                  const missing = missingForInvoice(line);
                  return (
                    <tr key={line.id}>
                      <td><strong>{line.legacy_id}</strong></td>
                      <td>{line.calculated_name}</td>
                      <td>{qtyOf(line)}</td>
                      <td className="num">{formatAmount(line.actual_price)}</td>
                      <td className="num">{formatAmount(line.actual_sum)}</td>
                      <td>{line.invoice_number ?? ""}</td>
                      <td className="edit-cell"><ExpectedDateCell line={line} editing={editing} disabled={disabled} /></td>
                      <td className="missing-cell">{missing.length ? <span className="tone-warn">{missing.join(", ")}</span> : <span className="tone-ok">всё есть</span>}</td>
                      <td className="edit-cell note-cell"><NoteCell line={line} editing={editing} disabled={disabled} /></td>
                      <td className="menu">{panel.menu(line)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
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

const toPayColumns: DataColumn<ProcurementLine>[] = [
  idColumn,
  { key: "order", title: "№ заказа", value: (line) => line.order_number ?? "" },
  supplierColumn,
  nameColumn,
  { key: "qty", title: "Кол-во (факт)", value: qtyOf },
  sumColumn,
  invoiceColumn,
  { key: "ready", title: "Готовность", value: (line) => (payable(line) ? "можно оплачивать" : "не готово"), render: payReadiness },
];

export function ToPayStage({ lines, disabled, busyId, actionsOf, editing, bulk, onCloseBulk, onMarkPaid }: StageProps & { onMarkPaid: (lines: ProcurementLine[]) => Promise<void> }) {
  const panel = useLinePanel(lines, actionsOf, disabled, busyId);
  const bulkActions: DataBulkAction<ProcurementLine>[] = [{ label: "Оплачено → В пути", run: onMarkPaid, disabled: () => disabled }];
  return (
    <section className="panel table-panel procurement-stage">
      <StageHeading title="Ждёт оплаты" count={lines.length} hint="Отметьте оплаченные счета — строки уйдут «В пути» в приёмку" />
      <BulkStatus bulk={bulk} onClose={onCloseBulk} />
      {!lines.length ? <StageEmpty text="Нет счетов к оплате" /> : (
        <DataTable
          label="Ждёт оплаты"
          rows={lines}
          columns={[...toPayColumns.slice(0, -1), expectedColumn(editing, disabled), toPayColumns[toPayColumns.length - 1], noteColumn(editing, disabled), menuColumn(panel.menu)]}
          rowKey={(line) => line.id}
          storageKey="procurement-to-pay"
          compactBelow={COMPACT_BELOW}
          bulkActions={bulkActions}
          emptyText="Нет строк в оплату"
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

const receiptColumns: DataColumn<ProcurementLine>[] = [
  idColumn,
  nameColumn,
  supplierColumn,
  { key: "order", title: "№ заказа", value: (line) => line.order_number ?? "" },
  { key: "qty", title: "Кол-во (факт)", value: qtyOf },
  {
    key: "status", title: "Статус", value: (line) => line.status, filter: true,
    render: (line) => line.status === "Карантин" ? <span className="tone-warn" title="Приёмка нашла несоответствие — ждёт решения">Карантин</span> : line.status,
  },
  invoiceColumn,
  sumColumn,
  {
    key: "source", title: "В счёте", value: (line) => (line.source_currency ? `${line.source_sum ?? line.source_price_with_vat ?? line.source_price ?? ""} ${line.source_currency}` : ""),
  },
  {
    key: "rate", title: "Курс ЦБ", value: (line) => (line.source_currency ? `${line.exchange_rate ?? ""}${line.exchange_rate_manual ? " · вручную" : ""}` : ""),
    render: (line) => line.source_currency
      ? <>{line.exchange_rate} на {formatDate(line.exchange_rate_date)}{line.exchange_rate_manual && <span className="tone-warn"> · вручную, сверьте</span>}</>
      : "",
  },
];

export function PaidStage({ lines, disabled, busyId, actionsOf, editing, bulk, onCloseBulk, onReconcile }: StageProps & {
  /** «Сверить курс с ЦБ» для выбранных строк с курсом, введённым вручную. */
  onReconcile: (lines: ProcurementLine[]) => Promise<unknown>;
}) {
  const panel = useLinePanel(lines, actionsOf, disabled, busyId);
  // «В счёте» и «Курс ЦБ» — только когда есть счёт в валюте; рублёвые строки их не заполняют (колонки вне списка, не `hidden`).
  const foreign = lines.some((line) => line.source_currency);
  const shownColumns = receiptColumns.filter((column) => foreign || (column.key !== "source" && column.key !== "rate"));
  // Сроки — рядом с количеством: ожидали → приняли; «Комментарий» последним перед меню.
  const at = shownColumns.findIndex((column) => column.key === "qty") + 1;
  const columns = [...shownColumns.slice(0, at), expectedColumn(editing, disabled), actualArrivalColumn, ...shownColumns.slice(at), noteColumn(editing, disabled)];
  const manual = (rows: ProcurementLine[]) => rows.filter((line) => line.exchange_rate_manual);
  const bulkActions: DataBulkAction<ProcurementLine>[] = [{
    label: "Сверить курс с ЦБ",
    run: (rows) => onReconcile(manual(rows)),
    disabled: (rows) => disabled || !manual(rows).length,
  }];
  return (
    <section className="panel table-panel procurement-stage">
      <StageHeading title="Оплачено" count={lines.length} hint="Оплачено и передано в приёмку: в пути и на карантине (приёмка нашла несоответствие — ждёт решения)" />
      <BulkStatus bulk={bulk} onClose={onCloseBulk} />
      {!lines.length ? <StageEmpty text="Нет оплаченных позиций в пути" /> : (
        <DataTable
          label="Оплачено"
          rows={lines}
          columns={[...columns, menuColumn(panel.menu)]}
          rowKey={(line) => line.id}
          storageKey="procurement-paid"
          compactBelow={COMPACT_BELOW}
          bulkActions={bulkActions}
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
