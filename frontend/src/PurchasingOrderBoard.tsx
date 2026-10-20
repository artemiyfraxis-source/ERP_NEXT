import { useState } from "react";
import { formatDayMonth } from "./format";
import { projectLabel } from "./internalProjects";
import { formatAmount } from "./ProcurementSheetColumns";
import type { Order, OrderColumn } from "./purchasingOrdersApi";

/* «Счета и поставки» доской (Roller_Buyer OrderBoard): карточка — заказ поставщику ERP (строки одного № заказа). Колонка —
   по самой ранней стадии строк заказа и фактам заказа (сервер, `purchasing/orders.py`); что колонка прячет (часть уже
   оплачена или пришла, карантин, просрочка, дозавоз) — чипы на карточке. Перетаскивание — только вперёд и только своих
   заказов: «Не оплачен» — окно «Вставить счёт», «В оплате» и «В доставке» — факт заказа сегодняшним днём с «Отменить»,
   «Ждём отгрузку» — оплата (подтверждение, отменить нельзя); «Приёмка» и «На складе» — только приёмкой. */

export const ORDER_COLUMNS: { key: OrderColumn; label: string; hint: string }[] = [
  { key: "awaiting", label: "Ждём счёт", hint: "запрос отправлен — вставьте счёт" },
  { key: "unpaid", label: "Не оплачен", hint: "передать в оплату" },
  { key: "to_pay", label: "В оплате", hint: "ждём оплату" },
  { key: "waiting", label: "Ждём отгрузку", hint: "оплачен, поставщик ещё не отгрузил" },
  { key: "transit", label: "В доставке", hint: "отгружен или пришла часть" },
  { key: "receiving", label: "Приёмка", hint: "приёмка нашла несоответствие — ждёт решения" },
  { key: "stocked", label: "На складе", hint: "всё пришло" },
];

const index = (column: OrderColumn) => ORDER_COLUMNS.findIndex((item) => item.key === column);

export type DropAction =
  | { kind: "invoice" }
  | { kind: "sent_to_pay" }
  | { kind: "pay" }
  | { kind: "shipped" }
  | { kind: "refuse"; text: string; receiving?: boolean }
  | null;

export const REFUSE_FOREIGN = "Это заказ другого закупщика — только просмотр";
export const REFUSE_RECEIVING = "На склад заказ попадает приёмкой: товар принимают и проверяют в «Приёмке»";
export const REFUSE_BACK = "Назад карточка не переносится: оплату не отменить, а дату передачи в оплату или отгрузки исправьте в карточке заказа";
export const REFUSE_NO_INVOICE = "Сначала счёт: перетащите заказ в «Не оплачен» — откроется «Вставить счёт»";
export const REFUSE_NOT_PAID = "Заказ ещё не оплачен: сначала «Ждём отгрузку» — оплата";

/** Что делает перенос карточки из её колонки в `to` (Roller_Buyer `dropResult`, переложенный на стадии ERP). */
export function dropResult(order: Pick<Order, "column" | "mine">, to: OrderColumn): DropAction {
  const from = order.column;
  if (from === to) return null;
  if (!order.mine) return { kind: "refuse", text: REFUSE_FOREIGN };
  if (to === "receiving" || to === "stocked") return { kind: "refuse", text: REFUSE_RECEIVING, receiving: true };
  if (index(to) < index(from)) return { kind: "refuse", text: REFUSE_BACK };
  if (from === "awaiting") return to === "unpaid" ? { kind: "invoice" } : { kind: "refuse", text: REFUSE_NO_INVOICE };
  if (to === "to_pay") return { kind: "sent_to_pay" };
  if (to === "waiting") return { kind: "pay" };
  return from === "waiting" ? { kind: "shipped" } : { kind: "refuse", text: REFUSE_NOT_PAID };
}

/** Заказы по колонкам доски. */
export function boardColumns(orders: Order[]): Map<OrderColumn, Order[]> {
  const columns = new Map<OrderColumn, Order[]>(ORDER_COLUMNS.map((column) => [column.key, []]));
  for (const order of orders) columns.get(order.column)?.push(order);
  return columns;
}

const invoiceDay = (order: Order) => order.invoice_date ?? order.order_date ?? "";
const expectedDay = (order: Order) => order.expected ?? order.promised ?? "";

