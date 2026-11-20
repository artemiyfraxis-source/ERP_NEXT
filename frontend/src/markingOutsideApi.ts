import { api } from "./api";

/** «Отметить напечатанные вне ERP» (owner 20.11): a numbered label of a КБ printed outside the ERP counts as printed. */
export interface MarkingOutsideRow {
  task_id: string; item: string; task_number: string; serial: number; text: string; quantity: number;
  /** new — will be / was marked now; exists — marked before; removed — «Снять отметку» took it back. */
  state: "new" | "exists" | "removed";
  actor?: string; at?: string; comment?: string;
}
/** `task_ids` empty — every КБ of the group; `dry_run` — what would be marked, nothing written. */
export interface MarkingOutsideRequest {
  group_id: string; version: number; task_ids: string[]; serials: number[]; comment: string; dry_run?: boolean;
}
export interface MarkingOutsideAnswer { dry_run: boolean; rows: MarkingOutsideRow[]; added?: number; existing?: number; removed?: number }

const root = "/api/production/marking/outside";
const post = (path: string, body: MarkingOutsideRequest) =>
  api<MarkingOutsideAnswer>(root + path, { method: "POST", body: JSON.stringify(body), safe: Boolean(body.dry_run) });

export const markingOutsideApi = {
  list: (groupId: string, signal?: AbortSignal) =>
    api<{ rows: MarkingOutsideRow[] }>(`${root}?group_id=${encodeURIComponent(groupId)}`, { signal }),
  mark: (body: MarkingOutsideRequest) => post("", body),
  undo: (body: MarkingOutsideRequest) => post("/undo", body),
};
