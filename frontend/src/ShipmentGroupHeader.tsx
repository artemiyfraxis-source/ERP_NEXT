import { type ProjectAddressee, type Shipment } from "./api";
import { isPickup, termsText } from "./carriers";
import { updLabel } from "./ShipmentData";
import { compareShipments } from "./shipmentOrder";

type GroupRow = { project: string; shipment?: Shipment | null };

/** Stable group identity, independent of display ordering. */
export function shipmentGroupKey(row: GroupRow): string {
  const shipment = row.shipment;
  return `${row.project}\u0001${shipment?.id ?? ""}`;
}

export function orderShipmentRows<T extends GroupRow>(rows: T[]): T[] {
  const newest = new Map<string, Shipment>();
  for (const row of rows) if (row.shipment) {
    const previous = newest.get(row.project);
    if (!previous || compareShipments(row.shipment, previous) < 0) newest.set(row.project, row.shipment);
  }
  return [...rows].sort((a, b) => {
    if (a.project !== b.project) {
      const headA = newest.get(a.project), headB = newest.get(b.project);
      return (headA && headB ? compareShipments(headA, headB) : headA ? -1 : headB ? 1 : 0)
        || a.project.localeCompare(b.project, "ru", { numeric: true });
    }
    return a.shipment && b.shipment ? compareShipments(a.shipment, b.shipment) : a.shipment ? -1 : b.shipment ? 1 : 0;
  });
}

/** Прочерк («-», «—», «.», «_») — то же, что пусто, как у сервера (`filled`, владелец 06.11). */
const PLACEHOLDER = /^[\s\-‐-―−_.]*$/;

export function ShippingValue({ label, value }: { label: string; value?: string | null }) {
  const text = value && !PLACEHOLDER.test(value) ? value.trim() : "";
  // «самовывоз» в получателе/телефоне/адресе — не данные: способ доставки уже сказан отдельно.
  if (text.toLowerCase() === "самовывоз" && label !== "ТК" && label !== "Способ доставки") return null;
  return <span className="ship-group-field">{label}: {text || <span className="tone-warn">не заполнено</span>}</span>;
}

type UpdSource = Pick<ProjectAddressee, "upd_type"> & Partial<Pick<ProjectAddressee, "upd_number" | "upd_url">>;

/** Тип, номер и файл одинаково видны у отправки и у карточки проекта. */
export function UpdCell({ shipment, link = true }: { shipment?: UpdSource | null; link?: boolean }) {
  if (shipment?.upd_type === "БЕЗ УПД") return <span>Без УПД</span>;
  const type = updLabel(shipment?.upd_type);
  return <span className="ship-upd-cell">
    <span className={type ? undefined : "tone-warn"}>{type || "не заполнено"}</span>
    {shipment?.upd_number ? <strong>№ {shipment.upd_number}</strong> : <span className="tone-warn">№ не заполнено</span>}
    {shipment?.upd_url ? link
      ? <a href={shipment.upd_url} target="_blank" rel="noreferrer" onClick={(event) => event.stopPropagation()}>файл</a>
      : <span>файл</span> : <span className="tone-warn">без файла</span>}
  </span>;
}

export function ProjectShippingData({ data, updRequired = true }: { data?: ProjectAddressee; updRequired?: boolean }) {
  const pickup = isPickup(data?.carrier ?? "") || data?.terms === "САМОВЫВОЗ";
  return <>
    <ShippingValue label="Получатель" value={data?.receiver} />
    <ShippingValue label="Телефон" value={data?.phone} />
    {pickup ? <span>Самовывоз</span> : <ShippingValue label="Адрес" value={data?.shipping_point} />}
    <ShippingValue label="Способ доставки" value={pickup ? "Самовывоз" : termsText(data?.terms)} />
    {!pickup && <ShippingValue label="ТК" value={data?.carrier} />}
    <span className="ship-group-field">УПД: {updRequired ? <UpdCell shipment={data} /> : <span className="muted">{updLabel(data?.upd_type) || "—"}</span>}</span>
  </>;
}
