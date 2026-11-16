import type { Shipment } from "./api";

type DatedShipment = Pick<Shipment, "id" | "created_at" | "shipped_at" | "deadline">;
const SHOP_DAY = new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Moscow", year: "numeric", month: "2-digit", day: "2-digit" });
const HAS_OFFSET = /(?:Z|[+-]\d{2}:?\d{2})$/i;

/** SQLite returns the shop's wall time without an offset; PostgreSQL returns an instant. */
export function shipmentCreatedDay(created: string): string {
  if (!HAS_OFFSET.test(created)) return created.slice(0, 10);
  const parts = SHOP_DAY.formatToParts(new Date(created));
  return ["year", "month", "day"].map((type) => parts.find((part) => part.type === type)?.value).join("-");
}

export function shipmentDay(shipment: DatedShipment): string {
  return shipment.shipped_at ?? shipment.deadline ?? shipmentCreatedDay(shipment.created_at);
}

export function shipmentDateKind(shipment: DatedShipment): string {
  return shipment.shipped_at ? "факт" : shipment.deadline ? "план" : "создана";
}

const createdTime = (value: string) => Date.parse(HAS_OFFSET.test(value) ? value : `${value.replace(" ", "T")}+03:00`);
const microseconds = (value: string) => Number((/\.(\d+)/.exec(value)?.[1] ?? "").padEnd(6, "0").slice(3, 6));

/** Display order only; never use this to change stock allocation or picking order. */
export function compareShipments(a: DatedShipment, b: DatedShipment): number {
  const dayA = shipmentDay(a), dayB = shipmentDay(b);
  return (dayA < dayB ? 1 : dayA > dayB ? -1 : 0)
    || createdTime(b.created_at) - createdTime(a.created_at)
    || microseconds(b.created_at) - microseconds(a.created_at)
    || (a.id < b.id ? 1 : a.id > b.id ? -1 : 0);
}
