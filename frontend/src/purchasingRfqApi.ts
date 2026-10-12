import { api, ApiError } from "./api";

/** «Запросы КП» (stage option procurement.rfq): backend `purchasing/api_rfq.py`. Numbers come as strings. */
const BASE = "/api/purchasing/rfqs";

export type RfqSummary = {
  id: string; number: string; title: string | null; created_at: string; closed_at: string | null; mine: boolean;
  lines: number; suppliers: number; sent: number; quotes: number; decided: number;
};

export type RfqCandidate = {
  id: string; legacy_id: string; name: string; article: string | null; quantity: string; unit: string;
  supplier: string | null; budget_unit_gross: string | null; need_by: string | null; status: string; version: number;
};

export type DecisionField = "gate_override_reason" | "not_cheapest_reason" | "analog_approved_by" | "quote_line_id";

export type RfqCell = {
  asked: true; quote_line_id: string; quote_id: string; received_on: string; valid_until: string | null;
  unit_price: string | null; vat_included: boolean; vat_rate: string; gross_unit: string | null;
  cover_qty: string | null; total: string | null; moq: string | null; pack: string | null;
  tiers: { from: string; price: string }[]; lead_days: number | null; in_stock: boolean; analog: boolean;
  analog_note: string | null; declined: boolean; expired: boolean; cheapest: boolean; baseline: boolean;
  decided: boolean; requires: DecisionField[];
};
/** A line not asked of that supplier: no quote expected there. */
export type RfqUnaskedCell = { asked: false };

export type RfqGate = { priced: number; required: number; has_non_key: boolean; require_non_key: boolean; passes: boolean };

export type RfqDecision = {
  id: string; quote_line_id: string; supplier_id: string | null; cover_quantity: string; unit_gross: string;
  total_gross: string; baseline_unit_gross: string | null; cheapest_unit_gross: string | null; priced_quotes: number;
  gate_override_reason: string | null; not_cheapest_reason: string | null; analog_approved_by: string | null;
  decided_at: string; applied_at: string | null;
};

export type RfqLine = {
  id: string; procurement_line_id: string; legacy_id: string | null; status: string | null; version: number | null;
  editable: boolean; name: string; article: string | null; quantity: string; unit: string; need_by: string | null;
  budget_unit_gross: string | null; delta_pct: string | null; gate: RfqGate;
  cheapest_quote_line_id: string | null; baseline_quote_line_id: string | null; decision: RfqDecision | null;
  cells: Record<string, RfqCell | RfqUnaskedCell>;
};

/** The supplier's quote cell of a line; undefined — no quote (or the line is not asked of him). */
export function quoteCell(line: RfqLine, supplierId: string): RfqCell | undefined {
  const cell = line.cells[supplierId];
  return cell && cell.asked !== false ? cell : undefined;
}

/** The line is not asked of the supplier (the request asks him for a part of its lines). */
export function unasked(line: RfqLine, supplierId: string): boolean {
  return line.cells[supplierId]?.asked === false;
}

export type RfqSupplier = {
  supplier_id: string; name: string; is_key: boolean; reason: string; sent_at: string | null;
  /** the request lines asked of him; null — every line */
  line_ids: string[] | null;
  declined_at: string | null; decline_note: string | null; answered: boolean; delivery_cost: string | null;
  min_order_sum: string | null; basket_total: string | null; basket_lines: number; chosen_total: string | null;
  min_order_warning: string | null;
};

export type RfqMatrix = {
  rfq: { id: string; number: string; title: string | null; created_at: string; closed_at: string | null; mine: boolean };
  settings: { gate_min_quotes: number; gate_require_non_key: boolean; default_vat_rate: string };
  suppliers: RfqSupplier[];
  lines: RfqLine[];
  quotes: {
    id: string; supplier_id: string; supplier_is_key: boolean; received_on: string; valid_until: string | null;
    delivery_cost: string | null; min_order_sum: string | null; source: string; used: boolean;
  }[];
};

export type SupplierBrief = { supplier_id: string; name: string; is_key: boolean; categories: string[] };

/** The request pool (`GET /pool`): the buyer's lines for a new request by nomenclature category. */
export type PoolCategory = "cable" | "connector" | "ekb" | "other";
export type PastPrice = { price: string; date: string; supplier: string | null; unit: string | null };
export type PoolLine = RfqCandidate & {
  project: string | null; category: PoolCategory; supplier_name: string | null; supplier_id: string | null;
  item: { id: string; name: string; category: string; matched_by: "link" | "part_key" | "alias" } | null;
  history: { purchases: number; last: PastPrice | null; min: PastPrice | null } | null;
  cheaper_before_pct: string | null;
};
export type PoolGroup = {
  category: PoolCategory; label: string; count: number;
  suppliers: { name: string; supplier_id: string | null; lines: number }[]; lines: PoolLine[];
  /** lines whose supplier column says «Склад» — taken from our stock, nobody to ask */
  from_stock?: number;
};
export type RfqPoolResult = { total: number; groups: PoolGroup[] };

/** Whom to ask (`GET /suggest`): each supplier once with all its reasons, ranked; `supplier_id: null` — not in the
 *  directory yet (added by name when the request is created). */
