import type { Shipment } from "./api";
import { termsText } from "./carriers";
import { lineGrns } from "./dispatchPositionRows";
import { formatDay } from "./format";
import { shipmentDay } from "./shipmentOrder";

export function shipmentSearch(shipment: Shipment): string[] {
  return [shipment.number, shipment.status, shipment.customer, shipment.receiver, shipment.shipping_point, shipment.phone,
    shipment.contact, shipment.carrier, termsText(shipment.terms), shipment.track_number, shipment.upd_type ?? "", shipment.upd_number,
    shipment.upd_url, shipment.pl_number, shipment.dn_number, shipment.note, shipment.tk_note, shipment.manager, shipment.weight,
    shipmentDay(shipment), formatDay(shipmentDay(shipment)), shipment.created_at, shipment.deadline ?? "", shipment.packed_at ?? "", shipment.shipped_at ?? "", shipment.upd_date ?? "",
    ...(shipment.missing_data ?? []), ...(shipment.missing_ship ?? []),
    ...shipment.lines.flatMap((line) => [line.project, line.item_index, line.article, line.name, line.requested, line.picked, line.shortage,
      line.unit, ...lineGrns(line), ...(line.lots ?? []).flatMap((lot) => [lot.grn, lot.quantity])]),
    ...shipment.boxes.flatMap((box) => [String(box.place), box.kind, box.dims, box.net, box.gross, box.seal,
      ...box.items.flatMap((item) => [item.item_index, item.quantity])])];
}

