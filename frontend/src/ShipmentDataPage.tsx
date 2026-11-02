import { useEffect, useMemo, useState } from "react";
import { failureText, shippingApi, type Principal, type ProjectAddressee, type ProjectForecast, type Shipment } from "./api";
import "./shipping.css";
import { loadPersonalViews, savePersonalView } from "./personalViews";
import { intakeApi, type IntakeRow } from "./productionIntakeApi";
import { DataTable, type DataColumn } from "./DataTable";
import { StatusChip } from "./StatusChip";
import type { Tone } from "./statusTone";
import { Readiness, ShipmentDataWindow, updLabel } from "./ShipmentData";
import { PackDecision, ReadyForecast, readiness } from "./shippingCells";
import { formatDate, formatNumber, NBSP } from "./format";
import { useCanPrint } from "./rights";
import { openShippingForms } from "./shippingPrint";
import { DISPATCH_STAGES, projectDispatchStage, shipmentDispatchStage, stageLabel, type DispatchStage } from "./dispatchStage";
import { DispatchCard } from "./DispatchCard";
import { canAct } from "./navigation";
import { kbKey, num, stagePieces } from "./intakeStages";

/* «Данные отправок» (владелец 28.10.2026, раздел «Менеджмент»): менеджер (Ульяна Ладутько) вносит данные для
   отгрузки — тип доставки, перевозчик, адрес, получателя, телефон, УПД. Кладовщик упаковывает, а без этих данных
   отправить не может. Список — этапы общей лестницы (`dispatchStage.ts`: В ожидании · В производстве · Готово · Принято · Упаковано · На отгрузку · Отгружено); проекты без живой отправки — своими строками: данные проекта можно внести заранее, новые
   отправки берут их из карточки. По умолчанию открыт этап «Упаковано» — упакованное ждёт её данных, чтобы уехать;
   «Только с недостающими данными» — отдельный переключатель поверх любого этапа. */

const ACTIVE = ["Новая", "Собрана", "Упакована"];
const SENT = ["Отгружена", "Закрыта"];
const STORAGE_KEY = "shipment-data-positions-v2";
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

/** Строка списка: отправка или ещё не набранный остаток проекта. `stage` — этап общей лестницы (у отменённой — null). */
export interface DataRow {
  id: string; kind: "shipment" | "project"; project: string; customer: string; shipment: Shipment | null;
  stage: DispatchStage | null; item_index: string; name: string; quantity: number; unit: string; grn: string; ownStage: string;
}

const deadlineOf = (row: DataRow) => row.shipment?.deadline ?? "9999";

/** Все отправки (отменённые только под «Все») и проекты из приёмки с ещё не набранным остатком. */
export function dataRows(shipments: Shipment[], intake: IntakeRow[] = []): DataRow[] {
  const byKb = new Map(intake.map((row) => [kbKey(row), row]));
  const rows: DataRow[] = shipments.flatMap((shipment) => shipment.lines.map((line) => {
    const source = byKb.get(kbKey(line));
    const ownStage = source?.stage || source?.project_stage || "Принято складом";
    return { id: `${shipment.id}:${line.id}`, kind: "shipment" as const, project: line.project,
      customer: shipment.customer || shipment.receiver, shipment, stage: shipmentDispatchStage(shipment, ownStage),
      item_index: line.item_index, name: line.name, quantity: num(line.picked), unit: line.unit, grn: line.grn || "", ownStage };
  }));
  for (const source of intake) {
    // Empty shipment input keeps only physical free quantities; actual picked lines already have their own rows.
    const pieces = stagePieces(source, []);
    const free = DISPATCH_STAGES.filter(([stage]) => stage !== "shipped" && pieces[stage] > 0);
    if (!free.length && num(source.needed) <= 0 && num(source.sent) <= 0 && !shipments.some((shipment) => shipment.status !== "Отменена" && shipment.lines.some((line) => kbKey(line) === kbKey(source)))) {
      free.push([projectDispatchStage(source.stage || source.project_stage), ""]);
    }
    for (const [stage] of free) rows.push({ id: `project:${kbKey(source)}:${stage}`, kind: "project", project: source.project,
      customer: source.customer, shipment: null, stage, item_index: source.item_index, name: source.name,
      quantity: pieces[stage], unit: source.unit, grn: source.grn || "", ownStage: source.stage || source.project_stage });
  }
  return rows.sort((a, b) => deadlineOf(a) < deadlineOf(b) ? -1 : deadlineOf(a) > deadlineOf(b) ? 1 : (a.shipment?.number ?? a.project).localeCompare(b.shipment?.number ?? b.project));
}

