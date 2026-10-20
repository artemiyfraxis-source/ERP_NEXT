import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { ApiError, failureText, manualRatePrompt, procurementApi, type ManualRatePrompt } from "./api";
import { formatDay, formatDayMonth, formatQty } from "./format";
import { projectLabel } from "./internalProjects";
import { openSection } from "./navigation";
import { ORDER_COLUMNS, orderSumText } from "./PurchasingOrderBoard";
import { formatAmount } from "./ProcurementSheetColumns";
import { ordersApi, type Order, type OrderCard, type OrderFactsPatch, type OrderLine } from "./purchasingOrdersApi";
import { StatusChip } from "./StatusChip";

/* Карточка заказа «Счета и поставки» (Roller_Buyer OrderDetail): обещано (только чтение — по нему честный «в срок»),
   ожидается (для всего заказа сразу), передан в оплату, оплачен (дата оплаты ERP), отгрузка, трек, доставка, заметка;
   кнопки шага; строки заказа; приёмка — только чтение (поступления, брак, недопоставку и ГРН ведёт «Приёмка»); УПД. */

const AWAITING = "Жду счет";
const TO_PAY = "В оплату";
const EXPECTED_EDITABLE = new Set([AWAITING, TO_PAY, "В пути", "Карантин"]);
const LINE_STAGE: Record<string, string> = { [AWAITING]: "Жду счёт", [TO_PAY]: "Ждёт оплаты", "Готово": "Принято" };

export const columnLabel = (order: Pick<Order, "column">) => ORDER_COLUMNS.find((column) => column.key === order.column)?.label ?? "";

/** Строки, чья ожидаемая дата меняется сейчас (дозавоз — со своей датой). */
export const expectedLines = (order: Order) =>
  (order.lines ?? []).filter((line) => EXPECTED_EDITABLE.has(line.status)).map((line) => ({ line_id: line.id, version: line.version }));

const lineSum = (line: OrderLine) => line.actual_sum ?? line.calculated_sum;

function money(value: string | null | undefined, estimated = false) {
  if (value === null || value === undefined || value === "") return "—";
  return `${estimated ? "≈ " : ""}${formatAmount(value)} ₽`;
}

