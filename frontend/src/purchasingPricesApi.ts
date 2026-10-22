import { api } from "./api";

/** «Цены и карточки»: /api/purchasing/prices. Money is integer kopecks;
 *  reference prices keep unit_price4 = unit price × 10 000 in their own currency. */
export type PriceCurrency = "RUB" | "CNY" | "USD";
export const CURRENCY_SIGNS: Record<PriceCurrency, string> = { RUB: "₽", CNY: "¥", USD: "$" };

export type PriceRef = {
  id: string; supplier_id: string | null; supplier_name: string; offered_part: string; offered_manufacturer: string;
  currency: PriceCurrency; unit_price4: number; vat_included: boolean; quantity: string | null; lead_time: string; source: string;
};
export type UpdPurchase = {
  upd_id: string; number: string; date: string | null; supplier_id: string | null; supplier_name: string; line_name: string;
  quantity: string | null; unit: string; unit_price_with_vat: number | null; item_unit_price_with_vat: number | null; needs_review: boolean;
};
export type InvoicePosition = {
  invoice_id: string; number: string | null; date: string | null; status: string; supplier_id: string | null; supplier_name: string;
  name: string; article: string; quantity: number | null; unit: string; price: number | null; sum: number | null;
};
export type ItemPrices = {
  unit: string; refs: PriceRef[]; upd: UpdPurchase[]; invoices: InvoicePosition[];
  summary: { count: number; min: number | null; last: number | null; last_date: string | null; last_supplier: string | null };
};
export type SupplierPrices = {
  upd: { count: number; total_gross: number; last_date: string | null; share_pct: number | null;
    recent: { id: string; number: string; date: string | null; kind: string; total_gross: number | null }[] };
  invoices: { count: number; total: number; unpaid_count: number; unpaid_total: number; last_date: string | null;
    recent: { id: string; number: string | null; date: string | null; status: string; total: number | null }[] };
  top_positions: { key: string; name: string; article: string; unit: string; purchases: number; sum: number; last_date: string; last_price: number | null }[];
  offers: { item_id: string; item_name: string; offered_part: string; currency: PriceCurrency; unit_price4: number; vat_included: boolean; lead_time: string }[];
  offers_total: number;
};
export type CatalogEntry = {
  key: string; name: string; article: string; unit: string; purchases: number; suppliers: number; last_price: number | null;
  last_date: string | null; last_supplier: string | null; min_price: number | null; max_price: number | null;
};
export type CatalogPoint = { upd_id: string; number: string; date: string | null; supplier_name: string; quantity: string | null; unit: string; unit_price_with_vat: number | null };
export type CatalogPage = { total: number; page: number; page_size: number; entries: CatalogEntry[] };
export type UpdItemCandidate = { key: string; name: string; article: string; unit: string; category: string; lines: number; suppliers: string[]; last_date: string | null };
export type UpdItemsPlan = { candidates: UpdItemCandidate[]; known: number; skipped_services: number; skipped_advances: number; waiting_documents: number };

const BASE = "/api/purchasing/prices";

export const purchasingPricesApi = {
  item: (id: string) => api<ItemPrices>(`${BASE}/item/${id}`),
  supplier: (id: string) => api<SupplierPrices>(`${BASE}/supplier/${id}`),
  catalog: (q: string, page: number) => {
    const params = new URLSearchParams();
    if (q.trim()) params.set("q", q.trim());
    if (page > 1) params.set("page", String(page));
    const text = params.toString();
    return api<CatalogPage>(text ? `${BASE}/catalog?${text}` : `${BASE}/catalog`);
  },
  points: (key: string) => api<CatalogPoint[]>(`${BASE}/catalog/points?key=${encodeURIComponent(key)}`),
  plan: () => api<UpdItemsPlan>(`${BASE}/upd-items/plan`),
  apply: (keys: string[]) => api<{ created: number; aliases: number }>(`${BASE}/upd-items/apply`, { method: "POST", body: JSON.stringify({ keys }) }),
};
