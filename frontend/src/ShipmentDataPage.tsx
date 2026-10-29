import { useEffect, useMemo, useState } from "react";
import { failureText, shippingApi, type Principal, type ProjectForecast, type Shipment } from "./api";
import "./shipping.css";
import { loadPersonalViews, savePersonalView } from "./personalViews";
import { intakeApi, type IntakeRow } from "./productionIntakeApi";
import { DataTable, type DataColumn } from "./DataTable";
import { StatusChip } from "./StatusChip";
import type { Tone } from "./statusTone";
import { Readiness, ShipmentDataWindow, updLabel } from "./ShipmentData";
import { Due, ReadyForecast, overdue, projectsOf, readiness } from "./shippingCells";
import { DISPATCH_STAGES, projectDispatchStage, shipmentDispatchStage, stageLabel, stageRank, type DispatchStage } from "./dispatchStage";
import { DispatchCard } from "./DispatchCard";
import { canAct } from "./navigation";

/* «Данные отправок» (владелец 28.10.2026, раздел «Менеджмент»): менеджер (Ульяна Ладутько) вносит данные для
   отгрузки — тип доставки, перевозчик, адрес, получателя, телефон, УПД. Кладовщик упаковывает, а без этих данных
   отправить не может. Список — этапы общей лестницы (`dispatchStage.ts`: В ожидании · В производстве · Готово · Принято · Упаковано · На отгрузку · Отгружено); проекты без живой отправки — своими строками: данные проекта можно внести заранее, новые
   отправки берут их из карточки. По умолчанию открыт этап «Упаковано» — упакованное ждёт её данных, чтобы уехать;
   «Только с недостающими данными» — отдельный переключатель поверх любого этапа. */

const ACTIVE = ["Новая", "Собрана", "Упакована"];
const SENT = ["Отгружена", "Закрыта"];
const STORAGE_KEY = "shipment-data-stages";
/** Личный вид списка («Таблица» / «Карточки») — на сервере у каждого свой (personalViews.ts), копия в браузере. */
const VIEW_KEY = "shipment-data.view";
type ListView = "table" | "cards";
const VIEWS: Array<[ListView, string]> = [["table", "Таблица"], ["cards", "Карточки"]];

function localView(): ListView | null {
  try { const value = window.localStorage.getItem(VIEW_KEY); return value === "cards" || value === "table" ? value : null; } catch { return null; }
}

/** Что не хватает живой отправке (слова сервера). Уехавшие могли уехать осознанно без данных (владелец 28.10): для них
 *  «не хватает» не считается и в отбор не попадает. */
export function dataGaps(shipment: Shipment): string[] {
  return ACTIVE.includes(shipment.status) ? shipment.missing_data : [];
}

/** Тихая заметка уехавшей отправке: ЭДО/бумажная УПД без файла — «Внести: файл УПД» (не предупреждение). */
export function sentNote(shipment: Shipment): string {
  return shipment.status === "Отгружена" && shipment.missing_data.includes("файл УПД") ? "Внести: файл УПД" : "";
}

/** УПД отправки одной строкой (владелец 29.10: «нет понимания, где есть УПД, файл, номер»): тип, № и файл — у любой отправки,
 *  и у уехавшей, где «Данные» молчат. */
export function updText(shipment: Shipment): string {
  if (shipment.upd_type === "БЕЗ УПД") return "Без УПД";
  const parts = [updLabel(shipment.upd_type) || "тип не выбран", shipment.upd_number ? `№ ${shipment.upd_number}` : "нет №"];
  return [...parts, shipment.upd_url ? "файл" : "без файла"].join(" · ");
}

/** `link` — файл ссылкой; в карточке-кнопке ссылки нет (кнопка внутри кнопки), файл откроется в окне отправки. */
function UpdCell({ shipment, link = true }: { shipment: Shipment | null; link?: boolean }) {
  if (!shipment) return <span className="muted">—</span>;
  if (shipment.upd_type === "БЕЗ УПД") return <span className="muted">Без УПД</span>;
  const type = updLabel(shipment.upd_type);
  return <span className="ship-upd-cell">
    <span className={type ? undefined : "muted"}>{type || "тип не выбран"}</span>
    {shipment.upd_number ? <strong>№ {shipment.upd_number}</strong> : <span className="tone-warn">нет №</span>}
    {shipment.upd_url && !link ? <span>файл</span> : shipment.upd_url
      ? <a href={shipment.upd_url} target="_blank" rel="noreferrer" onClick={(event) => event.stopPropagation()}>файл</a>
      : <span className="tone-warn">без файла</span>}
  </span>;
}

/** Строка списка: отправка или проект, по которому живой отправки нет. `stage` — этап общей лестницы (у отменённой — null). */
export interface DataRow { id: string; kind: "shipment" | "project"; project: string; customer: string; shipment: Shipment | null; stage: DispatchStage | null }

const deadlineOf = (row: DataRow) => row.shipment?.deadline ?? "9999";
const num = (value: string) => Number.parseFloat(value.replace(",", ".")) || 0;
const CANCELLED = "Отменена";