/** Окно «Оплачено»: строки «В оплату» и сумма; оплату не отменить — строки уйдут «В пути», их начнёт ждать приёмка. */
export function PayOrderDialog({ order, onClose, onPaid, onConflict, onOpenCard }: {
  order: Order;
  onClose: () => void;
  onPaid: (count: number) => void;
  onConflict: (message: string) => void;
  onOpenCard?: () => void;
}) {
  const lines = (order.lines ?? []).filter((line) => line.status === TO_PAY);
  const missing = lines.filter((line) => line.pay_missing.length > 0);
  const [prompt, setPrompt] = useState<ManualRatePrompt | null>(null);
  const [rate, setRate] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const total = lines.reduce((sum, line) => sum + Number(lineSum(line) ?? 0), 0);
  const estimated = lines.some((line) => line.actual_sum === null);

  useEffect(() => {
    const close = (event: KeyboardEvent) => { if (event.key === "Escape" && !busy) onClose(); };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [busy, onClose]);

  async function pay(event: FormEvent) {
    event.preventDefault();
    if (busy || missing.length || !lines.length) return;
    setBusy(true);
    setError("");
    try {
      await procurementApi.orderPaid(order.order_number, lines.map((line) => ({ line_id: line.id, version: line.version })), prompt ? rate.trim() : undefined);
      onPaid(lines.length);
    } catch (reason) {
      const asked = manualRatePrompt(reason);
      if (asked) setPrompt(asked);
      else if (reason instanceof ApiError && reason.status === 409) onConflict("Строки заказа изменились — доска обновлена. Проверьте заказ и подтвердите оплату ещё раз");
      else setError(failureText(reason, "Оплата не проведена"));
    } finally {
      setBusy(false);
    }
  }

  return createPortal(
    <div className="dialog-overlay" onMouseDown={(event) => { if (event.target === event.currentTarget && !busy) onClose(); }}>
      <form className="panel move-dialog wide" role="dialog" aria-label={`Оплата ${order.order_number}`} onSubmit={(event) => void pay(event)}>
        <h2>Оплачено · {order.order_number}</h2>
        <div className="move-lot"><strong>{order.supplier}</strong><span>{lines.length} стр. · {money(String(total), estimated)}</span></div>
        <div className="table-wrap">
          <table className="grid" aria-label="Строки к оплате">
            <thead>
              <tr><th>Позиция</th><th className="num">Кол-во</th><th className="num">Цена с НДС, ₽</th><th className="num">Сумма, ₽</th></tr>
            </thead>
            <tbody>
              {lines.map((line) => (
                <tr key={line.id}>
                  <td className="text"><strong>{line.legacy_id}</strong> {line.name}
                    {line.pay_missing.length > 0 && <small className="tone-warn">не хватает: {line.pay_missing.join(", ")}</small>}
                  </td>
                  <td className="num">{formatQty(line.quantity, line.unit)}</td>
                  <td className="num">{formatAmount(line.price_with_vat) || "—"}</td>
                  <td className="num">{formatAmount(lineSum(line)) || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {missing.length > 0 ? (
          <div className="warning-inline" role="alert">
            Оплата закрыта: у {missing.length === 1 ? "строки" : "строк"} {missing.map((line) => line.legacy_id).join(", ")} не заполнено нужное для оплаты.
            Цену вносит «Заменить счёт» в «Очередях»; остальное — в строке.
          </div>
        ) : (
          <p className="warning-inline">Оплату нельзя отменить: строки уйдут «В пути», их начнёт ждать приёмка. Дата оплаты — сегодня.</p>
        )}
        {prompt && (
          <label className="inline-rate">{prompt.message}. Курс, ₽ за 1 {prompt.currency} *
            <input inputMode="decimal" required value={rate} onChange={(event) => setRate(event.target.value)} />
          </label>
        )}
        {error && <div className="error-banner" role="alert">{error}</div>}
        <div className="move-actions">
          {missing.length > 0 && onOpenCard && <button type="button" className="link-button" onClick={onOpenCard}>Открыть заказ</button>}
          <button type="button" className="secondary" disabled={busy} onClick={onClose}>Отмена</button>
          <button className="primary" disabled={busy || missing.length > 0 || !lines.length || (Boolean(prompt) && !rate.trim())}>
            {busy ? "Проводим…" : `Оплачено (${lines.length})`}
          </button>
        </div>
      </form>
    </div>,
    document.body,
  );
}

type FactField = keyof OrderFactsPatch;

/** Содержимое карточки заказа: в окне над доской и под строкой таблицы. */
export function OrderCardBody({ number, today, readOnly, refreshKey = 0, onChanged, onPay, onInvoice, onOpenUpd }: {
  number: string;
  today: string;
  readOnly: boolean;
  refreshKey?: number;
  onChanged: () => void;
  onPay: (order: Order) => void;
  onInvoice: (order: Order) => void;
  onOpenUpd?: (id: string) => void;
}) {
  const [card, setCard] = useState<OrderCard | null>(null);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [draft, setDraft] = useState<Record<string, string>>({});

  const load = useCallback(async () => {
    try {
      const next = await ordersApi.card(number);
      setCard(next);
      setDraft({});
      setError("");
    } catch (reason) {
      setError(failureText(reason, "Заказ не загрузился"));
    }
  }, [number]);
  useEffect(() => { void load(); }, [load, refreshKey]);

  if (!card) return error ? <div className="error-banner" role="alert">{error}</div> : <div className="empty">Загружаю…</div>;
  const order = card.order;
  const editable = !readOnly && order.mine;
  const value = (field: string, saved: string | null) => draft[field] ?? saved ?? "";
  const set = (field: string, next: string) => setDraft((current) => ({ ...current, [field]: next }));

  async function saveFacts(patch: OrderFactsPatch) {
    setSaving(true);
    setError("");
    try {
      await ordersApi.saveFacts(order.order_number, order.facts_version, patch);
      onChanged();
      await load();
    } catch (reason) {
      setError(failureText(reason, "Не сохранилось"));
      if (reason instanceof ApiError && reason.status === 409) await load();
    } finally {
      setSaving(false);
    }
  }

  function blurFact(field: FactField, saved: string | null, kind: "date" | "text" | "money") {
    const raw = draft[field];
    if (raw === undefined || raw === (saved ?? "")) return;
    const text = raw.trim();
    const next = kind === "text" ? text : text ? (kind === "money" ? text.replace(/\s/g, "").replace(",", ".") : text) : null;
    void saveFacts({ [field]: next } as OrderFactsPatch);
  }

  async function saveExpected() {
    const raw = draft.expected;
    if (raw === undefined || !raw || raw === (order.expected ?? "")) return;
    setSaving(true);
    setError("");
    try {
      await ordersApi.setExpected(order.order_number, raw, expectedLines(order));
      onChanged();
      await load();
    } catch (reason) {
      setError(failureText(reason, "Ожидаемая дата не сохранилась"));
      if (reason instanceof ApiError && reason.status === 409) await load();
    } finally {
      setSaving(false);
    }
  }

  const invoices = order.invoices.filter((invoice) => invoice.url || invoice.number);
  const canPay = editable && order.stages[TO_PAY] > 0;
  const canShip = editable && !order.shipped_on && order.invoiced_count > 0 && order.column !== "stocked";
  const differs = order.expected && order.expected_last && order.expected !== order.expected_last;
  const receiving = card.receiving.filter((row) => !row.archived);
  const archived = card.receiving.length - receiving.length;

  return (
    <div className="order-card-body">
      <div className="order-card-facts">
        <dl className="order-facts">
          <div><dt>Поставщик</dt><dd>{order.supplier || "—"}</dd></div>
          <div><dt>Заказ</dt><dd>{order.order_number}{order.order_date ? ` от ${formatDay(order.order_date)}` : ""}
            {" · "}<a href={`/api/procurement/supplier-requests/${encodeURIComponent(order.order_number)}/document`} target="_blank" rel="noreferrer">запрос поставщику</a>
          </dd></div>
          <div><dt>{invoices.length > 1 ? "Счета" : "Счёт"}</dt><dd>
            {invoices.length === 0 ? <span className="muted">ещё нет</span> : invoices.map((invoice, index) => (
              <span key={`${invoice.url}|${invoice.number}`}>
                {index > 0 && ", "}
                {invoice.url
                  ? <a href={invoice.url} target="_blank" rel="noreferrer">{invoice.number ? `№ ${invoice.number}` : "счёт"}</a>
                  : `№ ${invoice.number}`}
                {invoice.date ? ` от ${formatDay(invoice.date)}` : ""}
              </span>
            ))}
          </dd></div>
          <div><dt>Сумма</dt><dd>{orderSumText(order)}{order.delivery_cost ? <span className="muted"> · в т. ч. доставка {money(order.delivery_cost)}</span> : null}
            {order.foreign.map((item) => <span key={item.currency} className="muted"> · {formatAmount(item.sum)} {item.currency}</span>)}
            {order.sum_estimated && <small className="muted">≈ — у части строк нет цены факт, взята расчётная</small>}
          </dd></div>
          <div><dt>Закупщик</dt><dd>{order.buyer || "—"}</dd></div>
          <div><dt>Колонка</dt><dd><StatusChip tone={order.column === "stocked" ? "success" : order.column === "receiving" ? "warning" : "info"}>{columnLabel(order)}</StatusChip>
            {order.late_days > 0 && order.column !== "stocked" && <span className="tone-danger"> · просрочка {order.late_days} дн.</span>}
          </dd></div>
        </dl>
        <div className="order-fact-form">
          <div className="order-fact-readonly" title="Обещанная дата фиксирована для честности KPI: дата из счёта, переносы срока её не меняют — по ней считается «в срок»">
            <span>Обещано · не меняется</span>
            <strong>{order.promised ? formatDay(order.promised) : "—"}</strong>
          </div>
          <label>Ожидается{differs ? <small className="muted"> у строк {formatDayMonth(order.expected)}–{formatDayMonth(order.expected_last)}</small> : null}
            <input type="date" aria-label="Ожидаемая дата" disabled={!editable || saving || expectedLines(order).length === 0}
              value={value("expected", order.expected)} onChange={(event) => set("expected", event.target.value)} onBlur={() => void saveExpected()} />
          </label>
          <label>Передан в оплату
            <input type="date" aria-label="Передан в оплату" max={today} disabled={!editable || saving || order.invoiced_count === 0}
              value={value("sent_to_pay_on", order.sent_to_pay_on)} onChange={(event) => set("sent_to_pay_on", event.target.value)}
              onBlur={() => blurFact("sent_to_pay_on", order.sent_to_pay_on, "date")} />
          </label>
          <div className="order-fact-readonly" title="Оплату проводит «Оплачено» — день оплаты ставит ERP">
            <span>Оплачен</span>
            <strong>{order.paid_on ? `${formatDay(order.paid_on)}${order.paid_count < order.lines_count ? ` · ${order.paid_count} из ${order.lines_count}` : ""}` : "—"}</strong>
          </div>
          <label>Поставщик отгрузил
            <input type="date" aria-label="Поставщик отгрузил" max={today} disabled={!editable || saving || order.invoiced_count === 0}
              value={value("shipped_on", order.shipped_on)} onChange={(event) => set("shipped_on", event.target.value)}
              onBlur={() => blurFact("shipped_on", order.shipped_on, "date")} />
          </label>
          <label>Доставка, ₽ с НДС
            <input inputMode="decimal" aria-label="Доставка" placeholder="0" disabled={!editable || saving}
              value={value("delivery_cost", order.delivery_cost)} onChange={(event) => set("delivery_cost", event.target.value)}
              onBlur={() => blurFact("delivery_cost", order.delivery_cost, "money")} />
          </label>
          <label className="wide">Транспорт, трек-номер
            <input aria-label="Транспорт, трек-номер" placeholder="СДЭК 1234567890, ПЭК, курьер…" maxLength={500} disabled={!editable || saving}
              value={value("shipment_note", order.shipment_note)} onChange={(event) => set("shipment_note", event.target.value)}
              onBlur={() => blurFact("shipment_note", order.shipment_note, "text")} />
          </label>
          <label className="wide">Заметка
            <textarea aria-label="Заметка" rows={2} maxLength={2000} disabled={!editable || saving}
              value={value("note", order.note)} onChange={(event) => set("note", event.target.value)}
              onBlur={() => blurFact("note", order.note, "text")} />
          </label>
        </div>
      </div>
      {editable && (
        <div className="order-card-actions">
          {order.column === "awaiting" && <button type="button" className="primary small" disabled={saving} onClick={() => onInvoice(order)}>Вставить счёт…</button>}
          {order.column === "unpaid" && (
            <button type="button" className="secondary small" disabled={saving} onClick={() => void saveFacts({ sent_to_pay_on: today })}>Передать в оплату сегодня</button>
          )}
          {canPay && <button type="button" className="primary small" disabled={saving} onClick={() => onPay(order)}>Оплачен…</button>}
          {canShip && <button type="button" className="secondary small" disabled={saving} onClick={() => void saveFacts({ shipped_on: today })}>Отгружен сегодня</button>}
          {order.facts_updated_by && <span className="muted">факты заказа: {order.facts_updated_by}{order.facts_updated_at ? `, ${formatDay(order.facts_updated_at)}` : ""}</span>}
        </div>
      )}
      {!editable && <p className="page-sub">{order.mine ? "Только просмотр." : `Заказ закупщика ${order.buyer || "—"} — только просмотр.`}</p>}
      {error && <div className="error-banner" role="alert">{error}</div>}

      <h3>Строки заказа</h3>
      <div className="table-wrap">
        <table className="grid order-lines" aria-label="Строки заказа">
          <thead>
            <tr>
              <th>Позиция</th><th>Проект</th><th className="num">Кол-во</th><th className="num">Цена с НДС, ₽</th>
              <th className="num">Сумма, ₽</th><th>Этап</th><th>Ожидается</th><th className="num">Принято</th>
            </tr>
          </thead>
          <tbody>
            {(order.lines ?? []).map((line) => (
              <tr key={line.id}>
                <td className="text"><strong>{line.legacy_id}</strong> {line.name}
                  {line.invoice_item_name && line.invoice_item_name.trim() !== line.calculated_name.trim()
                    ? <small className="muted">по заявке: {line.calculated_name}{line.article && line.article !== line.calculated_name ? ` · ${line.article}` : ""}</small>
                    : line.article && line.article !== line.calculated_name && <small className="muted">{line.article}</small>}
                </td>
                <td>{projectLabel(line.project) || "—"}</td>
                <td className="num">{formatQty(line.quantity, line.unit)}</td>
                <td className="num">{formatAmount(line.price_with_vat) || "—"}</td>
                <td className="num">{line.actual_sum ? formatAmount(line.actual_sum) : line.calculated_sum ? <span className="muted" title="Расчётная: цены факт ещё нет">≈ {formatAmount(line.calculated_sum)}</span> : "—"}
                  {line.source_currency && line.source_sum && <small className="muted">{formatAmount(line.source_sum)} {line.source_currency}</small>}
                </td>
                <td><StatusChip status={line.status}>{LINE_STAGE[line.status] ?? line.status}</StatusChip>
                  {line.status === TO_PAY && line.pay_missing.length > 0 && <small className="tone-warn">для оплаты: {line.pay_missing.join(", ")}</small>}
                </td>
                <td>{formatDay(line.expected_arrival) || "—"}
                  {line.promised && line.promised !== line.expected_arrival && <small className="muted">обещали {formatDayMonth(line.promised)}</small>}
                </td>
                <td className="num">{line.accepted_quantity !== null ? formatQty(line.accepted_quantity, line.unit) : line.status === "Готово" ? "всё" : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {order.backorders.length > 0 && (
        <>
          <h3>Дозавоз</h3>
          <ul className="doc-list" aria-label="Дозавоз">
            {order.backorders.map((line) => (
              <li key={line.legacy_id}>{line.legacy_id} · {line.name} · {formatQty(line.quantity, line.unit)} · <StatusChip status={line.status} /></li>
            ))}
          </ul>
          <p className="muted">Недопоставку приёмка выносит в дозавоз: подтвердить досыл или отменить — в «Очередях», блок «Дозавоз».{" "}
            <button type="button" className="link-button upd-inline-link" onClick={() => openSection("procurement", { tab: "queues" })}>Открыть «Очереди»</button>
          </p>
        </>
      )}

      <h3>Приёмка</h3>
      {receiving.length === 0 ? (
        <p className="muted">{order.paid_count ? "Поставок ещё нет: строки ждут приёмку." : "Приёмка ждёт строки после оплаты."}</p>
      ) : (
        <div className="table-wrap">
          <table className="grid" aria-label="Приёмка заказа">
            <thead>
              <tr><th>Строка</th><th>Статус</th><th className="num">Заказано</th><th className="num">Факт</th><th className="num">Принято</th><th>Несоответствие</th><th>ГРН</th><th>ВК</th><th>УПД</th></tr>
            </thead>
            <tbody>
              {receiving.map((row) => (
                <tr key={row.id}>
                  <td>{row.legacy_id}</td>
                  <td><StatusChip status={row.status} /></td>
                  <td className="num">{formatQty(row.ordered_quantity, row.unit)}</td>
                  <td className="num">{row.fact_quantity !== null ? formatQty(row.fact_quantity, row.unit) : "—"}</td>
                  <td className="num">{row.accepted_quantity !== null ? formatQty(row.accepted_quantity, row.unit) : "—"}
                    {Number(row.backorder_quantity ?? 0) > 0 && <small className="tone-warn">недовоз {formatQty(row.backorder_quantity, row.unit)}</small>}
                  </td>
                  <td className="text">{[...row.nonconformities, row.nonconformity_description].filter(Boolean).join("; ") || "—"}
                    {row.supplier_returns > 0 && <small className="muted">возвратов поставщику: {row.supplier_returns}</small>}
                  </td>
                  <td>{row.grn ? `${row.grn}${row.package ? ` · ${row.package}` : ""}` : "—"}{row.storage_location && <small className="muted">{row.storage_location}</small>}</td>
                  <td>{row.inspected_on ? formatDay(row.inspected_on) : "—"}{row.inspector && <small className="muted">{row.inspector}</small>}</td>
                  <td>{row.document_number ? `№ ${row.document_number}${row.document_date ? ` от ${formatDay(row.document_date)}` : ""}` : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="muted">
        Поступления, брак, недопоставку и УПД ведёт «Приёмка» — здесь только чтение{archived ? ` (ревизий ВК в архиве: ${archived})` : ""}.{" "}
        <button type="button" className="link-button upd-inline-link" onClick={() => openSection("receiving")}>Открыть «Приёмку»</button>
      </p>

      {card.upds !== null && (
        <>
          <h3>УПД</h3>
          {card.upds.length === 0 ? <p className="muted">УПД с номером счёта заказа или с номером из приёмки в реестре нет.</p> : (
            <ul className="doc-list" aria-label="УПД заказа">
              {card.upds.map((upd) => (
                <li key={upd.id}>
                  {onOpenUpd
                    ? <button type="button" className="link-button upd-inline-link" onClick={() => onOpenUpd(upd.id)}>{upd.kind || "УПД"} № {upd.number}</button>
                    : <>{upd.kind || "УПД"} № {upd.number}</>}
                  {upd.date ? ` от ${formatDay(upd.date)}` : ""} · {upd.seller}
                  {upd.total_gross !== null && ` · ${formatAmount(upd.total_gross / 100)} ₽`}
                  <span className="muted"> · {upd.by === "receiving" ? "номер в приёмке" : "основание — счёт заказа"}</span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}

/** Карточка заказа окном над доской (колонки доски для строк и приёмки узки). */
export function OrderModal({ number, onClose, children }: { number: string; onClose: () => void; children: ReactNode }) {
  useEffect(() => {
    const close = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [onClose]);
  return createPortal(
    <div className="dialog-overlay" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <div className="panel move-dialog wide order-modal" role="dialog" aria-label={`Заказ ${number}`}>
        <div className="order-modal-head">
          <h2>Заказ {number}</h2>
          <button type="button" className="secondary small" onClick={onClose}>Закрыть</button>
        </div>
        {children}
      </div>
    </div>,
    document.body,
  );
}
