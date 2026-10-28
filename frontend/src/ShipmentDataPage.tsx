import { useEffect, useMemo, useState } from "react";
import { failureText, shippingApi, type Principal, type Shipment } from "./api";
import { DataTable, type DataColumn } from "./DataTable";
import { StatusChip } from "./StatusChip";
import { DataChip, ShipmentDataWindow } from "./ShipmentData";
import { Due, overdue, projectsOf } from "./ShippingPage";

/* «Данные отправок» (владелец 28.10.2026, раздел «Менеджмент»): менеджер (Ульяна Ладутько) вносит данные для
   отгрузки — тип доставки, перевозчик, адрес, получателя, телефон, УПД. Кладовщик упаковывает, а без этих данных
   отправить не может. Список — живые отправки и уехавшие, которым ещё не хватает файла УПД. */

const ACTIVE = ["Новая", "Собрана", "Упакована"];
const SHIPPED = ["Отгружена"];
/** У уехавшей смотрим только УПД: прежние отправки по новым правилам заново не проверяются. */
const UPD_GAPS = (gaps: string[]) => gaps.filter((gap) => gap.includes("УПД") && gap !== "тип УПД");

/** Что не хватает отправке в этом списке (слова сервера). */
export function dataGaps(shipment: Shipment): string[] {
  if (ACTIVE.includes(shipment.status)) return shipment.missing_data;
  return SHIPPED.includes(shipment.status) ? UPD_GAPS(shipment.missing_data) : [];
}

export function dataRows(shipments: Shipment[]): Shipment[] {
  return shipments.filter((shipment) => ACTIVE.includes(shipment.status) || (SHIPPED.includes(shipment.status) && UPD_GAPS(shipment.missing_data).length > 0))
    .sort((a, b) => (a.deadline ?? "9999") < (b.deadline ?? "9999") ? -1 : (a.deadline ?? "9999") > (b.deadline ?? "9999") ? 1 : a.number.localeCompare(b.number));
}

export function ShipmentDataPage({ principal: _principal }: { principal: Principal }) {
  const [shipments, setShipments] = useState<Shipment[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [open, setOpen] = useState<string | null>(null);

  const reload = async () => {
    try { setShipments((await shippingApi.shipments()).shipments); setError(""); }
    catch (reason) { setError(failureText(reason, "Не удалось загрузить отправки")); }
    finally { setLoaded(true); }
  };
  useEffect(() => { void reload(); }, []);

  const rows = useMemo(() => dataRows(shipments), [shipments]);
  const current = shipments.find((shipment) => shipment.id === open) ?? null;
  const columns: DataColumn<Shipment>[] = [
    { key: "number", title: "№ отправки", value: (row) => row.number, render: (row) => <strong>{row.number}</strong> },
    { key: "project", title: "Проект", value: (row) => projectsOf(row), render: (row) => <span className="ship-clip ship-clip-projects" title={projectsOf(row)}>{projectsOf(row)}</span> },
    { key: "customer", title: "Заказчик", value: (row) => row.customer || row.receiver, filter: true },
    { key: "status", title: "Статус", value: (row) => row.status, filter: true, render: (row) => <StatusChip status={row.status} /> },
    { key: "gaps", title: "Что не хватает", value: (row) => dataGaps(row).join(", "), hint: "Слова сервера: то же, что не даёт отгрузить",
      render: (row) => dataGaps(row).length ? <DataChip shipment={{ ...row, missing_data: dataGaps(row) }} /> : <span className="muted">всё внесено</span> },
    { key: "deadline", title: "Срок", value: (row) => row.deadline ?? "", render: (row) => <Due day={row.deadline} late={overdue(row)} since={row.created_at} /> },
  ];
  return <section className="page shipping-page">
    <header className="page-head"><h1>Данные отправок</h1></header>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {!loaded ? <div className="empty">Загрузка…</div>
      : <DataTable label="Данные отправок" storageKey="shipment-data" rows={rows} columns={columns} rowKey={(row) => row.id}
        onRowClick={(row) => setOpen(row.id)} exportName="Данные отправок" searchPlaceholder="№ отправки, проект или заказчик"
        presets={[{ label: "Не хватает данных", test: (row) => dataGaps(row).length > 0 }, { label: "Все", test: () => true }]}
        defaultPreset="Не хватает данных"
        emptyText="Отправок, которым не хватает данных, нет — фильтр «Все» покажет остальные." />}
    {current && <ShipmentDataWindow key={current.id} shipment={current} close={() => setOpen(null)} onSaved={reload} />}
  </section>;
}
