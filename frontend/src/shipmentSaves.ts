import { api, shippingApi, type Shipment } from "./api";
import { PICKUP_CARRIER, termsFor } from "./carriers";
import type { DispatchShipment } from "./productionIntakeApi";

/* Сохранение полей отправки с экрана «Приёмка с производства» (владелец 26.11, v2): только существующие правки сервера.
   Шапка (`PUT /head`) — только изменённые поля; документы (`PUT /documents`) — всегда все пять значений, включая трек:
   пропущенный трек сервер стирает; «Для перевозчика» — своя правка `PUT /tk-note`. Порядок: шапка → документы →
   комментарий для перевозчика, каждый следующий запрос — с версией из ответа предыдущего. */

export const HEAD_KEYS = ["receiver", "contact", "phone", "shipping_point", "terms", "carrier", "note", "deadline"] as const;
export const DOCUMENT_KEYS = ["upd_type", "upd_number", "upd_date", "upd_url", "track_number"] as const;
export type HeadKey = typeof HEAD_KEYS[number];
export type DocumentKey = typeof DOCUMENT_KEYS[number];
export type DraftKey = HeadKey | DocumentKey | "tk_note";
/** Значения полей как строки формы: пусто — "" (дата, тип УПД и условия пустыми уходят как null). */
export type ShipmentDraft = Partial<Record<DraftKey, string>>;

const ACTIVE = ["Новая", "Собрана", "Упакована"];
const historical = (shipment: Pick<Shipment, "status">) => ["Отгружена", "Закрыта"].includes(shipment.status);

/** Что правится на экране у этой отправки. Шапка и «Для перевозчика» — до отгрузки; документы — и у отгруженной (кроме
 *  трека: он после отгрузки — исправлением истории); закрытая и отменённая — только чтение. */
export function editableKeys(shipment: Pick<Shipment, "status" | "track_number">, canEdit: boolean): Set<DraftKey> {
  if (!canEdit) return new Set();
  const keys = new Set<DraftKey>();
  if (ACTIVE.includes(shipment.status)) { for (const key of HEAD_KEYS) keys.add(key); keys.add("tk_note"); }
  if (ACTIVE.includes(shipment.status) || shipment.status === "Отгружена") {
    for (const key of DOCUMENT_KEYS) keys.add(key);
    if (historical(shipment) && shipment.track_number.trim()) keys.delete("track_number");
  }
  return keys;
}

/** Текущее значение поля как строка формы. Тип УПД из карточки проекта (`inherited`) — пусто: «из карточки проекта». */
export function storedValue(shipment: Shipment, key: DraftKey): string {
  if (key === "upd_type") return shipment.inherited.includes("upd_type") ? "" : shipment.upd_type ?? "";
  if (key === "carrier") return shipment.carrier ?? "";
  const value = shipment[key as keyof Shipment];
  return value === null || value === undefined ? "" : String(value);
}

const changed = (shipment: Shipment, draft: ShipmentDraft, key: DraftKey) =>
  draft[key] !== undefined && draft[key]!.trim() !== storedValue(shipment, key).trim();

/** Тело `PUT /head`: только изменённые поля. ТК из списка тянет свои условия (самовывоз / доставка ТК), выбор
 *  «Самовывоз» в способе доставки ставит ТК «Самовывоз» — как в окне данных отправки. */
export function headPatch(shipment: Shipment, draft: ShipmentDraft): Record<string, string | null> {
  const patch: Record<string, string | null> = {};
  for (const key of HEAD_KEYS) if (changed(shipment, draft, key)) {
    const value = draft[key]!.trim();
    patch[key] = value === "" && (key === "terms" || key === "deadline") ? null : value;
  }
  if ("carrier" in patch && !("terms" in patch)) {
    const terms = termsFor(patch.carrier ?? "");
    if (terms && terms !== shipment.terms) patch.terms = terms;
  }
  if (patch.terms === "САМОВЫВОЗ" && !("carrier" in patch) && shipment.carrier.trim() !== PICKUP_CARRIER) patch.carrier = PICKUP_CARRIER;
  if (patch.terms === "ДОСТАВКА ТК" && !("carrier" in patch) && shipment.carrier.trim() === PICKUP_CARRIER) patch.carrier = "";
  return patch;
}

/** Тело `PUT /documents`: все пять значений — изменённые из черновика, остальные как есть (трек никогда не теряется). */
export function documentsBody(shipment: Shipment, draft: ShipmentDraft): Record<string, string | number | null> | null {
  if (!DOCUMENT_KEYS.some((key) => changed(shipment, draft, key))) return null;
  const value = (key: DocumentKey) => (changed(shipment, draft, key) ? draft[key]! : storedValue(shipment, key)).trim();
  const type = value("upd_type");
  const noUpd = type === "БЕЗ УПД";
  return {
    version: shipment.version,
    upd_type: type || null,
    upd_number: noUpd ? "" : value("upd_number"),
    upd_date: noUpd ? null : value("upd_date") || null,
    upd_url: value("upd_url"),
    track_number: value("track_number"),
  };
}

export const tkNoteApi = (shipment: Pick<Shipment, "id" | "version">, tkNote: string) =>
  api<{ shipment: DispatchShipment }>(`/api/shipping/shipments/${encodeURIComponent(shipment.id)}/tk-note`, {
    method: "PUT", body: JSON.stringify({ version: shipment.version, tk_note: tkNote }),
  });

/** Часть правки могла сохраниться: `shipment` — последняя сохранённая версия (или исходная). */
export class PartialSaveError extends Error {
  constructor(public cause: unknown, public shipment: DispatchShipment, public saved: boolean) {
    super(cause instanceof Error ? cause.message : String(cause));
  }
}

/** Сохраняет черновик отправки; возвращает отправку из последнего ответа (или исходную, если менять нечего). */
export async function saveShipment(shipment: DispatchShipment, draft: ShipmentDraft): Promise<DispatchShipment> {
  let current = shipment;
  let saved = false;
  try {
    const head = headPatch(current, draft);
    if (Object.keys(head).length) { current = (await shippingApi.head(current.id, { version: current.version, ...head })).shipment as DispatchShipment; saved = true; }
    const documents = documentsBody(current, draft);
    if (documents) { current = (await shippingApi.documents(current.id, { ...documents, version: current.version })).shipment as DispatchShipment; saved = true; }
    if (changed(current, draft, "tk_note")) { current = (await tkNoteApi(current, draft.tk_note!.trim())).shipment; saved = true; }
    return current;
  } catch (reason) {
    throw new PartialSaveError(reason, current, saved);
  }
}
