import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ApiError, failureText, procurementApi, type ApplyOrderDocumentInput, type ProcurementLine } from "./api";
import { DataTable, readSaved, writeSaved, type DataColumn, type DataPreset } from "./DataTable";
import { formatDay, formatDayMonth } from "./format";
import { projectLabel, useInternalProjects } from "./internalProjects";
import { openSection, urlParam } from "./navigation";
import { OrderBoard, dropResult, orderSumText } from "./PurchasingOrderBoard";
import { OrderCardBody, OrderModal, PayOrderDialog, columnLabel } from "./PurchasingOrderCard";
import { NewOrderForm } from "./PurchasingNewOrder";
import { OrderDocumentDialog, groupByOrder, type OrderGroup } from "./ProcurementStages";
import { ordersApi, type Order, type OrderColumn, type OrderFollowups, type OrderList } from "./purchasingOrdersApi";
import { StatusChip } from "./StatusChip";

/* «Счета и поставки» (stage option procurement.orders; Roller_Buyer OrdersPage): заказы поставщикам доской или таблицей
   (выбор помнится в этом браузере), «Мои / Все закупщики» (чужие — только просмотр), проект, «Дожать», «Новый счёт».
   Ссылки: `?section=procurement&tab=orders&order=<№ заказа>` — карточка заказа; `&filter=<unpaid|to_pay|waiting|shipped|
   late|stocked>` — таблица с этим отбором. */

type View = "board" | "table";
type Scope = "mine" | "all";
const VIEW_KEY = "purchasing.orders.view";
const SCOPE_KEY = "purchasing.orders.scope";
const TABLE_KEY = "purchasing-orders";

function stored<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
  try {
    const value = window.localStorage.getItem(key);
    return allowed.find((item) => item === value) ?? fallback;
  } catch {
    return fallback;
  }
}
function store(key: string, value: string) {
  try { window.localStorage.setItem(key, value); } catch { /* приватное окно: выбор живёт до перезагрузки */ }
}

const inColumns = (...columns: OrderColumn[]) => (order: Order) => columns.includes(order.column);
/** Отбор таблицы (Roller_Buyer FILTERS) и два столбца ERP, которых в Roller_Buyer не было. */
export const ORDER_FILTERS: { key: string; label: string; test: (order: Order) => boolean }[] = [
  { key: "awaiting", label: "Ждём счёт", test: inColumns("awaiting") },
  { key: "unpaid", label: "Не оплачены", test: inColumns("unpaid", "to_pay") },
  { key: "to_pay", label: "В оплате", test: inColumns("to_pay") },
  { key: "waiting", label: "Ждём отгрузку", test: inColumns("waiting") },
  { key: "shipped", label: "В доставке", test: inColumns("transit") },
  { key: "late", label: "Просрочка", test: (order) => order.late_days > 0 && order.column !== "stocked" },
  { key: "receiving", label: "Приёмка", test: inColumns("receiving") },
  { key: "stocked", label: "На складе", test: inColumns("stocked") },
];
const PRESETS: DataPreset<Order>[] = ORDER_FILTERS.map(({ label, test }) => ({ label, test }));

function paymentCell(order: Order) {
  if (order.paid_count === order.lines_count && order.paid_on) return <StatusChip tone="success">Оплачен {formatDayMonth(order.paid_on)}</StatusChip>;
  if (order.column === "awaiting") return <StatusChip tone="neutral">Нет счёта</StatusChip>;
  if (order.sent_to_pay_on && order.paid_count < order.lines_count) return <StatusChip tone="info">В оплате {formatDayMonth(order.sent_to_pay_on)}</StatusChip>;
  if (order.paid_count > 0) return <StatusChip tone="warning">Оплачено {order.paid_count} из {order.lines_count}</StatusChip>;
  return <StatusChip tone="warning">Не оплачен</StatusChip>;
}