/** Все отправки (отменённые только под «Все») и проекты из приёмки без живой отправки, по которым ещё есть что отгружать. */
export function dataRows(shipments: Shipment[], intake: IntakeRow[] = []): DataRow[] {
  // Слово этапа проекта — самое раннее по его КБ (проект стоит там, где отстаёт последняя КБ).
  const projectWord = new Map<string, string>(), customers = new Map<string, string>(), open = new Set<string>();
  for (const row of intake) {
    const known = projectWord.get(row.project);
    if (known === undefined || stageRank(projectDispatchStage(row.project_stage)) < stageRank(projectDispatchStage(known))) projectWord.set(row.project, row.project_stage);
    if (!customers.has(row.project)) customers.set(row.project, row.customer);
    if (num(row.needed) - num(row.sent) > 0) open.add(row.project);
  }
  const rows: DataRow[] = shipments.map((shipment) => {
    // Проекты без строк приёмки считаем принятыми: отправка собрана из принятого.
    const words = shipment.lines.map((line) => projectWord.get(line.project) ?? "Принято складом");
    const earliest = words.reduce((low, word) => stageRank(projectDispatchStage(word)) < stageRank(projectDispatchStage(low)) ? word : low, "Принято складом");
    const stage = shipment.status === CANCELLED ? null : shipmentDispatchStage(shipment, earliest);
    return { id: shipment.id, kind: "shipment", project: projectsOf(shipment), customer: shipment.customer || shipment.receiver, shipment, stage };
  });
  const live = new Set(shipments.filter((shipment) => ACTIVE.includes(shipment.status)).flatMap((shipment) => shipment.lines.map((line) => line.project)));
  for (const project of open) {
    if (live.has(project)) continue;
    rows.push({ id: `project:${project}`, kind: "project", project, customer: customers.get(project) ?? "", shipment: null, stage: projectDispatchStage(projectWord.get(project) ?? "") });
  }
  return rows.sort((a, b) => deadlineOf(a) < deadlineOf(b) ? -1 : deadlineOf(a) > deadlineOf(b) ? 1 : (a.shipment?.number ?? a.project).localeCompare(b.shipment?.number ?? b.project));
}

const STAGE_TONE: Record<DispatchStage, Tone> = { waiting: "neutral", production: "info", ready: "info", accepted: "warning", packed: "warning", to_ship: "info", shipped: "success" };

/** Проекты строки — по ним ищется прогноз готовности. */
const projectNames = (row: DataRow) => row.shipment ? [...new Set(row.shipment.lines.map((line) => line.project))] : [row.project];

const gapsOf = (row: DataRow) => row.shipment ? dataGaps(row.shipment) : [];

