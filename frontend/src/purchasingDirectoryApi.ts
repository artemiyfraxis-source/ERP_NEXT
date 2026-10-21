import { api } from "./api";

export const SUPPLIER_CATEGORIES: Record<string, string> = {
  cable: "Кабель",
  connector: "Разъёмы",
  ekb: "ЭКБ",
  consumables: "Расходники",
  tools: "Инструмент",
  equipment: "Оборудование",
  other: "Прочее",
};

export interface PurchasingSetting {
  key: string;
  label: string;
  hint: string;
  kind: "int" | "decimal" | "bool" | "list" | "text";
  value: number | string | boolean | string[];
  default: number | string | boolean | string[];
  minimum: string | number | null;
  maximum: string | number | null;
}

export interface Supplier {
  id: string;
  name: string;
  inn: string | null;
  categories: string[];
  is_key: boolean;
  contact_person: string | null;
  phone: string | null;
  email: string | null;
  site: string | null;
  domains: string[];
  notes: string | null;
  archived: boolean;
  version: number;
  purchases_count?: number;
  purchases_sum?: string;
}

export interface SupplierInput {
  name: string;
  inn: string | null;
  categories: string[];
  is_key: boolean;
  contact_person: string | null;
  phone: string | null;
  email: string | null;
  site: string | null;
  domains: string[];
  notes: string | null;
}

export interface SupplierCard extends Supplier {
  procurement: {
    lines_total: number;
    paid_lines: number;
    paid_sum: string;
    received: number;
    received_with_date: number;
    on_time: number;
    on_time_pct: number | null;
    open_by_stage: Record<string, number>;
    last_purchases: {
      date: string;
      name: string;
      article: string | null;
      quantity: string | null;
      unit: string | null;
      unit_price_with_vat: string | null;
      order_number: string | null;
    }[];
  };
  rfq: { sent: number; answered: number; quotes: number; answered_pct: number | null };
}

export interface ImportVariant { text: string; lines: number }
export interface ImportPlan {
  dry_run: boolean;
  new: { name: string; key: string; variants: ImportVariant[]; domains: string[]; lines: number }[];
  existing: {
    supplier_id: string; name: string; key: string; lines: number;
    new_variants: ImportVariant[]; new_domains: string[];
  }[];
  created?: number;
  updated?: number;
}

export interface SupplierFilters { q: string; category: string; key: boolean; archived: boolean }

const BASE = "/api/purchasing/suppliers";
const post = (body: unknown) => ({ method: "POST", body: JSON.stringify(body) }) as RequestInit;

export const purchasingDirectoryApi = {
  settings: () => api<PurchasingSetting[]>("/api/purchasing/settings"),
  saveSettings: (changes: Record<string, number | string | boolean | string[]>) =>
    api<PurchasingSetting[]>("/api/purchasing/settings", { method: "PUT", body: JSON.stringify({ changes }) }),
  suppliers: (filters: SupplierFilters) => {
    const params = new URLSearchParams();
    if (filters.q.trim()) params.set("q", filters.q.trim());
    if (filters.category) params.set("category", filters.category);
    if (filters.key) params.set("key", "true");
    if (filters.archived) params.set("archived", "true");
    const query = params.toString();
    return api<Supplier[]>(query ? `${BASE}?${query}` : BASE);
  },
  card: (id: string) => api<SupplierCard>(`${BASE}/${id}`),
  create: (input: SupplierInput) => api<Supplier>(BASE, post(input)),
  update: (id: string, version: number, input: Partial<SupplierInput>) =>
    api<Supplier>(`${BASE}/${id}`, { method: "PATCH", body: JSON.stringify({ version, ...input }) }),
  archive: (id: string, archived: boolean) =>
    api<Supplier>(`${BASE}/${id}/${archived ? "archive" : "unarchive"}`, { method: "POST" }),
  importFromProcurement: (dryRun: boolean) =>
    api<ImportPlan>(`${BASE}/import-from-procurement`, post({ dry_run: dryRun })),
};

/** Контрольная сумма ИНН (10 и 12 цифр) — то же правило, что на сервере. */
export function innValid(inn: string): boolean {
  if (!/^\d+$/.test(inn)) return false;
  const d = inn.split("").map(Number);
  const check = (weights: number[]) => (weights.reduce((sum, w, i) => sum + w * d[i], 0) % 11) % 10;
  if (d.length === 10) return check([2, 4, 10, 3, 5, 9, 4, 6, 8]) === d[9];
  if (d.length === 12) return check([7, 2, 4, 10, 3, 5, 9, 4, 6, 8]) === d[10] && check([3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8]) === d[11];
  return false;
}

/** Текст ошибки формы поставщика или null. */
export function supplierFormError(input: SupplierInput): string | null {
  if (!input.name.trim()) return "Название обязательно";
  const inn = (input.inn ?? "").replace(/[\s-]/g, "");
  if (inn) {
    if (!/^(\d{10}|\d{12})$/.test(inn)) return "ИНН — 10 или 12 цифр";
    if (!innValid(inn)) return "ИНН не проходит проверку контрольной суммы";
  }
  if (input.email && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(input.email)) return "Почта указана неверно";
  return null;
}
