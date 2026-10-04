import { useEffect, useMemo, useState, type ReactNode } from "react";
import { DataTable, type DataBulkAction, type DataColumn } from "./DataTable";
import type { ProcurementLine } from "./api";
import { formatDate, formatDay } from "./format";
import { ExpectedDateCell, NoteCell, type LineEditing } from "./ProcurementCellEdit";
import { RowMenu, type MenuItem } from "./ProcurementRowMenu";
import { formatAmount } from "./ProcurementSheetColumns";

/* «Мои позиции» листа закупки: у каждой стадии после «В работе» своя плотная таблица, как в SUPPLYMAP —
   «Готов к запросу» (Запрос поставщику), «Жду счет» по заказам (portalKpOrders), «В оплату» со списком
   «отметить оплаченные» (portalPurchaseToPay), «Согласование» вместе с дозавозом, «В пути».
   Таблицы показывают; что делать со строкой, решает страница (`actionsOf`, колбэки): редкие
   действия лежат в меню «⋯» строки, формы открываются под таблицей. Две колонки правятся прямо в
   ячейке (`editing`): «Комментарий» на каждой стадии после «В работе» и «Ожид. дата» от «Жду счет»
   до «Карантин»; «Факт. дата» (день приёмки) только показывается. */

/** Действие строки: выполняется сразу (`run`) или открывает форму под таблицей (`panel`). */
export type RowAction = { label: string; title?: string; disabled?: boolean; run?: () => void; panel?: () => ReactNode };

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

