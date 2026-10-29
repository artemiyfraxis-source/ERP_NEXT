import { api, type ProjectAddressee, type Shipment } from "./api";
import type { PrintResult } from "./LabelPrint";

/* «Приёмка с производства»: список всего, что в работе (GET /api/shipping/intake), приёмка кусками и данные отправки. */

export interface IntakeTask { number: string; status: string; site: string; quantity: number | string; delivered: string }

/** Акт приёмки КБ складом: свой ГРН, штуки с учётом откатов, дата и место. */
export interface IntakeAct { grn: string; quantity: string; received_at: string; location: string }

export interface IntakeRow {
  project: string;
  customer: string;
  deadline: string | null;
  item_index: string;
  name: string;
  unit: string;
  stage: string;
  /** Этап проекта: Новый → В работе → Готово → ОТК → ОТК пройден → Принято складом → Упаковано → Отгружено. */
  project_stage: string;
  /** Менеджер решила «упаковывать» (владелец 29.10): без этого склад принятое не упаковывает. */
  pack_decided?: boolean;
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
  acts?: IntakeAct[];
}

/** Отправка с готовностью от сервера (владелец 29.10): трек вносится, когда есть УПД и ТК; «Отгрузить» — когда
 *  упакована и есть УПД, ТК и трек; без УПД — по разрешению одного из уполномоченных (ПИН на экране кладовщика). */
export type DispatchShipment = Shipment & {
  ready_for_track?: boolean;
  ready_to_ship?: boolean;
  no_upd_approved_by?: string | null;
  no_upd_approved_at?: string | null;
};

export interface NoUpdApprover { id: string; name: string }

export type UpdType = "ЭДО" | "БУМАГА" | "БЕЗ УПД";

/** Данные отправки проекта: карточка получателя + плановая дата и комментарий. */
export type DispatchData = Omit<ProjectAddressee, "upd_type"> & { upd_type: UpdType | null; plan_date: string | null; note: string };

/** `location` строки заменяет общее место пакета; пусто — общее (или прежнее место КБ). */
export interface AcceptLine { project: string; item_index: string; quantity: string; location?: string }

export const intakeApi = {
  rows: () => api<{ rows: IntakeRow[] }>("/api/shipping/intake"),
  /** Пакет: все отмеченные КБ или ни одной. Тот же ключ с тем же телом возвращает уже сделанное. */
  accept: (lines: AcceptLine[], receivedAt: string | null, location: string, idempotencyKey: string) =>
    api<{ replayed: boolean }>("/api/shipping/finished-goods/accept", {
      method: "POST", headers: { "Idempotency-Key": idempotencyKey },
      body: JSON.stringify({ lines, ...(receivedAt ? { received_at: receivedAt } : {}), ...(location.trim() ? { location: location.trim() } : {}) }),
    }),
  /** Этикетка ГРН акта приёмки — в ту же очередь печати склада, что у партий (права «Склад» на правку). */
  printLabel: (grn: string, copies = 1) =>
    api<PrintResult>(`/api/shipping/intake/${encodeURIComponent(grn)}/label`, { method: "POST", body: JSON.stringify({ copies }) }),
  dispatch: (project: string) => api<{ addressee: DispatchData | null }>(`/api/shipping/project-addressee?project=${encodeURIComponent(project)}`),
  shipments: () => api<{ shipments: DispatchShipment[] }>("/api/shipping/shipments"),
  /** Кто может разрешить отгрузку без УПД (Артемий, Зингер, Касаточкин). */
  noUpdApprovers: () => api<{ approvers: NoUpdApprover[] }>("/api/shipping/no-upd-approvers").then((result) => result.approvers ?? []),
  /** Разрешение на одну отправку: ПИН уполномоченного. Отказы — словами сервера: 403 неверный ПИН / не уполномочен /
   *  ПИН не назначен, 429 блокировка после попыток, 409 отправка изменилась, 422 уже разрешено или уехала. */
  approveNoUpd: (shipmentId: string, body: { version: number; approver_id: string; pin: string }) =>
    api<{ shipment: DispatchShipment }>(`/api/shipping/shipments/${encodeURIComponent(shipmentId)}/approve-no-upd`, {
      method: "POST", body: JSON.stringify(body),
    }),
  /** Трек ТК: та же правка документов отправки (сервер откажет, пока нет УПД и ТК); остальные поля — как есть. */
  saveTrack: (shipment: Shipment, trackNumber: string) =>
    api<{ shipment: DispatchShipment }>(`/api/shipping/shipments/${encodeURIComponent(shipment.id)}/documents`, {
      method: "PUT", body: JSON.stringify({
        version: shipment.version, upd_type: shipment.inherited.includes("upd_type") ? null : shipment.upd_type,
        upd_number: shipment.upd_number, upd_date: shipment.upd_date, upd_url: shipment.upd_url, track_number: trackNumber.trim(),
      }),
    }),
  saveDispatch: (payload: Record<string, unknown>) => api<{ addressee: DispatchData }>("/api/shipping/project-addressee", {
    method: "PUT", body: JSON.stringify(payload),
  }),
};