function tableColumns(all: boolean): DataColumn<Order>[] {
  return [
    { key: "number", title: "№ заказа", value: (order) => order.order_number, render: (order) => <strong>{order.order_number}</strong> },
    { key: "supplier", title: "Поставщик", grow: true, wrap: true, filter: true, value: (order) => order.supplier },
    ...(all ? [{ key: "buyer", title: "Закупщик", filter: true, value: (order: Order) => order.buyer }] : []),
    { key: "invoice", title: "Дата счёта", value: (order) => order.invoice_date ?? "", render: (order) => formatDay(order.invoice_date) || "—" },
    { key: "promised", title: "Обещано", value: (order) => order.promised ?? "", render: (order) => formatDay(order.promised) || "—" },
    {
      key: "expected", title: "Ожидается", value: (order) => order.expected ?? "",
      render: (order) => <>{formatDay(order.expected ?? order.promised) || "—"}{order.late_days > 0 && order.column !== "stocked" && <small className="tone-danger">просрочка {order.late_days} дн.</small>}</>,
    },
    { key: "payment", title: "Оплата", value: (order) => order.paid_on ?? order.sent_to_pay_on ?? "", render: paymentCell },
    { key: "lines", title: "Строк", numeric: true, value: (order) => order.lines_count },
    {
      key: "column", title: "Поставка", value: (order) => columnLabel(order), filter: true,
      render: (order) => <StatusChip tone={order.column === "stocked" ? "success" : order.column === "receiving" ? "warning" : "info"}>{columnLabel(order)}</StatusChip>,
    },
    { key: "sum", title: "Сумма, ₽", numeric: true, value: (order) => Number(order.sum ?? 0), render: orderSumText },
    { key: "projects", title: "Проекты", wrap: true, value: (order) => order.projects.map(projectLabel).join(", ") },
  ];
}

type Toast = { text: string; tone: "ok" | "error"; undo?: () => Promise<string>; action?: { label: string; run: () => void } };

function ToastBar({ toast, onClose }: { toast: Toast; onClose: () => void }) {
  const [busy, setBusy] = useState(false);
  const [undone, setUndone] = useState(false);
  const [text, setText] = useState(toast.text);
  useEffect(() => { setText(toast.text); setUndone(false); }, [toast]);
  useEffect(() => {
    const timer = window.setTimeout(onClose, toast.undo ? 12_000 : 8_000);
    return () => window.clearTimeout(timer);
  }, [toast, onClose]);
  return createPortal(
    <div className={`order-toast ${toast.tone === "error" ? "is-error" : ""}`} role={toast.tone === "error" ? "alert" : "status"}>
      <span>{text}</span>
      {toast.action && !undone && <button type="button" className="link-button" onClick={() => { toast.action!.run(); onClose(); }}>{toast.action.label}</button>}
      {toast.undo && !undone && (
        <button type="button" className="link-button" disabled={busy} onClick={async () => {
          setBusy(true);
          try {
            setText(`${await toast.undo!()} — отменено`);
            setUndone(true);
            window.setTimeout(onClose, 3_000);
          } catch (reason) {
            setText(failureText(reason, "Не отменилось"));
          } finally {
            setBusy(false);
          }
        }}>Отменить</button>
      )}
      <button type="button" className="link-button" aria-label="Скрыть" onClick={onClose}>✕</button>
    </div>,
    document.body,
  );
}

