import { api } from "./api";
import type { Tone } from "./statusTone";

/** «Счета», «Цепочка документов», «Поиск по документам»: /api/purchasing/invoices,
 *  /doc-chain, /upd/{id}/invoices, /doc-search. Money arrives in kopecks; the UI shows roubles. */

export type InvoiceStatus = "to_pay" | "paid" | "return" | "other";
export const INVOICE_STATUS_LABELS: Record<InvoiceStatus, string> = {
  to_pay: "В оплату",
  paid: "Оплачен",
  return: "Возврат",
  other: "Другое",
};
export const INVOICE_STATUS_TONE: Record<InvoiceStatus, Tone> = { to_pay: "warning", paid: "success", return: "neutral", other: "neutral" };

export type ChainState = "closed" | "partial" | "over" | "waiting" | "missing" | "unpaid" | "return" | "orphan";
export const CHAIN_STATES: readonly ChainState[] = ["closed", "partial", "over", "waiting", "missing", "unpaid", "return", "orphan"];
export const CHAIN_STATE_LABELS: Record<ChainState, string> = {
  closed: "Закрыт",
  partial: "Закрыт частично",
  over: "Документов больше счёта",
  waiting: "Ждём документы",
  missing: "Нет закрывающих",
  unpaid: "Не оплачен",
  return: "Возврат",
  orphan: "Документ без счёта",
};
export const CHAIN_STATE_TONE: Record<ChainState, Tone> = {
  closed: "success",
  partial: "warning",
  over: "danger",
  waiting: "info",
  missing: "danger",
  unpaid: "neutral",
  return: "neutral",
  orphan: "warning",
};

export type LinkMethod = "manual" | "basis_date" | "basis" | "payment" | "amount";
export const LINK_METHOD_LABELS: Record<LinkMethod, string> = {
  manual: "вручную",
  basis_date: "по основанию и дате",
  basis: "по номеру счёта",
  payment: "по платёжке",
  amount: "по сумме — проверьте",
};

export type CheckLevel = "error" | "warning" | "info";
export type DocCheck = { code: string; level: CheckLevel; text: string };
export type Payment = { number: string; date: string; sum: number };

export type ChainInvoice = {
  id: string; title: string; number: string | null; date: string | null; seller_name: string | null; seller_inn: string | null;
  supplier_id: string | null; supplier_name: string | null; total: number | null; status: InvoiceStatus; payments: Payment[];
  invoice_file_id: string | null;
};

export type ReceivingLine = {
  id: string; procurement_line_id: string; document_number: string; document_date: string | null; document_name: string;
  supplier: string; name: string; article: string; unit: string; accepted_quantity: string | null; fact_quantity: string | null;
  grn: string | null; package: string; status: string;
};
export type ReceivingSummary = { lines: number; grns: string[]; statuses: { status: string; count: number }[]; items?: ReceivingLine[] };

export type ChainUpd = {
  id: string; file_name: string; number: string; date: string | null; kind: string; origin: "google" | "edo" | "scan"; status: string;
  seller_name: string; seller_inn: string; supplier_id: string | null; total_gross: number | null; services_only: boolean;
  method: LinkMethod; basis: string; payment_doc: string; buyer_kind: "ours" | "other" | "unknown"; buyer_org_inn: string | null;
  /** null — no receiving on this stand. */
  receiving?: ReceivingSummary | null;
};

export type DocChain = {
  key: string; invoice: ChainInvoice | null; upds: ChainUpd[]; suggestions: ChainUpd[]; closed_gross: number; state: ChainState;
  problem: boolean; checks: DocCheck[];
};
export type ChainClosing = "edo" | "paper" | "none";
export type ChainList = {
  summary: { by_state: Record<ChainState, number>; problems: number; by_closing: Record<ChainClosing, number> };
  chains: DocChain[]; total: number; page: number; page_size: number; receiving_available: boolean;
};
export type ChainQuery = { view: "problems" | "all"; q: string; closing: ChainClosing | ""; buyer: "" | "ours" | "other" | "unknown"; from: string; to: string; page: number };

