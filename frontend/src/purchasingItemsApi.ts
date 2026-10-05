import { api } from "./api";

/** «Номенклатура» (stage option references.items): /api/purchasing/items. Money and quantities arrive as strings. */
export type ItemCategory = "cable" | "connector" | "ekb" | "other";
export const ITEM_CATEGORIES: { key: ItemCategory; label: string }[] = [
  { key: "cable", label: "Кабель" },
  { key: "connector", label: "Разъёмы" },
  { key: "ekb", label: "ЭКБ" },
  { key: "other", label: "Прочее" },
];
export const categoryLabel = (key: string) => ITEM_CATEGORIES.find((c) => c.key === key)?.label ?? key;

export type ItemDemand = { unit: string; quantity: string; lines: number };
export type Item = {
  id: string; name: string; part_number: string | null; manufacturer: string | null; category: ItemCategory;
  item_type: string | null; unit: string; notes: string | null; archived: boolean; version: number;
};
export type ItemRow = Item & {
  last_price: string | null; purchases: number; linked_lines: number; demand: ItemDemand[];
  available: string | null; available_unit: string | null; stock_lots: number;
};
export type LotVia = "manual" | "match" | "chain";
export const VIA_LABELS: Record<LotVia, string> = { manual: "вручную", match: "по совпадению", chain: "по приёмке" };
export type ItemStockLot = {
  lot_id: string; warehouse: string; grn: string; package: string; name: string; storage_location: string; unit: string;
  balance: string; reserved: string; available: string; via: LotVia;
};
export type ItemStock = { lots: ItemStockLot[]; totals: { unit: string; balance: string; reserved: string; available: string }[] };
export type ItemReceipt = {
  receiving_id: string; date: string; document_number: string | null; supplier: string; unit: string; ordered: string;
  fact: string | null; status: string; verdict: string | null; grn: string | null; package: string;
};
export type ItemBrief = { item_id: string; name: string; category: ItemCategory; via: LotVia };
export type StockImportLot = { lot_id: string; warehouse: string; grn: string; package: string; name: string; article: string };
export type StockImportResult = {
  linked: (StockImportLot & { item_id: string; item_name: string; reason: string })[];
  create: { kind: "article" | "name"; key: string; item_id: string; name: string; part_number: string | null; unit: string; category: ItemCategory; lots: StockImportLot[] }[];
  ambiguous: (StockImportLot & { candidates: { item_id: string; name: string }[] })[];
  lots: number; dry_run: boolean;
};
export type ItemList = { items: ItemRow[]; counts: Record<ItemCategory, number>; total: number; page: number; page_size: number };
export type ItemAlias = { id: string; text: string; supplier_id: string | null; supplier_name: string | null };
export type PricePoint = { line_id: string; date: string; supplier: string | null; unit_price_with_vat: string; quantity: string; unit: string; order_number: string | null };
export type ItemQuote = { quote_id: string; date: string; supplier: string; unit_price_with_vat: string | null; moq: string | null; declined: boolean; analog: boolean };
export type ItemCard = Item & {
  aliases: ItemAlias[]; linked_lines: number; demand: ItemDemand[]; history: PricePoint[];
  price: { min: string | null; avg: string | null; last: string | null }; quotes: ItemQuote[];
  stock?: ItemStock; receipts?: ItemReceipt[];
};
export type ItemInput = { name: string; part_number?: string | null; manufacturer?: string | null; category?: ItemCategory | null; item_type?: string | null; unit?: string | null; notes?: string | null };
export type ImportGroup = {
  kind: "article" | "name"; key: string; name: string; part_number: string | null; unit: string; category: ItemCategory;
  manufacturer: string | null; lines: number; action: "create" | "link"; reason: string; item_id: string; existing_name?: string; archived?: boolean;
};
export type ImportResult = { groups: ImportGroup[]; created: number; linked_groups: number; aliases: number; lines: number; dry_run: boolean };

const BASE = "/api/purchasing/items";
const json = (method: string, body: unknown): RequestInit => ({ method, body: JSON.stringify(body) });

export const purchasingItemsApi = {
  list: (query: { q: string; category: ItemCategory | ""; hasPrice: boolean; page: number }) => {
    const params = new URLSearchParams();
    if (query.q.trim()) params.set("q", query.q.trim());
    if (query.category) params.set("category", query.category);
    if (query.hasPrice) params.set("has_price", "true");
    if (query.page > 1) params.set("page", String(query.page));
    const text = params.toString();
    return api<ItemList>(text ? `${BASE}?${text}` : BASE);
  },
  card: (id: string) => api<ItemCard>(`${BASE}/${id}`),
  create: (input: ItemInput) => api<Item>(BASE, json("POST", input)),
  update: (id: string, version: number, changes: Partial<ItemInput>) => api<Item>(`${BASE}/${id}`, json("PATCH", { version, ...changes })),
  archive: (id: string) => api<Item>(`${BASE}/${id}/archive`, { method: "POST" }),
  addAlias: (id: string, text: string, supplierId: string | null) => api<{ aliases: ItemAlias[] }>(`${BASE}/${id}/aliases`, json("POST", { text, supplier_id: supplierId })),
  deleteAlias: (id: string, aliasId: string) => api<{ aliases: ItemAlias[] }>(`${BASE}/${id}/aliases/${aliasId}`, { method: "DELETE" }),
  merge: (keepId: string, dropId: string) => api<Item>(`${BASE}/merge`, json("POST", { keep_id: keepId, drop_id: dropId })),
  link: (procurementLineId: string, itemId: string) => api<unknown>(`${BASE}/link`, json("POST", { procurement_line_id: procurementLineId, item_id: itemId })),
  forLines: (ids: string[]) => api<Record<string, { item_id: string; name: string; part_number: string | null; unit: string }>>(`${BASE}/for-lines?ids=${ids.join(",")}`),
  forLots: (ids: string[]) => api<Record<string, ItemBrief>>(`${BASE}/for-lots?ids=${ids.join(",")}`),
  forReceiving: (ids: string[]) => api<Record<string, ItemBrief>>(`${BASE}/for-receiving?ids=${ids.join(",")}`),
  linkLot: (lotId: string, itemId: string) => api<unknown>(`${BASE}/link-lot`, json("POST", { lot_id: lotId, item_id: itemId })),
  unlinkLot: (lotId: string) => api<unknown>(`${BASE}/link-lot/${lotId}`, { method: "DELETE" }),
  importFromStock: (dryRun: boolean) => api<StockImportResult>(`${BASE}/import-from-stock`, json("POST", { dry_run: dryRun })),
  importFromProcurement: (dryRun: boolean) => api<ImportResult>(`${BASE}/import-from-procurement`, json("POST", { dry_run: dryRun })),
};