function FollowupsBlock({ data, onOpenOrder, onOpenRfq }: {
  data: OrderFollowups; onOpenOrder: (number: string) => void; onOpenRfq?: (rfqId: string) => void;
}) {
  const nothing = !data.awaiting_quotes.length && !data.late.length && !data.arriving.length && !data.unpaid.length;
  if (nothing) return null;
  const order = (item: OrderFollowups["late"][number], tail: string) => (
    <li key={`${item.order_number}`}>
      {item.order_number
        ? <button type="button" className="link-button upd-inline-link" onClick={() => onOpenOrder(item.order_number!)}>{item.order_number}</button>
        : "без № заказа"}
      {" · "}{item.supplier || "—"} · {item.count} поз.{item.names.length ? ` (${item.names.join(", ")}${item.count > item.names.length ? "…" : ""})` : ""} — {tail}
    </li>
  );
  // Свёрнуто в строку счётчиков, как в Roller_Buyer: полный список сталкивал доску за экран; с кнопками — на «Сегодня».
  return (
    <details className="panel order-followups">
      <summary>
        <b>Дожать</b>
        {data.awaiting_quotes.length > 0 && <StatusChip tone="warning">молчат по КП {data.awaiting_quotes.length}</StatusChip>}
        {data.late.length > 0 && <StatusChip tone="danger">просрочены поставки {data.late.length}</StatusChip>}
        {data.arriving.length > 0 && <StatusChip tone="info">скоро приедет {data.arriving.length}</StatusChip>}
        {data.unpaid.length > 0 && <StatusChip tone="neutral">не оплачены {data.unpaid.length}</StatusChip>}
        <span className="muted">подробнее</span>
      </summary>
      <div className="order-followups-body">
        {data.awaiting_quotes.length > 0 && (
          <>
            <div className="muted">Молчат по КП:</div>
            <ul>
              {data.awaiting_quotes.map((item) => (
                <li key={item.rfq_id}>
                  {onOpenRfq ? <button type="button" className="link-button upd-inline-link" onClick={() => onOpenRfq(item.rfq_id)}>{item.rfq_number}</button> : item.rfq_number}
                  {" — "}{item.hint.replace(`${item.rfq_number}: `, "")}{item.days !== null ? `, ${item.days} дн.` : ""} · {item.count} поз.
                </li>
              ))}
            </ul>
          </>
        )}
        {(data.late.length > 0 || data.arriving.length > 0) && (
          <>
            <div className="muted">Поставки к сроку:</div>
            <ul>
              {data.late.map((item) => order(item, `ждали ${formatDayMonth(item.date)}, просрочено на ${item.days} дн.`))}
              {data.arriving.map((item) => order(item, `приезжает ${item.days === 0 ? "сегодня" : "завтра"}`))}
            </ul>
          </>
        )}
        {data.unpaid.length > 0 && (
          <>
            <div className="muted">Счета не оплачены:</div>
            <ul>{data.unpaid.map((item) => order(item, `счёт от ${formatDayMonth(item.date)}, ${item.days ?? 0} дн.`))}</ul>
          </>
        )}
      </div>
    </details>
  );
}

const FILTER_LABEL = Object.fromEntries(ORDER_FILTERS.map((filter) => [filter.key, filter.label]));

