import { api } from "./api";
import { formatNumber, NBSP } from "./format";
import { innValid } from "./purchasingDirectoryApi";
import type { Tone } from "./statusTone";

/** «Реестр УПД»: /api/purchasing/upd. Money in kopecks (integers), quantities and unit
 *  prices as decimal strings — as the server keeps them. The line and document checks below are the server's
 *  (backend upd_checks.py), so the edit form highlights exactly what the card will report after saving. */

export type UpdStatus = "waiting" | "parsed" | "needs_review" | "verified" | "duplicate" | "not_purchase";
export const UPD_STATUS_LABELS: Record<UpdStatus, string> = {
  waiting: "Ожидаем данные",
  parsed: "Разобран",
  needs_review: "Требует ручной проверки",
  verified: "Проверен вручную",
  duplicate: "Дубликат",
  not_purchase: "Не документ закупки",
};
export const UPD_STATUS_TONES: Record<UpdStatus, Tone> = {
  waiting: "neutral", parsed: "success", needs_review: "danger", verified: "info", duplicate: "neutral", not_purchase: "neutral",
};
/** The buyer works with these first; duplicates live apart. */
export const UPD_STATUS_ORDER: UpdStatus[] = ["needs_review", "waiting", "parsed", "verified", "not_purchase"];

export type UpdOrigin = "google" | "edo";
export const UPD_ORIGIN_LABELS: Record<UpdOrigin, string> = { edo: "ЭДО", google: "Бумажный" };
export const UPD_ORIGIN_HINTS: Record<UpdOrigin, string> = {
  edo: "электронный документ из Диадока (XML), подписан в ЭДО",
  google: "скан или PDF, распознан разборщиком Google — оригинал на бумаге",
};
export type UpdBuyerKind = "ours" | "other" | "unknown";
/** Our organisation: documents for any other INN are bought for someone else. */
export const OUR_INN = "5040191181";

export interface UpdCard {
  id: string; file_name: string; kind: string; origin: UpdOrigin; status: UpdStatus; parser_status: string;
  number: string; date: string | null; seller_name: string; seller_inn: string; seller_org_inn: string; seller_note: string;
  supplier_id: string | null; supplier_name: string | null; supplier_manual: boolean;
  total_gross: number | null; line_count: number; issues: string[]; has_file: boolean; project_reference: string | null;
  manual: boolean; verified: boolean; changed_in_parser: boolean; twin_of: string | null; copies: number;
  buyer_inn: string; buyer_kind: UpdBuyerKind; buyer_org_inn: string | null; buyer_note: string;
}

/** Tabs of the registry: УПД · М-15 · ТН+СФ · Прочие (backend upd_registry.kind_group). */
export type UpdKindGroup = "upd" | "m15" | "tn_sf" | "other";
export const UPD_KIND_GROUPS: { key: UpdKindGroup; label: string; hint: string }[] = [
  { key: "upd", label: "УПД", hint: "«УПД» и «УПД до 2019 года»" },
  { key: "m15", label: "М-15", hint: "накладные М-15 (давальческие материалы)" },
  { key: "tn_sf", label: "ТН+СФ", hint: "«ТОРГ-12» и «Счёт-фактура»" },
  { key: "other", label: "Прочие", hint: "корректировочные счета-фактуры, акты и всё остальное" },
];

export interface UpdList {
  kinds: Record<UpdKindGroup, number>;
  items: UpdCard[]; total: number; page: number; page_size: number; counts: Record<UpdStatus, number>; review: number;
  origins: Record<UpdOrigin, number>; buyers: { key: string; count: number }[];
  /** Suppliers the documents in force are linked to, and how many documents have none. */
  suppliers: { id: string; name: string; count: number }[]; no_supplier: number; sum_gross: number;
}

export interface UpdHeader {
  number: string; date: string | null; seller_name: string; seller_inn: string; seller_kpp: string; buyer_inn: string;
  total_net: number | null; total_vat: number | null; total_gross: number | null;
}

export interface UpdLine {
  seq: number; name: string; article: string; unit: string; quantity: string | null; price: string | null;
  net: number | null; vat_rate: string; vat: number | null; gross: number | null; is_service: boolean;
  product_type_code: string; unit_code: string; excise: string; country_code: string; country: string; declaration: string;
  issues: string[]; fixes: string[];
}

export interface UpdBrief {
  id: string; origin: UpdOrigin; file_name: string; kind: string; number: string; date: string | null; status: UpdStatus;
  total_gross: number | null; seller_name: string;
}

export interface UpdDetail extends UpdCard {
  folder: string; tag: string; model: string; basis: string; payment_doc: string; file_amount: number | null;
  dup_of: string | null; drive_url: string; parser_updated: string; synced_at: string | null; verified_at: string | null;
  manual_saved_at: string | null; header: UpdHeader; parser_header: UpdHeader; lines: UpdLine[]; parser_lines: UpdLine[];
  twins: { main: UpdBrief | null; copies: UpdBrief[] };
  file: { name: string; size: number; kind: "pdf" | "xml" | "file" } | null;
}

