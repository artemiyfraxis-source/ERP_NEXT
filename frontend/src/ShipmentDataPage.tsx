import { useEffect, useMemo, useState } from "react";
import { failureText, shippingApi, type Principal, type Shipment } from "./api";
import { intakeApi, type IntakeRow } from "./productionIntakeApi";
import { DataTable, type DataColumn } from "./DataTable";
import { StatusChip } from "./StatusChip";
import type { Tone } from "./statusTone";
import { Readiness, ShipmentDataWindow } from "./ShipmentData";
import { Due, overdue, projectsOf } from "./ShippingPage";
import { DispatchCard } from "./DispatchCard";
import { canAct } from "./navigation";

/* «Данные отправок» (владелец 28.10.2026, раздел «Менеджмент»): менеджер (Ульяна Ладутько) вносит данные для
   отгрузки — тип доставки, перевозчик, адрес, получателя, телефон, УПД. Кладовщик упаковывает, а без этих данных
   отправить не может. Список — шесть этапов: ожидание производства, производство, готово, принято на склад, упаковано, отгружено; проекты без живой отправки — своими строками: данные проекта можно внести заранее, новые
   отправки берут их из карточки. По умолчанию открыт этап «Упакованы» — упакованное ждёт её данных, чтобы уехать;
   «Только с недостающими данными» — отдельный переключатель поверх любого этапа. */

const ACTIVE = ["Новая", "Собрана", "Упакована"];
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

/** Этапы по владельцу (29.10): ожидание производства — всё без статуса «В работе», дальше по лестнице проекта до отгрузки. */
export const STAGES = ["В ожидании производства", "В производстве", "Готово", "Принято на склад", "Упаковано", "Отгружено"] as const;
const PROJECT_STAGE: Record<string, number> = { "В работе": 1, "ОТК": 2, "Принято складом": 3, "Упаковано": 4, "Отгружено": 5 };
const ACCEPTED = 3;
/** Этап проекта по слову сервера; новое и незнакомое (ТК готова, КБ набрана…) — ожидание производства. */
export const stageIndexOf = (projectStage: string) => PROJECT_STAGE[projectStage] ?? 0;
const CANCELLED = "Отменена";

/** Строка списка: отправка или проект, по которому живой отправки нет. `stage` — этап из STAGES (у отменённой — «Отменена»). */
export interface DataRow { id: string; kind: "shipment" | "project"; project: string; customer: string; shipment: Shipment | null; stage: string }

const deadlineOf = (row: DataRow) => row.shipment?.deadline ?? "9999";
const num = (value: string) => Number.parseFloat(value.replace(",", ".")) || 0;

/** Все отправки (отменённые только под «Все») и проекты из приёмки без живой отправки, по которым ещё есть что отгружать. */
export function dataRows(shipments: Shipment[], intake: IntakeRow[] = []): DataRow[] {
  const projectStage = new Map<string, number>(), customers = new Map<string, string>(), open = new Set<string>();
  for (const row of intake) {
    const index = stageIndexOf(row.project_stage);
    projectStage.set(row.project, Math.min(projectStage.get(row.project) ?? index, index));
    if (!customers.has(row.project)) customers.set(row.project, row.customer);
    if (num(row.needed) - num(row.sent) > 0) open.add(row.project);
  }
  const rows: DataRow[] = shipments.map((shipment) => {
    const projects = shipment.lines.map((line) => line.project);
    const own = Math.min(ACCEPTED, ...projects.map((name) => projectStage.get(name) ?? ACCEPTED));
    const stage = shipment.status === CANCELLED ? CANCELLED : SENT.includes(shipment.status) ? STAGES[5] : shipment.status === "Упакована" ? STAGES[4] : STAGES[own];
    return { id: shipment.id, kind: "shipment", project: projectsOf(shipment), customer: shipment.customer || shipment.receiver, shipment, stage };
  });
  const live = new Set(shipments.filter((shipment) => ACTIVE.includes(shipment.status)).flatMap((shipment) => shipment.lines.map((line) => line.project)));
  for (const project of open) {
    if (live.has(project)) continue;
    rows.push({ id: `project:${project}`, kind: "project", project, customer: customers.get(project) ?? "", shipment: null, stage: STAGES[projectStage.get(project) ?? 0] });
  }
  return rows.sort((a, b) => deadlineOf(a) < deadlineOf(b) ? -1 : deadlineOf(a) > deadlineOf(b) ? 1 : (a.shipment?.number ?? a.project).localeCompare(b.shipment?.number ?? b.project));
}

const STAGE_TONE: Record<string, Tone> = { "В ожидании производства": "neutral", "В производстве": "info", "Готово": "info", "Принято на склад": "warning", "Упаковано": "warning", "Отгружено": "success" };

const gapsOf = (row: DataRow) => row.shipment ? dataGaps(row.shipment) : [];

export function ShipmentDataPage({ principal }: { principal: Principal }) {
  const [shipments, setShipments] = useState<Shipment[]>([]);
  const [intake, setIntake] = useState<IntakeRow[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [onlyGaps, setOnlyGaps] = useState(false);
  const canEdit = canAct(principal, "shipping") || canAct(principal, "shipping_goods");

  const reload = async () => {
    try {
      const [shipmentResult, intakeResult] = await Promise.all([shippingApi.shipments(), intakeApi.rows()]);
      setShipments(shipmentResult.shipments); setIntake(intakeResult.rows); setError("");
    } catch (reason) { setError(failureText(reason, "Не удалось загрузить отправки")); }
    finally { setLoaded(true); }
  };
  useEffect(() => { void reload(); }, []);

  const rows = useMemo(() => dataRows(shipments, intake).filter((row) => !onlyGaps || gapsOf(row).length > 0), [shipments, intake, onlyGaps]);
  const current = open?.startsWith("project:") ? null : shipments.find((shipment) => shipment.id === open) ?? null;
  const project = open?.startsWith("project:") ? rows.find((row) => row.id === open) ?? null : null;
  const columns: DataColumn<DataRow>[] = [
    { key: "number", title: "№ отправки", value: (row) => row.shipment?.number ?? "", render: (row) => row.shipment ? <><strong>{row.shipment.number}</strong><div className="muted ship-sub">{row.shipment.status}</div></> : <span className="muted">нет отправки</span> },
    { key: "project", title: "Проект", value: (row) => row.project, render: (row) => <span className="ship-clip ship-clip-projects" title={row.project}>{row.project}</span> },
    { key: "customer", title: "Заказчик", value: (row) => row.customer, filter: true },
    { key: "status", title: "Статус", value: (row) => row.stage, filter: true, render: (row) => <StatusChip tone={STAGE_TONE[row.stage] ?? "neutral"}>{row.stage}</StatusChip> },
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
        presets={STAGES.map((name) => ({ label: name, test: (row: DataRow) => row.stage === name }))}
        defaultPreset="Упаковано" allLast
        emptyText="В этом разделе пусто — другие этапы под чипами выше." />}
    {current && <ShipmentDataWindow key={current.id} shipment={current} close={() => setOpen(null)} onSaved={reload} />}
    {project && <DispatchCard key={project.id} project={project.project} customer={project.customer} canEdit={canEdit} close={() => setOpen(null)} onSaved={() => void reload()} />}
  </section>;
}