export function PurchasingOrders({ readOnly = false, onOpenUpd, onOpenRfq }: {
  readOnly?: boolean; onOpenUpd?: (id: string) => void; onOpenRfq?: (rfqId: string) => void;
} = {}) {
  useInternalProjects(); // option projects.internal: «ВП-001 · Название»
  const [scope, setScopeState] = useState<Scope>(() => (readOnly ? "all" : stored(SCOPE_KEY, ["mine", "all"] as const, "mine")));
  const [view, setViewState] = useState<View>(() => {
    const filter = urlParam("filter");
    if (FILTER_LABEL[filter]) {
      writeSaved(TABLE_KEY, { ...readSaved(TABLE_KEY), preset: FILTER_LABEL[filter] });
      return "table";
    }
    return stored(VIEW_KEY, ["board", "table"] as const, "board");
  });
  const [tableKey, setTableKey] = useState(0);
  const [project, setProject] = useState("");
  const [list, setList] = useState<OrderList | null>(null);
  const [error, setError] = useState("");
  const [followups, setFollowups] = useState<OrderFollowups | null>(null);
  const [open, setOpen] = useState<string | null>(() => urlParam("order") || null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [toast, setToast] = useState<Toast | null>(null);
  const [invoice, setInvoice] = useState<{ group: OrderGroup; lines: ProcurementLine[] } | null>(null);
  const [pay, setPay] = useState<Order | null>(null);
  const [creating, setCreating] = useState(false);
  const live = useRef(true);

  const setView = (next: View) => { setViewState(next); store(VIEW_KEY, next); };
  const setScope = (next: Scope) => { setScopeState(next); store(SCOPE_KEY, next); };

  const reload = useCallback(async () => {
    try {
      const next = await ordersApi.list(scope);
      if (!live.current) return;
      setList(next);
      setError("");
    } catch (reason) {
      if (live.current) setError(failureText(reason, "Заказы не загрузились"));
    }
    if (!readOnly) ordersApi.followups().then((next) => { if (live.current) setFollowups(next); }).catch(() => undefined);
    setRefreshKey((current) => current + 1);
  }, [scope, readOnly]);

  useEffect(() => {
    live.current = true;
    void reload();
    return () => { live.current = false; };
  }, [reload]);

  // A link opened from inside the app (`openPlace` / `openSection`): the card or the filter follow the address bar.
  useEffect(() => {
    const follow = () => {
      if (urlParam("tab") && urlParam("tab") !== "orders") return;
      const number = urlParam("order");
      if (number) setOpen(number);
      const filter = urlParam("filter");
      if (FILTER_LABEL[filter]) {
        writeSaved(TABLE_KEY, { ...readSaved(TABLE_KEY), preset: FILTER_LABEL[filter] });
        setView("table");
        setTableKey((current) => current + 1);
      }
    };
    window.addEventListener("popstate", follow);
    return () => window.removeEventListener("popstate", follow);
  }, []);

  const say = (text: string, tone: Toast["tone"] = "ok", extra: Partial<Toast> = {}) => setToast({ text, tone, ...extra });
  const hideToast = useCallback(() => setToast(null), []);
  const today = list?.today ?? new Date().toISOString().slice(0, 10);
  const orders = useMemo(() => list?.orders ?? [], [list]);
  const projects = useMemo(() => [...new Set(orders.flatMap((order) => order.projects))].sort(), [orders]);
  const shown = project ? orders.filter((order) => order.projects.includes(project)) : orders;

  async function openInvoice(order: Order) {
    try {
      const { lines } = await procurementApi.kpOrder(order.order_number);
      const [group] = groupByOrder(lines);
      if (!group) throw new Error(`У заказа ${order.order_number} нет строк «Жду счёт»`);
      setInvoice({ group, lines });
    } catch (reason) {
      say(failureText(reason, "Окно счёта не открылось"), "error");
      void reload();
    }
  }

  async function applyInvoice(group: OrderGroup, input: ApplyOrderDocumentInput) {
    try {
      const result = await procurementApi.applyOrderDocument(group.number, input);
      const warnings = result.warnings ?? [];
      say(`${group.number}: счёт внесён, строк «В оплату»: ${input.lines.length}${warnings.length ? ` · ${warnings.join("; ")}` : ""}`);
      void reload();
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 409) {
        void reload();
        throw new Error("Строки заказа изменились — доска обновлена. Закройте окно и вставьте счёт ещё раз");
      }
      throw reason;
    }
  }

  async function writeFact(order: Order, field: "sent_to_pay_on" | "shipped_on") {
    const before = order[field];
    const done = field === "sent_to_pay_on" ? "передан в оплату" : "отгружен";
    try {
      const saved = await ordersApi.saveFacts(order.order_number, order.facts_version, { [field]: today });
      void reload();
      say(`${order.order_number}: ${done} сегодня`, "ok", {
        undo: async () => {
          await ordersApi.saveFacts(order.order_number, saved.order.facts_version, { [field]: before });
          void reload();
          return `${order.order_number} ${done}`;
        },
        action: field === "shipped_on" ? { label: "Трек-номер…", run: () => setOpen(order.order_number) } : undefined,
      });
    } catch (reason) {
      say(failureText(reason, "Не сохранилось"), "error");
      if (reason instanceof ApiError && reason.status === 409) void reload();
    }
  }

  function drop(order: Order, to: OrderColumn) {
    const action = dropResult(order, to);
    if (!action) return;
    if (action.kind === "refuse") {
      say(action.text, "error", action.receiving ? { action: { label: "Открыть «Приёмку»", run: () => openSection("receiving") } } : {});
      return;
    }
    if (action.kind === "invoice") void openInvoice(order);
    else if (action.kind === "pay") setPay(order);
    else void writeFact(order, action.kind === "sent_to_pay" ? "sent_to_pay_on" : "shipped_on");
  }

  const card = (number: string) => (
    <OrderCardBody
      number={number} today={today} readOnly={readOnly} refreshKey={refreshKey} onOpenUpd={onOpenUpd}
      onChanged={() => void reload()} onInvoice={(order) => void openInvoice(order)} onPay={setPay}
    />
  );

  return (
    <section className="page purchasing-orders">
      <div className="orders-toolbar">
        <div className="seg-tabs" role="group" aria-label="Вид">
          <button type="button" className={view === "board" ? "on" : undefined} aria-pressed={view === "board"} onClick={() => setView("board")}>Доска</button>
          <button type="button" className={view === "table" ? "on" : undefined} aria-pressed={view === "table"} onClick={() => setView("table")}>Таблица</button>
        </div>
        {!readOnly && (
          <div className="seg-tabs" role="group" aria-label="Чьи заказы">
            <button type="button" className={scope === "mine" ? "on" : undefined} aria-pressed={scope === "mine"} onClick={() => setScope("mine")}>Мои</button>
            <button type="button" className={scope === "all" ? "on" : undefined} aria-pressed={scope === "all"} onClick={() => setScope("all")}>Все закупщики</button>
          </div>
        )}
        <label className="doc-inline-label">Проект
          <select aria-label="Проект" value={project} onChange={(event) => setProject(event.target.value)}>
            <option value="">Все проекты</option>
            {projects.map((item) => <option key={item} value={item}>{projectLabel(item)}</option>)}
          </select>
        </label>
        <span className="upd-spacer" />
        {view === "board" && orders.length > 0 && (
          <span className="muted orders-hint" title="Перетаскиваются только свои заказы и только вперёд; оплату не отменить, остальное — «Отменить» в подсказке внизу">
            {readOnly ? "Только просмотр — щелчок открывает заказ." : "Перетащите карточку вперёд — шаг запишется сегодня. Щелчок открывает заказ."}
          </span>
        )}
        {!readOnly && <button type="button" className={creating ? "secondary" : "primary"} onClick={() => setCreating((current) => !current)}>{creating ? "Свернуть" : "+ Новый счёт"}</button>}
      </div>

      {followups && !readOnly && <FollowupsBlock data={followups} onOpenOrder={setOpen} onOpenRfq={onOpenRfq} />}

      {creating && (
        <NewOrderForm
          onCancel={() => setCreating(false)}
          onDone={(numbers, notes) => {
            setCreating(false);
            void reload();
            say(`Оформлен заказ ${numbers.join(", ")}${notes.length ? ` · ${notes.join("; ")}` : ""}`, "ok", {
              action: { label: "Открыть", run: () => setOpen(numbers[0]) },
            });
          }}
        />
      )}

      {error && <div className="error-banner" role="alert">{error}</div>}
      {!list && !error && <div className="empty">Загружаю…</div>}
      {list && orders.length === 0 && (
        <div className="empty">{scope === "mine" ? "Ваших заказов поставщикам ещё нет: их создаёт запрос поставщику или «+ Новый счёт»." : "Заказов поставщикам ещё нет."}</div>
      )}

      {list && orders.length > 0 && view === "board" && (
        <OrderBoard orders={shown} today={today} onOpen={setOpen} onDrop={drop} />
      )}
      {list && orders.length > 0 && view === "table" && (
        <DataTable
          key={tableKey}
          rows={shown}
          columns={tableColumns(scope === "all")}
          rowKey={(order) => order.order_number}
          storageKey={TABLE_KEY}
          label="Заказы поставщикам"
          presets={PRESETS}
          presetCountsFollowSearch
          rowClassName={(order) => (order.late_days > 0 && order.column !== "stocked" ? "order-row-late" : "")}
          renderDetail={(order) => card(order.order_number)}
          exportName="Счета и поставки"
          emptyText="Нет заказов с таким отбором."
        />
      )}

      {open && (
        <OrderModal number={open} onClose={() => setOpen(null)}>{card(open)}</OrderModal>
      )}
      {invoice && (
        <OrderDocumentDialog
          group={invoice.group}
          lines={invoice.lines}
          kind="invoice"
          replace={false}
          onRecognize={(file) => procurementApi.recognizeOrderDocument(invoice.group.number, file, "invoice")}
          onApply={(input) => applyInvoice(invoice.group, input)}
          onClose={() => setInvoice(null)}
        />
      )}
      {pay && (
        <PayOrderDialog
          order={pay}
          onClose={() => setPay(null)}
          onOpenCard={() => { setOpen(pay.order_number); setPay(null); }}
          onPaid={(count) => {
            say(`${pay.order_number}: оплачено, строк в пути: ${count}`);
            setPay(null);
            void reload();
          }}
          onConflict={(message) => {
            setPay(null);
            say(message, "error");
            void reload();
          }}
        />
      )}
      {toast && <ToastBar toast={toast} onClose={hideToast} />}
    </section>
  );
}
