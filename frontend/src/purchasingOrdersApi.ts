import { api } from "./api";

/** «Счета и поставки» (stage option procurement.orders): backend `purchasing/api_orders.py`. Numbers come as strings. */
const BASE = "/api/purchasing/orders";

/** Колонки доски слева направо. */
export type OrderColumn = "awaiting" | "unpaid" | "to_pay" | "waiting" | "transit" | "receiving" | "stocked";

export type OrderLine = {
  id: string; legacy_id: string; version: number; status: string; name: string; calculated_name: string;
  invoice_item_name: string | null; article: string | null; project: string | null; quantity: string | null; unit: string;
  price_with_vat: string | null; actual_sum: string | null; calculated_sum: string | null; currency: string | null;
  source_currency: string | null; source_sum: string | null; expected_arrival: string | null; promised: string | null;
  invoice_number: string | null; invoice_date: string | null; invoice_url: string | null; paid_on: string | null;
  actual_arrival: string | null; note: string | null; is_backorder: boolean; pay_missing: string[];
  accepted_quantity: string | null; counted_quantity: string | null; inspected_on: string | null;
};

export type OrderInvoice = { number: string | null; date: string | null; url: string | null };

export type Order = {
  order_number: string; supplier: string; buyer: string; mine: boolean; order_date: string | null; column: OrderColumn;
  stages: Record<string, number>; lines_count: number; paid_count: number; received_count: number; invoiced_count: number;
  quarantine_count: number; invoices: OrderInvoice[]; invoice_date: string | null; expected: string | null;
  expected_last: string | null; promised: string | null; paid_on: string | null; stocked_on: string | null; late_days: number;
  sum: string | null; sum_estimated: boolean; foreign: { currency: string; sum: string | null }[]; projects: string[];
  /** Только в карточке (`card`): доска и таблица строки не читают. */
  lines?: OrderLine[];
  /** В списке — только № и этап дозавоза, в карточке — строка целиком. */
  backorders: (Pick<OrderLine, "legacy_id" | "status"> & Partial<OrderLine>)[];
  sent_to_pay_on: string | null; shipped_on: string | null; shipment_note: string; delivery_cost: string | null; note: string;
  facts_version: number; facts_updated_by: string | null; facts_updated_at: string | null;
};

export type OrderList = { today: string; scope: "mine" | "all"; orders: Order[] };

export type ReceivingRow = {
  id: string; procurement_line_id: string; line_legacy_id: string | null; legacy_id: string; status: string; unit: string;
  ordered_quantity: string | null; document_quantity: string | null; fact_quantity: string | null;
  accepted_quantity: string | null; backorder_quantity: string | null; verdict: string | null; nonconformities: string[];
  nonconformity_description: string; grn: string | null; package: string; storage_location: string | null;
  inspected_on: string | null; inspector: string | null; document_number: string; document_date: string | null;
  supplier_returns: number; archived: boolean;
};

export type OrderUpd = {
  id: string; number: string | null; date: string | null; seller: string | null; total_gross: number | null;
  kind: string; status: string; by: "receiving" | "basis"; seller_agrees: boolean | null;
};

export type OrderCard = { order: Order; receiving: ReceivingRow[]; upds: OrderUpd[] | null };

/** Поля фактов заказа: только переданные меняются, null очищает. */
export type OrderFactsPatch = Partial<{
  sent_to_pay_on: string | null; shipped_on: string | null; shipment_note: string; delivery_cost: string | null; note: string;
}>;

export type OrderFollowupOrder = {
  order_number: string | null; supplier: string | null; date: string | null; days: number | null; count: number; names: string[];
};
export type OrderFollowups = {
  awaiting_quotes: { rfq_id: string; rfq_number: string; hint: string; date: string | null; days: number | null; count: number }[];
  late: OrderFollowupOrder[]; arriving: OrderFollowupOrder[]; unpaid: OrderFollowupOrder[]; awaiting_invoice: OrderFollowupOrder[];
};

export type OpenDecisionLine = {
  line_id: string; legacy_id: string; version: number; name: string; article: string | null; project: string | null;
  quantity: string | null; unit: string; need: string; decided_quantity: string; unit_gross: string; total_gross: string;
  vat_rate: string; vat_included: boolean; unit_price: string | null; moq: string | null; pack: string | null;
  lead_days: number | null; currency: string; rfq_id: string; rfq_number: string; quote_supplier: string; blockers: string[];
};
export type OpenDecisionSupplier = {
  supplier: string; currency: string; min_order_sum: string | null; delivery_cost: string | null; lines: OpenDecisionLine[];
};

export const ordersApi = {
  list: (scope: "mine" | "all") => api<OrderList>(`${BASE}?scope=${scope}`),
  card: (number: string) => api<OrderCard>(`${BASE}/${encodeURIComponent(number)}`),
  saveFacts: (number: string, version: number, patch: OrderFactsPatch) =>
    api<{ order: Order }>(`${BASE}/${encodeURIComponent(number)}/facts`, {
      method: "PATCH",
      body: JSON.stringify({ version, ...patch }),
    }),
  followups: () => api<OrderFollowups>(`${BASE}/followups`),
  decisions: () => api<{ suppliers: OpenDecisionSupplier[] }>(`${BASE}/decisions`),
  /** ОЖИД. ДАТА всего заказа: все строки, чья дата ещё меняется, с их версиями — все или ни одной. */
  setExpected: (number: string, expected: string, lines: { line_id: string; version: number }[]) =>
    api<{ lines: unknown[] }>(`/api/procurement/orders/${encodeURIComponent(number)}/expected-arrival`, {
      method: "POST",
      body: JSON.stringify({ expected_arrival: expected, lines }),
    }),
};
