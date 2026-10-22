import { api } from "./api";

/* «Заявки» закупщика (Roller_Buyer RequestsPage) over procurement lines. */

export type RbStatus =
  | "new" | "requote" | "from_stock" | "in_rfq" | "quoted" | "decided" | "ordered" | "to_pay" | "paid" | "shipped"
  | "partial" | "delivered" | "cancelled";

export type RqProject = { key: string; kind: "project" | "purpose" | "none"; value: string };
export type RqCounts = Partial<Record<RbStatus, number>>;
export type RqProjectSummary = RqProject & { lines: number; counts: RqCounts };

export type RqBatch = {
  key: string;
  project: RqProject;
  designation: string;
  request_date: string;
  planned_date: string;
  initiators: string[];
  lines: number;
  counts: RqCounts;
  mine: number;
  selectable: number;
  to_take: number;
};

export type RqLine = {
  id: string;
  legacy_id: string;
  version: number;
  status: string;
  rb_status: RbStatus;
  name: string;
  article: string | null;
  item_type: string;
  category: string;
  quantity: string;
  to_buy: string;
  stock_covered: string;
  unit: string;
  planned_date: string;
  buyer: string | null;
  mine: boolean;
  source_request_id: string;
  plan: { price_with_vat: string | null; link: string | null; label: string } | null;
  last: { price: string; date: string; supplier: string | null; unit: string | null; source: "procurement" | "upd" } | null;
  free_stock: string | null;
  rfq: { id: string; number: string } | null;
  requote_from: string | null;
  cancel_reason: string | null;
  selectable: boolean;
  can_take: boolean;
  can_split: boolean;
  can_cover: boolean;
  can_cancel: boolean;
  can_resend: boolean;
};

export type RqScope = "mine" | "all";

export type RqView = {
  scope: RqScope;
  projects: RqProjectSummary[];
  batches: RqBatch[];
  /** Requests in the scope; without a project only the newest are listed. */
  batches_total: number;
  focus: { project: string; batch: string } | null;
};

export type CancelManyResult = { cancelled: string[]; skipped: { id: string; reason: string }[] };

function query(params: Record<string, string | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) if (value) search.set(key, value);
  const text = search.toString();
  return text ? `?${text}` : "";
}

export const requestsViewApi = {
  view: (params: { scope: RqScope; project?: string; line?: string }) =>
    api<RqView>(`/api/procurement/requests-view${query(params)}`),
  /** One request opened: only its lines. */
  batch: (scope: RqScope, batch: string) =>
    api<{ scope: RqScope; batch: string; lines: RqLine[] }>(`/api/procurement/requests-view${query({ scope, batch })}`),
  splitQty: (lineId: string, version: number, quantity: string) =>
    api<{ original: { legacy_id: string }; created: { legacy_id: string }; rfq: { rfq_number: string; quotes: number } | null }>(
      `/api/procurement/${encodeURIComponent(lineId)}/split-qty`,
      { method: "POST", body: JSON.stringify({ version, quantity }) },
    ),
  cancelMany: (lines: { id: string; version: number }[], reason: string) =>
    api<CancelManyResult>("/api/procurement/cancel-many", { method: "POST", body: JSON.stringify({ lines, reason }) }),
};