export type UpdLineInput = {
  seq: number; name: string; article: string; unit: string; quantity: string | null; price: string | null;
  net: number | null; vat_rate: string; vat: number | null; gross: number | null; is_service: boolean;
};
export type UpdManualInput = { header: UpdHeader; lines: UpdLineInput[]; parser_updated: string; manual_saved_at: string | null };

export type EdoStatus = "created" | "updated" | "unchanged" | "twin" | "skipped" | "error";
export const EDO_STATUS_LABELS: Record<EdoStatus, string> = {
  created: "добавлен", updated: "обновлён", unchanged: "уже был", twin: "двойник — уже есть", skipped: "пропущен", error: "ошибка",
};
export interface EdoFileResult { name: string; status: EdoStatus; document_id?: string; number?: string; seller_name?: string; message?: string }
export interface EdoImportResult { files: EdoFileResult[]; dry_run: boolean }

export interface UpdSeller {
  inn: string; name: string; documents: number; total_gross: number; last_date: string | null; needs_review: number;
}
export interface UpdSellers { sellers: UpdSeller[]; skipped_invalid_inn: number }
export interface SellersImportResult {
  created: { inn: string; name: string; supplier_id: string }[]; skipped: { inn: string; name: string; reason: string }[];
  suppliers_changed: number; dry_run: boolean;
}

export interface UpdFilters {
  q: string; status: UpdStatus | ""; origin: UpdOrigin | ""; buyer: string; supplierId: string; noSupplier: boolean;
  dateFrom: string; dateTo: string; review: boolean; duplicates: boolean; kind: UpdKindGroup | ""; page: number;
}
export const EMPTY_FILTERS: UpdFilters = {
  q: "", status: "", origin: "", buyer: "", supplierId: "", noSupplier: false, dateFrom: "", dateTo: "", review: false,
  duplicates: false, kind: "", page: 1,
};

const BASE = "/api/purchasing/upd";
const json = (method: string, body: unknown): RequestInit => ({ method, body: JSON.stringify(body) });
const doc = (id: string) => `${BASE}/${encodeURIComponent(id)}`;

export const purchasingUpdApi = {
  list: (filters: UpdFilters) => {
    const params = new URLSearchParams();
    if (filters.q.trim()) params.set("q", filters.q.trim());
    if (filters.status) params.set("status", filters.status);
    if (filters.origin) params.set("origin", filters.origin);
    if (filters.buyer) params.set("buyer", filters.buyer);
    if (filters.supplierId) params.set("supplier_id", filters.supplierId);
    if (filters.noSupplier) params.set("no_supplier", "true");
    if (filters.dateFrom) params.set("date_from", filters.dateFrom);
    if (filters.dateTo) params.set("date_to", filters.dateTo);
    if (filters.review) params.set("review", "true");
    if (filters.duplicates) params.set("duplicates", "true");
    if (filters.kind) params.set("kind", filters.kind);
    if (filters.page > 1) params.set("page", String(filters.page));
    const text = params.toString();
    return api<UpdList>(text ? `${BASE}?${text}` : BASE);
  },
  card: (id: string) => api<UpdDetail>(doc(id)),
  /** The original: a PDF opens in a new tab (files cannot be framed), an EDO XML downloads. */
  fileUrl: (id: string) => `${doc(id)}/file`,
  saveManual: (id: string, input: UpdManualInput) => api<UpdDetail>(`${doc(id)}/manual`, json("PUT", input)),
  dropManual: (id: string) => api<UpdDetail>(`${doc(id)}/manual`, { method: "DELETE" }),
  setVerified: (id: string, verified: boolean) => api<UpdDetail>(`${doc(id)}/verified`, json("PUT", { verified })),
  setSupplier: (id: string, supplierId: string | null) => api<UpdDetail>(`${doc(id)}/supplier`, json("PUT", { supplier_id: supplierId })),
  setProject: (id: string, reference: string | null) => api<UpdDetail>(`${doc(id)}/project`, json("PUT", { project_reference: reference })),
  /** One request per file keeps every body under the server's ceiling; no deadline — a big archive over a slow link
   *  is not a hung request. */
  importEdo: (file: { file_name: string; content_base64: string }, dryRun: boolean) =>
    api<EdoImportResult>(`${BASE}/import-edo`, { ...json("POST", { files: [file], dry_run: dryRun }), timeoutMs: null }),
  sellers: () => api<UpdSellers>(`${BASE}/sellers`),
  importSellers: (dryRun: boolean, inns?: string[]) =>
    api<SellersImportResult>(`${BASE}/sellers/import`, { ...json("POST", { dry_run: dryRun, inns: inns ?? null }), safe: dryRun }),
};