// "manual" — only on the client: a supplier the buyer added by name in the panel.
export type SuggestReasonCode = "items" | "category_history" | "original" | "key" | "rotation" | "category" | "manual";
export type SuggestReason = { code: SuggestReasonCode; label: string; lines: number | null; last_date: string | null };
/** A chosen line the supplier relates to (procurement line id) and why — ticked by default. */
export type SuggestLine = { line_id: string; reasons: SuggestReasonCode[] };
export type SupplierSuggestion = {
  supplier_id: string | null; name: string; is_key: boolean; categories: string[]; reasons: SuggestReason[];
  reason: SuggestReasonCode; rfq_reason: string; preselect: boolean; in_rfq: boolean; last_asked_at: string | null;
  lines: SuggestLine[];
};
export type SuggestResult = { categories: string[]; lines: number; suggestions: SupplierSuggestion[] };
/** `line_ids` — the procurement lines asked of this supplier; omitted — every line of the request. */
export type SupplierPick = { supplier_id?: string; name?: string; reason: string; line_ids?: string[] };

export type QuoteLineInput = {
  rfq_line_id: string; unit_price: string | null; vat_included: boolean; vat_rate: string | null; moq: string | null;
  pack: string | null; lead_days: number | null; in_stock: boolean; declined: boolean; analog: boolean;
  analog_note: string | null;
};
export type QuoteInput = {
  supplier_id: string; received_on: string | null; valid_until: string | null; delivery_cost: string | null;
  min_order_sum: string | null; source: "manual" | "paste"; lines: QuoteLineInput[];
};

export type PastedRow = {
  source_name: string; source_article: string; qty: string | null; unit: string; price: string | null;
  vat_rate: string | null; lead_days: number | null; in_stock: boolean; declined: boolean; analog: boolean;
  rfq_line_id: string | null; matched_by: "article" | "name" | null;
};
export type PastePreview = {
  header: string[] | null; columns: Record<string, number>; vat_included: boolean; default_vat_rate: string; rows: PastedRow[];
};

export type DecisionInput = {
  rfq_line_id: string; quote_line_id: string; expected_version: number | null;
  gate_override_reason?: string; not_cheapest_reason?: string; analog_approved_by?: string;
};

export type Violation = { field: DecisionField; message: string };

/** The fields the server refused a decision for (422 «decision_reasons»), else []. */
export function decisionViolations(error: unknown): Violation[] {
  if (!(error instanceof ApiError) || error.status !== 422) return [];
  const detail = error.detail as { code?: string; violations?: Violation[] } | undefined;
  return detail?.code === "decision_reasons" ? detail.violations ?? [] : [];
}

const post = <T>(path: string, body: unknown = {}) => api<T>(path, { method: "POST", body: JSON.stringify(body) });
const remove = (path: string) => api<void>(path, { method: "DELETE" });
const put = <T>(path: string, body: unknown) => api<T>(path, { method: "PUT", body: JSON.stringify(body) });

export const rfqApi = {
  list: (scope: "mine" | "all") => api<{ rfqs: RfqSummary[] }>(`${BASE}?scope=${scope}`),
  candidates: () => api<{ lines: RfqCandidate[] }>(`${BASE}/candidates`),
  pool: () => api<RfqPoolResult>(`${BASE}/pool`),
  create: (lineIds: string[], title: string, suppliers: SupplierPick[] = []) =>
    post<{ id: string; number: string; suppliers: number }>(BASE, { line_ids: lineIds, title: title || null, suppliers }),
  matrix: (id: string) => api<RfqMatrix>(`${BASE}/${id}`),
  directory: () => api<{ suppliers: SupplierBrief[] }>(`${BASE}/directory`),
  suggest: (id: string) => api<SuggestResult>(`${BASE}/suggest?rfq_id=${encodeURIComponent(id)}`),
  suggestForLines: (lineIds: string[]) =>
    api<SuggestResult>(`${BASE}/suggest?line_ids=${lineIds.map(encodeURIComponent).join(",")}`),
  addSupplier: (id: string, supplier: { supplier_id?: string; name?: string }, reason = "manual") =>
    post<SupplierBrief>(`${BASE}/${id}/suppliers`, { ...supplier, reason }),
  removeSupplier: (id: string, supplierId: string) => remove(`${BASE}/${id}/suppliers/${supplierId}`),
  setSupplierLines: (id: string, supplierId: string, lineIds: string[]) =>
    put<{ line_ids: string[] | null }>(`${BASE}/${id}/suppliers/${supplierId}/lines`, { line_ids: lineIds }),
  markSent: (id: string, supplierId: string) => post(`${BASE}/${id}/suppliers/${supplierId}/sent`),
  markDeclined: (id: string, supplierId: string, note: string, declined = true) =>
    post(`${BASE}/${id}/suppliers/${supplierId}/declined`, { note: note || null, declined }),
  addQuote: (id: string, quote: QuoteInput) => post<{ id: string }>(`${BASE}/${id}/quotes`, quote),
  paste: (id: string, text: string, supplierId?: string) =>
    post<PastePreview>(`${BASE}/${id}/quotes/paste`, supplierId ? { text, supplier_id: supplierId } : { text }),
  deleteQuote: (quoteId: string) => remove(`${BASE}/quotes/${quoteId}`),
  decide: (id: string, decision: DecisionInput) => post<RfqDecision>(`${BASE}/${id}/decisions`, decision),
  undoDecision: (id: string, decisionId: string) => remove(`${BASE}/${id}/decisions/${decisionId}`),
  close: (id: string) => post(`${BASE}/${id}/close`),
};