/** Колонки оплаты — старый счёт первым; поставка — ближайшая дата первой; склад — последний пришедший первым. */
export function sortColumn(column: OrderColumn, list: Order[]): Order[] {
  const by = (key: (order: Order) => string, desc = false) =>
    [...list].sort((a, b) => (desc ? -1 : 1) * key(a).localeCompare(key(b)) || a.order_number.localeCompare(b.order_number, "ru", { numeric: true }));
  if (column === "awaiting") return by((order) => order.order_date ?? "");
  if (column === "unpaid" || column === "to_pay") return by(invoiceDay);
  if (column === "stocked") return by((order) => order.stocked_on ?? "", true);
  return by(expectedDay);
}

/** «На складе» растёт без конца — на доске последние, остальные по кнопке; в остальных колонках (все закупщики — сотни
 *  заказов) — первые 40, остальные тоже по кнопке. */
export const STOCKED_SHOWN = 8;
export const COLUMN_SHOWN = 40;

const daysBetween = (from: string | null, to: string) => {
  if (!from) return null;
  const a = Date.parse(`${from.slice(0, 10)}T00:00:00Z`);
  const b = Date.parse(`${to}T00:00:00Z`);
  return Number.isFinite(a) && Number.isFinite(b) ? Math.round((b - a) / 86_400_000) : null;
};

/** Строка под поставщиком: с какого дня заказ стоит в колонке. */
export function cardWhen(order: Order, today: string): string {
  if (order.column === "awaiting") {
    const days = daysBetween(order.order_date, today);
    return `запрос от ${formatDayMonth(order.order_date)}${days !== null ? ` · ${days} дн.` : ""}`;
  }
  if (order.column === "unpaid") {
    const day = invoiceDay(order) || null;
    const days = daysBetween(day, today);
    return `счёт от ${formatDayMonth(day)}${days !== null ? ` · ${days} дн.` : ""}`;
  }
  if (order.column === "to_pay") return `в оплате с ${formatDayMonth(order.sent_to_pay_on)}`;
  if (order.column === "stocked") return order.stocked_on ? `на складе с ${formatDayMonth(order.stocked_on)}` : "на складе";
  const shipped = order.shipped_on ? `отгружен ${formatDayMonth(order.shipped_on)} · ` : "";
  const expected = expectedDay(order);
  return `${shipped}${expected ? `ожидается ${formatDayMonth(expected)}` : "дата не указана"}`;
}

/** Чипы того, что колонка прячет: часть строк уже дальше, карантин, дозавоз, чужой заказ. */
export function cardChips(order: Order): { text: string; tone: string; title?: string }[] {
  const chips: { text: string; tone: string; title?: string }[] = [];
  const of = (count: number) => `${count} из ${order.lines_count}`;
  if (order.column === "awaiting" && order.invoiced_count > 0) chips.push({ text: `со счётом ${of(order.invoiced_count)}`, tone: "info" });
  if ((order.column === "unpaid" || order.column === "to_pay") && order.paid_count > 0) {
    chips.push({ text: `оплачено ${of(order.paid_count)}`, tone: "info" });
  }
  if ((order.column === "transit" || order.column === "receiving") && order.received_count > 0) {
    chips.push({ text: `пришло ${of(order.received_count)}`, tone: "info" });
  }
  if (order.column !== "receiving" && order.quarantine_count > 0) {
    chips.push({ text: `карантин ${of(order.quarantine_count)}`, tone: "warning", title: "Приёмка нашла несоответствие — ждёт решения" });
  }
  if (order.backorders.length) {
    chips.push({ text: `дозавоз ${order.backorders.length}`, tone: "warning", title: order.backorders.map((line) => `${line.legacy_id} · ${line.status}`).join("\n") });
  }
  if (!order.mine) chips.push({ text: order.buyer || "другой закупщик", tone: "neutral", title: "Заказ другого закупщика — только просмотр" });
  return chips;
}

export function orderSumText(order: Pick<Order, "sum" | "sum_estimated" | "delivery_cost">): string {
  const total = Number(order.sum ?? 0) + Number(order.delivery_cost ?? 0);
  const text = `${formatAmount(total)} ₽`;
  return order.sum_estimated ? `≈ ${text}` : text;
}