const STAGE_TONE: Record<DispatchStage, Tone> = { waiting: "neutral", production: "info", ready: "info", accepted: "warning", packed: "warning", to_ship: "info", shipped: "success" };

/** Проекты строки — по ним ищется прогноз готовности. */
const projectNames = (row: DataRow) => [row.project];

const deciding = (row: DataRow) => !row.shipment && (row.stage === "ready" || row.stage === "accepted");

export function ShipmentDataPage({ principal }: { principal: Principal }) {
  const [shipments, setShipments] = useState<Shipment[]>([]);
  const [intake, setIntake] = useState<IntakeRow[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [cards, setCards] = useState<Record<string, ProjectAddressee> | null>(null);
  const [cardsError, setCardsError] = useState("");
  const [open, setOpen] = useState<{ id: string; row: DataRow } | null>(null);
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
  const canDecide = canAct(principal, "shipping");
  const canPrint = useCanPrint();
  const printDocument = async (shipment: Shipment, document: "pl" | "dn") => {
    setError("");
    try { openShippingForms((await shippingApi.print(shipment.id, document)).forms); }
    catch (reason) { setError(failureText(reason, document === "pl" ? "Не удалось открыть упаковочный лист" : "Не удалось открыть отгрузочную ведомость")); }
  };
  const documentCell = (shipment: Shipment | null, document: "pl" | "dn") => {
    const number = document === "pl" ? shipment?.pl_number : shipment?.dn_number;
    const label = document === "pl" ? "Упаковочный лист" : "Отгрузочная ведомость";
    if (!shipment) return <span className="muted">Нет отправки</span>;
    if (!number) return <span className="muted">Не оформлен{document === "dn" ? "а" : ""}</span>;
    const blocked = document === "dn" && ACTIVE.includes(shipment.status) && shipment.missing_data.length > 0;
    return <span className="ship-position-document" title={blocked ? `Не хватает: ${shipment.missing_data.join(", ")}` : number}>
      {canPrint ? <button type="button" className="link-button ship-pl" disabled={blocked}
        aria-label={`Печать ${document === "pl" ? "ПЛ" : "DN"} ${number}`} onClick={(event) => { event.stopPropagation(); void printDocument(shipment, document); }}>{label}</button>
        : <span className="muted">{label}</span>}
      {blocked && <span className="muted">Не хватает данных</span>}
    </span>;
  };
  const projectCell = (row: DataRow) => <span className="ship-data-project">
    <strong>{row.item_index} · {row.name}</strong>
    <span>{row.quantity > 0 || row.shipment ? `${formatNumber(row.quantity)}${NBSP}${row.unit}` : "—"} · {row.grn || "ГРН не присвоен"}</span>
    {row.shipment && <span className="muted ship-sub">{row.shipment.number}<span className="ship-sub">{row.shipment.status}</span></span>}
  </span>;

  const reload = async () => {
    try {
      const [shipmentResult, intakeResult] = await Promise.all([shippingApi.shipments(), intakeApi.rows()]);
      setShipments(shipmentResult.shipments); setIntake(intakeResult.rows); setError("");
      const projects = [...new Set(dataRows(shipmentResult.shipments, intakeResult.rows).filter((row) => !row.shipment).map((row) => row.project))];
      setCards(null); setCardsError("");
      try {
        const result = projects.length ? await shippingApi.addressees(projects) : { addressees: [] };
        setCards(Object.fromEntries(result.addressees.map((card) => [card.project, card])));
      } catch (reason) { setCardsError(failureText(reason, "Не удалось загрузить данные проектов")); }
    } catch (reason) { setError(failureText(reason, "Не удалось загрузить отправки")); }
    finally { setLoaded(true); }
  };
  useEffect(() => { void reload(); }, []);
  // Прогноз цеха — подсказка к сроку: не пришёл (нет связи, нет данных) — строки остаются без него.
  useEffect(() => { void shippingApi.forecast().then((answer) => setForecasts(answer?.projects ?? {}), () => undefined); }, []);

  const allRows = useMemo(() => dataRows(shipments, intake), [shipments, intake]);
  const gapsOf = (row: DataRow) => row.shipment ? dataGaps(row.shipment) : cards?.[row.project]?.dispatch_gaps;
  const rows = allRows.filter((row) => !onlyGaps || (row.shipment ? dataGaps(row.shipment).length > 0
    : gapsOf(row) === undefined || Boolean(gapsOf(row)?.length) || (deciding(row) && !cards?.[row.project]?.pack_decided)));
  const current = open?.id.startsWith("project:") ? null : shipments.find((shipment) => shipment.id === open?.id) ?? null;
  const project = open?.id.startsWith("project:") ? open.row : null;
  const openRow = (row: DataRow) => setOpen({ id: row.shipment?.id ?? `project:${row.project}`, row });
  const decisionCell = (row: DataRow) => deciding(row) ? <PackDecision project={row.project} editable={canDecide}
    card={cards?.[row.project] ?? null} onCard={(card) => setCards((old) => ({ ...old, [card.project]: card }))} onReload={reload} /> : null;
  const dataCell = (row: DataRow) => {
    if (!row.shipment) {
      const gaps = gapsOf(row);
      return <span className={gaps === undefined ? "muted" : gaps.length ? "tone-warn" : "muted"}>
        {gaps === undefined ? "Данные проекта не загружены" : gaps.length ? `Не заполнено: ${gaps.join(", ")}` : "Данные получателя и УПД заполнены"}</span>;
    }
    return ACTIVE.includes(row.shipment.status) ? <Readiness source={row.shipment} gaps={dataGaps(row.shipment)} />
      : <span className="muted">{SENT.includes(row.shipment.status) ? sentNote(row.shipment) || "—" : "—"}</span>;
  };
  const forecastOf = (row: DataRow) => readiness(projectNames(row), forecasts, row.shipment?.deadline ?? null, row.shipment?.status ?? "Новая");
  const dueCell = (row: DataRow) => <>
    {row.shipment && <span className="muted ship-due">{formatDate(row.shipment.deadline) || "—"}</span>}
    <ReadyForecast forecast={forecastOf(row)} />
  </>;
  const card = (row: DataRow) => <div className="data-card">
    <button type="button" className="data-card-main" onClick={() => openRow(row)} aria-label={`Открыть: ${row.shipment?.number ?? row.project}`}>
    <span className="data-card-head">
      {projectCell(row)}
      <StatusChip tone={row.stage ? STAGE_TONE[row.stage] : "neutral"}>{stageLabel(row.stage)}</StatusChip>
    </span>
    {row.customer && <span className="data-card-customer">{row.customer}</span>}
    {row.shipment && <span>{dataCell(row)}</span>}
    <span className="data-card-due"><span className="muted">Дата отгрузки</span><span>{formatDate(row.shipment?.shipped_at) || "—"}</span></span>
    </button>
    <span className="data-card-upd">{documentCell(row.shipment, "pl")}{documentCell(row.shipment, "dn")}</span>
    {row.shipment && <span className="data-card-upd"><span className="muted">УПД</span><UpdCell shipment={row.shipment} /></span>}
  </div>;
  const columns: DataColumn<DataRow>[] = [
    { key: "project", title: "Проект", value: (row) => row.project, hidden: true },
    { key: "kb", title: "КБ", value: (row) => row.item_index, width: 56, maxWidth: 72 },
    { key: "name", title: "Наименование", value: (row) => row.name, grow: true, minWidth: 160, wrap: true,
      render: (row) => <span className="ship-position-name" title={row.name}>{row.name}</span> },
    { key: "quantity", title: "Количество", value: (row) => row.quantity, numeric: true, width: 88, render: (row) => row.quantity > 0 || row.shipment ? `${formatNumber(row.quantity)}${NBSP}${row.unit}` : "—" },
    { key: "status", title: "Этап позиции", value: (row) => stageLabel(row.stage), filter: true, width: 112,
      render: (row) => <span title={row.ownStage}><StatusChip tone={row.stage ? STAGE_TONE[row.stage] : "neutral"}>{stageLabel(row.stage)}</StatusChip></span> },
    { key: "grn", title: "ГРН", value: (row) => row.grn, width: 128, render: (row) => <span className="ship-position-grn">{row.grn || "—"}</span> },
    { key: "shipmentStatus", title: "Статус отправки", value: (row) => row.shipment?.status ?? "Не создана", width: 130, wrap: true, maxWidth: 160,
      render: (row) => <span className="ship-position-shipment"><span>{row.shipment?.status ?? "Не создана"}</span>{row.shipment && <span className="muted ship-sub">{row.shipment.number}</span>}</span> },
    { key: "shippedAt", title: "Дата отгрузки", value: (row) => row.shipment?.shipped_at ?? "", width: 104, render: (row) => formatDate(row.shipment?.shipped_at) || "—" },
    { key: "pl", title: "ПЛ", hint: "Упаковочный лист", value: (row) => row.shipment?.pl_number ?? "", width: 108, wrap: true, maxWidth: 132, render: (row) => documentCell(row.shipment, "pl") },
    { key: "dn", title: "DN", hint: "Отгрузочная ведомость", value: (row) => row.shipment?.dn_number ?? "", width: 112, wrap: true, maxWidth: 132, render: (row) => documentCell(row.shipment, "dn") },
    { key: "upd", title: "УПД", value: (row) => row.shipment ? updText(row.shipment) : "", filter: true, width: 96, wrap: true, maxWidth: 120, render: (row) => <UpdCell shipment={row.shipment} /> },
    { key: "customer", title: "Заказчик", value: (row) => row.customer, filter: true, hidden: true },
    { key: "gaps", title: "Данные", value: (row) => gapsOf(row)?.join(", ") || (row.shipment ? sentNote(row.shipment) : ""), hint: "Что внесено и чего не хватает; итог считает сервер", wrap: true, maxWidth: 520,
      hidden: true, render: dataCell },
    { key: "deadline", title: "Плановая дата", hidden: true, value: (row) => row.shipment?.deadline ?? "", render: dueCell },
  ];
  const groupLabel = (name: string, groupRows: DataRow[]) => {
    const source = groupRows.find(deciding) ?? groupRows.find((row) => !row.shipment) ?? groupRows[0];
    const decision = groupRows.find(deciding);
    const shipmentRows = [...new Map(groupRows.filter((row) => row.shipment).map((row) => [row.shipment!.id, row])).values()];
    return <span className="ship-position-group">
      <button type="button" className="link-button ship-position-group-title" onClick={(event) => { event.stopPropagation(); setOpen({ id: `project:${name}`, row: source }); }}><strong>{name}</strong>{source.customer && ` · ${source.customer}`}</button>
      {!source.shipment && <span className="ship-position-group-data">{dataCell(source)}</span>}
      {shipmentRows.map((row) => <span className="ship-position-group-data" key={row.shipment!.id} title={row.shipment!.number}>{dataCell(row)}</span>)}
      <ReadyForecast forecast={forecastOf(source)} />
      {decision && decisionCell(decision)}
    </span>;
  };
  return <section className="page shipping-page ship-positions-page">
    <header className="page-head"><h1>Данные отправок</h1>
      <label className="inline-check"><input type="checkbox" checked={onlyGaps} onChange={(event) => setOnlyGaps(event.target.checked)} /> Только с недостающими данными</label></header>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {cardsError && <div className="error-banner" role="alert">{cardsError}</div>}
    {!loaded ? <div className="empty">Загрузка…</div>
      : <DataTable label="Данные отправок" storageKey={STORAGE_KEY} rows={rows} columns={columns} rowKey={(row) => row.id}
        groupBy={(row) => row.project} groupLabel={groupLabel} groupCards
        onRowClick={openRow} exportName="Данные отправок" searchPlaceholder="Проект, КБ, наименование, ГРН или № отправки"
        searchText={(row) => [row.shipment?.number, row.name, row.grn, row.item_index]}
        presets={DISPATCH_STAGES.map(([key, label]) => ({ label, test: (row: DataRow) => row.stage === key }))}
        defaultPreset="Упаковано" allLast cards={view === "cards" ? card : undefined}
        toolbar={<><span className="muted">Счётчики: позиции КБ</span><span className="view-switch" role="group" aria-label="Вид списка">
          {VIEWS.map(([key, label]) => <button type="button" key={key} className="secondary small" aria-pressed={view === key} onClick={() => chooseView(key)}>{label}</button>)}
        </span></>}
        emptyText="В этом разделе пусто — другие этапы под чипами выше." />}
    {current && <ShipmentDataWindow key={current.id} shipment={current} stage={open?.row.stage} canEdit={canEdit} close={() => setOpen(null)} onSaved={reload} />}
    {project && <DispatchCard key={project.id} project={project.project} customer={project.customer} stage={project.stage ?? undefined} canEdit={canEdit} close={() => setOpen(null)} onSaved={() => void reload()} />}
  </section>;
}