/** A picked file as base64 for the JSON upload (the server takes no multipart). */
export function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",")[1] ?? "");
    reader.onerror = () => reject(new Error(`Не удалось прочитать файл «${file.name}»`));
    reader.readAsDataURL(file);
  });
}

/* ---- money and numbers ---- */

/** Kopecks → «1 234,56 ₽»; null → «—». */
export function rub(kopecks: number | null | undefined): string {
  if (kopecks === null || kopecks === undefined) return "—";
  return `${formatNumber(kopecks / 100, { digits: 2, minDigits: 2 })}${NBSP}₽`;
}

/** Kopecks → «1234.56» for an input box; null → "". */
export function kopToInput(kopecks: number | null): string {
  return kopecks === null ? "" : (kopecks / 100).toFixed(2);
}

/** «1 234,56» → kopecks; empty → null; not a number → NaN (the form says so). */
export function inputToKop(text: string): number | null {
  const value = text.replace(/\s/g, "").replace(",", ".");
  if (value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? Math.round(number * 100) : Number.NaN;
}

/** «250,5» → «250.5»; empty → null; not a non-negative number → false. */
export function inputToDecimal(text: string): string | null | false {
  const value = text.replace(/\s/g, "").replace(",", ".");
  if (value === "") return null;
  return /^\d+(\.\d+)?$/.test(value) ? value : false;
}

/* ---- checks (backend upd_checks.py) ---- */

/** «22», «22%», «без НДС» → percent (0 for no VAT) or null when unknown. */
export function vatRate(text: string): number | null {
  if (/без\s*ндс/i.test(text)) return 0;
  const number = Number(text.replace("%", "").replace(",", ".").trim());
  if (text.trim() === "" || !Number.isFinite(number)) return null;
  return number > 0 && number < 1 ? Math.round(number * 100) : number;
}

export interface LineFacts { quantity: number | null; price: number | null; net: number | null; vat_rate: string; vat: number | null; gross: number | null }

/** Same tolerance as the Google parser: 5 kopecks or 1 %. */
const close = (a: number, b: number) => Math.abs(a - b) <= Math.max(5, Math.abs(b) * 0.01);

export function lineIssues(line: LineFacts): string[] {
  const issues: string[] = [];
  const rate = vatRate(line.vat_rate);
  if (line.quantity !== null && line.price !== null && line.net !== null && !close(line.quantity * line.price * 100, line.net)) {
    issues.push("кол-во × цена ≠ без НДС");
  }
  if (rate !== null && rate > 0 && line.net !== null && line.vat !== null && !close((line.net * rate) / 100, line.vat)) {
    issues.push(`НДС не равен ставке ${rate}%`);
  }
  const vat = line.vat ?? (rate === 0 ? 0 : null);
  if (line.net !== null && vat !== null && line.gross !== null && !close(line.net + vat, line.gross)) {
    issues.push("без НДС + НДС ≠ с НДС");
  }
  return issues;
}

const plainRub = (kopecks: number) => String(Math.round(kopecks) / 100);

/** Checks of the buyer's own version (the parser checks its own). */
export function docIssues(kind: string, header: UpdHeader, lines: LineFacts[]): string[] {
  const issues: string[] = [];
  const m15 = kind === "М-15";
  if (lines.length === 0) issues.push("нет ни одной позиции");
  if (!header.number) issues.push("нет номера документа");
  if (!header.date) issues.push("нет даты документа");
  if (!header.seller_name) issues.push(m15 ? "нет отправителя" : "нет продавца");
  if (!m15) {
    if (!header.seller_inn) issues.push("нет ИНН продавца");
    else if (!innValid(header.seller_inn.replace(/\D/g, ""))) issues.push(`ИНН продавца не проходит проверку: ${header.seller_inn}`);
  }
  const total = header.total_gross;
  if (lines.length > 0 && total !== null && lines.every((line) => line.gross !== null)) {
    const sum = lines.reduce((acc, line) => acc + (line.gross ?? 0), 0);
    if (Math.abs(sum - total) > Math.max(100, total * 0.005)) issues.push(`сумма строк ${plainRub(sum)} ≠ итог документа ${plainRub(total)}`);
  }
  const bad = lines.filter((line) => lineIssues(line).length > 0).length;
  if (bad > 0) issues.push(`строк с замечаниями: ${bad}`);
  return issues;
}

/** «ours», «unknown» or the INN of the other organisation the document is for. */
export function buyerKey(card: Pick<UpdCard, "buyer_kind" | "buyer_org_inn">): string {
  return card.buyer_kind === "other" ? card.buyer_org_inn ?? "unknown" : card.buyer_kind;
}

export function buyerLabel(key: string): string {
  if (key === "ours") return `мы — ИНН ${OUR_INN}`;
  if (key === "unknown") return "не указан";
  if (key === "other") return "не наш покупатель";
  return `другая организация — ИНН ${key}`;
}
