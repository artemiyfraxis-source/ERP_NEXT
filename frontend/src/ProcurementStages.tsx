import { useEffect, useMemo, useState, type FormEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { DataTable, readSaved, writeSaved, type DataBulkAction, type DataColumn } from "./DataTable";
import {
  failureText, type ApplyOrderDocumentInput, type ManualRatePrompt, type OrderDocumentKind, type OrderDocumentRecognition, type ProcurementLine,
} from "./api";
import { formatDate, formatDay } from "./format";
import { ExpectedDateCell, NoteCell, type LineEditing } from "./ProcurementCellEdit";
import { RowMenu, type MenuItem } from "./ProcurementRowMenu";
import { formatAmount } from "./ProcurementSheetColumns";

/* «Мои позиции» листа закупки — раздельные блоки, каждый сворачивается щелчком по заголовку (владелец, 05.10:
   «блоки раздельные и их можно сворачивать, чтобы не засран был экран»; выбор помнится в браузере):
   «В работе» (ProcurementInWorkTable, группы поставщиков к запросу) → «Жду счёт» → «Ждёт оплаты» → «Оплачено»
   (`OrderStageBlock`: заказы «PO-012 от 05.10 · поставщик · N поз. · факт … ₽ / расчёт … ₽», строки заказа
   раскрываются щелчком по заголовку) → «Дозавоз» (когда есть) → «Прочие позиции». Цену вносит окно
   «Вставить счёт» / «Вставить КП» (файл → распознанные цены). Ручного этапа «Согласование» нет (владелец, 04.10).
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

/** Свёрнут ли блок «Моих позиций»; помнится в браузере по ключу блока. */
export function useCollapsed(block: string, initially = false): [boolean, () => void] {
  const key = `procurement-block-collapsed:${block}`;
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try {
      const saved = window.localStorage.getItem(key);
      return saved === null ? initially : saved === "1";
    } catch {
      return initially;
    }
  });
  const toggle = () => setCollapsed((current) => {
    try { window.localStorage.setItem(key, current ? "0" : "1"); } catch { /* приватное окно: до перезагрузки */ }
    return !current;
  });
  return [collapsed, toggle];
}

/** Одна строка заголовка блока: название и число, подсказка — знаком «?», справа действия. С `onToggle`
 *  название — кнопка «▾ / ▸»: блок сворачивается до заголовка. */
export function StageHeading({ title, count, hint, summary, collapsed, onToggle, children }: {
  title: string; count: number; hint?: string; summary?: string; collapsed?: boolean; onToggle?: () => void; children?: ReactNode;
}) {
  return (
    <div className="panel-heading stage-head">
      {onToggle ? (
        <h2>
          <button type="button" className="stage-toggle" aria-expanded={!collapsed} onClick={onToggle}>
            <span className="caret" aria-hidden="true">{collapsed ? "▸" : "▾"}</span>{title} <span className="stage-count">{count}</span>
          </button>
        </h2>
      ) : <h2>{title} <span className="stage-count">{count}</span></h2>}
      {hint && <span className="stage-help" tabIndex={0} role="note" aria-label={hint} title={hint}>?</span>}
      {summary && <span className="stage-sum">{summary}</span>}
      <div className="heading-actions">{children}</div>
    </div>
  );
}

/** Итог блока в заголовке: «3 зак. · 12 поз. · расчёт 1 100,00 ₽ / факт 1 250,00 ₽» (заказы — только у блоков заказов). */
export function stageSummary(lines: ProcurementLine[], orders?: number): string {
  const fact = lines.some((line) => line.actual_sum) ? `${formatAmount(total(lines, (line) => line.actual_sum))} ₽` : "—";
  return [
    orders === undefined ? "" : `${orders} зак.`,
    `${lines.length} поз.`,
    `расчёт ${formatAmount(total(lines, (line) => line.calculated_sum))} ₽ / факт ${fact}`,
  ].filter(Boolean).join(" · ");
}

