import type { ReactNode } from "react";
import { shippingApi, failureText, type ProjectAddressee, type Shipment } from "./api";
import { isPickup, needsTrack, termsText } from "./carriers";
import { formatDay, formatNumber, NBSP } from "./format";
import { useCanPrint } from "./rights";
import { updLabel } from "./ShipmentData";
import { openShippingForms } from "./shippingPrint";
import { StatusChip } from "./StatusChip";

type GroupRow = { project: string; shipment?: Shipment | null };

/** Проекты рядом, отправки — от ранней даты к поздней; сортировка таблицы меняет только строки внутри блока. */
export function shipmentGroupKey(row: GroupRow): string {
  const shipment = row.shipment;
  return `${row.project}\u0001${shipment ? `${shipment.shipped_at || shipment.created_at}\u0001${shipment.created_at}\u0001${shipment.number}\u0001${shipment.id}` : ""}`;
}

export function orderShipmentRows<T extends GroupRow>(rows: T[]): T[] {
  return [...rows].sort((a, b) => a.project.localeCompare(b.project, "ru", { numeric: true })
    || shipmentGroupKey(a).localeCompare(shipmentGroupKey(b), "ru", { numeric: true }));
}

/** Нумерация только тех отправок проекта, которые есть на выбранной вкладке. */
export function shipmentPositions(rows: GroupRow[]): Map<string, { index: number; count: number }> {
  const projects = new Map<string, string[]>();
  for (const row of orderShipmentRows(rows)) {
    if (!row.shipment) continue;
    const ids = projects.get(row.project) ?? [];
    if (!ids.includes(row.shipment.id)) ids.push(row.shipment.id);
    projects.set(row.project, ids);
  }
  const positions = new Map<string, { index: number; count: number }>();
  for (const [project, ids] of projects) ids.forEach((id, index) => positions.set(`${project}\u0001${id}`, { index: index + 1, count: ids.length }));
  return positions;
}

export function ShippingValue({ label, value }: { label: string; value?: string | null }) {
  return <span className="ship-group-field">{label}: {value?.trim() || <span className="tone-warn">не заполнено</span>}</span>;
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

export function ProjectShippingData({ data }: { data?: ProjectAddressee }) {
  const pickup = isPickup(data?.carrier ?? "") || data?.terms === "САМОВЫВОЗ";
  return <>
    <ShippingValue label="Получатель" value={data?.receiver} />
    <ShippingValue label="Телефон" value={data?.phone} />
    {pickup ? <span>Самовывоз</span> : <ShippingValue label="Адрес" value={data?.shipping_point} />}
    <ShippingValue label="Способ доставки" value={pickup ? "Самовывоз" : termsText(data?.terms)} />
    {!pickup && <ShippingValue label="ТК" value={data?.carrier} />}
    <span className="ship-group-field">УПД: <UpdCell shipment={data} /></span>
  </>;
}

/** Заголовок одной отгрузки: общие данные не повторяются в каждой строке КБ. */
export function ShipmentGroupHeader({ project, customer, shipment, index, count, quantity, onOpenProject, onOpenShipment, onError, children }: {
  project: string; customer?: string; shipment: Shipment; index: number; count: number; quantity: number;
  onOpenProject?: () => void; onOpenShipment: () => void; onError: (error: string) => void; children?: ReactNode;
}) {
  const canPrint = useCanPrint();
  const title = [project, customer].filter(Boolean).join(" · ");
  const pickup = isPickup(shipment.carrier) || shipment.terms === "САМОВЫВОЗ";
  const document = (kind: "pl" | "dn") => {
    const number = kind === "pl" ? shipment.pl_number : shipment.dn_number;
    const gaps = kind === "dn" && ["Новая", "Собрана", "Упакована"].includes(shipment.status) ? shipment.missing_data : [];
    const print = async () => {
      try { openShippingForms((await shippingApi.print(shipment.id, kind)).forms); }
      catch (reason) { onError(failureText(reason, "Не удалось открыть документ")); }
    };
    return <span className="ship-group-field" title={gaps.length ? `Не хватает: ${gaps.join(", ")}` : kind === "pl" ? "Упаковочный лист" : "Отгрузочная ведомость"}>{kind.toUpperCase()}: {number ? canPrint
      ? <button type="button" className="link-button" disabled={gaps.length > 0}
        aria-label={`Печать ${kind.toUpperCase()} ${number}`} onClick={(event) => { event.stopPropagation(); void print(); }}>{number}</button>
      : <span>{number}</span> : <span className="tone-warn">не заполнено</span>}</span>;
  };
  return <span className="ship-position-group ship-shipment-group">
    {onOpenProject ? <button type="button" className="link-button ship-position-group-title" onClick={(event) => { event.stopPropagation(); onOpenProject(); }}><strong>{title}</strong></button>
      : <strong className="ship-position-group-title">{title}</strong>}
    <button type="button" className="link-button ship-group-open" aria-label={`Данные отправки ${shipment.number}`} onClick={(event) => { event.stopPropagation(); onOpenShipment(); }}>
      <strong>Отгрузка {index} из {count}</strong><strong>{shipment.number}</strong><StatusChip status={shipment.status} />
    </button>
    {shipment.shipped_at && <span>{formatDay(shipment.shipped_at)}</span>}
    <strong>{formatNumber(quantity)}{NBSP}шт</strong>
    <ShippingValue label="ТК" value={pickup ? "Самовывоз" : shipment.carrier} />
    {needsTrack(shipment) && <ShippingValue label="Трек" value={shipment.track_number} />}
    <ShippingValue label="Получатель" value={shipment.receiver} />
    <ShippingValue label="Телефон" value={shipment.phone} />
    <span className="ship-group-field">УПД: <UpdCell shipment={shipment} /></span>
    {document("pl")}{document("dn")}{children}
  </span>;
}