function StageHeading({ title, count, hint, children }: { title: string; count: number; hint?: string; children?: ReactNode }) {
  return (
    <div className="panel-heading">
      <h2>{title} <span className="stage-count">{count}</span></h2>
      <div className="heading-actions">{hint && <span>{hint}</span>}{children}</div>
    </div>
  );
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

// ── Готов к запросу ─────────────────────────────────────────────────────────────────────────────

const readyColumns: DataColumn<ProcurementLine>[] = [
  idColumn,
  nameColumn,
  { key: "site_name", title: "Наименование на сайте", value: (line) => line.site_name ?? "", wrap: true },
  { key: "designation", title: "Назначение", value: (line) => line.designation },
  supplierColumn,
  { key: "qty", title: "Кол-во (факт)", value: qtyOf },
  { key: "delivery_point", title: "Точка отгрузки", value: (line) => line.delivery_point },
  { key: "planned", title: "Срок", value: (line) => line.planned_date },
];

export function ReadyStage({ lines, disabled, busyId, actionsOf, editing, building, onBuild }: StageProps & { building: boolean; onBuild: () => void }) {
  const panel = useLinePanel(lines, actionsOf, disabled, busyId);
  return (
    <section className="panel table-panel procurement-stage">
      <StageHeading title="Готов к запросу" count={lines.length} hint="Запросы группируются по поставщику и типу задачи, № заказа PO-NNN">
        <button type="button" className="primary small" disabled={disabled} onClick={onBuild}>
          {building ? "Формируем…" : "Сформировать запросы поставщикам"}
        </button>
      </StageHeading>
      <DataTable
        label="Готов к запросу"
        rows={lines}
        columns={[...readyColumns, noteColumn(editing, disabled), menuColumn(panel.menu)]}
        rowKey={(line) => line.id}
        storageKey="procurement-ready"
        emptyText="Нет строк, готовых к запросу"
      />
      {panel.node}
    </section>
  );
}

// ── Жду счет: по заказам ────────────────────────────────────────────────────────────────────────

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

export function AwaitingInvoiceStage({ lines, disabled, busyId, actionsOf, editing, onOpenKp, onPrint }: StageProps & {
  onOpenKp: (reference: string) => void;
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
        <button type="button" className="primary small" disabled={disabled || !group.number} onClick={(event) => { event.stopPropagation(); onOpenKp(group.number); }}>Внести КП</button>
      ),
    },
  ];
  return (
    <section className="panel table-panel procurement-stage">
      <StageHeading title="Жду счет" count={groups.length} hint="«Внести КП» — все строки заказа одной формой, можно из файла КП" />
      <DataTable
        label="Жду счет"
        rows={groups}
        columns={columns}
        rowKey={(group) => group.key}
        storageKey="procurement-awaiting-invoice"
        emptyText="Нет заказов, ждущих счёт"
        renderDetail={(group) => (
          <table className="inwork-table order-lines">
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
                    <td className="inwork-menu">{panel.menu(line)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      />
      {panel.node}
    </section>
  );
}

// ── В оплату ────────────────────────────────────────────────────────────────────────────────────

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

export function ToPayStage({ lines, disabled, busyId, actionsOf, editing, onMarkPaid }: StageProps & { onMarkPaid: (lines: ProcurementLine[]) => Promise<void> }) {
  const panel = useLinePanel(lines, actionsOf, disabled, busyId);
  const bulk: DataBulkAction<ProcurementLine>[] = [{ label: "Оплачено → В пути", run: onMarkPaid, disabled: () => disabled }];
  return (
    <section className="panel table-panel procurement-stage">
      <StageHeading title="В оплату" count={lines.length} hint="Отметьте оплаченные счета — строки уйдут «В пути» в приёмку" />
      <DataTable
        label="В оплату"
        rows={lines}
        columns={[...toPayColumns.slice(0, -1), expectedColumn(editing, disabled), toPayColumns[toPayColumns.length - 1], noteColumn(editing, disabled), menuColumn(panel.menu)]}
        rowKey={(line) => line.id}
        storageKey="procurement-to-pay"
        bulkActions={bulk}
        emptyText="Нет строк в оплату"
      />
      {panel.node}
    </section>
  );
}

// ── Согласование (вместе с дозавозом) ───────────────────────────────────────────────────────────

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
  key: "note", title: "Основание", wrap: true,
  value: (line) => (line.is_backorder ? `Дозавоз по ${backorderBase(line)}: недовоз уже оплачен по счёту ${line.invoice_number ? `№ ${line.invoice_number}` : line.invoice_url ?? ""}` : ""),
};

export function ApprovalStage({ lines, disabled, busyId, actionsOf, editing, onResume, onConfirmBackorder }: StageProps & {
  onResume: (line: ProcurementLine) => void;
  onConfirmBackorder: (line: ProcurementLine, date: string) => void;
}) {
  const panel = useLinePanel(lines, actionsOf, disabled, busyId);
  // Колонки «Основание» (у дозавоза) и «Сумма» есть, только когда им есть что показать: не `hidden`, а вне списка,
  // иначе таблица запомнит «скрыто» с первого показа и спрячет их, когда данные появятся.
  const columns: DataColumn<ProcurementLine>[] = [
    idColumn,
    nameColumn,
    supplierColumn,
    { key: "qty", title: "Кол-во", value: qtyOf },
    { key: "kind", title: "Вид", value: (line) => (line.is_backorder ? "Дозавоз" : "Ждёт одобрения"), filter: true },
    ...(lines.some((line) => line.is_backorder) ? [backorderNoteColumn] : []),
    ...(lines.some((line) => line.actual_sum) ? [sumColumn] : []),
    noteColumn(editing, disabled),
    {
      key: "decision", title: "Решение", value: () => "",
      render: (line) => line.is_backorder
        ? <BackorderAction line={line} disabled={disabled || busyId === line.id} onConfirm={onConfirmBackorder} />
        : <button type="button" className="primary small" disabled={disabled || busyId === line.id} onClick={() => onResume(line)}>Одобрено — продолжить</button>,
    },
  ];
  return (
    <section className="panel table-panel procurement-stage">
      <StageHeading title="Согласование" count={lines.length} hint="Ждут одобрения, заказ не формируется · дозавоз — недовоз уже оплачен" />
      <DataTable
        label="Согласование"
        rows={lines}
        columns={[...columns, menuColumn(panel.menu)]}
        rowKey={(line) => line.id}
        storageKey="procurement-approval"
        emptyText="Нет строк на согласовании"
      />
      {panel.node}
    </section>
  );
}

// ── В пути и Карантин (стадии приёмки) ──────────────────────────────────────────────────────────

const receiptColumns: DataColumn<ProcurementLine>[] = [
  idColumn,
  nameColumn,
  supplierColumn,
  { key: "order", title: "№ заказа", value: (line) => line.order_number ?? "" },
  { key: "qty", title: "Кол-во (факт)", value: qtyOf },
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

export function ReceiptStage({ title, hint, storageKey, lines, disabled, busyId, actionsOf, editing }: StageProps & {
  title: string;
  hint: string;
  storageKey: string;
}) {
  const panel = useLinePanel(lines, actionsOf, disabled, busyId);
  // «В счёте» и «Курс ЦБ» — только когда есть счёт в валюте; рублёвые строки их не заполняют (колонки вне списка, не `hidden`).
  const foreign = lines.some((line) => line.source_currency);
  const shownColumns = receiptColumns.filter((column) => foreign || (column.key !== "source" && column.key !== "rate"));
  // Сроки — рядом с количеством: ожидали → приняли; «Комментарий» последним перед меню.
  const at = shownColumns.findIndex((column) => column.key === "qty") + 1;
  const columns = [...shownColumns.slice(0, at), expectedColumn(editing, disabled), actualArrivalColumn, ...shownColumns.slice(at), noteColumn(editing, disabled)];
  return (
    <section className="panel table-panel procurement-stage">
      <StageHeading title={title} count={lines.length} hint={hint} />
      <DataTable
        label={title}
        rows={lines}
        columns={[...columns, menuColumn(panel.menu)]}
        rowKey={(line) => line.id}
        storageKey={storageKey}
      />
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
      />
      {panel.node}
    </section>
  );
}