export type InvoiceFile = { id: string; name: string; kind: "invoice" | "payment" | "other"; size: number; downloaded: boolean };
export type InvoiceLine = { seq: number; name: string; article: string; qty: number | string | null; unit: string; price: number | null; sum: number | null };
export type InvoiceRow = ChainInvoice & {
  column_title: string; card_created: string | null; vat: number | null; vat_free: boolean; parse_issues: string; line_count: number;
  payment_numbers: string; payment_date: string | null; payment_sum: number | null; payment_matches: boolean | null; files: InvoiceFile[];
  chain: { state: ChainState; problem: boolean; upds: number; closed_gross: number; checks: DocCheck[] } | null;
};
export type InvoiceList = { items: InvoiceRow[]; total: number; page: number; page_size: number; counts: Record<InvoiceStatus, number>; sum: number };
export type InvoiceQuery = { q: string; status: InvoiceStatus | ""; closing: "" | "none"; from: string; to: string; page: number };
export type InvoiceCard = ChainInvoice & {
  board_title: string; column_title: string; card_created: string | null; vat: number | null; vat_free: boolean; parse_issues: string;
  lines: InvoiceLine[]; payment_numbers: string; payment_date: string | null; payment_sum: number | null; payment_matches: boolean | null;
  files: InvoiceFile[]; chain: DocChain | null; receiving_available: boolean;
};

export type DocRef = { kind: "invoice" | "order" | "contract" | "act" | "other"; number: string; date: string | null; raw: string };
export type UpdInvoice = ChainInvoice & {
  method: LinkMethod; decision: "confirmed" | "rejected" | null;
  /** Candidates only: the amount agrees too. */
  suggested?: boolean;
};
export type UpdInvoices = {
  linked: UpdInvoice[]; candidates: UpdInvoice[]; rejected: UpdInvoice[]; basis_refs: DocRef[];
  payment_refs: { number: string; date: string | null }[]; checks: DocCheck[]; receiving: ReceivingSummary | null;
};
export type Decision = "confirmed" | "rejected" | null;

export type SearchKind = "all" | "upd" | "invoice";
export type SearchLine = { seq: number; name: string; article: string; qty: number | string | null; unit: string; price: number | null; sum: number | null };
export type SearchHit = {
  kind: "upd" | "invoice"; id: string; doc_kind: string; number: string | null; date: string | null; seller_name: string | null;
  seller_inn: string | null; origin: "google" | "edo" | "scan" | null; total: number | null; file_id: string | null; title: string | null;
  lines: SearchLine[]; snippet: string | null; matched: "lines" | "header" | "amount" | "text" | "title";
};
export type SearchResult = { hits: SearchHit[]; found: number };

const BASE = "/api/purchasing";

function query(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === "" || (key === "page" && value === 1)) continue;
    search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

export const documentsApi = {
  invoices: (q: InvoiceQuery) =>
    api<InvoiceList>(`${BASE}/invoices${query({ q: q.q.trim(), status: q.status, closing: q.closing, from: q.from, to: q.to, page: q.page })}`),
  invoice: (id: string) => api<InvoiceCard>(`${BASE}/invoices/${id}`),
  chains: (q: ChainQuery) =>
    api<ChainList>(`${BASE}/doc-chain${query({ view: q.view, q: q.q.trim(), closing: q.closing, buyer: q.buyer, from: q.from, to: q.to, page: q.page })}`),
  updInvoices: (updId: string) => api<UpdInvoices>(`${BASE}/upd/${encodeURIComponent(updId)}/invoices`),
  decide: (updId: string, invoiceId: string, decision: Decision) =>
    api<UpdInvoices>(`${BASE}/upd/${encodeURIComponent(updId)}/invoices/${invoiceId}`, { method: "PUT", body: JSON.stringify({ decision }) }),
  search: (q: string, kind: SearchKind) => api<SearchResult>(`${BASE}/doc-search${query({ q: q.trim(), kind: kind === "all" ? undefined : kind })}`),
};

/** A file of a Kaiten card, opened in a new tab (stored files can't be framed). */
export const invoiceFileUrl = (invoiceId: string, fileId: string) => `${BASE}/invoices/${invoiceId}/files/${fileId}`;

/** Kopecks → «1 234,50 ₽»; null → «—». */
export function rub(kop: number | null | undefined): string {
  if (kop === null || kop === undefined) return "—";
  return `${(kop / 100).toLocaleString("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ₽`;
}

export const dayText = (iso: string | null | undefined) => (iso ? iso.split("-").reverse().join(".") : "без даты");

export function docLabel(u: { kind: string; number: string; date: string | null }): string {
  return `${u.kind || "Документ"} № ${u.number || "—"}${u.date ? ` от ${dayText(u.date)}` : ""}`;
}

export const ORIGIN_LABELS: Record<"google" | "edo" | "scan", string> = { google: "бумажный", edo: "ЭДО", scan: "скан склада" };
