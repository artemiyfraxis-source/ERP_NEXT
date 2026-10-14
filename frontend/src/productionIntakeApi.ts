import { api, type ProjectAddressee } from "./api";

/* «Приёмка с производства»: список всего, что в работе (GET /api/shipping/intake), приёмка кусками и данные отправки. */

export interface IntakeTask { number: string; status: string; site: string; quantity: number | string; delivered: string }

export interface IntakeRow {
  project: string;
  customer: string;
  deadline: string | null;
  item_index: string;
  name: string;
  unit: string;
  stage: string;
  needed: string;
  launched: string;
  delivered: string;
  accepted: string;
  packed: string;
  sent: string;
  /** Можно принять сейчас. */
  remaining: string;
  good_id: string | null;
  good_version: number | null;
  grn: string | null;
  location: string;
  tasks: IntakeTask[];
}

export type UpdType = "ЭДО" | "БУМАГА" | "БЕЗ УПД";

/** Данные отправки проекта: карточка получателя + плановая дата и комментарий. */
export type DispatchData = Omit<ProjectAddressee, "upd_type"> & { upd_type: UpdType | null; plan_date: string | null; note: string };

export interface AcceptLine { project: string; item_index: string; quantity: string }

export const intakeApi = {
  rows: () => api<{ rows: IntakeRow[] }>("/api/shipping/intake"),
  /** Пакет: все отмеченные КБ или ни одной. Тот же ключ с тем же телом возвращает уже сделанное. */
  accept: (lines: AcceptLine[], receivedAt: string | null, location: string, idempotencyKey: string) =>
    api<{ replayed: boolean }>("/api/shipping/finished-goods/accept", {
      method: "POST", headers: { "Idempotency-Key": idempotencyKey },
      body: JSON.stringify({ lines, ...(receivedAt ? { received_at: receivedAt } : {}), ...(location.trim() ? { location: location.trim() } : {}) }),
    }),
  dispatch: (project: string) => api<{ addressee: DispatchData | null }>(`/api/shipping/project-addressee?project=${encodeURIComponent(project)}`),
  saveDispatch: (payload: Record<string, unknown>) => api<{ addressee: DispatchData }>("/api/shipping/project-addressee", {
    method: "PUT", body: JSON.stringify(payload),
  }),
};
