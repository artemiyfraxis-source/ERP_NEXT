import { useEffect, useMemo, useState } from "react";
import { failureText, shippingApi, type FinishedGood, type Principal, type Shipment } from "./api";
import { DataTable, type DataColumn } from "./DataTable";
import { StatusChip } from "./StatusChip";
import { Readiness, ShipmentDataWindow } from "./ShipmentData";
import { Due, overdue, projectsOf } from "./ShippingPage";
import { DispatchCard } from "./DispatchCard";
import { canAct } from "./navigation";

/* «Данные отправок» (владелец 28.10.2026, раздел «Менеджмент»): менеджер (Ульяна Ладутько) вносит данные для
   отгрузки — тип доставки, перевозчик, адрес, получателя, телефон, УПД. Кладовщик упаковывает, а без этих данных
   отправить не может. Список — все отправки по этапам и проекты без отправки («Не в работе»): данные проекта можно внести заранее, новые
   отправки берут их из карточки. По умолчанию открыт этап «Упакованы» — упакованное ждёт её данных, чтобы уехать;
   «Только с недостающими данными» — отдельный переключатель поверх любого этапа. */

const ACTIVE = ["Новая", "Собрана", "Упакована"];
const WORKING = ["Новая", "Собрана"];
const SENT = ["Отгружена", "Закрыта"];

/** Что не хватает живой отправке (слова сервера). Уехавшие могли уехать осознанно без данных (владелец 28.10): для них
 *  «не хватает» не считается и в отбор не попадает. */
export function dataGaps(shipment: Shipment): string[] {
  return ACTIVE.includes(shipment.status) ? shipment.missing_data : [];
}

/** Тихая заметка уехавшей отправке: ЭДО/бумажная УПД без файла — «Внести: файл УПД» (не предупреждение). */
export function sentNote(shipment: Shipment): string {
  return shipment.status === "Отгружена" && shipment.missing_data.includes("файл УПД") ? "Внести: файл УПД" : "";
}

/** Строка списка: отправка или проект, по которому отправки ещё нет. */
export interface DataRow { id: string; kind: "shipment" | "project"; project: string; customer: string; shipment: Shipment | null }

const deadlineOf = (row: DataRow) => row.shipment?.deadline ?? "9999";
const num = (value: string) => Number.parseFloat(value.replace(",", ".")) || 0;

/** Все отправки (отменённые тоже — они только под «Все») и проекты с готовой продукцией, которым не оформлена ни одна живая отправка. */
export function dataRows(shipments: Shipment[], goods: FinishedGood[] = []): DataRow[] {
  const rows: DataRow[] = shipments.map((shipment) => ({ id: shipment.id, kind: "shipment", project: projectsOf(shipment), customer: shipment.customer || shipment.receiver, shipment }));
  const shipped = new Set(shipments.filter((shipment) => shipment.status !== "Отменена").flatMap((shipment) => shipment.lines.map((line) => line.project)));
  const projects = new Map<string, string>();
  for (const good of goods) if (!shipped.has(good.project) && num(good.ordered) - num(good.sent) > 0 && !projects.has(good.project)) projects.set(good.project, good.customer);
  for (const [project, customer] of projects) rows.push({ id: `project:${project}`, kind: "project", project, customer, shipment: null });
  return rows.sort((a, b) => deadlineOf(a) < deadlineOf(b) ? -1 : deadlineOf(a) > deadlineOf(b) ? 1 : (a.shipment?.number ?? a.project).localeCompare(b.shipment?.number ?? b.project));
}

const gapsOf = (row: DataRow) => row.shipment ? dataGaps(row.shipment) : [];