export function ShipmentDataPage({ principal }: { principal: Principal }) {
  const [shipments, setShipments] = useState<Shipment[]>([]);
  const [intake, setIntake] = useState<IntakeRow[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [onlyGaps, setOnlyGaps] = useState(false);
  const [forecasts, setForecasts] = useState<Record<string, ProjectForecast>>({});
  const [view, setView] = useState<ListView>(() => localView() ?? "table");
  useEffect(() => {
    let live = true;
    void loadPersonalViews().then((views) => {
      const stored = (views?.[VIEW_KEY] as { mode?: string } | undefined)?.mode;
      if (live && (stored === "cards" || stored === "table")) setView(stored);
    });
    return () => { live = false; };
  }, []);
  const chooseView = (next: ListView) => {
    setView(next);
    try { window.localStorage.setItem(VIEW_KEY, next); } catch { /* браузер без хранилища: останется серверный вид */ }
    savePersonalView(VIEW_KEY, { mode: next });
  };
  const canEdit = canAct(principal, "shipping") || canAct(principal, "shipping_goods");

  const reload = async () => {
    try {
      const [shipmentResult, intakeResult] = await Promise.all([shippingApi.shipments(), intakeApi.rows()]);
      setShipments(shipmentResult.shipments); setIntake(intakeResult.rows); setError("");
    } catch (reason) { setError(failureText(reason, "Не удалось загрузить отправки")); }
    finally { setLoaded(true); }
  };
  useEffect(() => { void reload(); }, []);
  // Прогноз цеха — подсказка к сроку: не пришёл (нет связи, нет данных) — строки остаются без него.
  useEffect(() => { void shippingApi.forecast().then((answer) => setForecasts(answer?.projects ?? {}), () => undefined); }, []);

  const rows = useMemo(() => dataRows(shipments, intake).filter((row) => !onlyGaps || gapsOf(row).length > 0), [shipments, intake, onlyGaps]);
  const current = open?.startsWith("project:") ? null : shipments.find((shipment) => shipment.id === open) ?? null;
  const project = open?.startsWith("project:") ? rows.find((row) => row.id === open) ?? null : null;
  const forecastOf = (row: DataRow) => readiness(projectNames(row), forecasts, row.shipment?.deadline ?? null, row.shipment?.status ?? "Новая");
  const dueCell = (row: DataRow) => <>
    {row.shipment && <Due day={row.shipment.deadline} late={overdue(row.shipment)} since={row.shipment.created_at} />}
    <ReadyForecast forecast={forecastOf(row)} />
  </>;
  const card = (row: DataRow) => <button type="button" className="data-card" onClick={() => setOpen(row.id)}
    aria-label={`Открыть: ${row.shipment?.number ?? row.project}`}>
    <span className="data-card-head">
      <strong>{row.shipment?.number ?? "Нет отправки"}</strong>
      <StatusChip tone={row.stage ? STAGE_TONE[row.stage] : "neutral"}>{stageLabel(row.stage)}</StatusChip>
    </span>
    <span className="data-card-project">{row.project}</span>
    {row.customer && <span className="data-card-customer">{row.customer}</span>}
    <span>{!row.shipment ? <span className="muted">внести заранее</span> : ACTIVE.includes(row.shipment.status) ? <Readiness source={row.shipment} gaps={gapsOf(row)} />
      : <span className="muted">{SENT.includes(row.shipment.status) ? sentNote(row.shipment) || "—" : "—"}</span>}</span>
    {row.shipment && <span className="data-card-upd"><span className="muted">УПД</span><UpdCell shipment={row.shipment} link={false} /></span>}
    <span className="data-card-due"><span className="muted">Срок</span>{row.shipment ? <Due day={row.shipment.deadline} late={overdue(row.shipment)} since={row.shipment.created_at} /> : <span className="muted">—</span>}
      <ReadyForecast forecast={forecastOf(row)} /></span>
  </button>;
  const columns: DataColumn<DataRow>[] = [
    { key: "number", title: "№ отправки", value: (row) => row.shipment?.number ?? "", render: (row) => row.shipment ? <><strong>{row.shipment.number}</strong><div className="muted ship-sub">{row.shipment.status}</div></> : <span className="muted">нет отправки</span> },
    { key: "project", title: "Проект", value: (row) => row.project, render: (row) => <span className="ship-clip ship-clip-projects" title={row.project}>{row.project}</span> },
    { key: "customer", title: "Заказчик", value: (row) => row.customer, filter: true },
    { key: "status", title: "Статус", value: (row) => stageLabel(row.stage), filter: true, render: (row) => <StatusChip tone={row.stage ? STAGE_TONE[row.stage] : "neutral"}>{stageLabel(row.stage)}</StatusChip> },
    { key: "gaps", title: "Данные", value: (row) => gapsOf(row).join(", ") || (row.shipment ? sentNote(row.shipment) : ""), hint: "Что внесено и чего не хватает; итог считает сервер", wrap: true, maxWidth: 520,
      render: (row) => !row.shipment ? <span className="muted">внести заранее</span> : ACTIVE.includes(row.shipment.status) ? <Readiness source={row.shipment} gaps={gapsOf(row)} />
        : SENT.includes(row.shipment.status) ? <span className="muted">{sentNote(row.shipment) || "—"}</span> : <span className="muted">—</span> },
    { key: "upd", title: "УПД", value: (row) => row.shipment ? updText(row.shipment) : "", filter: true, render: (row) => <UpdCell shipment={row.shipment} /> },
    { key: "deadline", title: "Срок", value: (row) => row.shipment?.deadline ?? "", render: dueCell },
  ];
  return <section className="page shipping-page">
    <header className="page-head"><h1>Данные отправок</h1>
      <label className="inline-check"><input type="checkbox" checked={onlyGaps} onChange={(event) => setOnlyGaps(event.target.checked)} /> Только с недостающими данными</label></header>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {!loaded ? <div className="empty">Загрузка…</div>
      : <DataTable label="Данные отправок" storageKey={STORAGE_KEY} rows={rows} columns={columns} rowKey={(row) => row.id}
        onRowClick={(row) => setOpen(row.id)} exportName="Данные отправок" searchPlaceholder="№ отправки, проект или заказчик"
        presets={DISPATCH_STAGES.map(([key, label]) => ({ label, test: (row: DataRow) => row.stage === key }))}
        defaultPreset="Упаковано" allLast cards={view === "cards" ? card : undefined}
        toolbar={<span className="view-switch" role="group" aria-label="Вид списка">
          {VIEWS.map(([key, label]) => <button type="button" key={key} className="secondary small" aria-pressed={view === key} onClick={() => chooseView(key)}>{label}</button>)}
        </span>}
        emptyText="В этом разделе пусто — другие этапы под чипами выше." />}
    {current && <ShipmentDataWindow key={current.id} shipment={current} stage={rows.find((row) => row.id === current.id)?.stage} canEdit={canEdit} close={() => setOpen(null)} onSaved={reload} />}
    {project && <DispatchCard key={project.id} project={project.project} customer={project.customer} stage={project.stage ?? undefined} canEdit={canEdit} close={() => setOpen(null)} onSaved={() => void reload()} />}
  </section>;
}