/** Пустой блок «Моих позиций»: заголовок с нулём и одна серая строка, без карточки и таблицы. */
export function EmptyStage({ title, hint, text }: { title: string; hint?: string; text: string }) {
  return (
    <section className="panel table-panel procurement-stage stage-block stage-block-empty" aria-label={title}>
      <StageHeading title={title} count={0} hint={hint} />
      <StageEmpty text={text} />
    </section>
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

/** Заказы одной лентой: заголовок группы (`tr.group-row`) — № и дата заказа, поставщик, итоги, статус и действия
 *  заказа; щелчок по заголовку раскрывает строки заказа. Свёрнуто по умолчанию. */
function OrderGroupTable({ label, groups, columns, expanded, onToggle, headerActions, menu }: {
  label: string;
  groups: OrderGroup[];
  columns: GroupColumn[];
  expanded: Set<string>;
  onToggle: (key: string) => void;
  headerActions: (group: OrderGroup) => ReactNode;
  menu?: (line: ProcurementLine) => ReactNode;
}) {
  const span = columns.length + (menu ? 1 : 0);
  return (
    <div className="data-table order-list" role="region" aria-label={label}>
      <div className="table-wrap">
        <table className="grid wide order-lines">
          <thead>
            <tr>
              {columns.map((column) => <th key={column.key} className={column.numeric ? "num" : undefined}>{column.title}</th>)}
              {menu && <th aria-label="Действия" />}
            </tr>
          </thead>
          {groups.map((group) => {
            const heading = groupHeading(group);
            const status = orderStatus(group);
            const open = expanded.has(group.key);
            return (
              <tbody key={group.key} aria-label={heading.title} className={open ? "order-open" : undefined}>
                <tr className="group-row order-head" onClick={() => onToggle(group.key)}>
                  <td className="group-cell" colSpan={span}>
                    <div className="group-line">
                      <button type="button" className="group-toggle" aria-expanded={open} onClick={(event) => { event.stopPropagation(); onToggle(group.key); }}>
                        <span className="caret" aria-hidden="true">{open ? "▾" : "▸"}</span>
                        <span className="group-title">{heading.title}</span>
                      </button>
                      <span className="group-meta">{heading.meta}</span>
                      <span className={`order-chip ${status.tone}`} title={status.title}>{status.label}</span>
                      <span className="group-actions" onClick={(event) => event.stopPropagation()}>{headerActions(group)}</span>
                    </div>
                  </td>
                </tr>
                {open && group.lines.map((line) => (
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

const documentItems = (group: OrderGroup): MenuItem[] => [
  { label: "Документ запроса", disabled: !group.url, title: group.url ? undefined : "Документ запроса не сформирован", run: () => { if (group.url) window.open(group.url, "_blank", "noopener"); } },
];

// ── Статус заказа: меняется на месте, заказ из ленты не уходит ─────────────────────────────────────

const AWAITING = "Жду счет";
const TO_PAY = "В оплату";
const QUARANTINE = "Карантин";
const DONE = "Готово";

export type OrderStage = "awaiting" | "topay" | "transit" | "quarantine" | "accepted";

/** Стадия заказа — по самой ранней из его строк: пока есть строка «Жду счет», заказ ждёт счёт; «Карантин» у любой
 *  строки выносит заказ в «Карантин»; «Принято» — все строки «Готово»; остальное (в том числе частично принятый) — «В пути». */
export function orderStage(group: OrderGroup): OrderStage {
  if (group.lines.some((line) => line.status === AWAITING)) return "awaiting";
  if (group.lines.some((line) => line.status === TO_PAY)) return "topay";
  if (group.lines.some((line) => line.status === QUARANTINE)) return "quarantine";
  if (group.lines.every((line) => line.status === DONE)) return "accepted";
  return "transit";
}

/** Плашка статуса в заголовке заказа: Жду счёт / Ждёт оплаты / В пути (принято N из M) / Карантин: строки / Принято. */
export function orderStatus(group: OrderGroup): { label: string; tone: string; title?: string } {
  const stage = orderStage(group);
  if (stage === "awaiting") return { label: "Жду счёт", tone: "tone-info", title: group.date ? `Запрос отправлен ${formatDay(group.date)}` : "Запрос отправлен" };
  if (stage === "topay") return { label: "Ждёт оплаты", tone: "tone-warn" };
  if (stage === "accepted") return { label: "Принято", tone: "tone-neutral" };
  const done = group.lines.filter((line) => line.status === DONE).length;
  const accepted = done ? `принято ${done} из ${group.lines.length}` : "";
  if (stage === "quarantine") {
    const ids = group.lines.filter((line) => line.status === QUARANTINE).map((line) => line.legacy_id);
    const shown = ids.length > 3 ? `${ids.slice(0, 3).join(", ")} и ещё ${ids.length - 3}` : ids.join(", ");
    return {
      label: ["Карантин: " + shown, accepted].filter(Boolean).join(" · "),
      tone: "tone-warn",
      title: `Приёмка нашла несоответствие — ждёт решения: ${ids.join(", ")}`,
    };
  }
  return { label: ["В пути", accepted].filter(Boolean).join(" · "), tone: "tone-ok" };
}
/** Заказы блока: свежие сверху. */
export function orderGroups(lines: ProcurementLine[]): OrderGroup[] {
  return groupByOrder(lines).sort((a, b) => (b.date ?? "").localeCompare(a.date ?? "")
    || b.number.localeCompare(a.number, "ru", { numeric: true }));
}

const LINE_STATUS: Record<string, string> = { [AWAITING]: "Жду счёт", [TO_PAY]: "Ждёт оплаты", "Готово": "Принято" };

/** «Статус» строки заказа и, мелко под ним, чего не хватает для счёта или оплаты. */
function lineStatus(line: ProcurementLine): ReactNode {
  const label = LINE_STATUS[line.status] ?? line.status;
  if (line.status === AWAITING) {
    const missing = missingForInvoice(line);
    return <>{label}{missing.length > 0 && <small className="tone-warn">не хватает: {missing.join(", ")}</small>}</>;
  }
  if (line.status === TO_PAY) {
    const hint = !line.invoice_url ? "нет счёта" : line.pay_missing.length ? `не хватает: ${line.pay_missing.join(", ")}` : "";
    return <>{label}{hint && <small className="tone-warn">{hint}</small>}</>;
  }
  if (line.status === "Карантин") return <span className="tone-warn" title="Приёмка нашла несоответствие — ждёт решения">Карантин</span>;
  return label;
}

/** «Сумма факт» в рублях; счёт в валюте — второй строкой: сумма в валюте и курс ЦБ. */
function sumCell(line: ProcurementLine): ReactNode {
  if (!line.source_currency) return formatAmount(line.actual_sum);
  return (
    <>
      {formatAmount(line.actual_sum)}
      <small className="source-money">
        <span>{`${line.source_sum ?? line.source_price_with_vat ?? line.source_price ?? ""} ${line.source_currency}`}</span>
        {line.exchange_rate && <> · курс {line.exchange_rate} на {formatDate(line.exchange_rate_date)}</>}
        {line.exchange_rate_manual && <span className="tone-warn"> · вручную, сверьте</span>}
      </small>
    </>
  );
}

// ── Лента заказов «Мои позиции» ───────────────────────────────────────────────────────────────────

/** `portalPurchaseToPay`: «В оплату» со счётом и без отметки об оплате — кандидаты на «Оплачено → В пути». */
export function payable(line: ProcurementLine) {
  return line.status === TO_PAY && Boolean(line.invoice_url) && !line.paid_at;
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
  const notReady = group.lines.filter((line) => line.status === TO_PAY).some((line) => !payable(line) || line.pay_missing.length > 0);

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

/** Колонки таблицы заказов: «Колонки» блока выбирает, какие показывать; выбор помнится по стадии. */
export const ORDER_COLUMNS = [
  { key: "id", title: "ID" }, { key: "name", title: "Наименование" }, { key: "qty", title: "Кол-во факт" },
  { key: "price", title: "Цена факт, ₽" }, { key: "sum", title: "Сумма факт, ₽" }, { key: "calc", title: "Расчёт, ₽" },
  { key: "invoice", title: "Счёт" }, { key: "expected", title: "Ожид. дата" }, { key: "actual_arrival", title: "Факт. дата" },
  { key: "status", title: "Статус" }, { key: "note", title: "Комментарий" },
] as const;

/** Заказов в блоке сразу; остальные — по «Показать ещё» (сотни заказов не рисуются разом). */
export const ORDERS_PAGE = 30;

const orderColumnsKey = (stage: string) => `procurement-orders-${stage}`;

/** Скрытые колонки блока заказов; выбор помнится в браузере (тот же ключ, что у «Колонок» DataTable). */
export function useOrderColumns(stage: string): [string[], (key: string, shown: boolean) => void] {
  const [hidden, setHidden] = useState<string[]>(() => readSaved(orderColumnsKey(stage)).hidden ?? []);
  const toggle = (key: string, shown: boolean) => setHidden((current) => {
    const next = shown ? current.filter((item) => item !== key) : [...current.filter((item) => item !== key), key];
    writeSaved(orderColumnsKey(stage), { ...readSaved(orderColumnsKey(stage)), hidden: next });
    return next;
  });
  return [hidden, toggle];
}

/** Документ по заказу: открытое окно «Вставить счёт» / «Вставить КП» / «Заменить счёт». */
type OpenDocument = { key: string; kind: OrderDocumentKind; replace: boolean };

export type OrderDocumentHandlers = {
  /** Файл → распознанные № и дата, цены строк заказа. */
  onRecognize: (group: OrderGroup, file: File, kind: OrderDocumentKind) => Promise<OrderDocumentRecognition>;
  /** Проверенные цены (и счёт) — на сервер; при отказе бросает Error с текстом для окна. */
  onApplyDocument: (group: OrderGroup, input: ApplyOrderDocumentInput, replace: boolean) => Promise<void>;
};

/** Заказы одного блока («Жду счёт» / «Ждёт оплаты» / «В пути» / «Карантин» / «Принято»). Заголовок заказа свёрнут; в нём действия
 *  стадии — «Вставить счёт» / «Вставить КП» (окно), «Оплачено», меню «⋯». Строки заказа только показывают цену:
 *  её вносит окно документа. */
export function OrderList({
  label = "Заказы", groups, hiddenColumns = [], disabled, busyId, actionsOf, editing, bulk, onCloseBulk, onOpenKp, onPaidOrder, onReconcile,
  onRecognize, onApplyDocument,
}: StageProps & OrderDocumentHandlers & {
  label?: string;
  groups: OrderGroup[];
  /** Скрытые колонки (ключи `ORDER_COLUMNS`). */
  hiddenColumns?: string[];
  /** «Внести КП в валюте» — прежняя форма КП по строкам (курс, НДС, суммы). */
  onOpenKp: (references: string[]) => void;
  /** «Оплачено» по заказу; вернёт вопрос про курс, если ЦБ не ответил. */
  onPaidOrder: (group: OrderGroup, manualRate?: string) => Promise<ManualRatePrompt | null>;
  /** «Сверить курс с ЦБ» для строк с курсом, введённым вручную. */
  onReconcile: (lines: ProcurementLine[]) => Promise<unknown>;
}) {
  const lines = useMemo(() => groups.flatMap((group) => group.lines), [groups]);
  const panel = useLinePanel(lines, actionsOf, disabled, busyId);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [doc, setDoc] = useState<OpenDocument | null>(null);
  const [limit, setLimit] = useState(ORDERS_PAGE);
  const docGroup = doc ? groups.find((group) => group.key === doc.key) : undefined;
  const docLines = doc && docGroup ? docGroup.lines.filter((line) => line.status === (doc.replace ? TO_PAY : AWAITING)) : [];
  // Строки заказа ушли дальше (другая вкладка, перечитанная очередь) — окно закрывается.
  useEffect(() => { if (doc && !docLines.length) setDoc(null); }, [doc, docLines.length]);

  const toggle = (key: string) => setExpanded((current) => {
    const next = new Set(current);
    if (next.has(key)) next.delete(key); else next.add(key);
    return next;
  });

  const allColumns: GroupColumn[] = [
    { key: "id", title: "ID", render: (line) => <strong>{line.legacy_id}</strong> },
    { key: "name", title: "Наименование", cell: "name-cell", render: (line) => line.calculated_name },
    { key: "qty", title: "Кол-во факт", render: qtyOf },
    { key: "price", title: "Цена факт, ₽", numeric: true, render: (line) => formatAmount(line.actual_price_with_vat) },
    { key: "sum", title: "Сумма факт, ₽", numeric: true, render: sumCell },
    { key: "calc", title: "Расчёт, ₽", numeric: true, render: (line) => formatAmount(line.calculated_sum) },
    { key: "invoice", title: "Счёт", render: (line) => invoiceColumn.render!(line) },
    { key: "expected", title: "Ожид. дата", render: (line) => <ExpectedDateCell line={line} editing={editing} disabled={disabled} /> },
    { key: "actual_arrival", title: "Факт. дата", render: (line) => formatDay(line.actual_arrival) },
    { key: "status", title: "Статус", cell: "status-cell", render: lineStatus },
    { key: "note", title: "Комментарий", cell: "note-cell", render: (line) => <NoteCell line={line} editing={editing} disabled={disabled} /> },
  ];
  const columns = allColumns.filter((column) => !hiddenColumns.includes(column.key));

  const headerActions = (group: OrderGroup) => {
    const stage = orderStage(group);
    const off = disabled || !group.number;
    const docs = documentItems(group);
    if (stage === "awaiting") {
      return (
        <>
          <button type="button" className="primary small" disabled={off} onClick={() => setDoc({ key: group.key, kind: "invoice", replace: false })}>Вставить счёт</button>
          <button type="button" className="secondary small" disabled={off} onClick={() => setDoc({ key: group.key, kind: "kp", replace: false })}>Вставить КП</button>
          <OrderMenu group={group} disabled={disabled} items={[
            ...docs,
            { label: "Внести КП в валюте…", title: "КП по строкам: валюта, НДС, суммы, курс", disabled: !group.number, run: () => onOpenKp([group.number]) },
          ]} />
        </>
      );
    }
    if (stage === "topay") {
      return (
        <>
          <PaidOrderAction group={group} disabled={off} onPaidOrder={onPaidOrder} />
          <OrderMenu group={group} disabled={disabled} items={[
            ...docs,
            { label: "Заменить счёт…", disabled: !group.number, run: () => setDoc({ key: group.key, kind: "invoice", replace: true }) },
          ]} />
        </>
      );
    }
    const manual = group.lines.filter((line) => line.exchange_rate_manual);
    return (
      <OrderMenu group={group} disabled={disabled} items={[
        ...docs,
        ...(manual.length ? [{ label: `Сверить курс с ЦБ (${manual.length})`, title: "Курс введён вручную — сверьте с ЦБ, когда он ответит", run: () => void onReconcile(manual) }] : []),
      ]} />
    );
  };

  return (
    <div className="order-block">
      <BulkStatus bulk={bulk} onClose={onCloseBulk} />
      {groups.length > 0 && (
        <OrderGroupTable
          label={label} groups={groups.slice(0, limit)} columns={columns} expanded={expanded} onToggle={toggle}
          headerActions={headerActions} menu={panel.menu}
        />
      )}
      {groups.length > limit && (
        <div className="stage-more">
          <button type="button" className="secondary small" onClick={() => setLimit((current) => current + ORDERS_PAGE)}>
            Показать ещё {Math.min(ORDERS_PAGE, groups.length - limit)} · показано {limit} из {groups.length}
          </button>
        </div>
      )}
      {panel.node}
      {doc && docGroup && docLines.length > 0 && (
        <OrderDocumentDialog
          key={`${doc.key}:${doc.kind}:${doc.replace}`}
          group={docGroup}
          lines={docLines}
          kind={doc.kind}
          replace={doc.replace}
          onRecognize={(file) => onRecognize(docGroup, file, doc.kind)}
          onApply={(input) => onApplyDocument(docGroup, input, doc.replace)}
          onClose={() => setDoc(null)}
        />
      )}
    </div>
  );
}

export type OrderBlockProps = StageProps & OrderDocumentHandlers & {
  onOpenKp: (references: string[]) => void;
  onPaidOrder: (group: OrderGroup, manualRate?: string) => Promise<ManualRatePrompt | null>;
  onReconcile: (lines: ProcurementLine[]) => Promise<unknown>;
};

/** Блок заказов одной стадии со своим заголовком (заказы · позиции · расчёт / факт); сворачивается, выбор помнится
 *  (`useCollapsed`); «Колонки» выбирает колонки таблицы. Без заказов — заголовок с нулём и одна серая строка. */
export function OrderStageBlock({ title, hint, block, stage, initiallyCollapsed = false, groups, ...props }: OrderBlockProps & {
  title: string;
  hint?: string;
  block: string;
  stage: string;
  initiallyCollapsed?: boolean;
  groups: OrderGroup[];
}) {
  const [collapsed, toggle] = useCollapsed(block, initiallyCollapsed);
  const [hidden, setColumn] = useOrderColumns(stage);
  const [choosing, setChoosing] = useState(false);
  const lines = useMemo(() => groups.flatMap((group) => group.lines), [groups]);
  if (!groups.length) return <EmptyStage title={title} hint={hint} text="Нет заказов" />;
  return (
    <section className="panel table-panel procurement-stage stage-block" aria-label={title}>
      <StageHeading title={title} count={groups.length} hint={hint} summary={stageSummary(lines, groups.length)} collapsed={collapsed} onToggle={toggle}>
        {!collapsed && <button type="button" className="secondary small" aria-expanded={choosing} onClick={() => setChoosing((current) => !current)}>Колонки</button>}
      </StageHeading>
      {!collapsed && choosing && (
        <fieldset className="column-chooser stage-columns">
          <legend>Показывать колонки</legend>
          {ORDER_COLUMNS.map((column) => (
            <label key={column.key} className="check-inline">
              <input type="checkbox" checked={!hidden.includes(column.key)} onChange={(event) => setColumn(column.key, event.target.checked)} />
              {column.title}
            </label>
          ))}
        </fieldset>
      )}
      {!collapsed && <OrderList {...props} label={`Заказы: ${title}`} groups={groups} hiddenColumns={hidden} />}
    </section>
  );
}

// ── Окно «Вставить счёт» / «Вставить КП» ─────────────────────────────────────────────────────────

const CONFIDENCE: Record<string, { text: string; tone: string }> = {
  article: { text: "по артикулу", tone: "tone-ok" },
  name: { text: "по названию", tone: "tone-ok" },
  weak: { text: "слабое — проверьте", tone: "tone-warn" },
};

const decimal = (value: string) => value.trim().replace(/\s/g, "").replace(",", ".");
const isNumber = (value: string) => decimal(value) !== "" && Number.isFinite(Number(decimal(value)));

/** Окно документа по заказу (владелец, 05.10: «вставка КП / счёта, и с него автоматом считывается цена»,
 *  «это должно быть окном, а не снизу»): файл → сервер распознаёт № и дату, цены строк и насколько уверенно
 *  нашёл каждую; цены правятся здесь же, если распознано не то. Подтверждение пишет все строки заказа или ни одной:
 *  КП — только цены, строки ждут счёт; счёт — цены и счёт, строки уходят «В оплату» (ожидаемая дата обязательна). */
function OrderDocumentDialog({ group, lines, kind, replace, onRecognize, onApply, onClose }: {
  group: OrderGroup;
  /** Строки стадии, к которым относится документ: «Жду счет», а для «Заменить счёт» — «В оплату». */
  lines: ProcurementLine[];
  kind: OrderDocumentKind;
  replace: boolean;
  onRecognize: (file: File) => Promise<OrderDocumentRecognition>;
  onApply: (input: ApplyOrderDocumentInput) => Promise<void>;
  onClose: () => void;
}) {
  const invoice = kind === "invoice";
  const title = replace ? "Заменить счёт" : invoice ? "Вставить счёт" : "Вставить КП";
  const heading = groupHeading(group);
  const first = lines[0];
  const [result, setResult] = useState<OrderDocumentRecognition | null>(null);
  const startPrices = () => Object.fromEntries(lines.map((line) => [line.id, line.actual_price_with_vat ?? ""]));
  const [prices, setPrices] = useState<Record<string, string>>(startPrices);
  const [number, setNumber] = useState(replace ? first?.invoice_number ?? "" : "");
  const [date, setDate] = useState(replace ? first?.invoice_date ?? "" : "");
  const [expected, setExpected] = useState(replace && group.expected ? group.expected : nearestWorkingDay());
  const [reading, setReading] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const busy = reading || sending;

  useEffect(() => {
    const close = (event: KeyboardEvent) => { if (event.key === "Escape" && !sending) onClose(); };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [onClose, sending]);

  const matches = new Map((result?.lines ?? []).map((match) => [match.line_id, match]));
  const unmatched = new Set(result?.unmatched_lines ?? []);
  const priceOf = (line: ProcurementLine) => prices[line.id] ?? "";
  const quantityOf = (line: ProcurementLine) => Number(line.actual_quantity ?? line.calculated_quantity);
  const sumOf = (line: ProcurementLine) => {
    const price = priceOf(line);
    return isNumber(price) && Number.isFinite(quantityOf(line)) ? Math.round(Number(decimal(price)) * quantityOf(line) * 100) / 100 : null;
  };
  const total = Math.round(lines.reduce((sum, line) => sum + (sumOf(line) ?? 0), 0) * 100) / 100;
  const documentTotal = result?.totals?.sum_with_vat;
  const totalDiffers = Boolean(documentTotal) && Math.abs(Number(documentTotal) - total) > 0.01;
  const currency = (result?.currency || "RUB").toUpperCase();
  const money = currency === "RUB" || currency === "РУБ" ? "₽" : currency;
  const badPrice = lines.filter((line) => priceOf(line).trim() !== "" && !isNumber(priceOf(line)));
  const noPrice = lines.filter((line) => priceOf(line).trim() === "");
  const blocker = !result ? `Выберите файл ${invoice ? "счёта" : "КП"}`
    : badPrice.length ? `Цена не числом: ${badPrice.map((line) => line.legacy_id).join(", ")}`
      : invoice && noPrice.length ? `Нет цены: ${noPrice.map((line) => line.legacy_id).join(", ")}`
        : invoice && !expected ? "Укажите ожидаемую дату" : "";

  async function read(file: File) {
    setReading(true);
    setError("");
    try {
      const recognized = await onRecognize(file);
      setResult(recognized);
      if (recognized.invoice_number) setNumber(recognized.invoice_number);
      if (recognized.invoice_date) setDate(recognized.invoice_date);
      setPrices((current) => {
        const next = { ...current };
        for (const match of recognized.lines) if (match.price_with_vat) next[match.line_id] = match.price_with_vat;
        return next;
      });
    } catch (reason) {
      setError(failureText(reason, "Файл не распознан"));
    } finally {
      setReading(false);
    }
  }

  /** «Убрать»: вставили не тот файл — окно возвращается к началу, можно выбрать другой. */
  function removeFile() {
    setResult(null);
    setPrices(startPrices());
    setNumber(replace ? first?.invoice_number ?? "" : "");
    setDate(replace ? first?.invoice_date ?? "" : "");
    setError("");
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!result || blocker) return;
    setSending(true);
    setError("");
    const input: ApplyOrderDocumentInput = {
      kind,
      file_url: result.file.url,
      lines: lines.map((line) => ({ line_id: line.id, version: line.version, price_with_vat: decimal(priceOf(line)) || null })),
    };
    if (invoice) {
      input.invoice_number = number.trim() || null;
      input.invoice_date = date || null;
      input.expected_arrival = expected;
    }
    try {
      await onApply(input);
      onClose();
    } catch (reason) {
      setError(failureText(reason, "Документ не принят"));
      setSending(false);
    }
  }

  return createPortal(
    <div className="dialog-overlay" onMouseDown={(event) => { if (event.target === event.currentTarget && !sending) onClose(); }}>
      <form className="panel move-dialog wide order-doc-dialog" role="dialog" aria-label={`${title} ${group.number}`} onSubmit={(event) => void submit(event)}>
        <h2>{title} · {group.number}</h2>
        <div className="move-lot"><strong>{heading.title}</strong><span>{heading.meta}</span></div>
        <div className={invoice ? "move-grid" : "move-grid two"}>
          {result ? (
            <div className="doc-file">
              <span>Файл {invoice ? "счёта" : "КП"}</span>
              <span className="doc-file-chip">
                <span className="doc-file-name" title={result.file.name}>{result.file.name}</span>
                <button type="button" className="link-button" disabled={busy} onClick={removeFile}>Убрать</button>
              </span>
            </div>
          ) : (
            <label>Файл {invoice ? "счёта" : "КП"}
              <span className={busy ? "doc-file-pick secondary disabled" : "doc-file-pick secondary"}>
                {reading ? "Читаем…" : "Выбрать файл"}
                <input type="file" aria-label={`Файл ${invoice ? "счёта" : "КП"}`} accept=".pdf,.xlsx,.xls,.csv,image/*" disabled={busy} onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ""; if (file) void read(file); }} />
              </span>
            </label>
          )}
          {invoice && <>
            <label>№ счёта<input value={number} disabled={busy} onChange={(event) => setNumber(event.target.value)} /></label>
            <label>Дата счёта<input type="date" value={date} disabled={busy} onChange={(event) => setDate(event.target.value)} /></label>
            <label>Ожидаемая дата *<input type="date" required value={expected} disabled={busy} onChange={(event) => setExpected(event.target.value)} /></label>
          </>}
        </div>
        {reading && <p className="hint doc-note">Читаем файл…</p>}
        {result && (
          <p className="hint doc-note">
            {result.file.name} · распознано: № {result.invoice_number || "—"}{result.invoice_date ? ` от ${formatDay(result.invoice_date)}` : ""}
            {" · "}найдено строк {lines.filter((line) => matches.get(line.id)?.confidence).length} из {lines.length} — проверьте цены
          </p>
        )}
        <div className="table-wrap doc-lines">
          <table className="grid wide">
            <thead>
              <tr>
                <th>Позиция</th>
                <th className="num">Кол-во факт</th>
                <th className="num">Цена с НДС, {money}</th>
                <th className="num">Сумма, {money}</th>
                <th>Совпадение</th>
              </tr>
            </thead>
            <tbody>
              {lines.map((line) => {
                const match = matches.get(line.id);
                const lost = Boolean(result) && (!match?.confidence || unmatched.has(line.legacy_id));
                const confidence = match?.confidence ? CONFIDENCE[match.confidence] : undefined;
                const warn = lost || match?.confidence === "weak";
                const otherQuantity = match?.quantity ? Number(match.quantity) !== quantityOf(line) : false;
                return (
                  <tr key={line.id} className={warn ? "doc-warn" : undefined}>
                    <td>
                      <strong>{line.legacy_id}</strong> {line.calculated_name}
                      {match?.matched_name && <small>в документе: {match.matched_name}</small>}
                    </td>
                    <td className="num">
                      {qtyOf(line)}
                      {otherQuantity && <small className="tone-warn">в документе {match?.quantity} {match?.unit ?? ""}</small>}
                      {!otherQuantity && match?.warnings?.map((text) => <small key={text} className="tone-warn">{text}</small>)}
                    </td>
                    <td className="num doc-price">
                      <input
                        aria-label={`Цена с НДС ${line.legacy_id}`} inputMode="decimal" disabled={sending} aria-invalid={badPrice.includes(line) || undefined}
                        value={priceOf(line)} onChange={(event) => setPrices((current) => ({ ...current, [line.id]: event.target.value }))}
                      />
                    </td>
                    <td className="num">{formatAmount(sumOf(line))}</td>
                    <td>{!result ? "—" : lost ? <span className="tone-warn">нет в документе</span> : <span className={confidence?.tone}>{confidence?.text}</span>}</td>
                  </tr>
                );
              })}
            </tbody>
            <tfoot>
              <tr>
                <td colSpan={3}>Итого{totalDiffers && <span className="tone-warn"> · в документе {formatAmount(documentTotal)} {money}</span>}</td>
                <td className="num"><strong>{formatAmount(total)}</strong></td>
                <td />
              </tr>
            </tfoot>
          </table>
        </div>
        {result?.hint && <div className="warning-inline" role="status">{result.hint}</div>}
        {result && result.unmatched_items.length > 0 && (
          <div className="warning-inline doc-unmatched">
            <strong>В документе, но не найдено в заказе: {result.unmatched_items.length}</strong>
            <ul>
              {result.unmatched_items.map((item, index) => (
                <li key={`${index}:${item.name}`}>
                  {item.name}{item.quantity ? ` · ${item.quantity}` : ""}{item.price_with_vat ? ` · ${formatAmount(item.price_with_vat)} ${money}` : ""}
                </li>
              ))}
            </ul>
          </div>
        )}
        {error && <div className="error-banner" role="alert">{error}</div>}
        <div className="move-actions">
          {blocker && <small className="hint doc-blocker">{blocker}</small>}
          <button type="button" className="secondary" disabled={sending} onClick={onClose}>Отмена</button>
          <button className="primary" disabled={busy || Boolean(blocker)}>
            {sending ? "Сохраняем…" : replace ? "Заменить счёт" : invoice ? `В оплату (${lines.length})` : `Внести цены (${lines.length})`}
          </button>
        </div>
      </form>
    </div>,
    document.body,
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

const BACKORDER_HINT = "Недовоз уже оплачен: подтвердите досыл с новой датой или отмените дозавоз в меню строки";

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
  const [collapsed, toggle] = useCollapsed("backorder");
  if (!lines.length) return <EmptyStage title="Дозавоз" hint={BACKORDER_HINT} text="Дозавоза нет" />;
  return (
    <section className="panel table-panel procurement-stage stage-block">
      <StageHeading title="Дозавоз" count={lines.length} hint={BACKORDER_HINT} summary={stageSummary(lines)} collapsed={collapsed} onToggle={toggle} />
      {!collapsed && <><DataTable
        label="Дозавоз"
        rows={lines}
        columns={[...columns, menuColumn(panel.menu)]}
        rowKey={(line) => line.id}
        storageKey="procurement-backorder"
        compactBelow={COMPACT_BELOW}
        emptyText="Дозавоза нет"
      />
      {panel.node}</>}
    </section>
  );
}

// ── Прочие статусы: строка не пропадает с экрана, даже если стадия новая ───────────────────────────

/** Что внутри «Прочих позиций» (подсказка в заголовке). */
export const OTHER_HINT = "Строки вне потока: новые, на согласовании";

export function OtherStage({ lines, disabled, busyId, actionsOf, editing }: StageProps) {
  const panel = useLinePanel(lines, actionsOf, disabled, busyId);
  const [collapsed, toggle] = useCollapsed("other");
  // Единственный блок, что прячется без строк: он нужен лишь тем, чей статус вне потока.
  if (!lines.length) return null;
  return (
    <section className="panel table-panel procurement-stage stage-block">
      <StageHeading title="Прочие позиции" count={lines.length} hint={OTHER_HINT} summary={stageSummary(lines)} collapsed={collapsed} onToggle={toggle} />
      {!collapsed && <><DataTable
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
      {panel.node}</>}
    </section>
  );
}