export function ShipmentDataPage({ principal }: { principal: Principal }) {
  const [shipments, setShipments] = useState<Shipment[]>([]);
  const [goods, setGoods] = useState<FinishedGood[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [onlyGaps, setOnlyGaps] = useState(false);
  const canEdit = canAct(principal, "shipping") || canAct(principal, "shipping_goods");

  const reload = async () => {
    try {
      const [shipmentResult, goodsResult] = await Promise.all([shippingApi.shipments(), shippingApi.goods()]);
      setShipments(shipmentResult.shipments); setGoods(goodsResult.goods); setError("");
    } catch (reason) { setError(failureText(reason, "Не удалось загрузить отправки")); }
    finally { setLoaded(true); }
  };
  useEffect(() => { void reload(); }, []);

  const rows = useMemo(() => dataRows(shipments, goods).filter((row) => !onlyGaps || gapsOf(row).length > 0), [shipments, goods, onlyGaps]);
  const current = open?.startsWith("project:") ? null : shipments.find((shipment) => shipment.id === open) ?? null;
  const project = open?.startsWith("project:") ? rows.find((row) => row.id === open) ?? null : null;
  const stage = (statuses: string[]) => (row: DataRow) => row.shipment !== null && statuses.includes(row.shipment.status);
  const columns: DataColumn<DataRow>[] = [
    { key: "number", title: "№ отправки", value: (row) => row.shipment?.number ?? "", render: (row) => row.shipment ? <strong>{row.shipment.number}</strong> : <span className="muted">нет отправки</span> },
    { key: "project", title: "Проект", value: (row) => row.project, render: (row) => <span className="ship-clip ship-clip-projects" title={row.project}>{row.project}</span> },
    { key: "customer", title: "Заказчик", value: (row) => row.customer, filter: true },
    { key: "status", title: "Статус", value: (row) => row.shipment?.status ?? "Не в работе", filter: true, render: (row) => row.shipment ? <StatusChip status={row.shipment.status} /> : <span className="muted">Не в работе</span> },
    { key: "gaps", title: "Данные", value: (row) => gapsOf(row).join(", ") || (row.shipment ? sentNote(row.shipment) : ""), hint: "Что внесено и чего не хватает; итог считает сервер", wrap: true, maxWidth: 520,
      render: (row) => !row.shipment ? <span className="muted">внести заранее</span> : ACTIVE.includes(row.shipment.status) ? <Readiness source={row.shipment} gaps={gapsOf(row)} />
        : SENT.includes(row.shipment.status) ? <span className="muted">{sentNote(row.shipment) || "—"}</span> : <span className="muted">—</span> },
    { key: "deadline", title: "Срок", value: (row) => row.shipment?.deadline ?? "", render: (row) => row.shipment ? <Due day={row.shipment.deadline} late={overdue(row.shipment)} since={row.shipment.created_at} /> : null },
  ];
  return <section className="page shipping-page">
    <header className="page-head"><h1>Данные отправок</h1>
      <label className="inline-check"><input type="checkbox" checked={onlyGaps} onChange={(event) => setOnlyGaps(event.target.checked)} /> Только с недостающими данными</label></header>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {!loaded ? <div className="empty">Загрузка…</div>
      : <DataTable label="Данные отправок" storageKey="shipment-data" rows={rows} columns={columns} rowKey={(row) => row.id}
        onRowClick={(row) => setOpen(row.id)} exportName="Данные отправок" searchPlaceholder="№ отправки, проект или заказчик"
        presets={[
          { label: "Не в работе", test: (row) => row.kind === "project" },
          { label: "В работе", test: stage(WORKING) },
          { label: "Упакованы", test: stage(["Упакована"]) },
          { label: "Отправлены", test: stage(SENT) },
        ]}
        defaultPreset="Упакованы"
        emptyText="В этом разделе пусто — другие этапы под чипами выше." />}
    {current && <ShipmentDataWindow key={current.id} shipment={current} close={() => setOpen(null)} onSaved={reload} />}
    {project && <DispatchCard key={project.id} project={project.project} customer={project.customer} canEdit={canEdit} close={() => setOpen(null)} onSaved={() => void reload()} />}
  </section>;
}