export function OrderBoard({ orders, today, onOpen, onDrop }: {
  orders: Order[];
  today: string;
  onOpen: (number: string) => void;
  onDrop: (order: Order, to: OrderColumn) => void;
}) {
  const [dragged, setDragged] = useState<Order | null>(null);
  const [over, setOver] = useState<OrderColumn | null>(null);
  const [expanded, setExpanded] = useState<Set<OrderColumn>>(new Set());
  const columns = boardColumns(orders);

  function drop(to: OrderColumn) {
    const order = dragged;
    setDragged(null);
    setOver(null);
    if (order) onDrop(order, to);
  }

  return (
    <div className="order-board" role="list" aria-label="Доска заказов">
      {ORDER_COLUMNS.map((column) => {
        const list = sortColumn(column.key, columns.get(column.key) ?? []);
        const limit = column.key === "stocked" ? STOCKED_SHOWN : COLUMN_SHOWN;
        const shown = expanded.has(column.key) ? list : list.slice(0, limit);
        const total = list.reduce((sum, order) => sum + Number(order.sum ?? 0) + Number(order.delivery_cost ?? 0), 0);
        return (
          <section
            key={column.key}
            role="listitem"
            aria-label={column.label}
            className={`order-column${dragged && over === column.key ? " is-drop" : ""}`}
            onDragOver={(event) => {
              if (!dragged) return;
              event.preventDefault();
              if (over !== column.key) setOver(column.key);
            }}
            onDragLeave={(event) => {
              if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setOver(null);
            }}
            onDrop={(event) => {
              event.preventDefault();
              drop(column.key);
            }}
          >
            <header className="order-column-head" title={column.hint}>
              <b>{column.label}</b>
              <span className="order-column-count">{list.length}</span>
              {list.length > 0 && <span className="order-column-sum">{formatAmount(total)} ₽</span>}
            </header>
            {list.length === 0 && <div className="order-column-empty">{column.hint}</div>}
            {shown.map((order) => (
              <OrderBoardCard
                key={order.order_number}
                order={order}
                today={today}
                dragging={dragged?.order_number === order.order_number}
                onOpen={() => onOpen(order.order_number)}
                onDragStart={() => setDragged(order)}
                onDragEnd={() => { setDragged(null); setOver(null); }}
              />
            ))}
            {list.length > shown.length && (
              <button type="button" className="secondary small order-column-more" onClick={() => setExpanded((current) => new Set(current).add(column.key))}>Показать все {list.length}</button>
            )}
          </section>
        );
      })}
    </div>
  );
}

function OrderBoardCard({ order, today, dragging, onOpen, onDragStart, onDragEnd }: {
  order: Order;
  today: string;
  dragging: boolean;
  onOpen: () => void;
  onDragStart: () => void;
  onDragEnd: () => void;
}) {
  const projects = order.projects.map(projectLabel);
  const late = order.late_days > 0 && order.column !== "stocked";
  return (
    <div
      className={`order-card${late ? " is-late" : ""}${dragging ? " is-dragging" : ""}${order.mine ? "" : " is-foreign"}`}
      draggable={order.mine}
      onDragStart={(event) => {
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/plain", order.order_number);
        onDragStart();
      }}
      onDragEnd={onDragEnd}
    >
      <button type="button" className="order-card-summary" onClick={onOpen} aria-label={`Заказ ${order.order_number} · ${order.supplier}`}>
        <span className="order-card-top">
          <span className="order-card-number">{order.order_number}</span>
          <span className="order-card-sum">{orderSumText(order)}</span>
        </span>
        <span className="order-card-supplier">{order.supplier || "поставщик не указан"}</span>
        <span className="order-card-meta">{cardWhen(order, today)}</span>
        {late && (
          <span className="order-card-late">просрочка {order.late_days} дн.{order.promised ? ` · обещали ${formatDayMonth(order.promised)}` : ""}</span>
        )}
        {order.column === "transit" && order.shipment_note && <span className="order-card-meta">{order.shipment_note}</span>}
        <span className="order-card-chips">
          {cardChips(order).map((chip) => (
            <span key={chip.text} className="status-chip" data-tone={chip.tone} title={chip.title}>{chip.text}</span>
          ))}
          <span className="order-card-meta">
            {order.lines_count} стр.{projects.length ? ` · ${projects.slice(0, 2).join(", ")}${projects.length > 2 ? ` и ещё ${projects.length - 2}` : ""}` : ""}
          </span>
        </span>
      </button>
    </div>
  );
}
